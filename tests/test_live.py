import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from value_engine.cli import main
from value_engine.core import clv, devig
from value_engine.live import BetLog, OddsAPI, OddsAPIError, ScanConfig, capture_closing, parse_events, scan

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def book(key, prices, mkey="h2h"):
    return {"key": key, "title": key.title(), "last_update": "2026-11-14T11:59:00Z",
            "markets": [{"key": mkey, "last_update": "2026-11-14T11:59:00Z",
                         "outcomes": [{"name": n, "price": p} for n, p in prices.items()]}]}


def event(eid="e1", home="Arsenal", away="Chelsea", hours=5, books=()):
    return {"id": eid, "sport_key": "soccer_epl", "sport_title": "EPL",
            "commence_time": (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "home_team": home, "away_team": away, "bookmakers": list(books)}


def three(h, d, a, home="Arsenal", away="Chelsea"):
    return {home: h, "Draw": d, away: a}


SAMPLE = [
    event(books=[book("pinnacle", three(2.00, 3.50, 4.00)), book("betfair_ex_eu", three(2.02, 3.55, 4.05)),
                 book("bet365", three(2.30, 3.30, 3.70)), book("unibet", three(1.95, 3.40, 3.90))]),
    event("e2", "Lakers", "Celtics", 10, books=[book("pinnacle", {"Lakers": 1.90, "Celtics": 1.95}),
                                                book("bet365", {"Lakers": 1.85, "Celtics": 2.00})]),
]


class FakeResp:
    def __init__(self, data, status=200, headers=None):
        self._d, self.status_code = data, status
        self.headers = headers or {"x-requests-remaining": "480", "x-requests-used": "20", "x-requests-last": "1"}
        self.text = json.dumps(data)

    def json(self):
        return self._d


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.resp


def client(tmp_path, resp=None, **kw):
    s = FakeSession(resp or FakeResp(SAMPLE))
    return OddsAPI("k", cache_dir=tmp_path / "cache", session=s, **kw), s


# ---------------------------------------------------------------- odds api
def test_parse_events():
    evs = parse_events(SAMPLE)
    assert len(evs) == 2
    e = evs[0]
    assert e.home == "Arsenal" and e.away == "Chelsea"
    assert e.commence_time == NOW + timedelta(hours=5) and e.commence_time.tzinfo is not None
    assert e.markets["h2h"]["pinnacle"]["Draw"] == 3.50
    assert set(e.markets["h2h"]) == {"pinnacle", "betfair_ex_eu", "bet365", "unibet"}


def test_quota_and_params(tmp_path):
    c, s = client(tmp_path)
    evs = c.get_odds("soccer_epl")
    assert len(evs) == 2
    assert c.requests_remaining == 480 and c.requests_used == 20 and c.last_cost == 1
    url, params = s.calls[0]
    assert url.endswith("/sports/soccer_epl/odds")
    assert params["regions"] == "eu" and params["oddsFormat"] == "decimal" and params["apiKey"] == "k"


def test_low_quota_warns(tmp_path):
    c, _ = client(tmp_path, FakeResp(SAMPLE, headers={"x-requests-remaining": "3", "x-requests-used": "497"}))
    with pytest.warns(UserWarning, match="quota low"):
        c.get_odds("soccer_epl")


def test_cache_hit_avoids_second_call(tmp_path):
    c, s = client(tmp_path)
    c.get_odds("soccer_epl")
    c.get_odds("soccer_epl")
    assert len(s.calls) == 1 and c.last_from_cache
    c.get_odds("soccer_epl", use_cache=False)
    assert len(s.calls) == 2
    c2, s2 = client(tmp_path, cache_ttl=0)
    c2.get_odds("soccer_epl")
    assert len(s2.calls) == 1


def test_errors(tmp_path):
    c, _ = client(tmp_path, FakeResp({"message": "API key is not valid"}, status=401))
    with pytest.raises(OddsAPIError, match="401.*ODDS_API_KEY|ODDS_API_KEY"):
        c.get_odds("soccer_epl")
    c, _ = client(tmp_path / "b", FakeResp({"message": "quota"}, status=429))
    with pytest.raises(OddsAPIError, match="429"):
        c.get_odds("soccer_epl")
    c, _ = client(tmp_path / "c", FakeResp({"message": "Unknown sport"}, status=422))
    with pytest.raises(OddsAPIError, match="422.*Unknown sport"):
        c.get_odds("nope")


def test_missing_key(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    with pytest.raises(OddsAPIError, match="ODDS_API_KEY"):
        OddsAPI(cache_dir=None)


# ---------------------------------------------------------------- scanner
def test_scanner_finds_planted_value():
    vbs = scan(parse_events(SAMPLE), ScanConfig(), bankroll=1000, now=NOW)
    assert len(vbs) == 1
    v = vbs[0]
    assert (v.book, v.outcome, v.odds) == ("bet365", "Arsenal", 2.30)
    assert v.sharp_sources == 2 and v.outcome_names == ["Arsenal", "Draw", "Chelsea"]
    assert math.isclose(v.edge, v.fair_p * 2.30 - 1) and v.edge > 0.1
    assert 0.45 < v.fair_p < 0.5 and v.stake > 0
    # Lakers/Celtics 2-way: bet365 Celtics 2.00 vs fair ~2.0 -> no value
    assert all(v.event.id != "e2" for v in vbs)


def test_scanner_two_way_value():
    data = [event("e3", "A", "B", 3, books=[book("pinnacle", {"A": 1.90, "B": 1.95}),
                                           book("soft", {"A": 2.10, "B": 1.70})])]
    vbs = scan(parse_events(data), ScanConfig(), now=NOW)
    assert [(v.outcome, v.book) for v in vbs] == [("A", "soft")]


def test_scanner_skips_sharp_disagreement():
    data = [event(books=[book("pinnacle", three(2.00, 3.50, 4.00)), book("betfair_ex_eu", three(2.40, 3.40, 3.10)),
                         book("bet365", three(2.30, 3.30, 3.70))])]
    assert scan(parse_events(data), ScanConfig(), now=NOW) == []
    # with only pinnacle as sharp it's found again
    assert scan(parse_events(data), ScanConfig(sharp_books=("pinnacle",)), now=NOW)


def test_scanner_skips_incomplete_sharp_market():
    data = [event(books=[book("pinnacle", {"Arsenal": 2.00, "Chelsea": 4.00}),
                         book("bet365", three(2.30, 3.30, 3.70))])]
    assert scan(parse_events(data), ScanConfig(), now=NOW) == []


def test_scanner_time_window_and_soft_filter():
    evs = parse_events(SAMPLE)
    assert scan(evs, ScanConfig(max_hours_to_start=2), now=NOW) == []
    assert scan(evs, ScanConfig(soft_books=["unibet"]), now=NOW) == []


# ---------------------------------------------------------------- bet log
def test_betlog_roundtrip(tmp_path):
    log = BetLog(tmp_path / "bets.db")
    sharp = [2.00, 3.50, 4.00]
    fair = devig(sharp, "power")[0]
    b1 = log.add_bet(odds=2.30, stake=10, outcome="Arsenal", outcome_index=0, fair_p=fair,
                     sharp_odds=sharp, outcome_names=["Arsenal", "Draw", "Chelsea"], event_label="Arsenal v Chelsea")
    b2 = log.add_bet(odds=3.00, stake=5, outcome="Draw", outcome_index=1, outcome_names=["H", "D", "A"])
    close = [1.90, 3.60, 4.40]
    c1 = log.set_closing(b1, close, "power")
    assert math.isclose(c1, clv(2.30, devig(close, "power")[0]))
    log.set_closing(b2, close)
    assert log.settle(b1, "win") == pytest.approx(13.0)
    assert log.settle(b2, "loss") == -5
    r = log.get(b1)
    assert r["result"] == "win" and json.loads(r["closing_odds"]) == close and r["is_real"] == 0
    s = log.stats(start_bankroll=50)
    assert s["n_bets"] == 2 and s["staked"] == 15 and s["profit"] == pytest.approx(8.0)
    assert s["yield"] == pytest.approx(8 / 15) and s["bankroll"] == pytest.approx(58)
    assert s["n_clv"] == 2 and "too early" in s["verdict"]
    assert log.stats(real=True)["n_bets"] == 0
    assert log.export_csv(tmp_path / "out.csv") == 2
    with pytest.raises(ValueError):
        log.set_closing(b1, [2.0, 2.0])
    log.close()


def test_capture_closing(tmp_path):
    log = BetLog(tmp_path / "bets.db")
    vb = scan(parse_events(SAMPLE), ScanConfig(), bankroll=1000, now=NOW)[0]
    bid = log.add_value_bet(vb)
    c, s = client(tmp_path)
    now = vb.event.commence_time - timedelta(minutes=10)
    done = capture_closing(log, c, now=now)
    assert [d[0] for d in done] == [bid] and len(s.calls) == 1
    assert math.isclose(log.get(bid)["clv"], 2.30 * devig([2.00, 3.50, 4.00])[0] - 1)
    assert capture_closing(log, c, now=now) == []   # already closed


# ---------------------------------------------------------------- CLI
def test_cli_devig():
    r = CliRunner().invoke(main, ["devig", "2.10", "3.40", "3.60"])
    assert r.exit_code == 0, r.output
    assert "margin" in r.output.lower() and "power" in r.output
    r = CliRunner().invoke(main, ["devig", "2.10", "3.40", "3.60", "--all"])
    assert r.exit_code == 0 and "shin" in r.output and "multiplicative" in r.output


def test_cli_check():
    r = CliRunner().invoke(main, ["check", "--sharp", "2.10,3.40,3.60", "--pick", "0", "--soft", "2.30",
                                  "--bankroll", "1000"])
    assert r.exit_code == 0, r.output
    assert "VALUE" in r.output and "Suggested stake" in r.output
    r = CliRunner().invoke(main, ["check", "--sharp", "2.10,3.40,3.60", "--pick", "0", "--soft", "2.0"])
    assert "No value" in r.output


def test_cli_log(tmp_path):
    db = str(tmp_path / "b.db")
    run = lambda *a: CliRunner().invoke(main, list(a) + ["--db", db])
    r = run("log", "add", "--sharp", "2.10,3.40,3.60", "--pick", "0", "--odds", "2.30", "--stake", "1",
            "--event", "Arsenal v Chelsea", "--outcomes", "Arsenal,Draw,Chelsea")
    assert r.exit_code == 0, r.output and "#1" in r.output
    assert run("log", "close", "1", "2.05,3.50,3.80").exit_code == 0
    assert "win" in run("log", "settle", "1", "win").output
    r = run("log", "list")
    assert "Arsenal v Chelsea" in r.output and "paper" in r.output
    r = run("log", "stats", "--paper")
    assert r.exit_code == 0, r.output
    assert "Profit:          +1.30" in r.output and "Verdict" in r.output


def test_cli_scan(tmp_path, monkeypatch):
    import value_engine.live.odds_api as oa
    sess = FakeSession(FakeResp([{**e, "commence_time": (datetime.now(timezone.utc) + timedelta(hours=3))
                                  .strftime("%Y-%m-%dT%H:%M:%SZ")} for e in SAMPLE]))
    monkeypatch.setattr(oa.requests, "Session", lambda: sess)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["scan", "soccer_epl", "--api-key", "k", "--bankroll", "50", "--log", "--yes",
                                  "--db", str(tmp_path / "b.db")])
    assert r.exit_code == 0, r.output
    assert "bet365" in r.output and "Logged paper bet #1" in r.output and "PAPER TRADING" in r.output


def test_targets_sheet():
    from value_engine.live.scanner import targets
    ts = targets(parse_events(SAMPLE), ScanConfig(min_edge=0.03), now=NOW)
    e1 = [t for t in ts if t.event.id == "e1"]
    assert [t.outcome for t in e1] == ["Arsenal", "Draw", "Chelsea"]
    for t in e1:
        assert math.isclose(t.min_odds, t.fair_odds * 1.03)
        assert t.fair_odds > 1 / (1 / 2.00) * 0.99  # fair odds are above sharp price (vig removed)
    assert math.isclose(sum(t.fair_p for t in e1), 1.0)
    # a soft price at the target exactly clears the threshold
    t = e1[0]
    assert math.isclose(t.fair_p * t.min_odds - 1, 0.03)


def test_load_dotenv(tmp_path, monkeypatch):
    from value_engine.cli import load_dotenv
    (tmp_path / ".env").write_text("# comment\nVE_TEST_KEY='abc'\nVE_KEEP=file\n")
    sub = tmp_path / "a" / "b"; sub.mkdir(parents=True)
    monkeypatch.delenv("VE_TEST_KEY", raising=False)
    monkeypatch.setenv("VE_KEEP", "env")
    load_dotenv(str(sub))
    import os
    assert os.environ["VE_TEST_KEY"] == "abc" and os.environ["VE_KEEP"] == "env"
    monkeypatch.delenv("VE_TEST_KEY")
