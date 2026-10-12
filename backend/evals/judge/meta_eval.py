"""Judge validation (meta-eval) and judge scoring of the real pipeline outputs.

validate(judge)  gold set -> per criterion: accuracy, precision / recall on "bad", Cohen's kappa (bootstrap 95% CI),
                 consistency (same request asked twice), position bias (case and output swapped), verbosity bias
                 (neutral padding added to the graded text). A criterion is TRUSTED only if it clears every bar in TRUST;
                 otherwise it is ADVISORY: shown, never gated on.
score(judge, artifacts)  the real outputs of the 31 labelled runs, graded per rubric, with the deterministic twins.
"""
from __future__ import annotations

import random
import statistics
from typing import Any

from .core import deterministic_checks, grade, load_rubrics
from .gold import load as load_gold

TRUST = {"accuracy": 0.85, "kappa": 0.60, "bad_recall": 0.80, "max_consistency_flips": 1, "max_bias_flip_rate": 0.10}


def kappa(pairs: list[tuple[int, int]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa1 = sum(a for a, _ in pairs) / n
    pb1 = sum(b for _, b in pairs) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else (1.0 if po == 1 else 0.0)


def kappa_ci(pairs: list[tuple[int, int]], n_boot: int = 1000, seed: int = 7) -> list[float] | None:
    if len(pairs) < 5:
        return None
    rnd = random.Random(seed)
    ks = []
    for _ in range(n_boot):
        s = [pairs[rnd.randrange(len(pairs))] for _ in pairs]
        k = kappa(s)
        if k is not None:
            ks.append(k)
    ks.sort()
    return [round(ks[int(0.025 * len(ks))], 3), round(ks[int(0.975 * len(ks)) - 1], 3)]


def binary(pairs: list[tuple[int, int]]) -> dict[str, Any]:
    """pairs = (gold, judge) with 1 = good. 'bad' is the positive class for precision / recall."""
    tp = sum(1 for g, j in pairs if g == 0 and j == 0)
    fp = sum(1 for g, j in pairs if g == 1 and j == 0)
    fn = sum(1 for g, j in pairs if g == 0 and j == 1)
    tn = sum(1 for g, j in pairs if g == 1 and j == 1)
    n = len(pairs)
    return {"n": n, "bad_in_gold": tp + fn, "accuracy": round((tp + tn) / n, 3) if n else None,
            "bad_precision": round(tp / (tp + fp), 3) if tp + fp else None, "bad_recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "kappa": kappa(pairs), "kappa_ci95": kappa_ci(pairs), "confusion": {"tp_bad_caught": tp, "fp_good_flagged": fp, "fn_bad_missed": fn, "tn": tn}}


def validate(judge, gold: list[dict] | None = None) -> dict[str, Any]:
    rubrics = load_rubrics()
    gold = gold or load_gold()
    rows = []
    for item in gold:
        rb = rubrics[item["kind"]]
        res = {"base": grade(judge, rb, item["case"], item["output"], "base", 0),
               "repeat": grade(judge, rb, item["case"], item["output"], "base", 1),
               "swap": grade(judge, rb, item["case"], item["output"], "swap", 0)}
        if item["kind"] != "evidence_panel":
            res["pad"] = grade(judge, rb, item["case"], item["output"], "pad", 0)
        rows.append({"item": item, "res": res, "det": deterministic_checks({**item, "raw_handoff": None})})
    per: dict[str, Any] = {}
    for kind, rb in rubrics.items():
        for c in rb["criteria"]:
            cid = c["id"]
            rr = [r for r in rows if r["item"]["kind"] == kind]
            got = [(r["item"]["labels"][cid], r["res"]["base"]["criteria"][cid]) for r in rr]
            missing = sum(1 for _, x in got if x["p"] is None)
            pairs = [(g, int(x["raw_pass"])) for g, x in got if x["p"] is not None]
            m = binary(pairs)

            def flips(variant):
                fl, d = 0, []
                for r in rr:
                    a, b = r["res"]["base"]["criteria"][cid], (r["res"].get(variant) or {}).get("criteria", {}).get(cid)
                    if not b or a["p"] is None or b["p"] is None:
                        continue
                    fl += a["raw_pass"] != b["raw_pass"]
                    d.append(b["p"] - a["p"])
                return {"n": len(d), "flips": fl, "flip_rate": round(fl / len(d), 3) if d else None,
                        "mean_delta_p": round(statistics.mean(d), 3) if d else None, "mean_abs_delta_p": round(statistics.mean(abs(x) for x in d), 3) if d else None}

            cons, swap, pad = flips("repeat"), flips("swap"), flips("pad")
            reasons = []
            if missing:
                reasons.append(f"{missing} answers not recorded")
            if (m["accuracy"] or 0) < TRUST["accuracy"]:
                reasons.append(f"accuracy {m['accuracy']} < {TRUST['accuracy']}")
            if (m["kappa"] if m["kappa"] is not None else -1) < TRUST["kappa"]:
                reasons.append(f"kappa {m['kappa']} < {TRUST['kappa']}")
            if m["bad_recall"] is not None and m["bad_recall"] < TRUST["bad_recall"]:
                reasons.append(f"bad recall {m['bad_recall']} < {TRUST['bad_recall']}")
            if cons["flips"] > TRUST["max_consistency_flips"]:
                reasons.append(f"{cons['flips']} flips when asked twice")
            for nm, b in (("position", swap), ("verbosity", pad)):
                if b["flip_rate"] is not None and b["flip_rate"] > TRUST["max_bias_flip_rate"]:
                    reasons.append(f"{nm} bias flip rate {b['flip_rate']}")
            per[f"{kind}.{cid}"] = {**m, "not_recorded": missing, "consistency": cons, "position_bias": swap, "verbosity_bias": pad,
                                   "status": "trusted" if not reasons else "advisory", "why_advisory": reasons}
    # item level: an item is bad if any criterion is bad
    item_pairs = []
    for r in rows:
        crit = r["res"]["base"]["criteria"]
        if any(x["p"] is None for x in crit.values()):
            continue
        item_pairs.append((int(all(v == 1 for v in r["item"]["labels"].values())), int(all(x["raw_pass"] for x in crit.values()))))
    # deterministic twins vs the gold labels (exact checks, no model)
    twins = {}
    for name, kind, crit_id, fn in (
        ("groundedness checker vs explanation.grounded", "explanation", "grounded", lambda d: int(d["grounded"])),
        ("groundedness checker vs vendor_email.no_invented_facts", "vendor_email", "no_invented_facts", lambda d: int(d["grounded"])),
        ("no-email-for-bank-change rule vs vendor_email.appropriate_to_send", "vendor_email", "appropriate_to_send", lambda d: int(d["email_allowed_for_case"])),
        ("citation completeness vs evidence_panel.every_finding_cited", "evidence_panel", "every_finding_cited", lambda d: int(d["complete"])),
    ):
        pairs = [(r["item"]["labels"][crit_id], fn(r["det"])) for r in rows if r["item"]["kind"] == kind]
        twins[name] = binary(pairs)
    return {"trust_bars": TRUST, "criteria": per, "item_level": binary(item_pairs), "deterministic_twins": twins,
            "gold": {"n": len(gold), "by_kind": {k: sum(1 for g in gold if g["kind"] == k) for k in rubrics},
                     "bad_items": sum(1 for g in gold if g["origin"] == "hand-written bad"),
                     "hard_cases_v2": sum(1 for g in gold if "hard case" in g["origin"])},
            "rows": [{"id": r["item"]["id"], "kind": r["item"]["kind"], "origin": r["item"]["origin"], "why": r["item"]["why"], "labels": r["item"]["labels"],
                      "judge_p": {k: v["p"] for k, v in r["res"]["base"]["criteria"].items()},
                      "repeat_p": {k: v["p"] for k, v in r["res"]["repeat"]["criteria"].items()},
                      "swap_p": {k: v["p"] for k, v in r["res"]["swap"]["criteria"].items()},
                      "pad_p": {k: v["p"] for k, v in (r["res"].get("pad") or {"criteria": {}})["criteria"].items()},
                      "deterministic": r["det"]} for r in rows]}


def score(judge, artifacts: list[dict], validation: dict[str, Any]) -> dict[str, Any]:
    rubrics = load_rubrics()
    out: dict[str, Any] = {}
    rows = []
    for a in artifacts:
        rb = rubrics[a["kind"]]
        g = grade(judge, rb, a["case"], a["output"], "base", 0)
        rows.append({"case_id": a["case_id"], "kind": a["kind"], "criteria": {k: v["p"] for k, v in g["criteria"].items()},
                     "pass": {k: v["pass"] for k, v in g["criteria"].items()}, "deterministic": deterministic_checks(a), "meta": g["meta"]})
    for kind, rb in rubrics.items():
        rr = [r for r in rows if r["kind"] == kind]
        crit = {}
        for c in rb["criteria"]:
            cid = c["id"]
            ps = [r["criteria"][cid] for r in rr if r["criteria"][cid] is not None]
            passes = [r["pass"][cid] for r in rr if r["pass"][cid] is not None]
            status = validation["criteria"][f"{kind}.{cid}"]["status"]
            crit[cid] = {"n": len(ps), "pass_rate": round(sum(passes) / len(passes), 3) if passes else None,
                         "mean_p": round(statistics.mean(ps), 3) if ps else None, "min_p": min(ps) if ps else None, "status": status,
                         "failing_cases": [r["case_id"] for r in rr if r["pass"][cid] is False]}
        trusted = [v["pass_rate"] for v in crit.values() if v["status"] == "trusted" and v["pass_rate"] is not None]
        det = [r["deterministic"] for r in rr]
        out[kind] = {"n": len(rr), "criteria": crit, "trusted_pass_rate": round(min(trusted), 3) if trusted else None,
                     "deterministic": {
                         "grounded_rate": round(sum(1 for d in det if d.get("grounded")) / len(det), 3) if det and "grounded" in det[0] else None,
                         "ungrounded_examples": [(r["case_id"], r["deterministic"].get("ungrounded_facts")) for r in rr if r["deterministic"].get("grounded") is False][:5],
                         "complete_rate": round(sum(1 for d in det if d.get("complete")) / len(det), 3) if det and "complete" in det[0] else None,
                         "emails_for_forbidden_cases": sum(1 for d in det if d.get("email_allowed_for_case") is False),
                         "handoff_schema_errors": sum(1 for d in det if d.get("schema_errors")),
                     }}
    return {"by_kind": out, "rows": rows}
