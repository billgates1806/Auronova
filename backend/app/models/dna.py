# ==========================================================
# AURONOVA — Music DNA (t-SNE Visualization)
# Projects tracks into 2D space for the crystalline fingerprint
# ==========================================================

from __future__ import annotations

import hashlib
import json
import logging

import numpy as np
import pandas as pd
from sklearn.manifold import TSNE
from sklearn.cluster import KMeans

from app.data.processor import get_feature_vector

logger = logging.getLogger(__name__)

# ── Cluster name templates ────────────────────────────────
REGION_TEMPLATES = [
    "{genre} Core",
    "{genre} Nights",
    "{genre} Drift",
    "Weekend {genre}",
    "Deep {genre}",
    "Late Night {genre}",
    "{genre} Zone",
    "{genre} Frontier",
]


# ==========================================================
# COMPUTE DNA — t-SNE reduction + clustering
# ==========================================================
def compute_dna(df: pd.DataFrame, n_clusters: int = 5) -> dict:
    """
    Compute the Music DNA visualization data.

    Pipeline:
    1. Extract weighted audio feature vectors
    2. t-SNE → 2D coordinates
    3. K-Means clustering → taste regions
    4. Label clusters by dominant genre

    Args:
        df: Track DataFrame with audio features and genres
        n_clusters: Number of taste regions

    Returns:
        Dict with:
        - points: [{x, y, track_id, name, artist, genre, color}]
        - regions: [{name, centroid_x, centroid_y, color, percentage}]
        - genre_breakdown: [{genre, hours, color}]
    """
    if df.empty or len(df) < 5:
        return {"points": [], "regions": [], "genre_breakdown": []}

    # ── Feature matrix ──
    features = get_feature_vector(df)
    if features.size == 0:
        return {"points": [], "regions": [], "genre_breakdown": []}

    # Add deterministic jitter so the same library yields a stable map.
    track_seed_material = "|".join(sorted(df["track_id"].astype(str).tolist()))
    seed = int(hashlib.md5(track_seed_material.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    features = features + rng.normal(0, 1e-4, features.shape)

    # ── t-SNE → 2D ──
    perplexity = min(30, max(3, len(df) // 3))
    tsne = TSNE(
        n_components=2,
        perplexity=perplexity,
        random_state=42,
        n_iter=800,
        learning_rate="auto",
        init="pca" if len(df) > 50 else "random",
    )
    coords_2d = tsne.fit_transform(features)

    # Normalize to 0-1 range
    x_min, x_max = coords_2d[:, 0].min(), coords_2d[:, 0].max()
    y_min, y_max = coords_2d[:, 1].min(), coords_2d[:, 1].max()
    x_range = max(x_max - x_min, 1e-6)
    y_range = max(y_max - y_min, 1e-6)
    coords_2d[:, 0] = (coords_2d[:, 0] - x_min) / x_range
    coords_2d[:, 1] = (coords_2d[:, 1] - y_min) / y_range

    # ── K-Means clustering ──
    n_clusters = min(n_clusters, len(df))
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(features)

    # ── Assign colors ──
    palette = ["#6B8F71", "#8B6F5E", "#9B9A5B", "#7B9E87", "#C4A882", "#6B7B5A", "#9B8B6B", "#A89880"]

    # ── Build points ──
    points = []
    for i, (_, row) in enumerate(df.iterrows()):
        genres_raw = row.get("genres", "[]")
        try:
            genres = json.loads(genres_raw) if isinstance(genres_raw, str) else genres_raw
        except Exception:
            genres = []
        primary_genre = genres[0] if genres else "Unknown"

        points.append({
            "x": float(coords_2d[i, 0]),
            "y": float(coords_2d[i, 1]),
            "track_id": row.get("track_id", ""),
            "name": row.get("name", ""),
            "artist": row.get("artist", ""),
            "genre": primary_genre,
            "cluster": int(labels[i]),
            "color": palette[int(labels[i]) % len(palette)],
        })

    # ── Build regions (label clusters by dominant genre) ──
    regions = []
    cluster_counts = np.bincount(labels, minlength=n_clusters)
    total_tracks = len(df)

    for c in range(n_clusters):
        cluster_points = [p for p in points if p["cluster"] == c]
        if not cluster_points:
            continue

        # Find dominant genre in cluster
        genre_counts: dict[str, int] = {}
        for p in cluster_points:
            g = p["genre"]
            genre_counts[g] = genre_counts.get(g, 0) + 1

        dominant_genre = max(genre_counts, key=genre_counts.get)  # type: ignore
        template = REGION_TEMPLATES[c % len(REGION_TEMPLATES)]

        cx = float(np.mean([p["x"] for p in cluster_points]))
        cy = float(np.mean([p["y"] for p in cluster_points]))
        pct = round(cluster_counts[c] / total_tracks * 100, 1)

        regions.append({
            "name": template.format(genre=dominant_genre.title()),
            "centroid_x": cx,
            "centroid_y": cy,
            "color": palette[c % len(palette)],
            "percentage": pct,
        })

    # Sort regions by percentage
    regions.sort(key=lambda r: -r["percentage"])

    return {"points": points, "regions": regions}


# ==========================================================
# COMPUTE GENRE BREAKDOWN FOR DNA CARD
# ==========================================================
def compute_genre_breakdown(df: pd.DataFrame) -> list[dict]:
    """
    Compute genre distribution for the DNA visualization card.

    Returns list of dicts: [{genre, count, percentage, color}]
    """
    palette = ["#6B8F71", "#8B6F5E", "#9B9A5B", "#7B9E87", "#C4A882", "#6B7B5A", "#9B8B6B", "#A89880"]
    genre_counts: dict[str, int] = {}

    for _, row in df.iterrows():
        genres_raw = row.get("genres", "[]")
        try:
            genres = json.loads(genres_raw) if isinstance(genres_raw, str) else genres_raw
        except Exception:
            genres = []
        for g in genres:
            genre_counts[g] = genre_counts.get(g, 0) + 1

    total = sum(genre_counts.values()) or 1
    sorted_genres = sorted(genre_counts.items(), key=lambda x: -x[1])

    return [
        {
            "genre": g,
            "count": c,
            "percentage": round(c / total * 100, 1),
            "color": palette[i % len(palette)],
        }
        for i, (g, c) in enumerate(sorted_genres[:10])
    ]
