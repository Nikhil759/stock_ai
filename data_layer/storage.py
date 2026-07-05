"""
Storage: current dossiers live as JSON files (source of truth the agents read);
history lives in append-only SQLite (rarely read, but the backbone for
paper-trading analysis and 'what did we see when we bought this?').
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH, get_dossier_dir
from .dossier import Dossier


# ---------- JSON files (current dossiers) ----------

def save_dossier(dossier: Dossier) -> Path:
    dossier_dir = get_dossier_dir()
    dossier_dir.mkdir(parents=True, exist_ok=True)
    path = dossier_dir / f"{dossier.meta.ticker}.json"
    path.write_text(dossier.to_json())
    return path


def load_dossier(ticker: str) -> Dossier | None:
    path = get_dossier_dir() / f"{ticker}.json"
    if not path.exists():
        return None
    return Dossier.from_json(path.read_text())


def load_all_dossiers() -> list[Dossier]:
    dossier_dir = get_dossier_dir()
    if not dossier_dir.exists():
        return []
    out = []
    for p in sorted(dossier_dir.glob("*.json")):
        try:
            out.append(Dossier.from_json(p.read_text()))
        except Exception as e:
            print(f"[storage] skipping unreadable {p.name}: {e}")
    return out


# ---------- SQLite (append-only history) ----------

def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dossier_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            as_of TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            price REAL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snap_ticker_date ON dossier_snapshots(ticker, as_of)"
    )
    conn.commit()
    conn.close()


def append_snapshot(dossier: Dossier) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO dossier_snapshots (ticker, as_of, snapshot, price, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            dossier.meta.ticker,
            dossier.meta.as_of,
            dossier.meta.snapshot,
            dossier.fundamentals.price,
            dossier.to_json(indent=0),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
