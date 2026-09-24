"""YouTube tools exposed via MCP.

Six tools: metadata, transcript, search, channel_videos, download_audio, comments.
All tools return structured dicts and never raise — errors surface as `{"error": ...}`
so the calling model can react.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

_VIDEO_ID_PATTERNS = [
    re.compile(r"(?:v=|youtu\.be/|embed/|shorts/|/v/)([a-zA-Z0-9_-]{11})"),
    re.compile(r"^([a-zA-Z0-9_-]{11})$"),
]


def _extract_video_id(url: str) -> str | None:
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def register_youtube_tools(mcp: FastMCP) -> None:
    """Register every YouTube tool onto the given FastMCP instance."""

    @mcp.tool()
    def youtube_metadata(url: str) -> dict[str, Any]:
        """Fetch video metadata: title, channel, duration, views, upload date, description, tags, chapters, thumbnail.

        Args:
            url: YouTube video URL (any form) or 11-character video ID.
        """
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            return {"error": f"yt-dlp failed: {e!s}"}

        return {
            "id": info.get("id"),
            "title": info.get("title"),
            "channel": info.get("uploader") or info.get("channel"),
            "channel_id": info.get("channel_id"),
            "channel_url": info.get("channel_url"),
            "duration_seconds": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "upload_date": info.get("upload_date"),
            "description": info.get("description"),
            "tags": info.get("tags") or [],
            "categories": info.get("categories") or [],
            "chapters": info.get("chapters") or [],
            "thumbnail": info.get("thumbnail"),
            "webpage_url": info.get("webpage_url"),
            "language": info.get("language"),
        }

    @mcp.tool()
    def youtube_transcript(
        url: str,
        languages: list[str] | None = None,
        include_timestamps: bool = True,
    ) -> dict[str, Any]:
        """Fetch captions/transcript from a YouTube video.

        Fast path via youtube-transcript-api. Returns full text plus per-segment
        start/duration when `include_timestamps` is True. If no captions exist,
        returns an error suggesting the audio-download + Whisper fallback.

        Args:
            url: YouTube video URL or 11-char video ID.
            languages: Preferred caption languages by priority, e.g. ["en", "en-US", "hi"]. Default ["en"].
            include_timestamps: If True, include start/duration per segment.
        """
        video_id = _extract_video_id(url)
        if not video_id:
            return {"error": "Could not extract YouTube video ID from URL."}

        languages = languages or ["en"]

        try:
            from youtube_transcript_api import (
                NoTranscriptFound,
                TranscriptsDisabled,
                VideoUnavailable,
                YouTubeTranscriptApi,
            )
        except ImportError:
            return {"error": "youtube-transcript-api not installed."}

        try:
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
        except (TranscriptsDisabled, NoTranscriptFound):
            return {
                "error": "No captions available in the requested language. "
                "Fallback: call youtube_download_audio then transcribe the mp3 in a sandbox using openai-whisper."
            }
        except VideoUnavailable:
            return {"error": "Video unavailable (private, deleted, or region-locked)."}
        except Exception as e:
            return {"error": f"Transcript fetch failed: {e!s}"}

        snippets = fetched.snippets
        text = " ".join(s.text for s in snippets)
        result: dict[str, Any] = {
            "video_id": video_id,
            "language": fetched.language_code,
            "generated": fetched.is_generated,
            "text": text,
            "segment_count": len(snippets),
        }
        if include_timestamps:
            result["segments"] = [
                {"text": s.text, "start": s.start, "duration": s.duration}
                for s in snippets
            ]
        return result

    @mcp.tool()
    def youtube_search(query: str, limit: int = 10) -> dict[str, Any]:
        """Search YouTube and return top N video hits with basic metadata.

        Args:
            query: Free-form search query.
            limit: Max results (1-50).
        """
        import yt_dlp

        limit = max(1, min(int(limit), 50))
        try:
            with yt_dlp.YoutubeDL(
                {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
            ) as ydl:
                results = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        except Exception as e:
            return {"error": f"Search failed: {e!s}"}

        videos = []
        for entry in results.get("entries") or []:
            vid = entry.get("id")
            videos.append(
                {
                    "id": vid,
                    "title": entry.get("title"),
                    "url": entry.get("url") or (f"https://youtube.com/watch?v={vid}" if vid else None),
                    "channel": entry.get("channel") or entry.get("uploader"),
                    "duration_seconds": entry.get("duration"),
                    "view_count": entry.get("view_count"),
                }
            )
        return {"query": query, "count": len(videos), "videos": videos}

    @mcp.tool()
    def youtube_channel_videos(channel_url: str, limit: int = 20) -> dict[str, Any]:
        """List recent uploads from a YouTube channel.

        Args:
            channel_url: Channel URL in any form (/@handle, /c/name, /channel/UC…).
            limit: Max videos to return (1-200).
        """
        import yt_dlp

        limit = max(1, min(int(limit), 200))
        try:
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": True,
                    "playlistend": limit,
                    "skip_download": True,
                }
            ) as ydl:
                info = ydl.extract_info(channel_url, download=False)
        except Exception as e:
            return {"error": f"yt-dlp failed: {e!s}"}

        entries = info.get("entries") or []
        if entries and entries[0].get("_type") == "playlist":
            entries = entries[0].get("entries") or []

        videos = []
        for entry in entries[:limit]:
            vid = entry.get("id")
            videos.append(
                {
                    "id": vid,
                    "title": entry.get("title"),
                    "url": entry.get("url") or (f"https://youtube.com/watch?v={vid}" if vid else None),
                    "duration_seconds": entry.get("duration"),
                    "view_count": entry.get("view_count"),
                    "upload_date": entry.get("upload_date"),
                }
            )
        return {
            "channel": info.get("uploader") or info.get("channel") or info.get("title"),
            "channel_id": info.get("channel_id"),
            "count": len(videos),
            "videos": videos,
        }

    @mcp.tool()
    def youtube_download_audio(url: str, output_dir: str = "./downloads") -> dict[str, Any]:
        """Download the audio track of a YouTube video as an mp3.

        Returns the absolute path so the calling agent can Whisper-transcribe it
        in a sandbox when captions are unavailable.

        Args:
            url: YouTube video URL or ID.
            output_dir: Directory to save the mp3 (created if missing). Defaults to ./downloads.
        """
        import yt_dlp

        outdir = Path(output_dir).expanduser().resolve()
        outdir.mkdir(parents=True, exist_ok=True)
        outtpl = str(outdir / "%(id)s.%(ext)s")

        try:
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "format": "bestaudio/best",
                    "outtmpl": outtpl,
                    "postprocessors": [
                        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
                    ],
                }
            ) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            return {"error": f"Download failed: {e!s}"}

        video_id = info.get("id") or _extract_video_id(url) or "unknown"
        final = outdir / f"{video_id}.mp3"
        return {
            "video_id": info.get("id"),
            "title": info.get("title"),
            "duration_seconds": info.get("duration"),
            "file_path": str(final),
            "file_exists": final.exists(),
            "file_size_bytes": final.stat().st_size if final.exists() else 0,
        }

    @mcp.tool()
    def youtube_comments(url: str, limit: int = 100) -> dict[str, Any]:
        """Fetch top comments on a YouTube video. Slower than metadata (10-30s typical).

        Args:
            url: YouTube video URL or ID.
            limit: Max top-level comments (1-500).
        """
        import yt_dlp

        limit = max(1, min(int(limit), 500))
        try:
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "getcomments": True,
                    "extractor_args": {"youtube": {"max_comments": [str(limit), "0", "0", "0"]}},
                }
            ) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            return {"error": f"yt-dlp failed: {e!s}"}

        comments = info.get("comments") or []
        return {
            "video_id": info.get("id"),
            "title": info.get("title"),
            "total_comments": info.get("comment_count"),
            "count_returned": len(comments[:limit]),
            "comments": [
                {
                    "author": c.get("author"),
                    "text": c.get("text"),
                    "likes": c.get("like_count"),
                    "timestamp": c.get("timestamp"),
                }
                for c in comments[:limit]
            ],
        }
