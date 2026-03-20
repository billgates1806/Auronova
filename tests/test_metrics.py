# ==========================================================
# AURONOVA — Industry Recommendation Metrics
# Precision@K, Recall@K, Hit Rate, Coverage, Diversity, Novelty
# ==========================================================

import numpy as np
import pytest


# ==========================================================
# METRIC FUNCTIONS
# ==========================================================

def precision_at_k(recommended: list[str], relevant: set[str], k: int = 10) -> float:
    """
    Precision@K: fraction of top-K recommendations that are relevant.
    P@K = |recommended ∩ relevant| / K
    """
    top_k = recommended[:k]
    hits = sum(1 for r in top_k if r in relevant)
    return hits / k if k > 0 else 0.0


def recall_at_k(recommended: list[str], relevant: set[str], k: int = 10) -> float:
    """
    Recall@K: fraction of relevant items appearing in top-K.
    R@K = |recommended ∩ relevant| / |relevant|
    """
    top_k = recommended[:k]
    hits = sum(1 for r in top_k if r in relevant)
    return hits / len(relevant) if relevant else 0.0


def hit_rate(user_recs: list[list[str]], user_relevants: list[set[str]], k: int = 10) -> float:
    """
    Hit Rate: fraction of users with at least 1 relevant item in top-K.
    HR = Σ(1 if P@K > 0 else 0) / n_users
    """
    hits = sum(1 for recs, rels in zip(user_recs, user_relevants) if precision_at_k(recs, rels, k) > 0)
    return hits / len(user_recs) if user_recs else 0.0


def coverage(recommended_items: set[str], catalog_size: int) -> float:
    """
    Coverage: fraction of the catalog represented in recommendations.
    Coverage = |unique recommended items| / |catalog|
    """
    return len(recommended_items) / catalog_size if catalog_size > 0 else 0.0


def diversity(feature_vectors: list[np.ndarray]) -> float:
    """
    Diversity: average pairwise cosine distance between recommended tracks.
    Higher = more diverse recommendations.
    D = (2 / n(n-1)) Σ (1 - cos_sim(i, j)) for all pairs i,j
    """
    n = len(feature_vectors)
    if n < 2:
        return 0.0

    total_dist = 0.0
    count = 0
    for i in range(n):
        for j in range(i + 1, n):
            vi, vj = feature_vectors[i], feature_vectors[j]
            norm_i, norm_j = np.linalg.norm(vi), np.linalg.norm(vj)
            if norm_i > 0 and norm_j > 0:
                sim = np.dot(vi, vj) / (norm_i * norm_j)
                total_dist += 1 - sim
                count += 1

    return total_dist / count if count > 0 else 0.0


def novelty(recommended_ids: list[str], popularity: dict[str, float], catalog_size: int) -> float:
    """
    Novelty: average inverse popularity of recommendations.
    Higher = recommending more obscure/niche tracks.
    N = (1/|recs|) Σ log2(catalog_size / popularity(i))
    """
    if not recommended_ids or catalog_size == 0:
        return 0.0

    scores = []
    for rid in recommended_ids:
        pop = popularity.get(rid, 1)
        pop = max(pop, 1)  # avoid log(0)
        scores.append(np.log2(catalog_size / pop))

    return float(np.mean(scores))


# ==========================================================
# METRIC REPORT — compute all metrics at once
# ==========================================================
def compute_metrics_report(
    recommended: list[str],
    relevant: set[str],
    feature_vectors: list[np.ndarray],
    popularity: dict[str, float],
    catalog_size: int,
    k: int = 10,
) -> dict[str, float]:
    """
    Compute a full metrics report for one user's recommendations.
    """
    return {
        "precision_at_k": round(precision_at_k(recommended, relevant, k), 4),
        "recall_at_k": round(recall_at_k(recommended, relevant, k), 4),
        "coverage": round(coverage(set(recommended), catalog_size), 4),
        "diversity": round(diversity(feature_vectors), 4),
        "novelty": round(novelty(recommended, popularity, catalog_size), 4),
        "k": k,
        "n_recommended": len(recommended),
        "n_relevant": len(relevant),
    }


# ==========================================================
# TESTS
# ==========================================================
class TestPrecisionAtK:
    def test_perfect_precision(self):
        recs = ["a", "b", "c", "d", "e"]
        relevant = {"a", "b", "c", "d", "e"}
        assert precision_at_k(recs, relevant, k=5) == 1.0

    def test_zero_precision(self):
        recs = ["x", "y", "z"]
        relevant = {"a", "b", "c"}
        assert precision_at_k(recs, relevant, k=3) == 0.0

    def test_partial_precision(self):
        recs = ["a", "x", "b", "y", "c"]
        relevant = {"a", "b", "c"}
        assert precision_at_k(recs, relevant, k=5) == 0.6

    def test_k_larger_than_recs(self):
        recs = ["a", "b"]
        relevant = {"a"}
        assert precision_at_k(recs, relevant, k=5) == 0.2


class TestRecallAtK:
    def test_perfect_recall(self):
        recs = ["a", "b", "c"]
        relevant = {"a", "b", "c"}
        assert recall_at_k(recs, relevant, k=3) == 1.0

    def test_partial_recall(self):
        recs = ["a", "x", "y"]
        relevant = {"a", "b", "c"}
        assert recall_at_k(recs, relevant, k=3) == pytest.approx(1 / 3)

    def test_empty_relevant(self):
        recs = ["a", "b"]
        assert recall_at_k(recs, set(), k=2) == 0.0


class TestHitRate:
    def test_all_hits(self):
        user_recs = [["a", "b"], ["c", "d"]]
        user_rels = [{"a"}, {"c"}]
        assert hit_rate(user_recs, user_rels, k=2) == 1.0

    def test_no_hits(self):
        user_recs = [["x", "y"], ["x", "y"]]
        user_rels = [{"a"}, {"b"}]
        assert hit_rate(user_recs, user_rels, k=2) == 0.0

    def test_half_hits(self):
        user_recs = [["a", "b"], ["x", "y"]]
        user_rels = [{"a"}, {"c"}]
        assert hit_rate(user_recs, user_rels, k=2) == 0.5


class TestCoverage:
    def test_full_coverage(self):
        assert coverage({"a", "b", "c"}, 3) == 1.0

    def test_partial_coverage(self):
        assert coverage({"a"}, 4) == 0.25

    def test_zero_catalog(self):
        assert coverage(set(), 0) == 0.0


class TestDiversity:
    def test_identical_vectors(self):
        vecs = [np.array([1, 0, 0]), np.array([1, 0, 0])]
        assert diversity(vecs) == 0.0

    def test_orthogonal_vectors(self):
        vecs = [np.array([1, 0, 0]), np.array([0, 1, 0])]
        assert diversity(vecs) == pytest.approx(1.0)

    def test_single_vector(self):
        assert diversity([np.array([1, 2, 3])]) == 0.0


class TestNovelty:
    def test_popular_items_low_novelty(self):
        recs = ["a", "b"]
        pop = {"a": 100, "b": 100}
        n1 = novelty(recs, pop, catalog_size=100)
        # All popular → low novelty (log2(100/100) = 0)
        assert n1 == pytest.approx(0.0)

    def test_obscure_items_high_novelty(self):
        recs = ["a", "b"]
        pop = {"a": 1, "b": 1}
        n1 = novelty(recs, pop, catalog_size=1000)
        assert n1 > 5  # log2(1000/1) ≈ 10

    def test_empty_recs(self):
        assert novelty([], {}, 100) == 0.0


class TestMetricsReport:
    def test_full_report(self):
        recs = ["a", "b", "c", "d", "e"]
        relevant = {"a", "c", "e"}
        vectors = [np.random.rand(5) for _ in range(5)]
        pop = {r: i + 1 for i, r in enumerate(recs)}
        report = compute_metrics_report(recs, relevant, vectors, pop, catalog_size=100, k=5)

        assert "precision_at_k" in report
        assert "recall_at_k" in report
        assert "coverage" in report
        assert "diversity" in report
        assert "novelty" in report
        assert report["precision_at_k"] == 0.6
        assert report["recall_at_k"] == 1.0
