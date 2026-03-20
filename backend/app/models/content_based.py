# ==========================================================
# AURONOVA — Content-Based Recommendation Engine
# TF-IDF on genres, SVD on audio features, cosine similarity
# ==========================================================

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

from app.data.processor import FEATURE_COLS, get_feature_vector, get_user_profile

logger = logging.getLogger(__name__)

# ── Mood → audio feature ranges ──────────────────────────
MOOD_PROFILES: dict[str, dict[str, tuple[float, float]]] = {
    "energetic": {"energy": (0.6, 1.0), "valence": (0.5, 1.0), "danceability": (0.5, 1.0), "tempo": (0.5, 1.0)},
    "chill":     {"energy": (0.0, 0.45), "acousticness": (0.3, 1.0), "valence": (0.2, 0.7), "tempo": (0.0, 0.5)},
    "focused":   {"energy": (0.2, 0.6), "instrumentalness": (0.3, 1.0), "speechiness": (0.0, 0.15)},
    "melancholy":{"valence": (0.0, 0.35), "energy": (0.1, 0.5), "acousticness": (0.2, 1.0)},
}


class ContentEngine:
    """
    Content-based filtering engine.

    Pipeline:
    1. TF-IDF on genre tags → genre similarity
    2. SVD on audio features → latent taste dimensions
    3. Cosine similarity for final ranking
    4. Mood filtering → narrow by audio feature ranges
    5. Discovery dial → adjust similarity threshold
    """

    def __init__(self):
        self.tfidf = TfidfVectorizer(max_features=200, stop_words="english")
        self.svd = TruncatedSVD(n_components=20, random_state=42)
        self._fitted = False
        self._genre_matrix: Optional[np.ndarray] = None
        self._feature_matrix: Optional[np.ndarray] = None
        self._combined_matrix: Optional[np.ndarray] = None
        self._track_ids: list[str] = []
        self._track_df: Optional[pd.DataFrame] = None

    # ==========================================================
    # FIT — build matrices from user's track data
    # ==========================================================
    def fit(self, df: pd.DataFrame) -> None:
        """
        Fit the engine on the user's track DataFrame.

        Args:
            df: DataFrame from build_track_matrix() with audio features + genres
        """
        if df.empty:
            logger.warning("Empty DataFrame, cannot fit ContentEngine")
            return

        self._track_df = df.copy()
        self._track_ids = df["track_id"].tolist()

        # ── Genre TF-IDF ──
        genre_texts = _build_genre_texts(df)

        if any(t != "unknown" for t in genre_texts):
            self._genre_matrix = self.tfidf.fit_transform(genre_texts).toarray()
        else:
            self._genre_matrix = np.zeros((len(df), 1))

        # ── Audio Features → SVD ──
        feature_matrix = get_feature_vector(df)
        if feature_matrix.size > 0 and feature_matrix.shape[0] > 1:
            n_components = min(20, feature_matrix.shape[0] - 1, feature_matrix.shape[1])
            if n_components > 0:
                self.svd = TruncatedSVD(n_components=n_components, random_state=42)
                self._feature_matrix = self.svd.fit_transform(feature_matrix)
            else:
                self._feature_matrix = feature_matrix
        else:
            self._feature_matrix = feature_matrix if feature_matrix.size > 0 else np.zeros((len(df), 1))

        # ── Combined matrix (genre + features) ──
        if self._genre_matrix.shape[0] == self._feature_matrix.shape[0]:
            self._combined_matrix = np.hstack([
                normalize(self._genre_matrix, norm="l2") * 0.3,  # genre weight
                normalize(self._feature_matrix, norm="l2") * 0.7,  # feature weight
            ])
        else:
            self._combined_matrix = self._feature_matrix

        self._fitted = True
        logger.info(f"ContentEngine fitted: {len(df)} tracks, combined dim={self._combined_matrix.shape[1]}")

    # ==========================================================
    # RECOMMEND — get top-N similar tracks
    # ==========================================================
    def recommend(
        self,
        user_profile_df: pd.DataFrame,
        candidate_df: pd.DataFrame,
        n: int = 20,
        mood: str | None = None,
        dial: int = 35,
    ) -> list[dict]:
        """
        Generate content-based recommendations.

        Args:
            user_profile_df: User's track DataFrame (for computing profile)
            candidate_df: Pool of candidate tracks to rank
            n: Number of recommendations
            mood: Active mood filter (or None)
            dial: Discovery dial value (0-100)

        Returns:
            List of dicts with track info + confidence score
        """
        if not self._fitted or self._combined_matrix is None:
            return []

        combined_candidate_matrix, combined_user_vec = self._combined_similarity_inputs(user_profile_df, candidate_df)
        if combined_candidate_matrix is not None and combined_user_vec is not None:
            similarities = cosine_similarity(combined_user_vec, combined_candidate_matrix)[0]
        else:
            # Fallback to raw feature vectors when the fitted matrix cannot be reused.
            user_vec = get_user_profile(user_profile_df)
            if user_vec.size == 0:
                return []

            candidate_features = get_feature_vector(candidate_df)
            if candidate_features.size == 0:
                return []

            if len(user_vec.shape) == 1:
                user_vec = user_vec.reshape(1, -1)
            if len(candidate_features.shape) == 1:
                candidate_features = candidate_features.reshape(1, -1)

            min_cols = min(user_vec.shape[1], candidate_features.shape[1])
            user_vec = user_vec[:, :min_cols]
            candidate_features = candidate_features[:, :min_cols]
            similarities = cosine_similarity(user_vec, candidate_features)[0]

        # ── Apply mood filter ──
        if mood and mood in MOOD_PROFILES:
            mood_mask = _apply_mood_filter(candidate_df, mood)
            similarities = similarities * mood_mask

        # ── Apply dial (similarity threshold) ──
        # Low dial = high similarity required, high dial = accept lower similarity
        sim_threshold = max(0.0, 0.8 - (dial / 100) * 0.8)
        mask = similarities >= sim_threshold
        similarities = similarities * mask

        # Rank and return top N
        top_indices = np.argsort(similarities)[::-1][:n]

        results = []
        for idx in top_indices:
            if idx >= len(candidate_df):
                continue
            row = candidate_df.iloc[idx]
            mood_fit = _score_mood_fit(row, mood)
            score = float(similarities[idx] * 0.75 + mood_fit * 0.25)
            if score <= 0:
                continue
            results.append({
                "track_id": row.get("track_id", ""),
                "name": row.get("name", "Unknown"),
                "artist": row.get("artist", "Unknown"),
                "album": row.get("album", ""),
                "preview_url": row.get("preview_url"),
                "genres": row.get("genres", []),
                "score": round(score, 4),
                "mood_score": round(float(mood_fit), 4),
                "source": "content",
            })

        return results

    def _combined_similarity_inputs(
        self,
        user_profile_df: pd.DataFrame,
        candidate_df: pd.DataFrame,
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """Map profile/candidate frames into the fitted combined space when possible."""
        if self._combined_matrix is None or self._track_df is None:
            return None, None

        track_index = {track_id: idx for idx, track_id in enumerate(self._track_ids)}

        candidate_indices = []
        for track_id in candidate_df["track_id"].tolist():
            idx = track_index.get(track_id)
            if idx is None:
                return None, None
            candidate_indices.append(idx)

        profile_indices = []
        profile_weights = []
        for _, row in user_profile_df.iterrows():
            idx = track_index.get(row.get("track_id", ""))
            if idx is None:
                continue
            profile_indices.append(idx)
            profile_weights.append(float(row.get("implicit_rating", 0.5) or 0.5))

        if not profile_indices:
            return None, None

        combined_candidates = self._combined_matrix[candidate_indices]
        combined_profile = self._combined_matrix[profile_indices]
        weight_sum = sum(profile_weights)
        if weight_sum <= 0:
            profile_weights = [1.0] * len(profile_indices)

        user_vec = np.average(combined_profile, axis=0, weights=profile_weights).reshape(1, -1)
        return combined_candidates, user_vec


# ==========================================================
# MOOD FILTER HELPER
# ==========================================================
def _apply_mood_filter(df: pd.DataFrame, mood: str) -> np.ndarray:
    """
    Return a mask (0 or 1 array) based on mood-appropriate feature ranges.
    Tracks outside the mood's ranges get 0 weight.
    """
    profile = MOOD_PROFILES.get(mood, {})
    mask = np.ones(len(df))

    for feature, (lo, hi) in profile.items():
        if feature in df.columns:
            values = df[feature].fillna(0.5).values
            feature_mask = ((values >= lo) & (values <= hi)).astype(float)
            # Soft mask — partial credit for near-matches
            soft = np.clip((values - lo) / max(hi - lo, 0.01), 0, 1)
            mask *= (feature_mask * 0.7 + soft * 0.3)

    return mask


def _build_genre_texts(df: pd.DataFrame) -> list[str]:
    """Normalize genre payloads into text for TF-IDF fitting."""
    import json

    genre_texts: list[str] = []
    for value in df["genres"]:
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
            genre_texts.append(" ".join(parsed) if parsed else "unknown")
        except Exception:
            genre_texts.append("unknown")

    return genre_texts


def _score_mood_fit(row: pd.Series, mood: str | None) -> float:
    """Score how well a single row matches the requested mood profile."""
    if not mood or mood not in MOOD_PROFILES:
        return 0.5

    profile = MOOD_PROFILES[mood]
    scores: list[float] = []
    for feature, (lo, hi) in profile.items():
        if feature not in row:
            continue

        try:
            value = float(row.get(feature, 0.5))
        except (TypeError, ValueError):
            value = 0.5

        if lo <= value <= hi:
            scores.append(1.0)
            continue

        span = max(hi - lo, 0.01)
        if value < lo:
            distance = lo - value
        else:
            distance = value - hi
        scores.append(max(0.0, 1.0 - (distance / span)))

    if not scores:
        return 0.5

    return float(np.mean(scores))
