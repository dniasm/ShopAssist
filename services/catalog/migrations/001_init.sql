-- Catalog: products with a generated full-text column, and the size x colour SKUs under them.
CREATE TABLE products (
    product_id   text PRIMARY KEY,
    name         text    NOT NULL,
    category     text    NOT NULL,
    brand        text    NOT NULL,
    description  text    NOT NULL DEFAULT '',
    price_cents  bigint  NOT NULL CHECK (price_cents >= 0),
    rating       numeric(2,1) NOT NULL DEFAULT 0 CHECK (rating BETWEEN 0 AND 5),
    rating_count integer NOT NULL DEFAULT 0 CHECK (rating_count >= 0),
    colors       text[]  NOT NULL DEFAULT '{}',
    sizes        text[]  NOT NULL DEFAULT '{}',   -- display order is the array order
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- name outranks brand/category, which outrank the description (weights A, B, C).
    search       tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', name), 'A') ||
        setweight(to_tsvector('english', brand || ' ' || category), 'B') ||
        setweight(to_tsvector('english', description), 'C')
    ) STORED
);
CREATE INDEX products_search_idx   ON products USING GIN (search);
CREATE INDEX products_category_idx ON products (category);
CREATE INDEX products_brand_idx    ON products (brand);

CREATE TABLE skus (
    sku_id     text PRIMARY KEY,
    product_id text NOT NULL REFERENCES products (product_id) ON DELETE CASCADE,
    size       text NOT NULL,
    color      text NOT NULL,
    UNIQUE (product_id, size, color)
);
CREATE INDEX skus_product_idx ON skus (product_id);
