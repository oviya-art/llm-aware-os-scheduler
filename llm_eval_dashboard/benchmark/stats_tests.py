"""Statistical comparison of the novel scheduler against each baseline (paired by workload seed)."""
from __future__ import annotations

import math
import random
import statistics as st
from typing import Any, Dict, List, Sequence

import numpy as np
from scipy import stats as sps


def mean_ci(xs: Sequence[float], conf: float = 0.95) -> Dict[str, float]:
    xs = [float(x) for x in xs]
    n = len(xs)
    m = st.mean(xs) if xs else 0.0
    if n < 2:
        return {"mean": m, "sd": 0.0, "lo": m, "hi": m, "n": n}
    sd = st.stdev(xs)
    h = sps.t.ppf(0.5 + conf / 2, n - 1) * sd / math.sqrt(n)
    return {"mean": m, "sd": sd, "lo": m - h, "hi": m + h, "n": n}


def bootstrap_ci(diffs: Sequence[float], iters: int = 4000, seed: int = 7, conf: float = 0.95):
    d = np.asarray(diffs, dtype=float)
    if len(d) < 2:
        v = float(d.mean()) if len(d) else 0.0
        return v, v
    rng = np.random.default_rng(seed)
    means = rng.choice(d, size=(iters, len(d)), replace=True).mean(axis=1)
    a = (1 - conf) / 2
    return float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def paired_compare(a: Sequence[float], b: Sequence[float], higher_better: bool) -> Dict[str, Any]:
    """Compare scheduler A (novel) with B (baseline) on paired samples.

    diff = a - b.  `improvement` is signed so that positive always means "A is better".
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    d = a - b
    imp = d if higher_better else -d
    out: Dict[str, Any] = {
        "n": int(len(d)),
        "mean_a": float(a.mean()), "mean_b": float(b.mean()),
        "mean_diff": float(d.mean()),
        "rel_improvement_pct": float(100.0 * imp.mean() / abs(b.mean())) if abs(b.mean()) > 1e-12 else None,
        "wins": int((imp > 1e-9).sum()), "losses": int((imp < -1e-9).sum()), "ties": int((np.abs(imp) <= 1e-9).sum()),
    }
    lo, hi = bootstrap_ci(imp)
    out["improvement_ci95"] = [lo, hi]
    sd = float(imp.std(ddof=1)) if len(imp) > 1 else 0.0
    out["cohens_dz"] = float(imp.mean() / sd) if sd > 1e-12 else (0.0 if abs(imp.mean()) < 1e-12 else float("inf"))
    # Wilcoxon signed-rank (two-sided); undefined when all differences are zero
    nz = imp[np.abs(imp) > 1e-12]
    if len(nz) >= 5:
        try:
            out["p_wilcoxon"] = float(sps.wilcoxon(nz, alternative="two-sided").pvalue)
        except ValueError:
            out["p_wilcoxon"] = 1.0
    elif len(nz) == 0:
        out["p_wilcoxon"] = 1.0
    else:                                                   # too few non-zero pairs: exact sign test
        k = int((nz > 0).sum())
        out["p_wilcoxon"] = float(sps.binomtest(k, len(nz), 0.5).pvalue)
    return out


def holm(pvals: List[float]) -> List[float]:
    """Holm-Bonferroni adjusted p-values (same order as input)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


def friedman(matrix: Dict[str, Sequence[float]]) -> Dict[str, Any]:
    """Friedman test over k schedulers x n paired workloads."""
    names = list(matrix)
    cols = [np.asarray(matrix[n], dtype=float) for n in names]
    try:
        stat, p = sps.friedmanchisquare(*cols)
    except ValueError:
        return {"chi2": None, "p": None}
    ranks = np.mean([sps.rankdata(row) for row in np.column_stack(cols)], axis=0)
    return {"chi2": float(stat), "p": float(p), "mean_rank": dict(zip(names, map(float, ranks)))}
