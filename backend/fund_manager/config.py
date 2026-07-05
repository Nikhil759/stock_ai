"""Fund manager tunables."""

from __future__ import annotations

import os

# Fill price for paper buys: "kite" (live LTP) or "intention" (buy_price from file)
FILL_PRICE_MODE = os.getenv("FUND_MANAGER_FILL_PRICE", "kite").lower()
