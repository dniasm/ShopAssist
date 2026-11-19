"""Locust load test for the ShopAssist orchestrator (REST only, no WebSocket).

Run (see loadtest/README.md):
    .venv\Scripts\locust.exe -f loadtest/locustfile.py --host http://127.0.0.1:8011 --headless -u 25 -r 5 -t 60s

Every simulated user signs in (email and DEMO_PASSWORD) as one of the demo users U-01..U-06, then repeats a weighted mix of
browse / search / filter / product page / cart / checkout / orders. Chat is OFF by default (it spends the
shared LLM quota); pass --with-chat to enable it.
"""
import os
import random
import uuid
from collections import Counter

from locust import HttpUser, between, events, task

# The six seeded demo accounts (data/seed/v2/customers.json); they share DEMO_PASSWORD (default "shopassist-demo").
DEMO_EMAILS = ["priya.raman@example.com", "tomas.lindqvist@example.com", "hana.mori@example.com",
               "daniel.osei@example.com", "lucia.ferrand@example.com", "samuel.adeyemi@example.com"]
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD") or "shopassist-demo"
CATEGORIES = ["accessories", "backpacks", "headphones", "hoodies", "jackets", "jeans", "shoes",
              "sunglasses", "t-shirts", "watches"]
SEARCHES = ["jacket", "black hoodie", "running shoes", "wireless headphones", "leather", "watch",
            "backpack", "sunglases", "denim", "gift"]
SORTS = ["relevance", "price_asc", "price_desc", "rating", "newest"]
CHAT_MESSAGES = ["show me black hoodies under 80", "what is in my cart?", "where is my last order?"]

# Business outcomes that are not failures of the system. They are tallied and printed at the end
# instead of being counted as HTTP failures (see RESULTS.md).
OUTCOMES: Counter = Counter()


@events.init_command_line_parser.add_listener
def _add_args(parser):
    parser.add_argument("--with-chat", action="store_true", default=False,
                        help="also send chat messages (spends the shared LLM quota; default off)")


@events.init.add_listener
def _enable_chat(environment, **_):
    if getattr(environment.parsed_options, "with_chat", False):
        ChatShopper.weight = 1


@events.quitting.add_listener
def _print_outcomes(environment, **_):
    if OUTCOMES:
        print("Business outcomes (counted as success):", dict(OUTCOMES))


class Shopper(HttpUser):
    # Think time between actions. LOADTEST_THINK=0 removes it (a saturation run: every user fires as fast as it can).
    wait_time = between(0.5, 2.0) if os.environ.get("LOADTEST_THINK", "1") != "0" else between(0, 0.05)
    token: str = ""
    seen_products: list
    seen_skus: list

    def on_start(self):
        self.seen_products = []
        self.seen_skus = []
        email = random.choice(DEMO_EMAILS)
        with self.client.post("/api/session", json={"email": email, "password": DEMO_PASSWORD}, name="POST /api/session",
                              catch_response=True) as r:
            if r.status_code == 200:
                self.token = r.json()["token"]
            else:
                r.failure(f"sign-in {r.status_code}")
        self.auth = {"Authorization": f"Bearer {self.token}"}
        self._list({})  # prime product ids

    # ---- helpers -------------------------------------------------------------------------------
    def _list(self, params: dict, name: str = "GET /api/products"):
        r = self.client.get("/api/products", params={"page_size": 24, **params}, name=name)
        if r.status_code == 200:
            self.seen_products = [p["product_id"] for p in r.json()["products"]] or self.seen_products
        return r

    def _expected(self, r, ok_codes: set, outcome_codes: dict):
        """Mark r a success if its status is in ok_codes, or a tallied business outcome."""
        if r.status_code in ok_codes:
            r.success()
        elif r.status_code in outcome_codes:
            OUTCOMES[outcome_codes[r.status_code]] += 1
            r.success()
        else:
            r.failure(f"HTTP {r.status_code}: {r.text[:120]}")

    # ---- browsing ------------------------------------------------------------------------------
    @task(5)
    def browse(self):
        self._list({"sort": random.choice(SORTS)})

    @task(4)
    def search(self):
        self._list({"q": random.choice(SEARCHES)}, name="GET /api/products?q=")

    @task(4)
    def filter(self):
        params = {"category": random.choice(CATEGORIES), "sort": random.choice(SORTS)}
        if random.random() < 0.5:
            params.update(min_price_cents=2000, max_price_cents=15000)
        self._list(params, name="GET /api/products?category=")

    @task(6)
    def product_page(self):
        if not self.seen_products:
            return
        pid = random.choice(self.seen_products)
        with self.client.get(f"/api/products/{pid}", name="GET /api/products/{id}", catch_response=True) as r:
            if r.status_code == 200:
                self.seen_skus = [s for s in r.json()["stock"] if s["available"] >= 2]
                r.success()
            else:
                r.failure(f"HTTP {r.status_code}")

    # ---- cart and checkout ---------------------------------------------------------------------
    @task(3)
    def add_to_cart(self):
        if not self.seen_skus:
            return
        sku = random.choice(self.seen_skus)
        self.client.put(f"/api/cart/lines/{sku['sku_id']}", json={"quantity": random.randint(1, 2)},
                        headers=self.auth, name="PUT /api/cart/lines/{sku}")

    @task(1)
    def view_cart(self):
        self.client.get("/api/cart", headers=self.auth, name="GET /api/cart")

    @task(2)
    def checkout(self):
        self.add_to_cart()
        method = "card_declined" if random.random() < 0.2 else "card_ok"
        body = {"idempotency_key": uuid.uuid4().hex, "payment_method": method}
        with self.client.post("/api/checkout", json=body, headers=self.auth, name="POST /api/checkout",
                              catch_response=True) as r:
            # card_declined is reported on the WebSocket (the HTTP call is still 202). 409/422 = the
            # cart was emptied or short of stock by another simulated user sharing the same demo account.
            self._expected(r, {202}, {409: "checkout_conflict_or_out_of_stock", 422: "checkout_empty_cart"})

    @task(3)
    def list_orders(self):
        self.client.get("/api/orders", params={"page_size": 10}, headers=self.auth, name="GET /api/orders")


class ChatShopper(Shopper):
    """Only spawned with --with-chat (weight 0 otherwise)."""
    weight = 0

    @task(2)
    def chat(self):
        self.client.post("/api/chat", json={"message": random.choice(CHAT_MESSAGES),
                                            "context": {"path": "/"}},
                         headers=self.auth, name="POST /api/chat")
