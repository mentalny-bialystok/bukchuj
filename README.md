# value-engine

A paper-trading-first sports betting **value engine**. It doesn't predict matches. It uses a sharp bookmaker's prices (Pinnacle, Betfair Exchange) as the "true" probability and flags soft bookmakers whose prices are out of line, then judges itself by **closing line value (CLV)**.

> ⚠️ This tool only recommends and logs. It never places bets. Bookmakers limit or close winning accounts. Paper trade first and only ever stake money you can afford to lose.

## How it works

1. **De-vig** the sharp odds to remove the bookmaker margin → fair probability (`multiplicative`, `additive`, `power`, `shin`, `odds_ratio`; blends multiple sharp sources in log-odds space).
2. **Edge** = fair_p × soft_odds − 1. Bet only above a threshold, with confidence filters (sharp margin, sharp-source disagreement, odds range, time to kick-off).
3. **Stake** with shrunk, fractional, capped Kelly.
4. **Grade** with CLV = odds_taken × closing_fair_p − 1. CLV confirms an edge in hundreds of bets; profit needs thousands.

## Install

```bash
pip install -e ".[dev]"
pytest
```

## Usage

```bash
# Manual mode (no API key)
ve devig 2.10 3.40 3.60 --all
ve check --sharp 2.10,3.40,3.60 --pick 0 --soft 2.30 --bankroll 1000

# Backtest on football-data.co.uk CSVs (2019/20+ have Pinnacle closing odds)
ve backtest data/*.csv --book B365 --walk-forward

# Live scanning (free key from the-odds-api.com)
export ODDS_API_KEY=...
ve sports
ve scan soccer_epl --bankroll 1000 --log --yes

# Bet log
ve log list --pending
ve log close 1 2.05,3.50,3.80     # sharp closing odds, all outcomes
ve log settle 1 win
ve log stats --paper
ve log export bets.csv
```

### Lithuania workflow (books not covered by the API)

Lithuania's licensed books (Top Sport, OlyBet, Betsafe, Optibet, Cbet) aren't in The Odds API, and Pinnacle/1xBet/bet365 are blacklisted there (their *prices* are still fine as reference data). So:

```bash
ve targets soccer_epl --min-edge 0.03 --csv targets.csv
```

prints, per outcome, the fair odds and the **minimum odds worth taking**. Check your licensed books; if one offers at least the target, log it as a paper bet with the sharp odds shown:

```bash
ve log add --sharp 2.05,3.50,3.90 --pick 0 --odds 2.20 --stake 1 --book optibet --event "Arsenal v Chelsea"
```

Then record closing odds before kick-off (`ve log close`) to measure CLV. A few hundred bets will show whether these books are soft enough to be worth it.

Browser versions of the manual checker and backtester are in `web/`.

## Reading results honestly

- **Trust the walk-forward (out-of-sample) number.** Settings are tuned only on earlier seasons and tested on later ones.
- **CLV t > 3** is strong evidence; **> 2** promising. Profit can be negative for hundreds of bets even with a real edge.
- `--book Max` (best price across all books) is optimistic. Test a single book such as `B365`.
- Historical "pre-closing" odds are single snapshots; live availability is messier.

## Layout

```
value_engine/core/      de-vig, staking, statistics
value_engine/backtest/  football-data loader, strategy, engine, walk-forward, reports
value_engine/live/      Odds API client (cached), scanner, SQLite bet log
value_engine/cli.py     `ve` command
web/                    standalone HTML tools
```

## Background reading

- Kaunitz, Zhong & Kreiner (2017), *Beating the bookies with their own numbers* — arXiv:1710.02824
- Joseph Buchdahl, *Wisdom of the Crowd* — football-data.co.uk
- Hubáček & Šír (2023), *Beating the market with a bad predictive model* — IJF
- Walsh & Joshi (2024), *Machine learning for sports betting: accuracy or calibration?*
