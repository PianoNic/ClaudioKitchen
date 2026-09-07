# SPDX-License-Identifier: Apache-2.0
"""Entrypoint: assembles the FastMCP server and runs it over Streamable HTTP.

See src/app.py for the FastMCP instance; src/routes.py, src/image_view.py and
src/tools/ register the custom routes, the MCP Apps view, and every @mcp.tool
respectively - imported here purely for that registration side effect.
"""

import os

import src.image_view  # noqa: F401
import src.routes  # noqa: F401
import src.tools  # noqa: F401
from src.app import mcp

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
