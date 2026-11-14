"""Backtester tests on a synthetic football-data.co.uk world with known true probabilities."""
import csv
import datetime as dt
import math
import random

import pytest

from value_engine.backtest import (StrategyConfig, format_result, format_walkforward, grid, holdout,
                                   load, run, season_of, soft_books, walk_forward, write_equity_csv)

COLS = ["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
        "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA", "B365H", "B365D", "B365A",
        "MaxH", "MaxD", "MaxA", "P>2.5", "P<2.5", "PC>2.5", "PC<2.5", "B365>2.5", "B365<2.5"]


def _price(p, mg):
    return [round(1 / (x * (1 + mg)), 3) for x in p]


def make_world(path, seasons=4, per_season=380, noise=0.06, soft_margin=0.05, seed=7):
    """True probs -> Pinnacle (2.5%), Pinnacle close (2%), soft books (5% + noise)."""
    rng = random.Random(seed)
    rows = []
    for s in range(seasons):
        start = dt.date(2018 + s, 8, 10)
        for i in range(per_season):
            d = start + dt.timedelta(days=i * 280 // per_season)
            ph = rng.uniform(0.2, 0.65)
            pd_ = rng.uniform(0.22, 0.3)
            p = [ph, pd_, 1 - ph - pd_]
            po = rng.uniform(0.4, 0.6)
            ou = [po, 1 - po]
            r = rng.random()
            res = "H" if r < p[0] else "D" if r < p[0] + p[1] else "A"
            hg, ag = {"H": (2, 0), "D": (1, 1), "A": (0, 1)}[res]
            if rng.random() < po:  # make goals consistent with O/U draw
                hg, ag = hg + 2, ag + 1
                if res == "D":
                    hg, ag = 2, 2
            elif res == "D":
                hg, ag = 0, 0

            def soft(probs):
                if noise == 0:
                    return _price(probs, soft_margin)
                return [round(1 / (x * (1 + soft_margin)) * math.exp(rng.gauss(0, noise)), 3) for x in probs]
            ps, pc, b365 = _price(p, 0.025), _price(p, 0.02), soft(p)
            mx = [max(a, b) for a, b in zip(b365, soft(p))]
            yy = d.strftime("%d/%m/%y") if s % 2 == 0 else d.strftime("%d/%m/%Y")
            rows.append(["E0", yy, f"T{i % 20}", f"T{(i + 7) % 20}", hg, ag, res,
                         *ps, *pc, *b365, *mx, *_price(ou, 0.025), *_price(ou, 0.02), *soft(ou)])
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(COLS + ["", ""])  # trailing empty columns as in real files
        for r in rows:
            w.writerow(r + ["", ""])
    return path


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return load(make_world(tmp_path_factory.mktemp("w") / "E0.csv"))


@pytest.fixture(scope="module")
def flat_world(tmp_path_factory):
    return load(make_world(tmp_path_factory.mktemp("f") / "E0.csv", noise=0.0, soft_margin=0.06))


def test_loader_formats(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,PSH,PSD,PSA,BbMxH,BbMxD,BbMxA,,\n"
                 "E0,10/08/19,A,B,1,0,H,2.0,3.5,4.0,2.1,3.6,4.2,,\n"
                 "E0,11/08/2019,C,D,0,0,D,,3.5,4.0,2.1,3.6,4.2,,\n"
                 "E0,,E,F,0,0,D,2.0,3.5,4.0,,,,,\n"
                 ",,,,,,,,,,,,,,\n", encoding="utf-8")
    ms = load(tmp_path)
    assert len(ms) == 2
    assert ms[0].date == dt.date(2019, 8, 10) and ms[1].date == dt.date(2019, 8, 11)
    assert ms[0].div == "E0" and ms[0].result == "H" and ms[0].home_goals == 1
    assert ms[0].odds["MaxH"] == 2.1           # BbMx alias mapped
    assert "PSH" not in ms[1].odds             # blank cell skipped
    assert season_of(ms[0].date) == "2019-20" and season_of(dt.date(2020, 3, 1)) == "2019-20"


def test_world_loaded(world):
    assert len(world) == 4 * 380
    assert {"B365", "Max"} <= set(soft_books(world))
    assert "PS" not in soft_books(world) and "PSC" not in soft_books(world)


def test_edge_and_odds_filters(world):
    cfg = StrategyConfig(soft_book="B365", min_edge=0.04, max_edge=0.2, min_odds=1.5, max_odds=5.0, markets=("1x2", "ou25"))
    bets = run(world, cfg).bets
    assert bets
    assert all(0.04 <= b.edge <= 0.2 and 1.5 <= b.odds <= 5.0 for b in bets)
    assert all(b.kelly_frac <= cfg.max_stake_pct for b in bets)
    assert [b.date for b in bets] == sorted(b.date for b in bets)
    assert {b.market for b in bets} == {"1x2", "ou25"}


def test_outcome_and_league_filters(world):
    assert {b.outcome for b in run(world, StrategyConfig(outcomes=("D",))).bets} == {"D"}
    assert run(world, StrategyConfig(leagues=("SP1",))).bets == []


def test_clv_positive_with_genuine_value(world):
    r = run(world, StrategyConfig(soft_book="B365", markets=("1x2", "ou25")))
    assert r.summary.n > 200
    assert r.summary.avg_clv > 0.02
    assert r.summary.clv_t > 3


def test_no_value_world_has_no_bets(flat_world):
    assert run(flat_world, StrategyConfig(soft_book="B365", min_edge=0.01)).summary.n <= 5


def test_grid_one_row_per_combination(world):
    rows = grid(world, StrategyConfig(), min_edge=[0.02, 0.04, 0.06], devig_method=["power", "shin"])
    assert len(rows) == 6
    assert {(r["min_edge"], r["devig_method"]) for r in rows} == {
        (e, m) for e in (0.02, 0.04, 0.06) for m in ("power", "shin")}
    assert all("clv_t" in r for r in rows)


def test_walk_forward_uses_only_past(world):
    pg = {"min_edge": [0.02, 0.05], "soft_book": ["B365", "Max"]}
    w = walk_forward(world, StrategyConfig(), pg, min_train_seasons=2, min_bets=100)
    assert [f.season for f in w.folds] == ["2020-21", "2021-22"]
    for f in w.folds:
        assert f.train_end < f.test_start
        assert all(s < f.season for s in f.train_seasons)
        assert f.params is not None and f.test is not None
    assert w.oos.n == sum(f.test.n for f in w.folds)
    assert all(b.date >= w.folds[0].test_start for b in w.oos_bets)
    assert w.oos.avg_clv > 0
    assert "OUT-OF-SAMPLE" in format_walkforward(w)


def test_walk_forward_unaffected_by_future_data(world):
    """Changing the test season must not change the params chosen for it."""
    pg = {"min_edge": [0.02, 0.05]}
    a = walk_forward(world, StrategyConfig(), pg, min_bets=100)
    cut = [m for m in world if season_of(m.date) <= "2020-21"]
    b = walk_forward(cut, StrategyConfig(), pg, min_bets=100)
    assert a.folds[0].params == b.folds[0].params


def test_holdout(world):
    h = holdout(world, StrategyConfig(), {"min_edge": [0.02, 0.04]}, dt.date(2021, 7, 1), min_bets=100)
    assert h.folds[0].train_end < dt.date(2021, 7, 1)
    assert all(b.date >= dt.date(2021, 7, 1) for b in h.oos_bets)


def test_report_and_equity(world, tmp_path):
    r = run(world, StrategyConfig())
    text = format_result(r)
    assert "Verdict" in text and "by odds_band" in text
    assert "TOO FEW" in format_result(run(world[:20], StrategyConfig()))
    out = tmp_path / "eq.csv"
    write_equity_csv(r.bets, out)
    lines = out.read_text().splitlines()
    assert lines[0] == "bet,date,cum_profit,cum_expected,cum_clv" and len(lines) == r.summary.n + 1
