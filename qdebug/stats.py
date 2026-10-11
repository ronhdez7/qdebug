"""Statistics helpers: distance metrics and shot-noise floors.

Everything works on plain ``dict[str, float]`` distributions keyed by bitstring
(Qiskit order, e.g. ``"011"`` means q2=0, q1=1, q0=1).
"""
from __future__ import annotations

import numpy as np

Dist = dict[str, float]


def normalize_key(key: str) -> str:
    """Strip register separators ("01 1" -> "011")."""
    return key.replace(" ", "")


def counts_to_probs(counts: dict) -> Dist:
    total = float(sum(counts.values()))
    if total <= 0:
        raise ValueError("empty counts")
    out: Dist = {}
    for k, v in counts.items():
        k = normalize_key(str(k))
        out[k] = out.get(k, 0.0) + v / total
    return out


def clean_probs(probs: dict, eps: float = 1e-12) -> Dist:
    """Normalise keys, drop numerically-zero entries, renormalise to sum 1."""
    out: Dist = {}
    for k, v in probs.items():
        k = normalize_key(str(k))
        out[k] = out.get(k, 0.0) + float(v)
    out = {k: v for k, v in out.items() if v > eps}
    s = sum(out.values())
    if s <= 0:
        raise ValueError("distribution has no mass")
    return {k: v / s for k, v in out.items()}


def tvd(p: Dist, q: Dist) -> float:
    """Total variation distance, in [0, 1]."""
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def sample_counts(p: Dist, shots: int, rng: np.random.Generator) -> Dist:
    keys = sorted(p)
    probs = np.array([p[k] for k in keys], dtype=float)
    probs /= probs.sum()
    draw = rng.multinomial(shots, probs)
    return {k: c / shots for k, c in zip(keys, draw) if c}


def shot_noise_floor(p: Dist, shots: int, quantile: float = 0.99,
                     trials: int = 400, seed: int = 1234) -> float:
    """TVD you'd see from finite sampling alone (no hardware noise).

    Draws ``trials`` multinomial samples of ``shots`` from ``p`` and returns the
    requested quantile of TVD(sample, p). Used so we never blame a bug/noise for
    what is just shot noise.
    """
    rng = np.random.default_rng(seed)
    vals = [tvd(sample_counts(p, shots, rng), p) for _ in range(trials)]
    return float(np.quantile(vals, quantile))


def mean_std(xs: list[float]) -> tuple[float, float]:
    arr = np.asarray(xs, dtype=float)
    if arr.size == 0:
        return 0.0, 0.0
    return float(arr.mean()), float(arr.std(ddof=1)) if arr.size > 1 else 0.0
