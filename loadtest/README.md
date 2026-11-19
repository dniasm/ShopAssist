# Load test

A [Locust](https://locust.io) script that drives the orchestrator's REST API like a crowd of shoppers.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe scripts/pg.py start
.venv\Scripts\python.exe scripts/run_all.py               # the stack, in its own window

# headless: 25 users, 5 new users per second, 60 seconds, CSV files in loadtest/out/
.venv\Scripts\locust.exe -f loadtest/locustfile.py --host http://127.0.0.1:8011 --headless -u 25 -r 5 -t 60s --csv loadtest/out/run25

# or with Locust's web UI on http://localhost:8089
.venv\Scripts\locust.exe -f loadtest/locustfile.py --host http://127.0.0.1:8011
```

What a simulated user does: signs in as a random demo user (U-01..U-06), then repeats a weighted mix of browsing the
catalog (sort, search, category and price filters), opening product pages, adding stocked SKUs to the cart, viewing the
cart, checking out (80 % `card_ok`, 20 % `card_declined`) and listing orders. Checkout answers 409/422 (cart emptied or
short of stock by another simulated user on the same demo account) are business outcomes, not failures; they are tallied
and printed at the end of the run.

Options:

- `--with-chat` also sends chat messages (`POST /api/chat`). **Off by default**: with an LLM key configured, every
  message spends the shared Groq quota.
- `LOADTEST_THINK=0` (environment variable) removes the 0.5-2 s think time, to find where the system saturates.

The test writes orders and moves stock, so reset afterwards: `scripts/run_all.py --reset`. Never point it at port 8000
(another app). The recorded numbers are in [RESULTS.md](RESULTS.md).
