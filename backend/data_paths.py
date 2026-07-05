"""Persistent data locations — local dev vs Railway volume."""

from __future__ import annotations

import os
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND.parent


def get_data_dir() -> Path:
    """Root for bot.db and intentions/.

    On Railway with a volume, RAILWAY_VOLUME_MOUNT_PATH is injected automatically.
    Override with WOLF_DATA_DIR for local testing against a fixed data folder.
  """
    for key in ("WOLF_DATA_DIR", "RAILWAY_VOLUME_MOUNT_PATH"):
        val = os.environ.get(key, "").strip()
        if val:
            return Path(val)
    return _REPO_ROOT


def get_db_path() -> Path:
    data_dir = get_data_dir()
    if data_dir == _REPO_ROOT:
        return _BACKEND / "bot.db"
    return data_dir / "bot.db"


def get_intentions_dir() -> Path:
    return get_data_dir() / "intentions"
