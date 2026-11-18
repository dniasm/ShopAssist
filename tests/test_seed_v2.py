"""The real data/seed/v2 files load into the three 5.1 services and agree with each other."""
import json

import psycopg
import pytest

from services.common.seed import seed_dir

SEED = seed_dir()
pytestmark = pytest.mark.skipif(not (SEED / "catalog.json").exists(), reason="data/seed/v2 has not been generated yet")


def load(name):
    return json.loads((SEED / name).read_text(encoding="utf-8"))


def count(url, table):
    with psycopg.connect(url) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_seed_files_are_consistent_across_services():
    catalog, inventory = load("catalog.json"), load("inventory.json")
    sku_ids = {s["sku_id"] for s in catalog["skus"]}
    assert {i["sku_id"] for i in inventory} == sku_ids  # stock exists for exactly the catalog's SKUs
    products = {p["product_id"] for p in catalog["products"]}
    assert {s["product_id"] for s in catalog["skus"]} <= products
    assert all(i["on_hand"] >= 0 for i in inventory)


def test_real_seed_loads_into_all_three_services(loop_thread, temp_dbs):
    from services.catalog.server import create_service as catalog
    from services.customer.server import create_service as customer
    from services.inventory.server import create_service as inventory

    for name, create, table, expected in (
        ("catalog", catalog, "products", len(load("catalog.json")["products"])),
        ("catalog", catalog, "skus", len(load("catalog.json")["skus"])),
        ("inventory", inventory, "stock", len(load("inventory.json"))),
        ("customer", customer, "users", len(load("customers.json")["users"])),
    ):
        url = temp_dbs.create(name)
        service = loop_thread.call(create(url))
        loop_thread.call(service.stop(0.1))
        assert count(url, table) == expected
