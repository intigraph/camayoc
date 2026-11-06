#!/usr/bin/env python3
"""Benchmark the question-mapping arms against a labelled set — aegis-4hhqoe.9.

No arm is trusted before this runs (docs/design/jev-typed-decisions.md §8.2).
Input: a JSONL of {state, expected, kind, why} where expected is a competency
question id or "none-of-these". For each arm it reports:

  agreement      top match == expected (or Empty when expected is none)
  conf | agree   mean Jev confidence on the items it got right
  conf | miss    mean Jev confidence on the items it got wrong  (the gap between
                 these two is what makes a confidence floor usable at all)
  abstain rate   how often the none option won
  tokens         input tokens summed (jev only)

Then every miss, with what the arm picked and at what probability, because a
benchmark that hides its misses is a press release.

    export TYPESAFE_API_KEY=$(cd ~/workspace/goldblum && just infisical get TYPESAFE_API_KEY)
    python3 scripts/jev_bench.py bench/competency-coverage.jsonl            # both arms
    python3 scripts/jev_bench.py bench/competency-coverage.jsonl --arm lexical
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import competency  # noqa: E402

NONE = "none-of-these"


def run_arm(arm: str, items: list[dict], suite) -> dict:
    if arm in ("jev", "jev-hier"):
        import jev
        scorer = competency.JevScorer(jev.JevClient(), hierarchical=(arm == "jev-hier"))
    else:
        scorer = competency.Scorer(); scorer._embedder = None
    rows = []
    for it in items:
        v = competency.assess(it["state"], suite, scorer=scorer)
        top = v["matches"][0]["id"] if v["matches"] else None
        picked = NONE if v["coverage"] == "Empty" else top
        exp = it["expected"]
        hit = (picked == exp) if exp == NONE else (v["coverage"] != "Empty" and top == exp)
        j = v.get("jev") or {}
        rows.append({"state": it["state"], "expected": exp, "kind": it.get("kind"), "picked": picked,
                     "p": v["best_score"], "coverage": v["coverage"], "hit": hit,
                     "confidence": j.get("confidence"), "abstained": bool(j.get("abstained")),
                     "tokens": (j.get("usage") or {}).get("input_tokens", 0),
                     "runner_up": [(m["id"], m["score"]) for m in v["matches"][1:3]]})
    n = len(rows); hits = [r for r in rows if r["hit"]]; miss = [r for r in rows if not r["hit"]]
    conf = lambda rs: (sum(r["confidence"] for r in rs if r["confidence"] is not None) / max(1, len([r for r in rs if r["confidence"] is not None]))) if rs else None
    return {"arm": arm, "n": n, "agreement": len(hits) / n if n else 0,
            "conf_agree": conf(hits) if arm.startswith("jev") else None,
            "conf_miss": conf(miss) if arm.startswith("jev") else None,
            "abstain_rate": sum(r["abstained"] for r in rows) / n if n else 0,
            "tokens": sum(r["tokens"] for r in rows), "rows": rows, "misses": miss}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("labelled", type=Path)
    ap.add_argument("--suite", default=str(Path(__file__).resolve().parents[1] / "competency"))
    ap.add_argument("--arm", choices=("lexical", "jev", "jev-hier", "both", "all"), default="both")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--slot", choices=("competency", "ordinal", "scheduled-work"), default="competency")
    ap.add_argument("--responses", type=Path, help="Replay ordinal responses without a model")
    ap.add_argument("--live", action="store_true", help="Explicitly enable paid ordinal calls")
    ap.add_argument("--max-calls", type=int, help="Hard call ceiling; no automatic retries")
    ap.add_argument("--output", type=Path, help="New ordinal response JSONL; never overwritten")
    ap.add_argument("--max-cost-usd", help="Scheduled-work run's separately authorized dollar ceiling")
    ap.add_argument("--approval-ref", help="Recorded scheduled-work benchmark spend ruling")
    a = ap.parse_args(argv)
    items = [json.loads(l) for l in a.labelled.read_text().splitlines() if l.strip()]
    if a.slot == "scheduled-work":
        import seed_schedule
        import jev_mcp
        try:
            result = seed_schedule.run(items, a.responses, a.live, a.max_calls,
                                       a.output, a.max_cost_usd, a.approval_ref,
                                       lambda out: jev_mcp.record_usage("scheduled-work-benchmark", out))
        except (ValueError, OSError) as exc:
            print(f"scheduled-work: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get("unknown", 0) else 0
    if a.max_cost_usd is not None or a.approval_ref is not None:
        ap.error("spend options require --slot scheduled-work")
    if a.slot == "ordinal":
        import jev_ordinal
        try:
            result = jev_ordinal.run(items, a.responses, a.live, a.max_calls, a.output)
        except (ValueError, OSError) as exc:
            print(f"ordinal: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get("unavailable", 0) else 0
    if a.responses or a.live or a.max_calls is not None or a.output:
        ap.error("ordinal options require --slot ordinal")
    suite = competency.parse_suite(Path(a.suite))
    arms = {"both": ["lexical", "jev"], "all": ["lexical", "jev", "jev-hier"]}.get(a.arm, [a.arm])
    results = []
    for arm in arms:
        try:
            results.append(run_arm(arm, items, suite))
        except Exception as exc:  # noqa: BLE001 - report, keep the other arm
            print(f"{arm}: {exc}", file=sys.stderr)
    if a.json:
        print(json.dumps(results, indent=1)); return 0
    print(f"labelled set: {a.labelled} ({len(items)} items) · suite {competency.watermark(suite)} ({len(suite)} q)\n")
    print(f"{'arm':<8} {'agree':>7} {'conf|agree':>11} {'conf|miss':>10} {'abstain':>8} {'tokens':>7}")
    for r in results:
        f = lambda x: "-" if x is None else f"{x:.2f}"
        print(f"{r['arm']:<8} {r['agreement']:>7.0%} {f(r['conf_agree']):>11} {f(r['conf_miss']):>10} {r['abstain_rate']:>8.0%} {r['tokens']:>7}")
    for r in results:
        if r["misses"]:
            print(f"\n{r['arm']} misses ({len(r['misses'])}):")
            for m in r["misses"]:
                c = f" conf {m['confidence']:.2f}" if m["confidence"] is not None else ""
                print(f"  [{m['kind']}] {m['state'][:60]!r}\n      expected {m['expected']}  picked {m['picked']} p={m['p']}{c}  next {m['runner_up']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
