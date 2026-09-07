# SPDX-License-Identifier: Apache-2.0
"""File storage helpers: saving generated files, sniffing extensions, tolerant
base64 decoding, SSRF-guarded outbound fetches, path resolution, and one-time
upload tickets."""

import base64
import ipaddress
import mimetypes
import socket
import time
import uuid
from pathlib import Path

import httpx

from src import config

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


def _save_file(data: bytes, ext: str) -> str:
    name = f"{uuid.uuid4().hex}.{ext}"
    (config.FILES_DIR / name).write_bytes(data)
    return f"{config.BASE_URL}/files/{name}?token={config.FILES_TOKEN}"


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
                      max_bytes: int = config.FETCH_MAX_BYTES) -> tuple[bytes, str]:
    """Fetch a caller/model-supplied URL defensively: http(s) only, public hosts
    only (SSRF guard), no redirects, and a hard size cap. Returns (bytes, content_type)."""
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs are allowed (got {parsed.scheme!r}).")
    # Files hosted on this server's own /files store are trusted (skip the SSRF host
    # check so uploaded files work even when BASE_URL is a private/tunnel host).
    if not url.startswith(config.BASE_URL.rstrip("/") + "/"):
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
    path = (config.FILES_DIR / name).resolve()
    if config.FILES_DIR not in path.parents:
        raise ValueError("Resolved path is outside the files directory.")
    return path
