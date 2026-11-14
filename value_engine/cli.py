"""`ve` command line: de-vig odds, check a price, scan for value, keep a bet log.

This tool never places bets. It recommends and logs; you decide and place bets yourself.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

import click

from value_engine.core import METHODS, StakingRules, devig, edge, margin, stake

PAPER_REMINDER = ("Reminder: this is PAPER TRADING by default - log bets, track CLV, risk nothing. "
                  "Bookmakers limit or close accounts that win consistently.")


# ---------------------------------------------------------------- helpers
def table(rows: list[list], headers: list[str]) -> str:
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]

    def fmt(r):
        return "  ".join(c.rjust(w) if _numeric(c) else c.ljust(w) for c, w in zip(r, widths)).rstrip()
    lines = [fmt(cells[0]), "  ".join("-" * w for w in widths)]
    return "\n".join(lines + [fmt(r) for r in cells[1:]])


def _numeric(s: str) -> bool:
    try:
        float(s.replace("%", "").replace("+", "").replace("€", ""))
        return True
    except ValueError:
        return False


def pct(x, signed=True) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def num(x, d=2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:.{d}f}"


def parse_odds_list(s: str) -> list[float]:
    try:
        vals = [float(x) for x in s.replace(" ", "").split(",") if x]
    except ValueError:
        raise click.BadParameter(f"expected comma-separated decimal odds like 2.10,3.40,3.60, got {s!r}")
    if len(vals) < 2 or any(v <= 1 for v in vals):
        raise click.BadParameter("need at least two decimal odds, each greater than 1")
    return vals


def fmt_delta(td: timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    if mins < 0:
        return "started"
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    return f"{h}h{m:02d}" if h < 48 else f"{h // 24}d{h % 24}h"


def rules_from(kelly, max_stake_pct, min_stake=0.0, round_to=0.5) -> StakingRules:
    return StakingRules(kelly=kelly, max_stake_pct=max_stake_pct, min_stake=min_stake, round_to=round_to)


method_opt = click.option("--method", type=click.Choice(sorted(METHODS)), default="power",
                          show_default=True, help="How to remove the bookmaker margin.")
db_opt = click.option("--db", default=lambda: os.environ.get("VE_DB", "data/bets.db"),
                      show_default="data/bets.db", help="Bet log database file (or env VE_DB).")


@click.group()
@click.version_option(package_name="value-engine")
def main():
    """Value engine: find soft-bookmaker prices that beat the sharp line. Paper trading first."""


# ---------------------------------------------------------------- devig
@main.command("devig")
@click.argument("odds", nargs=-1, type=float, required=True)
@method_opt
@click.option("--all", "all_methods", is_flag=True, help="Show every de-vig method side by side.")
def devig_cmd(odds, method, all_methods):
    """Turn bookmaker ODDS (e.g. 2.10 3.40 3.60) into fair probabilities."""
    odds = list(odds)
    try:
        m = margin(odds)
    except ValueError as e:
        raise click.UsageError(str(e))
    click.echo(f"Bookmaker margin: {m:.2%}")
    methods = sorted(METHODS) if all_methods else [method]
    headers = ["#", "odds"] + [f"{k} p" for k in methods] + ([f"{methods[0]} fair odds"] if not all_methods else [])
    fairs = {k: devig(odds, k) for k in methods}
    rows = []
    for i, o in enumerate(odds):
        r = [i, num(o)] + [pct(fairs[k][i], False) for k in methods]
        if not all_methods:
            r.append(num(1 / fairs[method][i]))
        rows.append(r)
    click.echo(table(rows, headers))


# ---------------------------------------------------------------- check
@main.command("check")
@click.option("--sharp", required=True, help="Sharp odds for ALL outcomes, e.g. 2.10,3.40,3.60")
@click.option("--pick", type=int, required=True, help="Which outcome you'd bet (0 = first).")
@click.option("--soft", type=float, required=True, help="The soft bookmaker's price for that outcome.")
@click.option("--bankroll", type=float, default=0.0, help="Your bankroll, to suggest a stake.")
@click.option("--min-edge", type=float, default=0.03, show_default=True)
@click.option("--kelly", type=float, default=0.25, show_default=True)
@click.option("--max-stake-pct", type=float, default=0.02, show_default=True)
@method_opt
def check_cmd(sharp, pick, soft, bankroll, min_edge, kelly, max_stake_pct, method):
    """Manually check one price against a sharp market (no API needed)."""
    odds = parse_odds_list(sharp)
    if not 0 <= pick < len(odds):
        raise click.BadParameter(f"--pick must be between 0 and {len(odds) - 1}")
    if soft <= 1:
        raise click.BadParameter("--soft must be decimal odds > 1")
    fair = devig(odds, method)[pick]
    e = edge(fair, soft)
    click.echo(f"Sharp margin:     {margin(odds):.2%}")
    click.echo(f"Fair probability: {fair:.2%}  (fair odds {1 / fair:.2f})")
    click.echo(f"Your price:       {soft:.2f}")
    click.echo(f"Edge:             {e:+.2%}")
    if bankroll > 0:
        s = stake(bankroll, fair, soft, rules_from(kelly, max_stake_pct))
        click.echo(f"Suggested stake:  {s:.2f} of {bankroll:.2f}")
        if s == 0 and e > 0:
            click.echo("  (Kelly stake rounds below 0.50 on this bankroll - fine for paper trading; "
                       "log a notional 1.00 to track it.)")
    if e >= 0.25:
        v = "Suspiciously large edge - the price is probably stale or a mistake. Double-check before logging."
    elif e >= min_edge:
        v = "VALUE: price beats the fair line by more than your threshold."
    elif e > 0:
        v = "Small edge, below your threshold - skip (margin for error is too thin)."
    else:
        v = "No value: the price is at or below fair."
    click.echo(f"Verdict: {v}")


# ---------------------------------------------------------------- sports / scan
def _client(api_key, cache_ttl=600.0):
    from value_engine.live.odds_api import OddsAPI, OddsAPIError
    try:
        return OddsAPI(api_key, cache_ttl=cache_ttl)
    except OddsAPIError as e:
        raise click.ClickException(str(e))


def _quota(client):
    src = " (from cache, no credits used)" if client.last_from_cache else ""
    if client.requests_remaining is not None:
        click.echo(f"API credits: {client.requests_remaining} remaining, {client.requests_used} used{src}")
    elif src:
        click.echo(f"API credits:{src}")


@main.command("sports")
@click.option("--api-key", envvar="ODDS_API_KEY", help="The Odds API key (or env ODDS_API_KEY).")
@click.option("--all", "all_sports", is_flag=True, help="Include out-of-season sports.")
def sports_cmd(api_key, all_sports):
    """List sport keys you can scan (free call)."""
    from value_engine.live.odds_api import OddsAPIError
    c = _client(api_key)
    try:
        sports = c.list_sports(all_sports)
    except OddsAPIError as e:
        raise click.ClickException(str(e))
    rows = [[s["key"], s.get("title", ""), s.get("group", "")] for s in sports]
    click.echo(table(rows, ["key", "title", "group"]))
    _quota(c)


@main.command("scan")
@click.argument("sport_key")
@click.option("--api-key", envvar="ODDS_API_KEY", help="The Odds API key (or env ODDS_API_KEY).")
@click.option("--regions", default="eu", show_default=True, help="Odds API regions (each costs 1 credit).")
@click.option("--sharp", default="pinnacle,betfair_ex_eu", show_default=True, help="Sharp book keys.")
@click.option("--book", "books", multiple=True, help="Only consider these soft books (repeatable).")
@click.option("--min-edge", type=float, default=0.03, show_default=True)
@click.option("--max-edge", type=float, default=0.25, show_default=True)
@click.option("--min-odds", type=float, default=1.2, show_default=True)
@click.option("--max-odds", type=float, default=10.0, show_default=True)
@click.option("--max-hours", type=float, default=72.0, show_default=True, help="Only events starting within this.")
@click.option("--commission", type=float, default=0.0, help="Exchange commission, e.g. 0.05.")
@click.option("--bankroll", type=float, default=0.0, help="Bankroll for suggested stakes.")
@click.option("--kelly", type=float, default=0.25, show_default=True)
@click.option("--max-stake-pct", type=float, default=0.02, show_default=True)
@click.option("--cache-ttl", type=float, default=600.0, show_default=True, help="Seconds to reuse cached odds.")
@method_opt
@click.option("--log", "do_log", is_flag=True, help="Log chosen rows to the bet log as paper bets.")
@click.option("--yes", is_flag=True, help="With --log: log every row without asking.")
@db_opt
def scan_cmd(sport_key, api_key, regions, sharp, books, min_edge, max_edge, min_odds, max_odds, max_hours,
             commission, bankroll, kelly, max_stake_pct, cache_ttl, method, do_log, yes, db):
    """Scan SPORT_KEY (e.g. soccer_epl) for soft prices above the sharp fair line."""
    from value_engine.live.odds_api import OddsAPI, OddsAPIError
    from value_engine.live.scanner import ScanConfig, scan
    c = _client(api_key, cache_ttl)
    click.echo(f"Fetching {sport_key} odds (costs ~{OddsAPI.estimated_cost(regions)} credit(s) unless cached)...")
    try:
        events = c.get_odds(sport_key, regions=regions)
    except OddsAPIError as e:
        raise click.ClickException(str(e))
    cfg = ScanConfig(sharp_books=tuple(s for s in sharp.split(",") if s), soft_books=list(books) or None,
                     devig_method=method, min_edge=min_edge, max_edge=max_edge, min_odds=min_odds,
                     max_odds=max_odds, max_hours_to_start=max_hours, exchange_commission=commission)
    rules = rules_from(kelly, max_stake_pct)
    vbs = scan(events, cfg, bankroll, rules)
    with_sharp = sum(any(b in ev.markets.get("h2h", {}) for b in cfg.sharp_books) for ev in events)
    click.echo(f"{len(events)} events, {with_sharp} with sharp prices, {len(vbs)} value bet(s).")
    if vbs:
        rows = [[i, v.event.label, v.outcome, v.book, num(v.odds), num(v.fair_odds), pct(v.edge),
                 v.sharp_sources, num(v.stake) if bankroll else "-", fmt_delta(v.starts_in)]
                for i, v in enumerate(vbs)]
        click.echo(table(rows, ["#", "event", "pick", "book", "odds", "fair", "edge", "sharps", "stake", "starts"]))
    _quota(c)
    if do_log and vbs:
        from value_engine.live.betlog import BetLog
        if yes:
            chosen = list(range(len(vbs)))
        else:
            ans = click.prompt("Rows to log as paper bets (e.g. 0,2 or 'all', blank = none)",
                               default="", show_default=False)
            chosen = list(range(len(vbs))) if ans.strip().lower() == "all" else \
                [int(x) for x in ans.replace(" ", "").split(",") if x.isdigit() and int(x) < len(vbs)]
        with BetLog(db) as log:
            for i in chosen:
                bid = log.add_value_bet(vbs[i], stake=vbs[i].stake or 1.0)
                click.echo(f"Logged paper bet #{bid}: {vbs[i].outcome} @ {vbs[i].odds:.2f} ({vbs[i].book})")
    click.echo(PAPER_REMINDER)


LT_BOOKS = "Top Sport, OlyBet, Betsafe, Optibet, Cbet"


@main.command("targets")
@click.argument("sport_key")
@click.option("--api-key", envvar="ODDS_API_KEY", help="The Odds API key (or env ODDS_API_KEY).")
@click.option("--sharp", default="pinnacle,betfair_ex_eu", show_default=True, help="Sharp book keys.")
@click.option("--min-edge", type=float, default=0.03, show_default=True)
@click.option("--min-odds", type=float, default=1.2, show_default=True)
@click.option("--max-odds", type=float, default=6.0, show_default=True)
@click.option("--max-hours", type=float, default=48.0, show_default=True, help="Only events starting within this.")
@click.option("--cache-ttl", type=float, default=600.0, show_default=True)
@click.option("--csv", "csv_path", type=click.Path(dir_okay=False), help="Also save the sheet as CSV.")
@method_opt
def targets_cmd(sport_key, api_key, sharp, min_edge, min_odds, max_odds, max_hours, cache_ttl, csv_path, method):
    """Target-odds sheet for bookmakers the API doesn't cover (e.g. Lithuanian books).

    For each outcome it shows the fair odds from the sharp books and the MINIMUM
    odds worth taking. If your bookmaker offers at least the target, that's a value bet:
    log it with `ve log add`, using the sharp odds shown.
    """
    from value_engine.live.odds_api import OddsAPI, OddsAPIError
    from value_engine.live.scanner import ScanConfig, targets
    c = _client(api_key, cache_ttl)
    click.echo(f"Fetching {sport_key} sharp odds (costs ~{OddsAPI.estimated_cost('eu')} credit unless cached)...")
    try:
        events = c.get_odds(sport_key, regions="eu", bookmakers=sharp)
    except OddsAPIError as e:
        raise click.ClickException(str(e))
    cfg = ScanConfig(sharp_books=tuple(s for s in sharp.split(",") if s), devig_method=method,
                     min_edge=min_edge, min_odds=min_odds, max_odds=max_odds, max_hours_to_start=max_hours)
    ts = targets(events, cfg)
    if not ts:
        click.echo("No events with usable sharp prices in that window.")
    else:
        rows = [[t.event.label, t.outcome, num(t.fair_odds), num(t.min_odds),
                 ",".join(f"{o:.2f}" for o in t.sharp_odds), fmt_delta(t.starts_in)] for t in ts]
        click.echo(table(rows, ["event", "outcome", "fair", "take if >=", "sharp odds (log these)", "starts"]))
        if csv_path:
            import csv
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["kickoff", "event", "outcome", "fair_odds", "min_odds", "sharp_odds"])
                for t in ts:
                    w.writerow([t.event.commence_time.isoformat(), t.event.label, t.outcome,
                                f"{t.fair_odds:.3f}", f"{t.min_odds:.3f}", ",".join(f"{o:.2f}" for o in t.sharp_odds)])
            click.echo(f"Saved {csv_path}")
    _quota(c)
    click.echo(f"Compare against your licensed books ({LT_BOOKS}). Prices move: re-run close to betting.")
    click.echo(PAPER_REMINDER)


# ---------------------------------------------------------------- log
@main.group("log")
def log_grp():
    """Your bet log: add, close, settle, stats."""


@log_grp.command("add")
@click.option("--sharp", help="Sharp odds for ALL outcomes at bet time, e.g. 2.10,3.40,3.60")
@click.option("--pick", type=int, help="Index of the outcome you bet (0 = first).")
@click.option("--odds", type=float, required=True, help="The odds you took.")
@click.option("--stake", type=float, required=True)
@click.option("--event", "event_label", default="", help="e.g. 'Arsenal v Chelsea'")
@click.option("--outcomes", help="Outcome names, comma-separated, matching --sharp order.")
@click.option("--book", default="")
@click.option("--sport", default="")
@click.option("--start", help="Kick-off time, ISO e.g. 2026-11-20T20:00Z")
@click.option("--real", is_flag=True, help="This was a REAL money bet (default: paper).")
@click.option("--notes")
@method_opt
@db_opt
def log_add(sharp, pick, odds, stake, event_label, outcomes, book, sport, start, real, notes, method, db):
    """Manually log a bet."""
    from value_engine.live.betlog import BetLog
    sharp_odds = parse_odds_list(sharp) if sharp else None
    fair_p = None
    names = [n.strip() for n in outcomes.split(",")] if outcomes else None
    if sharp_odds:
        if pick is None or not 0 <= pick < len(sharp_odds):
            raise click.BadParameter("--pick (0-based index into --sharp) is required with --sharp")
        if names and len(names) != len(sharp_odds):
            raise click.BadParameter("--outcomes must have as many names as --sharp prices")
        fair_p = devig(sharp_odds, method)[pick]
        names = names or [str(i) for i in range(len(sharp_odds))]
    commence = None
    if start:
        try:
            commence = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if commence.tzinfo is None:
                commence = commence.replace(tzinfo=timezone.utc)
        except ValueError:
            raise click.BadParameter("--start must be ISO time like 2026-11-20T20:00Z")
    with BetLog(db) as log:
        bid = log.add_bet(odds=odds, stake=stake, outcome=names[pick] if names and pick is not None else "",
                          outcome_index=pick, fair_p=fair_p, event_label=event_label, book=book, sport=sport,
                          commence_time=commence, sharp_odds=sharp_odds, outcome_names=names,
                          is_real=real, notes=notes)
    kind = "REAL" if real else "paper"
    extra = f", edge {pct(fair_p * odds - 1)}" if fair_p else ""
    click.echo(f"Logged {kind} bet #{bid}: {stake:.2f} @ {odds:.2f}{extra}")


@log_grp.command("list")
@click.option("--real/--paper", "real", default=None, help="Only real or only paper bets.")
@click.option("--pending", is_flag=True, help="Only unsettled bets.")
@db_opt
def log_list(real, pending, db):
    """Show logged bets."""
    from value_engine.live.betlog import BetLog
    with BetLog(db) as log:
        bets = log.list_bets(real=real, result="pending" if pending else None)
    if not bets:
        click.echo("No bets logged yet.")
        return
    rows = [[b["id"], (b["created_at"] or "")[:10], b["event_label"] or "", b["outcome"] or "", b["book"] or "",
             num(b["odds"]), num(b["stake"]), pct(b["edge"]), pct(b["clv"]), b["result"],
             num(b["pl"]) if b["pl"] is not None else "-", "real" if b["is_real"] else "paper"] for b in bets]
    click.echo(table(rows, ["id", "date", "event", "pick", "book", "odds", "stake", "edge", "clv",
                            "result", "p/l", "type"]))


@log_grp.command("close")
@click.argument("bet_id", type=int)
@click.argument("closing_odds")
@method_opt
@db_opt
def log_close(bet_id, closing_odds, method, db):
    """Record sharp CLOSING_ODDS (all outcomes, e.g. 2.05,3.50,3.80) for BET_ID."""
    from value_engine.live.betlog import BetLog
    with BetLog(db) as log:
        try:
            c = log.set_closing(bet_id, parse_odds_list(closing_odds), method)
        except (KeyError, ValueError) as e:
            raise click.ClickException(str(e))
    good = "you beat the closing line" if c > 0 else "the line moved against you"
    click.echo(f"Bet #{bet_id}: CLV {c:+.2%} ({good}).")


@log_grp.command("settle")
@click.argument("bet_id", type=int)
@click.argument("result", type=click.Choice(["win", "loss", "void", "pending"], case_sensitive=False))
@db_opt
def log_settle(bet_id, result, db):
    """Settle BET_ID as win, loss or void."""
    from value_engine.live.betlog import BetLog
    with BetLog(db) as log:
        try:
            pl = log.settle(bet_id, result)
        except KeyError as e:
            raise click.ClickException(str(e))
    click.echo(f"Bet #{bet_id} settled as {result}: P/L {pl:+.2f}")


@log_grp.command("stats")
@click.option("--real/--paper", "real", default=None, help="Only real or only paper bets (default all).")
@click.option("--bankroll", type=float, default=0.0, help="Starting bankroll, to show the current one.")
@db_opt
def log_stats(real, bankroll, db):
    """Performance summary and an honest verdict."""
    from value_engine.live.betlog import BetLog
    with BetLog(db) as log:
        s = log.stats(real=real, start_bankroll=bankroll)
    which = {None: "all", True: "real", False: "paper"}[real]
    click.echo(f"Bets ({which}):     {s['n_bets']}  (settled {s['n_settled']}, pending {s['n_pending']})")
    if bankroll:
        click.echo(f"Bankroll:        {s['bankroll']:.2f} (started {bankroll:.2f})")
    click.echo(f"Staked:          {s['staked']:.2f}")
    click.echo(f"Profit:          {s['profit']:+.2f}")
    click.echo(f"Yield:           {pct(s['yield'])}")
    click.echo(f"Avg edge at bet: {pct(s['avg_edge'])}")
    click.echo(f"Avg CLV:         {pct(s['avg_clv'])}  over {s['n_clv']} bets with closing odds")
    click.echo(f"CLV t-stat:      {num(s['clv_t'])}")
    click.echo(f"Beat close:      {pct(s['beat_close'], False)}")
    click.echo(f"Verdict: {s['verdict']}")
    click.echo("(Profit is mostly luck over a few hundred bets; CLV is the better guide to skill.)")


@log_grp.command("export")
@click.argument("out", type=click.Path(dir_okay=False))
@click.option("--real/--paper", "real", default=None)
@db_opt
def log_export(out, real, db):
    """Export bets to a CSV file."""
    from value_engine.live.betlog import BetLog
    with BetLog(db) as log:
        n = log.export_csv(out, real=real)
    click.echo(f"Exported {n} bets to {out}")


@log_grp.command("capture-closing")
@click.option("--api-key", envvar="ODDS_API_KEY")
@click.option("--within", type=float, default=30.0, show_default=True, help="Minutes before kick-off.")
@click.option("--grace", type=float, default=15.0, show_default=True, help="Minutes after kick-off still accepted.")
@click.option("--sharp", default="pinnacle,betfair_ex_eu", show_default=True)
@click.option("--regions", default="eu", show_default=True)
@method_opt
@db_opt
def log_capture(api_key, within, grace, sharp, regions, method, db):
    """Fetch sharp odds for pending bets about to start and store them as closing odds.

    Run this shortly before kick-off (e.g. from cron every 15 min). One credit per sport.
    """
    from value_engine.live.betlog import BetLog, capture_closing
    from value_engine.live.odds_api import OddsAPIError
    with BetLog(db) as log:
        if not log.pending_for_closing(within, grace):
            click.echo("No pending bets near kick-off; nothing fetched (no credits used).")
            return
        c = _client(api_key, 0)
        try:
            done = capture_closing(log, c, within, grace, tuple(sharp.split(",")), method, regions)
        except OddsAPIError as e:
            raise click.ClickException(str(e))
    for bid, v in done:
        click.echo(f"Bet #{bid}: closing captured, CLV {v:+.2%}")
    if not done:
        click.echo("Found bets near kick-off but no matching sharp prices.")
    _quota(c)


# ---------------------------------------------------------------- backtest
@main.command("backtest")
@click.argument("paths", nargs=-1, required=True)
@click.option("--book", default="Max", show_default=True, help="Soft book prefix in the CSVs (Max = best price).")
@method_opt
@click.option("--min-edge", type=float, default=0.03, show_default=True)
@click.option("--walk-forward", is_flag=True, help="Season-by-season out-of-sample test over min-edge.")
def backtest_cmd(paths, book, method, min_edge, walk_forward):
    """Backtest on football-data.co.uk CSV files (PATHS can be files, dirs or globs)."""
    try:
        import value_engine.backtest as bt
    except ImportError as e:
        raise click.ClickException(f"The backtest module isn't available yet ({e}).")
    loader = getattr(bt, "load_matches", None) or getattr(bt, "load", None)
    if loader is None:
        raise click.ClickException("value_engine.backtest has no load_matches/load function.")
    matches = loader(list(paths))
    if not matches:
        raise click.ClickException("No matches loaded - check the paths.")
    cfg = bt.StrategyConfig(soft_book=book, devig_method=method, min_edge=min_edge)
    click.echo(f"Loaded {len(matches)} matches.")
    if walk_forward:
        w = bt.walk_forward(matches, cfg, {"min_edge": [0.02, 0.03, 0.04, 0.05]})
        click.echo(bt.format_walkforward(w))
    else:
        click.echo(bt.format_result(bt.run(matches, cfg)))


if __name__ == "__main__":
    main()
