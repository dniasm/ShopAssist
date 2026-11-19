"""CatalogService over real gRPC against a throwaway Postgres: search, facets, pagination, lookups."""
import grpc
import pytest

from shopassist_proto import shopassist_pb2 as pb
from tests.fixtures import PRODUCTS, SKUS


def list_ids(catalog, **kw):
    """Walk every page of a ListProducts request; returns the ids in order."""
    ids, token = [], ""
    while True:
        r = catalog.ListProducts(pb.ListProductsRequest(page_size=kw.pop("page_size", 7), page_token=token, **kw))
        ids += [p.product_id for p in r.products]
        token = r.next_page_token
        if not token:
            return ids
        kw["page_size"] = 7


def facet(resp, field):
    return {v.value: v.count for f in resp.facets if f.field == field for v in f.values}


def matches(p, categories=(), brands=(), colors=(), sizes=(), min_price=0, max_price=0):
    """Independent oracle: OR within a field, AND across fields."""
    return (
        (not categories or p["category"] in categories)
        and (not brands or p["brand"] in brands)
        and (not colors or set(colors) & set(p["colors"]))
        and (not sizes or set(sizes) & set(p["sizes"]))
        and (not min_price or p["price_cents"] >= min_price)
        and (not max_price or p["price_cents"] <= max_price)
    )


def test_name_match_ranks_above_description_match(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(query="parka"))
    assert [p.product_id for p in r.products] == ["P-2001", "P-2002"]  # name (A) beats description (C), even at lower rating
    assert r.total == 2


def test_search_uses_stemming_and_ignores_garbage_syntax(catalog):
    assert [p.product_id for p in catalog.ListProducts(pb.ListProductsRequest(query="parkas")).products][0] == "P-2001"
    r = catalog.ListProducts(pb.ListProductsRequest(query='"unclosed & | ) ( :*'))  # must not raise
    assert r.total == 0


def test_brand_and_category_are_searchable(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(query="headphones", page_size=100))
    assert {p.category for p in r.products} == {"headphones"}
    assert r.total == 10


def test_facet_counts_exclude_their_own_filter(catalog):
    req = dict(categories=["hoodies"], colors=["black"])
    r = catalog.ListProducts(pb.ListProductsRequest(**req))
    expected_total = sum(matches(p, **req) for p in PRODUCTS)
    assert r.total == expected_total > 0
    # category facet: applies colour=black but NOT category=hoodies, so other categories still show counts
    expected_category = {}
    for p in PRODUCTS:
        if matches(p, colors=["black"]):
            expected_category[p["category"]] = expected_category.get(p["category"], 0) + 1
    assert facet(r, "category") == expected_category and len(expected_category) > 1
    # colour facet: applies category=hoodies but NOT colour=black
    expected_color = {}
    for p in PRODUCTS:
        if matches(p, categories=["hoodies"]):
            for c in p["colors"]:
                expected_color[c] = expected_color.get(c, 0) + 1
    assert facet(r, "color") == expected_color and len(expected_color) > 1
    # brand and size facets apply BOTH active filters
    expected_brand = {}
    for p in PRODUCTS:
        if matches(p, **req):
            expected_brand[p["brand"]] = expected_brand.get(p["brand"], 0) + 1
    assert facet(r, "brand") == expected_brand


def test_facets_respect_query_and_price_filters(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(query="hoodie", min_price_cents=5000, max_price_cents=9000))
    assert r.total > 0
    expected = {}
    for p in PRODUCTS:
        if p["category"] == "hoodies" and 5000 <= p["price_cents"] <= 9000:
            expected[p["category"]] = expected.get(p["category"], 0) + 1
    assert facet(r, "category") == expected


def test_price_range_is_of_the_filtered_set_without_the_price_filter(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(categories=["shoes"], max_price_cents=5000))
    shoes = [p["price_cents"] for p in PRODUCTS if p["category"] == "shoes"]
    assert (r.price_min_cents, r.price_max_cents) == (min(shoes), max(shoes))  # slider bounds stay stable while dragging
    assert r.total == sum(1 for c in shoes if c <= 5000)


def test_multi_value_filters_are_or_within_a_field(catalog):
    req = dict(categories=["hoodies", "shoes"], sizes=["XS", "9"])
    r = catalog.ListProducts(pb.ListProductsRequest(page_size=100, **req))
    assert {p.product_id for p in r.products} == {p["product_id"] for p in PRODUCTS if matches(p, **req)}


@pytest.mark.parametrize("sort", ["SORT_RELEVANCE", "SORT_PRICE_ASC", "SORT_PRICE_DESC", "SORT_RATING", "SORT_NEWEST"])
@pytest.mark.parametrize("query", ["", "reliable"])
def test_pagination_covers_every_product_exactly_once(catalog, sort, query):
    ids = list_ids(catalog, query=query, sort=pb.SortBy.Value(sort))
    expected = {p["product_id"] for p in PRODUCTS if not query or "reliable" in p["description"]}
    assert len(ids) == len(set(ids)) == len(expected)
    assert set(ids) == expected


def test_sorts_order_results(catalog):
    asc = [p.price_cents for p in catalog.ListProducts(pb.ListProductsRequest(sort=pb.SORT_PRICE_ASC, page_size=100)).products]
    desc = [p.price_cents for p in catalog.ListProducts(pb.ListProductsRequest(sort=pb.SORT_PRICE_DESC, page_size=100)).products]
    assert asc == sorted(asc) and desc == sorted(desc, reverse=True)
    rating = [p.rating for p in catalog.ListProducts(pb.ListProductsRequest(sort=pb.SORT_RATING, page_size=100)).products]
    assert rating == sorted(rating, reverse=True)
    newest = [p.created_at for p in catalog.ListProducts(pb.ListProductsRequest(sort=pb.SORT_NEWEST, page_size=100)).products]
    assert newest == sorted(newest, reverse=True)


def test_relevance_ranks_by_engagement_not_by_rating(catalog):
    def listed(sort, query=""):
        return list(catalog.ListProducts(pb.ListProductsRequest(sort=sort, query=query, page_size=100)).products)
    relevance = listed(pb.SORT_RELEVANCE)
    assert [(p.rating_count, p.rating) for p in relevance] == sorted(((p.rating_count, p.rating) for p in relevance), reverse=True)
    assert [p.product_id for p in relevance] != [p.product_id for p in listed(pb.SORT_RATING)]  # two different orders
    # with a query, equally good text matches are ordered by engagement too
    matched = listed(pb.SORT_RELEVANCE, "reliable")  # every fixture description says "reliable": the text rank ties
    assert [p.rating_count for p in matched] == sorted((p.rating_count for p in matched), reverse=True)


def test_page_size_defaults_to_24_and_is_capped_at_100(catalog):
    assert len(catalog.ListProducts(pb.ListProductsRequest()).products) == 24
    assert len(catalog.ListProducts(pb.ListProductsRequest(page_size=1000)).products) == len(PRODUCTS)


def test_bad_page_token_is_invalid_argument(catalog):
    with pytest.raises(grpc.RpcError) as e:
        catalog.ListProducts(pb.ListProductsRequest(page_token="not-a-token!"))
    assert e.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_list_products_returns_colors_and_sizes_in_display_order(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(categories=["hoodies"], page_size=1))
    assert list(r.products[0].sizes) == ["XS", "S", "M", "L", "XL"]
    assert r.products[0].price_cents > 0


def test_get_product_includes_skus(catalog):
    p = catalog.GetProduct(pb.GetProductRequest(product_id="P-1001"))
    expected = [s for s in SKUS if s["product_id"] == "P-1001"]
    assert {s.sku_id for s in p.skus} == {s["sku_id"] for s in expected}
    assert p.name and p.created_at


def test_get_product_unknown_is_not_found(catalog):
    with pytest.raises(grpc.RpcError) as e:
        catalog.GetProduct(pb.GetProductRequest(product_id="P-0000"))
    assert e.value.code() == grpc.StatusCode.NOT_FOUND


def test_get_products_skips_unknown_ids(catalog):
    r = catalog.GetProducts(pb.GetProductsRequest(product_ids=["P-1001", "P-nope", "P-1002"]))
    assert sorted(p.product_id for p in r.products) == ["P-1001", "P-1002"]


def test_get_skus_returns_name_and_price(catalog):
    sku = SKUS[0]
    r = catalog.GetSkus(pb.GetSkusRequest(sku_ids=[sku["sku_id"], "SKU-nope"]))
    assert len(r.items) == 1
    item = r.items[0]
    assert (item.sku.sku_id, item.sku.size, item.sku.color) == (sku["sku_id"], sku["size"], sku["color"])
    product = next(p for p in PRODUCTS if p["product_id"] == sku["product_id"])
    assert (item.name, item.price_cents) == (product["name"], product["price_cents"])


# ---------------- forgiving search: prefix and typos ----------------

def names(resp, n=3):
    return [p.name for p in resp.products[:n]]


def test_a_partial_last_word_finds_products_by_prefix(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(query="hood", page_size=100))
    assert not r.fuzzy  # a prefix match is still a full-text match
    assert r.total == len([p for p in PRODUCTS if p["category"] == "hoodies"])
    assert {p.category for p in r.products} == {"hoodies"}


def test_prefix_combines_with_earlier_words_and_exclusions_keep_working(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(query="northwind hood", page_size=100))
    assert r.total > 0 and {(p.brand, p.category) for p in r.products} == {("Northwind", "hoodies")}
    r = catalog.ListProducts(pb.ListProductsRequest(query="hoodie -northwind", page_size=100))
    assert r.total > 0 and "Northwind" not in {p.brand for p in r.products}


def test_hyphenated_and_symbol_laden_queries_do_not_error(catalog):
    for q in ["running-shoe", "hood:*", "a | b", "x) (y", "hoodie'; DROP TABLE products; --"]:
        catalog.ListProducts(pb.ListProductsRequest(query=q))
    assert catalog.ListProducts(pb.ListProductsRequest()).total == len(PRODUCTS)  # still there


@pytest.mark.parametrize("typo, category", [("hodie", "hoodies"), ("headphnes", "headphones"), ("hoodei", "hoodies")])
def test_a_typo_falls_back_to_fuzzy_results_with_the_right_category_first(catalog, typo, category):
    r = catalog.ListProducts(pb.ListProductsRequest(query=typo, page_size=100))
    assert r.fuzzy and r.total > 0
    assert {p.category for p in r.products[:5]} == {category}


def test_fuzzy_results_keep_total_facets_and_pagination_consistent(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(query="hodie", page_size=100))
    assert len(r.products) == r.total
    assert sum(facet(r, "category").values()) == r.total  # every product is in exactly one category
    assert sum(facet(r, "brand").values()) == r.total
    prices = [p.price_cents for p in r.products]
    assert (r.price_min_cents, r.price_max_cents) == (min(prices), max(prices))
    paged = list_ids(catalog, query="hodie")
    assert paged == [p.product_id for p in r.products]  # same order and no repeats across pages
    filtered = catalog.ListProducts(pb.ListProductsRequest(query="hodie", brands=["Northwind"], page_size=100))
    assert filtered.fuzzy and {p.brand for p in filtered.products} == {"Northwind"}


def test_fuzzy_is_false_for_normal_empty_and_hopeless_queries(catalog):
    assert not catalog.ListProducts(pb.ListProductsRequest(query="parka")).fuzzy
    assert not catalog.ListProducts(pb.ListProductsRequest()).fuzzy
    nothing = catalog.ListProducts(pb.ListProductsRequest(query="zzqqxx"))
    assert nothing.total == 0 and not nothing.fuzzy  # fuzzy means "we found something approximately"


def test_filters_that_empty_a_real_match_do_not_trigger_fuzzy(catalog):
    r = catalog.ListProducts(pb.ListProductsRequest(query="hoodie", categories=["shoes"]))
    assert r.total == 0 and not r.fuzzy


def test_get_skus_includes_the_products_category(catalog):
    sku = SKUS[0]
    item = catalog.GetSkus(pb.GetSkusRequest(sku_ids=[sku["sku_id"]])).items[0]
    assert item.category == next(p["category"] for p in PRODUCTS if p["product_id"] == sku["product_id"])
