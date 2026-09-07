# SPDX-License-Identifier: Apache-2.0
"""Custom Starlette routes: health check, file downloads, and the upload page/API."""

import json
import secrets

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from src import config
from src.app import mcp
from src.files import _consume_ticket, _save_file, _sniff_ext


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    """Unauthenticated liveness probe for container/reverse-proxy health checks."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/files/{name}", methods=["GET"])
async def serve_file(request: Request):
    token = request.query_params.get("token", "")
    if not secrets.compare_digest(token, config.FILES_TOKEN):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    name = request.path_params["name"]
    path = (config.FILES_DIR / name).resolve()
    if not path.is_file() or config.FILES_DIR not in path.parents:
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
<h1>🍳 ClaudioKitchen · Datei hochladen</h1>
<div id=drop>Datei hierher ziehen oder klicken<br><small>(Bild, PDF, Audio; dann URL in den Claude-Chat kopieren)</small></div>
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
    has_token = secrets.compare_digest(q.get("token", ""), config.FILES_TOKEN)

    if request.method == "GET":
        if not has_token:
            return HTMLResponse("Forbidden: append ?token=&lt;FILES_TOKEN&gt; to the URL.",
                                status_code=403)
        return HTMLResponse(_UPLOAD_PAGE.format(token=json.dumps(config.FILES_TOKEN)))

    # PUT/POST: authorize via static token OR a one-time upload ticket
    if not (has_token or _consume_ticket(q.get("ticket", ""))):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > config.UPLOAD_MAX_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    raw = await request.body()
    if not raw:
        return JSONResponse({"error": "empty body"}, status_code=400)
    if len(raw) > config.UPLOAD_MAX_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    name = q.get("name", "")
    ext = _sniff_ext(raw, name=name, content_type=request.headers.get("content-type", ""))
    return JSONResponse({"url": _save_file(raw, ext)})
