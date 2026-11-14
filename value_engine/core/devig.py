"""Remove the bookmaker margin ("vig") from a set of odds to get fair probabilities.

All functions take decimal odds for every outcome of one market (e.g. home/draw/away)
and return fair probabilities that sum to 1.

Methods differ in how they assume the bookmaker spread its margin:
- multiplicative: in proportion to each outcome's implied probability (simplest)
- additive: equally across outcomes (can go negative on big longshots)
- power: p_i = q_i ** k; takes more margin off longshots (favourite-longshot bias)
- shin: Shin (1993) insider-trading model; also corrects longshot bias
- odds_ratio: constant odds ratio between fair and implied probability (Cheung 2015)
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

Probs = list[float]


def implied(odds: Sequence[float]) -> Probs:
    if len(odds) < 2:
        raise ValueError("need at least two outcomes")
    if any(not (o > 1.0) for o in odds):
        raise ValueError(f"decimal odds must all be > 1: {odds}")
    return [1.0 / o for o in odds]


def margin(odds: Sequence[float]) -> float:
    """Bookmaker overround, e.g. 0.03 for a 3% margin."""
    return sum(implied(odds)) - 1.0


def _bisect(f: Callable[[float], float], lo: float, hi: float, tol: float = 1e-12) -> float:
    flo = f(lo)
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (flo <= 0) == (fm <= 0):
            lo, flo = mid, fm
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def multiplicative(odds: Sequence[float]) -> Probs:
    q = implied(odds)
    s = sum(q)
    return [x / s for x in q]


def additive(odds: Sequence[float]) -> Probs:
    q = implied(odds)
    excess = (sum(q) - 1.0) / len(q)
    p = [x - excess for x in q]
    if min(p) <= 0:  # infeasible on extreme longshots; fall back
        return multiplicative(odds)
    return p


def power(odds: Sequence[float]) -> Probs:
    q = implied(odds)
    if sum(q) <= 1.0:
        return multiplicative(odds)
    k = _bisect(lambda k: sum(x ** k for x in q) - 1.0, 1.0, 50.0)
    return [x ** k for x in q]


def shin(odds: Sequence[float]) -> Probs:
    q = implied(odds)
    big_b = sum(q)
    if big_b <= 1.0:
        return multiplicative(odds)

    def probs(z: float) -> Probs:
        return [(math.sqrt(z * z + 4 * (1 - z) * b * b / big_b) - z) / (2 * (1 - z)) for b in q]

    z = _bisect(lambda z: sum(probs(z)) - 1.0, 0.0, 0.99)
    return probs(z)


def odds_ratio(odds: Sequence[float]) -> Probs:
    q = implied(odds)
    if sum(q) <= 1.0:
        return multiplicative(odds)

    def probs(c: float) -> Probs:
        return [x / (c + x - c * x) for x in q]

    c = _bisect(lambda c: sum(probs(c)) - 1.0, 1.0, 100.0)
    return probs(c)


METHODS: dict[str, Callable[[Sequence[float]], Probs]] = {
    "multiplicative": multiplicative,
    "additive": additive,
    "power": power,
    "shin": shin,
    "odds_ratio": odds_ratio,
}


def devig(odds: Sequence[float], method: str = "power") -> Probs:
    try:
        return METHODS[method](odds)
    except KeyError:
        raise ValueError(f"unknown de-vig method {method!r}; choose from {sorted(METHODS)}") from None


def consensus(markets: Sequence[Sequence[float]], method: str = "power",
              weights: Sequence[float] | None = None) -> Probs:
    """Blend fair probabilities from several sharp sources (e.g. Pinnacle + exchange).

    Averages in log-odds space, which treats 1% vs 2% as a bigger gap than 50% vs 51%,
    then renormalises.
    """
    if not markets:
        raise ValueError("no markets")
    w = list(weights) if weights else [1.0] * len(markets)
    fairs = [devig(m, method) for m in markets]
    n = len(fairs[0])
    logits = []
    for i in range(n):
        acc = sum(wj * math.log(f[i] / (1 - f[i])) for wj, f in zip(w, fairs)) / sum(w)
        logits.append(1 / (1 + math.exp(-acc)))
    s = sum(logits)
    return [x / s for x in logits]


def disagreement(markets: Sequence[Sequence[float]], method: str = "power") -> float:
    """Largest absolute gap in fair probability between sharp sources, for any outcome."""
    fairs = [devig(m, method) for m in markets]
    return max(max(f[i] for f in fairs) - min(f[i] for f in fairs) for i in range(len(fairs[0])))
