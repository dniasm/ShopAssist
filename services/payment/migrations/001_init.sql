-- Payment: simulated charges and refunds. Both are idempotent by key, and remember the request behind the key.
CREATE SEQUENCE payment_seq START 20001;   -- seeded history uses PAY-10xxx
CREATE SEQUENCE refund_seq  START 20001;

CREATE TABLE payments (
    payment_id      text PRIMARY KEY DEFAULT 'PAY-' || nextval('payment_seq'),
    idempotency_key text   NOT NULL UNIQUE,
    fingerprint     text,                       -- NULL for seeded history
    order_id        text   NOT NULL,
    amount_cents    bigint NOT NULL CHECK (amount_cents > 0),
    payment_method  text   NOT NULL,
    status          text   NOT NULL CHECK (status IN ('SUCCEEDED', 'DECLINED', 'REFUNDED')),
    decline_reason  text   NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX payments_order_idx ON payments (order_id);

CREATE TABLE refunds (
    refund_id       text PRIMARY KEY DEFAULT 'RF-' || nextval('refund_seq'),
    idempotency_key text   NOT NULL UNIQUE,
    fingerprint     text,
    payment_id      text   NOT NULL UNIQUE REFERENCES payments (payment_id),   -- UNIQUE: a payment is refunded at most once
    amount_cents    bigint NOT NULL CHECK (amount_cents > 0),
    created_at      timestamptz NOT NULL DEFAULT now()
);
