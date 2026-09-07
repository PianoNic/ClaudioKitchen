# SPDX-License-Identifier: Apache-2.0
"""Importing this package registers every @mcp.tool with the FastMCP instance."""

from src.tools import (  # noqa: F401
    audio,
    discovery,
    embeddings,
    files_admin,
    images,
    uploads,
    usage_tool,
    video,
)
