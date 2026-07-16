# 🍳 ClaudioKitchen — OpenRouter Multimodal MCP

Remote MCP server (Streamable HTTP) for Claude with OIDC auth (Pocket ID etc.) and an email allowlist, exposing OpenRouter's image / video / TTS / transcription / embeddings / rerank APIs — plus file management and per-request cost tracking.

## Why an OIDC *proxy*?
Claude's remote MCP connectors require OAuth with **Dynamic Client Registration (DCR)**. Pocket ID (and most OIDC providers) don't support DCR, so FastMCP's `OIDCProxy` sits in between: Claude registers dynamically against the MCP server, which proxies the actual login to your OIDC provider. After login, `ALLOWED_EMAILS` is enforced on every tool call.

## Setup

1. **Pocket ID**: create a new OIDC client
   - Callback URL: `https://<your-mcp-domain>/auth/callback`
   - Copy client id + secret
2. Copy `.env.example` -> `.env` and fill everything in. `BASE_URL` must be the public HTTPS URL (reverse proxy in front, e.g. Traefik/Caddy/Pangolin).
3. `docker compose up -d --build`
4. In claude.ai: **Settings → Connectors → Add custom connector** → URL: `https://<your-mcp-domain>/mcp`
   - Claude will redirect you to Pocket ID, you log in, done.

## Tools

| Tool | OpenRouter endpoint |
|---|---|
| `list_models` | `GET /models?output_modalities=...` |
| `list_video_models` | `GET /videos/models` |
| `upload_image` | stores base64 / data-URL / re-hosted URL → returns a usable URL |
| `generate_image` | `POST /chat/completions` with `modalities` |
| `edit_image` | `POST /chat/completions` (input image(s) + instruction) |
| `describe_image` | `POST /chat/completions` (vision) |
| `generate_video` / `check_video` | `POST /videos`, `GET /videos/{id}` (async job) |
| `text_to_speech` | `POST /chat/completions` (streamed `pcm16` audio → WAV) |
| `transcribe_audio` | `POST /chat/completions` (`input_audio`) |
| `create_embeddings` | `POST /embeddings` |
| `rerank` | `POST /rerank` |
| `list_files` / `delete_file` / `cleanup_files` | manage files stored under `FILES_DIR` (list, delete one, bulk-prune by age / keep-newest) |
| `usage_summary` | today / this-month / all-time spend (USD) + per-tool breakdown |

All `model` params accept **any** OpenRouter model id — discover them with `list_models` / `list_video_models`. Defaults are overridable via env (`DEFAULT_IMAGE_MODEL`, `DEFAULT_TTS_MODEL`, `DEFAULT_STT_MODEL`). For **image-only** models (Flux, Sourceful, Recraft, …) set `text_and_image=False` on `generate_image`.

**Per-request cost:** every generating tool reports the price of that request in USD — image/edit append a `💲 Request cost` line; the others return a `cost_usd` field. (OpenRouter credits = USD.)

**Video — two modes:**
- `generate_video(..., wait=False)` *(default)*: submits and returns a job id; poll later with `check_video`.
- `generate_video(..., wait=True, wait_timeout=600)`: polls internally until the clip is finished (emitting progress notifications), then returns the completed result with the downloaded video URL. Best for short clips; if it exceeds `wait_timeout` it returns the job id so you can continue with `check_video`.

> Note: a remote MCP server can only return data as the result of an active tool call (or progress notifications during one). It **cannot** push an unsolicited message into a Claude chat after the turn ends — `wait=True` is the way to have Claude report the finished video automatically.

Generated images/audio/video are saved to `FILES_DIR` and served at `/files/<uuid>.<ext>?token=<FILES_TOKEN>`. Completed videos are pulled from OpenRouter's `unsigned_urls` onto this server (their URLs expire).

## Uploading your own images (e.g. to edit a photo)
Three ways, pick what fits:

**A) Claude with a sandbox (recommended, no base64):** Claude calls `create_upload_url` to mint a short-lived ticket, then PUTs the raw file bytes directly:
```
curl -T /path/to/photo.png "<upload_url>"
```
The PUT returns `{"url": "https://.../files/<id>.jpg?token=..."}` — Claude passes that `url` to `edit_image` / `describe_image` / `generate_video`. Tickets are time-limited (`expires_in`, default 900 s) and use-limited (`max_uses`, default 5).

**B) Browser uploader (for a pasted chat image):** open **`<BASE_URL>/upload?token=<FILES_TOKEN>`**, drag-drop, copy the returned URL into chat.

**C) `upload_image` tool:** for images Claude already has as base64 / `data:` / http URL. Tolerant base64 decoding + image-type auto-detection (JPEG/PNG/GIF/WEBP/BMP).

## Cost tracking & budget
Every generating tool records its per-request USD cost to an append-only ledger (`usage.jsonl`, next to `FILES_DIR` on the volume; relocate with `USAGE_LOG`). Ask for **`usage_summary`** to see today / this-month / all-time spend with a per-tool breakdown. Set **`MONTHLY_BUDGET_USD`** to a hard cap — once this calendar month's recorded spend reaches it, generating tools refuse to run until next month. Video jobs are recorded once (deduped by job id) even if you poll `check_video` repeatedly.

## Managing generated files
Files served under `/files` accumulate on the volume. Use **`list_files`** (name / size / modified / URL + totals), **`delete_file`** (by filename or download URL), and **`cleanup_files`** (bulk-prune by `older_than_days` and/or `keep_newest`; `dry_run=True` previews first).

## File security
The `/files/` route requires `?token=<FILES_TOKEN>`. Set `FILES_TOKEN` in `.env` (a stable secret) so download links keep working across restarts; if unset, a random one is generated per run and logged. Returned download URLs already include the token.

Hardening applied in this server:
- Downloads are served with `X-Content-Type-Options: nosniff` and `Content-Disposition: attachment`, so a stored `.svg`/`.html` can't execute script in the server's origin.
- Outbound fetches (`upload_image` from a URL, `edit_image`, `describe_image`, `transcribe_audio`) are restricted to public **http(s)** hosts — loopback, link-local (incl. `169.254.169.254`), and private ranges are blocked (SSRF guard) — with redirects disabled and a size cap (`FETCH_MAX_BYTES`, default 100 MB). Uploads are capped by `UPLOAD_MAX_BYTES`.

> Known limitation: `FILES_TOKEN` is a single global secret carried in the URL query string. Don't pass `/files` URLs to third-party APIs, and configure your reverse proxy to strip query strings from access logs. Rotating it invalidates all existing download links.

## Notes
- Video generation costs credits the moment you submit — use `list_video_models` first to check pricing/capabilities.
- TTS uses streamed `pcm16` (OpenRouter requires `stream:true` for audio output) wrapped into a 24 kHz mono WAV.
- For local testing without HTTPS you can tunnel with `cloudflared`/`ngrok` and set that URL as `BASE_URL`.
- A `/health` endpoint (unauthenticated) is available for container/reverse-proxy health checks; the Docker image wires it into a `HEALTHCHECK`.

## License
[Apache-2.0](LICENSE) © pianonic
