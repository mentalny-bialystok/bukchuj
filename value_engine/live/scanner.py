"""Find soft-bookmaker prices that beat the de-vigged sharp price."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from value_engine.core import StakingRules, consensus, disagreement, edge, margin, stake

from .odds_api import DEFAULT_SHARP_BOOKS, EXCHANGE_BOOKS, Event


@dataclass
class ScanConfig:
    sharp_books: Sequence[str] = DEFAULT_SHARP_BOOKS
    soft_books: Sequence[str] | None = None   # None = every non-sharp book
    devig_method: str = "power"
    min_edge: float = 0.03
    max_edge: float = 0.25        # bigger "edges" are usually stale/wrong prices
    min_odds: float = 1.2
    max_odds: float = 10.0
    max_sharp_margin: float = 0.06
    max_sharp_disagreement: float = 0.03
    min_minutes_to_start: float = 5.0
    max_hours_to_start: float = 72.0
    exchange_commission: float = 0.0   # applied to exchange back prices when betting there
    markets: Sequence[str] = ("h2h",)


@dataclass
class ValueBet:
    event: Event
    market: str
    outcome: str
    outcome_index: int
    outcome_names: list[str]
    book: str
    odds: float          # effective odds (after commission for exchanges)
    fair_p: float
    fair_odds: float
    edge: float
    sharp_margin: float
    sharp_sources: int
    sharp_odds: list[float]   # reference sharp prices (first sharp book) for every outcome
    stake: float
    starts_in: timedelta
    raw_odds: float = 0.0
    sharp_books_used: list[str] = field(default_factory=list)


def _sharp_fair(book_prices: dict[str, dict[str, float]], names: list[str], cfg: ScanConfig):
    """Return (fair_probs, avg_margin, books_used, ref_odds) or None if unusable."""
    rows, used = [], []
    for b in cfg.sharp_books:
        p = book_prices.get(b)
        if not p or set(p) != set(names):   # sharp market must contain exactly all outcomes
            continue
        odds = [p[n] for n in names]
        try:
            m = margin(odds)
        except ValueError:
            continue
        if m > cfg.max_sharp_margin:
            continue
        rows.append(odds)
        used.append(b)
    if not rows:
        return None
    if len(rows) > 1 and disagreement(rows, cfg.devig_method) > cfg.max_sharp_disagreement:
        return None
    fair = consensus(rows, cfg.devig_method)
    avg_m = sum(margin(r) for r in rows) / len(rows)
    return fair, avg_m, used, rows[0]


def outcome_order(ev: Event, names: Iterable[str]) -> list[str]:
    """Stable outcome ordering: home, draw, away, then anything else alphabetically."""
    names = set(names)
    order = [n for n in (ev.home, "Draw", ev.away) if n in names]
    return order + sorted(names - set(order))


def scan(events: Iterable[Event], cfg: ScanConfig = ScanConfig(), bankroll: float = 0.0,
         rules: StakingRules = StakingRules(), now: datetime | None = None) -> list[ValueBet]:
    now = now or datetime.now(timezone.utc)
    sharp = set(cfg.sharp_books)
    out: list[ValueBet] = []
    for ev in events:
        starts_in = ev.commence_time - now
        if starts_in < timedelta(minutes=cfg.min_minutes_to_start):
            continue
        if starts_in > timedelta(hours=cfg.max_hours_to_start):
            continue
        for mkey in cfg.markets:
            book_prices = ev.markets.get(mkey)
            if not book_prices:
                continue
            ref = next((book_prices[b] for b in cfg.sharp_books if b in book_prices), None)
            if not ref:
                continue
            names = outcome_order(ev, ref)
            sf = _sharp_fair(book_prices, names, cfg)
            if sf is None:
                continue
            fair, sm, used, ref_odds = sf
            for book, prices in book_prices.items():
                if book in sharp:
                    continue
                if cfg.soft_books is not None and book not in cfg.soft_books:
                    continue
                if set(prices) != set(names):
                    continue
                for i, n in enumerate(names):
                    raw = prices[n]
                    eff = raw
                    if book in EXCHANGE_BOOKS and cfg.exchange_commission:
                        eff = 1 + (raw - 1) * (1 - cfg.exchange_commission)
                    if not (cfg.min_odds <= eff <= cfg.max_odds):
                        continue
                    e = edge(fair[i], eff)
                    if e < cfg.min_edge or e > cfg.max_edge:
                        continue
                    out.append(ValueBet(
                        event=ev, market=mkey, outcome=n, outcome_index=i, outcome_names=names,
                        book=book, odds=eff, fair_p=fair[i], fair_odds=1 / fair[i], edge=e,
                        sharp_margin=sm, sharp_sources=len(used), sharp_odds=list(ref_odds),
                        stake=stake(bankroll, fair[i], eff, rules) if bankroll > 0 else 0.0,
                        starts_in=starts_in, raw_odds=raw, sharp_books_used=used,
                    ))
    out.sort(key=lambda v: v.edge, reverse=True)
    return out
