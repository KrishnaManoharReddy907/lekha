"""Optimal one-to-one assignment (the Hungarian / Kuhn-Munkres problem).

A drop-in for `scipy.optimize.linear_sum_assignment`, so the API does not need SciPy at run time.
That import alone costs ~50 MB of RAM, a tenth of the free host's memory, for one function.
Shortest-augmenting-path form with potentials, O(n^2 m), vectorised with NumPy over columns.
Exact: tests cross-check the optimal cost against SciPy on thousands of random matrices.
"""

from __future__ import annotations

import numpy as np


def linear_sum_assignment(cost) -> tuple[np.ndarray, np.ndarray]:
    """Minimum-cost matching of rows to columns of a (possibly rectangular) cost matrix.

    Returns (row_indices, col_indices) sorted by row, min(n_rows, n_cols) pairs, like SciPy.
    """
    a = np.asarray(cost, dtype=float)
    if a.ndim != 2:
        raise ValueError("cost matrix must be 2-D")
    if not np.all(np.isfinite(a)):
        raise ValueError("cost matrix contains non-finite values")
    if a.shape[0] > a.shape[1]:
        cols, rows = _solve(a.T)
        order = np.argsort(rows)
        return rows[order], cols[order]
    return _solve(a)


def _solve(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n, m = a.shape  # n <= m
    if n == 0:
        return np.empty(0, dtype=np.intp), np.empty(0, dtype=np.intp)
    inf = np.inf
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=np.intp)  # p[j] = row (1-based) assigned to column j; column 0 is a sentinel
    way = np.zeros(m + 1, dtype=np.intp)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, inf)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            free = ~used[1:]
            cur = a[i0 - 1] - u[i0] - v[1:]
            better = free & (cur < minv[1:])
            minv[1:][better] = cur[better]
            way[1:][better] = j0
            cand = np.where(free, minv[1:], inf)
            j1 = int(np.argmin(cand)) + 1
            delta = cand[j1 - 1]
            u[p[used]] += delta
            v[used] -= delta
            minv[~used] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    cols = np.nonzero(p[1:])[0]
    rows = p[1:][cols] - 1
    order = np.argsort(rows)
    return rows[order].astype(np.intp), cols[order].astype(np.intp)
