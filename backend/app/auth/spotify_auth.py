# ==========================================================
# AURONOVA — Spotify OAuth 2.0 Helpers
# Token exchange, refresh, and authenticated client creation
# ==========================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from app.config import get_settings

_s = get_settings()


# ── OAuth Object ──────────────────────────────────────────
def _get_oauth() -> SpotifyOAuth:
    """Return a configured SpotifyOAuth instance."""
    return SpotifyOAuth(
        client_id=_s.spotify_client_id,
        client_secret=_s.spotify_client_secret,
        redirect_uri=_s.spotify_redirect_uri,
        scope=_s.spotify_scopes,
        show_dialog=True,
    )


# ── Auth URL ──────────────────────────────────────────────
def get_auth_url(state: Optional[str] = None) -> str:
    """Generate the Spotify authorization URL for the user to visit."""
    oauth = _get_oauth()
    return oauth.get_authorize_url(state=state)


# ── Token Exchange ────────────────────────────────────────
def exchange_code(code: str) -> dict:
    """
    Exchange an authorization code for access + refresh tokens.

    Returns dict with keys:
        access_token, refresh_token, expires_at, token_type, scope
    """
    oauth = _get_oauth()
    token_info = oauth.get_access_token(code, as_dict=True, check_cache=False)
    return {
        "access_token": token_info["access_token"],
        "refresh_token": token_info["refresh_token"],
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=token_info.get("expires_in", 3600)),
    }


# ── Token Refresh ─────────────────────────────────────────
def refresh_access_token(refresh_token: str) -> dict:
    """
    Refresh an expired access token.

    Returns dict with:
        access_token, expires_at
    """
    oauth = _get_oauth()
    token_info = oauth.refresh_access_token(refresh_token)
    return {
        "access_token": token_info["access_token"],
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=token_info.get("expires_in", 3600)),
    }


# ── Spotify Client ────────────────────────────────────────
def get_spotify_client(access_token: str) -> spotipy.Spotify:
    """Return an authenticated Spotify client for API calls."""
    return spotipy.Spotify(auth=access_token, requests_timeout=10)


# ── Token Validity Check ─────────────────────────────────
def is_token_expired(expires_at: datetime | None) -> bool:
    """Check if the stored token has expired (with 5-min buffer)."""
    if expires_at is None:
        return True
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return now >= expires_at - timedelta(minutes=5)
