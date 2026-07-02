"""
Helper to turn raw bar data into a clean pandas DataFrame.

Accepts either:
  - a dict in your yfinance shape: {closes, opens, highs, lows, volumes, price}
  - or an existing DataFrame with open/high/low/close/volume columns.
"""
from __future__ import annotations
import pandas as pd


def to_frame(bars) -> pd.DataFrame:
    if isinstance(bars, pd.DataFrame):
        df = bars.rename(columns=str.lower).copy()
    else:
        df = pd.DataFrame(
            {
                "open": bars.get("opens", []),
                "high": bars.get("highs", []),
                "low": bars.get("lows", []),
                "close": bars.get("closes", []),
                "volume": bars.get("volumes", []),
            }
        )
    for c in ("open", "high", "low", "close", "volume"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.reset_index(drop=True)
