#!/bin/zsh
# Kite TOTP refresh + Supabase sync for local launchd/cron.
# LAUNCHD_CATCHUP=1 → weekday catch-up polls only (6:00–11:59 AM IST).
set -euo pipefail

REPO="/Users/nikhilbansal/Desktop/stock_ai"
PY="$REPO/.venv/bin/python"
LOG="/tmp/wolfcapital-kite-token.log"

log() {
  echo "$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S IST') $*" >>"$LOG"
}

if [[ "${LAUNCHD_CATCHUP:-0}" == "1" ]]; then
  DOW=$(TZ=Asia/Kolkata date +%u)   # 1=Mon … 7=Sun
  HOUR=$(TZ=Asia/Kolkata date +%H)
  if [[ "$DOW" -gt 5 ]] || [[ "$HOUR" -lt 6 ]] || [[ "$HOUR" -gt 11 ]]; then
    exit 0
  fi
fi

cd "$REPO/backend"
log "start sync (catchup=${LAUNCHD_CATCHUP:-0})"
if "$PY" -m scripts.refresh_kite_token --sync >>"$LOG" 2>&1; then
  log "sync ok"
else
  code=$?
  log "sync failed exit=$code"
  exit "$code"
fi
