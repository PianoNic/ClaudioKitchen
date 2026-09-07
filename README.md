<p align="center">
  <strong>🍳 ClaudioKitchen</strong>
</p>
<p align="center">
  Remote MCP server that gives Claude real multimodal generation: image, video, TTS, transcription, embeddings, and rerank, via OpenRouter.
</p>
<p align="center">
  <a href="https://github.com/PianoNic/ClaudioKitchen"><img src="https://badgetrack.pianonic.ch/badge?tag=claudiokitchen&label=visits&color=fb923c&style=flat" alt="visits"/></a>
  <a href="docs/configuration.md"><img src="https://img.shields.io/badge/Configuration-Docs-fb923c.svg" alt="Configuration"/></a>
  <a href="docs/usage-guide.md"><img src="https://img.shields.io/badge/Usage-Guide-fb923c.svg" alt="Usage guide"/></a>
  <a href="docs/dev_setup.md"><img src="https://img.shields.io/badge/Development-Setup-fb923c.svg" alt="Development"/></a>
</p>

---

## What is ClaudioKitchen?

It's a Streamable HTTP MCP server that adds OIDC auth (Pocket ID, or any OIDC
provider) with an email allowlist, then exposes OpenRouter's generation APIs as tools:
generate/edit/describe images, generate video, text-to-speech, transcription,
embeddings, rerank, plus file management and per-request cost tracking.

Claude's remote MCP connectors require OAuth with **Dynamic Client Registration
(DCR)**, which most OIDC providers (including Pocket ID) don't support. FastMCP's
`OIDCProxy` bridges the gap: Claude registers dynamically against this server, and the
server proxies the real login to your OIDC provider.

## Setup

1. Copy `template.env` to `.env` and fill in your OIDC and OpenRouter values. See
   [Configuration](docs/configuration.md) for every variable and how to set up an OIDC
   client (Pocket ID walkthrough included).
2. `docker compose up -d` (pulls the published image), or
   `docker compose -f compose.dev.yml up --build` to build from source.
3. In claude.ai: **Settings → Connectors → Add custom connector**, URL:
   `https://<your-mcp-domain>/mcp`. Claude redirects you to your OIDC provider, you log
   in, and you're done.

## Tools

| Tool | What it does |
|---|---|
| `generate_image` / `edit_image` / `describe_image` | create, edit/combine, or analyze (OCR/vision) images; renders inline in claude.ai via MCP Apps |
| `generate_video` / `check_video` | async video generation (image-to-video supported), poll or wait inline |
| `text_to_speech` / `transcribe_audio` | speech synthesis and transcription |
| `create_embeddings` / `rerank` | embedding vectors and relevance reranking |
| `list_models` / `list_video_models` | discover any OpenRouter model id, with pricing |
| `upload_file` / `create_upload_url` | get any file (image, PDF, audio, ...) onto the server as a download URL |
| `list_files` / `delete_file` / `cleanup_files` | manage stored files |
| `usage_summary` | spend today / this month / all-time, per tool |

Full behavior, model overrides, and the two video run modes are in the
[usage guide](docs/usage-guide.md).

## Security

OIDC auth + email allowlist on every call, a token-gated download route, an SSRF guard
on every outbound fetch, and `nosniff`/attachment headers on stored files. Details and
known limitations: [docs/security.md](docs/security.md).

## License

[Apache-2.0](LICENSE) © pianonic

---
<p align="center">Made with ❤️ by <a href="https://github.com/PianoNic">PianoNic</a></p>
