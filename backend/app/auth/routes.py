# ==========================================================
# AURONOVA — Auth Routes
# /login  → redirect to Spotify OAuth
# /callback → exchange code, store user, return JWT
# ==========================================================

from __future__ import annotations

import logging
from urllib.parse import urlencode, urlparse
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from jose import jwt
from sqlalchemy.orm import Session

from app.auth.spotify_auth import (
    exchange_code,
    get_auth_url,
    get_spotify_client,
    is_token_expired,
    refresh_access_token,
)
from app.config import get_settings
from app.data.database import User, get_db

router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)
_s = get_settings()
_ALLOWED_FRONTEND_ORIGINS = {
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
}


def _sanitize_frontend_origin(frontend_origin: Optional[str]) -> str:
    """Allow only known local frontend origins for OAuth redirection."""
    if not frontend_origin:
        return _s.frontend_url.rstrip("/")

    parsed = urlparse(frontend_origin)
    if parsed.scheme not in {"http", "https"}:
        return _s.frontend_url.rstrip("/")

    normalized = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return normalized if normalized in _ALLOWED_FRONTEND_ORIGINS else _s.frontend_url.rstrip("/")


def _frontend_redirect(frontend_url: str, **params: str) -> RedirectResponse:
    """Redirect back to the frontend with query params."""
    query = urlencode({key: value for key, value in params.items() if value})
    url = f"{frontend_url}/"
    if query:
        url = f"{url}?{query}"
    return RedirectResponse(url=url)


# ── Helper: Create JWT ────────────────────────────────────
def _create_jwt(user_id: str) -> str:
    """Create a signed JWT containing the Spotify user ID."""
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_s.jwt_expiry_minutes),
    }
    return jwt.encode(payload, _s.secret_key, algorithm=_s.jwt_algorithm)


# ── Helper: Decode JWT ────────────────────────────────────
def decode_jwt(token: str) -> str:
    """Decode a JWT and return the user_id (sub claim)."""
    try:
        payload = jwt.decode(token, _s.secret_key, algorithms=[_s.jwt_algorithm])
        user_id: str = payload.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ── Helper: Get current user (with auto token refresh) ────
def get_current_user(token: str, db: Session) -> User:
    """
    Resolve a JWT to a User ORM object.
    Automatically refreshes the Spotify access token if expired.
    """
    user_id = decode_jwt(token)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Auto-refresh if Spotify token expired
    if is_token_expired(user.token_expires_at):
        try:
            refreshed = refresh_access_token(user.refresh_token)
            user.access_token = refreshed["access_token"]
            user.token_expires_at = refreshed["expires_at"]
            user.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(user)
        except Exception as e:
            logger.error(f"Failed to refresh token for user {user_id}: {e}")
            # Even if refresh fails, return user, let the Spotify API calls fail with 401 later if it really is expired
            pass

    return user


# ==========================================================
# GET /auth/login
# Redirects the user to Spotify's authorization page
# ==========================================================
@router.get("/login")
async def login(frontend_origin: Optional[str] = Query(default=None)):
    """Redirect user to Spotify login."""
    frontend_url = _sanitize_frontend_origin(frontend_origin)
    if not _s.spotify_client_id or not _s.spotify_client_secret:
        return _frontend_redirect(
            frontend_url,
            auth_error="Spotify credentials are missing on the backend.",
        )

    url = get_auth_url(state=frontend_url)
    return RedirectResponse(url)


# ==========================================================
# GET /auth/callback
# Spotify redirects here with ?code=... after user approves
# ==========================================================
@router.get("/callback")
async def callback(
    code: str = Query(...),
    state: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Exchange the authorization code for tokens, create/update
    the user in the database, and return a JWT.
    """
    # Exchange code for tokens
    try:
        tokens = exchange_code(code)
    except Exception as e:
        frontend_url = _sanitize_frontend_origin(state)
        logger.error("Spotify token exchange failed: %s", e)
        return _frontend_redirect(frontend_url, auth_error="Spotify token exchange failed.")

    # Fetch user profile from Spotify
    sp = get_spotify_client(tokens["access_token"])
    try:
        profile = sp.current_user()
    except Exception as e:
        frontend_url = _sanitize_frontend_origin(state)
        logger.error("Spotify profile fetch failed: %s", e)
        return _frontend_redirect(frontend_url, auth_error="Spotify profile fetch failed.")

    user_id = profile["id"]
    display_name = profile.get("display_name", "")
    email = profile.get("email", "")
    images = profile.get("images", [])
    image_url = images[0]["url"] if images else None

    # Upsert user
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.access_token = tokens["access_token"]
        user.refresh_token = tokens["refresh_token"]
        user.token_expires_at = tokens["expires_at"]
        user.display_name = display_name
        user.email = email
        user.image_url = image_url
        user.updated_at = datetime.now(timezone.utc)
    else:
        user = User(
            id=user_id,
            display_name=display_name,
            email=email,
            image_url=image_url,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_expires_at=tokens["expires_at"],
        )
        db.add(user)

    db.commit()

    # Return Redirect to Frontend with JWT
    jwt_token = _create_jwt(user_id)
    frontend_url = _sanitize_frontend_origin(state)
    return _frontend_redirect(frontend_url, token=jwt_token)
