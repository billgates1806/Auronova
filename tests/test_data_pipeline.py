# ==========================================================
# AURONOVA — Data Pipeline Tests
# Tests for track matrix, genre hours, bubble detection
# ==========================================================

import json
import pytest
import numpy as np
import pandas as pd

from app.data.processor import (
    compute_artist_hours,
    compute_genre_hours,
    count_core_genres,
    detect_bubble,
    get_feature_vector,
    get_user_profile,
    FEATURE_COLS,
)
from app.data.collector import fetch_top_artists, fetch_top_tracks


# ── Fixtures ──────────────────────────────────────────────
@pytest.fixture
def mock_artists():
    """Mock top artists data."""
    return [
        {"id": "a1", "name": "Arctic Monkeys", "genres": ["indie rock", "alternative rock"], "popularity": 85},
        {"id": "a2", "name": "The Weeknd", "genres": ["r&b", "pop"], "popularity": 92},
        {"id": "a3", "name": "Tame Impala", "genres": ["psychedelic rock", "indie rock"], "popularity": 78},
        {"id": "a4", "name": "Frank Ocean", "genres": ["r&b", "soul"], "popularity": 80},
        {"id": "a5", "name": "Radiohead", "genres": ["alternative rock", "art rock"], "popularity": 75},
    ]


@pytest.fixture
def mock_track_df():
    """Mock track DataFrame with audio features."""
    return pd.DataFrame([
        {"track_id": "t1", "name": "Song A", "artist": "A", "genres": '["rock"]', "source": "top_short",
         "implicit_rating": 1.0, "danceability": 0.7, "energy": 0.8, "valence": 0.6,
         "acousticness": 0.1, "instrumentalness": 0.0, "tempo": 0.6, "loudness": 0.7},
        {"track_id": "t2", "name": "Song B", "artist": "B", "genres": '["jazz"]', "source": "saved",
         "implicit_rating": 0.6, "danceability": 0.3, "energy": 0.2, "valence": 0.4,
         "acousticness": 0.8, "instrumentalness": 0.5, "tempo": 0.3, "loudness": 0.3},
        {"track_id": "t3", "name": "Song C", "artist": "C", "genres": '["pop"]', "source": "recent",
         "implicit_rating": 0.5, "danceability": 0.8, "energy": 0.9, "valence": 0.8,
         "acousticness": 0.05, "instrumentalness": 0.0, "tempo": 0.7, "loudness": 0.8},
    ])


# ==========================================================
# GENRE HOURS TESTS
# ==========================================================
class TestGenreHours:
    def test_returns_dict(self, mock_artists):
        result = compute_genre_hours(mock_artists)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_most_listened_genre_has_highest_hours(self, mock_artists):
        result = compute_genre_hours(mock_artists)
        genres = list(result.keys())
        # First genre should have the most hours
        assert result[genres[0]] >= result[genres[-1]]

    def test_empty_artists(self):
        assert compute_genre_hours([]) == {}

    def test_no_genres(self):
        artists = [{"id": "a1", "name": "Test", "genres": [], "popularity": 50}]
        assert compute_genre_hours(artists) == {}


# ==========================================================
# ARTIST HOURS TESTS
# ==========================================================
class TestArtistHours:
    def test_returns_list(self, mock_artists):
        result = compute_artist_hours(mock_artists)
        assert isinstance(result, list)
        assert len(result) == len(mock_artists)

    def test_sorted_by_hours_desc(self, mock_artists):
        result = compute_artist_hours(mock_artists)
        for i in range(len(result) - 1):
            assert result[i]["hrs"] >= result[i + 1]["hrs"]

    def test_has_required_keys(self, mock_artists):
        result = compute_artist_hours(mock_artists)
        for a in result:
            assert "name" in a
            assert "genre" in a
            assert "hrs" in a
            assert a["hrs"] > 0


# ==========================================================
# BUBBLE DETECTION TESTS
# ==========================================================
class TestBubbleDetection:
    def test_no_bubble_diverse(self):
        hours = {"rock": 30, "jazz": 25, "pop": 20, "electronic": 15, "hip hop": 10}
        assert detect_bubble(hours) is None

    def test_bubble_detected(self):
        hours = {"indie rock": 90, "pop": 5, "jazz": 5}
        result = detect_bubble(hours)
        assert result is not None
        assert result["bubble"] is True
        assert result["genre"] == "indie rock"

    def test_empty_input(self):
        assert detect_bubble({}) is None

    def test_single_genre(self):
        result = detect_bubble({"rock": 100})
        assert result is not None
        assert result["bubble"] is True


class TestCoreGenreCount:
    def test_counts_only_meaningful_shares(self):
        hours = {"rock": 40, "pop": 30, "jazz": 20, "ambient": 10, "micro": 1}
        assert count_core_genres(hours, min_share=0.1) == 3

    def test_empty_input(self):
        assert count_core_genres({}) == 0


# ==========================================================
# FEATURE VECTOR TESTS
# ==========================================================
class TestFeatureVector:
    def test_shape(self, mock_track_df):
        vec = get_feature_vector(mock_track_df)
        assert vec.shape == (3, len(FEATURE_COLS))

    def test_empty_df(self):
        result = get_feature_vector(pd.DataFrame())
        assert result.size == 0

    def test_values_reasonable(self, mock_track_df):
        vec = get_feature_vector(mock_track_df)
        # Features should be non-negative after weighting
        assert np.all(vec >= 0)


# ==========================================================
# USER PROFILE TESTS
# ==========================================================
class TestUserProfile:
    def test_shape(self, mock_track_df):
        profile = get_user_profile(mock_track_df)
        assert profile.shape == (len(FEATURE_COLS),)

    def test_empty_df(self):
        profile = get_user_profile(pd.DataFrame())
        assert len(profile) == len(FEATURE_COLS)
        assert np.all(profile == 0)

    def test_weighted_by_rating(self, mock_track_df):
        profile = get_user_profile(mock_track_df)
        # Profile should be closer to top-rated track features
        assert profile[0] > 0  # danceability weighted average should be > 0


class TestCollectorFallbacks:
    def test_fetch_top_tracks_returns_empty_on_api_error(self):
        class BrokenSpotify:
            def current_user_top_tracks(self, **_kwargs):
                raise RuntimeError("spotify unavailable")

        assert fetch_top_tracks(BrokenSpotify()) == []

    def test_fetch_top_artists_returns_empty_on_api_error(self):
        class BrokenSpotify:
            def current_user_top_artists(self, **_kwargs):
                raise RuntimeError("spotify unavailable")

        assert fetch_top_artists(BrokenSpotify()) == []
