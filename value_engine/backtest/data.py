"""Load football-data.co.uk season CSVs into typed Match records.

Each row is one match with result and odds from many bookmakers. Pinnacle (prefix
``PS`` pre-match, ``PSC`` closing) is the sharp reference; every other bookmaker is
a "soft" book we might bet at. Rows are messy: blank cells, BOMs, trailing empty
columns and 2- or 4-digit years are all handled. Odds that are blank or not > 1 are
simply left out of ``Match.odds``; the strategy skips markets it cannot price.
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import os
from dataclasses import dataclass, field
from typing import Iterable

PINNACLE = "PS"
PINNACLE_CLOSE = "PSC"
# Older files name market max/average differently; map them onto the modern names.
_ALIASES = {"BbMx": "Max", "BbAv": "Avg"}
_OU_SUFFIXES = (">2.5", "<2.5")


@dataclass(frozen=True)
class Match:
    div: str
    date: dt.date
    home: str
    away: str
    result: str           # "H", "D" or "A"
    home_goals: int
    away_goals: int
    odds: dict[str, float] = field(default_factory=dict, compare=False, hash=False)

    @property
    def label(self) -> str:
        return f"{self.home} v {self.away}"

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals


def season_of(d: dt.date) -> str:
    """Football season label: Jul-Jun, e.g. 2019-08-10 -> "2019-20"."""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def parse_date(s: str) -> dt.date:
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {s!r}")


def _to_odds(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        x = float(v.strip())
    except ValueError:
        return None
    return x if x > 1.0 else None


def _normalise_key(k: str) -> str:
    for old, new in _ALIASES.items():
        if k.startswith(old):
            rest = k[len(old):]
            if rest in ("H", "D", "A") or rest in _OU_SUFFIXES:
                return new + rest
    return k


def _parse_row(row: dict[str, str | None]) -> Match | None:
    try:
        res = (row.get("FTR") or "").strip()
        if res not in ("H", "D", "A"):
            return None
        m_date = parse_date(row.get("Date") or "")
        hg, ag = int(float(row["FTHG"] or "")), int(float(row["FTAG"] or ""))
    except (ValueError, KeyError, TypeError):
        return None
    odds: dict[str, float] = {}
    for k, v in row.items():
        if not k:
            continue  # trailing empty columns
        x = _to_odds(v)
        if x is None:
            continue
        nk = _normalise_key(k.strip())
        odds.setdefault(nk, x)  # a modern column wins over its alias if both exist
    return Match(div=(row.get("Div") or "").strip(), date=m_date,
                 home=(row.get("HomeTeam") or "").strip(), away=(row.get("AwayTeam") or "").strip(),
                 result=res, home_goals=hg, away_goals=ag, odds=odds)


def _expand(paths: str | os.PathLike | Iterable[str | os.PathLike]) -> list[str]:
    items = [paths] if isinstance(paths, (str, os.PathLike)) else list(paths)
    out: list[str] = []
    for p in items:
        p = os.fspath(p)
        if os.path.isdir(p):
            out.extend(sorted(glob.glob(os.path.join(p, "*.csv"))))
        elif any(c in p for c in "*?["):
            out.extend(sorted(glob.glob(p)))
        else:
            out.append(p)
    return out


def load(paths: str | os.PathLike | Iterable[str | os.PathLike]) -> list[Match]:
    """Load one file, a directory of CSVs, a glob, or a list of any of these.

    Returns matches sorted by date. Rows without a valid result/date/score are dropped.
    """
    matches: list[Match] = []
    for path in _expand(paths):
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            for row in csv.DictReader(fh):
                m = _parse_row(row)
                if m is not None:
                    matches.append(m)
    matches.sort(key=lambda m: (m.date, m.div, m.home))
    return matches


def soft_books(matches: Iterable[Match], min_coverage: float = 0.0) -> list[str]:
    """Bookmaker prefixes X with XH/XD/XA prices, excluding Pinnacle and closing columns.

    ``min_coverage`` drops books priced on fewer than that share of matches.
    """
    counts: dict[str, int] = {}
    n = 0
    for m in matches:
        n += 1
        for k in m.odds:
            if k.endswith("H") and k[:-1] and all(k[:-1] + s in m.odds for s in "DA"):
                counts[k[:-1]] = counts.get(k[:-1], 0) + 1
    excluded = {PINNACLE, PINNACLE_CLOSE}
    books = [b for b, c in counts.items()
             if b not in excluded and not (b.endswith("C") and b[:-1] in counts)
             and (n == 0 or c / n >= min_coverage)]
    return sorted(books)
