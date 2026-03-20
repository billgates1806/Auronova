# ==========================================================
# AURONOVA — Neural Collaborative Filtering (NCF)
# PyTorch model for deep learning recommendations
# ==========================================================

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

logger = logging.getLogger(__name__)


# ==========================================================
# DATASET — user-item interactions
# ==========================================================
class InteractionDataset(Dataset):
    """Dataset of (user_idx, item_idx, rating) tuples."""

    def __init__(self, user_ids: np.ndarray, item_ids: np.ndarray, ratings: np.ndarray):
        self.users = torch.LongTensor(user_ids)
        self.items = torch.LongTensor(item_ids)
        self.ratings = torch.FloatTensor(ratings)

    def __len__(self):
        return len(self.ratings)

    def __getitem__(self, idx):
        return self.users[idx], self.items[idx], self.ratings[idx]


# ==========================================================
# NCF MODEL — Embedding + MLP
# ==========================================================
class NCFModel(nn.Module):
    """
    Neural Collaborative Filtering.

    Architecture:
    - User embedding (dim=32)
    - Item embedding (dim=32)
    - Concatenate → MLP [64, 32, 16] → sigmoid output
    """

    def __init__(self, n_users: int, n_items: int, embed_dim: int = 32):
        super().__init__()
        self.user_embed = nn.Embedding(n_users, embed_dim)
        self.item_embed = nn.Embedding(n_items, embed_dim)

        self.mlp = nn.Sequential(
            nn.Linear(embed_dim * 2, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )

        # Initialize weights
        nn.init.normal_(self.user_embed.weight, std=0.01)
        nn.init.normal_(self.item_embed.weight, std=0.01)

    def forward(self, user_ids: torch.Tensor, item_ids: torch.Tensor) -> torch.Tensor:
        u = self.user_embed(user_ids)
        i = self.item_embed(item_ids)
        x = torch.cat([u, i], dim=1)
        return self.mlp(x).squeeze()


# ==========================================================
# NCF TRAINER — handles training and prediction
# ==========================================================
class NCFTrainer:
    """
    Manages training and inference for the NCF model.

    Internally maps Spotify IDs to contiguous integer indices
    for embedding lookups.
    """

    def __init__(self):
        self.model: Optional[NCFModel] = None
        self._user_to_idx: dict[str, int] = {}
        self._item_to_idx: dict[str, int] = {}
        self._idx_to_item: dict[int, str] = {}
        self._trained = False
        self._device = torch.device("cpu")

    # ==========================================================
    # TRAIN — fit the NCF model on interaction data
    # ==========================================================
    def train(
        self,
        interactions: list[dict],
        epochs: int = 15,
        batch_size: int = 64,
        lr: float = 0.001,
    ) -> dict[str, float]:
        """
        Train the NCF model.

        Args:
            interactions: List of dicts with {user_id, track_id, rating}
            epochs: Training epochs
            batch_size: Batch size
            lr: Learning rate

        Returns:
            Dict with training metrics: final_loss, n_interactions
        """
        if len(interactions) < 10:
            logger.warning("Not enough interactions for NCF training (need >= 10)")
            return {"final_loss": 0, "n_interactions": len(interactions)}

        # ── Build ID mappings ──
        users = sorted(set(i["user_id"] for i in interactions))
        items = sorted(set(i["track_id"] for i in interactions))
        self._user_to_idx = {uid: idx for idx, uid in enumerate(users)}
        self._item_to_idx = {iid: idx for idx, iid in enumerate(items)}
        self._idx_to_item = {idx: iid for iid, idx in self._item_to_idx.items()}

        n_users = len(users)
        n_items = len(items)

        # ── Prepare tensors ──
        user_ids = np.array([self._user_to_idx[i["user_id"]] for i in interactions])
        item_ids = np.array([self._item_to_idx[i["track_id"]] for i in interactions])
        ratings = np.array([i["rating"] for i in interactions], dtype=np.float32)

        # Normalize ratings to 0-1
        if ratings.max() > 1:
            ratings = ratings / ratings.max()

        dataset = InteractionDataset(user_ids, item_ids, ratings)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

        # ── Model ──
        self.model = NCFModel(n_users, n_items).to(self._device)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        criterion = nn.MSELoss()

        # ── Train ──
        self.model.train()
        final_loss = 0.0

        for epoch in range(epochs):
            epoch_loss = 0.0
            for u, i, r in loader:
                u, i, r = u.to(self._device), i.to(self._device), r.to(self._device)
                optimizer.zero_grad()
                pred = self.model(u, i)
                loss = criterion(pred, r)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()

            final_loss = epoch_loss / len(loader) if loader else 0
            if (epoch + 1) % 5 == 0:
                logger.info(f"NCF Epoch {epoch+1}/{epochs}, Loss: {final_loss:.4f}")

        self._trained = True
        logger.info(f"NCF trained: {n_users} users, {n_items} items, loss={final_loss:.4f}")

        return {"final_loss": final_loss, "n_interactions": len(interactions)}

    # ==========================================================
    # PREDICT — score candidate tracks for a user
    # ==========================================================
    def predict(self, user_id: str, candidate_track_ids: list[str]) -> list[tuple[str, float]]:
        """
        Score candidate tracks for a user.

        Returns list of (track_id, score) sorted by score descending.
        """
        if not self._trained or self.model is None:
            return [(tid, 0.5) for tid in candidate_track_ids]

        if user_id not in self._user_to_idx:
            return [(tid, 0.5) for tid in candidate_track_ids]

        self.model.eval()
        user_idx = self._user_to_idx[user_id]

        scores = []
        with torch.no_grad():
            for tid in candidate_track_ids:
                if tid in self._item_to_idx:
                    item_idx = self._item_to_idx[tid]
                    u_t = torch.LongTensor([user_idx]).to(self._device)
                    i_t = torch.LongTensor([item_idx]).to(self._device)
                    score = self.model(u_t, i_t).item()
                else:
                    score = 0.5  # unknown item → neutral score
                scores.append((tid, score))

        scores.sort(key=lambda x: -x[1])
        return scores
