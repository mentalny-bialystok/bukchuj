"""Backtesting for the Route B value strategy (see strategy.py)."""
from .data import Match, load, parse_date, season_of, soft_books
from .engine import Result, configs, grid, odds_band, run, summarise_bets
from .report import format_result, format_walkforward, verdict, write_equity_csv
from .strategy import MARKETS, Bet, StrategyConfig, bets_for_match, generate_bets
from .walkforward import Fold, WalkForwardResult, holdout, walk_forward

__all__ = [
    "Match", "load", "parse_date", "season_of", "soft_books",
    "Result", "configs", "grid", "odds_band", "run", "summarise_bets",
    "format_result", "format_walkforward", "verdict", "write_equity_csv",
    "MARKETS", "Bet", "StrategyConfig", "bets_for_match", "generate_bets",
    "Fold", "WalkForwardResult", "holdout", "walk_forward",
]
