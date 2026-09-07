# Development setup

## Project layout

```
main.py              entrypoint: wires up src/app.py's mcp instance and runs it
src/
  app.py              the FastMCP instance + the client-facing SERVER_INSTRUCTIONS
  config.py           all env-var reads / constants (loaded once, at import time)
  auth.py             OIDCProxy wiring + the per-call email allowlist check
  files.py            save/sniff/decode helpers, the SSRF-guarded fetcher, upload tickets
  usage.py            the cost ledger (usage.jsonl) and the monthly budget cap
  image_view.py       the MCP Apps view that renders images inline in claude.ai
  routes.py           custom Starlette routes: /health, /files/{name}, /upload
  tools/
    discovery.py      list_models, list_video_models
    uploads.py         create_upload_url, upload_file
    images.py          generate_image, edit_image, describe_image
    video.py           generate_video, check_video
    audio.py           text_to_speech, transcribe_audio
    embeddings.py       create_embeddings, rerank
    files_admin.py     list_files, delete_file, cleanup_files
    usage_tool.py       usage_summary
tests/                 unit tests for the pure helpers in src/files.py and src/usage.py
```

Every `@mcp.tool` / `@mcp.custom_route` / `@mcp.resource` decorator needs the shared
`mcp` instance from `src/app.py`, so each module does `from src.app import mcp` and
decorates at import time. `src/tools/__init__.py` imports every tool submodule purely
for that registration side effect; `main.py` does the same for `src.routes` and
`src.image_view`.

## Running locally without Docker

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements.txt -r requirements-dev.txt

cp template.env .env   # fill in real OIDC / OpenRouter values
# export the vars from .env into your shell, or use a tool like `dotenvx`/`direnv`

python main.py
```

The server listens on `PORT` (default `8000`) over Streamable HTTP.

## Running the tests

```bash
pytest
```

The suite only covers pure logic that doesn't need a live OIDC provider or OpenRouter
account: extension sniffing, base64 decoding, the SSRF host guard, path resolution
under `FILES_DIR`, and the cost-ledger formatting. `tests/conftest.py` fills in fake
env vars so `src/config.py` (which reads required vars at import time) doesn't error
out during collection.

## Adding a tool

1. Add the `@mcp.tool` function to the right module under `src/tools/` (or a new
   module, if it's a new domain) — see any existing tool for the pattern: `await
   _check_user()`, then `await _check_budget()` for anything that spends credits.
2. If it's a new module, add it to the import list in `src/tools/__init__.py`.
3. Update the tools table in the [usage guide](usage-guide.md).
