-- Phase 5.3: fulfilment tracking and cancellation.
--   tracked    : only orders placed through checkout are moved along by the fulfilment worker; seeded history is not
--                (its timestamps are old, so it would otherwise count as "due" at every restart).
--                DEFAULT false on purpose: every row that exists when this runs is an older order and must stay as it is;
--                checkout creates its orders with tracked = true explicitly (db.begin_checkout).
--   cancel_key : the idempotency key of the CancelOrder call that claimed this order (saga_step = 'CANCELLING').
ALTER TABLE orders ADD COLUMN tracked    boolean NOT NULL DEFAULT false;
ALTER TABLE orders ADD COLUMN cancel_key text;
CREATE INDEX orders_due_idx ON orders (updated_at) WHERE tracked AND saga_step = 'DONE'
    AND status IN ('CONFIRMED', 'PACKED', 'SHIPPED', 'OUT_FOR_DELIVERY');
