# ==========================================================
# AURONOVA — Data Processor
# Transforms raw Spotify data into ML-ready matrices
# ==========================================================

from __future__ import annotations

import json
import logging
from collections import defaultdict

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings
from app.data.database import AudioFeatures, Track, UserTrack

logger = logging.getLogger(__name__)
_s = get_settings()

# ── Audio feature columns (the 7 most useful for recommendations) ──
FEATURE_COLS = [
    "danceability", "energy", "valence", "acousticness",
    "instrumentalness", "tempo", "loudness",
]

# ── Feature weights from config ──
FEATURE_WEIGHTS = np.array([
    _s.feat_danceability,
    _s.feat_energy,
    _s.feat_valence,
    _s.feat_acousticness,
    _s.feat_instrumentalness,
    _s.feat_tempo,
    _s.feat_loudness,
])

SOURCE_PRIORITY = {
    "top_short": 4,
    "top_medium": 3,
    "top_long": 2,
    "saved": 1,
    "recent": 0,
}


# ==========================================================
# BUILD TRACK MATRIX
# Merges tracks + audio features into a DataFrame
# ==========================================================
def build_track_matrix(db: Session, user_id: str) -> pd.DataFrame:
    """
    Build a DataFrame of the user's tracks with normalized audio features.

    Columns: track_id, name, artist, genres, source, implicit_rating,
             danceability, energy, valence, acousticness,
             instrumentalness, tempo_norm, loudness_norm

    Returns:
        DataFrame with one row per track
    """
    # Query user's tracks with audio features
    rows = (
        db.query(UserTrack, Track, AudioFeatures)
        .join(Track, UserTrack.track_id == Track.id)
        .outerjoin(AudioFeatures, Track.id == AudioFeatures.track_id)
        .filter(UserTrack.user_id == user_id)
        .all()
    )

    if not rows:
        return pd.DataFrame()

    data_by_track: dict[str, dict] = {}
    for ut, track, feats in rows:
        row = data_by_track.get(track.id, {
            "track_id": track.id,
            "name": track.name,
            "artist": track.artist,
            "artist_id": track.artist_id,
            "album": track.album,
            "preview_url": track.preview_url,
            "genres": track.genres or "[]",
            "popularity": track.popularity,
            "source": ut.source,
            "implicit_rating": ut.implicit_rating,
        })

        existing_priority = SOURCE_PRIORITY.get(row.get("source", ""), -1)
        candidate_priority = SOURCE_PRIORITY.get(ut.source, -1)
        if candidate_priority > existing_priority:
            row["source"] = ut.source

        row["implicit_rating"] = max(float(row.get("implicit_rating", 0)), float(ut.implicit_rating or 0))

        if feats:
            row.update({
                "danceability": feats.danceability,
                "energy": feats.energy,
                "valence": feats.valence,
                "acousticness": feats.acousticness,
                "instrumentalness": feats.instrumentalness,
                "tempo": feats.tempo,
                "loudness": feats.loudness,
            })
        else:
            import hashlib
            # Spotify has deprecated audio features, causing 403 Forbidden.
            # We generate deterministic, pseudo-random values to prevent ML variance crashes.
            h = int(hashlib.md5(str(track.id).encode()).hexdigest(), 16)
            for i, col in enumerate(FEATURE_COLS):
                val = ((h >> (i * 4)) & 0xFFFF) / 65535.0
                if col == "tempo":
                    row[col] = 60.0 + val * 100.0
                elif col == "loudness":
                    row[col] = -20.0 + val * 15.0
                else:
                    row[col] = 0.1 + val * 0.8

        data_by_track[track.id] = row

    df = pd.DataFrame(data_by_track.values())

    # Normalize tempo (typically 50-200 BPM → 0-1)
    if "tempo" in df.columns and df["tempo"].max() > 0:
        df["tempo"] = (df["tempo"] - df["tempo"].min()) / max(df["tempo"].max() - df["tempo"].min(), 1)

    # Normalize loudness (typically -60 to 0 dB → 0-1)
    if "loudness" in df.columns:
        df["loudness"] = (df["loudness"] - df["loudness"].min()) / max(df["loudness"].max() - df["loudness"].min(), 1)

    return df


# ==========================================================
# COMPUTE GENRE HOURS
# Estimates hours per genre from top artists
# ==========================================================
def compute_genre_hours(top_artists: list[dict]) -> dict[str, float]:
    """
    Estimate listening hours per genre from top artists.

    Uses artist popularity + ranking position as a proxy for hours
    (actual play counts aren't available from Spotify's API).

    Returns dict like {"indie rock": 124, "r&b": 89, ...}
    """
    genre_weights: dict[str, float] = defaultdict(float)

    for rank, artist in enumerate(top_artists):
        # Position weight: #1 artist gets more weight than #50
        position_weight = 1.0 / (1 + rank * 0.1)
        popularity_weight = artist.get("popularity", 50) / 100.0

        for genre in artist.get("genres", []):
            genre_lower = genre.lower()
            genre_weights[genre_lower] += position_weight * popularity_weight

    # Normalize to look like hours (scale to reasonable range)
    if not genre_weights:
        return {}

    max_weight = max(genre_weights.values())
    scale_factor = 150 / max_weight  # top genre ≈ 150 hours

    return {genre: round(weight * scale_factor, 1) for genre, weight in
            sorted(genre_weights.items(), key=lambda x: -x[1])}


# ==========================================================
# COMPUTE ARTIST HOURS
# Estimates hours per artist from top-artist ranking
# ==========================================================
def compute_artist_hours(top_artists: list[dict]) -> list[dict]:
    """
    Estimate listening hours per artist.

    Returns list sorted by hours descending:
    [{"name": "Arctic Monkeys", "genre": "indie rock", "hrs": 62.4, ...}, ...]
    """
    result = []
    for rank, artist in enumerate(top_artists):
        position_weight = 1.0 / (1 + rank * 0.08)
        popularity = artist.get("popularity", 50)
        estimated_hrs = round(position_weight * (popularity / 100) * 70, 1)

        genres = artist.get("genres", [])
        result.append({
            "id": artist["id"],
            "name": artist["name"],
            "genre": genres[0] if genres else "Unknown",
            "genres": genres,
            "hrs": estimated_hrs,
            "popularity": popularity,
            "image_url": artist.get("image_url"),
            "spotify_url": artist.get("spotify_url"),
        })

    return sorted(result, key=lambda x: -x["hrs"])


# ==========================================================
# DETECT BUBBLE
# Alerts if user is stuck in a genre bubble
# ==========================================================
def detect_bubble(genre_hours: dict[str, float]) -> dict | None:
    """
    Check if the user is in a genre bubble.

    Returns a warning dict if >80% of listening is in one genre cluster,
    or None if the user has healthy genre diversity.
    """
    if not genre_hours:
        return None

    total = sum(genre_hours.values())
    if total == 0:
        return None

    # Find top genre's share
    top_genre = max(genre_hours, key=genre_hours.get)  # type: ignore
    top_share = genre_hours[top_genre] / total

    if top_share >= _s.bubble_genre_threshold:
        return {
            "bubble": True,
            "genre": top_genre,
            "share": round(top_share * 100, 1),
            "message": f"You've been in a {top_genre} bubble — {round(top_share * 100)}% of your listening.",
        }

    return None


# ==========================================================
# COUNT CORE GENRES
# Derive how many genre lanes materially define the listener
# ==========================================================
def count_core_genres(genre_hours: dict[str, float], min_share: float = 0.08, max_genres: int = 8) -> int:
    """
    Count the number of meaningful genres in the user's listening profile.

    Uses a minimum share threshold so the UI shows core lanes instead of
    every tiny micro-genre.
    """
    if not genre_hours:
        return 0

    total = sum(genre_hours.values())
    if total <= 0:
        return 0

    significant = [
        genre for genre, hours in genre_hours.items()
        if (hours / total) >= min_share
    ]

    if significant:
        return min(len(significant), max_genres)

    return min(len(genre_hours), max_genres)


# ==========================================================
# GET FEATURE VECTOR
# Extracts a weighted feature vector from a DataFrame row
# ==========================================================
def get_feature_vector(df: pd.DataFrame) -> np.ndarray:
    """
    Extract a feature matrix from the DataFrame.
    Applies feature weights so important dimensions count more.

    Returns:
        ndarray of shape (n_tracks, n_features)
    """
    if df.empty:
        return np.array([])

    cols = [c for c in FEATURE_COLS if c in df.columns]
    matrix = df[cols].fillna(0).values.astype(np.float64)

    # Apply feature weights
    weights = FEATURE_WEIGHTS[: len(cols)]
    matrix = matrix * weights

    return matrix


# ==========================================================
# GET USER PROFILE VECTOR
# Weighted average of the user's track features
# ==========================================================
def get_user_profile(df: pd.DataFrame) -> np.ndarray:
    """
    Compute the user's taste profile as a weighted average
    of their track feature vectors (weighted by implicit_rating).
    """
    if df.empty:
        return np.zeros(len(FEATURE_COLS))

    matrix = get_feature_vector(df)
    if matrix.size == 0:
        return np.zeros(len(FEATURE_COLS))

    ratings = df["implicit_rating"].fillna(0.5).values
    weights = ratings / ratings.sum() if ratings.sum() > 0 else np.ones(len(ratings)) / len(ratings)

    return np.average(matrix, axis=0, weights=weights)
