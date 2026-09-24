"""Document generation tools: create_docx, create_pdf, create_xlsx, create_pptx.

All tools:
- accept markdown / structured input
- save under the shared downloads directory
- return a dict with `file_path` (absolute) and `download_url` (served by this MCP)
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def _openui_markdown_snippet(content: str, variant: str = "card") -> str:
    """Build a ready-to-paste OpenUI snippet that renders `content` as a
    MarkDownRenderer card inline in the TrueForge chat.

    Uses a dynamically-sized backtick fence so nested code blocks in the
    markdown content don't prematurely close the outer openui fence.
    Escapes the markdown as a JSON string (matches OpenUI's double-quoted
    string rules).
    """
    quoted = json.dumps(content)
    longest_backticks = 0
    for match in re.finditer(r"`+", content):
        longest_backticks = max(longest_backticks, len(match.group()))
    fence = "`" * max(3, longest_backticks + 1)
    return f'{fence}openui\nroot = MarkDownRenderer({quoted}, "{variant}")\n{fence}'


DEFAULT_DOWNLOADS_DIR = Path.home() / "thinksource-downloads"


def get_downloads_dir() -> Path:
    """Resolve the downloads directory (env override, auto-create)."""
    override = os.getenv("THINKSOURCE_DOWNLOADS_DIR")
    d = Path(override).expanduser().resolve() if override else DEFAULT_DOWNLOADS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download_url(filename: str) -> str:
    """Build the URL that a browser (running on the same host) can hit."""
    host = os.getenv("THINKSOURCE_PUBLIC_HOST", "localhost")
    port = int(os.getenv("THINKSOURCE_MCP_PORT", "9000"))
    return f"http://{host}:{port}/files/{filename}"


_PREVIEWABLE_EXTS = {".md", ".markdown", ".html", ".htm", ".pdf"}


def _preview_url(filename: str) -> str | None:
    """Return a browser-renderable preview URL, or None if not previewable."""
    ext = Path(filename).suffix.lower()
    if ext not in _PREVIEWABLE_EXTS:
        return None
    host = os.getenv("THINKSOURCE_PUBLIC_HOST", "localhost")
    port = int(os.getenv("THINKSOURCE_MCP_PORT", "9000"))
    return f"http://{host}:{port}/preview/{filename}"


def _ensure_ext(filename: str, ext: str) -> str:
    """Add extension if missing. `ext` includes the leading dot."""
    name = Path(filename).name
    if not name.lower().endswith(ext.lower()):
        name = f"{name}{ext}"
    return name


def _unique_path(filename: str) -> Path:
    """Return a downloads-dir path; if the name collides, prefix with a short uid."""
    dl = get_downloads_dir()
    target = dl / filename
    if target.exists():
        target = dl / f"{uuid.uuid4().hex[:6]}-{filename}"
    return target


# ---------------------------------------------------------------------------
# Minimal markdown parser for docx (headings, bullets, ordered lists, bold, italic)
# ---------------------------------------------------------------------------

_INLINE_PATTERN = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)")


def _add_inline_runs(paragraph, text: str) -> None:
    """Handle **bold**, *italic*, and `code` inline markers."""
    for tok in _INLINE_PATTERN.split(text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            run = paragraph.add_run(tok[2:-2])
            run.bold = True
        elif tok.startswith("*") and tok.endswith("*"):
            run = paragraph.add_run(tok[1:-1])
            run.italic = True
        elif tok.startswith("`") and tok.endswith("`"):
            run = paragraph.add_run(tok[1:-1])
            run.font.name = "Consolas"
        else:
            paragraph.add_run(tok)


def _markdown_to_docx(doc, markdown_text: str) -> None:
    """Very small markdown-to-docx converter. Covers 90% of everyday docs.

    Supports: h1-h6, paragraphs, `-`/`*` bullets, `1.` ordered lists, blank
    lines as breaks, and inline **bold** / *italic* / `code`.
    """
    for raw_line in markdown_text.split("\n"):
        line = raw_line.rstrip()
        if not line:
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            doc.add_heading(heading_match.group(2), level=level)
            continue

        if re.match(r"^[-*]\s+", line):
            para = doc.add_paragraph(style="List Bullet")
            _add_inline_runs(para, re.sub(r"^[-*]\s+", "", line))
            continue

        if re.match(r"^\d+\.\s+", line):
            para = doc.add_paragraph(style="List Number")
            _add_inline_runs(para, re.sub(r"^\d+\.\s+", "", line))
            continue

        para = doc.add_paragraph()
        _add_inline_runs(para, line)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def register_document_tools(mcp: FastMCP) -> None:
    """Register create_docx / create_pdf / create_xlsx / create_pptx onto FastMCP."""

    @mcp.tool()
    def create_docx(
        content: str,
        filename: str,
        title: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        """Create a Microsoft Word (.docx) file from markdown or plain text.

        PREFER THIS OVER writing python-docx code in a sandbox / exec tool: this
        runs in-process (no container cold-start), always returns a stable
        `download_url` the user can click, and stores the file at a known path.
        Only fall back to sandbox+python-docx if you need a feature this tool
        does not support (tables, images, page numbers, custom styles).

        Supports h1-h6, paragraphs, bullet + numbered lists, and inline
        **bold** / *italic* / `code`. Tables and images are not yet supported.

        Args:
            content: Body of the document as markdown or plain text.
            filename: Output filename (with or without .docx extension).
            title: Optional document title (also becomes an h1 at the top if content doesn't start with one).
            author: Optional author metadata.
        """
        from docx import Document

        name = _ensure_ext(filename, ".docx")
        path = _unique_path(name)

        doc = Document()
        if author:
            doc.core_properties.author = author
        if title:
            doc.core_properties.title = title
            if not content.lstrip().startswith("#"):
                doc.add_heading(title, level=0)
        _markdown_to_docx(doc, content)

        try:
            doc.save(path)
        except Exception as e:
            return {"error": f"docx save failed: {e!s}"}

        return {
            "file_path": str(path),
            "file_name": path.name,
            "download_url": _download_url(path.name),
            "preview_url": _preview_url(path.name),
            "size_bytes": path.stat().st_size,
        }

    @mcp.tool()
    def create_pdf(
        content: str,
        filename: str,
        title: str | None = None,
        orientation: str = "portrait",
    ) -> dict[str, Any]:
        """Create a PDF from markdown or plain text.

        PREFER THIS OVER writing reportlab / fpdf / weasyprint code in a
        sandbox: in-process (no cold-start), returns a stable `download_url`,
        saves to a known path. Only fall back to sandbox if you need features
        this tool does not support (fine-grained layout, embedded fonts, forms).

        Markdown is converted to HTML then rendered with fpdf2. Supports
        headings, paragraphs, lists, bold/italic, and simple tables.

        Args:
            content: Body of the document (markdown or plain text).
            filename: Output filename (with or without .pdf extension).
            title: Optional document title (shown at the top).
            orientation: "portrait" (default) or "landscape".
        """
        from fpdf import FPDF
        from markdown_it import MarkdownIt

        name = _ensure_ext(filename, ".pdf")
        path = _unique_path(name)

        orient = "L" if orientation.lower().startswith("l") else "P"
        pdf = FPDF(orientation=orient, unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        if title:
            pdf.set_font("Helvetica", "B", 18)
            pdf.cell(0, 10, title, ln=True)
            pdf.ln(2)
        pdf.set_font("Helvetica", size=11)

        html = MarkdownIt("commonmark", {"html": False}).render(content)
        try:
            pdf.write_html(html)
            pdf.output(str(path))
        except Exception as e:
            return {"error": f"pdf write failed: {e!s}"}

        return {
            "file_path": str(path),
            "file_name": path.name,
            "download_url": _download_url(path.name),
            "preview_url": _preview_url(path.name),
            "size_bytes": path.stat().st_size,
        }

    @mcp.tool()
    def create_xlsx(
        sheets: dict[str, list[list[Any]]] | list[list[Any]],
        filename: str,
    ) -> dict[str, Any]:
        """Create an Excel (.xlsx) spreadsheet with real Excel formulas.

        PREFER THIS OVER writing openpyxl code in a sandbox / exec tool for
        every xlsx task — including financial models, tables with formulas,
        pivot-style summaries. In-process (no container cold-start), stable
        `download_url`, saves to a known path. Only fall back to sandbox if
        you need charts, conditional formatting, macros, or images.

        Cells whose string value starts with `=` are treated as formulas
        (e.g. `"=B2*C2"`, `"=SUM(D2:D13)"`) — write them like you would in
        Excel.

        Args:
            sheets: Either a single 2D list (one sheet named "Sheet1")
                    or a dict {sheet_name: 2D_list} for multiple sheets.
                    Each inner list is one row; cells can be numbers,
                    strings, bools, or formula strings starting with "=".
            filename: Output filename (with or without .xlsx extension).
        """
        from openpyxl import Workbook

        name = _ensure_ext(filename, ".xlsx")
        path = _unique_path(name)

        if isinstance(sheets, list):
            sheets = {"Sheet1": sheets}
        if not isinstance(sheets, dict) or not sheets:
            return {"error": "sheets must be a non-empty dict or list."}

        wb = Workbook()
        wb.remove(wb.active)
        for sheet_name, rows in sheets.items():
            ws = wb.create_sheet(title=str(sheet_name)[:31] or "Sheet1")
            for row_idx, row in enumerate(rows, start=1):
                for col_idx, value in enumerate(row, start=1):
                    ws.cell(row=row_idx, column=col_idx, value=value)

        try:
            wb.save(path)
        except Exception as e:
            return {"error": f"xlsx save failed: {e!s}"}

        return {
            "file_path": str(path),
            "file_name": path.name,
            "download_url": _download_url(path.name),
            "preview_url": _preview_url(path.name),
            "size_bytes": path.stat().st_size,
            "sheet_count": len(sheets),
        }

    @mcp.tool()
    def create_pptx(
        slides: list[dict[str, Any]],
        filename: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create a PowerPoint (.pptx) deck.

        PREFER THIS OVER writing python-pptx code in a sandbox: in-process
        (no cold-start), stable `download_url`, saves to a known path. Only
        fall back to sandbox if you need custom layouts, embedded charts,
        or heavy image manipulation.

        Each slide dict may have: `title` (str), `subtitle` (str, first slide),
        `bullets` (list[str]), `content` (str, single paragraph).

        Args:
            slides: List of slide specs. Example:
                    [{"title": "Intro", "subtitle": "Overview"},
                     {"title": "Findings", "bullets": ["A", "B", "C"]},
                     {"title": "Next", "content": "Discussion..."}]
            filename: Output filename (with or without .pptx extension).
            title: Optional deck title (also sets file metadata).
        """
        from pptx import Presentation
        from pptx.util import Inches, Pt

        name = _ensure_ext(filename, ".pptx")
        path = _unique_path(name)

        prs = Presentation()
        if title:
            prs.core_properties.title = title

        if not slides:
            return {"error": "slides list is empty."}

        for i, spec in enumerate(slides):
            slide_title = str(spec.get("title") or "").strip()
            bullets = spec.get("bullets") or []
            content = spec.get("content")
            subtitle = spec.get("subtitle")

            if i == 0 and (subtitle or (not bullets and not content)):
                layout = prs.slide_layouts[0]  # title slide
                slide = prs.slides.add_slide(layout)
                slide.shapes.title.text = slide_title
                if len(slide.placeholders) > 1 and subtitle:
                    slide.placeholders[1].text = str(subtitle)
                continue

            layout = prs.slide_layouts[1]  # title + content
            slide = prs.slides.add_slide(layout)
            if slide_title:
                slide.shapes.title.text = slide_title

            body_placeholder = None
            for shape in slide.placeholders:
                if shape.placeholder_format.idx == 1:
                    body_placeholder = shape
                    break
            if body_placeholder is None:
                continue

            tf = body_placeholder.text_frame
            tf.clear()
            if bullets:
                for idx, bullet in enumerate(bullets):
                    p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
                    p.text = str(bullet)
                    p.level = 0
                    for run in p.runs:
                        run.font.size = Pt(18)
            elif content:
                tf.text = str(content)
                for run in tf.paragraphs[0].runs:
                    run.font.size = Pt(18)

        try:
            prs.save(path)
        except Exception as e:
            return {"error": f"pptx save failed: {e!s}"}

        return {
            "file_path": str(path),
            "file_name": path.name,
            "download_url": _download_url(path.name),
            "preview_url": _preview_url(path.name),
            "size_bytes": path.stat().st_size,
            "slide_count": len(slides),
        }

    @mcp.tool()
    def create_markdown(
        content: str,
        filename: str,
        title: str | None = None,
        inline_variant: str = "card",
    ) -> dict[str, Any]:
        """Create a Markdown (.md) file AND get a ready-to-paste OpenUI snippet
        that renders it INLINE in the TrueForge chat (like Claude Artifacts /
        ChatGPT Canvas).

        HOW TO SHOW THE USER THE RENDERED MARKDOWN:
        - The response contains an `openui_snippet` field. Include it verbatim
          in your reply to the user. TrueForge's frontend will render the
          markdown as a card inline in the chat — no new tab, no download prompt.
        - Also mention `download_url` and `preview_url` as separate links so the
          user can save the file or open the full-page preview.

        PREFER THIS OVER writing markdown files in the sandbox via `echo` /
        `cat << EOF`: this runs in-process, saves to a known path, generates
        the inline-preview snippet automatically, and always returns clean URLs.

        Args:
            content: Body of the document as markdown.
            filename: Output filename (with or without .md extension).
            title: Optional H1 title prepended if content doesn't start with '# '.
            inline_variant: OpenUI MarkDownRenderer variant: "card" (default,
                elevated), "sunk" (recessed), or "clear" (transparent).
        """
        name = _ensure_ext(filename, ".md")
        path = _unique_path(name)

        body = content
        if title and not content.lstrip().startswith("#"):
            body = f"# {title}\n\n{content}"

        try:
            path.write_text(body, encoding="utf-8")
        except Exception as e:
            return {"error": f"markdown write failed: {e!s}"}

        variant = inline_variant if inline_variant in {"card", "sunk", "clear"} else "card"

        return {
            "file_path": str(path),
            "file_name": path.name,
            "download_url": _download_url(path.name),
            "preview_url": _preview_url(path.name),
            "size_bytes": path.stat().st_size,
            "openui_snippet": _openui_markdown_snippet(body, variant),
            "usage_hint": (
                "Paste `openui_snippet` verbatim into your reply for inline "
                "rendering, then add `download_url` and `preview_url` as links."
            ),
        }

    @mcp.tool()
    def list_downloads(limit: int = 50) -> dict[str, Any]:
        """List files in the ThinkSource downloads directory (newest first).

        Args:
            limit: Max files to return (1-500).
        """
        limit = max(1, min(int(limit), 500))
        d = get_downloads_dir()
        files = sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        return {
            "downloads_dir": str(d),
            "count": len(files),
            "files": [
                {
                    "name": f.name,
                    "size_bytes": f.stat().st_size,
                    "modified_at": f.stat().st_mtime,
                    "download_url": _download_url(f.name),
                }
                for f in files
                if f.is_file()
            ],
        }
