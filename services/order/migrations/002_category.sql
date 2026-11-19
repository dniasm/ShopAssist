-- Order lines remember the product's category as it was at checkout (carts and order tiles show it).
-- Nullable: history written before this migration is backfilled from the catalog seed at startup.
ALTER TABLE order_lines ADD COLUMN category text;
