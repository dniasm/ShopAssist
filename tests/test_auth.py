"""Logins: password hashing, the Customer service's Authenticate/CreateUser, and the gateway's sign-in, sign-up,
invite code and wrong-password throttling."""
import grpc
import psycopg
import pytest

from orchestrator.app import create_app
from orchestrator.login_limits import MAX_ADDRESS_FAILURES, MAX_FAILURES, WINDOW, LoginLimits
from orchestrator.settings import Settings
from tests import authchan
from services.customer.passwords import check_password, hash_password
from shopassist_proto import shopassist_pb2 as pb
from shopassist_proto import shopassist_pb2_grpc as pb_grpc
from tests.conftest import AppClient
from tests.fixtures import DEMO_PASSWORD, EMAIL_OF, USERS


# ---------------- hashing ----------------

def test_a_hash_is_salted_pbkdf2_and_only_the_right_password_matches_it():
    a, b = hash_password("correct horse", rounds=1000), hash_password("correct horse", rounds=1000)
    assert a.startswith("pbkdf2_sha256$1000$") and a != b  # a fresh salt every time
    assert check_password("correct horse", a) and not check_password("wrong horse", a)
    assert not check_password("anything", "") and not check_password("anything", "md5$x$y$z")


# ---------------- Customer service ----------------

@pytest.fixture
def fresh_customer(fresh_customer_running):
    with authchan.channel(fresh_customer_running.addr) as ch:
        yield pb_grpc.CustomerServiceStub(ch)


def code_of(call):
    with pytest.raises(grpc.RpcError) as e:
        call()
    return e.value.code()


def test_seeded_users_sign_in_with_the_demo_password_which_is_stored_hashed(fresh_customer, fresh_customer_running):
    user = fresh_customer.Authenticate(pb.AuthenticateRequest(email=EMAIL_OF["U-01"].upper(), password=DEMO_PASSWORD))
    assert user.user_id == "U-01"
    with psycopg.connect(fresh_customer_running.url) as conn:
        hashes = [h for (h,) in conn.execute("SELECT password_hash FROM users")]
    assert len(hashes) == len(USERS) and all(h.startswith("pbkdf2_sha256$") and DEMO_PASSWORD not in h for h in hashes)


def test_a_wrong_password_or_unknown_email_is_unauthenticated(fresh_customer):
    wrong = pb.AuthenticateRequest(email=EMAIL_OF["U-01"], password="nope-nope")
    unknown = pb.AuthenticateRequest(email="nobody@example.test", password=DEMO_PASSWORD)
    assert code_of(lambda: fresh_customer.Authenticate(wrong)) == grpc.StatusCode.UNAUTHENTICATED
    assert code_of(lambda: fresh_customer.Authenticate(unknown)) == grpc.StatusCode.UNAUTHENTICATED


def test_create_user_takes_the_next_id_gets_a_cart_and_can_sign_in(fresh_customer):
    user = fresh_customer.CreateUser(pb.CreateUserRequest(name="Katherine Johnson", email="kj@example.test", password="orbital-mechanics"))
    assert user.user_id == f"U-{len(USERS) + 1:02d}" and user.name == "Katherine Johnson"
    assert fresh_customer.Authenticate(pb.AuthenticateRequest(email="KJ@example.test", password="orbital-mechanics")).user_id == user.user_id
    cart = fresh_customer.GetCart(pb.GetCartRequest(), metadata=(("x-user-id", user.user_id),))
    assert cart.user_id == user.user_id and list(cart.lines) == []


def test_create_user_refuses_a_taken_email_in_any_case_and_bad_input(fresh_customer):
    taken = pb.CreateUserRequest(name="Ada again", email=EMAIL_OF["U-01"].upper(), password="long-enough")
    assert code_of(lambda: fresh_customer.CreateUser(taken)) == grpc.StatusCode.ALREADY_EXISTS
    for bad in (dict(name="", email="x@example.test", password="long-enough"),
                dict(name="X", email="not-an-email", password="long-enough"),
                dict(name="X", email="x@example.test", password="short")):
        assert code_of(lambda: fresh_customer.CreateUser(pb.CreateUserRequest(**bad))) == grpc.StatusCode.INVALID_ARGUMENT


# ---------------- the gateway ----------------

@pytest.fixture
def make_client(catalog_running, inventory_ro_running, fresh_customer_running, order_ro_running, payment_ro_running, tmp_path):
    """An app over a customer service of its own (sign-ups create users), optionally with an invite code."""
    clients = []

    def make(signup_code=""):
        settings = Settings(
            catalog_addr=catalog_running.addr, inventory_addr=inventory_ro_running.addr, customer_addr=fresh_customer_running.addr,
            order_addr=order_ro_running.addr, payment_addr=payment_ro_running.addr, session_secret="test-secret",
            llm_api_keys=(), signup_code=signup_code,
        )
        client = AppClient(create_app(settings, web_dist=tmp_path / "missing"))
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def error(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]["message"]


def test_sign_up_creates_an_account_and_signs_in_as_it(make_client):
    client = make_client()
    assert client.get("/api/signups").json() == {"signups": "open"}
    r = client.post("/api/signup", json={"name": "Mary Jackson", "email": "mary@example.test", "password": "wind-tunnel-1"})
    assert r.status_code == 201, r.text
    body = r.json()
    me = client.get("/api/me", headers={"Authorization": f"Bearer {body['token']}"}).json()
    assert me["user"] == body["user"] and body["user"]["email"] == "mary@example.test"
    again = client.post("/api/session", json={"email": "mary@example.test", "password": "wind-tunnel-1"})
    assert again.status_code == 200 and again.json()["user"]["user_id"] == body["user"]["user_id"]


def test_sign_up_explains_a_taken_email_and_bad_input(make_client):
    client = make_client()
    taken = client.post("/api/signup", json={"name": "Ada", "email": EMAIL_OF["U-01"], "password": "long-enough"})
    assert "already exists" in error(taken, 409, "conflict")
    error(client.post("/api/signup", json={"name": "X", "email": "x@example.test", "password": "short"}), 422, "invalid")
    bad_email = client.post("/api/signup", json={"name": "X", "email": "not-an-email", "password": "long-enough"})
    assert "email" in error(bad_email, 422, "invalid")


def test_with_an_invite_code_sign_up_needs_it(make_client):
    client = make_client(signup_code="let-me-in")
    assert client.get("/api/signups").json() == {"signups": "invite"}
    body = {"name": "Dorothy Vaughan", "email": "dorothy@example.test", "password": "fortran-77"}
    error(client.post("/api/signup", json=body), 403, "forbidden")
    error(client.post("/api/signup", json={**body, "invite": "wrong"}), 403, "forbidden")
    assert client.post("/api/signup", json={**body, "invite": " let-me-in "}).status_code == 201


def test_repeated_wrong_passwords_are_throttled_even_for_the_right_one(make_client):
    client = make_client()
    email = EMAIL_OF["U-04"]
    for _ in range(MAX_FAILURES):
        error(client.post("/api/session", json={"email": email, "password": "guess-guess"}), 401, "unauthenticated")
    error(client.post("/api/session", json={"email": email, "password": DEMO_PASSWORD}), 429, "rate_limited")
    # only that email is held back for this address; another account still signs in
    assert client.post("/api/session", json={"email": EMAIL_OF["U-05"], "password": DEMO_PASSWORD}).status_code == 200


def test_throttling_forgets_after_the_window_and_stops_an_address_guessing_many_emails():
    now = [0.0]
    limits = LoginLimits(clock=lambda: now[0])
    for _ in range(MAX_FAILURES):
        limits.failed("a@x.test", "1.2.3.4")
    assert limits.blocked("A@x.test", "1.2.3.4") and not limits.blocked("a@x.test", "5.6.7.8")
    now[0] += WINDOW + 1
    assert not limits.blocked("a@x.test", "1.2.3.4")
    for i in range(MAX_ADDRESS_FAILURES):
        limits.failed(f"user{i}@x.test", "1.2.3.4")
    assert limits.blocked("fresh@x.test", "1.2.3.4") and limits.address_blocked("1.2.3.4")
