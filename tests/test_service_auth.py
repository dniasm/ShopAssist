"""Service-to-service auth: every gRPC call but health Check must carry a valid, unexpired, correctly addressed token."""
import time

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from shopassist_proto import svcauth

LIST = "/shopassist.v2.CatalogService/ListProducts"
OK, UNAUTH, DENIED = None, grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED


def meta(caller="orchestrator", audience="catalog", method=LIST, key=None, expiry=None):
    key = key or svcauth.load_key(caller)
    expiry = int(time.time()) + 30 if expiry is None else expiry
    return ((svcauth.CALLER_HEADER, caller), (svcauth.TOKEN_HEADER, svcauth.sign(key, caller, audience, method, expiry)))


def code(addr, metadata, method=LIST, request=pb.ListProductsRequest(), response=pb.ListProductsResponse):
    """Status of one raw (unsigned unless `metadata` says so) unary call; None when it succeeded."""
    with grpc.insecure_channel(addr) as ch:
        call = ch.unary_unary(method, request_serializer=type(request).SerializeToString, response_deserializer=response.FromString)
        try:
            call(request, metadata=metadata, timeout=5)
        except grpc.RpcError as e:
            return e.code()
    return None


def test_no_token_is_unauthenticated(catalog_running):
    assert code(catalog_running.addr, None) == UNAUTH
    assert code(catalog_running.addr, ((svcauth.CALLER_HEADER, "orchestrator"),)) == UNAUTH  # caller alone is not enough


def test_wrong_key_is_unauthenticated(catalog_running):
    assert code(catalog_running.addr, meta(key=b"not-the-orchestrator-key")) == UNAUTH


def test_expired_token_is_unauthenticated(catalog_running):
    assert code(catalog_running.addr, meta(expiry=int(time.time()) - 5)) == UNAUTH


def test_token_valid_for_too_long_is_unauthenticated(catalog_running):
    assert code(catalog_running.addr, meta(expiry=int(time.time()) + 3600)) == UNAUTH


def test_token_for_another_audience_is_unauthenticated(catalog_running):
    assert code(catalog_running.addr, meta(audience="inventory")) == UNAUTH


def test_token_for_another_method_is_unauthenticated(catalog_running):
    assert code(catalog_running.addr, meta(method="/shopassist.v2.CatalogService/GetSkus")) == UNAUTH


def test_unknown_caller_and_garbage_tokens_are_unauthenticated(catalog_running):
    assert code(catalog_running.addr, ((svcauth.CALLER_HEADER, "mallory"), (svcauth.TOKEN_HEADER, "1.ab"))) == UNAUTH
    for token in ("", "x", "123", "abc.def", "99999999999.00"):
        assert code(catalog_running.addr, ((svcauth.CALLER_HEADER, "orchestrator"), (svcauth.TOKEN_HEADER, token))) == UNAUTH


def test_order_may_not_call_the_customer_service(customer_running):
    method = "/shopassist.v2.CustomerService/ListUsers"
    request = pb.ListUsersRequest()
    assert code(customer_running.addr, meta("order", "customer", method), method, request, pb.ListUsersResponse) == DENIED
    assert code(customer_running.addr, meta("orchestrator", "customer", method), method, request, pb.ListUsersResponse) == OK


def test_order_may_call_catalog_and_inventory(catalog_running, inventory_ro_running):
    assert code(catalog_running.addr, meta("order")) == OK
    method = "/shopassist.v2.InventoryService/GetStock"
    assert code(inventory_ro_running.addr, meta("order", "inventory", method), method, pb.GetStockRequest(), pb.GetStockResponse) != DENIED


def test_valid_token_works_and_carries_the_user_header(catalog_running, customer_running):
    assert code(catalog_running.addr, meta()) == OK
    method = "/shopassist.v2.CustomerService/GetCart"
    both = meta("orchestrator", "customer", method) + (("x-user-id", "U-01"),)
    assert code(customer_running.addr, both, method, pb.GetCartRequest(), pb.Cart) == OK


def test_x_user_id_alone_does_not_authenticate(customer_running):
    method = "/shopassist.v2.CustomerService/GetCart"
    assert code(customer_running.addr, (("x-user-id", "U-01"),), method, pb.GetCartRequest(), pb.Cart) == UNAUTH


def test_health_check_needs_no_token(catalog_running):
    with grpc.insecure_channel(catalog_running.addr) as ch:
        reply = health_pb2_grpc.HealthStub(ch).Check(health_pb2.HealthCheckRequest(service=""), timeout=5)
    assert reply.status == health_pb2.HealthCheckResponse.SERVING


def test_streaming_calls_are_rejected_without_a_token(order_ro_running):
    with grpc.insecure_channel(order_ro_running.addr) as ch:
        stream = pb_grpc.OrderServiceStub(ch).WatchOrders(pb.WatchOrdersRequest(after_event_id=-1), metadata=(("x-user-id", "U-01"),), timeout=5)
        with pytest.raises(grpc.RpcError) as e:
            next(stream)
    assert e.value.code() == UNAUTH


def test_signed_streaming_call_is_accepted(order_ro_running):
    from tests import authchan
    with authchan.channel(order_ro_running.addr) as ch:
        stream = pb_grpc.OrderServiceStub(ch).WatchOrders(pb.WatchOrdersRequest(after_event_id=-1), metadata=(("x-user-id", "U-01"),), timeout=5)
        first = next(stream)  # the opening cursor message
        stream.cancel()
    assert not first.order_id


def test_a_missing_key_stops_the_service_from_starting_and_never_names_a_value():
    with pytest.raises(RuntimeError, match="SVC_KEY_ORCHESTRATOR is not set") as e:
        svcauth.AuthInterceptor.for_service("catalog", {"SVC_KEY_ORDER": "secret-value"})
    assert "secret-value" not in str(e.value)
