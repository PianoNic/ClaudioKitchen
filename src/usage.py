# SPDX-License-Identifier: Apache-2.0
"""Per-request cost ledger (usage.jsonl) and the monthly budget cap."""

import asyncio
import datetime as dt
import json
import time

from src import config

_usage_lock = asyncio.Lock()  # serialize ledger appends across concurrent tool calls


def _cost(usage: dict | None) -> float | None:
    """Extract the request cost (USD) from an OpenRouter usage object."""
    if not isinstance(usage, dict):
        return None
    return usage.get("cost")


def _cost_note(usage: dict | None) -> str:
    c = _cost(usage)
    return f"💲 Request cost: ${c:.6f} USD" if c is not None else "💲 Request cost: unknown"


def _read_usage() -> list[dict]:
    """Load the append-only cost ledger (tolerant of partial/corrupt lines)."""
    if not config.USAGE_LOG.is_file():
        return []
    out = []
    for line in config.USAGE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


async def _record_cost(tool: str, model: str | None, usage: dict | None,
                       dedupe_key: str | None = None) -> None:
    """Append one request's cost to the ledger. No-op when cost is unknown.

    Pass dedupe_key (e.g. a video job id) to record a cost at most once even if
    the tool is polled repeatedly.
    """
    cost = _cost(usage)
    if cost is None:
        return
    async with _usage_lock:
        if dedupe_key is not None:
            for r in _read_usage():
                if r.get("dedupe_key") == dedupe_key:
                    return
        entry: dict = {"ts": time.time(), "tool": tool, "model": model, "cost_usd": cost}
        if dedupe_key is not None:
            entry["dedupe_key"] = dedupe_key
        with config.USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def _month_start_ts() -> float:
    now = dt.datetime.now(dt.timezone.utc)
    return dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc).timestamp()


def _day_start_ts() -> float:
    now = dt.datetime.now(dt.timezone.utc)
    return dt.datetime(now.year, now.month, now.day, tzinfo=dt.timezone.utc).timestamp()


def _spend_since(ts: float, records: list[dict] | None = None) -> float:
    recs = records if records is not None else _read_usage()
    return sum((r.get("cost_usd") or 0.0) for r in recs if (r.get("ts") or 0) >= ts)


async def _check_budget() -> None:
    """Block a generating call if the monthly budget cap is already reached."""
    if config.MONTHLY_BUDGET_USD is None:
        return
    spent = _spend_since(_month_start_ts())
    if spent >= config.MONTHLY_BUDGET_USD:
        raise RuntimeError(
            f"Monthly budget reached: spent ${spent:.4f} of ${config.MONTHLY_BUDGET_USD:.2f} "
            f"this month. Raise MONTHLY_BUDGET_USD or wait until next month."
        )
