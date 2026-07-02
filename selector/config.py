"""Central config for the selector. Edit budget/funnel/model tunables here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTENTIONS_DIR = ROOT / "intentions"   # <strategy>_<date>.json handoff files
LOG_DIR = ROOT / "logs"                # <strategy>_<date>.log — full DEBUG trace of every run

# --- budget / sizing (selector-only account view; the fund manager owns the real one) ---
TEST_BUDGET = 10000          # INR, per bot
PER_STOCK_CAP_PCT = 40       # max % of budget in a single pick

# --- Phase 1 funnel ---
FUNNEL_MAX_SURVIVORS = 30    # cap per strategy before the LLM pass

# --- Phase 2/3 LLM ---
GEMINI_MODEL = "gemini-2.5-flash"   # matches backend/llm.py's existing default
