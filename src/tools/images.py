# SPDX-License-Identifier: Apache-2.0
"""Image generation, editing, and vision (describe/OCR) tools."""

import base64

import httpx
from fastmcp.apps import AppConfig
from mcp.types import ImageContent

from src import config
from src.app import mcp
from src.auth import _check_user
from src.files import _decode_b64, _safe_fetch, _save_file, _sniff_image_ext
from src.image_view import IMAGE_VIEW_URI
from src.usage import _check_budget, _cost, _cost_note, _record_cost


def _images_from_message(msg: dict) -> list:
    """Convert chat-completions image outputs to MCP image blocks + download URLs.

    Handles both raster output (PNG/JPG/... from Gemini, Flux, ...) and vector output
    (image/svg+xml from Recraft's vector models), saving each with the right extension
    and the correct MIME so it renders inline.
    """
    images = msg.get("images") or []
    if not images:
        return [f"No image returned. Model said: {msg.get('content')}"]
    out = []
    for img in images:
        url = ((img or {}).get("image_url") or {}).get("url", "")
        if url.startswith("data:") and "," in url:
            header, _ = url.split(",", 1)
            raw = _decode_b64(url)
            mime = header[5:].split(";")[0].strip().lower()  # data:<mime>;base64
            if mime == "image/svg+xml" or raw.lstrip()[:4] == b"<svg" or raw[:5] == b"<?xml":
                # claude.ai rejects SVG inside an image content block, so emit only the
                # download link; the MCP Apps view loads the SVG from that URL.
                out.append(f"Download: {_save_file(raw, 'svg')}")
            else:
                ext = _sniff_image_ext(raw, fallback=(mime.split("/")[-1] if mime else "png"))
                mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
                out.append(ImageContent(type="image",
                                        data=base64.b64encode(raw).decode(),
                                        mimeType=mime))
                out.append(f"Download: {_save_file(raw, ext)}")
        elif url.startswith(("http://", "https://")):
            # Some providers return a hosted URL rather than inline bytes.
            out.append(f"Image URL: {url}")
        elif url:
            out.append(f"Unrecognized image payload: {url[:80]}")
    if msg.get("content"):
        out.append(msg["content"])
    return out


@mcp.tool(app=AppConfig(resource_uri=IMAGE_VIEW_URI))
async def generate_image(prompt: str,
                         model: str = config.DEFAULT_IMAGE_MODEL,
                         text_and_image: bool = True,
                         aspect_ratio: str | None = None):
    """Generate an image from a text prompt. Call this whenever the user asks you to
    create / draw / make / generate an image - you CAN produce images here, so do it
    instead of declining. `model` accepts ANY OpenRouter image
    model id (e.g. 'google/gemini-2.5-flash-image', 'black-forest-labs/flux-1.1-pro',
    'openai/gpt-image-1', ...) - discover them with list_models(output_modality='image').
    Image-only models (Flux, and Recraft's vector models like
    'recraft/recraft-v4.1-pro-vector', which return a real SVG) are handled
    automatically, so text_and_image can be left at its default. Optional aspect_ratio
    like '16:9'. Returns the image inline plus a download URL."""
    await _check_user()
    await _check_budget()
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"] if text_and_image else ["image"],
        "usage": config.USAGE,
    }
    if aspect_ratio:
        body["image_config"] = {"aspect_ratio": aspect_ratio}

    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"{config.OR_BASE}/chat/completions", headers=config.OR_HEADERS,
                         json=body)
        # Image-only models (Recraft vector, Flux, ...) reject modalities ["image","text"]
        # with a 404 ("No endpoints found that support the requested output modalities").
        # Retry image-only so the caller doesn't have to know to set text_and_image=False.
        if (r.status_code == 404 and body.get("modalities") == ["image", "text"]
                and "output modalities" in r.text.lower()):
            body["modalities"] = ["image"]
            r = await c.post(f"{config.OR_BASE}/chat/completions", headers=config.OR_HEADERS,
                             json=body)
        r.raise_for_status()
        j = r.json()
        msg = j["choices"][0]["message"]
    await _record_cost("generate_image", model, j.get("usage"))
    return _images_from_message(msg) + [_cost_note(j.get("usage"))]


@mcp.tool(app=AppConfig(resource_uri=IMAGE_VIEW_URI))
async def edit_image(prompt: str,
                     image_urls: list[str],
                     model: str = config.DEFAULT_IMAGE_MODEL):
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

        ebody = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"],
            "usage": config.USAGE,
        }
        r = await c.post(f"{config.OR_BASE}/chat/completions", headers=config.OR_HEADERS,
                         json=ebody)
        if r.status_code == 404 and "output modalities" in r.text.lower():
            ebody["modalities"] = ["image"]
            r = await c.post(f"{config.OR_BASE}/chat/completions", headers=config.OR_HEADERS,
                             json=ebody)
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
        r = await c.post(f"{config.OR_BASE}/chat/completions", headers=config.OR_HEADERS, json={
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
            "usage": config.USAGE,
        })
        r.raise_for_status()
        j = r.json()
    await _record_cost("describe_image", model, j.get("usage"))
    return {"answer": j["choices"][0]["message"]["content"],
            "cost_usd": _cost(j.get("usage"))}
