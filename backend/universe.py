"""NSE index universes — Nifty 200 default scan pool."""

import csv
import json
from pathlib import Path
from urllib.request import Request, urlopen

from data import NIFTY_50

ROOT = Path(__file__).resolve().parent
NIFTY200_JSON = ROOT / "nifty200.json"
NSE_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"

# Technical strategies: liquid large/mid cap subset (same pool for now)
TECH_UNIVERSE = None  # resolved at load


def _load_json_symbols() -> list[str]:
    if NIFTY200_JSON.exists():
        return json.loads(NIFTY200_JSON.read_text(encoding="utf-8"))
    return list(NIFTY_50)


def refresh_nifty200_from_nse() -> list[str]:
    """Download latest Nifty 200 list from NSE archives."""
    req = Request(NSE_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    syms = []
    for row in csv.DictReader(text.splitlines()):
        sym = (row.get("Symbol") or "").strip()
        if sym:
            syms.append(sym)
    if syms:
        NIFTY200_JSON.write_text(json.dumps(syms, indent=2), encoding="utf-8")
    return syms


def get_nifty200() -> list[str]:
    global TECH_UNIVERSE
    syms = _load_json_symbols()
    TECH_UNIVERSE = syms
    return syms


def get_universe(strategy: str) -> list[str]:
    """Return symbol list for a strategy scan."""
    if strategy == "value":
        return get_nifty200()
    if strategy in ("winners", "box", "dip"):
        return get_nifty200()
    return get_nifty200()
