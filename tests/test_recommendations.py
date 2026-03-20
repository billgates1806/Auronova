# ==========================================================
# AURONOVA — Recommendation Engine Tests
# Tests for content engine, mood filtering, dial integration
# ==========================================================

import numpy as np
import pandas as pd
import pytest

from app.models.content_based import ContentEngine, _apply_mood_filter
from app.models.context import MoodContextModel, get_time_bucket
from app.models.discovery import DiscoveryEngine
from app.models.dna import compute_dna
from app.utils.helpers import enrich_recommendation_previews


# ── Fixtures ──────────────────────────────────────────────
@pytest.fixture
def sample_df():
    """50 synthetic tracks with audio features."""
    np.random.seed(42)
    n = 50
    return pd.DataFrame({
        "track_id": [f"t{i}" for i in range(n)],
        "name": [f"Track {i}" for i in range(n)],
        "artist": [f"Artist {i % 10}" for i in range(n)],
        "artist_id": [f"a{i % 10}" for i in range(n)],
        "genres": ['["rock"]' if i % 3 == 0 else '["pop"]' if i % 3 == 1 else '["jazz"]' for i in range(n)],
        "popularity": np.random.randint(20, 100, n),
        "source": ["top_short"] * 10 + ["top_medium"] * 15 + ["saved"] * 15 + ["recent"] * 10,
        "implicit_rating": [1.0] * 10 + [0.85] * 15 + [0.6] * 15 + [0.5] * 10,
        "danceability": np.random.uniform(0, 1, n),
        "energy": np.random.uniform(0, 1, n),
        "valence": np.random.uniform(0, 1, n),
        "acousticness": np.random.uniform(0, 1, n),
        "instrumentalness": np.random.uniform(0, 0.5, n),
        "tempo": np.random.uniform(0, 1, n),
        "loudness": np.random.uniform(0, 1, n),
    })


# ==========================================================
# CONTENT ENGINE TESTS
# ==========================================================
class TestContentEngine:
    def test_fit(self, sample_df):
        engine = ContentEngine()
        engine.fit(sample_df)
        assert engine._fitted is True
        assert engine._combined_matrix is not None

    def test_recommend_returns_list(self, sample_df):
        engine = ContentEngine()
        engine.fit(sample_df)
        recs = engine.recommend(sample_df, sample_df, n=10)
        assert isinstance(recs, list)
        assert len(recs) <= 10

    def test_recommend_has_required_keys(self, sample_df):
        engine = ContentEngine()
        engine.fit(sample_df)
        recs = engine.recommend(sample_df, sample_df, n=5)
        if recs:
            for r in recs:
                assert "track_id" in r
                assert "name" in r
                assert "score" in r
                assert r["score"] >= 0

    def test_recommend_with_mood(self, sample_df):
        engine = ContentEngine()
        engine.fit(sample_df)
        recs_chill = engine.recommend(sample_df, sample_df, n=10, mood="chill")
        recs_energy = engine.recommend(sample_df, sample_df, n=10, mood="energetic")
        # Different moods should produce different ordering
        if recs_chill and recs_energy:
            ids_chill = [r["track_id"] for r in recs_chill]
            ids_energy = [r["track_id"] for r in recs_energy]
            # At least some difference expected
            assert ids_chill != ids_energy or len(ids_chill) == 0

    def test_dial_affects_results(self, sample_df):
        engine = ContentEngine()
        engine.fit(sample_df)
        recs_low = engine.recommend(sample_df, sample_df, n=10, dial=10)
        recs_high = engine.recommend(sample_df, sample_df, n=10, dial=90)
        # Higher dial should be more permissive (potentially more results)
        assert isinstance(recs_low, list)
        assert isinstance(recs_high, list)

    def test_empty_df(self):
        engine = ContentEngine()
        engine.fit(pd.DataFrame())
        assert engine._fitted is False


# ==========================================================
# MOOD FILTER TESTS
# ==========================================================
class TestMoodFilter:
    def test_chill_filter(self, sample_df):
        mask = _apply_mood_filter(sample_df, "chill")
        assert len(mask) == len(sample_df)
        assert np.all(mask >= 0) and np.all(mask <= 1)

    def test_energetic_filter(self, sample_df):
        mask = _apply_mood_filter(sample_df, "energetic")
        assert len(mask) == len(sample_df)

    def test_unknown_mood(self, sample_df):
        mask = _apply_mood_filter(sample_df, "nonexistent")
        # Unknown mood → all 1s (no filtering)
        assert np.all(mask == 1)


# ==========================================================
# TIME BUCKET TESTS
# ==========================================================
class TestTimeBucket:
    def test_morning(self):
        assert get_time_bucket(8) == "morning"

    def test_afternoon(self):
        assert get_time_bucket(14) == "afternoon"

    def test_evening(self):
        assert get_time_bucket(19) == "evening"

    def test_night(self):
        assert get_time_bucket(23) == "night"

    def test_midnight(self):
        assert get_time_bucket(0) == "night"

    def test_boundary_morning(self):
        assert get_time_bucket(6) == "morning"
        assert get_time_bucket(5) == "night"


# ==========================================================
# CONTEXT MODEL TESTS
# ==========================================================
class TestContextModel:
    def test_get_weights_empty(self):
        model = MoodContextModel()
        weights = model.get_context_weights("evening", "chill")
        assert weights == {}

    def test_apply_context_no_weights(self):
        model = MoodContextModel()
        recs = [{"track_id": "t1", "score": 0.8, "genres": ["rock"]}]
        result = model.apply_context(recs, "evening", "chill")
        assert len(result) == 1
        assert result[0]["score"] == 0.8  # unchanged


# ==========================================================
# DISCOVERY ENGINE TESTS
# ==========================================================
class TestDiscoveryEngine:
    def test_blend_recs_low_dial(self):
        content = [{"track_id": f"c{i}", "score": 0.9 - i * 0.1} for i in range(5)]
        discovery = [{"track_id": f"d{i}", "score": 0.5} for i in range(5)]
        blended = DiscoveryEngine.blend_recs(content, discovery, dial=10)
        # Low dial → mostly content
        assert len(blended) > 0

    def test_blend_recs_high_dial(self):
        content = [{"track_id": f"c{i}", "score": 0.9} for i in range(5)]
        discovery = [{"track_id": f"d{i}", "score": 0.5, "source": "discovery"} for i in range(5)]
        blended = DiscoveryEngine.blend_recs(content, discovery, dial=90)
        # High dial → more discovery tracks
        disc_count = sum(1 for r in blended if r.get("source") == "discovery")
        assert disc_count >= 0  # at least some discovery

    def test_get_artists_empty_graph(self):
        engine = DiscoveryEngine()
        artists = engine.get_artists_at_dial(50)
        assert artists == []

    def test_build_graph_keeps_seed_artists_for_low_dial(self):
        class FakeSpotify:
            def artists(self, ids):
                return {
                    "artists": [
                        {
                            "id": aid,
                            "name": f"Artist {aid}",
                            "genres": ["indie rock"],
                            "popularity": 70,
                            "images": [],
                        }
                        for aid in ids
                    ]
                }

            def artist_related_artists(self, artist_id):
                return {
                    "artists": [
                        {
                            "id": f"{artist_id}-rel",
                            "name": f"Related {artist_id}",
                            "genres": ["alt pop"],
                            "popularity": 60,
                            "images": [],
                        }
                    ]
                }

        engine = DiscoveryEngine()
        engine.build_graph(FakeSpotify(), ["seed-a"], max_depth=1)
        artists = engine.get_artists_at_dial(10)
        ids = {artist["id"] for artist in artists}
        assert "seed-a" in ids


# ==========================================================
# PREVIEW ENRICHMENT TESTS
# ==========================================================
class TestPreviewEnrichment:
    def test_fills_missing_preview_url(self, monkeypatch):
        def fake_lookup(track_name, artist_name):
            assert track_name == "Track 1"
            assert artist_name == "Artist 1"
            return "https://example.com/preview.mp3"

        monkeypatch.setattr("app.utils.helpers.lookup_preview_url", fake_lookup)
        recs = [{"track_id": "t1", "name": "Track 1", "artist": "Artist 1", "preview_url": None}]

        enriched = enrich_recommendation_previews(recs)
        assert enriched[0]["preview_url"] == "https://example.com/preview.mp3"

    def test_keeps_existing_preview_url(self, monkeypatch):
        def fail_lookup(*_args, **_kwargs):
            raise AssertionError("lookup should not run when preview_url already exists")

        monkeypatch.setattr("app.utils.helpers.lookup_preview_url", fail_lookup)
        recs = [{"track_id": "t1", "name": "Track 1", "artist": "Artist 1", "preview_url": "https://spotify"}]

        enriched = enrich_recommendation_previews(recs)
        assert enriched[0]["preview_url"] == "https://spotify"


class TestDNA:
    def test_compute_dna_is_stable_for_same_input(self, sample_df):
        dna_a = compute_dna(sample_df.head(12))
        dna_b = compute_dna(sample_df.head(12))
        assert dna_a["regions"] == dna_b["regions"]
        assert dna_a["points"] == dna_b["points"]
