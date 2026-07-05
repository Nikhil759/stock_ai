"""Configure verbose console logging for dossier screening."""
from __future__ import annotations

import logging
import sys


def setup_app_logging(verbose: bool = True) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
        root.addHandler(handler)
    root.setLevel(level)

    for name in (
        "dossier_screen",
        "selector",
        "selector.pipeline",
        "selector.funnel",
        "selector.llm.scoring",
        "selector.llm.final",
        "selector.llm.client",
        "selector.reasoning_log",
    ):
        logging.getLogger(name).setLevel(level)
