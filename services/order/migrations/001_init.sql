-- Orders: the order row carries the checkout saga's progress (saga_step), so a crashed saga can be finished or undone.
CREATE SEQUENCE order_seq START 20001;      -- seeded history uses O-10xxx

CREATE TABLE orders (
    order_id        text PRIMARY KEY,
    user_id         text NOT NULL,
    status          text NOT NULL CHECK (status IN
        ('PENDING', 'CONFIRMED', 'PACKED', 'SHIPPED', 'OUT_FOR_DELIVERY', 'DELIVERED', 'CANCELLED', 'FAILED')),
    -- Where the saga is. Written BEFORE each remote call (so recovery knows what may have happened) and
    -- again after it with the outcome. VALIDATING -> RESERVING -> CHARGING -> CONFIRMING -> DONE, or COMPENSATING.
    saga_step       text NOT NULL DEFAULT 'VALIDATING',
    payment_method  text NOT NULL DEFAULT 'card_ok',
    payment_id      text NOT NULL DEFAULT '',
    failure_reason  text NOT NULL DEFAULT '',
    shortages       jsonb NOT NULL DEFAULT '[]',     -- [{sku_id, requested, available}] when stock ran out
    subtotal_cents  bigint NOT NULL DEFAULT 0,
    shipping_cents  bigint NOT NULL DEFAULT 0,
    total_cents     bigint NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX orders_user_idx    ON orders (user_id, created_at DESC, order_id DESC);
CREATE INDEX orders_pending_idx ON orders (created_at) WHERE status = 'PENDING';

CREATE TABLE order_lines (
    order_id         text    NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
    sku_id           text    NOT NULL,
    product_id       text    NOT NULL,
    name             text    NOT NULL,
    size             text    NOT NULL,
    color            text    NOT NULL,
    quantity         integer NOT NULL CHECK (quantity > 0),
    unit_price_cents bigint  NOT NULL CHECK (unit_price_cents >= 0),
    PRIMARY KEY (order_id, sku_id)
);

-- The outbox: one row per status change, written in the same transaction as the change (NOTIFY arrives in 5.3).
CREATE TABLE order_events (
    event_id   bigserial PRIMARY KEY,
    order_id   text NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
    user_id    text NOT NULL,
    status     text NOT NULL,
    message    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX order_events_order_idx ON order_events (order_id, event_id);

-- One checkout per (user, key); the fingerprint says what the key was first used for.
CREATE TABLE checkout_keys (
    user_id         text NOT NULL,
    idempotency_key text NOT NULL,
    fingerprint     text NOT NULL,
    order_id        text NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, idempotency_key)
);
