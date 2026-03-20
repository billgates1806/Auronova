# ==========================================================
# AURONOVA — API Routes
# 10 endpoints for dashboard, recommendations, DNA,
# feedback, and Spotify library actions
# ==========================================================

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.routes import get_current_user
from app.auth.spotify_auth import get_spotify_client
from app.data.collector import fetch_top_artists, fetch_top_tracks, sync_user_data
from app.data.database import Feedback, User, get_db
from app.data.processor import compute_artist_hours, compute_genre_hours, count_core_genres, detect_bubble
from app.models.ensemble import EnsembleRecommender

logger = logging.getLogger(__name__)
router = APIRouter(tags=["api"])

# ── Shared recommender instance ──
_recommenders: dict[str, EnsembleRecommender] = {}


def _invalidate_recommender(user_id: str) -> None:
    """Drop cached recommender state for a user after a full sync."""
    _recommenders.pop(user_id, None)


def _get_recommender(user: User, db: Session) -> EnsembleRecommender:
    """Get or create a recommender for this user."""
    cached = _recommenders.get(user.id)
    if cached is None or not cached._initialized:
        rec = EnsembleRecommender()
        sp = get_spotify_client(user.access_token)
        rec.initialize(sp, user.id, db)
        _recommenders[user.id] = rec
    return _recommenders[user.id]


def _get_artists_from_tracks(sp, limit=50):
    """Fallback: derive top artists from top tracks if top artists API returns empty."""
    tracks = fetch_top_tracks(sp, "short_term", limit=limit)
    if not tracks:
        return []
    
    # Count artist frequencies and store names
    artist_counts = {}
    artist_names = {}
    for t in tracks:
        aid = t.get("artist_id")
        if aid:
            artist_counts[aid] = artist_counts.get(aid, 0) + 1
            if aid not in artist_names:
                artist_names[aid] = t.get("artist", "Unknown Artist")
            
    # Sort by frequency and take top ones
    top_aids = sorted(artist_counts.keys(), key=lambda k: artist_counts[k], reverse=True)[:limit]
    
    # Fetch full artist info from Spotify to get genres and images
    artists = []
    fallback_needed = False
    
    for i in range(0, len(top_aids), 50):
        batch = top_aids[i:i+50]
        try:
            results = sp.artists(batch)
            for item in results.get("artists", []):
                if not item: continue
                # API sometimes returns empty genres array for lesser known artists. 
                # Provide a fallback so compute_genre_hours doesn't return empty.
                genres = item.get("genres", [])
                if not genres:
                    genres = ["Pop"] # generic fallback so math doesn't crash to 0

                pop = item.get("popularity")
                if not pop or pop <= 0:
                    pop = 50

                artists.append({
                    "id": item["id"],
                    "name": item["name"],
                    "genres": genres,
                    "popularity": pop,
                    "image_url": item["images"][0]["url"] if item.get("images") else None,
                    "spotify_url": item.get("external_urls", {}).get("spotify"),
                })
        except Exception as e:
            logger.warning(f"Failed to fetch artist details for fallback: {e}")
            fallback_needed = True
            
    if fallback_needed and not artists:
        for aid in top_aids:
            artists.append({
                "id": aid,
                "name": artist_names.get(aid, "Unknown"),
                "genres": ["Pop"],
                "popularity": 50,
                "image_url": None,
                "spotify_url": None,
            })
            
    return artists


# ── Request / Response Models ────────────────────────────
class FeedbackRequest(BaseModel):
    track_id: str
    action: str  # "love" or "skip"
    mood: Optional[str] = None
    discovery_level: Optional[int] = None


class PlaylistRequest(BaseModel):
    name: str = "Auronova Mix"
    description: str = "Curated by Auronova"
    track_ids: list[str]


class SaveTrackRequest(BaseModel):
    track_id: str


# ==========================================================
# GET /me/profile
# Returns user profile info
# ==========================================================
@router.get("/me/profile")
async def get_profile(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """Get the current user's profile."""
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)

    return {
        "id": user.id,
        "display_name": user.display_name,
        "email": user.email,
        "image_url": user.image_url,
    }


# ==========================================================
# GET /me/top-artists
# Returns top 5 artists with estimated hours
# ==========================================================
@router.get("/me/top-artists")
async def get_top_artists(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """Get the user's top 5 artists with listening hours."""
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)
    sp = get_spotify_client(user.access_token)

    raw = fetch_top_artists(sp, "medium_term", 50)
    if not raw:
        raw = _get_artists_from_tracks(sp, 50)
        
    artists = compute_artist_hours(raw)

    return {
        "artists": artists[:5],
        "artist_count": len({
            artist.get("id") or artist.get("name")
            for artist in raw
            if artist.get("id") or artist.get("name")
        }),
    }


# ==========================================================
# GET /me/genre-hours
# Returns genre breakdown by estimated hours
# ==========================================================
@router.get("/me/genre-hours")
async def get_genre_hours(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """Get genre breakdown with estimated listening hours."""
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)
    sp = get_spotify_client(user.access_token)

    raw = fetch_top_artists(sp, "medium_term", 50)
    if not raw:
        raw = _get_artists_from_tracks(sp, 50)
        
    genre_hours = compute_genre_hours(raw)
    bubble = detect_bubble(genre_hours)
    total_hours = round(sum(genre_hours.values()), 1)

    return {
        "genres": genre_hours,
        "bubble": bubble,
        "total_hours": total_hours,
        "core_genre_count": count_core_genres(genre_hours),
        "hours_basis": "live_spotify_estimate",
    }


# ==========================================================
# GET /me/music-dna
# Returns t-SNE coordinates for the Music DNA visualization
# ==========================================================
@router.get("/me/music-dna")
async def get_music_dna(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """Get Music DNA visualization data (t-SNE + clusters)."""
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)

    recommender = _get_recommender(user, db)
    dna_data = recommender.get_dna_data()

    return dna_data


# ==========================================================
# GET /recommendations
# Main recommendation endpoint with mood + discovery dial
# ==========================================================
@router.get("/recommendations")
async def get_recommendations(
    mood: str = Query("chill", description="Mood: energetic, chill, focused, melancholy"),
    discovery: int = Query(35, ge=0, le=100, description="Discovery dial 0-100"),
    n: int = Query(20, ge=1, le=50, description="Number of recommendations"),
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """
    Get personalized recommendations.

    Blends content-based, NCF, context, and discovery engines
    based on the current mood and discovery dial setting.
    """
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)
    sp = get_spotify_client(user.access_token)

    recommender = _get_recommender(user, db)
    recs = recommender.recommend(sp, user.id, mood=mood, dial=discovery, n=n)

    return {
        "recommendations": recs,
        "mood": mood,
        "discovery": discovery,
        "count": len(recs),
    }


# ==========================================================
# POST /feedback
# Records user's love/skip signals for feedback loop
# ==========================================================
@router.post("/feedback")
async def post_feedback(
    body: FeedbackRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """Record a love/skip feedback signal."""
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)

    if body.action not in ("love", "skip"):
        raise HTTPException(status_code=400, detail="Action must be 'love' or 'skip'")

    fb = Feedback(
        user_id=user.id,
        track_id=body.track_id,
        action=body.action,
        mood=body.mood,
        discovery_level=body.discovery_level,
    )
    db.add(fb)
    db.commit()

    return {"status": "ok", "action": body.action, "track_id": body.track_id}


# ==========================================================
# POST /spotify/sync
# Fully synchronize user listening history from Spotify
# ==========================================================
@router.post("/spotify/sync")
async def sync_spotify_data(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """Sync all user data (artists, tracks, features) from Spotify."""
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)
    sp = get_spotify_client(user.access_token)

    try:
        _invalidate_recommender(user.id)
        counts = sync_user_data(sp, user.id, db)
        _invalidate_recommender(user.id)
        return {"status": "success", "synced": counts}
    except Exception as e:
        logger.error(f"Sync failed for user {user.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to sync Spotify data")


# ==========================================================
# PUT /spotify/save-track
# Saves a track to user's Liked Songs on Spotify
# ==========================================================
@router.put("/spotify/save-track")
async def save_track(
    body: SaveTrackRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """Save a track to user's Spotify Liked Songs."""
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)
    sp = get_spotify_client(user.access_token)

    try:
        sp.current_user_saved_tracks_add([body.track_id])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Spotify API error: {e}")

    return {"status": "saved", "track_id": body.track_id}


# ==========================================================
# POST /spotify/create-playlist
# Creates a playlist and adds tracks
# ==========================================================
@router.post("/spotify/create-playlist")
async def create_playlist(
    body: PlaylistRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    """Create a Spotify playlist and add tracks to it."""
    token = authorization.replace("Bearer ", "")
    user = get_current_user(token, db)
    sp = get_spotify_client(user.access_token)

    try:
        # Create playlist
        playlist = sp.user_playlist_create(
            user=user.id,
            name=body.name,
            public=True,
            description=body.description,
        )

        # Add tracks
        track_uris = [f"spotify:track:{tid}" for tid in body.track_ids]
        if track_uris:
            sp.playlist_add_items(playlist["id"], track_uris)

    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Spotify API error: {e}")

    return {
        "status": "created",
        "playlist_id": playlist["id"],
        "playlist_url": playlist.get("external_urls", {}).get("spotify", ""),
        "tracks_added": len(body.track_ids),
    }
