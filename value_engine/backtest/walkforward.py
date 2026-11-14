"""Honest out-of-sample validation.

Picking the best of many configs on the same data you evaluate it on overstates the
edge (you partly select for luck). Walk-forward avoids this: for each season, choose
the config using ONLY earlier seasons, freeze it, then measure it on the season that
follows. The pooled out-of-sample result across all test seasons is the HEADLINE
number for judging the strategy; in-sample grid results are not.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from value_engine.core import Summary

from .data import Match, season_of
from .engine import configs, summarise_bets
from .strategy import Bet, StrategyConfig, generate_bets


@dataclass
class Fold:
    season: str
    train_seasons: list[str]
    train_end: dt.date | None      # last match date used for selection
    test_start: dt.date
    params: dict[str, Any] | None  # None: no config reached min_bets in training
    train_n: int
    train_clv_t: float
    test: Summary | None

    def row(self) -> dict[str, Any]:
        t = self.test
        return {"season": self.season, "params": self.params, "train_n": self.train_n,
                "train_clv_t": self.train_clv_t, "test_n": t.n if t else 0,
                "test_avg_clv": t.avg_clv if t else math.nan,
                "test_clv_t": t.clv_t if t else math.nan,
                "test_yield": t.yield_ if t else math.nan}


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    oos_bets: list[Bet]
    oos: Summary                    # headline: pooled out-of-sample performance
    rows: list[dict[str, Any]] = field(default_factory=list)


def _select(train: list[Match], cands: list[StrategyConfig],
            min_bets: int) -> tuple[StrategyConfig | None, int, float]:
    """Best config by CLV t-stat among those with at least ``min_bets`` bets."""
    best: tuple[StrategyConfig | None, int, float] = (None, 0, -math.inf)
    for cfg in cands:
        bets = generate_bets(train, cfg)
        if len(bets) < min_bets:
            continue
        t = summarise_bets(bets).clv_t
        if not math.isnan(t) and t > best[2]:
            best = (cfg, len(bets), t)
    return best


def walk_forward(matches: Sequence[Match], base_config: StrategyConfig,
                 param_grid: dict[str, Sequence[Any]], min_train_seasons: int = 2,
                 min_bets: int = 200, expanding: bool = True) -> WalkForwardResult:
    """Season-by-season walk-forward.

    For each season k with at least ``min_train_seasons`` earlier seasons, every grid
    config is scored on the earlier seasons (all of them if ``expanding``, else only
    the last ``min_train_seasons``) by CLV t-stat, requiring ``min_bets`` bets. The
    winner is frozen and evaluated on season k. Test bets from all folds are pooled
    into ``oos`` - the number to believe.
    """
    by_season: dict[str, list[Match]] = {}
    for m in sorted(matches, key=lambda m: m.date):
        by_season.setdefault(season_of(m.date), []).append(m)
    seasons = sorted(by_season)
    cands = configs(base_config, param_grid)
    names = list(param_grid)
    folds: list[Fold] = []
    oos: list[Bet] = []
    for k in range(min_train_seasons, len(seasons)):
        train_s = seasons[:k] if expanding else seasons[k - min_train_seasons:k]
        train = [m for s in train_s for m in by_season[s]]
        test = by_season[seasons[k]]
        cfg, n, t = _select(train, cands, min_bets)
        test_sum = None
        if cfg is not None:
            tb = generate_bets(test, cfg)
            oos.extend(tb)
            test_sum = summarise_bets(tb)
        folds.append(Fold(season=seasons[k], train_seasons=train_s,
                          train_end=max((m.date for m in train), default=None),
                          test_start=test[0].date,
                          params={nm: getattr(cfg, nm) for nm in names} if cfg else None,
                          train_n=n, train_clv_t=t if cfg else math.nan, test=test_sum))
    oos.sort(key=lambda b: b.date)
    return WalkForwardResult(folds=folds, oos_bets=oos, oos=summarise_bets(oos),
                             rows=[f.row() for f in folds])


def holdout(matches: Sequence[Match], base_config: StrategyConfig,
            param_grid: dict[str, Sequence[Any]], test_from_date: dt.date,
            min_bets: int = 200) -> WalkForwardResult:
    """Single split: select on matches before ``test_from_date``, evaluate on the rest."""
    train = [m for m in matches if m.date < test_from_date]
    test = sorted((m for m in matches if m.date >= test_from_date), key=lambda m: m.date)
    if not test:
        raise ValueError("no matches on or after test_from_date")
    names = list(param_grid)
    cfg, n, t = _select(train, configs(base_config, param_grid), min_bets)
    tb = generate_bets(test, cfg) if cfg else []
    fold = Fold(season=f"from {test_from_date}", train_seasons=sorted({season_of(m.date) for m in train}),
                train_end=max((m.date for m in train), default=None), test_start=test[0].date,
                params={nm: getattr(cfg, nm) for nm in names} if cfg else None,
                train_n=n, train_clv_t=t if cfg else math.nan,
                test=summarise_bets(tb) if cfg else None)
    return WalkForwardResult(folds=[fold], oos_bets=tb, oos=summarise_bets(tb), rows=[fold.row()])
