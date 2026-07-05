"""Deterministic fund manager — paper trading executor per bot."""

from fund_manager.deploy import morning_deploy, print_deploy_summary
from fund_manager.evening import run_evening_job, print_evening_summary
from fund_manager.kite_auth import get_kite, login_url
from fund_manager.ledger import BotLedger
from fund_manager.prices import get_prices
from fund_manager.redeploy import handle_freed_cash

__all__ = [
    "get_kite",
    "login_url",
    "get_prices",
    "BotLedger",
    "morning_deploy",
    "print_deploy_summary",
    "run_evening_job",
    "print_evening_summary",
    "handle_freed_cash",
]
