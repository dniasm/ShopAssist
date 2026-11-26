-- Facts keep insertion order: rows written in one batch share created_at and fact_id is random, so a serial breaks the tie.
ALTER TABLE facts ADD COLUMN IF NOT EXISTS seq bigserial;
CREATE INDEX IF NOT EXISTS facts_user_order_idx ON facts (user_id, created_at, seq);
