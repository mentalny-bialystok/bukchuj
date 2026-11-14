import math

import pytest

from value_engine.core import (METHODS, StakingRules, bets_needed, clv, consensus, devig,
                               disagreement, edge, kelly_fraction, margin, stake, tstat)

MARKETS = [[2.4, 3.4, 3.1], [1.285, 3.6], [1.3, 5.5, 10.0], [1.05, 15.0, 30.0], [1.95, 1.95]]


@pytest.mark.parametrize("method", sorted(METHODS))
@pytest.mark.parametrize("odds", MARKETS)
def test_devig_sums_to_one_and_valid(method, odds):
    p = devig(odds, method)
    assert math.isclose(sum(p), 1.0, abs_tol=1e-9)
    assert all(0 < x < 1 for x in p)


@pytest.mark.parametrize("method", sorted(METHODS))
def test_symmetric_market_is_fifty_fifty(method):
    assert devig([1.95, 1.95], method) == pytest.approx([0.5, 0.5])


def test_known_values():
    # published worked example: 1.285 / 3.60
    assert [round(1 / x, 2) for x in devig([1.285, 3.6], "power")] == [1.32, 4.13]
    assert [round(1 / x, 2) for x in devig([1.285, 3.6], "shin")] == [1.33, 4.0]


def test_longshot_bias_ordering():
    odds = [1.3, 5.5, 10.0]
    m, p, s = devig(odds, "multiplicative"), devig(odds, "power"), devig(odds, "shin")
    assert p[2] < s[2] < m[2]  # power and shin strip more from the longshot


def test_margin():
    assert margin([1.95, 1.95]) == pytest.approx(2 / 1.95 - 1)


def test_bad_inputs():
    with pytest.raises(ValueError):
        devig([1.0, 2.0])
    with pytest.raises(ValueError):
        devig([2.0])
    with pytest.raises(ValueError):
        devig([2.0, 2.0], "magic")


def test_consensus_and_disagreement():
    a, b = [2.0, 3.6, 3.9], [2.05, 3.5, 3.8]
    c = consensus([a, b])
    assert math.isclose(sum(c), 1.0)
    assert consensus([a, a]) == pytest.approx(devig(a))
    assert disagreement([a, a]) == pytest.approx(0)
    assert disagreement([a, b]) > 0


def test_edge_and_kelly():
    assert edge(0.5, 2.2) == pytest.approx(0.1)
    assert kelly_fraction(0.5, 2.2) == pytest.approx(0.1 / 1.2)
    assert kelly_fraction(0.4, 2.0) == 0


def test_stake_rules():
    r = StakingRules(kelly=0.25, edge_shrink=1.0, max_stake_pct=1.0, round_to=0)
    assert stake(1000, 0.5, 2.2, r) == pytest.approx(1000 * 0.25 * 0.1 / 1.2)
    capped = StakingRules(kelly=1, edge_shrink=1, max_stake_pct=0.02, round_to=0)
    assert stake(1000, 0.6, 2.2, capped) == pytest.approx(20)
    assert stake(1000, 0.4, 2.0) == 0
    assert stake(50, 0.5, 2.1, StakingRules(min_stake=1.0)) == 0  # tiny bankroll, under minimum
    assert stake(1000, 0.5, 2.2, StakingRules(round_to=0.5)) % 0.5 == 0


def test_clv():
    assert clv(2.2, 1 / 2.0) == pytest.approx(0.1)


def test_stats():
    assert tstat([1, 1, 1, 1.0001]) > 3
    assert abs(tstat([1, -1, 1, -1, 1, -1])) < 1
    assert 3000 < bets_needed(0.03, 2.0) < 6000
