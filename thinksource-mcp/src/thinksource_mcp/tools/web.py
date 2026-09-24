"""Web + PDF tools: web_search, web_fetch, pdf_extract.

Runs on the host (not the sandbox) so it has real internet regardless of any
Daytona egress restrictions. All tools return structured dicts and never raise
— errors surface as `{"error": ...}` so the calling model can react.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from thinksource_mcp.tools.documents import get_downloads_dir

_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB safety cap on PDF downloads


def _safe_download_path(candidate: str) -> Path | None:
    """Resolve a user-supplied path safely to somewhere inside downloads dir."""
    dl = get_downloads_dir().resolve()
    p = Path(candidate).expanduser().resolve()
    try:
        p.relative_to(dl)
        return p
    except ValueError:
        return None


def register_web_tools(mcp: FastMCP) -> None:
    """Register web_search / web_fetch / pdf_extract onto FastMCP."""

    @mcp.tool()
    def web_search(query: str, limit: int = 10) -> dict[str, Any]:
        """Search the public web via DuckDuckGo (no API key needed).

        PREFER THIS OVER running curl/wget/requests in the sandbox — the sandbox
        typically has restricted outbound network and will fail. This tool runs
        on the host and always has real internet.

        Args:
            query: Free-form search query.
            limit: Max results (1-25).
        """
        limit = max(1, min(int(limit), 25))
        try:
            from ddgs import DDGS
        except ImportError:
            return {"error": "ddgs not installed."}

        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=limit))
        except Exception as e:
            return {"error": f"web_search failed: {e!s}"}

        results = [
            {
                "title": r.get("title"),
                "url": r.get("href") or r.get("url"),
                "snippet": r.get("body") or r.get("snippet"),
            }
            for r in raw
        ]
        return {"query": query, "count": len(results), "results": results}

    @mcp.tool()
    def web_fetch(
        url: str,
        extract_readable: bool = True,
        max_chars: int | None = 200_000,
        include_html: bool = False,
    ) -> dict[str, Any]:
        """Fetch a URL and return clean, readable text.

        PREFER THIS OVER running curl in the sandbox — sandbox network is often
        blocked. This tool runs on the host, follows redirects, and by default
        uses trafilatura to strip nav/ads/boilerplate so the model only sees
        real article content.

        Args:
            url: The URL to fetch (http/https).
            extract_readable: If True (default), run trafilatura to extract main
                content. If False, return raw text/html.
            max_chars: Truncate output to this many chars (None = no cap).
            include_html: If True, include raw HTML alongside extracted text.
        """
        import httpx

        try:
            with httpx.Client(
                headers={"User-Agent": _DEFAULT_UA},
                follow_redirects=True,
                timeout=30.0,
            ) as client:
                resp = client.get(url)
        except Exception as e:
            return {"error": f"HTTP fetch failed: {e!s}"}

        content_type = resp.headers.get("content-type", "")
        raw = resp.text if resp.encoding else resp.content.decode(errors="replace")
        title: str | None = None
        text = raw

        if extract_readable and "html" in content_type.lower():
            try:
                import trafilatura

                extracted = trafilatura.extract(
                    raw,
                    include_comments=False,
                    include_tables=True,
                    with_metadata=True,
                    output_format="markdown",
                )
                if extracted:
                    text = extracted
                metadata = trafilatura.extract_metadata(raw)
                if metadata:
                    title = metadata.title
            except Exception as e:
                text = raw
                title = f"(extraction failed: {e!s})"

        if max_chars is not None and len(text) > max_chars:
            truncated = True
            text = text[:max_chars]
        else:
            truncated = False

        result: dict[str, Any] = {
            "url": url,
            "final_url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": content_type,
            "title": title,
            "text": text,
            "char_count": len(text),
            "truncated": truncated,
            "extracted": extract_readable and "html" in content_type.lower(),
        }
        if include_html:
            result["html"] = raw[: max_chars or len(raw)]
        return result

    @mcp.tool()
    def pdf_extract(
        source: str,
        pages: str | None = None,
        include_page_texts: bool = False,
    ) -> dict[str, Any]:
        """Extract text from a PDF, given a URL or a local path.

        For URLs: downloads over HTTPS with a 50 MB safety cap.
        For local paths: must be inside the ThinkSource downloads directory
        (~/thinksource-downloads/) to prevent arbitrary filesystem reads.

        Args:
            source: Either an http(s) URL or an absolute path inside the
                downloads directory.
            pages: Optional page range like "1-5" or "3,7,9". Default: all.
            include_page_texts: If True, include per-page text array.
        """
        from pypdf import PdfReader

        tmp_path: Path | None = None
        try:
            if source.startswith(("http://", "https://")):
                import httpx

                try:
                    with httpx.Client(
                        headers={"User-Agent": _DEFAULT_UA},
                        follow_redirects=True,
                        timeout=60.0,
                    ) as client:
                        with client.stream("GET", source) as resp:
                            resp.raise_for_status()
                            tmp = tempfile.NamedTemporaryFile(
                                suffix=".pdf", delete=False
                            )
                            tmp_path = Path(tmp.name)
                            written = 0
                            for chunk in resp.iter_bytes():
                                written += len(chunk)
                                if written > _MAX_PDF_BYTES:
                                    tmp.close()
                                    return {
                                        "error": f"PDF too large (>{_MAX_PDF_BYTES} bytes)."
                                    }
                                tmp.write(chunk)
                            tmp.close()
                            file_path = tmp_path
                except Exception as e:
                    return {"error": f"PDF download failed: {e!s}"}
            else:
                candidate = _safe_download_path(source)
                if candidate is None:
                    return {
                        "error": "Local paths must be inside the downloads directory "
                        f"({get_downloads_dir()})."
                    }
                if not candidate.is_file():
                    return {"error": f"File not found: {candidate}"}
                file_path = candidate

            try:
                reader = PdfReader(str(file_path))
            except Exception as e:
                return {"error": f"PDF parse failed: {e!s}"}

            page_count = len(reader.pages)
            page_indices = _parse_page_range(pages, page_count)

            per_page: list[dict[str, Any]] = []
            texts: list[str] = []
            for idx in page_indices:
                try:
                    t = reader.pages[idx].extract_text() or ""
                except Exception as e:
                    t = f"(page {idx + 1} extract failed: {e!s})"
                per_page.append({"page": idx + 1, "text": t})
                texts.append(t)

            full_text = "\n\n".join(texts)
            result: dict[str, Any] = {
                "source": source,
                "page_count": page_count,
                "pages_extracted": len(page_indices),
                "char_count": len(full_text),
                "text": full_text,
                "metadata": {
                    "title": (reader.metadata or {}).get("/Title"),
                    "author": (reader.metadata or {}).get("/Author"),
                    "subject": (reader.metadata or {}).get("/Subject"),
                    "producer": (reader.metadata or {}).get("/Producer"),
                }
                if reader.metadata
                else None,
            }
            if include_page_texts:
                result["pages"] = per_page
            return result
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass


def _parse_page_range(spec: str | None, page_count: int) -> list[int]:
    """Parse a "1-5,7,9-11"-style spec into a list of 0-indexed page numbers."""
    if not spec:
        return list(range(page_count))
    indices: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                continue
            for p in range(max(1, lo), min(page_count, hi) + 1):
                indices.append(p - 1)
        else:
            try:
                p = int(part)
                if 1 <= p <= page_count:
                    indices.append(p - 1)
            except ValueError:
                continue
    seen: set[int] = set()
    return [i for i in indices if not (i in seen or seen.add(i))]
