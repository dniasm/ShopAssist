"""Database rows -> protobuf messages for the Order service."""
from services.common.util import iso
from shopassist_proto import shopassist_pb2 as pb


def order_event(row: dict) -> pb.OrderEvent:
    return pb.OrderEvent(
        event_id=row["event_id"], order_id=row["order_id"], status=pb.OrderStatus.Value(row["status"]),
        message=row["message"], timestamp=iso(row["created_at"]),
    )


def order(row: dict) -> pb.Order:
    return pb.Order(
        order_id=row["order_id"], user_id=row["user_id"], status=pb.OrderStatus.Value(row["status"]),
        lines=[pb.OrderLine(sku_id=l["sku_id"], product_id=l["product_id"], name=l["name"], size=l["size"], color=l["color"],
                            quantity=l["quantity"], unit_price_cents=l["unit_price_cents"],
                            category=l["category"] or "") for l in row["lines"]],
        subtotal_cents=row["subtotal_cents"], shipping_cents=row["shipping_cents"], total_cents=row["total_cents"],
        payment_id=row["payment_id"], failure_reason=row["failure_reason"], created_at=iso(row["created_at"]),
        updated_at=iso(row["updated_at"]), history=[order_event(e) for e in row["history"]],
    )


def shortages(items: list[dict]) -> list[pb.Shortage]:
    return [pb.Shortage(sku_id=s["sku_id"], requested=s["requested"], available=s["available"]) for s in items]


def final_event(row: dict) -> pb.CheckoutEvent:
    """The closing event rebuilt from the database, for callers that arrive after the saga ended."""
    failed = row["status"] == "FAILED"
    return pb.CheckoutEvent(
        order_id=row["order_id"], step=pb.FINISHED, state=pb.STEP_FAILED if failed else pb.STEP_SUCCEEDED,
        message=row["failure_reason"] if failed else "Order confirmed.", shortages=shortages(row["shortages"]),
        order=order(row), timestamp=iso(row["updated_at"]),
    )
