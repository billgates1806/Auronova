# ==========================================================
# AURONOVA — FastAPI Application Entry Point
# Wires up routers, CORS, startup events, and middleware
# ==========================================================

from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.routes import router as auth_router
from app.api.routes import router as api_router
from app.config import get_settings
from app.data.database import init_db

_settings = get_settings()
_ALLOWED_ORIGINS = sorted({
    _settings.frontend_url.rstrip("/"),
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
})

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize shared application resources before serving requests."""
    init_db()
    logging.getLogger(__name__).info("Auronova API started - database initialized")
    yield


# ==========================================================
# APP INITIALIZATION
# ==========================================================
app = FastAPI(
    title="Auronova",
    description=(
        "AI-powered music recommendations using Spotify listening history. "
        "Features mood-context engine, discovery dial, and Music DNA fingerprint."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ==========================================================
# CORS MIDDLEWARE
# Allow frontend to communicate with the API
# ==========================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# ROUTERS
# ==========================================================
app.include_router(auth_router, prefix="/auth")
app.include_router(api_router)


# ==========================================================
# HEALTH CHECK
# ==========================================================
@app.get("/health", tags=["system"])
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "auronova", "version": "1.0.0"}
