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

# Web service may set DOSSIER_DIR after syncing from data-layer-cron internal API.
def get_dossier_dir() -> Path:
    override = os.environ.get("DOSSIER_DIR", "").strip()
    if override:
        return Path(override)
    return STATE_DIR / "dossiers"


# Back-compat alias for code that imported DOSSIER_DIR directly.
DOSSIER_DIR = get_dossier_dir()

DB_PATH = STATE_DIR / "data" / "history.sqlite"
CACHE_DIR = STATE_DIR / "data" / "cache"      # parent for fundamentals/news/events per-day caches

# --- internal dossier API (data-layer-cron serve + stock_ai sync) ---
DOSSIER_API_TOKEN = os.environ.get("DOSSIER_API_TOKEN", "").strip()
BUILD_CRON = os.environ.get("DOSSIER_BUILD_CRON", "30 2 * * 1-5")  # UTC, weekdays pre-open
POST_CLOSE_BUILD_CRON = os.environ.get(
    "DOSSIER_POST_CLOSE_CRON", "30 10 * * 1-5"
)  # UTC, weekdays ~4:00 PM IST post-close refresh

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
DOSSIER_VERSION = "1.3"
