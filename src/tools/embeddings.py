# SPDX-License-Identifier: Apache-2.0
"""Embedding and rerank tools."""

import httpx

from src import config
from src.app import mcp
from src.auth import _check_user
from src.usage import _check_budget, _cost, _record_cost


@mcp.tool
async def create_embeddings(texts: list[str],
                            model: str = "openai/text-embedding-3-small") -> dict:
    """Create embedding vectors for a list of texts. Includes the request cost."""
    await _check_user()
    await _check_budget()
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{config.OR_BASE}/embeddings", headers=config.OR_HEADERS,
                         json={"model": model, "input": texts})
        r.raise_for_status()
        data = r.json()
    await _record_cost("create_embeddings", model, data.get("usage"))
    data["cost_usd"] = _cost(data.get("usage"))
    return data


@mcp.tool
async def rerank(query: str, documents: list[str],
                 model: str = "cohere/rerank-v3.5", top_n: int = 5) -> dict:
    """Rerank documents by relevance to a query. Includes the request cost."""
    await _check_user()
    await _check_budget()
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{config.OR_BASE}/rerank", headers=config.OR_HEADERS,
                         json={"model": model, "query": query,
                               "documents": documents, "top_n": top_n})
        r.raise_for_status()
        data = r.json()
    await _record_cost("rerank", model, data.get("usage"))
    data["cost_usd"] = _cost(data.get("usage"))
    return data
