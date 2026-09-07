# SPDX-License-Identifier: Apache-2.0
"""Cost/usage reporting tool."""

from src import config
from src.app import mcp
from src.auth import _check_user
from src.usage import _day_start_ts, _month_start_ts, _read_usage, _spend_since


@mcp.tool
async def usage_summary() -> dict:
    """Report recorded OpenRouter spend (USD): today, this calendar month, all-time,
    a per-tool breakdown, and remaining monthly budget if MONTHLY_BUDGET_USD is set.
    Costs are logged automatically by every generating tool."""
    await _check_user()
    recs = _read_usage()
    today = _spend_since(_day_start_ts(), recs)
    month = _spend_since(_month_start_ts(), recs)
    all_time = sum((r.get("cost_usd") or 0.0) for r in recs)
    by_tool: dict[str, dict] = {}
    for r in recs:
        t = r.get("tool") or "unknown"
        agg = by_tool.setdefault(t, {"requests": 0, "cost_usd": 0.0})
        agg["requests"] += 1
        agg["cost_usd"] = round(agg["cost_usd"] + (r.get("cost_usd") or 0.0), 6)
    out = {
        "requests": len(recs),
        "today_usd": round(today, 6),
        "this_month_usd": round(month, 6),
        "all_time_usd": round(all_time, 6),
        "by_tool": by_tool,
    }
    if config.MONTHLY_BUDGET_USD is not None:
        out["monthly_budget_usd"] = config.MONTHLY_BUDGET_USD
        out["budget_remaining_usd"] = round(max(0.0, config.MONTHLY_BUDGET_USD - month), 6)
    return out
