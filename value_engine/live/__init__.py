"""Live odds scanning and bet logging (recommend + log only; never places bets)."""
from .betlog import BetLog, capture_closing, verdict
from .odds_api import DEFAULT_SHARP_BOOKS, Event, OddsAPI, OddsAPIError, parse_events
from .scanner import ScanConfig, ValueBet, scan

__all__ = ["BetLog", "capture_closing", "verdict", "DEFAULT_SHARP_BOOKS", "Event", "OddsAPI",
           "OddsAPIError", "parse_events", "ScanConfig", "ValueBet", "scan"]
