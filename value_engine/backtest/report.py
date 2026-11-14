"""Plain-text reports and an equity-curve CSV."""
from __future__ import annotations

import csv
import math
import os
from typing import Sequence

from value_engine.core import Summary

from .engine import Result
from .strategy import Bet
from .walkforward import WalkForwardResult


def verdict(s: Summary) -> str:
    """Judge by CLV t-stat: it needs far fewer bets than profit to become meaningful."""
    if s.n < 100:
        return f"TOO FEW BETS ({s.n} < 100): no conclusion possible"
    t = s.clv_t
    if t > 3:
        return f"STRONG: CLV t={t:.2f} > 3, edge very unlikely to be luck"
    if t > 2:
        return f"PROMISING: CLV t={t:.2f} > 2, keep collecting evidence"
    return f"NO RELIABLE EDGE: CLV t={t:.2f}"


def _pct(x: float) -> str:
    return "   n/a" if x is None or math.isnan(x) else f"{x * 100:+6.2f}%"


def _num(x: float) -> str:
    return "  n/a" if x is None or math.isnan(x) else f"{x:5.2f}"


_HEAD = f"{'':<12}{'n':>6}{'odds':>7}{'edge':>9}{'CLV':>9}{'CLV t':>7}{'beat%':>8}{'yield':>9}{'P/L t':>7}"


def _line(name: str, s: Summary) -> str:
    beat = "   n/a" if math.isnan(s.beat_close) else f"{s.beat_close * 100:6.1f}%"
    return (f"{name[:12]:<12}{s.n:>6}{_num(s.avg_odds):>7}{_pct(s.avg_edge):>9}{_pct(s.avg_clv):>9}"
            f"{_num(s.clv_t):>7}{beat:>8}{_pct(s.yield_):>9}{_num(s.profit_t):>7}")


def format_result(r: Result) -> str:
    out = ["Backtest", f"config: {r.config}", "", _HEAD, _line("ALL", r.summary)]
    if not math.isnan(r.summary.kelly_growth):
        out.append(f"Kelly bankroll growth: {_pct(r.summary.kelly_growth).strip()}")
    for dim, groups in r.by.items():
        out += ["", f"by {dim}", _HEAD] + [_line(k, s) for k, s in groups.items()]
    out += ["", "Verdict: " + verdict(r.summary)]
    return "\n".join(out)


def format_walkforward(w: WalkForwardResult) -> str:
    out = ["Walk-forward (params chosen on earlier seasons only)", "",
           f"{'season':<10}{'train n':>8}{'train t':>8}{'test n':>7}{'test CLV':>10}{'test t':>7}{'yield':>9}  params"]
    for r in w.rows:
        out.append(f"{str(r['season'])[:10]:<10}{r['train_n']:>8}{_num(r['train_clv_t']):>8}{r['test_n']:>7}"
                   f"{_pct(r['test_avg_clv']):>10}{_num(r['test_clv_t']):>7}{_pct(r['test_yield']):>9}"
                   f"  {r['params'] if r['params'] is not None else '(no config met min bets)'}")
    out += ["", "OUT-OF-SAMPLE (headline)", _HEAD, _line("OOS", w.oos), "", "Verdict: " + verdict(w.oos)]
    return "\n".join(out)


def write_equity_csv(bets: Sequence[Bet], path: str | os.PathLike) -> None:
    """Flat 1-unit stakes: cumulative profit vs cumulative expected (edge) and CLV.

    Profit wandering far below expected while CLV holds up suggests bad luck; CLV
    falling too suggests the edge estimate is wrong.
    """
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bet", "date", "cum_profit", "cum_expected", "cum_clv"])
        p = e = c = 0.0
        for i, b in enumerate(bets, 1):
            p += b.profit
            e += b.edge
            c += b.clv
            w.writerow([i, b.date.isoformat(), f"{p:.4f}", f"{e:.4f}", f"{c:.4f}"])
