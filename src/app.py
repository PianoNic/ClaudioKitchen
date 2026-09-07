# SPDX-License-Identifier: Apache-2.0
"""The FastMCP server instance.

Created here, separately from main.py, so the route/resource/tool modules can
`from src.app import mcp` and decorate it without circular imports.
"""

from fastmcp import FastMCP

from src.auth import auth

# Sent to the client on initialize (MCP `instructions`). Clients like Claude may fold
# this into the system prompt, so it's where we tell Claude it CAN generate media here.
SERVER_INSTRUCTIONS = """\
ClaudioKitchen gives you real multimodal generation through OpenRouter. You are NOT
limited to text on this connection: when the user asks you to create / generate / draw
/ make an image, edit or combine images, describe or OCR an image, generate a video,
speak text aloud (text-to-speech), transcribe audio, or embed / rerank text, actually
DO IT by calling the matching tool below. Never reply that you "can't generate images,
video, or audio". Through these tools, you can.

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
