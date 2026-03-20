# ==========================================================
# AURONOVA — Discovery Engine (BFS on Artist Graph)
# Controls how far outside the user's comfort zone recs go
# ==========================================================

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

import spotipy

from app.config import get_settings

logger = logging.getLogger(__name__)
_s = get_settings()


class DiscoveryEngine:
    """
    BFS-based artist discovery using Spotify's related-artists API.

    The Discovery Dial maps 0-100% to BFS depth:
    - 0-25%   → 0-1 hops (same artists + immediate relations)
    - 25-50%  → 1-2 hops (related artists)
    - 50-75%  → 2-3 hops (2nd degree relations)
    - 75-100% → 3-4 hops (frontier — far-out discoveries)
    """

    def __init__(self):
        self._graph: dict[str, list[str]] = {}      # artist_id → [related_artist_ids]
        self._artist_info: dict[str, dict] = {}      # artist_id → {name, genres, ...}
        self._depth_map: dict[str, int] = {}         # artist_id → BFS depth from seeds

    # ==========================================================
    # BUILD ARTIST GRAPH — BFS from seed artists
    # ==========================================================
    def build_graph(
        self,
        sp: spotipy.Spotify,
        seed_artist_ids: list[str],
        max_depth: int | None = None,
    ) -> int:
        """
        Build a graph of related artists via BFS.

        Args:
            sp: Authenticated Spotify client
            seed_artist_ids: User's top artist IDs (seeds)
            max_depth: Maximum BFS depth (default from config)

        Returns:
            Total number of artists discovered
        """
        if max_depth is None:
            max_depth = _s.bfs_max_depth

        queue: deque[tuple[str, int]] = deque()
        visited: set[str] = set()
        self._graph = {}
        self._artist_info = {}
        self._depth_map = {}

        if seed_artist_ids:
            try:
                for artist in sp.artists(seed_artist_ids[:50]).get("artists", []):
                    if not artist:
                        continue
                    self._artist_info[artist["id"]] = {
                        "id": artist["id"],
                        "name": artist["name"],
                        "genres": artist.get("genres", []),
                        "popularity": artist.get("popularity", 0),
                        "image_url": artist["images"][0]["url"] if artist.get("images") else None,
                    }
            except Exception as e:
                logger.warning("Failed to fetch seed artist metadata: %s", e)

        # Initialize seeds at depth 0
        for aid in seed_artist_ids:
            queue.append((aid, 0))
            visited.add(aid)
            self._depth_map[aid] = 0
            self._graph.setdefault(aid, [])

        while queue:
            artist_id, depth = queue.popleft()

            if depth >= max_depth:
                continue

            try:
                related = sp.artist_related_artists(artist_id)
                related_ids = []
                for r in related.get("artists", [])[:10]:  # limit to top 10 related
                    rid = r["id"]
                    related_ids.append(rid)

                    # Store artist info
                    if rid not in self._artist_info:
                        self._artist_info[rid] = {
                            "id": rid,
                            "name": r["name"],
                            "genres": r.get("genres", []),
                            "popularity": r.get("popularity", 0),
                            "image_url": r["images"][0]["url"] if r.get("images") else None,
                        }

                    # BFS expand
                    if rid not in visited:
                        visited.add(rid)
                        self._depth_map[rid] = depth + 1
                        queue.append((rid, depth + 1))

                self._graph[artist_id] = related_ids

            except Exception as e:
                is_forbidden = getattr(e, "http_status", 0) == 403
                if isinstance(e, spotipy.SpotifyException) and is_forbidden:
                    logger.warning(f"Related artists endpoint is forbidden (403). Falling back to top track artists.")
                else:
                    logger.warning(f"Related artists failed for {artist_id}: {e}")
                
                # Fallback: Use artists from top tracks
                try:
                    top_tracks = sp.artist_top_tracks(artist_id, country="US")
                    related_artists_fallback = []
                    for t in top_tracks.get("tracks", []):
                        for a in t.get("artists", []):
                            if a["id"] != artist_id and a["id"] not in [r["id"] for r in related_artists_fallback]:
                                # Fetch full artist info to get images/genres
                                try:
                                    full_a = sp.artist(a["id"])
                                    related_artists_fallback.append(full_a)
                                except Exception:
                                    pass
                            if len(related_artists_fallback) >= 5:
                                break
                        if len(related_artists_fallback) >= 5:
                            break
                    
                    related_ids = []
                    for r in related_artists_fallback:
                        rid = r["id"]
                        related_ids.append(rid)

                        if rid not in self._artist_info:
                            self._artist_info[rid] = {
                                "id": rid,
                                "name": r["name"],
                                "genres": r.get("genres", []),
                                "popularity": r.get("popularity", 0),
                                "image_url": r["images"][0]["url"] if r.get("images") else None,
                            }

                        if rid not in visited:
                            visited.add(rid)
                            self._depth_map[rid] = depth + 1
                            queue.append((rid, depth + 1))
                            
                    self._graph[artist_id] = related_ids
                except Exception as fallback_e:
                    logger.warning(f"Fallback for {artist_id} failed: {fallback_e}")
                    if is_forbidden:
                        break # Stop discovery entirely on 403 if fallback also fails

        logger.info(f"Artist graph built: {len(visited)} artists, max_depth={max_depth}")
        return len(visited)

    # ==========================================================
    # GET ARTISTS AT DEPTH — dial → BFS depth mapping
    # ==========================================================
    def get_artists_at_dial(self, dial: int) -> list[dict]:
        """
        Map the discovery dial (0-100%) to BFS depth and return
        artists at the appropriate depth.

        Returns list of artist info dicts at the target depth.
        """
        # Map dial to target depth range
        if dial <= 25:
            min_depth, max_depth = 0, 1
        elif dial <= 50:
            min_depth, max_depth = 1, 2
        elif dial <= 75:
            min_depth, max_depth = 2, 3
        else:
            min_depth, max_depth = 3, _s.bfs_max_depth

        artists = []
        for aid, depth in self._depth_map.items():
            if min_depth <= depth <= max_depth and aid in self._artist_info:
                artists.append({**self._artist_info[aid], "discovery_depth": depth})

        return artists

    # ==========================================================
    # GET DISCOVERY TRACKS — fetch top tracks from discovered artists
    # ==========================================================
    def get_discovery_tracks(
        self,
        sp: spotipy.Spotify,
        dial: int,
        limit: int = 30,
    ) -> list[dict]:
        """
        Get track recommendations from artists at the dial's depth.

        Fetches top tracks for discovered artists and returns them
        as recommendation candidates.
        """
        artists = self.get_artists_at_dial(dial)
        if not artists:
            return []

        tracks = []
        max_depth = max(_s.bfs_max_depth, 1)
        target_depth = max(1, round((dial / 100) * max_depth))
        sampled = sorted(
            artists,
            key=lambda artist: (
                abs(artist.get("discovery_depth", max_depth) - target_depth),
                -artist.get("popularity", 0) if dial < 50 else artist.get("popularity", 0),
            ),
        )[: min(len(artists), 12)]

        for artist in sampled:
            try:
                results = sp.artist_top_tracks(artist["id"], country="US")
                for t in results.get("tracks", [])[:3]:  # top 3 per artist
                    artists_info = t.get("artists", [])
                    album = t.get("album", {})
                    tracks.append({
                        "track_id": t["id"],
                        "name": t["name"],
                        "artist": artists_info[0]["name"] if artists_info else artist["name"],
                        "artist_id": artist["id"],
                        "album": album.get("name", ""),
                        "preview_url": t.get("preview_url"),
                        "genres": artist.get("genres", []),
                        "popularity": t.get("popularity", 0),
                        "discovery_depth": artist["discovery_depth"],
                        "why": f"{artist['discovery_depth']} hops · via {artist['name']}",
                        "source": "discovery",
                    })
            except Exception as e:
                logger.warning(f"Top tracks failed for {artist['id']}: {e}")

        return tracks[:limit]

    # ==========================================================
    # BLEND — merge content recs with discovery recs
    # ==========================================================
    @staticmethod
    def blend_recs(
        content_recs: list[dict],
        discovery_recs: list[dict],
        dial: int,
    ) -> list[dict]:
        """
        Blend content-based and discovery recommendations.

        The dial controls the mix:
        - dial=0  → 100% content, 0% discovery
        - dial=50 → 50/50 mix
        - dial=100 → 20% content, 80% discovery
        """
        discovery_weight = dial / 100
        content_weight = 1 - discovery_weight * 0.8  # never fully remove content

        # Interleave based on weights
        n_content = int(len(content_recs) * content_weight)
        n_discovery = len(content_recs) - n_content

        blended = content_recs[:n_content] + discovery_recs[:n_discovery]

        # Mark source for transparency
        for r in blended:
            if "source" not in r:
                r["source"] = "blend"

        return blended
