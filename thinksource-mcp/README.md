# thinksource-mcp

MCP server exposing tools for ThinkSource / TrueForge agents. First release: **YouTube**.

## Tools shipped

- `youtube_metadata(url)` — title, channel, duration, views, description, tags, chapters
- `youtube_transcript(url, languages=["en"], include_timestamps=True)` — captions with per-segment timestamps
- `youtube_search(query, limit=10)` — top N video hits for a query
- `youtube_channel_videos(channel_url, limit=20)` — recent uploads from a channel
- `youtube_download_audio(url, output_dir="./downloads")` — save mp3 for Whisper transcription
- `youtube_comments(url, limit=100)` — top comments on a video

## Run

```powershell
cd d:/agentic_proj/ThinkSource/thinksource-mcp
uv sync
uv run thinksource-mcp
```

Listens at `http://0.0.0.0:9000/mcp`. Configure via env:

- `THINKSOURCE_MCP_PORT` (default `9000`)
- `THINKSOURCE_MCP_HOST` (default `0.0.0.0`)

## Register in TrueForge

TrueForge runs in Docker; the MCP server runs on the host. Use Docker Desktop's host DNS:

- **URL from container:** `http://host.docker.internal:9000/mcp`
- Add via UI: Settings → Connectors → Add MCP Server
- Or via API:
  ```bash
  curl -X POST http://127.0.0.1:8791/api/v1/settings/mcp-servers \
    -H 'content-type: application/json' \
    -d '{"manifest":{"name":"thinksource","url":"http://host.docker.internal:9000/mcp","transport":"streamable-http"}}'
  ```

## Roadmap

- v0.2: `web_fetch` + `pdf_ingest` for source-based research
- v0.3: `create_docx` / `create_pdf` / `create_pptx` / `create_xlsx` (replaces Anthropic doc skills with permissive license)
- v0.4: `sources` — RAG store + `search_sources`, `add_source`, `list_notebooks`
