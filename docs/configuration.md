# Configuration

Copy [`template.env`](../template.env) to `.env` and fill in real values. Never commit
`.env`. `compose.yml` picks it up via `env_file: .env`.

```bash
cp template.env .env
```

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `OIDC_CONFIG_URL` | yes | n/a | Your OIDC provider's discovery document URL (e.g. Pocket ID's `.well-known/openid-configuration`). |
| `OIDC_CLIENT_ID` | yes | n/a | OIDC client id, from the client you create in your provider. |
| `OIDC_CLIENT_SECRET` | yes | n/a | OIDC client secret. |
| `BASE_URL` | yes | n/a | Public HTTPS URL of this server, as seen by Claude (no trailing slash). Used to build callback, download, and upload URLs. Must sit behind a reverse proxy / tunnel that terminates TLS. |
| `ALLOWED_EMAILS` | yes | n/a | Comma-separated allowlist. Only these emails (matched via the OIDC provider's userinfo) may call any tool. Case-insensitive. |
| `OPENROUTER_API_KEY` | yes | n/a | From [openrouter.ai/keys](https://openrouter.ai/keys). OpenRouter credits are billed in USD. |
| `PORT` | no | `8000` | Port the server listens on. |
| `FILES_DIR` | no | `./generated` | Where generated/uploaded files are stored. The Docker image sets this to `/data/generated`. |
| `FILES_TOKEN` | no | random per run | Secret that gates the `/files/<id>` download route (`?token=...`). Set a **stable** value so download links survive restarts; if unset, a random one is generated and printed to the logs each run. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(24))"`. |
| `USAGE_LOG` | no | `<FILES_DIR>/../usage.jsonl` | Path to the append-only cost ledger. |
| `MONTHLY_BUDGET_USD` | no | unset (no cap) | Hard monthly spend cap. Once this calendar month's recorded spend reaches it, generating tools refuse to run until next month. |
| `DEFAULT_IMAGE_MODEL` | no | `google/gemini-2.5-flash-image` | Default model for `generate_image` / `edit_image`. Any OpenRouter image model id works via the `model` param. |
| `DEFAULT_TTS_MODEL` | no | `openai/gpt-audio` | Default model for `text_to_speech`. |
| `DEFAULT_STT_MODEL` | no | `google/gemini-2.5-flash` | Default model for `transcribe_audio`. |
| `UPLOAD_MAX_BYTES` | no | `104857600` (100 MB) | Hard ceiling on inbound uploads. |
| `FETCH_MAX_BYTES` | no | `104857600` (100 MB) | Hard ceiling on outbound fetches (e.g. `edit_image` downloading an image URL). |

## Docker deployment

### Images

A GitHub Actions workflow (`.github/workflows/docker-publish.yml`) builds and pushes an
image to the GitHub Container Registry on every push to `main` and on `v*` tags:

```bash
docker pull ghcr.io/pianonic/claudiokitchen:latest
```

Tags published: `latest` (default branch), the short commit SHA, and semver tags like
`1.2` / `1.2.3` when you push a `v1.2.3` tag. The package is private until you set it
public in the repo's package settings.

### Run the published image

`compose.yml` pulls the published image:

```yaml
services:
  openrouter-mcp:
    image: ghcr.io/pianonic/claudiokitchen:latest
    env_file: .env
    ports:
      - "3417:8000"
    volumes:
      - ./data:/data
    restart: unless-stopped
```

```bash
docker compose up -d
```

If your reverse proxy (Traefik/Caddy/Pangolin) runs on the same host, bind the port to
loopback instead so nothing but the proxy can reach the app:
`"127.0.0.1:3417:8000"`.

### Build from source (local development)

`compose.dev.yml` builds the image from this repo instead of pulling it:

```bash
docker compose -f compose.dev.yml up --build
```

## Setting up the OIDC provider (Pocket ID example)

Claude's remote MCP connectors require OAuth with **Dynamic Client Registration
(DCR)**, which Pocket ID (like most OIDC providers) doesn't support. FastMCP's
`OIDCProxy` bridges the gap: Claude registers dynamically against this MCP server, and
the server proxies the real login to your OIDC provider.

1. In Pocket ID, create a new OIDC client.
2. Set its callback URL to `https://<your-mcp-domain>/auth/callback`.
3. Copy the client id and secret into `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET`.
4. In claude.ai: **Settings → Connectors → Add custom connector**, URL:
   `https://<your-mcp-domain>/mcp`. Claude redirects to Pocket ID; once you log in,
   `ALLOWED_EMAILS` is enforced on every tool call.

For local testing without HTTPS, tunnel with `cloudflared` or `ngrok` and set that URL
as `BASE_URL`.
