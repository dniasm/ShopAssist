-- Typo-tolerant fallback search: trigram similarity over name + brand + category.
-- pg_trgm is a "trusted" extension, so the database owner (the service role) may create it.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- The expression must match services/catalog/db.py TRGM_EXPR exactly or the planner cannot use the index.
CREATE INDEX products_trgm_idx ON products USING GIN ((lower(name || ' ' || brand || ' ' || category)) gin_trgm_ops);
