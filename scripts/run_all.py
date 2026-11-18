"""Start the three gRPC microservices and the orchestrator as subprocesses.

Usage (from the repo root):
    python scripts/run_all.py

- Uses the same Python interpreter that runs this script.
- Loads .env from the repo root if it exists.
- Prefixes every child's output with its name, e.g. [product] ...
- Ctrl+C, or any child exiting, stops all of them.
"""

from __future__ import annotations

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

_print_lock = threading.Lock()


def _say(line: str) -> None:
    with _print_lock:
        print(line, flush=True)


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
    # Make Windows consoles tolerate any characters a child prints.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    _load_dotenv()

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
