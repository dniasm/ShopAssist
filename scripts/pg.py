"""Start, stop or check the local PostgreSQL 17 cluster.

Usage (from the repo root):
    python scripts/pg.py start | stop | status

Reads PG_BIN (binaries directory) and PG_DATA (cluster directory) from .env.
Listens on localhost:5432 only. Server log: <PG_DATA>/../pgdata17.log
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
PORT = 5432
WAIT_SECONDS = 60


def _config() -> tuple[Path, Path, Path]:
    env = dotenv_values(REPO_ROOT / ".env")
    bin_dir = env.get("PG_BIN") or os.environ.get("PG_BIN")
    data = env.get("PG_DATA") or os.environ.get("PG_DATA")
    if not bin_dir or not data:
        sys.exit("pg: PG_BIN and PG_DATA must be set in .env")
    exe = Path(bin_dir) / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
    if not exe.is_file():
        sys.exit("pg: pg_ctl not found under PG_BIN")
    data_path = Path(data)
    return exe, data_path, data_path.parent / "pgdata17.log"


def _ctl(exe: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(exe), *args], capture_output=True, text=True)


def _state(exe: Path, data: Path) -> str:
    result = _ctl(exe, "status", "-D", str(data))
    if result.returncode == 0:
        return "running"
    if result.returncode == 3:  # pg_ctl: no server running
        return "stopped"
    return "unknown"


def cmd_status(exe: Path, data: Path, _log: Path) -> int:
    state = _state(exe, data)
    print(f"{state}, port {PORT}")
    return 0 if state != "unknown" else 1


def cmd_start(exe: Path, data: Path, log: Path) -> int:
    if _state(exe, data) == "running":
        print(f"running, port {PORT} (already running)")
        return 0
    result = _ctl(
        exe, "start", "-D", str(data), "-l", str(log), "-w", "-t", str(WAIT_SECONDS),
        "-o", f"-p {PORT} -c listen_addresses=localhost",
    )
    state = _state(exe, data)
    if result.returncode != 0 or state != "running":
        print(f"pg: start failed (pg_ctl exit {result.returncode}); see {log}")
        return 1
    print(f"running, port {PORT}")
    return 0


def cmd_stop(exe: Path, data: Path, _log: Path) -> int:
    if _state(exe, data) == "stopped":
        print(f"stopped, port {PORT} (already stopped)")
        return 0
    result = _ctl(exe, "stop", "-D", str(data), "-m", "fast", "-w", "-t", str(WAIT_SECONDS))
    state = _state(exe, data)
    if result.returncode != 0 or state != "stopped":
        print(f"pg: stop failed (pg_ctl exit {result.returncode})")
        return 1
    print(f"stopped, port {PORT}")
    return 0


COMMANDS = {"start": cmd_start, "stop": cmd_stop, "status": cmd_status}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print("usage: python scripts/pg.py start | stop | status", file=sys.stderr)
        return 2
    exe, data, log = _config()
    return COMMANDS[sys.argv[1]](exe, data, log)


if __name__ == "__main__":
    sys.exit(main())
