# Load-test results

Recorded 2026-11-19 with `loadtest/locustfile.py` (Locust 2.47, headless, 60 s per run, spawn rate 5 users/s, chat off).

## Machine and set-up

- Laptop: AMD Ryzen 5 5600H (6 cores / 12 threads), about 6 GB RAM reported by Windows, Windows 11.
- **Everything ran on this one machine**: Locust, the orchestrator (one uvicorn process on 8011), the five gRPC services
  (one process each), and Postgres 17. The load generator competes with the system under test for the same CPU, so
  these numbers are a floor for what the code can do, not a server benchmark.
- Data: the standard seed (400 products, 6 demo users). `PAYMENT_LATENCY_MS=300`, so a checkout's saga takes ~0.3 s of
  simulated payment time, but `POST /api/checkout` returns 202 as soon as the order id exists, so that time is not in
  the numbers below (progress arrives on the WebSocket, which this test does not open).
- Each simulated user signs in as one of U-01..U-06 and loops over: browse, search, filter, product page, add to cart,
  view cart, checkout (80 % `card_ok`, 20 % `card_declined`) and list orders, with 0.5-2 s of think time.
- With 25 or 50 users, several simulated users share one of the 6 demo accounts, and so share a cart. A checkout can
  then find the cart already emptied by another simulated user (HTTP 422) or short of stock (409). These are counted
  as successes in the table (they are correct answers), and tallied at the end of the run: {10 users: 2, 25 users: 7, 50 users: 4}
  empty-cart checkouts in total (each run's count is in its console output).

## Summary

| Users | Requests | req/s | Median ms | p95 ms | p99 ms | Failure rate |
|---|---|---|---|---|---|---|
| 10 | 508 | 8.7 | 11 | 33 | 190 | 0.00 % |
| 25 | 1230 | 21.1 | 10 | 23 | 41 | 0.00 % |
| 50 | 2336 | 40.0 | 12 | 32 | 50 | 0.00 % |
| 50 (no think time) | 10322 | 177.0 | 200 | 630 | 870 | 0.00 % |

"No think time" is a saturation run: `LOADTEST_THINK=0`, 50 users firing back to back (spawn rate 25/s).

## Reading the numbers (honest notes)

- **Zero failures in every run**, including the saturation run. No request returned a 5xx or timed out.
- With normal think time, req/s grows with the number of users (8.7, 21, 38-40) and latency stays flat (p95 ~ 25-35 ms).
  That means 10-50 users is **below the knee**: the system was waiting for the simulated users, not the other way round.
  The p95 of 330 ms in the 10-user run is the first few requests after the sign-in burst (cold pools and caches), not a trend.
- Without think time (50 users), throughput reached about 177 req/s and the median rose to ~200 ms (p95 ~ 630 ms): the
  requests now queue. This is where the knee is on this laptop.
- **Bottleneck.** I did not profile. A separate 20 s sampling of per-process CPU during the saturation run showed no
  single process pinned at 100 % of a core: inventory ~75 %, order ~65 %, orchestrator ~60 %, catalog ~35 %, customer
  ~15 %, Postgres backends 3-7 % each. So the limit was the whole machine being shared by ~25 busy processes (Locust
  included) and the chain of gRPC hops each request makes (a cart or product page asks Catalog, Inventory and Customer),
  with the single-process orchestrator and Inventory (called for stock on almost every request) the first to saturate.
  Likely next steps if more throughput were needed: run the load generator on another machine, batch the Inventory lookups,
  or run more than one orchestrator process (blocked today by the in-memory per-user WebSocket event log).
- Not covered: the WebSocket paths (live checkout progress, order tracking), and chat (opt-in, see README).

### 10 users

| Endpoint | Requests | req/s | Median ms | p95 ms | Failures |
|---|---|---|---|---|---|
| `GET /api/cart` | 19 | 0.3 | 8 | 13 | 0 |
| `GET /api/orders` | 37 | 0.6 | 7 | 19 | 0 |
| `GET /api/products` | 84 | 1.4 | 12 | 330 | 0 |
| `GET /api/products/{id}` | 98 | 1.7 | 8 | 23 | 0 |
| `GET /api/products?category=` | 65 | 1.1 | 11 | 30 | 0 |
| `GET /api/products?q=` | 78 | 1.3 | 11 | 39 | 0 |
| `POST /api/checkout` | 40 | 0.7 | 11 | 29 | 0 |
| `POST /api/session` | 10 | 0.2 | 140 | 190 | 0 |
| `PUT /api/cart/lines/{sku}` | 77 | 1.3 | 11 | 26 | 0 |

### 25 users

| Endpoint | Requests | req/s | Median ms | p95 ms | Failures |
|---|---|---|---|---|---|
| `GET /api/cart` | 52 | 0.9 | 8 | 12 | 0 |
| `GET /api/orders` | 113 | 1.9 | 6 | 12 | 0 |
| `GET /api/products` | 204 | 3.5 | 12 | 23 | 0 |
| `GET /api/products/{id}` | 231 | 4.0 | 7 | 14 | 0 |
| `GET /api/products?category=` | 178 | 3.1 | 11 | 21 | 0 |
| `GET /api/products?q=` | 154 | 2.6 | 11 | 36 | 0 |
| `POST /api/checkout` | 88 | 1.5 | 10 | 27 | 0 |
| `POST /api/session` | 25 | 0.4 | 12 | 28 | 0 |
| `PUT /api/cart/lines/{sku}` | 185 | 3.2 | 12 | 23 | 0 |

### 50 users

| Endpoint | Requests | req/s | Median ms | p95 ms | Failures |
|---|---|---|---|---|---|
| `GET /api/cart` | 60 | 1.0 | 10 | 27 | 0 |
| `GET /api/orders` | 222 | 3.8 | 8 | 19 | 0 |
| `GET /api/products` | 474 | 8.1 | 14 | 39 | 0 |
| `GET /api/products/{id}` | 423 | 7.2 | 8 | 20 | 0 |
| `GET /api/products?category=` | 319 | 5.5 | 12 | 27 | 0 |
| `GET /api/products?q=` | 297 | 5.1 | 13 | 41 | 0 |
| `POST /api/checkout` | 160 | 2.7 | 12 | 28 | 0 |
| `POST /api/session` | 50 | 0.9 | 20 | 30 | 0 |
| `PUT /api/cart/lines/{sku}` | 331 | 5.7 | 14 | 30 | 0 |

### 50 (no think time) users

| Endpoint | Requests | req/s | Median ms | p95 ms | Failures |
|---|---|---|---|---|---|
| `GET /api/cart` | 310 | 5.3 | 250 | 660 | 0 |
| `GET /api/orders` | 1034 | 17.7 | 110 | 220 | 0 |
| `GET /api/products` | 1690 | 29.0 | 200 | 630 | 0 |
| `GET /api/products/{id}` | 2050 | 35.2 | 190 | 620 | 0 |
| `GET /api/products?category=` | 1396 | 23.9 | 210 | 650 | 0 |
| `GET /api/products?q=` | 1418 | 24.3 | 200 | 690 | 0 |
| `POST /api/checkout` | 688 | 11.8 | 130 | 260 | 0 |
| `POST /api/session` | 50 | 0.9 | 89 | 190 | 0 |
| `PUT /api/cart/lines/{sku}` | 1686 | 28.9 | 270 | 700 | 0 |
