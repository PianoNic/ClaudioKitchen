# SPDX-License-Identifier: Apache-2.0
"""Model discovery tools."""

import httpx

from src import config
from src.app import mcp
from src.auth import _check_user


@mcp.tool
async def list_models(output_modality: str = "image") -> dict:
    """List OpenRouter models by output modality: text, image, audio, embeddings.
    For video models use list_video_models instead."""
    await _check_user()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{config.OR_BASE}/models", headers=config.OR_HEADERS,
                        params={"output_modalities": output_modality})
        r.raise_for_status()
        data = r.json().get("data") or []
    return {"models": [{"id": m.get("id"), "name": m.get("name"),
                        "pricing": m.get("pricing")}
                       for m in data if isinstance(m, dict)]}


@mcp.tool
async def list_video_models() -> dict:
    """List all video generation models with capabilities (resolutions, durations,
    aspect ratios, pricing)."""
    await _check_user()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{config.OR_BASE}/videos/models", headers=config.OR_HEADERS)
        r.raise_for_status()
        return r.json()
