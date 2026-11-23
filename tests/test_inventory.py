"""InventoryService invariants, over real gRPC against a throwaway Postgres.

The three that matter most (each has a test that tries to break it):
  1. stock never goes negative:       on_hand - reserved >= 0 and reserved >= 0, always
  2. Reserve is all-or-nothing:       either every line is held or nothing changed
  3. writes are idempotent:           the same key twice has the effect of once
"""
import asyncio
from datetime import datetime, timedelta, timezone

import grpc
import psycopg
import pytest

from tests import authchan
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests.conftest import run, start, stop
from tests.fixtures import INVENTORY


def sku_with(on_hand: int, skip: int = 0) -> str:
    """The id of a seeded SKU holding exactly `on_hand` units (`skip` picks a different one)."""
    return [i["sku_id"] for i in INVENTORY if i["on_hand"] == on_hand][skip]


def lines(*pairs) -> list[pb.StockLine]:
    return [pb.StockLine(sku_id=s, quantity=q) for s, q in pairs]


def stock_of(inv, *sku_ids):
    return {l.sku_id: l for l in inv.GetStock(pb.GetStockRequest(sku_ids=list(sku_ids))).levels}


def reserve(inv, rid, *pairs, ttl=0):
    return inv.Reserve(pb.ReserveRequest(reservation_id=rid, lines=lines(*pairs), ttl_seconds=ttl))


def query(url, sql, params=()):
    with psycopg.connect(url) as conn:
        return conn.execute(sql, params).fetchall()


def assert_no_negative_stock(url):
    bad = query(url, "SELECT sku_id FROM stock WHERE reserved < 0 OR on_hand - reserved < 0")
    assert bad == []


def code_of(fn):
    with pytest.raises(grpc.RpcError) as e:
        fn()
    return e.value.code()


# ---------------- reads ----------------

def test_get_stock_by_sku_and_product_is_a_union(inventory):
    a, b = INVENTORY[0], INVENTORY[-1]
    r = inventory.GetStock(pb.GetStockRequest(sku_ids=[a["sku_id"]], product_ids=[b["product_id"]]))
    got = {l.sku_id for l in r.levels}
    expected = {a["sku_id"]} | {i["sku_id"] for i in INVENTORY if i["product_id"] == b["product_id"]}
    assert got == expected
    lvl = next(l for l in r.levels if l.sku_id == a["sku_id"])
    assert (lvl.on_hand, lvl.reserved, lvl.available) == (a["on_hand"], 0, a["on_hand"])


def test_get_stock_with_no_ids_is_empty(inventory):
    assert list(inventory.GetStock(pb.GetStockRequest()).levels) == []


# ---------------- reserve ----------------

def test_reserve_holds_stock_and_reports_expiry(inventory):
    sku = sku_with(4)
    r = reserve(inventory, "R-1", (sku, 3))
    assert r.ok and not r.shortages
    assert datetime.fromisoformat(r.expires_at) > datetime.now(timezone.utc) + timedelta(seconds=590)  # default ttl 600 s
    lvl = stock_of(inventory, sku)[sku]
    assert (lvl.on_hand, lvl.reserved, lvl.available) == (4, 3, 1)


def test_reserve_is_all_or_nothing(inventory):
    plenty, scarce = sku_with(4), sku_with(1)
    r = reserve(inventory, "R-AON", (plenty, 2), (scarce, 5))
    assert not r.ok
    assert [(s.sku_id, s.requested, s.available) for s in r.shortages] == [(scarce, 5, 1)]
    levels = stock_of(inventory, plenty, scarce)
    assert levels[plenty].reserved == 0 and levels[scarce].reserved == 0  # the satisfiable line was NOT kept


def test_reserve_reports_every_shortage_not_just_the_first(inventory):
    a, b = sku_with(1), sku_with(2)
    r = reserve(inventory, "R-SHORT", (a, 9), (b, 9))
    assert not r.ok and {s.sku_id for s in r.shortages} == {a, b}


def test_reserve_with_a_failed_attempt_leaves_no_reservation_row(inventory, inventory_running):
    reserve(inventory, "R-NONE", (sku_with(1), 9))
    assert query(inventory_running.url, "SELECT 1 FROM reservations WHERE reservation_id = 'R-NONE'") == []


def test_reserve_unknown_sku_is_a_shortage_with_zero_available(inventory):
    r = reserve(inventory, "R-UNK", ("SKU-nope", 1))
    assert not r.ok and (r.shortages[0].sku_id, r.shortages[0].available) == ("SKU-nope", 0)


def test_reserve_is_idempotent_on_reservation_id(inventory):
    sku = sku_with(4)
    first = reserve(inventory, "R-IDEM", (sku, 2))
    again = reserve(inventory, "R-IDEM", (sku, 2))
    assert first.ok and again.ok and first.expires_at == again.expires_at
    assert stock_of(inventory, sku)[sku].reserved == 2  # held once, not twice


def test_duplicate_lines_for_one_sku_are_merged(inventory):
    sku = sku_with(3)
    assert not reserve(inventory, "R-DUP", (sku, 2), (sku, 2)).ok  # 4 > 3 available
    assert reserve(inventory, "R-DUP2", (sku, 1), (sku, 2)).ok
    assert stock_of(inventory, sku)[sku].reserved == 3


def test_reserve_rejects_bad_input(inventory):
    sku = sku_with(1)
    assert code_of(lambda: reserve(inventory, "", (sku, 1))) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: reserve(inventory, "R-X")) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: reserve(inventory, "R-X", (sku, 0))) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: reserve(inventory, "R-X", (sku, -1))) == grpc.StatusCode.INVALID_ARGUMENT


def test_database_itself_refuses_negative_available(inventory_running):
    with psycopg.connect(inventory_running.url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE stock SET reserved = on_hand + 1 WHERE sku_id = %s", (sku_with(1),))


# Invariant: with 1 unit in stock, any number of concurrent Reserve calls for it yield exactly
# one ok=true, and on_hand - reserved is never negative afterwards. Broken by a read-then-write
# implementation (all 20 would read available=1), which is why Reserve uses a conditional UPDATE.
def test_twenty_concurrent_reserves_for_one_unit_yield_exactly_one_winner(inventory_running):
    sku = sku_with(1)

    async def scenario():
        async with authchan.aio_channel(inventory_running.addr) as ch:
            stub = pb_grpc.InventoryServiceStub(ch)
            results = await asyncio.gather(*(
                stub.Reserve(pb.ReserveRequest(reservation_id=f"R-C{i}", lines=lines((sku, 1)))) for i in range(20)
            ))
            return [r.ok for r in results]

    oks = run(scenario())
    assert oks.count(True) == 1 and oks.count(False) == 19
    assert query(inventory_running.url, "SELECT on_hand, reserved FROM stock WHERE sku_id = %s", (sku,)) == [(1, 1)]
    assert_no_negative_stock(inventory_running.url)


# Invariant: concurrent multi-line reservations touching the same SKUs in opposite orders never
# deadlock and never oversell (rows are updated in sku_id order, whatever order the client sent).
def test_opposite_order_reservations_do_not_deadlock_or_oversell(inventory_running):
    a, b = sku_with(4), sku_with(4, skip=1)

    async def scenario():
        async with authchan.aio_channel(inventory_running.addr) as ch:
            stub = pb_grpc.InventoryServiceStub(ch)
            calls = []
            for i in range(30):
                pairs = ((a, 1), (b, 1)) if i % 2 else ((b, 1), (a, 1))
                calls.append(stub.Reserve(pb.ReserveRequest(reservation_id=f"R-D{i}", lines=lines(*pairs)), timeout=20))
            return [r.ok for r in await asyncio.gather(*calls)]

    oks = run(scenario())
    assert oks.count(True) == 4  # exactly the 4 units each SKU has
    rows = dict(query(inventory_running.url, "SELECT sku_id, reserved FROM stock WHERE sku_id IN (%s, %s)", (a, b)))
    assert rows == {a: 4, b: 4}
    assert_no_negative_stock(inventory_running.url)


# ---------------- commit ----------------

def test_commit_moves_stock_out_of_on_hand_and_is_idempotent(inventory):
    sku = sku_with(4)
    reserve(inventory, "R-COM", (sku, 3))
    inventory.Commit(pb.CommitRequest(reservation_id="R-COM"))
    inventory.Commit(pb.CommitRequest(reservation_id="R-COM"))  # repeat: no second deduction
    lvl = stock_of(inventory, sku)[sku]
    assert (lvl.on_hand, lvl.reserved, lvl.available) == (1, 0, 1)


def test_commit_unknown_reservation_is_not_found(inventory):
    assert code_of(lambda: inventory.Commit(pb.CommitRequest(reservation_id="R-???"))) == grpc.StatusCode.NOT_FOUND


def test_commit_after_release_is_failed_precondition(inventory):
    reserve(inventory, "R-CR", (sku_with(2), 1))
    inventory.Release(pb.ReleaseRequest(reservation_id="R-CR"))
    assert code_of(lambda: inventory.Commit(pb.CommitRequest(reservation_id="R-CR"))) == grpc.StatusCode.FAILED_PRECONDITION


# ---------------- release ----------------

def test_release_returns_stock_and_is_idempotent(inventory):
    sku = sku_with(4)
    reserve(inventory, "R-REL", (sku, 3))
    inventory.Release(pb.ReleaseRequest(reservation_id="R-REL", reason="payment declined"))
    inventory.Release(pb.ReleaseRequest(reservation_id="R-REL", reason="again"))  # no second give-back
    lvl = stock_of(inventory, sku)[sku]
    assert (lvl.on_hand, lvl.reserved, lvl.available) == (4, 0, 4)


def test_replaying_reserve_on_a_released_id_is_failed_precondition(inventory):
    sku = sku_with(4)
    reserve(inventory, "R-DEAD", (sku, 1))
    inventory.Release(pb.ReleaseRequest(reservation_id="R-DEAD"))
    assert code_of(lambda: reserve(inventory, "R-DEAD", (sku, 1))) == grpc.StatusCode.FAILED_PRECONDITION


def test_release_of_unknown_reservation_is_a_noop(inventory):
    inventory.Release(pb.ReleaseRequest(reservation_id="R-never-created"))  # saga compensation may race a crash


def test_release_after_commit_is_failed_precondition(inventory):
    reserve(inventory, "R-RC", (sku_with(2), 1))
    inventory.Commit(pb.CommitRequest(reservation_id="R-RC"))
    assert code_of(lambda: inventory.Release(pb.ReleaseRequest(reservation_id="R-RC"))) == grpc.StatusCode.FAILED_PRECONDITION


def expire(url, rid):
    with psycopg.connect(url) as conn:
        conn.execute("UPDATE reservations SET expires_at = now() - interval '1 minute' WHERE reservation_id = %s", (rid,))


def test_expired_reservation_releases_its_stock(inventory, inventory_running, loop_thread):
    from services.inventory import db
    sku = sku_with(4)
    reserve(inventory, "R-EXP", (sku, 3))
    expire(inventory_running.url, "R-EXP")
    swept = loop_thread.call(db.expire_due(inventory_running.service.pool))
    assert swept == 1
    assert stock_of(inventory, sku)[sku].available == 4
    assert query(inventory_running.url, "SELECT state FROM reservations WHERE reservation_id = 'R-EXP'") == [("EXPIRED",)]
    assert code_of(lambda: inventory.Commit(pb.CommitRequest(reservation_id="R-EXP"))) == grpc.StatusCode.FAILED_PRECONDITION
    inventory.Release(pb.ReleaseRequest(reservation_id="R-EXP"))  # no-op, not an error


def test_sweeper_leaves_live_and_committed_reservations_alone(inventory, inventory_running, loop_thread):
    from services.inventory import db
    sku = sku_with(4)
    reserve(inventory, "R-LIVE", (sku, 1))
    reserve(inventory, "R-DONE", (sku, 1))
    inventory.Commit(pb.CommitRequest(reservation_id="R-DONE"))
    expire(inventory_running.url, "R-DONE")  # expiry time is irrelevant once committed
    assert loop_thread.call(db.expire_due(inventory_running.service.pool)) == 0
    lvl = stock_of(inventory, sku)[sku]
    assert (lvl.on_hand, lvl.reserved) == (3, 1)


def test_commit_on_unswept_expired_reservation_is_refused_and_frees_the_stock(inventory, inventory_running):
    sku = sku_with(4)
    reserve(inventory, "R-LATE", (sku, 2))
    expire(inventory_running.url, "R-LATE")
    assert code_of(lambda: inventory.Commit(pb.CommitRequest(reservation_id="R-LATE"))) == grpc.StatusCode.FAILED_PRECONDITION
    lvl = stock_of(inventory, sku)[sku]
    assert (lvl.on_hand, lvl.reserved) == (4, 0)  # the refused commit still expired it, so nothing stays held


def test_background_sweeper_expires_reservations_by_itself(loop_thread, temp_dbs, seed_dir):
    from services.inventory.server import create_service
    running = start(loop_thread, temp_dbs, "inventory", create_service, seed_dir=seed_dir, sweep_seconds=0.1)
    try:
        sku = sku_with(4)
        with authchan.channel(running.addr) as ch:
            stub = pb_grpc.InventoryServiceStub(ch)
            assert reserve(stub, "R-AUTO", (sku, 2), ttl=1).ok
            deadline = datetime.now() + timedelta(seconds=6)
            while datetime.now() < deadline and stock_of(stub, sku)[sku].reserved:
                asyncio.run(asyncio.sleep(0.2))
            assert stock_of(stub, sku)[sku].reserved == 0
    finally:
        stop(loop_thread, temp_dbs, running)


# ---------------- restock ----------------

def test_restock_adds_on_hand_once_per_idempotency_key(inventory):
    sku = sku_with(1)
    for _ in range(2):
        inventory.Restock(pb.RestockRequest(idempotency_key="refund:O-1", lines=lines((sku, 2))))
    lvl = stock_of(inventory, sku)[sku]
    assert (lvl.on_hand, lvl.available) == (3, 3)


def test_restock_with_unknown_sku_changes_nothing(inventory):
    sku = sku_with(1)
    code = code_of(lambda: inventory.Restock(pb.RestockRequest(idempotency_key="k", lines=lines((sku, 2), ("SKU-nope", 1)))))
    assert code == grpc.StatusCode.NOT_FOUND
    assert stock_of(inventory, sku)[sku].on_hand == 1
    # the failed attempt must not burn the key: a corrected retry with the same key still applies
    inventory.Restock(pb.RestockRequest(idempotency_key="k", lines=lines((sku, 2))))
    assert stock_of(inventory, sku)[sku].on_hand == 3


def test_restock_rejects_bad_input(inventory):
    assert code_of(lambda: inventory.Restock(pb.RestockRequest(idempotency_key="", lines=lines((sku_with(1), 1))))) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: inventory.Restock(pb.RestockRequest(idempotency_key="k"))) == grpc.StatusCode.INVALID_ARGUMENT


# ---------------- a reused idempotency key must mean the SAME request ----------------

def test_reserve_same_key_same_lines_in_any_order_is_the_same_result(inventory):
    a, b = sku_with(4), sku_with(3)
    first = reserve(inventory, "R-SAME", (a, 1), (b, 2))
    again = reserve(inventory, "R-SAME", (b, 2), (a, 1))  # same request, lines sent in another order
    assert first.ok and again.ok and first.expires_at == again.expires_at
    assert stock_of(inventory, a, b)[a].reserved == 1 and stock_of(inventory, a, b)[b].reserved == 2


def test_reserve_same_key_different_lines_is_already_exists_and_changes_nothing(inventory):
    a, b = sku_with(4), sku_with(3)
    assert reserve(inventory, "R-DIFF", (a, 1)).ok
    for other in ((a, 2), (b, 1)):
        assert code_of(lambda: reserve(inventory, "R-DIFF", other)) == grpc.StatusCode.ALREADY_EXISTS
    levels = stock_of(inventory, a, b)
    assert (levels[a].reserved, levels[b].reserved) == (1, 0)  # neither the original nor anything else moved


def test_reserve_key_reuse_is_detected_even_after_the_reservation_ended(inventory):
    sku = sku_with(4)
    reserve(inventory, "R-OLD", (sku, 1))
    inventory.Release(pb.ReleaseRequest(reservation_id="R-OLD"))
    assert code_of(lambda: reserve(inventory, "R-OLD", (sku, 2))) == grpc.StatusCode.ALREADY_EXISTS  # not FAILED_PRECONDITION


def test_restock_same_key_different_lines_is_already_exists_and_changes_nothing(inventory):
    sku = sku_with(1)
    inventory.Restock(pb.RestockRequest(idempotency_key="refund:O-9", lines=lines((sku, 2))))
    code = code_of(lambda: inventory.Restock(pb.RestockRequest(idempotency_key="refund:O-9", lines=lines((sku, 5)))))
    assert code == grpc.StatusCode.ALREADY_EXISTS
    assert stock_of(inventory, sku)[sku].on_hand == 3  # only the first restock applied
    inventory.Restock(pb.RestockRequest(idempotency_key="refund:O-9", lines=lines((sku, 2))))  # the true replay is fine
    assert stock_of(inventory, sku)[sku].on_hand == 3
