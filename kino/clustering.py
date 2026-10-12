"""Unsupervised grouping of unknown faces for the "Who is this?" page (CLAUDE.md §10, §11.3).

Pure compute, no DB or file access. Embeddings are L2-normalised float32 vectors, so the
euclidean distance HDBSCAN uses is monotonic in cosine distance (``|a-b|^2 = 2 - 2cos``).
Library choice: ``sklearn.cluster.HDBSCAN`` (scikit-learn ships wheels for amd64 and arm64;
the standalone ``hdbscan`` package has to be compiled).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from sklearn.cluster import HDBSCAN

NOISE = -1
MIN_CLUSTER_SIZE = 4
SPLIT_MIN_CLUSTER_SIZE = 3
MIN_CENTROID_COSINE = 0.55


def _run(embeddings: NDArray[np.float32], min_cluster_size: int, method: str) -> NDArray[np.intp]:
    n = len(embeddings)
    if n < max(min_cluster_size, 2):
        return np.full(n, NOISE, dtype=np.intp)
    model = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min(min_cluster_size, n - 1),
        metric="euclidean",
        cluster_selection_method=method,
        copy=True,
    )
    labels: NDArray[np.intp] = model.fit_predict(embeddings.astype(np.float64)).astype(np.intp)
    return _trim_outliers(embeddings, labels, min_cluster_size)


def _trim_outliers(
    embeddings: NDArray[np.float32], labels: NDArray[np.intp], min_cluster_size: int
) -> NDArray[np.intp]:
    """Send members far from their cluster centroid back to noise.

    HDBSCAN keeps the lowest-density fringe points in a cluster; for faces those are
    strangers, which must not reach the labeling page.
    """
    out = labels.copy()
    for lab in set(labels.tolist()) - {NOISE}:
        idx = np.flatnonzero(labels == lab)
        centroid = embeddings[idx].mean(axis=0)
        centroid /= max(float(np.linalg.norm(centroid)), 1e-12)
        far = embeddings[idx] @ centroid < MIN_CENTROID_COSINE
        out[idx[far]] = NOISE
        if len(idx) - int(far.sum()) < min_cluster_size:
            out[idx] = NOISE
    return _renumber(out)


def _renumber(labels: NDArray[np.intp]) -> NDArray[np.intp]:
    out = labels.copy()
    for new, old in enumerate(sorted(set(labels.tolist()) - {NOISE})):
        out[labels == old] = new
    return out


def cluster_embeddings(embeddings: NDArray[np.float32]) -> NDArray[np.intp]:
    """Cluster label per row (0..k-1); ``NOISE`` (-1) for faces that belong to no cluster."""
    return _run(embeddings, MIN_CLUSTER_SIZE, "eom")


def split_embeddings(embeddings: NDArray[np.float32]) -> NDArray[np.intp]:
    """Tighter re-clustering of one cluster: smaller minimum size and leaf selection, which
    returns the finest clusters in the hierarchy instead of the most stable (broad) ones."""
    return _run(embeddings, SPLIT_MIN_CLUSTER_SIZE, "leaf")


__all__ = [
    "MIN_CLUSTER_SIZE",
    "NOISE",
    "SPLIT_MIN_CLUSTER_SIZE",
    "cluster_embeddings",
    "split_embeddings",
]
