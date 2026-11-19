"""CustomerService (5.1: users only; the cart RPCs arrive in 5.2 and must say so honestly)."""
import grpc
import psycopg
import pytest

from shopassist_proto import shopassist_pb2 as pb
from tests.fixtures import USERS


def test_list_users_returns_the_seeded_users_in_id_order(customer):
    users = customer.ListUsers(pb.ListUsersRequest()).users
    assert [(u.user_id, u.name, u.email, u.city) for u in users] == [
        (u["user_id"], u["name"], u["email"], u["city"]) for u in sorted(USERS, key=lambda u: u["user_id"])
    ]


def test_get_user(customer):
    u = customer.GetUser(pb.GetUserRequest(user_id="U-02"))
    assert (u.user_id, u.name) == ("U-02", "Grace Hopper")


def test_get_unknown_user_is_not_found(customer):
    with pytest.raises(grpc.RpcError) as e:
        customer.GetUser(pb.GetUserRequest(user_id="U-99"))
    assert e.value.code() == grpc.StatusCode.NOT_FOUND


def test_seeded_users_have_carts_with_a_per_line_quantity_limit(customer_running):
    with psycopg.connect(customer_running.url) as conn:
        conn.execute("INSERT INTO cart_lines (user_id, sku_id, product_id, quantity) VALUES ('U-01', 'S', 'P', 10)")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO cart_lines (user_id, sku_id, product_id, quantity) VALUES ('U-01', 'S2', 'P', 11)")
        conn.rollback()


# ---------------- carts (5.2) ----------------
import asyncio  # noqa: E402

from shopassist_proto import shopassist_pb2_grpc as pb_grpc  # noqa: E402
from tests.conftest import run  # noqa: E402


def as_user(user_id):
    return (("x-user-id", user_id),)


def set_line(stub, user, sku, qty, product="P-1"):
    return stub.SetCartLine(pb.SetCartLineRequest(sku_id=sku, product_id=product, quantity=qty), metadata=as_user(user))


def code_of(fn):
    with pytest.raises(grpc.RpcError) as e:
        fn()
    return e.value.code()


def test_a_new_cart_is_empty_at_version_zero(customer):
    cart = customer.GetCart(pb.GetCartRequest(), metadata=as_user("U-03"))
    assert (cart.user_id, list(cart.lines), cart.version) == ("U-03", [], 0)


def test_cart_calls_need_a_known_user(customer):
    assert code_of(lambda: customer.GetCart(pb.GetCartRequest())) == grpc.StatusCode.UNAUTHENTICATED
    assert code_of(lambda: customer.GetCart(pb.GetCartRequest(), metadata=as_user("U-99"))) == grpc.StatusCode.NOT_FOUND
    assert code_of(lambda: set_line(customer, "U-99", "SKU-1", 1)) == grpc.StatusCode.NOT_FOUND


def test_version_bumps_on_every_real_change_and_only_then(customer):
    u = "U-04"
    assert set_line(customer, u, "SKU-1", 2).version == 1
    assert set_line(customer, u, "SKU-1", 2).version == 1  # same quantity: nothing changed
    cart = set_line(customer, u, "SKU-1", 3)
    assert cart.version == 2 and [(l.sku_id, l.quantity) for l in cart.lines] == [("SKU-1", 3)]
    assert set_line(customer, u, "SKU-1", 0).version == 3 and not customer.GetCart(pb.GetCartRequest(), metadata=as_user(u)).lines
    assert set_line(customer, u, "SKU-1", 0).version == 3  # removing what is not there changes nothing


def test_quantity_must_be_zero_to_ten_and_lines_need_ids(customer):
    for qty in (11, -1):
        assert code_of(lambda: set_line(customer, "U-05", "SKU-1", qty)) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: set_line(customer, "U-05", "", 1)) == grpc.StatusCode.INVALID_ARGUMENT
    assert code_of(lambda: set_line(customer, "U-05", "SKU-1", 1, product="")) == grpc.StatusCode.INVALID_ARGUMENT
    assert set_line(customer, "U-05", "SKU-1", 10).lines[0].quantity == 10
    assert customer.GetCart(pb.GetCartRequest(), metadata=as_user("U-05")).version == 1  # rejected calls changed nothing


def test_carts_belong_to_their_user(customer):
    set_line(customer, "U-06", "SKU-6", 1)
    assert not customer.GetCart(pb.GetCartRequest(), metadata=as_user("U-07")).lines


def test_clear_cart_checks_the_version_it_was_given(customer):
    u = "U-08"
    set_line(customer, u, "SKU-1", 1)
    cart = set_line(customer, u, "SKU-2", 1)
    assert code_of(lambda: customer.ClearCart(pb.ClearCartRequest(if_version=cart.version - 1), metadata=as_user(u))) == grpc.StatusCode.ABORTED
    assert len(customer.GetCart(pb.GetCartRequest(), metadata=as_user(u)).lines) == 2  # the mismatch cleared nothing
    cleared = customer.ClearCart(pb.ClearCartRequest(if_version=cart.version), metadata=as_user(u))
    assert not cleared.lines and cleared.version == cart.version + 1
    set_line(customer, u, "SKU-3", 1)
    assert not customer.ClearCart(pb.ClearCartRequest(), metadata=as_user(u)).lines  # 0 = unconditional


def test_concurrent_edits_lose_nothing_and_count_every_change(customer_running):
    async def scenario():
        async with grpc.aio.insecure_channel(customer_running.addr) as ch:
            stub = pb_grpc.CustomerServiceStub(ch)
            await asyncio.gather(*(
                stub.SetCartLine(pb.SetCartLineRequest(sku_id=f"SKU-{i}", product_id="P-1", quantity=1), metadata=as_user("U-09"))
                for i in range(12)
            ))
            return await stub.GetCart(pb.GetCartRequest(), metadata=as_user("U-09"))

    cart = run(scenario())
    assert len(cart.lines) == 12 and cart.version == 12
