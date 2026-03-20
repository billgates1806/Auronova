# ==========================================================
# AURONOVA — Configuration
# Pydantic-based settings with environment variable loading
# ==========================================================

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT_DIR = _BACKEND_DIR.parent


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # ── Spotify OAuth ──
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8000/auth/callback"
    frontend_url: str = "http://127.0.0.1:3000"
    spotify_scopes: str = (
        "user-read-private "
        "user-read-email "
        "user-top-read "
        "user-read-recently-played "
        "user-library-read "
        "user-library-modify "
        "playlist-modify-public "
        "playlist-modify-private"
    )

    # ── Database ──
    database_url: str = f"sqlite:///{(_BACKEND_DIR / 'auronova.db').resolve()}"

    # ── Security ──
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60 * 24 * 7  # 1 week

    # ── Recommendation Weights ──
    weight_content: float = 0.40
    weight_ncf: float = 0.25
    weight_context: float = 0.20
    weight_discovery: float = 0.15

    # ── Audio Feature Weights (for content-based filtering) ──
    feat_energy: float = 1.5
    feat_valence: float = 1.3
    feat_danceability: float = 1.2
    feat_acousticness: float = 1.0
    feat_instrumentalness: float = 0.8
    feat_tempo: float = 0.6
    feat_loudness: float = 0.4

    # ── Discovery Dial ──
    bfs_max_depth: int = 4
    discovery_cache_ttl: int = 3600  # seconds

    # ── Bubble Detection ──
    bubble_genre_threshold: float = 0.80  # 80% in one genre cluster
    bubble_weeks_threshold: int = 2

    model_config = {
        "env_file": (
            str(_ROOT_DIR / ".env"),
            str(_BACKEND_DIR / ".env"),
            ".env",
        ),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """Cached singleton — returns the same Settings instance every call."""
    return Settings()
