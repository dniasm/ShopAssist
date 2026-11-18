"""Start the three gRPC microservices and the orchestrator as subprocesses.

Usage (from the repo root):
    python scripts/run_all.py
    python scripts/run_all.py --reset     # delete the three SQLite DBs first

- Uses the same Python interpreter that runs this script.
- Loads .env from the repo root if it exists.
- Prefixes every child's output with its name, e.g. [product] ...
- Ctrl+C, or any child exiting, stops all of them.
- --reset deletes the databases so the seed data is loaded again. Order tracking
  writes status changes to orders.db, so run --reset to repeat a live-tracking demo.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (name, module) pairs. Started in this order.
PROCESSES = [
    ("product", "services.product.server"),
    ("inventory", "services.inventory.server"),
    ("order", "services.order.server"),
    ("orchestrator", "orchestrator.main"),
]

STOP_TIMEOUT_SECONDS = 5
POLL_INTERVAL_SECONDS = 0.5

# (env var, default path) for each service database. Same rules as services/common:
# the env var wins, and a relative path is resolved against the repo root.
DB_SETTINGS = [
    ("PRODUCT_DB", "data/products.db"),
    ("INVENTORY_DB", "data/inventory.db"),
    ("ORDER_DB", "data/orders.db"),
]

# SQLite may leave these sidecar files next to a database (WAL and journal modes).
DB_SIDECAR_SUFFIXES = ["-wal", "-shm", "-journal"]

_print_lock = threading.Lock()


def _say(line: str) -> None:
    with _print_lock:
        print(line, flush=True)


def _db_path(env_name: str, default: str) -> Path:
    raw = os.environ.get(env_name) or default
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


def reset_databases() -> None:
    """Delete the three service databases (and any sidecar files) so they re-seed on start."""
    for env_name, default in DB_SETTINGS:
        db = _db_path(env_name, default)
        candidates = [db] + [Path(str(db) + s) for s in DB_SIDECAR_SUFFIXES]
        for path in candidates:
            if path.exists():
                path.unlink()
                _say(f"[run_all] removed {path}")
            elif path == db:
                _say(f"[run_all] nothing to remove at {path}")


def _load_dotenv() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _say("[run_all] python-dotenv not installed; skipping .env")
        return
    load_dotenv(env_file, override=False)
    _say(f"[run_all] loaded {env_file}")


def _pump(name: str, stream) -> None:
    """Copy one child's output to our stdout, one prefixed line at a time."""
    try:
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            _say(f"[{name}] {text}")
    finally:
        stream.close()


def _start(name: str, module: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    threading.Thread(target=_pump, args=(name, proc.stdout), daemon=True).start()
    _say(f"[run_all] started {name} (pid {proc.pid}): {sys.executable} -m {module}")
    return proc


def _stop_all(children: list[tuple[str, subprocess.Popen]]) -> None:
    for name, proc in children:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    for name, proc in children:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _say(f"[run_all] {name} did not stop in time; killing")
            proc.kill()
            proc.wait()
    for name, proc in children:
        _say(f"[run_all] {name} exited with code {proc.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the ShopAssist services and orchestrator.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the service databases before starting, so seed data is loaded again",
    )
    args = parser.parse_args()

    # Make Windows consoles tolerate any characters a child prints.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    _load_dotenv()

    if args.reset:
        reset_databases()

    children: list[tuple[str, subprocess.Popen]] = []
    exit_code = 0
    try:
        for name, module in PROCESSES:
            children.append((name, _start(name, module)))

        # Wait until something dies or the user presses Ctrl+C.
        while True:
            for name, proc in children:
                code = proc.poll()
                if code is not None:
                    _say(f"[run_all] {name} stopped on its own (exit code {code}); stopping the rest")
                    exit_code = code or 1
                    return exit_code
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        _say("[run_all] Ctrl+C received; stopping all processes")
        return 0
    finally:
        _stop_all(children)


if __name__ == "__main__":
    sys.exit(main())
