"""Locate monorepo packages (selector, data_layer) for local dev and Railway."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent


def find_repo_root() -> Path:
    """Return a directory containing both `selector/` and `data_layer/`.

    Local dev: repo root (parent of backend/).
    Railway (root dir = backend/): vendored copies live inside backend/.
    """
    env_root = os.environ.get("REPO_ROOT", "").strip()
    candidates = [
        Path(env_root) if env_root else None,
        _BACKEND.parent,   # stock_ai/ when running from backend/
        _BACKEND,          # backend/ when selector+data_layer vendored here
    ]
    for root in candidates:
        if root and (root / "selector").is_dir() and (root / "data_layer").is_dir():
            return root.resolve()
    raise RuntimeError(
        "Cannot find selector/ and data_layer/. "
        "Locally, run from the repo with backend/ as cwd. "
        "On Railway, ensure backend/scripts/railway_build.sh ran during deploy."
    )


def ensure_repo_on_path() -> Path:
    root = find_repo_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root
