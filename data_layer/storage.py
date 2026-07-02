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

from .config import DOSSIER_DIR, DB_PATH
from .dossier import Dossier


# ---------- JSON files (current dossiers) ----------

def save_dossier(dossier: Dossier) -> Path:
    DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    path = DOSSIER_DIR / f"{dossier.meta.ticker}.json"
    path.write_text(dossier.to_json())
    return path


def load_dossier(ticker: str) -> Dossier | None:
    path = DOSSIER_DIR / f"{ticker}.json"
    if not path.exists():
        return None
    return Dossier.from_json(path.read_text())


def load_all_dossiers() -> list[Dossier]:
    if not DOSSIER_DIR.exists():
        return []
    out = []
    for p in sorted(DOSSIER_DIR.glob("*.json")):
        try:
            out.append(Dossier.from_json(p.read_text()))
        except Exception as e:
            print(f"[storage] skipping unreadable {p.name}: {e}")
    return out


# ---------- SQLite (append-only history) ----------

def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS dossier_snapshots (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker    TEXT NOT NULL,
            as_of     TEXT NOT NULL,
            snapshot  TEXT,
            price     REAL,
            payload   TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_snap_ticker_date ON dossier_snapshots(ticker, as_of)"
    )
    con.commit()
    con.close()


def append_snapshot(dossier: Dossier) -> None:
    """Store a compact history row. Full payload kept for later analysis."""
    con = sqlite3.connect(DB_PATH)
    con.execute(
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
    con.commit()
    con.close()
