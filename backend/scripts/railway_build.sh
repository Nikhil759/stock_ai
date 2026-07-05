#!/usr/bin/env bash
# Railway build hook (root directory = backend/).
# Runtime image only has backend/ files; full git clone exists at build time.
set -euo pipefail

BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
MONOREPO="$(cd "$BACKEND/.." && pwd)"

echo "[railway_build] backend=$BACKEND monorepo=$MONOREPO"

vendor() {
  local name="$1"
  local src="$MONOREPO/$name"
  local dst="$BACKEND/$name"
  if [ -d "$src" ]; then
    rm -rf "$dst"
    cp -a "$src" "$dst"
    echo "[railway_build] vendored $name"
  elif [ -d "$dst" ]; then
    echo "[railway_build] $name already in backend/"
  else
    echo "[railway_build] ERROR: missing $name"
    exit 1
  fi
}

vendor selector
vendor data_layer

if [ -f "$MONOREPO/nifty200.json" ]; then
  cp "$MONOREPO/nifty200.json" "$BACKEND/nifty200.json"
  echo "[railway_build] copied nifty200.json"
fi

pip install -r "$BACKEND/requirements.txt"
echo "[railway_build] done"
