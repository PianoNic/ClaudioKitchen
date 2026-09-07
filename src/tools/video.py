# SPDX-License-Identifier: Apache-2.0
"""Video generation tools (OpenRouter's async job API)."""

import asyncio

import httpx
from fastmcp import Context

from src import config
from src.app import mcp
from src.auth import _check_user
from src.files import _save_file
from src.usage import _check_budget, _cost, _record_cost

TERMINAL_VIDEO_STATES = {"completed", "failed", "cancelled", "expired"}


async def _finalize_video(data: dict, c: httpx.AsyncClient, save_to_server: bool) -> dict:
    """Download completed videos onto this server and attach cost."""
    urls = data.get("unsigned_urls") or []
    if save_to_server and data.get("status") == "completed" and urls:
        saved = []
        for u in urls:
            # OpenRouter's content URLs require the API key; external/signed URLs don't
            hdrs = config.OR_HEADERS if u.startswith(config.OR_BASE) else None
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
        r = await c.post(f"{config.OR_BASE}/videos", headers=config.OR_HEADERS, json=body)
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
            r = await c.get(f"{config.OR_BASE}/videos/{job_id}", headers=config.OR_HEADERS)
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
        r = await c.get(f"{config.OR_BASE}/videos/{job_id}", headers=config.OR_HEADERS)
        r.raise_for_status()
        data = await _finalize_video(r.json(), c, save_to_server)
    if data.get("status") == "completed":
        await _record_cost("generate_video", data.get("model"), data.get("usage"),
                           dedupe_key=f"video:{job_id}")
    return data
