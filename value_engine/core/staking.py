"""Edge and stake sizing."""
from __future__ import annotations

from dataclasses import dataclass


def edge(fair_p: float, odds: float) -> float:
    """Expected return per unit staked: fair_p * odds - 1."""
    return fair_p * odds - 1.0


def fair_odds(fair_p: float) -> float:
    return 1.0 / fair_p


def kelly_fraction(fair_p: float, odds: float) -> float:
    """Full-Kelly fraction of bankroll for a single bet. 0 if no edge."""
    e = edge(fair_p, odds)
    return max(0.0, e / (odds - 1.0))


@dataclass(frozen=True)
class StakingRules:
    kelly: float = 0.25          # fraction of full Kelly
    edge_shrink: float = 0.6     # discount the measured edge for estimation error
    max_stake_pct: float = 0.02  # never stake more than this share of bankroll
    min_stake: float = 0.0       # bookmaker minimum; below this, no bet
    round_to: float = 0.5        # round stakes to look like a normal punter


def stake(bankroll: float, fair_p: float, odds: float, rules: StakingRules = StakingRules()) -> float:
    """Shrunk, fractional, capped Kelly stake, rounded down."""
    e = edge(fair_p, odds) * rules.edge_shrink
    if e <= 0:
        return 0.0
    f = rules.kelly * e / (odds - 1.0)
    s = bankroll * min(f, rules.max_stake_pct)
    if rules.round_to > 0:
        s = (s // rules.round_to) * rules.round_to
    return s if s >= max(rules.min_stake, 1e-9) else 0.0


def clv(odds_taken: float, closing_fair_p: float) -> float:
    """Closing line value: expected return of the bet priced at the de-vigged close."""
    return odds_taken * closing_fair_p - 1.0
