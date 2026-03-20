# ==========================================================
# AURONOVA — Mood-Context Engine
# Learns (time_bucket, mood) → genre preferences over time
# ==========================================================

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from sqlalchemy.orm import Session

from app.data.database import ListeningSession

logger = logging.getLogger(__name__)


# ==========================================================
# TIME BUCKET — automatic based on current hour
# ==========================================================
def get_time_bucket(hour: int | None = None) -> str:
    """
    Map hour to a time bucket.
    Morning (6-12), Afternoon (12-17), Evening (17-21), Night (21-6).
    """
    if hour is None:
        hour = datetime.now(timezone.utc).hour
    if 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"


class MoodContextModel:
    """
    Context-aware model that learns genre weights
    for each (time_bucket, mood) combination.

    Over time, if the user plays jazz on Friday nights when
    feeling "chill", the model learns to boost jazz recs for
    that context.
    """

    def __init__(self):
        # {(time_bucket, mood): {genre: cumulative_weight}}
        self._context_prefs: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))

    # ==========================================================
    # LOG SESSION — store a listening context entry
    # ==========================================================
    def log_session(
        self,
        user_id: str,
        time_bucket: str,
        mood: str,
        genres_played: dict[str, int],
        db: Session,
    ) -> None:
        """
        Record a listening session.

        Args:
            user_id: Spotify user ID
            time_bucket: morning/afternoon/evening/night
            mood: energetic/chill/focused/melancholy
            genres_played: dict of {genre: track_count}
            db: Database session
        """
        session = ListeningSession(
            user_id=user_id,
            time_bucket=time_bucket,
            mood=mood,
            genres_played=json.dumps(genres_played),
            track_count=sum(genres_played.values()),
        )
        db.add(session)
        db.commit()

        # Update in-memory prefs
        key = (time_bucket, mood)
        for genre, count in genres_played.items():
            self._context_prefs[key][genre] += count

        logger.info(f"Logged session: {time_bucket}/{mood}, {sum(genres_played.values())} tracks")

    # ==========================================================
    # LOAD FROM DB — rebuild context model from stored sessions
    # ==========================================================
    def load_from_db(self, user_id: str, db: Session) -> None:
        """Load all past sessions from DB to build context preferences."""
        sessions = db.query(ListeningSession).filter(ListeningSession.user_id == user_id).all()

        self._context_prefs = defaultdict(lambda: defaultdict(float))
        for sess in sessions:
            key = (sess.time_bucket, sess.mood)
            try:
                genres = json.loads(sess.genres_played) if sess.genres_played else {}
            except Exception:
                genres = {}
            for genre, count in genres.items():
                self._context_prefs[key][genre] += count

        logger.info(f"Loaded {len(sessions)} sessions for user {user_id}")

    # ==========================================================
    # GET CONTEXT WEIGHTS — genre boost factors for current context
    # ==========================================================
    def get_context_weights(self, time_bucket: str, mood: str) -> dict[str, float]:
        """
        Return genre weights (0-1) for the given context.
        Higher weight = user historically listens to this genre
        in this time/mood combination.
        """
        key = (time_bucket, mood)
        prefs = self._context_prefs.get(key, {})

        if not prefs:
            return {}

        total = sum(prefs.values())
        if total == 0:
            return {}

        return {genre: count / total for genre, count in prefs.items()}

    # ==========================================================
    # APPLY CONTEXT — re-rank recommendations
    # ==========================================================
    def apply_context(
        self,
        recs: list[dict],
        time_bucket: str,
        mood: str,
        boost_factor: float = 0.3,
    ) -> list[dict]:
        """
        Re-rank recommendations by boosting tracks whose genres
        match the user's context preferences.

        Args:
            recs: List of rec dicts (must have 'genre' or 'genres' key)
            time_bucket: Current time bucket
            mood: Current mood
            boost_factor: How much to boost (0-1). 0.3 = up to 30% boost.

        Returns:
            Re-ranked rec list
        """
        weights = self.get_context_weights(time_bucket, mood)
        if not weights:
            return recs

        for rec in recs:
            # Try to match genres from the rec
            rec_genres = rec.get("genres", [])
            if isinstance(rec_genres, str):
                try:
                    rec_genres = json.loads(rec_genres)
                except Exception:
                    rec_genres = [rec_genres]

            # Compute context boost
            boost = 0.0
            for genre in rec_genres:
                boost = max(boost, weights.get(genre.lower(), 0))

            # Apply boost to score
            original_score = rec.get("score", 0.5)
            rec["score"] = original_score + (boost * boost_factor)
            rec["context_boost"] = round(boost, 3)

        # Re-sort by boosted score
        recs.sort(key=lambda x: x.get("score", 0), reverse=True)
        return recs
