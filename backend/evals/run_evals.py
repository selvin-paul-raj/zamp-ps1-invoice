"""Evaluate the decision pipeline against the labelled seed set.

Usage: python -m evals.run_evals [--json out.json]
Runs every seed invoice (in received order) through a fresh temporary database and prints
decision accuracy, primary-reason accuracy, a confusion matrix, false approvals and coverage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PIPELINE_STEP_DELAY_MS", "0")

from app.config import Settings  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.service import Service  # noqa: E402

SEED = Path(__file__).resolve().parent.parent / "seed"
LABELS = ["APPROVE", "HOLD_FOR_REVIEW", "REJECT"]


def evaluate() -> dict:
    tmp = tempfile.mkdtemp()
    svc = Service(make_engine(f"sqlite:///{tmp}/eval.db"), Settings())
    svc.load_master(SEED)
    items = sorted(json.loads((SEED / "invoices.json").read_text()), key=lambda x: x["received_at"])
    rows = []
    for it in items:
        res = svc.ingest(it, idempotency_key=f"seed:{it['id']}", expected=it["expected"])
        svc.drain(delay_ms=0)
        run = svc.get_run(res["run_id"])
        codes = [r["code"] for r in run["reasons"]]
        exp = it["expected"]
        rows.append({
            "id": it["id"], "expected": exp["decision"], "got": run["decision"], "expected_reason": exp["reason"],
            "primary_reason": run["primary_reason"], "all_reasons": codes, "confidence": run["confidence"],
            "decision_ok": run["decision"] == exp["decision"],
            "reason_ok": (exp["reason"] is None and not codes) or exp["reason"] == run["primary_reason"],
            "reason_found": exp["reason"] is None or exp["reason"] in codes,
        })
    # Idempotency: re-ingesting the same invoice returns the same invoice + run, no new work.
    again = svc.ingest(items[0], idempotency_key=f"seed:{items[0]['id']}")
    idem_ok = again["created"] is False and svc.drain(delay_ms=0) == 0

    n = len(rows)
    cm = Counter((r["expected"], r["got"]) for r in rows)
    auto = [r for r in rows if r["got"] == "APPROVE"]
    return {
        "n": n,
        "decision_accuracy": sum(r["decision_ok"] for r in rows) / n,
        "primary_reason_accuracy": sum(r["reason_ok"] for r in rows) / n,
        "expected_reason_found": sum(r["reason_found"] for r in rows) / n,
        "false_approvals": sum(1 for r in auto if r["expected"] != "APPROVE"),
        "false_rejects": sum(1 for r in rows if r["got"] == "REJECT" and r["expected"] != "REJECT"),
        "auto_approve_coverage": len(auto) / n,
        "confusion": {e: {g: cm.get((e, g), 0) for g in LABELS} for e in LABELS},
        "idempotent_reingest": idem_ok,
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args()
    rep = evaluate()
    print(f"{'id':7} {'expected':16} {'got':16} {'expected_reason':22} {'primary_reason':22} conf")
    for r in rep["rows"]:
        mark = "OK " if r["decision_ok"] and r["reason_ok"] else "XX "
        print(f"{mark}{r['id']:5} {r['expected']:16} {r['got']:16} {str(r['expected_reason']):22} {str(r['primary_reason']):22} {r['confidence']:.2f}")
    print()
    print(f"Invoices evaluated      : {rep['n']}")
    print(f"Decision accuracy       : {rep['decision_accuracy']:.1%}")
    print(f"Primary-reason accuracy : {rep['primary_reason_accuracy']:.1%}")
    print(f"Expected reason raised  : {rep['expected_reason_found']:.1%}")
    print(f"False approvals         : {rep['false_approvals']}   (the metric that matters: paying a bad invoice)")
    print(f"False rejects           : {rep['false_rejects']}")
    print(f"Auto-approve coverage   : {rep['auto_approve_coverage']:.1%}")
    print(f"Idempotent re-ingest    : {rep['idempotent_reingest']}")
    print("Confusion (rows=expected, cols=got):")
    print(f"{'':18}" + "".join(f"{l:>17}" for l in LABELS))
    for e in LABELS:
        print(f"{e:18}" + "".join(f"{rep['confusion'][e][g]:>17}" for g in LABELS))
    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=2))
    return 0 if rep["false_approvals"] == 0 and rep["decision_accuracy"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
