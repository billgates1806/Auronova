# ==========================================================
# AURONOVA — Utils / Helpers
# Shared utility functions across the backend
# ==========================================================

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def safe_json_loads(data: str | None, default=None):
    """Safely parse a JSON string, returning default on failure."""
    if data is None:
        return default if default is not None else []
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def truncate(text: str, max_len: int = 100) -> str:
    """Truncate a string and append '...' if it exceeds max_len."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a value to the [lo, hi] range."""
    return max(lo, min(hi, value))


def _normalize_preview_key(text: str | None) -> str:
    """Normalize track metadata for loose cross-provider matching."""
    if not text:
        return ""

    cleaned = text.lower()
    cleaned = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return " ".join(cleaned.split())


@lru_cache(maxsize=2048)
def lookup_preview_url(track_name: str, artist_name: str) -> str | None:
    """
    Resolve a 30-second preview URL from iTunes when Spotify does not supply one.

    The fallback is cached so repeated recommendations do not trigger extra
    network requests for the same track.
    """
    if not track_name or not artist_name:
        return None

    params = {
        "term": f"{track_name} {artist_name}",
        "entity": "song",
        "limit": 5,
    }

    try:
        response = httpx.get(
            "https://itunes.apple.com/search",
            params=params,
            timeout=4.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception as exc:
        logger.warning("Preview fallback lookup failed for %s - %s: %s", track_name, artist_name, exc)
        return None

    wanted_track = _normalize_preview_key(track_name)
    wanted_artist = _normalize_preview_key(artist_name)

    fallback_preview = None
    for result in results:
        preview_url = result.get("previewUrl")
        if not preview_url:
            continue

        fallback_preview = fallback_preview or preview_url
        result_track = _normalize_preview_key(result.get("trackName"))
        result_artist = _normalize_preview_key(result.get("artistName"))
        track_matches = wanted_track and (wanted_track in result_track or result_track in wanted_track)
        artist_matches = wanted_artist and (wanted_artist in result_artist or result_artist in wanted_artist)
        if track_matches and artist_matches:
            return preview_url

    return fallback_preview


def enrich_recommendation_previews(recommendations: list[dict]) -> list[dict]:
    """Populate missing preview URLs in-place for recommendation cards."""
    for rec in recommendations:
        if rec.get("preview_url"):
            continue

        track_name = rec.get("name") or rec.get("track_name") or rec.get("title") or ""
        artist_name = rec.get("artist") or rec.get("artist_name") or ""
        rec["preview_url"] = lookup_preview_url(track_name, artist_name)

    return recommendations
