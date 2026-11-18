"""Catalog SQL: seed loading, filtered/sorted/paged product listing, facet counts and lookups.

All values go in as bound parameters. The only SQL text built dynamically is the WHERE clause (from fixed
fragments) and the ORDER BY (picked from a fixed table), never from client strings.
"""
from dataclasses import dataclass, field, replace

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from services.catalog.query import fuzzy_text, parse_search, prefix_tsquery

# Must equal the expression indexed in migration 002, or the trigram index is not usable.
TRGM_EXPR = "lower(name || ' ' || brand || ' ' || category)"
FUZZY_MIN_SIMILARITY = 0.35

SORTS = {  # tie-break on product_id so offset paging is stable
    "relevance": "rating DESC, rating_count DESC, product_id",
    "price_asc": "price_cents ASC, product_id",
    "price_desc": "price_cents DESC, product_id",
    "rating": "rating DESC, rating_count DESC, product_id",
    "newest": "created_at DESC, product_id",
}
FACET_FIELDS = ("category", "brand", "color", "size")
# Facet value expression and source: colours and sizes live in arrays, so they are unnested to be counted.
_FACET_SOURCE = {
    "category": ("category", "products"),
    "brand": ("brand", "products"),
    "color": ("v", "products, unnest(colors) AS v"),
    "size": ("v", "products, unnest(sizes) AS v"),
}


@dataclass
class Filters:
    query: str = ""
    categories: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)
    min_price_cents: int = 0
    max_price_cents: int = 0
    fuzzy: bool = False  # match the query by trigram similarity instead of full text


async def load_seed(conn: AsyncConnection, data: dict) -> int:
    """Insert products then SKUs from catalog.json ({"products": [...], "skus": [...]})."""
    products = data["products"]
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO products (product_id, name, category, brand, description, price_cents, rating,"
            " rating_count, colors, sizes, created_at)"
            " VALUES (%(product_id)s, %(name)s, %(category)s, %(brand)s, %(description)s, %(price_cents)s,"
            " %(rating)s, %(rating_count)s, %(colors)s, %(sizes)s, %(created_at)s)",
            [{"colors": [], "sizes": [], "rating": 0, "rating_count": 0, "description": "", **p} for p in products],
        )
        await cur.executemany(
            "INSERT INTO skus (sku_id, product_id, size, color) VALUES (%(sku_id)s, %(product_id)s, %(size)s, %(color)s)",
            data.get("skus", []),
        )
    return len(products)


def _text_clause(f: Filters) -> tuple[str, dict]:
    """The query condition: full text with a prefix on the last word, or (fuzzy) trigram similarity."""
    if f.fuzzy:
        # `<%` lets the GIN index prefilter (its threshold is set by use_fuzzy_threshold); the function is the exact cut.
        clause = f"%(fq)s <%% {TRGM_EXPR} AND word_similarity(%(fq)s, {TRGM_EXPR}) > {FUZZY_MIN_SIMILARITY}"
        return clause, {"fq": fuzzy_text(f.query)}
    tsquery, params = _tsquery(f.query)
    return f"search @@ {tsquery}", params


def _tsquery(query: str) -> tuple[str, dict]:
    """websearch syntax for all but the last word, which is a prefix. `empty && x` is `x`, so either part may be blank."""
    terms = parse_search(query)
    parts, params = [], {}
    if terms.rest:
        parts.append("websearch_to_tsquery('english', %(q_rest)s)")
        params["q_rest"] = terms.rest
    if terms.prefix:
        parts.append("to_tsquery('english', %(q_prefix)s)")
        params["q_prefix"] = prefix_tsquery(terms.prefix)
    return "(" + " && ".join(parts) + ")", params  # parenthesised: `@@` binds tighter than `&&`


def _relevance_order(f: Filters) -> str:
    if not f.query.strip():
        return SORTS["relevance"]
    if f.fuzzy:
        return f"word_similarity(%(fq)s, {TRGM_EXPR}) DESC, rating DESC, rating_count DESC, product_id"
    return f"ts_rank_cd(search, {_tsquery(f.query)[0]}) DESC, rating DESC, rating_count DESC, product_id"


def _where(f: Filters, exclude: str = "") -> tuple[str, dict]:
    """WHERE clause for the filters; `exclude` leaves one field out (facets must not filter by themselves)."""
    clauses, params = [], {}
    if f.query.strip():
        text_clause, params = _text_clause(f)
        clauses.append(text_clause)
    if f.categories and exclude != "category":
        clauses.append("category = ANY(%(categories)s)")
        params["categories"] = f.categories
    if f.brands and exclude != "brand":
        clauses.append("brand = ANY(%(brands)s)")
        params["brands"] = f.brands
    if f.colors and exclude != "color":
        clauses.append("colors && %(colors)s::text[]")
        params["colors"] = f.colors
    if f.sizes and exclude != "size":
        clauses.append("sizes && %(sizes)s::text[]")
        params["sizes"] = f.sizes
    if f.min_price_cents and exclude != "price":
        clauses.append("price_cents >= %(min_price)s")
        params["min_price"] = f.min_price_cents
    if f.max_price_cents and exclude != "price":
        clauses.append("price_cents <= %(max_price)s")
        params["max_price"] = f.max_price_cents
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


async def search_page(conn: AsyncConnection, f: Filters, sort: str, limit: int, offset: int) -> tuple[list[dict], int]:
    """One page of products (no SKUs) and the total number matching every filter."""
    where, params = _where(f)
    order = _relevance_order(f) if sort == "relevance" else SORTS[sort]
    cur = await conn.execute(f"SELECT count(*) AS n FROM products {where}", params)
    total = (await cur.fetchone())["n"]
    cur = await conn.execute(
        f"SELECT * FROM products {where} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s",
        {**params, "limit": limit, "offset": offset},
    )
    return await cur.fetchall(), total


@dataclass
class SearchResult:
    rows: list[dict]
    total: int
    counts: dict[str, dict[str, int]]
    price_low: int
    price_high: int
    fuzzy: bool


async def search(conn: AsyncConnection, f: Filters, sort: str, limit: int, offset: int) -> SearchResult:
    """Full-text search; if it finds nothing, retry by trigram similarity so typos still get answers.

    The page, total, facets and price range all come from the SAME filter set (the fuzzy one when it was used),
    so the numbers on screen always agree with each other. `fuzzy` is true only when the fallback found something.
    """
    rows, total = await search_page(conn, f, sort, limit, offset)
    fuzzy = False
    if total == 0 and fuzzy_text(f.query):
        # One transaction-local setting: the `<%` operator compares against this threshold.
        await conn.execute("SELECT set_config('pg_trgm.word_similarity_threshold', %s, true)", (str(FUZZY_MIN_SIMILARITY),))
        loose = replace(f, fuzzy=True)
        loose_rows, loose_total = await search_page(conn, loose, sort, limit, offset)
        if loose_total:
            f, rows, total, fuzzy = loose, loose_rows, loose_total, True
    low, high = await price_range(conn, f)
    return SearchResult(rows, total, await facet_counts(conn, f), low, high, fuzzy)


async def facet_counts(conn: AsyncConnection, f: Filters) -> dict[str, dict[str, int]]:
    """For each facet field, product counts per value under all OTHER active filters (classic faceted search)."""
    result: dict[str, dict[str, int]] = {}
    for name in FACET_FIELDS:
        where, params = _where(f, exclude=name)
        value_expr, source = _FACET_SOURCE[name]
        cur = await conn.execute(
            f"SELECT {value_expr} AS value, count(*) AS n FROM {source} {where} GROUP BY 1", params
        )
        result[name] = {r["value"]: r["n"] for r in await cur.fetchall()}
    return result


async def price_range(conn: AsyncConnection, f: Filters) -> tuple[int, int]:
    """Min/max price of the filtered set, ignoring the price filter itself so a slider's bounds stay put."""
    where, params = _where(f, exclude="price")
    cur = await conn.execute(f"SELECT min(price_cents) AS lo, max(price_cents) AS hi FROM products {where}", params)
    row = await cur.fetchone()
    return row["lo"] or 0, row["hi"] or 0


async def skus_for(conn: AsyncConnection, product_ids: list[str]) -> dict[str, list[dict]]:
    cur = await conn.execute(
        "SELECT sku_id, product_id, size, color FROM skus WHERE product_id = ANY(%s) ORDER BY sku_id", (product_ids,)
    )
    grouped: dict[str, list[dict]] = {}
    for row in await cur.fetchall():
        grouped.setdefault(row["product_id"], []).append(row)
    return grouped


async def get_products(pool: AsyncConnectionPool, product_ids: list[str], with_skus: bool = True) -> list[dict]:
    """Products by id (unknown ids are skipped), each with a "skus" list when requested."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM products WHERE product_id = ANY(%s) ORDER BY product_id", (product_ids,))
        rows = await cur.fetchall()
        if with_skus:
            by_product = await skus_for(conn, [r["product_id"] for r in rows])
            for r in rows:
                r["skus"] = by_product.get(r["product_id"], [])
    return rows


async def get_skus(pool: AsyncConnectionPool, sku_ids: list[str]) -> list[dict]:
    """SKUs with their product's name and price (unknown ids are skipped)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT s.sku_id, s.product_id, s.size, s.color, p.name, p.price_cents"
            " FROM skus s JOIN products p USING (product_id) WHERE s.sku_id = ANY(%s) ORDER BY s.sku_id",
            (sku_ids,),
        )
        return await cur.fetchall()
