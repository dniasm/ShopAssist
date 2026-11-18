-- Customer: demo users (no passwords) and one cart per user (cart RPCs arrive in phase 5.2).
CREATE TABLE users (
    user_id    text PRIMARY KEY,
    name       text NOT NULL,
    email      text NOT NULL UNIQUE,
    city       text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE carts (
    user_id    text PRIMARY KEY REFERENCES users (user_id),
    version    bigint NOT NULL DEFAULT 0,          -- bumps on every change; ClearCart(if_version) compares it
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cart_lines (
    user_id    text NOT NULL REFERENCES carts (user_id) ON DELETE CASCADE,
    sku_id     text NOT NULL,
    product_id text NOT NULL,
    quantity   integer NOT NULL CHECK (quantity BETWEEN 1 AND 10),
    added_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, sku_id)
);
