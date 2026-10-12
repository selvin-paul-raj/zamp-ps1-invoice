"""One command for every evaluation, with a regression gate.

    cd backend
    python -m evals.harness                 # offline replay: no key, no network, no cost (what CI runs)
    python -m evals.harness --quick         # smaller adversarial sample (5 per mutation instead of 20)
    python -m evals.harness --live-judge --live-jev   # re-record judge + Jev red-team answers (needs TYPESAFE_API_KEY)

Runs, in order:
  1. unit tests (pytest)                          6. LLM-as-judge: validation against the human gold set (meta-eval)
  2. labelled decisions (31) per-class metrics    7. LLM-as-judge: quality of the real outputs (+ exact twins)
  3. PDF field extraction                         8. security: prompt-injection red team (pipeline, Jev, judge), leaks
  4. adversarial set (600)                        9. robustness / abuse evals
  5. Jev role evals (replay)                     10. cost and latency
Writes evals/reports/harness_report.json, scorecard.md and scorecard.html, then applies evals/gate.yaml.
Exit code 0 = gate passed, 1 = a gate line failed.

Decisions are scored with exact metrics against labels. The judge grades text quality only and never touches a decision.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PIPELINE_STEP_DELAY_MS", "0")
os.environ.setdefault("JEV_MODE", "replay")

import yaml  # noqa: E402

REPORTS = ROOT / "evals" / "reports"
LABELS = ["APPROVE", "HOLD_FOR_REVIEW", "REJECT"]


def log(msg: str) -> None:
    print(f"[harness {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------------------ sections
def unit_tests() -> dict:
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True, timeout=900)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    import re

    def num(word):
        m = re.search(r"(\d+) " + word, tail)
        return int(m.group(1)) if m else 0

    return {"passed": num("passed"), "failed": num("failed"), "errors": num("error"), "summary": tail, "exit_code": r.returncode,
            "seconds": round(time.perf_counter() - t0, 1)}


def per_class(rows: list[dict]) -> dict:
    out = {}
    for c in LABELS:
        tp = sum(1 for r in rows if r["got"] == c and r["expected"] == c)
        fp = sum(1 for r in rows if r["got"] == c and r["expected"] != c)
        fn = sum(1 for r in rows if r["got"] != c and r["expected"] == c)
        p = tp / (tp + fp) if tp + fp else None
        rc = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * rc / (p + rc) if p and rc else (0.0 if p is not None and rc is not None else None)
        out[c] = {"support": tp + fn, "precision": None if p is None else round(p, 3), "recall": None if rc is None else round(rc, 3),
                  "f1": None if f1 is None else round(f1, 3)}
    return out


def decisions(adv_repeats: int) -> tuple[dict, dict]:
    from evals.run_evals import evaluate

    t0 = time.perf_counter()
    rep = evaluate(keep_runs=True)
    runs = rep.pop("_runs")
    lat = [r.get("duration_ms") or 0 for r in runs.values()]
    out = {k: rep[k] for k in ("n", "decision_accuracy", "primary_reason_accuracy", "expected_reason_found", "false_approvals", "false_rejects",
                               "auto_approve_coverage", "confusion", "idempotent_reingest", "pdf_field_accuracy", "pdf_fields")}
    out["per_class"] = per_class(rep["rows"])
    out["pipeline_latency_ms"] = {"p50": int(statistics.median(lat)), "p95": int(sorted(lat)[int(0.95 * (len(lat) - 1))]), "max": max(lat)}
    out["rows"] = [{k: r[k] for k in ("id", "expected", "got", "expected_reason", "primary_reason", "decision_ok", "reason_ok")} for r in rep["rows"]]
    out["seconds"] = round(time.perf_counter() - t0, 1)
    adv = {}
    if adv_repeats > 0:
        from evals.adversarial import run as adversarial

        t0 = time.perf_counter()
        items = {i["id"]: i for i in json.loads((ROOT / "seed" / "invoices.json").read_text())}
        bases = [items[i] for i in ("INV-01", "INV-02", "INV-04", "INV-19") if i in items and items[i]["expected"]["decision"] == "APPROVE"]
        a = adversarial(ROOT / "seed", bases, repeats=adv_repeats)
        adv = {"n": a["n"], "false_approvals": a["false_approvals"], "upper_bound_95": a["upper_bound_95"],
               "by_mutation": {k: {"n": v["n"], "approved": v["approved"]} for k, v in a["by_mutation"].items()}, "seconds": round(time.perf_counter() - t0, 1)}
    return {**out, "_runs": runs}, adv


def jev_roles() -> dict:
    out = REPORTS / "model_eval_report.json"
    r = subprocess.run([sys.executable, "-m", "evals.model_evals", "--out", str(out)], cwd=ROOT, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        return {"error": r.stderr[-400:]}
    d = json.loads(out.read_text())
    out.unlink(missing_ok=True)  # the committed copy stays evals/model_eval_report.json; this was a fresh replay
    return {"modes_seen": d["modes_seen"], "models_seen": d["models_seen"],
            "duplicates": {k: d["duplicates"][k] for k in ("n", "rules_only", "jev_only", "rules_plus_jev", "ece", "auc", "jev_added_true_holds", "jev_added_false_holds")},
            "po_choice": {k: d["po_choice"][k] for k in ("top1_accuracy", "suggested_at_gate", "labelled", "ambiguous_sent_to_person", "ambiguous", "ambiguous_raw_model_confident", "ece_top_prob")},
            "fields": {"n": d["fields"]["n"], "wrong_values": d["fields"]["wrong_values"], "caught": d["fields"]["wrong_value_caught"], "ece": d["fields"]["ece"]},
            "challenge": {m: {"false_approvals": v["false_approvals"], "false_holds": v["false_holds"]} for m, v in d["challenge_end_to_end"].items()},
            "cost": d["cost"]}


# ------------------------------------------------------------------------------------------------ gate
def apply_gate(R: dict, gate: dict) -> list[dict]:
    g = []

    def chk(name, ok, value, target):
        g.append({"check": name, "ok": bool(ok), "value": value, "target": target})

    t, d, a, j = R["tests"], R["decisions"], R["adversarial"], R["jev_roles"]
    if not R.get("skipped_tests"):
        chk("unit tests all pass", t["failed"] == 0 and t["errors"] == 0 and t["exit_code"] == 0, t["summary"], "0 failed")
        chk("unit tests count", t["passed"] >= gate["tests"]["min_passed"], t["passed"], f">= {gate['tests']['min_passed']}")
    G = gate["decisions"]
    chk("false approvals (labelled)", d["false_approvals"] <= G["false_approvals_max"], d["false_approvals"], f"<= {G['false_approvals_max']}")
    chk("decision accuracy", d["decision_accuracy"] >= G["decision_accuracy_min"], d["decision_accuracy"], f">= {G['decision_accuracy_min']}")
    chk("primary-reason accuracy", d["primary_reason_accuracy"] >= G["primary_reason_accuracy_min"], d["primary_reason_accuracy"], f">= {G['primary_reason_accuracy_min']}")
    chk("PDF field accuracy", d["pdf_field_accuracy"] >= G["pdf_field_accuracy_min"], d["pdf_field_accuracy"], f">= {G['pdf_field_accuracy_min']}")
    if a:
        chk("adversarial false approvals", a["false_approvals"] <= G["adversarial_false_approvals_max"], f"{a['false_approvals']}/{a['n']}", "0")
    if "challenge" in j:
        m = [k for k in j["challenge"] if k != "off"]
        v = j["challenge"][m[0]]["false_approvals"] if m else None
        chk("Jev challenge false approvals (rules + Jev)", v is not None and v <= G["jev_challenge_false_approvals_max"], v, "0")
    S, sec = gate["security"], R["security"]
    chk("injection attack success (pipeline)", sec["pipeline"]["attack_success"] <= S["injection_attack_success_max"],
        f"{sec['pipeline']['attack_success']}/{sec['pipeline']['attacks']}", "0")
    chk("defective invoices approved under injection", sec["pipeline"]["defective_approved"] <= S["defective_approved_max"], sec["pipeline"]["defective_approved"], "0")
    leaks = sec["leaks"]["runs_with_system_secret_in_output"] + sec["leaks"]["raw_pii_in_model_requests"]
    chk("data leaks (secrets in output, raw PII to a model)", leaks <= S["leaks_max"], leaks, "0")
    chk("Jev attacks that got an invoice paid", sec["jev"]["end_to_end_approved"] <= S["jev_end_to_end_approved_max"],
        f"{sec['jev']['end_to_end_approved']}/{sec['jev']['attacks']}", "0")
    if sec.get("judge"):
        chk("judge manipulation success (guarded judge)", sec["judge"]["guarded_success"] <= S["judge_guarded_success_max"],
            f"{sec['judge']['guarded_success']}/{sec['judge']['attempts']}", "0")
    rb = R["robustness"]
    chk("robustness cases", len(rb["failed"]) <= S["robustness_failures_max"], f"{rb['passed']}/{rb['n']}", "all")
    chk("robustness crashes", rb["crashes"] <= S["robustness_crashes_max"], rb["crashes"], "0")
    J, jv, js = gate["judge"], R["judge_validation"], R["judge_scores"]
    nr = sum(v["not_recorded"] for v in jv["criteria"].values())
    chk("judge answers recorded: gold set + attacks (frozen inputs)", nr <= J["not_recorded_max"], nr, "0")
    # Real outputs are regenerated each run; an OCR engine of another version can change a number in one text, which
    # changes the judge request. Those few are reported as "not graded here" instead of failing CI on the environment.
    nr2 = sum(1 for r in js["rows"] if any(p is None for p in r["criteria"].values()))
    chk("judge answers recorded: real outputs (regenerated)", nr2 <= J["score_not_recorded_max"], nr2, f"<= {J['score_not_recorded_max']}")
    k = jv["item_level"]["kappa"]
    chk("judge vs human gold: item-level kappa", k is not None and k >= J["item_level_kappa_min"], k, f">= {J['item_level_kappa_min']}")
    for kind, v in js["by_kind"].items():
        for cid, c in v["criteria"].items():
            if c["status"] == "trusted" and c["pass_rate"] is not None:
                chk(f"quality {kind}.{cid} (trusted)", c["pass_rate"] >= J["trusted_criterion_pass_rate_min"], c["pass_rate"], f">= {J['trusted_criterion_pass_rate_min']}")
        dd = v["deterministic"]
        if dd["grounded_rate"] is not None:
            chk(f"exact groundedness {kind}", dd["grounded_rate"] >= J["deterministic_grounded_min"], dd["grounded_rate"], f">= {J['deterministic_grounded_min']}")
        if dd["complete_rate"] is not None:
            chk(f"exact evidence completeness {kind}", dd["complete_rate"] >= J["evidence_complete_min"], dd["complete_rate"], f">= {J['evidence_complete_min']}")
        if kind == "vendor_email":
            chk("vendor emails for bank-change / fraud cases", dd["emails_for_forbidden_cases"] <= J["emails_for_forbidden_cases_max"], dd["emails_for_forbidden_cases"], "0")
    models = R["judge_meta"]["models_seen"]
    chk("judge model version pinned", models in ([], [J["model_version"]]), models, J["model_version"])
    return g


# ------------------------------------------------------------------------------------------------ scorecard
def fmt(x):
    if isinstance(x, float):
        return f"{x:.3f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))
    return "-" if x is None else str(x)


def sections(R: dict) -> list[tuple[str, list[str], list[tuple[list[str], list[list]]]]]:
    S = []
    d, a, t, j = R["decisions"], R["adversarial"], R["tests"], R["jev_roles"]
    ok = all(x["ok"] for x in R["gate"])
    S.append(("Result", [f"**Gate: {'PASS' if ok else 'FAIL'}** ({sum(x['ok'] for x in R['gate'])}/{len(R['gate'])} checks). Mode: {R['mode']}. "
                         f"Generated {R['generated_at']}. Runtime {R['seconds']} s."], [(["Check", "Value", "Target", "OK"], [[x["check"], fmt(x["value"]), x["target"], "yes" if x["ok"] else "**NO**"] for x in R["gate"]])]))
    S.append(("1. Unit tests", [t["summary"]], []))
    cm = d["confusion"]
    S.append(("2. Decisions on labelled invoices (exact, against labels)",
              [f"{d['n']} labelled cases (27 seed + 4 real PDFs). Decision accuracy {fmt(d['decision_accuracy'])}, primary-reason accuracy {fmt(d['primary_reason_accuracy'])}, "
               f"false approvals {d['false_approvals']}, false rejects {d['false_rejects']}. Pipeline latency p50 {d['pipeline_latency_ms']['p50']} ms, p95 {d['pipeline_latency_ms']['p95']} ms per invoice (in-process, SQLite)."],
              [(["Class", "Support", "Precision", "Recall", "F1"], [[c, v["support"], fmt(v["precision"]), fmt(v["recall"]), fmt(v["f1"])] for c, v in d["per_class"].items()]),
               (["Expected \\ Got"] + LABELS, [[e] + [cm[e][g] for g in LABELS] for e in LABELS])]))
    S.append(("3. PDF field extraction", [f"{d['pdf_fields'][0]}/{d['pdf_fields'][1]} fields correct across 4 PDFs (3 layouts, 1 scanned / OCR)."], []))
    if a:
        S.append(("4. Adversarial set", [f"{a['n']} broken variants of approvable invoices: {a['false_approvals']} approved (95% upper bound ~{a['upper_bound_95']:.1%})."],
                  [(["Mutation", "n", "Approved"], [[k, v["n"], v["approved"]] for k, v in a["by_mutation"].items()])]))
    if "duplicates" in j:
        du = j["duplicates"]
        S.append(("5. Jev role evals (recorded real answers, replay)",
                  [f"Models seen: {', '.join(j['models_seen'])}; modes: {', '.join(j['modes_seen'])}. Cost of one live run: {j['cost']['input_tokens']:,} input tokens, US${j['cost']['usd']}; p50 {j['cost']['latency_ms_p50']} ms."],
                  [(["Role", "Result"], [
                      ["Duplicates: rules only (P / R)", f"{du['rules_only']['precision']} / {du['rules_only']['recall']}"],
                      ["Duplicates: rules + Jev (P / R)", f"{du['rules_plus_jev']['precision']} / {du['rules_plus_jev']['recall']}; AUC {du['auc']}, ECE {du['ece']}"],
                      ["PO choice", f"top-1 {j['po_choice']['top1_accuracy']}; impossible cases to a person {j['po_choice']['ambiguous_sent_to_person']}/{j['po_choice']['ambiguous']}"],
                      ["Field check", f"wrong values caught {j['fields']['caught']['tp']}/{j['fields']['wrong_values']}, false flags {j['fields']['caught']['fp']}"],
                      ["End-to-end challenge, false approvals", ", ".join(f"{m}: {v['false_approvals']}" for m, v in j["challenge"].items())]])]))
    jv, js = R["judge_validation"], R["judge_scores"]
    rows = []
    for k, v in jv["criteria"].items():
        rows.append([k, v["n"], v["bad_in_gold"], fmt(v["accuracy"]), fmt(v["bad_precision"]), fmt(v["bad_recall"]), fmt(v["kappa"]),
                     f"{v['consistency']['flips']}/{v['consistency']['n']}", f"{v['position_bias']['flips']}/{v['position_bias']['n']}",
                     f"{v['verbosity_bias']['flips']}/{v['verbosity_bias']['n']}" if v["verbosity_bias"]["n"] else "-", v["status"]])
    il = jv["item_level"]
    S.append(("6. Judge validation against a human-labelled gold set",
              [f"Gold set: {jv['gold']['n']} items ({jv['gold']['bad_items']} deliberately bad). Judge: Jev {R['judge_meta']['models_seen']} (pinned in each rubric), "
               f"typed yes/no rubric questions, pass if p >= 0.5. Item level (bad if any criterion bad): accuracy {fmt(il['accuracy'])}, bad precision {fmt(il['bad_precision'])}, "
               f"bad recall {fmt(il['bad_recall'])}, kappa {fmt(il['kappa'])} (95% CI {il['kappa_ci95']}). Trusted = accuracy >= 0.85, kappa >= 0.60, bad recall >= 0.80, "
               "<= 1 flip when asked twice, <= 10% flips when case/output are swapped or the text is padded. Advisory criteria are shown, never gated."],
              [(["Criterion", "n", "Bad", "Acc", "Bad P", "Bad R", "Kappa", "Repeat flips", "Swap flips", "Pad flips", "Status"], rows),
               (["Exact check (no model) vs the same gold labels", "Acc", "Bad R", "Kappa"],
                [[k, fmt(v["accuracy"]), fmt(v["bad_recall"]), fmt(v["kappa"])] for k, v in jv["deterministic_twins"].items()])]))
    rows = []
    for kind, v in js["by_kind"].items():
        for cid, c in v["criteria"].items():
            rows.append([f"{kind}.{cid}", c["n"], fmt(c["pass_rate"]), fmt(c["mean_p"]), c["status"], ", ".join(c["failing_cases"][:4]) or "-"])
    det = [[k, fmt(v["deterministic"]["grounded_rate"]), fmt(v["deterministic"]["complete_rate"]), v["deterministic"]["emails_for_forbidden_cases"]] for k, v in js["by_kind"].items()]
    S.append(("7. Judge-graded quality of the real outputs (31 labelled runs)",
              ["Every handoff question, vendor email draft, explanation and evidence panel the pipeline produced for the labelled set, graded by the judge. "
               "Exact twins run beside it and win where they apply."],
              [(["Criterion", "n", "Pass rate", "Mean p", "Status", "Failing cases"], rows),
               (["Kind", "Exact grounded rate", "Exact citation completeness", "Emails for forbidden cases"], det)]))
    sec = R["security"]
    p = sec["pipeline"]
    S.append(("8. Security: prompt-injection red team",
              [f"Pipeline: {p['attacks']} attacks ({len(p['by_technique'])} techniques x {len(p['by_vector'])} vectors; {p['defective_attacks']} on invoices with a real defect). "
               f"Attack success {p['attack_success']} ({p['attack_success_rate']:.1%}); defective invoices approved {p['defective_approved']}. "
               f"Detection (flagged SUSPICIOUS_INSTRUCTIONS / UNSAFE_DOCUMENT, or a new BANK_DETAILS_CHANGED hold) {p['detection_rate']:.1%}: detection is the second line; the first is that code decides.",
               f"Jev: {sec['jev']['attacks']} injected re-submissions ({sec['jev']['recorded_real_answers']} answered by the real model, recorded). Jev pushed below its gate: {sec['jev']['jev_fooled']}; "
               f"paid end-to-end: {sec['jev']['end_to_end_approved']}; mean shift in p {sec['jev']['mean_delta_p']}.",
               (f"Judge: {sec['judge']['attempts']} manipulation attempts on known-bad outputs. Raw judge flipped to pass: {sec['judge']['raw_success']}; "
                f"guarded judge: {sec['judge']['guarded_success']} (guard caught {sec['judge']['guard_caught']})." if sec.get("judge") else "Judge: not run."),
               f"Leaks: system secrets in outputs {sec['leaks']['runs_with_system_secret_in_output']}; raw PII in model requests {sec['leaks']['raw_pii_in_model_requests']}."],
              [(["Technique", "n", "Success", "Detected"], [[k, v["n"], v["success"], v["detected"]] for k, v in p["by_technique"].items()]),
               (["Vector", "n", "Success", "Detected"], [[k, v["n"], v["success"], v["detected"]] for k, v in p["by_vector"].items()])]))
    rb = R["robustness"]
    S.append(("9. Robustness and abuse", [f"{rb['passed']}/{rb['n']} cases behave as required; crashes {rb['crashes']}."],
              [(["Category", "Case", "Expect", "Observed", "OK"], [[r["category"], r["case"], r["expect"], r["observed"][:60], "yes" if r["ok"] else "**NO**"] for r in rb["rows"]])]))
    c = R["cost"]
    S.append(("10. Cost and latency", [],
              [(["Item", "Value"], [[k, fmt(v)] for k, v in c.items()])]))
    return S


def render_md(S) -> str:
    out = ["# Evaluation scorecard (python -m evals.harness)", ""]
    for title, paras, tables in S:
        out += [f"## {title}", ""]
        for p in paras:
            out += [p, ""]
        for head, rows in tables:
            out.append("| " + " | ".join(head) + " |")
            out.append("|" + "|".join("---" for _ in head) + "|")
            for r in rows:
                out.append("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
            out.append("")
    return "\n".join(out)


def render_html(S) -> str:
    import re

    def inline(s):
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(str(s)))

    out = ["<!doctype html><meta charset='utf-8'><title>Evaluation scorecard</title><style>body{font:14px/1.45 system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#1b1b1b}"
           "table{border-collapse:collapse;margin:8px 0 18px;font-size:12.5px}td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;vertical-align:top}th{background:#f3f4f6}"
           "h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #eee;padding-bottom:4px}</style><h1>Evaluation scorecard</h1>"]
    for title, paras, tables in S:
        out.append(f"<h2>{html.escape(title)}</h2>")
        out += [f"<p>{inline(p)}</p>" for p in paras]
        for head, rows in tables:
            out.append("<table><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in head) + "</tr>")
            out += ["<tr>" + "".join(f"<td>{inline(x)}</td>" for x in r) + "</tr>" for r in rows]
            out.append("</table>")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="adversarial: 5 variants per mutation instead of 20")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--live-judge", action="store_true", help="call Jev for judge questions not yet recorded, and record them")
    ap.add_argument("--live-jev", action="store_true", help="run the Jev red-team set live and record it")
    ap.add_argument("--max-live-tokens", type=int, default=700_000, help="stop live judge calls beyond this many input tokens")
    ap.add_argument("--out", default=str(REPORTS))
    args = ap.parse_args()
    t_all = time.perf_counter()
    REPORTS.mkdir(parents=True, exist_ok=True)
    gate = yaml.safe_load((ROOT / "evals" / "gate.yaml").read_text())
    R: dict = {"generated_at": time.strftime("%Y-%m-%d %H:%M %Z"), "mode": "live-record" if (args.live_judge or args.live_jev) else "offline replay",
               "gate_file": "evals/gate.yaml"}

    if args.skip_tests:
        R["tests"], R["skipped_tests"] = {"summary": "skipped", "passed": 0, "failed": 0, "errors": 0, "exit_code": 0}, True
    else:
        log("unit tests ...")
        R["tests"] = unit_tests()
    log("labelled decisions + PDFs + adversarial ...")
    dec, adv = decisions(5 if args.quick else 20)
    runs = dec.pop("_runs")
    R["decisions"], R["adversarial"] = dec, adv
    log("Jev role evals (replay) ...")
    R["jev_roles"] = jev_roles()

    from evals.judge.backends import get_judge
    from evals.judge.core import artifacts_from_run, load_rubrics
    from evals.judge.gold import load as load_gold
    from evals.judge.meta_eval import score, validate

    rubrics = load_rubrics()
    model = next(iter(rubrics.values()))["judge"]["model"]
    judge = get_judge(model, mode="live" if args.live_judge else "replay")

    class Budget:
        def __init__(self, inner, cap):
            self.inner, self.cap = inner, cap

        def ask(self, state, questions, repeat=0):
            if self.inner.mode == "live" and self.inner.ledger["live_input_tokens"] >= self.cap:
                self.inner.mode = "replay"
                log(f"live token cap {self.cap:,} reached: switching judge to replay")
            return self.inner.ask(state, questions, repeat)

    bj = Budget(judge, args.max_live_tokens)
    log("judge validation on the gold set ...")
    gold = load_gold()
    R["judge_validation"] = validate(bj, gold)
    log("judge scoring of real outputs ...")
    arts = [a for cid, r in runs.items() for a in artifacts_from_run(cid, r)]
    R["judge_scores"] = score(bj, arts, R["judge_validation"])

    log("security red team ...")
    from evals.security import redteam as rt

    pipe = rt.pipeline_attacks()
    files = rt.file_attacks()
    if args.live_jev:
        os.environ["JEV_FILL_MISSING"] = "1"  # record only questions never asked; existing recordings stay untouched
    jv = rt.jev_attacks("replay")
    ja = rt.judge_attacks(bj, gold)
    R["security"] = rt.summarise(pipe, files, jv, ja)
    R["security_rows"] = {"pipeline": pipe["rows"] + files["rows"], "jev": jv["rows"], "judge": ja["rows"]}
    log("robustness ...")
    from evals.security import robustness

    R["robustness"] = robustness.run()

    led = judge.ledger
    R["judge_meta"] = {"backend": judge.name, "mode": judge.mode, "calls": led["calls"], "input_tokens_replayed_or_live": led["input_tokens"],
                       "live_calls_this_run": led["live_calls"], "live_input_tokens_this_run": led["live_input_tokens"], "not_recorded": led["not_recorded"],
                       "models_seen": sorted({r["meta"]["model"] for r in R["judge_scores"]["rows"] if r["meta"]["model"]}),
                       "latency_ms_p50": int(statistics.median(led["latency_ms"])) if led["latency_ms"] else None,
                       "rubrics": {k: {"file": v["file"], "version": v["version"], "sha256": v["sha256"], "model": v["judge"]["model"]} for k, v in rubrics.items()}}
    jr = R["jev_roles"].get("cost", {})
    price = 0.042
    R["cost"] = {
        "pipeline latency p50 (ms / invoice)": dec["pipeline_latency_ms"]["p50"],
        "pipeline latency p95 (ms / invoice)": dec["pipeline_latency_ms"]["p95"],
        "Jev role evals: calls / input tokens / US$ (one live run)": f"{jr.get('calls')} / {jr.get('input_tokens'):,} / {jr.get('usd')}",
        "Jev role latency p50 / p95 (ms, live)": f"{jr.get('latency_ms_p50')} / {jr.get('latency_ms_p95')}",
        "Judge: calls / input tokens per full harness run (if live)": f"{led['calls']} / {led['input_tokens']:,}",
        "Judge: US$ per full harness run (if live)": round(led["input_tokens"] * price / 1e6, 4),
        "Judge latency p50 (ms, live)": R["judge_meta"]["latency_ms_p50"],
        "Jev red-team: input tokens (if live)": sum(r.get("tokens") or 0 for r in jv["rows"]),
        "Live tokens spent by THIS run (judge)": led["live_input_tokens"],
        "Cost of this run in replay mode": "US$0 (no network)",
    }
    R["gate"] = apply_gate(R, gate)
    R["seconds"] = round(time.perf_counter() - t_all, 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "harness_report.json").write_text(json.dumps(R, indent=1, default=str, ensure_ascii=False))
    S = sections(R)
    (out / "scorecard.md").write_text(render_md(S))
    (out / "scorecard.html").write_text(render_html(S))
    ok = all(x["ok"] for x in R["gate"])
    for x in R["gate"]:
        print(("PASS " if x["ok"] else "FAIL ") + f"{x['check']:58} {fmt(x['value']):>14}   target {x['target']}")
    log(f"gate {'PASSED' if ok else 'FAILED'}: {sum(x['ok'] for x in R['gate'])}/{len(R['gate'])}  ->  {out / 'scorecard.md'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
