#!/bin/zsh
# Mac live-trading agent: deferred birth orders + live wolf daily review.
# Requires WOLF_LIVE_ORDERS_ENABLED=1 and whitelisted IP on Zerodha.
set -euo pipefail

REPO="/Users/nikhilbansal/Projects/stock_ai"
PY="$REPO/.venv/bin/python"
LOG="/tmp/wolfcapital-live-trading.log"

log() {
  echo "$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST') $*" >>"$LOG"
}

export WOLF_LIVE_ORDERS_ENABLED=1

cd "$REPO/backend"
log "live trading daily job start"

if ! "$PY" -m scripts.refresh_kite_token --sync >>"$LOG" 2>&1; then
  code=$?
  log "kite token sync failed exit=$code — aborting live job"
  exit "$code"
fi

if ! "$PY" -m scripts.run_live_trading_daily >>"$LOG" 2>&1; then
  code=$?
  log "live trading daily failed exit=$code"
  exit "$code"
fi

log "live trading daily job ok"
