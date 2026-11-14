"""Run a strategy over historical matches and summarise the results."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Sequence

from value_engine.core import Summary, summarise

from .data import Match
from .strategy import Bet, StrategyConfig, generate_bets


def summarise_bets(bets: Sequence[Bet]) -> Summary:
    return summarise([b.odds for b in bets], [b.won for b in bets], [b.edge for b in bets],
                     [b.clv for b in bets], [b.kelly_frac for b in bets])


def odds_band(odds: float) -> str:
    return "<2" if odds < 2 else "2-3" if odds < 3 else "3-6" if odds <= 6 else ">6"


def _breakdown(bets: Sequence[Bet], key: Callable[[Bet], str]) -> dict[str, Summary]:
    groups: dict[str, list[Bet]] = {}
    for b in bets:
        groups.setdefault(key(b), []).append(b)
    return {k: summarise_bets(v) for k, v in sorted(groups.items())}


@dataclass
class Result:
    config: StrategyConfig
    bets: list[Bet]
    summary: Summary
    by: dict[str, dict[str, Summary]] = field(default_factory=dict)


def run(matches: Iterable[Match], config: StrategyConfig = StrategyConfig()) -> Result:
    """Backtest one config. Breakdowns: league, outcome, market, odds_band."""
    bets = generate_bets(matches, config)
    by = {
        "league": _breakdown(bets, lambda b: b.div),
        "outcome": _breakdown(bets, lambda b: b.outcome),
        "market": _breakdown(bets, lambda b: b.market),
        "odds_band": _breakdown(bets, lambda b: odds_band(b.odds)),
    }
    return Result(config=config, bets=bets, summary=summarise_bets(bets), by=by)


def configs(base: StrategyConfig, param_lists: dict[str, Sequence[Any]]) -> list[StrategyConfig]:
    """Every combination of the given parameter values applied on top of ``base``."""
    names = list(param_lists)
    return [replace(base, **dict(zip(names, combo)))
            for combo in itertools.product(*(param_lists[n] for n in names))]


def grid(matches: Iterable[Match], base_config: StrategyConfig = StrategyConfig(),
         **param_lists: Sequence[Any]) -> list[dict[str, Any]]:
    """Run every combination; one row per combination: its params + Summary fields.

    In-sample only: the best row of a grid is optimistically biased. Use walkforward
    for an honest estimate.
    """
    ms = list(matches)
    rows = []
    for cfg in configs(base_config, param_lists):
        s = summarise_bets(generate_bets(ms, cfg))
        rows.append({**{n: getattr(cfg, n) for n in param_lists}, **s.as_dict()})
    return rows
