-- Inventory: stock per SKU, time-limited reservations, and idempotent restocks.
CREATE TABLE stock (
    sku_id     text PRIMARY KEY,
    product_id text    NOT NULL,
    on_hand    integer NOT NULL,
    reserved   integer NOT NULL DEFAULT 0,
    -- The invariant lives in the database too, so no code path (or manual SQL) can oversell.
    CONSTRAINT stock_never_negative CHECK (reserved >= 0 AND on_hand >= reserved)
);
CREATE INDEX stock_product_idx ON stock (product_id);

CREATE TABLE reservations (
    reservation_id text PRIMARY KEY,                -- idempotency key (the order id for checkouts)
    state          text NOT NULL CHECK (state IN ('HELD', 'COMMITTED', 'RELEASED', 'EXPIRED')),
    reason         text NOT NULL DEFAULT '',
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL
);
-- The sweeper only ever looks at live holds.
CREATE INDEX reservations_due_idx ON reservations (expires_at) WHERE state = 'HELD';

CREATE TABLE reservation_lines (
    reservation_id text    NOT NULL REFERENCES reservations (reservation_id) ON DELETE CASCADE,
    sku_id         text    NOT NULL REFERENCES stock (sku_id),
    quantity       integer NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (reservation_id, sku_id)
);

CREATE TABLE restocks (
    idempotency_key text PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now()
);
