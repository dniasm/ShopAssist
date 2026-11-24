"""One-shot Docker step: create the five databases and service roles, idempotently, reusing db_setup's helpers.

Reads PG_ADMIN_URL and <DB>_DB_PASSWORD (CATALOG, INVENTORY, ORDERS, CUSTOMER, PAYMENT) from the environment.
Re-running is safe: missing pieces are created, existing roles get their password re-applied, CONNECT stays locked down.
"""

import os
import sys
import time

import psycopg

import db_setup

admin_url = os.environ["PG_ADMIN_URL"]
for attempt in range(30):  # Postgres is healthy by now, but be tolerant
    try:
        conn = psycopg.connect(admin_url, autocommit=True)
        break
    except psycopg.OperationalError:
        time.sleep(1)
else:
    sys.exit("docker_db_init: Postgres not reachable")

with conn, conn.cursor() as cur:
    for database, role, _ in db_setup.SERVICES:
        password = os.environ[f"{database.upper()}_DB_PASSWORD"]
        if db_setup._exists_role(cur, role):
            db_setup._rotate_role(cur, role, password)
        else:
            db_setup._create_role(cur, role, password)
        if not db_setup._exists_db(cur, database):
            db_setup._create_db(cur, database, role)
        db_setup._lock_down(cur, database, role)
        print(f"{database}: ready (owner {role})")
