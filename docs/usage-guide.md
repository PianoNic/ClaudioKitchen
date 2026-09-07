# Usage guide

## Tools

| Tool | OpenRouter endpoint |
|---|---|
| `list_models` | `GET /models?output_modalities=...` |
| `list_video_models` | `GET /videos/models` |
| `upload_file` / `create_upload_url` | store **any** file, returns a download URL. Preferred: PUT raw bytes via `create_upload_url`, the browser uploader, or re-host an http URL. base64 is a fallback for small data. |
| `generate_image` | `POST /chat/completions` with `modalities` |
| `edit_image` | `POST /chat/completions` (input image(s) + instruction) |
| `describe_image` | `POST /chat/completions` (vision) |
| `generate_video` / `check_video` | `POST /videos`, `GET /videos/{id}` (async job) |
| `text_to_speech` | `POST /chat/completions` (streamed `pcm16` audio to WAV) |
| `transcribe_audio` | `POST /chat/completions` (`input_audio`) |
| `create_embeddings` | `POST /embeddings` |
| `rerank` | `POST /rerank` |
| `list_files` / `delete_file` / `cleanup_files` | manage files under `FILES_DIR` (list, delete one, bulk-prune by age or keep-newest) |
| `usage_summary` | today / this-month / all-time spend (USD) with a per-tool breakdown |

Every `model` param accepts **any** OpenRouter model id. Discover them with
`list_models` / `list_video_models`. Defaults are overridable via env
(`DEFAULT_IMAGE_MODEL`, `DEFAULT_TTS_MODEL`, `DEFAULT_STT_MODEL`, see
[configuration](configuration.md)). Image-only models (Flux, Sourceful, Recraft, etc.)
are handled automatically: if a model rejects the `text` modality, `generate_image`
retries image-only. That includes Recraft's **vector** models (e.g.
`recraft/recraft-v4.1-pro-vector`), which return a real **SVG** saved as `.svg`.

**Per-request cost:** every generating tool reports what the request cost in USD.
`generate_image` and `edit_image` append a `💲 Request cost` line; the rest return a
`cost_usd` field. (OpenRouter credits are USD.)

## Inline image display (MCP Apps)

`generate_image` and `edit_image` ship an
[MCP Apps](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/) UI resource
(`ui://claudiokitchen/image.html`, see `src/image_view.py`). On hosts that support MCP
Apps (claude.ai), the result renders **inline in the chat** instead of only as a
download link. This works around a claude.ai limitation where a tool's image content
is otherwise shown only inside the collapsed tool call. Hosts without MCP Apps support
ignore it and fall back to the image block plus the download link, so nothing is lost.

## Video: two run modes

- `generate_video(..., wait=False)` *(default)*: submits the job and returns a job id.
  Poll it later with `check_video`.
- `generate_video(..., wait=True, wait_timeout=600)`: polls internally until the clip is
  done, emitting progress notifications, then returns the finished result with the
  downloaded video URL. Good for short clips. If it exceeds `wait_timeout` it returns
  the job id so you can continue with `check_video`.

> Note: a remote MCP server can only return data as the result of an active tool call,
> or as progress notifications during one. It **cannot** push an unsolicited message
> into a Claude chat after the turn ends. Use `wait=True` to have Claude report the
> finished video automatically.

Generated images, audio, and video are saved to `FILES_DIR` and served at
`/files/<uuid>.<ext>?token=<FILES_TOKEN>`. Completed videos are pulled from
OpenRouter's `unsigned_urls` onto this server, since those URLs expire.

## Uploading your own files (images, PDFs, audio)

Any file type works. The extension comes from the filename you provide, or is sniffed
from the bytes and content-type (falling back to `.bin`). Downloads are always served
as attachments with `nosniff`. There are three ways to upload.

**A) Claude with a sandbox (recommended, no base64):** Claude calls `create_upload_url`
to mint a short-lived ticket, then PUTs the raw file bytes directly:
```
curl -T /path/to/report.pdf "<upload_url>&name=report.pdf"
```
The PUT returns `{"url": "https://.../files/<id>.pdf?token=..."}`. Claude passes that
`url` to the user, or to `edit_image` / `describe_image` / `transcribe_audio` /
`generate_video`. Add `&name=<filename>` to keep the right extension. Tickets are
time-limited (`expires_in`, default 900 s) and use-limited (`max_uses`, default 5).

**B) Browser uploader (for a file pasted or attached in chat):** open
**`<BASE_URL>/upload?token=<FILES_TOKEN>`**, drag-drop any file, and copy the returned
URL into chat.

**C) `upload_file` tool:** for a file Claude already has as base64, a `data:` URL, or
an http URL. Pass `filename` to preserve the extension. Base64 decoding is tolerant,
and the type is auto-detected (images, PDF, MP4, WAV, MP3, OGG, FLAC, ZIP, and more).

## Cost tracking and budget

Every generating tool records its per-request USD cost to an append-only ledger
(`usage.jsonl`, stored next to `FILES_DIR` on the volume; relocate it with
`USAGE_LOG`). Ask for **`usage_summary`** to see today / this-month / all-time spend
with a per-tool breakdown. Set **`MONTHLY_BUDGET_USD`** to a hard cap. Once this
calendar month's recorded spend reaches it, generating tools refuse to run until next
month. Video jobs are recorded once (deduped by job id) even if you poll `check_video`
repeatedly.

## Managing generated files

Files served under `/files` accumulate on the volume. Use **`list_files`** (name, size,
modified time, URL, plus totals), **`delete_file`** (by filename or download URL), and
**`cleanup_files`** (bulk-prune by `older_than_days` and/or `keep_newest`;
`dry_run=True` previews first).

## Notes

- Video generation costs credits the moment you submit. Check pricing and capabilities
  with `list_video_models` first.
- TTS uses streamed `pcm16` (OpenRouter requires `stream:true` for audio output),
  wrapped into a 24 kHz mono WAV.
- A `/health` endpoint (unauthenticated) is available for container and reverse-proxy
  health checks. The Docker image wires it into a `HEALTHCHECK`.

See also: [Configuration](configuration.md) for env vars and Docker deployment,
[Security](security.md) for the file token and SSRF guard, and
[Development setup](dev_setup.md) for the code layout.
