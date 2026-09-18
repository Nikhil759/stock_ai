-- Live Kite order fill tracking on the trades ledger

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS order_status TEXT NOT NULL DEFAULT 'complete';

ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_order_status_check;
ALTER TABLE trades ADD CONSTRAINT trades_order_status_check
    CHECK (order_status IN ('pending', 'complete', 'rejected', 'cancelled'));

-- Existing live Kite buys may not have filled yet — reconcile on next refresh.
UPDATE trades
SET order_status = 'pending'
WHERE mode = 'live'
  AND kite_order_id IS NOT NULL
  AND action = 'BUY'
  AND order_status = 'complete';
