"""Route B strategy: Pinnacle's de-vigged price is the truth; bet soft books that beat it.

For each market we strip Pinnacle's margin to get fair probabilities, compare against
a soft bookmaker's price, and bet when ``edge = fair_p * soft_odds - 1`` is large
enough. Each bet is judged by closing line value (CLV): the bet's expected return
priced at Pinnacle's de-vigged *closing* odds. CLV is far less noisy than profit, so
it tells us much sooner whether the edge is real.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable

from value_engine.core import clv, devig, edge, kelly_fraction, margin

from .data import PINNACLE, PINNACLE_CLOSE, Match

MARKETS: dict[str, tuple[str, ...]] = {"1x2": ("H", "D", "A"), "ou25": ("Over", "Under")}
_OU_COL = {"Over": ">2.5", "Under": "<2.5"}
# Pinnacle O/U columns use "P"/"PC" rather than "PS"/"PSC".
_OU_SHARP, _OU_CLOSE = "P", "PC"


@dataclass(frozen=True)
class StrategyConfig:
    """Levers of the strategy.

    soft_book: prefix of the book we bet at. Default "Max" (best price across all
        books) is OPTIMISTIC: in reality you rarely hold accounts everywhere, stakes at
        the max-price book get limited, and the max is often a stale/erroneous quote.
        Use a single book (e.g. "B365") for a realistic estimate.
    devig_method: how to remove Pinnacle's margin (see core.devig).
    markets: "1x2" and/or "ou25".
    min_edge / max_edge: bet only when the edge lies in this range; very large edges
        are usually bad data or a stale line, not real value.
    min_odds / max_odds: price band to bet in.
    max_sharp_margin: skip markets where Pinnacle's own overround exceeds this; a wide
        sharp margin means low liquidity and a less trustworthy "true" price.
    outcomes: allowed outcomes, any of H/D/A/Over/Under.
    leagues: allow-list of Div codes (None = all).
    kelly, edge_shrink, max_stake_pct: staking (fraction of Kelly, shrink applied to
        the measured edge, cap per bet as share of bankroll).
    """
    soft_book: str = "Max"
    devig_method: str = "power"
    markets: tuple[str, ...] = ("1x2",)
    min_edge: float = 0.03
    max_edge: float = 0.25
    min_odds: float = 1.2
    max_odds: float = 6.0
    max_sharp_margin: float = 0.05
    outcomes: tuple[str, ...] = ("H", "D", "A", "Over", "Under")
    leagues: tuple[str, ...] | None = None
    kelly: float = 0.25
    edge_shrink: float = 0.6
    max_stake_pct: float = 0.02


@dataclass(frozen=True)
class Bet:
    date: dt.date
    div: str
    match: str
    market: str
    outcome: str
    odds: float
    fair_p: float
    edge: float
    closing_fair_p: float
    clv: float
    won: bool
    kelly_frac: float   # bankroll fraction after kelly multiplier, edge shrink and cap

    @property
    def profit(self) -> float:
        """Profit on a 1-unit flat stake."""
        return self.odds - 1.0 if self.won else -1.0


def _prices(m: Match, market: str, book: str, kind: str) -> list[float] | None:
    if market == "1x2":
        prefix = {"sharp": PINNACLE, "close": PINNACLE_CLOSE, "soft": book}[kind]
        keys = [prefix + s for s in "HDA"]
    else:
        prefix = {"sharp": _OU_SHARP, "close": _OU_CLOSE, "soft": book}[kind]
        keys = [prefix + _OU_COL[o] for o in MARKETS["ou25"]]
    vals = [m.odds.get(k) for k in keys]
    return None if any(v is None for v in vals) else vals  # type: ignore[return-value]


def _won(m: Match, outcome: str) -> bool:
    if outcome == "Over":
        return m.total_goals > 2
    if outcome == "Under":
        return m.total_goals < 3
    return m.result == outcome


def bets_for_match(m: Match, cfg: StrategyConfig) -> list[Bet]:
    """At most one bet per market: the qualifying outcome with the largest edge."""
    if cfg.leagues is not None and m.div not in cfg.leagues:
        return []
    out: list[Bet] = []
    for market in cfg.markets:
        sharp = _prices(m, market, cfg.soft_book, "sharp")
        close = _prices(m, market, cfg.soft_book, "close")
        soft = _prices(m, market, cfg.soft_book, "soft")
        if sharp is None or close is None or soft is None:
            continue
        if margin(sharp) > cfg.max_sharp_margin:
            continue
        fair = devig(sharp, cfg.devig_method)
        fair_close = devig(close, cfg.devig_method)
        best: Bet | None = None
        for i, outcome in enumerate(MARKETS[market]):
            if outcome not in cfg.outcomes:
                continue
            o = soft[i]
            e = edge(fair[i], o)
            if not (cfg.min_edge <= e <= cfg.max_edge and cfg.min_odds <= o <= cfg.max_odds):
                continue
            kf = min(cfg.kelly * cfg.edge_shrink * kelly_fraction(fair[i], o), cfg.max_stake_pct)
            b = Bet(date=m.date, div=m.div, match=m.label, market=market, outcome=outcome,
                    odds=o, fair_p=fair[i], edge=e, closing_fair_p=fair_close[i],
                    clv=clv(o, fair_close[i]), won=_won(m, outcome), kelly_frac=kf)
            if best is None or b.edge > best.edge:
                best = b
        if best is not None:
            out.append(best)
    return out


def generate_bets(matches: Iterable[Match], cfg: StrategyConfig) -> list[Bet]:
    """All bets the strategy would place, in chronological order."""
    bets = [b for m in matches for b in bets_for_match(m, cfg)]
    bets.sort(key=lambda b: b.date)
    return bets
