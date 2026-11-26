"""Upgrade test for the assistant's migration 002: single favorite_color facts become liked_colors."""
import psycopg

from services.assistant.server import MIGRATIONS
from services.common.db import create_pool
from services.common.migrate import apply_migrations
from tests.conftest import run

OLD = [
    ("F-1", "U-01", "favorite_color", "navy"), ("F-2", "U-02", "favorite_colour", "olive"),
    ("F-3", "U-03", "favorite_color", "Red"), ("F-4", "U-03", "liked_colors", "red"), ("F-5", "U-01", "size_top", "L"),
]


def test_migration_002_moves_favorite_colors_to_liked_colors(temp_dbs):
    url = temp_dbs.create("assistant_mig")

    async def scenario():
        pool = await create_pool(url)
        try:
            await apply_migrations(pool, MIGRATIONS, max_version=1)
            async with pool.connection() as conn:
                for fact_id, user, key, value in OLD:
                    await conn.execute("INSERT INTO facts (fact_id, user_id, kind, key, value) VALUES (%s, %s, 'permanent', %s, %s)",
                                       (fact_id, user, key, value))
            assert await apply_migrations(pool, MIGRATIONS) == ["002_liked_colors.sql", "003_clean_vague_facts.sql", "004_fact_seq.sql"]
            assert await apply_migrations(pool, MIGRATIONS) == []  # a rerun changes nothing
        finally:
            await pool.close()

    run(scenario())
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT fact_id, key, value FROM facts ORDER BY fact_id").fetchall()
    assert rows == [("F-1", "liked_colors", "navy"), ("F-2", "liked_colors", "olive"),
                    ("F-4", "liked_colors", "red"), ("F-5", "size_top", "L")]  # F-3 was a duplicate of F-4


VAGUE = ["favorite color", "Favourite Colour", "my favorite color", "preferred colour", "the same one", "my usual size", "!!"]


def test_migration_003_drops_vague_values_and_keeps_real_ones(temp_dbs):
    url = temp_dbs.create("assistant_mig_vague")
    real = [("R-1", "liked_colors", "black"), ("R-2", "size_top", "L"), ("R-3", "styles", "color blocking"), ("R-4", "fit", "loose")]

    async def scenario():
        pool = await create_pool(url)
        try:
            await apply_migrations(pool, MIGRATIONS, max_version=2)
            async with pool.connection() as conn:
                rows = [(f"V-{i}", "liked_colors", v) for i, v in enumerate(VAGUE)] + real
                for fact_id, key, value in rows:
                    await conn.execute("INSERT INTO facts (fact_id, user_id, kind, key, value) VALUES (%s, %s, 'permanent', %s, %s)",
                                       (fact_id, "U-01", key, value))
            await apply_migrations(pool, MIGRATIONS)
            assert await apply_migrations(pool, MIGRATIONS) == []
        finally:
            await pool.close()

    run(scenario())
    with psycopg.connect(url) as conn:
        left = sorted(r[0] for r in conn.execute("SELECT fact_id FROM facts").fetchall())
    assert left == ["R-1", "R-2", "R-3", "R-4"]
