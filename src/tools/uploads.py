# SPDX-License-Identifier: Apache-2.0
"""Upload tools: short-lived PUT tickets and direct base64/URL uploads."""

import secrets
import time

import httpx

from src import config
from src.app import mcp
from src.auth import _check_user
from src.files import _decode_b64, _safe_fetch, _save_file, _sniff_ext, _upload_tickets


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
        "upload_url": f"{config.BASE_URL}/upload?ticket={ticket}",
        "method": "PUT",
        "expires_in": expires_in,
        "max_uses": max_uses,
        "curl_example": f'curl -T file.pdf "{config.BASE_URL}/upload?ticket={ticket}&name=file.pdf"',
        "next": "PUT the raw file bytes (add &name=<filename>); use the returned `url`.",
    }


@mcp.tool
async def upload_file(data: str, filename: str | None = None,
                      content_type: str | None = None) -> dict:
    """Store ANY file on this server (image, PDF, audio, video, text, doc, and more)
    and return a token-protected download URL, usable by the other tools and shareable
    back to the user.

    `data` accepts, in order of preference:
      1. an http(s) URL to re-host (cheap: the server downloads it for you).
      2. a `data:<mime>;base64,...` URL.
      3. raw base64 of the file bytes.
    Forms 2 and 3 mean emitting the whole file as text, so they only make sense for
    small or self-generated content (you cannot base64 a file you merely 'see'). For a
    local file you already have, prefer create_upload_url and PUT the raw bytes (no
    base64, no size blowup). For a file the user has on their machine, send them to the
    browser uploader at `<BASE_URL>/upload?token=<FILES_TOKEN>`. A local sandbox path
    like /mnt/... is NOT valid here; it does not exist on this server.

    Pass `filename` (e.g. 'report.pdf') to preserve the extension; otherwise the type
    is sniffed from the bytes / `content_type`. base64 is decoded tolerantly."""
    await _check_user()
    ctype = content_type or ""
    if data.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=120) as c:
            raw, fetched_ctype = await _safe_fetch(c, data, max_bytes=config.UPLOAD_MAX_BYTES)
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
