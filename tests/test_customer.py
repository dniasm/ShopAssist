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


def test_cart_rpcs_are_unimplemented_until_5_2(customer):
    for call, req in ((customer.GetCart, pb.GetCartRequest()), (customer.ClearCart, pb.ClearCartRequest())):
        with pytest.raises(grpc.RpcError) as e:
            call(req, metadata=(("x-user-id", "U-01"),))
        assert e.value.code() == grpc.StatusCode.UNIMPLEMENTED


def test_seeded_users_have_carts_with_a_per_line_quantity_limit(customer_running):
    with psycopg.connect(customer_running.url) as conn:
        conn.execute("INSERT INTO cart_lines (user_id, sku_id, product_id, quantity) VALUES ('U-01', 'S', 'P', 10)")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO cart_lines (user_id, sku_id, product_id, quantity) VALUES ('U-01', 'S2', 'P', 11)")
        conn.rollback()
