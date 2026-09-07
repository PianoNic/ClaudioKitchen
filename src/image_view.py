# SPDX-License-Identifier: Apache-2.0
"""MCP Apps view that renders generate_image / edit_image results inline.

claude.ai does not render an MCP tool's image content inline in the reply (it only
shows in the collapsed tool call). MCP Apps fixes this: a tool can point at a ui://
resource that the host renders in a sandboxed iframe, and the tool's image content is
delivered to it via postMessage. This is additive - clients without MCP Apps support
just ignore the `app` metadata and still get the image block + download link.
"""

from fastmcp.apps import AppConfig, ResourceCSP

from src import config
from src.app import mcp

IMAGE_VIEW_URI = "ui://claudiokitchen/image.html"

_IMAGE_VIEW_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<style>
  :root { color-scheme: light dark; }
  html, body { margin: 0; background: transparent; }
  #wrap { display: flex; flex-wrap: wrap; gap: 12px; justify-content: flex-start;
          align-items: flex-start; padding: 8px; box-sizing: border-box; }
  /* Absolute caps, not % / vh: the iframe starts tiny, so relative units collapse
     the image. autoResize then grows the frame to fit this size. */
  img { display: block; width: auto; height: auto; max-width: 480px; max-height: 480px;
        border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.25); }
  #msg { font: 14px system-ui, sans-serif; opacity: .6; padding: 24px; text-align: center; }
</style>
</head>
<body>
  <div id="wrap"></div>
  <div id="msg">Rendering image…</div>
  <script type="module">
    import { App } from "https://unpkg.com/@modelcontextprotocol/ext-apps@0.4.0/app-with-deps";
    const wrap = document.getElementById("wrap");
    const msg = document.getElementById("msg");
    const show = (nodes) => { msg.style.display = "none"; wrap.replaceChildren(...nodes); };
    const imgEl = (src, alt) => {
      const el = document.createElement("img"); el.src = src; el.alt = alt || "image";
      el.onerror = () => { msg.style.display = ""; msg.textContent = "Preview unavailable. Use the download link."; };
      return el;
    };
    const render = (content) => {
      const blocks = content || [];
      // Raster images arrive as image content blocks (base64 data URI).
      const imgs = blocks.filter(c => c && c.type === "image" && c.data);
      if (imgs.length) {
        show(imgs.map(b => imgEl(`data:${b.mimeType || "image/png"};base64,${b.data}`, "Generated image")));
        return;
      }
      // Vector output (SVG): claude.ai rejects SVG image blocks, so the server sends only
      // a download link. Load the SVG from that URL (CSP allows the files origin).
      const text = blocks.filter(c => c && c.type === "text").map(c => c.text || "").join("\n");
      const m = text.match(/https?:\/\/[^\s"']+\.svg[^\s"']*/i);
      if (m) { show([imgEl(m[0], "Generated vector image")]); return; }
      msg.textContent = "No image in this result.";
    };
    // autoResize (default on) makes the app report its size so the host grows the
    // iframe to fit the image instead of leaving it at the tiny default frame.
    const app = new App({ name: "ClaudioKitchen Image", version: "1.0.0" }, {}, { autoResize: true });
    app.ontoolresult = ({ content }) => render(content);
    await app.connect();
  </script>
</body>
</html>"""


@mcp.resource(IMAGE_VIEW_URI,
              app=AppConfig(csp=ResourceCSP(
                  resource_domains=["https://unpkg.com", config.BASE_ORIGIN])))
def image_view() -> str:
    """MCP Apps view: renders image content from generate_image / edit_image inline."""
    return _IMAGE_VIEW_HTML
