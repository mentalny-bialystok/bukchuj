"""SQLite log of every recommended/placed bet, with closing-line value tracking."""
from __future__ import annotations

import csv
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from value_engine.core import clv as clv_fn
from value_engine.core import devig, tstat

DEFAULT_DB = "data/bets.db"
RESULTS = ("pending", "win", "loss", "void")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_id TEXT,
    sport TEXT,
    event_label TEXT,
    commence_time TEXT,
    market TEXT DEFAULT 'h2h',
    outcome TEXT,
    outcome_index INTEGER,
    book TEXT,
    odds REAL NOT NULL,
    stake REAL NOT NULL,
    fair_p REAL,
    edge REAL,
    sharp_odds_at_bet TEXT,
    outcome_names TEXT,
    closing_odds TEXT,
    closing_fair_p REAL,
    clv REAL,
    result TEXT NOT NULL DEFAULT 'pending',
    pl REAL,
    is_real INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(t: datetime | str | None) -> str | None:
    if t is None or isinstance(t, str):
        return t
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


class BetLog:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- writes ------------------------------------------------------------
    def add_bet(self, *, odds: float, stake: float, outcome: str = "", outcome_index: int | None = None,
                fair_p: float | None = None, edge: float | None = None, event_id: str | None = None,
                sport: str | None = None, event_label: str | None = None,
                commence_time: datetime | str | None = None, market: str = "h2h", book: str | None = None,
                sharp_odds: Sequence[float] | None = None, outcome_names: Sequence[str] | None = None,
                is_real: bool = False, notes: str | None = None) -> int:
        if odds <= 1:
            raise ValueError("odds must be decimal odds > 1")
        if stake < 0:
            raise ValueError("stake cannot be negative")
        if edge is None and fair_p is not None:
            edge = fair_p * odds - 1
        cur = self.conn.execute(
            """INSERT INTO bets (created_at, event_id, sport, event_label, commence_time, market,
               outcome, outcome_index, book, odds, stake, fair_p, edge, sharp_odds_at_bet,
               outcome_names, is_real, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_now(), event_id, sport, event_label, _iso(commence_time), market, outcome, outcome_index,
             book, float(odds), float(stake), fair_p, edge,
             json.dumps(list(sharp_odds)) if sharp_odds else None,
             json.dumps(list(outcome_names)) if outcome_names else None, int(bool(is_real)), notes))
        self.conn.commit()
        return int(cur.lastrowid)

    def add_value_bet(self, vb, stake: float | None = None, is_real: bool = False, notes: str | None = None) -> int:
        return self.add_bet(
            odds=vb.odds, stake=vb.stake if stake is None else stake, outcome=vb.outcome,
            outcome_index=vb.outcome_index, fair_p=vb.fair_p, edge=vb.edge, event_id=vb.event.id,
            sport=vb.event.sport_key, event_label=vb.event.label, commence_time=vb.event.commence_time,
            market=vb.market, book=vb.book, sharp_odds=vb.sharp_odds, outcome_names=vb.outcome_names,
            is_real=is_real, notes=notes)

    def set_closing(self, bet_id: int, closing_odds: Sequence[float], method: str = "power") -> float:
        """Store sharp closing odds for every outcome; returns the CLV."""
        b = self.get(bet_id)
        idx = b["outcome_index"]
        if idx is None:
            raise ValueError(f"bet {bet_id} has no outcome_index; can't compute CLV")
        names = json.loads(b["outcome_names"]) if b["outcome_names"] else None
        if names and len(names) != len(closing_odds):
            raise ValueError(f"bet {bet_id} has {len(names)} outcomes, got {len(closing_odds)} closing prices")
        if idx >= len(closing_odds):
            raise ValueError("outcome_index out of range for closing odds")
        fair = devig(list(closing_odds), method)
        c = clv_fn(b["odds"], fair[idx])
        self.conn.execute("UPDATE bets SET closing_odds=?, closing_fair_p=?, clv=? WHERE id=?",
                          (json.dumps(list(closing_odds)), fair[idx], c, bet_id))
        self.conn.commit()
        return c

    def settle(self, bet_id: int, result: str) -> float:
        result = result.lower()
        if result not in RESULTS:
            raise ValueError(f"result must be one of {RESULTS}")
        b = self.get(bet_id)
        pl = {"win": b["stake"] * (b["odds"] - 1), "loss": -b["stake"], "void": 0.0}.get(result)
        self.conn.execute("UPDATE bets SET result=?, pl=? WHERE id=?", (result, pl, bet_id))
        self.conn.commit()
        return pl or 0.0

    def delete(self, bet_id: int) -> None:
        self.conn.execute("DELETE FROM bets WHERE id=?", (bet_id,))
        self.conn.commit()

    # -- reads -------------------------------------------------------------
    def get(self, bet_id: int) -> dict[str, Any]:
        r = self.conn.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
        if r is None:
            raise KeyError(f"no bet with id {bet_id}")
        return dict(r)

    def list_bets(self, *, real: bool | None = None, result: str | None = None,
                  sport: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM bets WHERE 1=1", []
        if real is not None:
            q += " AND is_real=?"; args.append(int(real))
        if result:
            q += " AND result=?"; args.append(result)
        if sport:
            q += " AND sport=?"; args.append(sport)
        q += " ORDER BY id"
        if limit:
            q += " LIMIT ?"; args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args)]

    def stats(self, real: bool | None = None, start_bankroll: float = 0.0) -> dict[str, Any]:
        bets = self.list_bets(real=real)
        settled = [b for b in bets if b["result"] in ("win", "loss")]
        staked = sum(b["stake"] for b in settled)
        profit = sum(b["pl"] or 0 for b in settled)
        clvs = [b["clv"] for b in bets if b["clv"] is not None]
        t = tstat(clvs)
        s = {
            "n_bets": len(bets), "n_settled": len(settled),
            "n_pending": sum(b["result"] == "pending" for b in bets),
            "staked": staked, "profit": profit, "yield": profit / staked if staked else float("nan"),
            "bankroll": start_bankroll + profit,
            "n_clv": len(clvs), "avg_clv": sum(clvs) / len(clvs) if clvs else float("nan"),
            "clv_t": t, "beat_close": sum(c > 0 for c in clvs) / len(clvs) if clvs else float("nan"),
            "avg_edge": _mean([b["edge"] for b in bets if b["edge"] is not None]),
        }
        s["verdict"] = verdict(len(clvs), s["avg_clv"], t)
        return s

    def pending_for_closing(self, within_minutes: float = 30, grace_minutes: float = 15,
                            now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        out = []
        for b in self.list_bets(result="pending"):
            if b["closing_odds"] or not b["commence_time"] or not b["event_id"]:
                continue
            ct = datetime.fromisoformat(b["commence_time"])
            if now - timedelta(minutes=grace_minutes) <= ct <= now + timedelta(minutes=within_minutes):
                out.append(b)
        return out

    def export_csv(self, path: str | Path, real: bool | None = None) -> int:
        bets = self.list_bets(real=real)
        cols = [d[1] for d in self.conn.execute("PRAGMA table_info(bets)")]
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(bets)
        return len(bets)


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def verdict(n: int, avg_clv: float, t: float) -> str:
    if n == 0:
        return "No closing odds recorded yet. Record closing prices (ve log close / capture-closing) to measure skill."
    if n < 30:
        return f"Only {n} bets with closing odds: far too early to tell anything. Keep paper trading."
    tt = "n/a" if math.isnan(t) else f"{t:.1f}"
    head = f"CLV t {tt} on {n} bets (avg CLV {avg_clv:+.1%}): "
    if math.isnan(t) or t < 0:
        return head + "you are NOT beating the closing line. Do not bet real money; revisit settings."
    if t < 1:
        return head + "no evidence of an edge yet. Keep paper trading."
    if t < 2:
        return head + "weak signal, could easily be luck. Keep paper trading."
    if t < 3 or n < 200:
        return head + "promising, keep paper trading to confirm before risking real money."
    return head + "strong evidence you beat the close. Small real stakes are defensible; expect account limits."


def capture_closing(log: BetLog, client, within_minutes: float = 30, grace_minutes: float = 15,
                    sharp_books: Sequence[str] = ("pinnacle", "betfair_ex_eu"), method: str = "power",
                    regions: str = "eu", now: datetime | None = None) -> list[tuple[int, float]]:
    """For pending bets starting soon (or just started), fetch sharp odds and store them as closing.

    One API call per sport (cached), costing regions x markets credits each.
    Returns [(bet_id, clv)].
    """
    todo = log.pending_for_closing(within_minutes, grace_minutes, now)
    by_sport: dict[tuple[str, str], list[dict]] = {}
    for b in todo:
        by_sport.setdefault((b["sport"], b["market"] or "h2h"), []).append(b)
    done = []
    for (sport, market), bets in by_sport.items():
        events = {e.id: e for e in client.get_odds(sport, regions=regions, markets=market, use_cache=False)}
        for b in bets:
            ev = events.get(b["event_id"])
            names = json.loads(b["outcome_names"]) if b["outcome_names"] else None
            if not ev or not names or market not in ev.markets:
                continue
            for book in sharp_books:
                prices = ev.markets[market].get(book)
                if prices and set(prices) == set(names):
                    done.append((b["id"], log.set_closing(b["id"], [prices[n] for n in names], method)))
                    break
    return done
