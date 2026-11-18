-- Idempotency keys now remember WHAT they were first used for (see services/common/idempotency.py).
-- Nullable: rows written before this migration have no fingerprint and are accepted on replay.
ALTER TABLE reservations ADD COLUMN fingerprint text;
ALTER TABLE restocks     ADD COLUMN fingerprint text;
