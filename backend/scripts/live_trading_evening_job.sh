#!/bin/zsh
# Mac live-trading agent: evening target/stop exits for live wolves only.
set -euo pipefail

REPO="/Users/nikhilbansal/Projects/stock_ai"
PY="$REPO/.venv/bin/python"
LOG="/tmp/wolfcapital-live-trading-evening.log"

log() {
  echo "$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST') $*" >>"$LOG"
}

export WOLF_LIVE_ORDERS_ENABLED=1

cd "$REPO/backend"
log "live trading evening job start"

if ! "$PY" -m scripts.refresh_kite_token --sync >>"$LOG" 2>&1; then
  code=$?
  log "kite token sync failed exit=$code — aborting live evening job"
  exit "$code"
fi

if ! "$PY" -m scripts.run_live_trading_evening >>"$LOG" 2>&1; then
  code=$?
  log "live trading evening failed exit=$code"
  exit "$code"
fi

log "live trading evening job ok"
