# SPDX-License-Identifier: Apache-2.0
"""
ClaudioKitchen - OpenRouter Multimodal MCP Server
- Remote MCP (Streamable HTTP), works with Claude (claude.ai custom connector)
- OAuth via OIDC proxy (Pocket ID) + email allowlist -> only you can use it
- Tools: model discovery, image gen + edit, video gen (async), TTS,
  transcription, embeddings, rerank, file management, usage/cost tracking
"""

import os
import io
import json
import time
import wave
import base64
import uuid
import socket
import secrets
import asyncio
import ipaddress
import mimetypes
import datetime as dt
from pathlib import Path

import httpx
from fastmcp import FastMCP, Context
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.types import Image
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, HTMLResponse

# ----------------- Config -----------------
OIDC_CONFIG_URL = os.environ["OIDC_CONFIG_URL"]
OIDC_CLIENT_ID = os.environ["OIDC_CLIENT_ID"]
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
BASE_URL = os.environ["BASE_URL"]
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ["ALLOWED_EMAILS"].split(",")}
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

OR_BASE = "https://openrouter.ai/api/v1"
OR_HEADERS = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}

# Defaults (override with any OpenRouter model id, or pass `model` per call)
DEFAULT_IMAGE_MODEL = os.environ.get("DEFAULT_IMAGE_MODEL", "google/gemini-2.5-flash-image")
DEFAULT_TTS_MODEL = os.environ.get("DEFAULT_TTS_MODEL", "openai/gpt-audio")
DEFAULT_STT_MODEL = os.environ.get("DEFAULT_STT_MODEL", "google/gemini-2.5-flash")

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

_usage_lock = asyncio.Lock()  # serialize ledger appends across concurrent tool calls

# Hard ceilings (bytes) on inbound uploads and outbound fetches to bound memory.
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", 100 * 1024 * 1024))
FETCH_MAX_BYTES = int(os.environ.get("FETCH_MAX_BYTES", 100 * 1024 * 1024))

# Short-lived single-purpose upload tickets: ticket -> {"exp": ts, "uses": n}
_upload_tickets: dict[str, dict] = {}


def _consume_ticket(ticket: str) -> bool:
    """Validate a one-time upload ticket; decrement its remaining uses."""
    now = time.time()
    for t, meta in list(_upload_tickets.items()):  # purge expired
        if meta["exp"] < now:
            _upload_tickets.pop(t, None)
    meta = _upload_tickets.get(ticket)
    if not meta or meta["exp"] < now or meta["uses"] <= 0:
        return False
    meta["uses"] -= 1
    if meta["uses"] <= 0:
        _upload_tickets.pop(ticket, None)
    return True

auth = OIDCProxy(
    config_url=OIDC_CONFIG_URL,
    client_id=OIDC_CLIENT_ID,
    client_secret=OIDC_CLIENT_SECRET,
    base_url=BASE_URL,
    required_scopes=[],
    extra_authorize_params={"scope": "openid email profile"},
)

# Sent to the client on initialize (MCP `instructions`). Clients like Claude may fold
# this into the system prompt, so it's where we tell Claude it CAN generate media here.
SERVER_INSTRUCTIONS = """\
ClaudioKitchen gives you real multimodal generation through OpenRouter. You are NOT
limited to text on this connection: when the user asks you to create / generate / draw
/ make an image, edit or combine images, describe or OCR an image, generate a video,
speak text aloud (text-to-speech), transcribe audio, or embed / rerank text, actually
DO IT by calling the matching tool below. Never reply that you "can't generate images,
video, or audio" — through these tools, you can.

Routing cheatsheet:
- "generate / make / draw / create an image of ..."  -> generate_image
- "edit / change / combine / restyle this image"     -> edit_image (give it image URL[s])
- "what's in this image / read the text in it"       -> describe_image
- "make / generate a video of ..."                   -> generate_video (spends credits)
- "read this aloud / say ... / voice this"           -> text_to_speech
- "transcribe / what is said in this audio"          -> transcribe_audio
- "embed these texts" / "rerank these by relevance"  -> create_embeddings / rerank

Each generating tool returns a download URL and reports its USD cost. If unsure which
model to use, call list_models / list_video_models first; otherwise the defaults are
fine. To put a file (image, PDF, audio, ...) on this server, use upload_file, or mint a
browser/PUT link with create_upload_url; the returned URL feeds edit_image /
describe_image / transcribe_audio / generate_video. Use list_files / usage_summary to
manage stored files and track spend.
"""

mcp = FastMCP("ClaudioKitchen", instructions=SERVER_INSTRUCTIONS, auth=auth)

# Caches to avoid repeated network calls
_email_cache: dict[str, str] = {}        # sub -> email
_userinfo_endpoint_cache: dict[str, str] = {}  # issuer -> userinfo endpoint


def _extract_email_from_claims(claims: dict) -> str:
    return (
        claims.get("email")
        or (claims.get("userinfo") or {}).get("email")
        or ""
    ).lower()


async def _userinfo_endpoint(issuer: str, client: httpx.AsyncClient) -> str:
    """Resolve the userinfo endpoint from the issuer's OIDC discovery doc (cached)."""
    if issuer in _userinfo_endpoint_cache:
        return _userinfo_endpoint_cache[issuer]
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    r = await client.get(url)
    r.raise_for_status()
    endpoint = r.json().get("userinfo_endpoint", "")
    if endpoint:
        _userinfo_endpoint_cache[issuer] = endpoint
    return endpoint


async def _check_user():
    """Allow only whitelisted emails (your Pocket ID account).

    The access token Pocket ID issues carries no email claim, so we resolve it via
    the provider's userinfo endpoint (discovered from the token issuer) and cache it.
    """
    token = get_access_token()
    claims = getattr(token, "claims", {}) or {}

    email = _extract_email_from_claims(claims)
    sub = claims.get("sub") or ""

    if not email and sub in _email_cache:
        email = _email_cache[sub]

    # Resolve via userinfo using the raw access token Claude presented
    if not email:
        issuer = claims.get("iss") or OIDC_CONFIG_URL.split("/.well-known")[0]
        raw_token = getattr(token, "token", None)
        if issuer and raw_token:
            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    endpoint = await _userinfo_endpoint(issuer, c)
                    if endpoint:
                        r = await c.get(
                            endpoint,
                            headers={"Authorization": f"Bearer {raw_token}"},
                        )
                        r.raise_for_status()
                        email = (r.json().get("email") or "").lower()
                        if sub and email:
                            _email_cache[sub] = email
            except Exception as e:
                print(f"[auth] userinfo lookup failed: {e}", flush=True)

    if email not in ALLOWED_EMAILS:
        print(f"[auth] DENIED user '{email or 'unknown'}' (claims={claims})", flush=True)
        raise PermissionError(
            f"User '{email or 'unknown'}' is not allowed to use this server."
        )


def _save_file(data: bytes, ext: str) -> str:
    name = f"{uuid.uuid4().hex}.{ext}"
    (FILES_DIR / name).write_bytes(data)
    return f"{BASE_URL}/files/{name}?token={FILES_TOKEN}"


def _sniff_image_ext(raw: bytes, fallback: str = "png") -> str:
    """Detect the real image type from magic bytes so the file gets the right extension."""
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:2] == b"BM":
        return "bmp"
    return "".join(ch for ch in fallback if ch.isalnum())[:5].lower() or "png"


def _clean_ext(name: str) -> str:
    """Sanitize an extension taken from a filename (alphanumeric, <=8 chars)."""
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return "".join(ch for ch in ext if ch.isalnum()).lower()[:8]


def _sniff_ext(raw: bytes, name: str = "", content_type: str = "") -> str:
    """Pick a file extension for ANY uploaded file (not just images).

    Order: the caller's filename extension (preserves .pdf/.csv/.json/.docx/...),
    then magic-byte detection, then the content-type, else 'bin'. Downloads are
    always served as attachments with nosniff, so a preserved extension is safe.
    """
    ext = _clean_ext(name)
    if ext:
        return ext
    # magic-byte detection for common formats
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return "wav"
    if raw[:2] == b"BM":
        return "bmp"
    if raw[:4] == b"%PDF":
        return "pdf"
    if raw[4:8] == b"ftyp":
        return "mp4"
    if raw[:3] == b"ID3" or raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if raw[:4] == b"OggS":
        return "ogg"
    if raw[:4] == b"fLaC":
        return "flac"
    if raw[:4] == b"PK\x03\x04":
        return "zip"
    if raw[:2] == b"\x1f\x8b":
        return "gz"
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        # a few common types mimetypes can miss depending on the OS registry
        extra = {"image/webp": "webp", "image/svg+xml": "svg", "audio/mpeg": "mp3",
                 "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg",
                 "video/mp4": "mp4", "video/webm": "webm"}
        if ct in extra:
            return extra[ct]
        guessed = mimetypes.guess_extension(ct)
        if guessed:
            return guessed.lstrip(".").lower()
    return "bin"


def _decode_b64(data: str) -> bytes:
    """Tolerant base64 decode: strips data: prefixes, whitespace, and fixes padding."""
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    data = "".join(data.split())  # drop whitespace/newlines
    data += "=" * (-len(data) % 4)  # fix missing padding
    return base64.b64decode(data)


def _host_is_public(host: str) -> bool:
    """True only if every resolved IP for `host` is a routable public address.

    Blocks SSRF into loopback, link-local (incl. cloud metadata 169.254.169.254),
    RFC-1918/ULA private ranges, and other reserved space.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0].split("%")[0]  # strip IPv6 zone id
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


async def _safe_fetch(c: httpx.AsyncClient, url: str,
                      max_bytes: int = FETCH_MAX_BYTES) -> tuple[bytes, str]:
    """Fetch a caller/model-supplied URL defensively: http(s) only, public hosts
    only (SSRF guard), no redirects, and a hard size cap. Returns (bytes, content_type)."""
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs are allowed (got {parsed.scheme!r}).")
    # Files hosted on this server's own /files store are trusted (skip the SSRF host
    # check so uploaded files work even when BASE_URL is a private/tunnel host).
    if not url.startswith(BASE_URL.rstrip("/") + "/"):
        host = parsed.host
        if not host or not _host_is_public(host):
            raise ValueError(f"Refusing to fetch non-public or unresolvable host: {host!r}")
    buf = bytearray()
    async with c.stream("GET", url, follow_redirects=False) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        async for chunk in r.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(f"Remote resource exceeds the {max_bytes}-byte limit.")
    return bytes(buf), ctype


def _pcm16_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap raw 16-bit mono PCM (what OpenRouter streams for TTS) in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


# Ask OpenRouter to include cost in every chat/completions response
USAGE = {"include": True}


def _cost(usage: dict | None) -> float | None:
    """Extract the request cost (USD) from an OpenRouter usage object."""
    if not isinstance(usage, dict):
        return None
    return usage.get("cost")


def _cost_note(usage: dict | None) -> str:
    c = _cost(usage)
    return f"💲 Request cost: ${c:.6f} USD" if c is not None else "💲 Request cost: unknown"


# ----------------- Usage ledger & budget -----------------
def _read_usage() -> list[dict]:
    """Load the append-only cost ledger (tolerant of partial/corrupt lines)."""
    if not USAGE_LOG.is_file():
        return []
    out = []
    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


async def _record_cost(tool: str, model: str | None, usage: dict | None,
                       dedupe_key: str | None = None) -> None:
    """Append one request's cost to the ledger. No-op when cost is unknown.

    Pass dedupe_key (e.g. a video job id) to record a cost at most once even if
    the tool is polled repeatedly.
    """
    cost = _cost(usage)
    if cost is None:
        return
    async with _usage_lock:
        if dedupe_key is not None:
            for r in _read_usage():
                if r.get("dedupe_key") == dedupe_key:
                    return
        entry: dict = {"ts": time.time(), "tool": tool, "model": model, "cost_usd": cost}
        if dedupe_key is not None:
            entry["dedupe_key"] = dedupe_key
        with USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def _month_start_ts() -> float:
    now = dt.datetime.now(dt.timezone.utc)
    return dt.datetime(now.year, now.month, 1, tzinfo=dt.timezone.utc).timestamp()


def _day_start_ts() -> float:
    now = dt.datetime.now(dt.timezone.utc)
    return dt.datetime(now.year, now.month, now.day, tzinfo=dt.timezone.utc).timestamp()


def _spend_since(ts: float, records: list[dict] | None = None) -> float:
    recs = records if records is not None else _read_usage()
    return sum((r.get("cost_usd") or 0.0) for r in recs if (r.get("ts") or 0) >= ts)


async def _check_budget() -> None:
    """Block a generating call if the monthly budget cap is already reached."""
    if MONTHLY_BUDGET_USD is None:
        return
    spent = _spend_since(_month_start_ts())
    if spent >= MONTHLY_BUDGET_USD:
        raise RuntimeError(
            f"Monthly budget reached: spent ${spent:.4f} of ${MONTHLY_BUDGET_USD:.2f} "
            f"this month. Raise MONTHLY_BUDGET_USD or wait until next month."
        )


def _images_from_message(msg: dict) -> list:
    """Convert chat-completions image outputs to MCP Image blocks + download URLs."""
    images = msg.get("images") or []
    if not images:
        return [f"No image returned. Model said: {msg.get('content')}"]
    out = []
    for img in images:
        url = ((img or {}).get("image_url") or {}).get("url", "")
        if url.startswith("data:") and "," in url:
            raw = _decode_b64(url)
            ext = _sniff_image_ext(raw, fallback="png")
            out.append(Image(data=raw, format=ext))
            out.append(f"Download: {_save_file(raw, ext)}")
        elif url.startswith(("http://", "https://")):
            # Some providers return a hosted URL rather than inline bytes.
            out.append(f"Image URL: {url}")
        elif url:
            out.append(f"Unrecognized image payload: {url[:80]}")
    if msg.get("content"):
        out.append(msg["content"])
    return out


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    """Unauthenticated liveness probe for container/reverse-proxy health checks."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/files/{name}", methods=["GET"])
async def serve_file(request: Request):
    token = request.query_params.get("token", "")
    if not secrets.compare_digest(token, FILES_TOKEN):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    name = request.path_params["name"]
    path = (FILES_DIR / name).resolve()
    if not path.is_file() or FILES_DIR not in path.parents:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Never let the browser sniff/execute a stored file in this origin (which carries
    # the OIDC session): disable content-type sniffing and force a download.
    return FileResponse(path, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'attachment; filename="{path.name}"',
    })


_UPLOAD_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>ClaudioKitchen · Upload</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#1c1917;color:#e7e5e4;display:flex;
   min-height:100vh;align-items:center;justify-content:center;margin:0}}
 .card{{background:#292524;padding:2rem;border-radius:16px;width:min(90vw,460px);
   box-shadow:0 10px 40px #0008}}
 h1{{font-size:1.2rem;margin:0 0 1rem}}
 #drop{{border:2px dashed #78716c;border-radius:12px;padding:2.5rem 1rem;text-align:center;
   cursor:pointer;transition:.15s}}
 #drop.hot{{border-color:#fb923c;background:#3f3f46}}
 input{{display:none}}
 .out{{margin-top:1rem;word-break:break-all;font-size:.85rem}}
 a{{color:#fb923c}} button{{margin-top:.5rem;background:#fb923c;border:0;color:#1c1917;
   padding:.5rem 1rem;border-radius:8px;cursor:pointer;font-weight:600}}
 img{{max-width:100%;border-radius:8px;margin-top:1rem}}
</style></head><body><div class=card>
<h1>🍳 ClaudioKitchen — Datei hochladen</h1>
<div id=drop>Datei hierher ziehen oder klicken<br><small>(Bild, PDF, Audio, … — dann URL in den Claude-Chat kopieren)</small></div>
<input id=f type=file>
<div class=out id=out></div></div>
<script>
 const tok={token};
 const drop=document.getElementById('drop'),inp=document.getElementById('f'),out=document.getElementById('out');
 drop.onclick=()=>inp.click();
 ;['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{{ev.preventDefault();drop.classList.add('hot')}}));
 ;['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{{ev.preventDefault();drop.classList.remove('hot')}}));
 drop.addEventListener('drop',ev=>up(ev.dataTransfer.files[0]));
 inp.onchange=()=>up(inp.files[0]);
 function esc(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}
 async function up(file){{
   if(!file)return; out.textContent='Lade hoch…';
   const r=await fetch('/upload?token='+encodeURIComponent(tok)+'&name='+encodeURIComponent(file.name),
     {{method:'PUT',body:file}});
   if(!r.ok){{out.textContent='Fehler: '+r.status;return}}
   const j=await r.json(); const u=esc(j.url);
   let html='<b>URL (in den Chat kopieren):</b><br><a href="'+u+'" target=_blank>'+u+'</a>'+
     '<br><button id=cp>Kopieren</button>';
   if((file.type||'').startsWith('image/')) html+='<br><img src="'+u+'">';
   out.innerHTML=html;
   document.getElementById('cp').onclick=()=>navigator.clipboard.writeText(j.url);
 }}
</script></body></html>"""


@mcp.custom_route("/upload", methods=["GET", "PUT", "POST"])
async def upload_route(request: Request):
    q = request.query_params
    has_token = secrets.compare_digest(q.get("token", ""), FILES_TOKEN)

    if request.method == "GET":
        if not has_token:
            return HTMLResponse("Forbidden: append ?token=&lt;FILES_TOKEN&gt; to the URL.",
                                status_code=403)
        return HTMLResponse(_UPLOAD_PAGE.format(token=json.dumps(FILES_TOKEN)))

    # PUT/POST: authorize via static token OR a one-time upload ticket
    if not (has_token or _consume_ticket(q.get("ticket", ""))):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > UPLOAD_MAX_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    raw = await request.body()
    if not raw:
        return JSONResponse({"error": "empty body"}, status_code=400)
    if len(raw) > UPLOAD_MAX_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    name = q.get("name", "")
    ext = _sniff_ext(raw, name=name, content_type=request.headers.get("content-type", ""))
    return JSONResponse({"url": _save_file(raw, ext)})


# ----------------- Discovery -----------------
@mcp.tool
async def list_models(output_modality: str = "image") -> dict:
    """List OpenRouter models by output modality: text, image, audio, embeddings.
    For video models use list_video_models instead."""
    await _check_user()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{OR_BASE}/models", headers=OR_HEADERS,
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
        r = await c.get(f"{OR_BASE}/videos/models", headers=OR_HEADERS)
        r.raise_for_status()
        return r.json()


# ----------------- Uploads -----------------
@mcp.tool
async def create_upload_url(expires_in: int = 900, max_uses: int = 5) -> dict:
    """Mint a short-lived upload URL so you can send ANY file's RAW BYTES directly to
    this server instead of base64-encoding it into a tool call. Best for uploading a
    local file from your sandbox (image, PDF, audio, video, doc, ...).

    Returns `upload_url`. Upload with an HTTP PUT of the raw bytes, e.g.:
        curl -T /path/to/file.pdf "<upload_url>&name=file.pdf"
    Add `&name=<filename>` to preserve the extension. The PUT returns JSON like
    {"url": "https://.../files/<id>.pdf?token=..."} - pass that `url` to the user or to
    edit_image / describe_image / transcribe_audio / generate_video(image_url=...)."""
    await _check_user()
    expires_in = max(60, min(int(expires_in), 24 * 3600))  # 1 min .. 24 h
    max_uses = max(1, min(int(max_uses), 100))
    ticket = secrets.token_urlsafe(24)
    _upload_tickets[ticket] = {"exp": time.time() + expires_in, "uses": max_uses}
    return {
        "upload_url": f"{BASE_URL}/upload?ticket={ticket}",
        "method": "PUT",
        "expires_in": expires_in,
        "max_uses": max_uses,
        "curl_example": f'curl -T file.pdf "{BASE_URL}/upload?ticket={ticket}&name=file.pdf"',
        "next": "PUT the raw file bytes (add &name=<filename>); use the returned `url`.",
    }


@mcp.tool
async def upload_file(data: str, filename: str | None = None,
                      content_type: str | None = None) -> dict:
    """Store ANY file on this server (image, PDF, audio, video, text, doc, ...) and
    return a token-protected download URL usable by the other tools and shareable back
    to the user. `data` may be raw base64, a full `data:<mime>;base64,...` URL, or an
    http(s) URL to re-host. Pass `filename` (e.g. 'report.pdf') to preserve the right
    extension; otherwise the type is sniffed from the bytes / `content_type`. base64 is
    decoded tolerantly (whitespace and missing padding are handled).

    Note: pass the file BYTES as base64, not a local sandbox path (e.g. /mnt/...) -
    that path doesn't exist on this server. For a large local file, prefer
    create_upload_url and PUT the raw bytes. To upload a file pasted into the chat via
    a browser, open `<BASE_URL>/upload?token=<FILES_TOKEN>`."""
    await _check_user()
    ctype = content_type or ""
    if data.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=120) as c:
            raw, fetched_ctype = await _safe_fetch(c, data, max_bytes=UPLOAD_MAX_BYTES)
        ctype = ctype or fetched_ctype
    else:
        if data.startswith("data:") and ";" in data.split(",", 1)[0]:
            ctype = ctype or data[5:].split(";", 1)[0]  # mime from the data: URL
        try:
            raw = _decode_b64(data)
        except Exception as e:
            raise ValueError(
                "data must be base64 file bytes or an http(s)/data URL - not a local "
                f"file path. ({e})"
            )
    ext = _sniff_ext(raw, name=filename or "", content_type=ctype)
    return {"url": _save_file(raw, ext)}


# ----------------- Image -----------------
@mcp.tool
async def generate_image(prompt: str,
                         model: str = DEFAULT_IMAGE_MODEL,
                         text_and_image: bool = True,
                         aspect_ratio: str | None = None):
    """Generate an image from a text prompt. Call this whenever the user asks you to
    create / draw / make / generate an image - you CAN produce images here, so do it
    instead of declining. `model` accepts ANY OpenRouter image
    model id (e.g. 'google/gemini-2.5-flash-image', 'black-forest-labs/flux-1.1-pro',
    'openai/gpt-image-1', ...) - discover them with list_models(output_modality='image').
    Set text_and_image=False for image-only models (e.g. Flux). Optional aspect_ratio
    like '16:9'. Returns the image inline plus a download URL."""
    await _check_user()
    await _check_budget()
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"] if text_and_image else ["image"],
        "usage": USAGE,
    }
    if aspect_ratio:
        body["image_config"] = {"aspect_ratio": aspect_ratio}

    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"{OR_BASE}/chat/completions", headers=OR_HEADERS, json=body)
        r.raise_for_status()
        j = r.json()
        msg = j["choices"][0]["message"]
    await _record_cost("generate_image", model, j.get("usage"))
    return _images_from_message(msg) + [_cost_note(j.get("usage"))]


@mcp.tool
async def edit_image(prompt: str,
                     image_urls: list[str],
                     model: str = DEFAULT_IMAGE_MODEL):
    """Edit or combine existing images with a text instruction (inpainting, style
    transfer, object removal, merging multiple images, etc). Provide 1-4 image URLs
    (e.g. download URLs from a previous generate_image call). `model` accepts any
    OpenRouter image-capable model id (default supports image editing)."""
    await _check_user()
    await _check_budget()
    content: list = [{"type": "text", "text": prompt}]
    async with httpx.AsyncClient(timeout=300) as c:
        for url in image_urls[:4]:
            raw, ctype = await _safe_fetch(c, url)
            mime = ctype.split(";")[0] or "image/png"
            b64 = base64.b64encode(raw).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}})

        r = await c.post(f"{OR_BASE}/chat/completions", headers=OR_HEADERS, json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"],
            "usage": USAGE,
        })
        r.raise_for_status()
        j = r.json()
        msg = j["choices"][0]["message"]
    await _record_cost("edit_image", model, j.get("usage"))
    return _images_from_message(msg) + [_cost_note(j.get("usage"))]


@mcp.tool
async def describe_image(image_url: str, question: str = "Describe this image in detail.",
                         model: str = "google/gemini-2.5-flash") -> dict:
    """Analyze an image with a vision model: describe it, answer questions about it,
    extract text (OCR), etc. Returns the answer plus the request cost."""
    await _check_user()
    await _check_budget()
    async with httpx.AsyncClient(timeout=300) as c:
        raw, ctype = await _safe_fetch(c, image_url)
        mime = ctype.split(";")[0] or "image/png"
        b64 = base64.b64encode(raw).decode()
        r = await c.post(f"{OR_BASE}/chat/completions", headers=OR_HEADERS, json={
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
            "usage": USAGE,
        })
        r.raise_for_status()
        j = r.json()
    await _record_cost("describe_image", model, j.get("usage"))
    return {"answer": j["choices"][0]["message"]["content"],
            "cost_usd": _cost(j.get("usage"))}


# ----------------- Video (async job API) -----------------
TERMINAL_VIDEO_STATES = {"completed", "failed", "cancelled", "expired"}


async def _finalize_video(data: dict, c: httpx.AsyncClient, save_to_server: bool) -> dict:
    """Download completed videos onto this server and attach cost."""
    urls = data.get("unsigned_urls") or []
    if save_to_server and data.get("status") == "completed" and urls:
        saved = []
        for u in urls:
            # OpenRouter's content URLs require the API key; external/signed URLs don't
            hdrs = OR_HEADERS if u.startswith(OR_BASE) else None
            v = await c.get(u, headers=hdrs)
            v.raise_for_status()
            saved.append(_save_file(v.content, "mp4"))
        data["server_urls"] = saved
    cost = _cost(data.get("usage"))
    if cost is not None:
        data["cost_usd"] = cost
    return data


@mcp.tool
async def generate_video(prompt: str,
                         model: str,
                         duration: int | None = None,
                         aspect_ratio: str | None = None,
                         resolution: str | None = None,
                         generate_audio: bool | None = None,
                         image_url: str | None = None,
                         wait: bool = False,
                         wait_timeout: int = 600,
                         ctx: Context | None = None) -> dict:
    """Generate a video (costs credits!). Pick a model via list_video_models. Optional
    image_url (a stable, directly downloadable URL) makes it image-to-video (first frame).

    Two modes:
      - wait=False (default): submits and returns immediately with a job id. Poll it
        yourself with check_video.
      - wait=True: polls internally until the video is finished (or wait_timeout
        seconds), reporting progress, then returns the completed result with the
        downloaded video URL. Use for short clips; long jobs may exceed client timeouts
        (then it returns the job id so you can continue with check_video)."""
    await _check_user()
    await _check_budget()
    body: dict = {"model": model, "prompt": prompt}
    if duration: body["duration"] = duration
    if aspect_ratio: body["aspect_ratio"] = aspect_ratio
    if resolution: body["resolution"] = resolution
    if generate_audio is not None: body["generate_audio"] = generate_audio
    if image_url:
        body["frame_images"] = [{
            "type": "image_url",
            "image_url": {"url": image_url},
            "frame_type": "first_frame",
        }]

    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"{OR_BASE}/videos", headers=OR_HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        job_id = data.get("id")
        if not wait or not job_id:
            return data

        waited, interval = 0, 8
        while data.get("status") not in TERMINAL_VIDEO_STATES and waited < wait_timeout:
            if ctx:
                await ctx.report_progress(progress=waited, total=wait_timeout)
                await ctx.info(f"video {job_id}: {data.get('status')} ({waited}s)")
            await asyncio.sleep(interval)
            waited += interval
            r = await c.get(f"{OR_BASE}/videos/{job_id}", headers=OR_HEADERS)
            r.raise_for_status()
            data = r.json()

        if data.get("status") not in TERMINAL_VIDEO_STATES:
            data["note"] = (f"Still '{data.get('status')}' after {wait_timeout}s. "
                            f"Keep polling with check_video(job_id='{job_id}').")
            return data
        final = await _finalize_video(data, c, save_to_server=True)
        if final.get("status") == "completed":
            await _record_cost("generate_video", model, final.get("usage"),
                               dedupe_key=f"video:{job_id}")
        return final


@mcp.tool
async def check_video(job_id: str, save_to_server: bool = True) -> dict:
    """Poll a video generation job. When completed, optionally downloads the video to
    this server and returns a permanent download URL plus the request cost."""
    await _check_user()
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.get(f"{OR_BASE}/videos/{job_id}", headers=OR_HEADERS)
        r.raise_for_status()
        data = await _finalize_video(r.json(), c, save_to_server)
    if data.get("status") == "completed":
        await _record_cost("generate_video", data.get("model"), data.get("usage"),
                           dedupe_key=f"video:{job_id}")
    return data


# ----------------- Audio -----------------
@mcp.tool
async def text_to_speech(text: str, model: str = DEFAULT_TTS_MODEL,
                         voice: str = "alloy") -> dict:
    """Generate speech audio from text. OpenRouter streams audio as 16-bit PCM, which
    is wrapped into a WAV file here. `voice` e.g. alloy, echo, fable, onyx, nova, shimmer.
    Returns the download URL for the .wav file plus the request cost."""
    await _check_user()
    await _check_budget()
    body = {
        "model": model,
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": "pcm16"},
        "stream": True,
        "usage": USAGE,
        "messages": [{"role": "user",
                      "content": f"Read the following text aloud verbatim, "
                                 f"with no extra words:\n\n{text}"}],
    }
    pcm = bytearray()
    usage = None
    async with httpx.AsyncClient(timeout=300) as c:
        async with c.stream("POST", f"{OR_BASE}/chat/completions",
                            headers=OR_HEADERS, json=body) as r:
            if r.status_code != 200:
                raise RuntimeError(f"TTS failed ({r.status_code}): {await r.aread()}")
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if ev.get("usage"):
                    usage = ev["usage"]
                try:
                    au = ev["choices"][0]["delta"].get("audio")
                except (KeyError, IndexError):
                    continue
                if isinstance(au, dict) and au.get("data"):
                    pcm.extend(base64.b64decode(au["data"]))
    if not pcm:
        raise RuntimeError("TTS returned no audio.")
    await _record_cost("text_to_speech", model, usage)
    return {"audio_url": _save_file(_pcm16_to_wav(bytes(pcm)), "wav"),
            "cost_usd": _cost(usage)}


@mcp.tool
async def transcribe_audio(audio_url: str, model: str = DEFAULT_STT_MODEL,
                           prompt: str = "Transcribe this audio verbatim.") -> dict:
    """Transcribe (or answer questions about) speech from an audio file URL. Uses an
    audio-capable chat model. `model` accepts any OpenRouter model with audio input.
    Returns the transcript plus the request cost."""
    await _check_user()
    await _check_budget()
    async with httpx.AsyncClient(timeout=300) as c:
        content, ctype = await _safe_fetch(c, audio_url)
        ctype = ctype.lower()
        # Derive the extension from the path only (ignore any dotted query string).
        last = audio_url.split("?", 1)[0].rsplit("/", 1)[-1]
        ext = last.rsplit(".", 1)[-1].lower() if "." in last else ""
        fmt = ("mp3" if "mp3" in ctype or "mpeg" in ctype or ext == "mp3"
               else "wav" if "wav" in ctype or ext == "wav"
               else ext or "mp3")
        b64 = base64.b64encode(content).decode()
        r = await c.post(f"{OR_BASE}/chat/completions", headers=OR_HEADERS, json={
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
            ]}],
            "usage": USAGE,
        })
        r.raise_for_status()
        j = r.json()
    await _record_cost("transcribe_audio", model, j.get("usage"))
    return {"text": j["choices"][0]["message"]["content"],
            "cost_usd": _cost(j.get("usage"))}


# ----------------- Embeddings & Rerank -----------------
@mcp.tool
async def create_embeddings(texts: list[str],
                            model: str = "openai/text-embedding-3-small") -> dict:
    """Create embedding vectors for a list of texts. Includes the request cost."""
    await _check_user()
    await _check_budget()
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{OR_BASE}/embeddings", headers=OR_HEADERS,
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
        r = await c.post(f"{OR_BASE}/rerank", headers=OR_HEADERS,
                         json={"model": model, "query": query,
                               "documents": documents, "top_n": top_n})
        r.raise_for_status()
        data = r.json()
    await _record_cost("rerank", model, data.get("usage"))
    data["cost_usd"] = _cost(data.get("usage"))
    return data


# ----------------- File management & usage -----------------
def _resolve_generated(name_or_url: str) -> Path:
    """Map a bare filename or a /files/<id> download URL to a path INSIDE FILES_DIR.

    Rejects anything with path separators so a caller can't escape the directory.
    """
    name = name_or_url.strip()
    if "/files/" in name:
        name = name.split("/files/", 1)[1]
    name = name.split("?", 1)[0].strip().strip("/")
    if not name or "/" in name or "\\" in name:
        raise ValueError(f"Invalid file name: {name_or_url!r}")
    path = (FILES_DIR / name).resolve()
    if FILES_DIR not in path.parents:
        raise ValueError("Resolved path is outside the files directory.")
    return path


@mcp.tool
async def list_files(limit: int = 100, sort: str = "newest") -> dict:
    """List files this server has generated/stored (served under /files). Shows each
    file's name, size, modified time and download URL, plus the total count and size.
    `sort` is 'newest' (default) or 'oldest'. Use delete_file / cleanup_files to prune."""
    await _check_user()
    entries = []
    for p in FILES_DIR.iterdir():
        if not p.is_file() or p.name == ".gitkeep":
            continue
        st = p.stat()
        entries.append({
            "name": p.name,
            "size_bytes": st.st_size,
            "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(),
            "url": f"{BASE_URL}/files/{p.name}?token={FILES_TOKEN}",
            "_mtime": st.st_mtime,
        })
    entries.sort(key=lambda e: e["_mtime"], reverse=(sort != "oldest"))
    total = sum(e["size_bytes"] for e in entries)
    for e in entries:
        e.pop("_mtime", None)
    return {
        "count": len(entries),
        "total_bytes": total,
        "total_mb": round(total / 1_048_576, 1),
        "files": entries[: max(0, limit)],
    }


@mcp.tool
async def delete_file(name_or_url: str) -> dict:
    """Delete one generated file. Accepts either the bare filename (e.g. 'ab12.png')
    or a full /files download URL. Returns whether it was deleted."""
    await _check_user()
    path = _resolve_generated(name_or_url)
    if not path.is_file():
        return {"deleted": False, "name": path.name, "error": "not found"}
    freed = path.stat().st_size
    path.unlink()
    return {"deleted": True, "name": path.name, "freed_bytes": freed}


@mcp.tool
async def cleanup_files(older_than_days: float | None = None,
                        keep_newest: int | None = None,
                        dry_run: bool = True) -> dict:
    """Bulk-prune generated files to reclaim disk. Provide `older_than_days` (delete
    files older than N days), `keep_newest` (delete all but the N most recent), or
    both (union). dry_run=True (default) only previews what would be deleted - call
    again with dry_run=False to actually delete."""
    await _check_user()
    if older_than_days is None and keep_newest is None:
        raise ValueError("Specify older_than_days and/or keep_newest.")
    files = [p for p in FILES_DIR.iterdir() if p.is_file() and p.name != ".gitkeep"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # newest first

    victims: set[Path] = set()
    if older_than_days is not None:
        cutoff = time.time() - older_than_days * 86400
        victims.update(p for p in files if p.stat().st_mtime < cutoff)
    if keep_newest is not None:
        victims.update(files[max(0, keep_newest):])

    freed = sum(p.stat().st_size for p in victims)
    names = sorted(p.name for p in victims)
    if not dry_run:
        for p in victims:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    return {
        "dry_run": dry_run,
        "matched": len(names),
        "freed_bytes": freed,
        "freed_mb": round(freed / 1_048_576, 1),
        "files": names[:100],
    }


@mcp.tool
async def usage_summary() -> dict:
    """Report recorded OpenRouter spend (USD): today, this calendar month, all-time,
    a per-tool breakdown, and remaining monthly budget if MONTHLY_BUDGET_USD is set.
    Costs are logged automatically by every generating tool."""
    await _check_user()
    recs = _read_usage()
    today = _spend_since(_day_start_ts(), recs)
    month = _spend_since(_month_start_ts(), recs)
    all_time = sum((r.get("cost_usd") or 0.0) for r in recs)
    by_tool: dict[str, dict] = {}
    for r in recs:
        t = r.get("tool") or "unknown"
        agg = by_tool.setdefault(t, {"requests": 0, "cost_usd": 0.0})
        agg["requests"] += 1
        agg["cost_usd"] = round(agg["cost_usd"] + (r.get("cost_usd") or 0.0), 6)
    out = {
        "requests": len(recs),
        "today_usd": round(today, 6),
        "this_month_usd": round(month, 6),
        "all_time_usd": round(all_time, 6),
        "by_tool": by_tool,
    }
    if MONTHLY_BUDGET_USD is not None:
        out["monthly_budget_usd"] = MONTHLY_BUDGET_USD
        out["budget_remaining_usd"] = round(max(0.0, MONTHLY_BUDGET_USD - month), 6)
    return out


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
