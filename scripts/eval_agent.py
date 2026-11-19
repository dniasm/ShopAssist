"""Score the agent's first action against tests/eval/queries.jsonl.

Usage (from the repo root):
    python scripts/eval_agent.py --mode rules                 # rules parser (llm=None)
    python scripts/eval_agent.py --mode llm                   # LLM with the keys in .env
    python scripts/eval_agent.py --mode rules --limit 20 --tags basic,typo

For each line, plan_next_action(query, history, context, llm=...) is called once, with no tool results,
and its FIRST action is compared with expect:
  - tool: exact string match
  - expect.args: must be a subset of the actual args
      strings: case-insensitive equality
      query:   token containment (expected tokens are a subset of the actual tokens; lower-cased,
               simple plural strip on both sides)
      numbers: exact (an integer or a numeric string equal to it)
Prints overall accuracy, accuracy per tag and per expected tool, and the failures. Writes
tests/eval/results/<mode>-<YYYYMMDD-HHMM>.json.

LLM mode makes one call at a time. Before each call it checks the key pool; if no key is usable it stops
and reports how far it got. Keys are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

EVAL_FILE = REPO / "tests" / "eval" / "queries.jsonl"
RESULTS_DIR = REPO / "tests" / "eval" / "results"
SEED = REPO / "data" / "seed" / "v2"
PLANNER_MODULE = "orchestrator.agent.planner"


# ----------------------------------------------------------------------------- pure matching
def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def strip_plural(token: str) -> str:
    """Simple plural strip: 'hoodies' -> 'hoodie', 'jeans' -> 'jean'. Short words are left alone ('bus')."""
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def query_matches(expected: str, actual: str | None) -> bool:
    """Every expected token must appear among the actual tokens (lower-cased, plural-stripped)."""
    want = {strip_plural(t) for t in tokens(expected)}
    have = {strip_plural(t) for t in tokens(actual or "")}
    return want <= have


def arg_matches(key: str, expected, actual) -> tuple[bool, str]:
    """One expected arg against the actual value. Returns (ok, reason-if-not-ok)."""
    if actual is None:
        return False, f"{key} missing"
    if key == "query":
        if query_matches(expected, actual if isinstance(actual, str) else str(actual)):
            return True, ""
        return False, f"query {actual!r} lacks the words of {expected!r}"
    if isinstance(expected, bool):
        return (actual is expected), f"{key}: expected {expected!r}, got {actual!r}"
    if isinstance(expected, int):
        if isinstance(actual, bool):
            return False, f"{key}: expected {expected}, got {actual!r}"
        if isinstance(actual, int):
            ok = actual == expected
        elif isinstance(actual, str) and actual.strip().lstrip("-").isdigit():
            ok = int(actual) == expected
        else:
            ok = False
        return ok, f"{key}: expected {expected}, got {actual!r}"
    if isinstance(expected, str):
        ok = str(actual).strip().lower() == expected.strip().lower()
        return ok, f"{key}: expected {expected!r}, got {actual!r}"
    return actual == expected, f"{key}: expected {expected!r}, got {actual!r}"


def action_matches(expected: dict, actual: dict | None) -> tuple[bool, list[str]]:
    """Tool exact; expected args a subset of the actual args. Returns (passed, reasons)."""
    actual = actual or {}
    reasons: list[str] = []
    if actual.get("tool") != expected["tool"]:
        reasons.append(f"tool {actual.get('tool')!r} != {expected['tool']!r}")
    actual_args = actual.get("args") or {}
    for key, value in expected.get("args", {}).items():
        ok, why = arg_matches(key, value, actual_args.get(key))
        if not ok:
            reasons.append(why)
    return not reasons, reasons


def summarise(results: list[dict]) -> dict:
    """Overall, per tag and per expected tool accuracy from per-line results."""
    def acc(rows: list[dict]) -> dict:
        n = len(rows)
        p = sum(1 for r in rows if r["passed"])
        return {"passed": p, "total": n, "accuracy": round(p / n, 4) if n else None}

    per_tag: dict[str, list[dict]] = defaultdict(list)
    per_tool: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        for t in r["tags"]:
            per_tag[t].append(r)
        per_tool[r["expected"]["tool"]].append(r)
    return {
        "overall": acc(results),
        "per_tag": {t: acc(rows) for t, rows in sorted(per_tag.items())},
        "per_tool": {t: acc(rows) for t, rows in sorted(per_tool.items())},
    }


def select_lines(lines: list[dict], tags: set[str] | None, limit: int | None) -> list[dict]:
    chosen = [l for l in lines if not tags or tags & set(l["tags"])]
    return chosen[:limit] if limit else chosen


def pool_usable(status: list[dict]) -> bool:
    """True if at least one key is not cooling or disabled. Input is KeyPool.status()."""
    return any(s.get("state") == "ok" for s in status)


def enrich_context(context: dict, products: dict[str, dict]) -> dict:
    """Fill the product summary the planner expects (no gRPC; the seed is read once by the caller)."""
    ctx = dict(context)
    pid = ctx.get("product_id")
    if pid and pid in products:
        p = products[pid]
        ctx["product"] = {"product_id": p["product_id"], "name": p["name"], "category": p["category"],
                          "price_cents": p["price_cents"], "colors": p["colors"], "sizes": p["sizes"]}
    return ctx


# ----------------------------------------------------------------------------- I/O side
def load_lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_products() -> dict[str, dict]:
    catalog = json.loads((SEED / "catalog.json").read_text(encoding="utf-8"))
    return {p["product_id"]: p for p in catalog["products"]}


def load_planner(module_name: str = PLANNER_MODULE):
    """Import the planner, or stop with a clear message if it has not been built yet."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name and module_name.startswith(exc.name):
            sys.exit(f"eval_agent: {module_name.replace('.', '/')}.py is missing. "
                     "The agent planner is not built yet, so there is nothing to score.")
        raise
    except ImportError as exc:
        sys.exit(f"eval_agent: {module_name} failed to import: {exc}")


def build_llm():
    """The same client the orchestrator builds from .env (orchestrator/app.py create_app). Keys stay hidden."""
    from dotenv import load_dotenv
    from orchestrator.llm import LLMClient
    from orchestrator.settings import Settings

    load_dotenv(REPO / ".env", override=False)
    settings = Settings.from_env()
    if not settings.llm_api_keys:
        sys.exit("eval_agent: no LLM keys in .env (LLM_API_KEYS is empty). Use --mode rules, or add keys.")
    print(f"llm: {len(settings.llm_api_keys)} key(s) configured (values not shown), model {settings.llm_model}")
    return LLMClient(settings)


async def run_eval(planner, lines: list[dict], llm, products: dict[str, dict]) -> tuple[list[dict], str | None]:
    results: list[dict] = []
    stopped: str | None = None
    for line in lines:
        if llm is not None:
            status = await llm.pool.status()
            if not pool_usable(status):
                stopped = (f"every LLM key is cooling or disabled before {line['id']}; "
                           f"{len(results)} of {len(lines)} lines were scored")
                break
        context = enrich_context(line["context"], products)
        row = {"id": line["id"], "query": line["query"], "tags": line["tags"], "expected": line["expect"]}
        try:
            action = await planner.plan_next_action(line["query"], line["history"], context, llm=llm)
        except Exception as exc:  # the planner or the LLM failed for this line; record it and carry on
            row.update({"actual": None, "passed": False, "reasons": [f"error: {type(exc).__name__}: {exc}"]})
        else:
            actual = {"tool": action.get("tool"), "args": action.get("args") or {}, "source": action.get("source")}
            passed, reasons = action_matches(line["expect"], actual)
            row.update({"actual": actual, "passed": passed, "reasons": reasons})
        results.append(row)
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['id']}", flush=True)
    return results, stopped


def print_report(summary: dict, failures: list[dict]) -> None:
    o = summary["overall"]
    print(f"\nOVERALL: {o['passed']}/{o['total']} = {o['accuracy']:.1%}" if o["total"] else "\nOVERALL: no lines scored")
    print("per tag:")
    for t, a in summary["per_tag"].items():
        print(f"  {t:<18} {a['passed']:>3}/{a['total']:<3} {a['accuracy']:.1%}")
    print("per expected tool:")
    for t, a in summary["per_tool"].items():
        print(f"  {t:<22} {a['passed']:>3}/{a['total']:<3} {a['accuracy']:.1%}")
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for f in failures:
            print(f"  {f['id']}: {f['query']!r}")
            print(f"    expected {json.dumps(f['expected'], ensure_ascii=False)}")
            print(f"    actual   {json.dumps(f['actual'], ensure_ascii=False)}")
            for r in f["reasons"]:
                print(f"    - {r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the agent's first action on the eval set.")
    parser.add_argument("--mode", choices=["rules", "llm"], required=True)
    parser.add_argument("--limit", type=int, default=None, help="score only the first N selected lines")
    parser.add_argument("--tags", default="", help="comma-separated; keep lines that have any of these tags")
    args = parser.parse_args()
    tags = {t.strip() for t in args.tags.split(",") if t.strip()} or None

    planner = load_planner()
    lines = select_lines(load_lines(EVAL_FILE), tags, args.limit)
    if not lines:
        sys.exit("eval_agent: no lines selected")
    llm = build_llm() if args.mode == "llm" else None
    products = load_products()

    print(f"mode={args.mode} lines={len(lines)}")
    results, stopped = asyncio.run(run_eval(planner, lines, llm, products))
    summary = summarise(results)
    failures = [r for r in results if not r["passed"]]
    print_report(summary, failures)
    if stopped:
        print(f"\nSTOPPED EARLY: {stopped}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.mode}-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    out.write_text(json.dumps({
        "mode": args.mode,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tags": sorted(tags) if tags else None,
        "limit": args.limit,
        "lines_selected": len(lines),
        "lines_scored": len(results),
        "stopped_early": stopped,
        "summary": summary,
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out}")
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
