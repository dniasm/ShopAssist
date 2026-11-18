"""gRPC messages -> plain dicts for the REST layer.

Why not `MessageToDict`: protobuf's JSON mapping turns int64 into strings, but money (`*_cents`) must reach
the client as a number. Field names equal the proto names, which equal the API names.
"""
from shopassist_proto import shopassist_pb2 as pb


def user(u: pb.User) -> dict:
    return {"user_id": u.user_id, "name": u.name, "email": u.email, "city": u.city}


def sku(s: pb.Sku) -> dict:
    return {"sku_id": s.sku_id, "product_id": s.product_id, "size": s.size, "color": s.color}


def product(p: pb.Product) -> dict:
    """A Product without its SKUs; callers add `skus` (detail) or `in_stock` (card)."""
    return {
        "product_id": p.product_id, "name": p.name, "category": p.category, "brand": p.brand,
        "description": p.description, "price_cents": p.price_cents, "rating": p.rating,
        "rating_count": p.rating_count, "colors": list(p.colors), "sizes": list(p.sizes), "created_at": p.created_at,
    }


def facet(f: pb.Facet) -> dict:
    return {"field": f.field, "values": [{"value": v.value, "count": v.count} for v in f.values]}
