"""Central config for the data layer. Edit paths and tunables here."""
import os
from pathlib import Path

# --- paths ---
ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_FILE = ROOT / "nifty200.json"   # list of ticker names

# All *runtime state* (dossiers, sqlite history, per-day fetch caches) lives
# under STATE_DIR instead of being scattered repo-relative paths. Locally
# this is just the repo root (identical to before). On Railway, a service
# with a Volume attached gets RAILWAY_VOLUME_MOUNT_PATH injected automatically
# -- pointing STATE_DIR at it means one Volume persists everything a daily
# cron run needs (dossiers/, data/history.sqlite, data/cache/*) across
# invocations, since cron containers are otherwise ephemeral.
STATE_DIR = Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"]) if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") else ROOT

DOSSIER_DIR = STATE_DIR / "dossiers"          # one <TICKER>.json per stock
DB_PATH = STATE_DIR / "data" / "history.sqlite"
CACHE_DIR = STATE_DIR / "data" / "cache"      # parent for fundamentals/news/events per-day caches

# --- yfinance ---
YF_SUFFIX = ".NS"        # NSE suffix for Yahoo tickers (RELIANCE -> RELIANCE.NS)
HISTORY_PERIOD = "1y"    # how much daily history to pull
FETCH_WORKERS = 8        # parallel threads for the universe scan
FETCH_DELAY = 0.0        # optional per-request sleep (seconds) to be gentle

# --- market context tickers ---
NIFTY_TICKER = "^NSEI"
VIX_TICKER = "^INDIAVIX"

# --- VIX regime thresholds (India VIX) ---
VIX_CALM = 15
VIX_ELEVATED = 22

# --- schema version ---
DOSSIER_VERSION = "1.1"
