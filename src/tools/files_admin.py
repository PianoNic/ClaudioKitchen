# SPDX-License-Identifier: Apache-2.0
"""File management tools: list, delete, and bulk-prune generated files."""

import datetime as dt
import time
from pathlib import Path

from src import config
from src.app import mcp
from src.auth import _check_user
from src.files import _resolve_generated


@mcp.tool
async def list_files(limit: int = 100, sort: str = "newest") -> dict:
    """List files this server has generated/stored (served under /files). Shows each
    file's name, size, modified time and download URL, plus the total count and size.
    `sort` is 'newest' (default) or 'oldest'. Use delete_file / cleanup_files to prune."""
    await _check_user()
    entries = []
    for p in config.FILES_DIR.iterdir():
        if not p.is_file() or p.name == ".gitkeep":
            continue
        st = p.stat()
        entries.append({
            "name": p.name,
            "size_bytes": st.st_size,
            "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(),
            "url": f"{config.BASE_URL}/files/{p.name}?token={config.FILES_TOKEN}",
            "_mtime": st.st_mtime,
        })
    entries.sort(key=lambda e: e["_mtime"], reverse=(sort != "oldest"))
    total = sum(e["size_bytes"] for e in entries)
    for e in entries:
        e.pop("_mtime", None)
    return {
        "count": len(entries),
        "total_bytes": total,
        "total_mb": round(total / 1_048_576, 1),
        "files": entries[: max(0, limit)],
    }


@mcp.tool
async def delete_file(name_or_url: str) -> dict:
    """Delete one generated file. Accepts either the bare filename (e.g. 'ab12.png')
    or a full /files download URL. Returns whether it was deleted."""
    await _check_user()
    path = _resolve_generated(name_or_url)
    if not path.is_file():
        return {"deleted": False, "name": path.name, "error": "not found"}
    freed = path.stat().st_size
    path.unlink()
    return {"deleted": True, "name": path.name, "freed_bytes": freed}


@mcp.tool
async def cleanup_files(older_than_days: float | None = None,
                        keep_newest: int | None = None,
                        dry_run: bool = True) -> dict:
    """Bulk-prune generated files to reclaim disk. Provide `older_than_days` (delete
    files older than N days), `keep_newest` (delete all but the N most recent), or
    both (union). dry_run=True (default) only previews what would be deleted - call
    again with dry_run=False to actually delete."""
    await _check_user()
    if older_than_days is None and keep_newest is None:
        raise ValueError("Specify older_than_days and/or keep_newest.")
    files = [p for p in config.FILES_DIR.iterdir() if p.is_file() and p.name != ".gitkeep"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # newest first

    victims: set[Path] = set()
    if older_than_days is not None:
        cutoff = time.time() - older_than_days * 86400
        victims.update(p for p in files if p.stat().st_mtime < cutoff)
    if keep_newest is not None:
        victims.update(files[max(0, keep_newest):])

    freed = sum(p.stat().st_size for p in victims)
    names = sorted(p.name for p in victims)
    if not dry_run:
        for p in victims:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    return {
        "dry_run": dry_run,
        "matched": len(names),
        "freed_bytes": freed,
        "freed_mb": round(freed / 1_048_576, 1),
        "files": names[:100],
    }
