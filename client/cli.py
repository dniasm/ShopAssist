"""ShopAssist command-line client.

Usage:
    python client/cli.py "Where is my order 1042?"
    python client/cli.py "Track order 1043 live" --base http://localhost:8000

Sends the query with POST /query, opens the returned WebSocket and prints each
event on one line. Exits 0 on final_answer, 1 on error, 2 if it cannot connect.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

DEFAULT_BASE = "http://localhost:8000"


def _ws_base(http_base: str) -> str:
    base = http_base.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    return base


def _format_event(event: dict) -> str:
    """Return one readable line for a single orchestrator event."""
    kind = event.get("type", "?")
    data = event.get("data") or {}
    ts = event.get("ts", "")
    time_part = ts[11:19] if len(ts) >= 19 else ts

    if kind == "accepted":
        detail = "query accepted"
    elif kind == "intent_detected":
        entities = json.dumps(data.get("entities", {}), ensure_ascii=False)
        detail = f"intent={data.get('intent')} source={data.get('source')} entities={entities}"
    elif kind == "service_call":
        detail = f"{data.get('service')}.{data.get('method')}"
    elif kind == "partial_result":
        detail = json.dumps(data, ensure_ascii=False)
    elif kind == "order_update":
        detail = json.dumps(data, ensure_ascii=False)
    elif kind == "final_answer":
        detail = f"answer: {data.get('answer')}"
    elif kind == "error":
        detail = f"message: {data.get('message', event.get('message'))}"
    else:
        detail = json.dumps(data, ensure_ascii=False)

    return f"[{time_part}] {kind:<16} {detail}"


def run(query: str, base: str) -> int:
    http_base = base.rstrip("/")
    try:
        response = httpx.post(f"{http_base}/query", json={"text": query}, timeout=15.0)
    except httpx.HTTPError as exc:
        print(f"Could not reach {http_base}: {exc}", file=sys.stderr)
        return 2

    if response.status_code != 202:
        print(f"POST /query failed: HTTP {response.status_code} {response.text}", file=sys.stderr)
        return 2

    body = response.json()
    ws_url = _ws_base(http_base) + body["ws_url"]
    print(f"query_id: {body['query_id']}")
    print(f"ws:       {ws_url}")

    try:
        with connect(ws_url, open_timeout=10) as ws:
            for raw in ws:
                event = json.loads(raw)
                print(_format_event(event), flush=True)
                if event.get("type") == "final_answer":
                    answer = (event.get("data") or {}).get("answer", "")
                    print(f"\n{answer}")
                    return 0
                if event.get("type") == "error":
                    return 1
    except (OSError, InvalidStatus, ConnectionClosed) as exc:
        print(f"WebSocket error: {exc}", file=sys.stderr)
        return 2

    print("Connection closed before a final_answer or error event.", file=sys.stderr)
    return 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Ask the ShopAssist orchestrator a question.")
    parser.add_argument("query", help="the question to send, e.g. \"Where is my order 1042?\"")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"orchestrator base URL (default {DEFAULT_BASE})")
    args = parser.parse_args()

    if not args.query.strip():
        parser.error("query must not be empty")

    return run(args.query, args.base)


if __name__ == "__main__":
    sys.exit(main())
