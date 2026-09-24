# ThinkSource MCP server image.
#
# Serves 15 tools (YouTube, docs, web/pdf, preview) over Streamable HTTP at
# /mcp. Meant to run alongside the ThinkSource (TrueForge) server via
# docker-compose — reachable inside the compose network as
# http://thinksource-mcp:9000/mcp.

FROM python:3.13-slim AS base

# ffmpeg is required by yt-dlp for `youtube_download_audio` post-processing.
# ripgrep is included for tools that may shell out to search text.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      ripgrep \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv — the same package manager we use in host dev.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Non-root runtime user.
RUN useradd --uid 10001 --user-group --create-home thinksource
WORKDIR /app

# ---- dependency install (cached layer) ----
COPY pyproject.toml uv.lock* README.md ./
RUN uv sync --frozen --no-install-project 2>/dev/null || uv sync --no-install-project

# ---- source ----
COPY src ./src
RUN uv sync --frozen 2>/dev/null || uv sync

# Downloads land here inside the container. Mount a host volume to persist.
ENV THINKSOURCE_DOWNLOADS_DIR=/data/downloads \
    THINKSOURCE_MCP_HOST=0.0.0.0 \
    THINKSOURCE_MCP_PORT=9000 \
    THINKSOURCE_PUBLIC_HOST=localhost
RUN mkdir -p /data/downloads && chown -R thinksource:thinksource /data /app

USER thinksource
EXPOSE 9000

# Streamable-HTTP MCP transport; health probed with a real initialize POST.
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=20s \
  CMD curl -sf -X POST http://127.0.0.1:9000/mcp \
      -H "content-type: application/json" \
      -H "accept: application/json, text/event-stream" \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"health","version":"0"}}}' \
      -o /dev/null || exit 1

CMD ["uv", "run", "thinksource-mcp"]
