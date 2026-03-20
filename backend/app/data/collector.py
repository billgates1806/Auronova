# ==========================================================
# AURONOVA — Spotify Data Collector
# Fetches user data from Spotify Web API via spotipy
# ==========================================================

from __future__ import annotations

import json
import logging
from typing import Any

import spotipy
from sqlalchemy.orm import Session

from app.data.database import AudioFeatures, Track, UserTrack

logger = logging.getLogger(__name__)


# ==========================================================
# FETCH TOP TRACKS
# Returns user's top tracks for a given time range
# ==========================================================
def fetch_top_tracks(sp: spotipy.Spotify, time_range: str = "medium_term", limit: int = 50) -> list[dict]:
    """
    Fetch user's top tracks from Spotify.

    Args:
        sp: Authenticated Spotify client
        time_range: "short_term" (4 weeks), "medium_term" (6 months), "long_term" (years)
        limit: Max tracks to fetch (up to 50)

    Returns:
        List of track dicts with id, name, artist, album, etc.
    """
    try:
        results = sp.current_user_top_tracks(limit=limit, time_range=time_range)
    except Exception as e:
        logger.warning("Failed to fetch top tracks (%s): %s", time_range, e)
        return []
    return _parse_tracks(results.get("items", []))


# ==========================================================
# FETCH TOP ARTISTS
# Returns user's top artists for a given time range
# ==========================================================
def fetch_top_artists(sp: spotipy.Spotify, time_range: str = "medium_term", limit: int = 50) -> list[dict]:
    """
    Fetch user's top artists from Spotify.
    Includes fallback to other time ranges if the primary one is empty.

    Returns list of dicts with: id, name, genres, popularity, image_url
    """
    time_ranges = [time_range]
    if time_range == "medium_term":
        time_ranges.extend(["short_term", "long_term"])

    results = {}
    for tr in time_ranges:
        try:
            results = sp.current_user_top_artists(limit=limit, time_range=tr)
        except Exception as e:
            logger.warning("Failed to fetch top artists (%s): %s", tr, e)
            continue
        if results.get("items"):
            break

    artists = []
    for item in results.get("items", []):
        genres = item.get("genres", [])
        if not genres:
            genres = ["Pop"]
            
        pop = item.get("popularity")
        if not pop or pop <= 0:
            pop = 50
            
        artists.append({
            "id": item["id"],
            "name": item["name"],
            "genres": genres,
            "popularity": pop,
            "image_url": item["images"][0]["url"] if item.get("images") else None,
            "spotify_url": item.get("external_urls", {}).get("spotify"),
        })
    return artists


# ==========================================================
# FETCH RECENTLY PLAYED
# Returns the user's last 50 recently played tracks
# ==========================================================
def fetch_recently_played(sp: spotipy.Spotify, limit: int = 50) -> list[dict]:
    """Fetch recently played tracks (last ~50)."""
    try:
        results = sp.current_user_recently_played(limit=limit)
    except Exception as e:
        logger.warning("Failed to fetch recently played tracks: %s", e)
        return []
    items = results.get("items", [])
    return _parse_tracks([item["track"] for item in items])


# ==========================================================
# FETCH SAVED / LIKED TRACKS
# Returns user's saved tracks (paginated)
# ==========================================================
def fetch_saved_tracks(sp: spotipy.Spotify, limit: int = 100) -> list[dict]:
    """
    Fetch user's liked/saved tracks (paginated).

    Args:
        limit: Total tracks to fetch (can exceed 50 via pagination)
    """
    tracks = []
    offset = 0
    batch = min(limit, 50)

    while offset < limit:
        try:
            results = sp.current_user_saved_tracks(limit=batch, offset=offset)
        except Exception as e:
            logger.warning("Failed to fetch saved tracks at offset %s: %s", offset, e)
            break
        items = results.get("items", [])
        if not items:
            break
        tracks.extend(_parse_tracks([item["track"] for item in items]))
        offset += len(items)

    return tracks[:limit]


# ==========================================================
# FETCH AUDIO FEATURES
# Gets Spotify's 13 audio features for a batch of tracks
# ==========================================================
def fetch_audio_features(sp: spotipy.Spotify, track_ids: list[str]) -> dict[str, dict]:
    """
    Fetch audio features for a list of track IDs.
    Handles batching (Spotify allows max 100 per request).

    Returns dict mapping track_id → {danceability, energy, ...}
    """
    features_map: dict[str, dict] = {}
    # Batch in groups of 100
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i : i + 100]
        try:
            results = sp.audio_features(batch)
            for feat in results:
                if feat is None:
                    continue
                features_map[feat["id"]] = {
                    "danceability": feat.get("danceability", 0),
                    "energy": feat.get("energy", 0),
                    "key": feat.get("key", 0),
                    "loudness": feat.get("loudness", 0),
                    "mode": feat.get("mode", 0),
                    "speechiness": feat.get("speechiness", 0),
                    "acousticness": feat.get("acousticness", 0),
                    "instrumentalness": feat.get("instrumentalness", 0),
                    "liveness": feat.get("liveness", 0),
                    "valence": feat.get("valence", 0),
                    "tempo": feat.get("tempo", 0),
                    "time_signature": feat.get("time_signature", 4),
                    "duration_ms": feat.get("duration_ms", 0),
                }
        except spotipy.SpotifyException as e:
            if e.http_status == 403:
                logger.warning(f"Audio features endpoint is forbidden (403). Proceeding without audio features.")
            else:
                logger.warning(f"Audio features batch failed: {e}")
        except Exception as e:
            logger.warning(f"Audio features batch failed: {e}")

    return features_map


# ==========================================================
# FULL DATA SYNC
# Orchestrates fetching all data and storing in DB
# ==========================================================
def sync_user_data(sp: spotipy.Spotify, user_id: str, db: Session) -> dict[str, int]:
    """
    Full data sync for a user:
    1. Fetch top tracks (short, medium, long term)
    2. Fetch recently played
    3. Fetch saved tracks
    4. Fetch audio features for all
    5. Store everything in DB

    Returns counts: {"tracks": N, "features": N}
    """
    all_tracks: dict[str, dict] = {}  # track_id → track data
    source_map: dict[str, str] = {}   # track_id → source

    # ── Gather tracks from all sources ──
    for time_range, source_label in [
        ("short_term", "top_short"),
        ("medium_term", "top_medium"),
        ("long_term", "top_long"),
    ]:
        tracks = fetch_top_tracks(sp, time_range)
        for t in tracks:
            all_tracks[t["id"]] = t
            if t["id"] not in source_map:
                source_map[t["id"]] = source_label

    for t in fetch_recently_played(sp):
        all_tracks[t["id"]] = t
        if t["id"] not in source_map:
            source_map[t["id"]] = "recent"

    for t in fetch_saved_tracks(sp, limit=200):
        all_tracks[t["id"]] = t
        if t["id"] not in source_map:
            source_map[t["id"]] = "saved"

    # ── Fetch audio features ──
    track_ids = list(all_tracks.keys())
    features = fetch_audio_features(sp, track_ids)

    # ── Fetch artist genres to attach to tracks ──
    artist_ids = list({t.get("artist_id") for t in all_tracks.values() if t.get("artist_id")})
    artist_genres = {}
    for i in range(0, len(artist_ids), 50):
        try:
            res = sp.artists(artist_ids[i:i+50])
            for a in res.get("artists", []):
                if a and "id" in a:
                    artist_genres[a["id"]] = a.get("genres", [])
        except Exception as e:
            logger.warning(f"Failed to fetch artist genres chunk: {e}")

    for tdata in all_tracks.values():
        aid = tdata.get("artist_id")
        if aid in artist_genres and artist_genres[aid]:
            tdata["genres"] = artist_genres[aid]
        else:
            tdata["genres"] = ["Pop"]

    # ── Store in DB ──
    stored_tracks = 0
    stored_features = 0

    for tid, tdata in all_tracks.items():
        # Upsert track
        existing = db.query(Track).filter(Track.id == tid).first()
        if not existing:
            existing = Track(id=tid)
            db.add(existing)
            stored_tracks += 1

        existing.name = tdata["name"]
        existing.artist = tdata["artist"]
        existing.artist_id = tdata.get("artist_id")
        existing.album = tdata.get("album")
        existing.album_art_url = tdata.get("album_art_url")
        existing.preview_url = tdata.get("preview_url")
        existing.popularity = tdata.get("popularity", 0)
        existing.duration_ms = tdata.get("duration_ms", 0)
        existing.genres = json.dumps(tdata.get("genres", []))

        # UserTrack link (avoid duplicates)
        exists_link = (
            db.query(UserTrack)
            .filter(UserTrack.user_id == user_id, UserTrack.track_id == tid, UserTrack.source == source_map[tid])
            .first()
        )
        if not exists_link:
            implicit_rating = _source_to_rating(source_map[tid])
            db.add(UserTrack(user_id=user_id, track_id=tid, source=source_map[tid], implicit_rating=implicit_rating))

        # Audio features
        if tid in features:
            existing_feat = db.query(AudioFeatures).filter(AudioFeatures.track_id == tid).first()
            if not existing_feat:
                db.add(AudioFeatures(track_id=tid, **features[tid]))
                stored_features += 1

    db.commit()
    logger.info(f"Synced {stored_tracks} tracks, {stored_features} features for user {user_id}")
    return {"tracks": stored_tracks, "features": stored_features}


# ==========================================================
# INTERNAL HELPERS
# ==========================================================
def _parse_tracks(items: list[dict[str, Any]]) -> list[dict]:
    """Parse Spotify track objects into clean dicts."""
    tracks = []
    for item in items:
        if not item:
            continue
        artists = item.get("artists", [])
        album = item.get("album", {})
        tracks.append({
            "id": item["id"],
            "name": item["name"],
            "artist": artists[0]["name"] if artists else "Unknown",
            "artist_id": artists[0]["id"] if artists else None,
            "album": album.get("name"),
            "album_art_url": album["images"][0]["url"] if album.get("images") else None,
            "preview_url": item.get("preview_url"),
            "spotify_url": item.get("external_urls", {}).get("spotify"),
            "popularity": item.get("popularity", 0),
            "duration_ms": item.get("duration_ms", 0),
            "genres": [],  # genres come from artist, not track
        })
    return tracks


def _source_to_rating(source: str) -> float:
    """Map track source to an implicit rating for the rec engine."""
    return {
        "top_short": 1.0,   # short-term top = highest signal
        "top_medium": 0.85,
        "top_long": 0.7,
        "saved": 0.6,
        "recent": 0.5,
    }.get(source, 0.5)
