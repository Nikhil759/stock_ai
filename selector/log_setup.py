"""
Central logging setup for the selector package. Every module gets its
logger via `logging.getLogger(__name__)` as usual -- this just configures
where/how those records show up.

Console shows INFO by default (the human-readable trail: funnel survivors,
per-stock verdicts, final picks/clamps). Pass verbose=True (or run.py's
--verbose flag) to also print DEBUG on the console -- every dossier's
per-check funnel result, raw LLM request/response sizes, etc.

The log FILE under logs/ always gets full DEBUG detail regardless of the
console level, so a local test run can be replayed/inspected afterwards
even if you forgot --verbose.
"""
from __future__ import annotations

import logging
import sys
from datetime import date

from .config import LOG_DIR

_configured = False


def setup_logging(strategy: str | None = None, verbose: bool = False, to_file: bool = True) -> logging.Logger:
    global _configured

    root = logging.getLogger("selector")
    root.setLevel(logging.DEBUG)  # handlers below do the actual filtering
    root.propagate = False

    if _configured:
        # already wired up in this process (e.g. run.py already called this) --
        # just adjust the console verbosity if asked.
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(logging.DEBUG if verbose else logging.INFO)
        return root

    root.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)-7s] %(name)-22s %(message)s", datefmt="%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(console)

    if to_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{strategy}_{date.today().isoformat()}.log" if strategy else f"selector_{date.today().isoformat()}.log"
        file_handler = logging.FileHandler(LOG_DIR / name)
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)
        root.info("full debug log for this run -> %s", LOG_DIR / name)

    _configured = True
    return root
