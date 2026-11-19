-- Logins: a password hash per user ('' = no password yet; the service gives seeded demo users DEMO_PASSWORD at start)
-- and email lookups that ignore case, so "Ada@Example.test" signs in as ada@example.test and cannot register twice.
ALTER TABLE users ADD COLUMN password_hash text NOT NULL DEFAULT '';
CREATE UNIQUE INDEX users_email_lower ON users (lower(email));
