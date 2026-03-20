# ==========================================================
# AURONOVA — API Integration Tests
# Tests all endpoints via FastAPI TestClient
# ==========================================================

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ==========================================================
# HEALTH CHECK
# ==========================================================
class TestHealthCheck:
    def test_health_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_body(self):
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "auronova"
        assert "version" in data


# ==========================================================
# AUTH ENDPOINTS
# ==========================================================
class TestAuth:
    def test_login_redirects(self):
        response = client.get("/auth/login", follow_redirects=False)
        # Should redirect to Spotify
        assert response.status_code in (302, 307)

    def test_callback_no_code(self):
        response = client.get("/auth/callback")
        # Missing code → 422 (validation error)
        assert response.status_code == 422


# ==========================================================
# PROTECTED ENDPOINTS (no auth → 422)
# ==========================================================
class TestProtectedEndpoints:
    def test_profile_requires_auth(self):
        response = client.get("/me/profile")
        assert response.status_code == 422

    def test_top_artists_requires_auth(self):
        response = client.get("/me/top-artists")
        assert response.status_code == 422

    def test_genre_hours_requires_auth(self):
        response = client.get("/me/genre-hours")
        assert response.status_code == 422

    def test_music_dna_requires_auth(self):
        response = client.get("/me/music-dna")
        assert response.status_code == 422

    def test_recommendations_requires_auth(self):
        response = client.get("/recommendations")
        assert response.status_code == 422

    def test_feedback_requires_auth(self):
        response = client.post("/feedback", json={"track_id": "t1", "action": "love"})
        assert response.status_code == 422

    def test_save_track_requires_auth(self):
        response = client.put("/spotify/save-track", json={"track_id": "t1"})
        assert response.status_code == 422

    def test_create_playlist_requires_auth(self):
        response = client.post("/spotify/create-playlist", json={
            "name": "Test", "track_ids": ["t1"]
        })
        assert response.status_code == 422


# ==========================================================
# DOCS ENDPOINT
# ==========================================================
class TestDocs:
    def test_openapi_schema(self):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "paths" in schema
        # Verify all expected paths exist
        paths = list(schema["paths"].keys())
        assert "/health" in paths
        assert "/auth/login" in paths
        assert "/auth/callback" in paths

    def test_docs_page(self):
        response = client.get("/docs")
        assert response.status_code == 200
