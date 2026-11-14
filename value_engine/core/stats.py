"""Honest evaluation: is the edge real or luck?"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def tstat(xs: Sequence[float]) -> float:
    """One-sample t-statistic against zero. >2 is suggestive, >3 is strong."""
    n = len(xs)
    if n < 3:
        return float("nan")
    m = mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else float("inf") * (1 if m > 0 else -1)


def bets_needed(edge: float, avg_odds: float, t_target: float = 2.0) -> int:
    """Roughly how many flat bets until profit alone reaches the target t-stat."""
    p = (1 + edge) / avg_odds
    sd = avg_odds * math.sqrt(p * (1 - p))
    return math.ceil((t_target * sd / edge) ** 2) if edge > 0 else -1


@dataclass
class Summary:
    n: int
    avg_odds: float
    avg_edge: float
    yield_: float
    profit_t: float
    avg_clv: float
    clv_t: float
    beat_close: float
    kelly_growth: float

    def as_dict(self) -> dict:
        return {k.rstrip("_"): v for k, v in self.__dict__.items()}


def summarise(odds: Sequence[float], won: Sequence[bool], edges: Sequence[float],
              clvs: Sequence[float], kelly_fracs: Sequence[float] | None = None) -> Summary:
    pl = [(o - 1) if w else -1.0 for o, w in zip(odds, won)]
    growth = float("nan")
    if kelly_fracs is not None:
        log_b = 0.0
        for o, w, f in zip(odds, won, kelly_fracs):
            log_b += math.log(1 + (f * (o - 1) if w else -f))
        growth = math.exp(log_b) - 1
    return Summary(
        n=len(pl), avg_odds=mean(odds), avg_edge=mean(edges), yield_=mean(pl),
        profit_t=tstat(pl), avg_clv=mean(clvs), clv_t=tstat(clvs),
        beat_close=mean([1.0 if c > 0 else 0.0 for c in clvs]), kelly_growth=growth,
    )
