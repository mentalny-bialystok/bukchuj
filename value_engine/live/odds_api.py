"""Client for The Odds API v4 (https://the-odds-api.com).

Quota: every /odds call costs (number of regions) x (number of markets) credits,
e.g. regions="eu", markets="h2h" costs 1 credit. /sports is free. The free tier is
about 500 credits per month, so responses are cached on disk (default 10 minutes).

This module only *reads* odds. It never places bets.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_SHARP_BOOKS: tuple[str, ...] = ("pinnacle", "betfair_ex_eu")
EXCHANGE_BOOKS: frozenset[str] = frozenset(
    {"betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au", "matchbook", "smarkets", "betdaq"})
LOW_QUOTA_WARNING = 50


class OddsAPIError(RuntimeError):
    pass


@dataclass
class Event:
    id: str
    sport_key: str
    commence_time: datetime
    home: str
    away: str
    # market_key -> bookmaker_key -> outcome_name -> decimal price
    markets: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    sport_title: str = ""

    @property
    def label(self) -> str:
        return f"{self.home} v {self.away}"


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_events(data: list[dict[str, Any]]) -> list[Event]:
    events = []
    for e in data:
        markets: dict[str, dict[str, dict[str, float]]] = {}
        for bk in e.get("bookmakers", []):
            for m in bk.get("markets", []):
                prices = {}
                for o in m.get("outcomes", []):
                    name = o["name"]
                    if o.get("point") is not None:  # spreads/totals: keep the line in the name
                        name = f"{name} {o['point']:+g}" if m["key"] == "spreads" else f"{name} {o['point']:g}"
                    prices[name] = float(o["price"])
                if prices:
                    markets.setdefault(m["key"], {})[bk["key"]] = prices
        events.append(Event(
            id=e["id"], sport_key=e["sport_key"], commence_time=parse_time(e["commence_time"]),
            home=e.get("home_team") or "", away=e.get("away_team") or "",
            markets=markets, sport_title=e.get("sport_title", ""),
        ))
    return events


class OddsAPI:
    def __init__(self, api_key: str | None = None, *, cache_dir: str | Path | None = "data/cache",
                 cache_ttl: float = 600.0, session: requests.Session | Any = None,
                 base_url: str = BASE_URL, timeout: float = 20.0):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        if not self.api_key:
            raise OddsAPIError(
                "No Odds API key. Get a free key at https://the-odds-api.com and run:\n"
                "  export ODDS_API_KEY=your_key_here")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.requests_remaining: int | None = None
        self.requests_used: int | None = None
        self.last_cost: int | None = None
        self.last_from_cache = False

    # -- caching -----------------------------------------------------------
    def _cache_path(self, path: str, params: dict) -> Path | None:
        if not self.cache_dir:
            return None
        key = json.dumps([path, sorted((k, v) for k, v in params.items() if k != "apiKey")])
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json")

    def _get(self, path: str, params: dict, use_cache: bool = True) -> Any:
        cp = self._cache_path(path, params)
        if use_cache and cp and cp.exists() and time.time() - cp.stat().st_mtime < self.cache_ttl:
            try:
                self.last_from_cache = True
                return json.loads(cp.read_text())["data"]
            except (ValueError, KeyError):
                pass
        self.last_from_cache = False
        try:
            r = self.session.get(f"{self.base_url}{path}", params={**params, "apiKey": self.api_key},
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            raise OddsAPIError(f"Could not reach The Odds API: {exc}") from exc
        self._track_quota(r.headers)
        if r.status_code == 401:
            raise OddsAPIError("Odds API rejected the key (401). Check ODDS_API_KEY is correct "
                               "and that your plan is active.")
        if r.status_code == 429:
            raise OddsAPIError("Odds API rate/quota limit hit (429). You've used up your credits "
                               "or are calling too fast; wait or upgrade the plan.")
        if r.status_code == 422:
            raise OddsAPIError(f"Odds API rejected the request parameters (422): {_msg(r)}. "
                               "Check the sport key (run `ve sports`), regions and markets.")
        if r.status_code >= 400:
            raise OddsAPIError(f"Odds API error {r.status_code}: {_msg(r)}")
        data = r.json()
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({"fetched_at": time.time(), "data": data}))
        return data

    def _track_quota(self, headers) -> None:
        def as_int(v):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None
        rem, used, last = (as_int(headers.get(h)) for h in
                           ("x-requests-remaining", "x-requests-used", "x-requests-last"))
        if rem is not None:
            self.requests_remaining = rem
        if used is not None:
            self.requests_used = used
        self.last_cost = last
        if rem is not None and rem < LOW_QUOTA_WARNING:
            warnings.warn(f"Odds API quota low: only {rem} credits left this month.", stacklevel=3)

    # -- endpoints ---------------------------------------------------------
    def list_sports(self, all_sports: bool = False) -> list[dict]:
        """Free call: list sport keys (in-season only unless all_sports)."""
        return self._get("/sports", {"all": "true"} if all_sports else {})

    def get_odds(self, sport_key: str, regions: str = "eu", markets: str = "h2h",
                 odds_format: str = "decimal", bookmakers: list[str] | str | None = None,
                 use_cache: bool = True) -> list[Event]:
        """Fetch odds. Costs regions x markets credits (bookmakers replaces regions:
        every 10 bookmakers count as one region)."""
        params: dict[str, str] = {"markets": markets, "oddsFormat": odds_format, "dateFormat": "iso"}
        if bookmakers:
            params["bookmakers"] = bookmakers if isinstance(bookmakers, str) else ",".join(bookmakers)
        else:
            params["regions"] = regions
        return parse_events(self._get(f"/sports/{sport_key}/odds", params, use_cache=use_cache))

    @staticmethod
    def estimated_cost(regions: str = "eu", markets: str = "h2h") -> int:
        return len([r for r in regions.split(",") if r]) * len([m for m in markets.split(",") if m])


def _msg(r) -> str:
    try:
        return r.json().get("message", r.text)
    except Exception:
        return getattr(r, "text", "")
