# ==========================================================
# AURONOVA — Database Models & Connection
# SQLAlchemy ORM with 6 tables for users, tracks, features,
# listening sessions, and feedback
# ==========================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

from app.config import get_settings

# ── Engine & Session ──────────────────────────────────────
_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in _settings.database_url else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


# ==========================================================
# TABLE: users
# Stores Spotify user info + OAuth tokens
# ==========================================================
class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)  # Spotify user ID
    display_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user_tracks = relationship("UserTrack", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("ListeningSession", back_populates="user", cascade="all, delete-orphan")
    feedback = relationship("Feedback", back_populates="user", cascade="all, delete-orphan")


# ==========================================================
# TABLE: tracks
# Canonical track information from Spotify
# ==========================================================
class Track(Base):
    __tablename__ = "tracks"

    id = Column(String, primary_key=True)  # Spotify track ID
    name = Column(String, nullable=False)
    artist = Column(String, nullable=False)
    artist_id = Column(String, nullable=True)
    album = Column(String, nullable=True)
    album_art_url = Column(String, nullable=True)
    preview_url = Column(String, nullable=True)
    popularity = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    genres = Column(Text, nullable=True)  # JSON array of genre strings
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    audio_features = relationship("AudioFeatures", back_populates="track", uselist=False, cascade="all, delete-orphan")
    user_tracks = relationship("UserTrack", back_populates="track")


# ==========================================================
# TABLE: audio_features
# Spotify's 13 audio features per track
# ==========================================================
class AudioFeatures(Base):
    __tablename__ = "audio_features"

    track_id = Column(String, ForeignKey("tracks.id"), primary_key=True)
    danceability = Column(Float, default=0.0)
    energy = Column(Float, default=0.0)
    key = Column(Integer, default=0)
    loudness = Column(Float, default=0.0)
    mode = Column(Integer, default=0)
    speechiness = Column(Float, default=0.0)
    acousticness = Column(Float, default=0.0)
    instrumentalness = Column(Float, default=0.0)
    liveness = Column(Float, default=0.0)
    valence = Column(Float, default=0.0)
    tempo = Column(Float, default=0.0)
    time_signature = Column(Integer, default=4)
    duration_ms = Column(Integer, default=0)

    track = relationship("Track", back_populates="audio_features")


# ==========================================================
# TABLE: user_tracks
# Links users to their tracks (top, saved, recent)
# ==========================================================
class UserTrack(Base):
    __tablename__ = "user_tracks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    track_id = Column(String, ForeignKey("tracks.id"), nullable=False)
    source = Column(String, nullable=False)  # "top_short", "top_medium", "top_long", "saved", "recent"
    implicit_rating = Column(Float, default=1.0)  # derived from source + play count
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="user_tracks")
    track = relationship("Track", back_populates="user_tracks")


# ==========================================================
# TABLE: listening_sessions
# Mood-context logs for the context engine
# ==========================================================
class ListeningSession(Base):
    __tablename__ = "listening_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    time_bucket = Column(String, nullable=False)  # morning, afternoon, evening, night
    mood = Column(String, nullable=False)  # energetic, chill, focused, melancholy
    genres_played = Column(Text, nullable=True)  # JSON: {"indie rock": 5, "jazz": 2}
    track_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="sessions")


# ==========================================================
# TABLE: feedback
# User's love/skip signals for the feedback loop
# ==========================================================
class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    track_id = Column(String, nullable=False)
    action = Column(String, nullable=False)  # "love" or "skip"
    mood = Column(String, nullable=True)
    discovery_level = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="feedback")
