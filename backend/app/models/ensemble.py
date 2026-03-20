# ==========================================================
# AURONOVA — Ensemble Recommender
# Blends content-based, NCF, context, and discovery engines
# ==========================================================

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Optional

import pandas as pd
import spotipy
from sqlalchemy.orm import Session

from app.config import get_settings
from app.data.collector import sync_user_data
from app.data.processor import build_track_matrix, compute_artist_hours, compute_genre_hours, detect_bubble
from app.models.content_based import ContentEngine
from app.models.context import MoodContextModel, get_time_bucket
from app.models.deep_model import NCFTrainer
from app.models.discovery import DiscoveryEngine
from app.models.dna import compute_dna, compute_genre_breakdown
from app.utils.helpers import clamp

logger = logging.getLogger(__name__)
_s = get_settings()

MOOD_GENRE_HINTS: dict[str, tuple[str, ...]] = {
    "energetic": ("dance", "edm", "electro", "house", "techno", "hip hop", "rap", "pop", "workout"),
    "chill": ("chill", "ambient", "lofi", "acoustic", "dream", "indie", "soul", "r&b"),
    "focused": ("instrumental", "classical", "piano", "study", "soundtrack", "jazz", "lofi"),
    "melancholy": ("sad", "blues", "folk", "singer songwriter", "indie", "ballad", "emo"),
}


class EnsembleRecommender:
    """
    Master recommendation orchestrator.

    Blends 4 engines:
    - Content-based (TF-IDF + SVD + cosine sim)  [weight: 0.40]
    - NCF (neural collaborative filtering)        [weight: 0.25]
    - Context (mood + time-of-day)                [weight: 0.20]
    - Discovery (BFS on artist graph)             [weight: 0.15]

    Final score = sum(engine_score * weight) for each track.
    """

    def __init__(self):
        self.content = ContentEngine()
        self.context = MoodContextModel()
        self.ncf = NCFTrainer()
        self.discovery = DiscoveryEngine()
        self._user_df: Optional[pd.DataFrame] = None
        self._initialized = False

    # ==========================================================
    # INITIALIZE — load user data and fit all engines
    # ==========================================================
    def initialize(self, sp: spotipy.Spotify, user_id: str, db: Session) -> dict:
        """
        Full initialization:
        1. Sync Spotify data → DB
        2. Build track matrix
        3. Fit content engine
        4. Load context history
        5. Build discovery graph
        6. Train NCF (if enough data)

        Returns init stats.
        """
        # Step 1: Sync
        sync_stats = sync_user_data(sp, user_id, db)

        # Step 2: Build track matrix
        self._user_df = build_track_matrix(db, user_id)
        if self._user_df.empty:
            logger.warning(f"No tracks for user {user_id}")
            return {"error": "No listening data found", **sync_stats}

        # Step 3: Fit content engine
        self.content.fit(self._user_df)

        # Step 4: Load context
        self.context.load_from_db(user_id, db)

        # Step 5: Build discovery graph from top artists
        top_artist_ids = self._user_df["artist_id"].dropna().unique().tolist()[:10]
        if top_artist_ids:
            try:
                self.discovery.build_graph(sp, top_artist_ids, max_depth=3)
            except Exception as e:
                logger.warning(f"Discovery graph build failed: {e}")

        # Step 6: Train NCF
        interactions = [
            {"user_id": user_id, "track_id": row["track_id"], "rating": row["implicit_rating"]}
            for _, row in self._user_df.iterrows()
        ]
        if len(interactions) >= 10:
            self.ncf.train(interactions)

        self._initialized = True
        logger.info(f"Ensemble initialized for {user_id}: {len(self._user_df)} tracks")

        return {
            "tracks_synced": sync_stats.get("tracks", 0),
            "total_tracks": len(self._user_df),
            "content_fitted": True,
            "ncf_trained": len(interactions) >= 10,
            "graph_size": len(self.discovery._artist_info),
        }

    # ==========================================================
    # RECOMMEND — main recommendation endpoint
    # ==========================================================
    def recommend(
        self,
        sp: spotipy.Spotify,
        user_id: str,
        mood: str = "chill",
        dial: int = 35,
        n: int = 20,
    ) -> list[dict]:
        """
        Generate blended recommendations.

        Args:
            sp: Authenticated Spotify client
            user_id: Spotify user ID
            mood: Current mood selection
            dial: Discovery dial value (0-100)
            n: Number of recs to return

        Returns:
            List of rec dicts sorted by blended score
        """
        if self._user_df is None or self._user_df.empty:
            return []

        time_bucket = get_time_bucket()
        all_recs: dict[str, dict] = {}  # track_id → rec data
        seen_artists = set(self._user_df["artist_id"].dropna().tolist())
        core_genres = self._extract_core_genres()
        dial_factor = dial / 100.0

        # ── Content-based recs ──
        content_recs = self.content.recommend(
            user_profile_df=self._user_df,
            candidate_df=self._user_df,
            n=n * 2,
            mood=mood,
            dial=dial,
        )
        for r in content_recs:
            tid = r["track_id"]
            all_recs[tid] = {
                **r,
                "content_score": r.get("score", 0),
                "mood_score": r.get("mood_score", 0.5),
                "familiarity_score": r.get("score", 0),
                "novelty_score": 0.05,
            }

        # ── NCF scores ──
        candidate_ids = list(all_recs.keys())
        if candidate_ids:
            ncf_scores = self.ncf.predict(user_id, candidate_ids)
            for tid, score in ncf_scores:
                if tid in all_recs:
                    all_recs[tid]["ncf_score"] = score

        # ── Discovery recs ──
        if dial > 15:
            discovery_recs = self.discovery.get_discovery_tracks(sp, dial, limit=n)
            for r in discovery_recs:
                tid = r.get("track_id", "")
                if tid not in all_recs:
                    all_recs[tid] = r
                depth = max(1, r.get("discovery_depth", 1))
                depth_norm = clamp(depth / max(_s.bfs_max_depth, 1))
                overlap = self._genre_overlap_score(r.get("genres", []), core_genres)
                if r.get("artist_id") in seen_artists:
                    overlap = max(overlap, 0.65)

                all_recs[tid]["familiarity_score"] = max(
                    all_recs[tid].get("familiarity_score", 0),
                    clamp((1.0 - depth_norm) * 0.7 + overlap * 0.3),
                )
                all_recs[tid]["novelty_score"] = max(
                    all_recs[tid].get("novelty_score", 0),
                    clamp(depth_norm * 0.75 + (1.0 - overlap) * 0.25),
                )
                all_recs[tid]["mood_score"] = max(
                    all_recs[tid].get("mood_score", 0),
                    self._genre_mood_score(r.get("genres", []), mood),
                )
                all_recs[tid]["discovery_score"] = 1.0 - depth_norm
                all_recs[tid]["why"] = r.get("why", "")

        # ── Compute blended score ──
        results = []
        for tid, rec in all_recs.items():
            content_s = rec.get("content_score", 0)
            ncf_s = rec.get("ncf_score", 0.5)
            familiarity_s = rec.get("familiarity_score", content_s)
            novelty_s = rec.get("novelty_score", rec.get("discovery_score", 0))
            mood_s = rec.get("mood_score", 0.5)

            dial_mix = ((1.0 - dial_factor) * familiarity_s) + (dial_factor * novelty_s)
            blended = (
                dial_mix * 0.6
                + mood_s * 0.25
                + ncf_s * 0.1
                + content_s * 0.05
            )
            rec["score"] = round(blended, 4)
            rec["track_id"] = tid
            results.append(rec)

        # ── Context re-ranking ──
        results = self.context.apply_context(results, time_bucket, mood)

        # ── Deduplicate and limit ──
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        discovery_candidates = [r for r in results if r.get("source") == "discovery"]
        familiar_candidates = [r for r in results if r.get("source") != "discovery"]
        discovery_target = min(
            len(discovery_candidates),
            max(0, round(n * clamp(0.15 + (dial_factor * 0.7), 0.0, 0.85))),
        )
        familiar_target = max(0, n - discovery_target)

        mixed = discovery_candidates[:discovery_target] + familiar_candidates[:familiar_target]
        if len(mixed) < n:
            leftovers = discovery_candidates[discovery_target:] + familiar_candidates[familiar_target:]
            mixed.extend(leftovers[: n - len(mixed)])

        seen = set()
        final = []
        for r in mixed:
            tid = r.get("track_id", "")
            if tid not in seen:
                seen.add(tid)
                final.append(r)

        return final[:n]

    # ==========================================================
    # CONVENIENCE: get dashboard data
    # ==========================================================
    def get_dashboard_data(self, sp: spotipy.Spotify) -> dict:
        """Return pre-computed dashboard stats."""
        from app.data.collector import fetch_top_artists

        top_artists_raw = fetch_top_artists(sp, "medium_term", 50)
        artist_hours = compute_artist_hours(top_artists_raw)
        genre_hours = compute_genre_hours(top_artists_raw)
        bubble = detect_bubble(genre_hours)

        total_hours = sum(genre_hours.values())
        unique_artists = len(top_artists_raw)
        unique_genres = len(genre_hours)

        return {
            "total_hours": round(total_hours, 1),
            "unique_artists": unique_artists,
            "unique_genres": unique_genres,
            "peak_time": "Evening",  # TODO: compute from recent plays
            "top_artists": artist_hours[:5],
            "genre_hours": dict(list(genre_hours.items())[:10]),
            "bubble": bubble,
        }

    # ==========================================================
    # CONVENIENCE: get music DNA data
    # ==========================================================
    def get_dna_data(self) -> dict:
        """Generate Music DNA visualization data."""
        if self._user_df is None or self._user_df.empty:
            return {"points": [], "regions": [], "genre_breakdown": []}

        dna = compute_dna(self._user_df)
        dna["genre_breakdown"] = compute_genre_breakdown(self._user_df)
        return dna

    def _extract_core_genres(self, limit: int = 8) -> set[str]:
        """Return the user's most frequent genres for overlap scoring."""
        if self._user_df is None or self._user_df.empty:
            return set()

        counts: Counter[str] = Counter()
        for value in self._user_df.get("genres", []):
            genres = self._parse_genres(value)
            for genre in genres:
                counts[genre.lower()] += 1

        return {genre for genre, _ in counts.most_common(limit)}

    @staticmethod
    def _parse_genres(value) -> list[str]:
        """Normalize stored genre payloads to a lowercase list."""
        if isinstance(value, list):
            return [str(item).lower() for item in value]

        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                parsed = [value]
            if isinstance(parsed, list):
                return [str(item).lower() for item in parsed]
            return [str(parsed).lower()]

        return []

    def _genre_overlap_score(self, genres: list[str] | str, core_genres: set[str]) -> float:
        """Score how close a recommendation's genres are to the user's core genres."""
        parsed = set(self._parse_genres(genres))
        if not parsed or not core_genres:
            return 0.0

        direct_hits = len(parsed & core_genres)
        partial_hits = sum(
            1
            for genre in parsed
            for core in core_genres
            if genre != core and (genre in core or core in genre)
        )
        return clamp((direct_hits * 0.7) + (partial_hits * 0.2), 0.0, 1.0)

    def _genre_mood_score(self, genres: list[str] | str, mood: str) -> float:
        """Approximate mood fit for discovery tracks from artist genres."""
        hints = MOOD_GENRE_HINTS.get(mood)
        if not hints:
            return 0.5

        parsed = self._parse_genres(genres)
        if not parsed:
            return 0.45

        hits = 0
        for genre in parsed:
            if any(hint in genre for hint in hints):
                hits += 1

        return clamp(0.35 + (hits / max(len(parsed), 1)), 0.0, 1.0)
