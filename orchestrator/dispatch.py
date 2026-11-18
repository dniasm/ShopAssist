"""Runs one query: detect intent, call the gRPC services, compose the answer, emit events along the way."""
import logging

from google.protobuf.json_format import MessageToDict

from orchestrator.composer import compose
from orchestrator.grpc_clients import Clients, NotFound, ServiceError
from orchestrator.intent import detect_intent
from orchestrator.store import QueryRecord

log = logging.getLogger(__name__)
STOCK_CHECK_LIMIT = 5


def to_dict(msg) -> dict:
    return MessageToDict(msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)


async def _call(rec: QueryRecord, service: str, method: str) -> None:
    await rec.emit("service_call", {"service": service, "method": method})


def _search_text(entities: dict, with_keywords: bool = True) -> str:
    words = [entities["color"]] if "color" in entities else []
    return " ".join(words + (entities.get("keywords", []) if with_keywords else []))


async def _search(rec: QueryRecord, clients: Clients, entities: dict, limit: int = 0) -> tuple[list, bool]:
    """SearchProducts; returns (products, relaxed).

    Search words must all match, so a slang/filler keyword from the user or the LLM ("cans") can empty an
    otherwise good search. If a search WITH keywords finds nothing and there is another filter (category, color
    or max_price), repeat once without the keywords. Keywords alone are never relaxed.
    """
    filters = dict(category=entities.get("category", ""), max_price=entities.get("max_price", 0.0), limit=limit)
    await _call(rec, "ProductService", "SearchProducts")
    products = await clients.search_products(text=_search_text(entities), **filters)
    has_other_filter = any(k in entities for k in ("category", "color", "max_price"))
    if products or not entities.get("keywords") or not has_other_filter:
        return products, False
    await _call(rec, "ProductService", "SearchProducts")  # second call: keep the event stream honest
    products = await clients.search_products(text=_search_text(entities, with_keywords=False), **filters)
    return products, bool(products)


async def order_status(rec: QueryRecord, clients: Clients, entities: dict) -> dict:
    order_id = entities.get("order_id")
    if not order_id:
        return {}
    await _call(rec, "OrderService", "GetOrder")
    try:
        order = to_dict(await clients.get_order(order_id))
    except NotFound:
        return {}
    await rec.emit("partial_result", {"order": order})
    names: dict[str, str] = {}
    for pid in dict.fromkeys(it["product_id"] for it in order["items"]):
        await _call(rec, "ProductService", "GetProduct")
        try:
            names[pid] = (await clients.get_product(pid)).name
        except NotFound:
            pass  # product missing from the catalogue: show its id instead
    for it in order["items"]:
        it["name"] = names.get(it["product_id"], it["product_id"])
    return {"order": order}


async def track_order(rec: QueryRecord, clients: Clients, entities: dict) -> dict:
    order_id = entities.get("order_id")
    if not order_id:
        return {}
    await _call(rec, "OrderService", "TrackOrder")
    rec.status = "streaming"
    updates: list[dict] = []
    try:
        async for update in clients.track_order(order_id):
            d = to_dict(update)
            updates.append(d)
            await rec.emit("order_update", d)
    except NotFound:
        pass  # only possible before the first update
    return {"order_id": order_id, "updates": updates}


async def product_search(rec: QueryRecord, clients: Clients, entities: dict) -> dict:
    products, relaxed = await _search(rec, clients, entities)
    data = {"products": [to_dict(p) for p in products]}
    if relaxed:
        data["relaxed"] = True
    await rec.emit("partial_result", data)
    return data


async def stock_check(rec: QueryRecord, clients: Clients, entities: dict) -> dict:
    relaxed = False
    if "product_id" in entities:
        await _call(rec, "ProductService", "GetProduct")
        try:
            products = [await clients.get_product(entities["product_id"])]
        except NotFound:
            products = []
    else:
        products, relaxed = await _search(rec, clients, entities, limit=STOCK_CHECK_LIMIT)
    results = []
    for p in products:
        await _call(rec, "InventoryService", "GetStock")
        levels = await clients.get_stock(p.product_id, entities.get("size", ""))
        result = {"product": to_dict(p), "stock": [to_dict(lv) for lv in levels]}
        results.append(result)
        await rec.emit("partial_result", result)
    return {"results": results, **({"relaxed": True} if relaxed else {})}


async def product_info(rec: QueryRecord, clients: Clients, entities: dict) -> dict:
    await _call(rec, "ProductService", "GetProduct")
    try:
        product = to_dict(await clients.get_product(entities["product_id"]))
    except NotFound:
        return {}
    await rec.emit("partial_result", {"product": product})
    await _call(rec, "InventoryService", "GetStock")
    stock = [to_dict(lv) for lv in await clients.get_stock(product["product_id"])]
    return {"product": product, "stock": stock}


async def unknown(rec: QueryRecord, clients: Clients, entities: dict) -> dict:
    return {}


HANDLERS = {
    "order_status": order_status,
    "track_order": track_order,
    "product_search": product_search,
    "stock_check": stock_check,
    "product_info": product_info,
    "unknown": unknown,
}


async def _fail(rec: QueryRecord, message: str) -> None:
    rec.status = "error"
    await rec.emit("error", {"message": message})


async def run_query(rec: QueryRecord, clients: Clients, llm=None) -> None:
    """Background task for one query. Always ends with exactly one final_answer or error event."""
    try:
        rec.status = "running"
        intent, entities, source = await detect_intent(rec.text, llm)
        rec.intent, rec.entities = intent, entities
        await rec.emit("intent_detected", {"intent": intent, "entities": entities, "source": source})
        data = await HANDLERS[intent](rec, clients, entities)
        rec.answer = compose(intent, entities, data)
        rec.data = data
        rec.status = "done"
        await rec.emit("final_answer", {"answer": rec.answer, "data": data})
    except ServiceError as e:
        await _fail(rec, str(e))
    except Exception:
        log.exception("query %s failed", rec.query_id)
        await _fail(rec, "Something went wrong while handling your request.")
