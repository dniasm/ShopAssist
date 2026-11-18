"""Small deterministic seed data in the exact v2 file shapes, so tests exercise the real seed loaders.

Why generated and not hand-listed: facet and pagination tests compare the service against an
independent Python count over the same list, so the dataset must be big enough to be interesting
(three categories, several brands/colours/sizes) but is still computed, not typed.
"""
import json
from pathlib import Path

CATEGORY_SPECS = {
    "hoodies": {"sizes": ["XS", "S", "M", "L", "XL"], "colors": ["black", "navy", "grey"], "count": 12},
    "headphones": {"sizes": ["ONE"], "colors": ["black", "white"], "count": 10},
    "shoes": {"sizes": ["8", "9", "10", "11"], "colors": ["white", "black", "red"], "count": 8},
}
BRANDS = ["Northwind", "Alpine", "Sonic", "Stride"]
CREATED = "2026-01-01T00:00:00+00:00"


def build_products() -> list[dict]:
    products: list[dict] = []
    n = 0
    for category, spec in CATEGORY_SPECS.items():
        for i in range(spec["count"]):
            n += 1
            # every product has 1-2 colours; the second colour is dropped for some to vary the facets
            colors = spec["colors"][: 1 + (i % 2) + (1 if i % 3 == 0 else 0)]
            products.append({
                "product_id": f"P-{1000 + n}",
                "name": f"{BRANDS[n % len(BRANDS)]} {category[:-1].title()} {n}",
                "category": category,
                "brand": BRANDS[n % len(BRANDS)],
                "description": f"A reliable {category[:-1]} number {n} for everyday use.",
                "price_cents": 1500 + n * 450,
                "rating": round(1.0 + (n * 7 % 40) / 10, 1),
                "rating_count": 10 + n * 3,
                "colors": colors,
                "sizes": spec["sizes"],
                "created_at": f"2026-01-{1 + n % 28:02d}T00:00:00+00:00",
            })
    # Two products that differ only in WHERE the word "parka" appears: name (weight A) vs description (weight C).
    products.append({
        "product_id": "P-2001", "name": "Arctic Parka", "category": "jackets", "brand": "Alpine",
        "description": "Insulated winter coat.", "price_cents": 12000, "rating": 4.0, "rating_count": 5,
        "colors": ["navy"], "sizes": ["M"], "created_at": CREATED,
    })
    products.append({
        "product_id": "P-2002", "name": "Winter Jacket", "category": "jackets", "brand": "Alpine",
        "description": "Cut like a parka, warm like a parka, light like a parka.", "price_cents": 11000,
        "rating": 5.0, "rating_count": 5, "colors": ["navy"], "sizes": ["M"], "created_at": CREATED,
    })
    return products


def build_skus(products: list[dict]) -> list[dict]:
    skus = []
    n = 100000
    for p in products:
        for size in p["sizes"]:
            for color in p["colors"]:
                n += 1
                skus.append({"sku_id": f"SKU-{n}", "product_id": p["product_id"], "size": size, "color": color})
    return skus


def build_inventory(skus: list[dict]) -> list[dict]:
    """Units cycle 0..4 across SKUs, and every 7th product is sold out entirely (in_stock must be False for those)."""
    sold_out = lambda product_id: int(product_id[2:]) % 7 == 3  # noqa: E731
    return [
        {"sku_id": s["sku_id"], "product_id": s["product_id"], "on_hand": 0 if sold_out(s["product_id"]) else i % 5}
        for i, s in enumerate(skus)
    ]


USERS = [
    {"user_id": "U-01", "name": "Ada Lovelace", "email": "ada@example.test", "city": "London"},
    {"user_id": "U-02", "name": "Grace Hopper", "email": "grace@example.test", "city": "New York"},
]

PRODUCTS = build_products()
SKUS = build_skus(PRODUCTS)
INVENTORY = build_inventory(SKUS)


def write_seed_dir(directory: Path) -> Path:
    """Write the v2 seed files the three 5.1 services read; orders/payments are not needed yet."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "catalog.json").write_text(json.dumps({"products": PRODUCTS, "skus": SKUS}), encoding="utf-8")
    (directory / "inventory.json").write_text(json.dumps(INVENTORY), encoding="utf-8")
    (directory / "customers.json").write_text(json.dumps({"users": USERS}), encoding="utf-8")
    return directory
