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
    # Real PDFs (text layer + scanned/OCR), through exactly the upload path.
    from app.ingest import parse_upload

    pdf_labels = json.loads((SEED / "pdfs" / "labels.json").read_text())
    field_hits = field_total = 0
    for name, exp in pdf_labels.items():
        payload = parse_upload(name, (SEED / "pdfs" / name).read_bytes())[0]
        payload["received_at"] = "2026-10-12T09:00:00+05:30"
        for k, v in exp.get("fields", {}).items():
            got = len(payload.get("lines") or []) if k == "line_count" else payload.get(k)
            field_total += 1
            field_hits += str(got) == str(v)
        res = svc.ingest(payload, idempotency_key=f"pdf:{name}", expected={k: v for k, v in exp.items() if k != "fields"})
        svc.drain(delay_ms=0)
        run = svc.get_run(res["run_id"])
        codes = [r["code"] for r in run["reasons"]]
        rows.append({
            "id": name.split("_")[0], "expected": exp["decision"], "got": run["decision"], "expected_reason": exp["reason"],
            "primary_reason": run["primary_reason"], "all_reasons": codes, "confidence": run["confidence"],
            "decision_ok": run["decision"] == exp["decision"],
            "reason_ok": (exp["reason"] is None and not codes) or exp["reason"] == run["primary_reason"],
            "reason_found": exp["reason"] is None or exp["reason"] in codes, "source": f"pdf/{payload.get('extraction', {}).get('method')}",
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
        "pdf_field_accuracy": field_hits / field_total if field_total else None,
        "pdf_fields": [field_hits, field_total],
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--adv-repeats", type=int, default=20, help="random variants per (base invoice, mutation); 0 skips")
    ap.add_argument("--jev", choices=["off", "replay", "live"], help="Jev mode for this run (default: env JEV_MODE, else replay)")
    args = ap.parse_args()
    if args.jev:
        os.environ["JEV_MODE"] = args.jev
        if args.jev == "live":
            os.environ["JEV_RECORD"] = "1"  # live answers are recorded so replay reproduces them exactly
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
    print(f"PDF field extraction    : {rep['pdf_fields'][0]}/{rep['pdf_fields'][1]} fields correct (4 PDFs, 3 layouts, 1 scanned)")

    # Held-out adversarial variants of invoices that should be approved.
    from evals.adversarial import run as adversarial

    items = {i["id"]: i for i in json.loads((SEED / "invoices.json").read_text())}
    bases = [items[i] for i in ("INV-01", "INV-02", "INV-04", "INV-19") if i in items]
    bases = [b for b in bases if b["expected"]["decision"] == "APPROVE"]
    if args.adv_repeats <= 0:
        if args.json:
            Path(args.json).write_text(json.dumps(rep, indent=2))
        return 0 if rep["false_approvals"] == 0 and rep["decision_accuracy"] == 1.0 else 1
    adv = adversarial(SEED, bases, repeats=args.adv_repeats)
    rep["adversarial"] = adv
    print()
    print(f"Adversarial held-out set: {adv['n']} broken variants of approvable invoices")
    for m, v in adv["by_mutation"].items():
        print(f"  {m:30} n={v['n']:3}  approved={v['approved']}  caught as {', '.join(f'{k}:{c}' for k, c in v['reasons'].items())}")
    print(f"  False approvals: {adv['false_approvals']} of {adv['n']}  ->  95% upper bound on false-approval rate ~ {adv['upper_bound_95']:.1%} (rule of three)")
    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=2))
    ok = rep["false_approvals"] == 0 and rep["decision_accuracy"] == 1.0 and adv["false_approvals"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
