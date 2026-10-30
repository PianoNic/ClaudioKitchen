# SPDX-License-Identifier: Apache-2.0
"""Environment-derived configuration and constants shared across the server."""

import os
import secrets
from pathlib import Path

import httpx

# ----------------- OIDC -----------------
OIDC_CONFIG_URL = os.environ["OIDC_CONFIG_URL"]
OIDC_CLIENT_ID = os.environ["OIDC_CLIENT_ID"]
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
BASE_URL = os.environ["BASE_URL"]

# Optional extra gate. When set, the named claim must be present and truthy on
# the id token or userinfo document. When unset, anyone who can complete the
# OIDC login may call tools (the provider's own user/group rules still apply).
REQUIRED_CLAIM = (os.environ.get("REQUIRED_CLAIM") or "").strip()

# Scopes requested from the IdP. `openid` yields an id token; `profile` is what
# Pocket ID uses to emit custom claims. Override if a provider releases
# REQUIRED_CLAIM under a different scope.
OIDC_SCOPES = " ".join((os.environ.get("OIDC_SCOPES") or "openid profile").split()) or "openid profile"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# The client's authorize request includes resource=<this server>/mcp (RFC 8707).
# Pocket ID, and most other IdPs, reject that URL unless it is a registered API.
# Leave this off so the IdP sees a normal login. Turn it on only after the
# provider is configured to accept this server's resource URL.
OIDC_FORWARD_RESOURCE = _env_flag("OIDC_FORWARD_RESOURCE", False)

# Origin of this server (scheme + host), so a sandboxed MCP Apps view may load a
# resource (e.g. an SVG) from a /files download URL under BASE_URL.
_BU = httpx.URL(BASE_URL)
BASE_ORIGIN = f"{_BU.scheme}://{_BU.netloc.decode()}"

# ----------------- OpenRouter -----------------
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OR_BASE = "https://openrouter.ai/api/v1"
OR_HEADERS = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}

# Ask OpenRouter to include cost in every chat/completions response
USAGE = {"include": True}

# Defaults (override with any OpenRouter model id, or pass `model` per call)
DEFAULT_IMAGE_MODEL = os.environ.get("DEFAULT_IMAGE_MODEL", "google/gemini-2.5-flash-image")
DEFAULT_TTS_MODEL = os.environ.get("DEFAULT_TTS_MODEL", "openai/gpt-audio")
DEFAULT_STT_MODEL = os.environ.get("DEFAULT_STT_MODEL", "google/gemini-2.5-flash")

# ----------------- Files -----------------
FILES_DIR = Path(os.environ.get("FILES_DIR", "./generated")).resolve()
FILES_DIR.mkdir(parents=True, exist_ok=True)

# Token required to download generated files (?token=...). Auto-generated if unset;
# set FILES_TOKEN in .env to keep download URLs valid across restarts.
FILES_TOKEN = os.environ.get("FILES_TOKEN") or secrets.token_urlsafe(24)
if not os.environ.get("FILES_TOKEN"):
    print(f"[files] FILES_TOKEN not set; generated one for this run: {FILES_TOKEN}",
          flush=True)

# Append-only ledger of per-request cost (USD). Lives next to FILES_DIR so it
# persists on the same volume. Set USAGE_LOG to relocate it.
USAGE_LOG = Path(os.environ.get("USAGE_LOG", FILES_DIR.parent / "usage.jsonl")).resolve()

# Optional hard monthly spend cap (USD). When set, generating tools refuse to
# run once the current calendar month's recorded spend reaches this amount.
MONTHLY_BUDGET_USD = (
    float(os.environ["MONTHLY_BUDGET_USD"]) if os.environ.get("MONTHLY_BUDGET_USD") else None
)

# Hard ceilings (bytes) on inbound uploads and outbound fetches to bound memory.
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", 100 * 1024 * 1024))
FETCH_MAX_BYTES = int(os.environ.get("FETCH_MAX_BYTES", 100 * 1024 * 1024))
