"""ThinkSource MCP server entry point. Streamable HTTP transport at /mcp.

Also serves produced files at /files/<name> so agents can hand back a URL.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from thinksource_mcp.tools.documents import get_downloads_dir, register_document_tools
from thinksource_mcp.tools.web import register_web_tools
from thinksource_mcp.tools.youtube import register_youtube_tools

_PREVIEW_CSS = """
:root { color-scheme: light dark; }
body {
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  max-width: 820px; margin: 40px auto; padding: 0 24px;
  color: #1f2328; background: #ffffff;
}
@media (prefers-color-scheme: dark) {
  body { color: #e6edf3; background: #0d1117; }
  a { color: #58a6ff; }
  code, pre { background: #161b22; }
  blockquote { border-left-color: #30363d; color: #8b949e; }
  hr { background: #30363d; }
  table th, table td { border-color: #30363d; }
}
h1, h2, h3 { line-height: 1.25; margin-top: 1.8em; margin-bottom: 0.5em; }
h1 { border-bottom: 1px solid #d1d9e0; padding-bottom: 0.3em; font-size: 2em; }
h2 { border-bottom: 1px solid #d1d9e0; padding-bottom: 0.3em; font-size: 1.5em; }
h3 { font-size: 1.25em; }
p { margin: 0.75em 0; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
code { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 88%; background: rgba(175,184,193,0.2); padding: 0.2em 0.4em; border-radius: 6px; }
pre { background: rgba(175,184,193,0.2); padding: 16px; border-radius: 8px; overflow: auto; }
pre code { background: transparent; padding: 0; }
blockquote { margin: 1em 0; padding: 0 1em; color: #59636e; border-left: 4px solid #d1d9e0; }
hr { border: 0; height: 1px; background: #d1d9e0; margin: 2em 0; }
table { border-collapse: collapse; margin: 1em 0; }
table th, table td { border: 1px solid #d1d9e0; padding: 6px 13px; }
table th { background: rgba(175,184,193,0.2); }
img { max-width: 100%; }
"""


def _render_markdown(text: str, title: str) -> str:
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})
    body = md.render(text)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head>\n'
        '<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
        f"<style>{_PREVIEW_CSS}</style>\n"
        "</head><body>\n"
        f"{body}\n"
        "</body></html>\n"
    )

mcp = FastMCP("thinksource")
register_youtube_tools(mcp)
register_document_tools(mcp)
register_web_tools(mcp)


@mcp.custom_route("/files/{filename:path}", methods=["GET"])
async def serve_download(request: Request):
    """Serve any file in the downloads directory. Refuses path traversal."""
    filename = request.path_params["filename"]
    downloads_dir = get_downloads_dir()
    candidate = (downloads_dir / filename).resolve()
    if downloads_dir.resolve() not in candidate.parents and candidate != downloads_dir.resolve():
        return JSONResponse({"error": "outside downloads dir"}, status_code=400)
    if not candidate.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(candidate, filename=Path(filename).name)


@mcp.custom_route("/preview/{filename:path}", methods=["GET"])
async def preview_file(request: Request):
    """Render a downloadable file inline in the browser.

    - .md / .markdown  -> render to HTML with a GitHub-ish stylesheet
    - .html / .htm     -> serve as HTML inline
    - .pdf             -> serve inline so browser's PDF viewer opens it
    - everything else  -> redirect to /files/<name> (download)
    """
    filename = request.path_params["filename"]
    downloads_dir = get_downloads_dir()
    candidate = (downloads_dir / filename).resolve()
    if downloads_dir.resolve() not in candidate.parents and candidate != downloads_dir.resolve():
        return JSONResponse({"error": "outside downloads dir"}, status_code=400)
    if not candidate.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    ext = candidate.suffix.lower()
    if ext in {".md", ".markdown"}:
        text = candidate.read_text(encoding="utf-8", errors="replace")
        return HTMLResponse(_render_markdown(text, candidate.stem))
    if ext in {".html", ".htm"}:
        return HTMLResponse(candidate.read_text(encoding="utf-8", errors="replace"))
    if ext == ".pdf":
        return FileResponse(
            candidate,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{candidate.name}"'},
        )
    return RedirectResponse(url=f"/files/{filename}")


def main() -> None:
    port = int(os.getenv("THINKSOURCE_MCP_PORT", "9000"))
    host = os.getenv("THINKSOURCE_MCP_HOST", "0.0.0.0")
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = "/mcp"

    # Baseline: local host + Docker Desktop host DNS + the compose service name
    # so a containerized TrueForge can reach us via
    # http://thinksource-mcp:9000/mcp on the compose network.
    default_hosts = [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "host.docker.internal:*",
        "thinksource-mcp:*",
    ]
    default_origins = [f"http://{h}" for h in default_hosts]

    # Extra hosts (e.g. custom compose service name, deployment DNS) can be
    # added via env — comma-separated `name:port` or `name:*`.
    extra = [h.strip() for h in os.getenv("THINKSOURCE_ALLOWED_HOSTS", "").split(",") if h.strip()]
    allowed_hosts = default_hosts + extra
    allowed_origins = default_origins + [f"http://{h}" for h in extra]

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
