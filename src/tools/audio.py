# SPDX-License-Identifier: Apache-2.0
"""Text-to-speech and transcription tools."""

import base64
import io
import json
import wave

import httpx

from src import config
from src.app import mcp
from src.auth import _check_user
from src.files import _safe_fetch, _save_file
from src.usage import _check_budget, _cost, _record_cost


def _pcm16_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap raw 16-bit mono PCM (what OpenRouter streams for TTS) in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


@mcp.tool
async def text_to_speech(text: str, model: str = config.DEFAULT_TTS_MODEL,
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
        "usage": config.USAGE,
        "messages": [{"role": "user",
                      "content": f"Read the following text aloud verbatim, "
                                 f"with no extra words:\n\n{text}"}],
    }
    pcm = bytearray()
    usage = None
    async with httpx.AsyncClient(timeout=300) as c:
        async with c.stream("POST", f"{config.OR_BASE}/chat/completions",
                            headers=config.OR_HEADERS, json=body) as r:
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
async def transcribe_audio(audio_url: str, model: str = config.DEFAULT_STT_MODEL,
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
        r = await c.post(f"{config.OR_BASE}/chat/completions", headers=config.OR_HEADERS, json={
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
            ]}],
            "usage": config.USAGE,
        })
        r.raise_for_status()
        j = r.json()
    await _record_cost("transcribe_audio", model, j.get("usage"))
    return {"text": j["choices"][0]["message"]["content"],
            "cost_usd": _cost(j.get("usage"))}
