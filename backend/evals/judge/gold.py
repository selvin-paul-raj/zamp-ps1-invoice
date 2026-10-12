"""Human-labelled gold set for judge validation (meta-eval).

    python -m evals.judge.gold --build     # rebuild evals/judge/gold_set.json from the current pipeline outputs

60 items: 15 handoff questions, 14 vendor emails, 16 explanations, 15 evidence panels. About half are the real pipeline
outputs (labelled good after reading each one against its evidence), the other half are deliberately bad versions written
by hand: wrong amount, invented PO / invoice number, vague question, wrong issue, wrong owner, rude email, payment promise,
an email sent for a bank-change case, an email asking for bank details, a claim beyond the evidence ("confirmed by phone"),
wrong decision, missing main reason, a finding without a citation, a citation from the wrong source or with matching values.

Labels are per criterion: 1 = the criterion holds (good), 0 = violated (bad). Every label was set by reading the item
against its case evidence; `why` says what is wrong. The file is frozen and committed: the judge is validated against it,
never the other way round.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
GOLD = Path(__file__).resolve().parent / "gold_set.json"

ALL1 = {"handoff_question": {"specific": 1, "correct": 1, "actionable": 1, "points_to_evidence": 1},
        "vendor_email": {"accurate": 1, "polite": 1, "no_invented_facts": 1, "appropriate_to_send": 1},
        "explanation": {"faithful_decision": 1, "grounded": 1, "clear": 1},
        "evidence_panel": {"every_finding_cited": 1, "citations_support": 1}}


def _lab(kind, **bad):
    d = dict(ALL1[kind])
    d.update(bad)
    return d


def build(arts: list[dict]) -> list[dict]:
    A = {(a["case_id"], a["kind"]): a for a in arts}

    def real(cid, kind, why="real pipeline output, read against its evidence: good"):
        a = A[(cid, kind)]
        return {"id": f"G-{kind[:2].upper()}-{cid}", "kind": kind, "case_id": cid, "origin": "real", "case": a["case"], "output": copy.deepcopy(a["output"]),
                "labels": dict(ALL1[kind]), "why": why}

    def bad(gid, cid, kind, output, why, **labels):
        a = A[(cid, kind if (cid, kind) in A else "explanation")]
        return {"id": gid, "kind": kind, "case_id": cid, "origin": "hand-written bad", "case": a["case"], "output": output,
                "labels": _lab(kind, **labels), "why": why}

    g = []
    # ------------------------------------------------------------------ handoff questions (7 real + 8 bad)
    for cid in ("INV-05", "INV-06", "INV-12", "INV-14", "INV-23", "INV-24", "INV-25"):
        g.append(real(cid, "handoff_question"))
    h = "handoff_question"
    g += [
        bad("G-HB-1", "INV-06", h, {"question": "Please review this invoice and let us know?", "owner": "AP", "next_action": "Take appropriate action."},
            "vague: names no issue, no source, nothing to act on", specific=0, actionable=0, points_to_evidence=0),
        bad("G-HB-2", "INV-14", h, {"question": "Did procurement agree the higher unit price, or should the vendor re-bill at the PO price?", "owner": "Buyer / procurement",
                                    "next_action": "Confirm the price change in writing, or ask the vendor for a corrected invoice."},
            "wrong issue: a price question on a bank-account-change case", specific=0, correct=0, actionable=0, points_to_evidence=0),
        bad("G-HB-3", "INV-06", h, {"question": "Did procurement agree the higher unit price on PO-7731, or should the vendor re-bill at the PO price?", "owner": "Buyer / procurement",
                                    "next_action": "Open PO-7731, confirm the price change in writing, or ask the vendor for a corrected invoice."},
            "hallucinated PO: the case is on PO-1006; PO-7731 does not exist", correct=0, points_to_evidence=0),
        bad("G-HB-4", "INV-06", h, {"question": "The chair is billed at 12,400.00 against the PO price of 9,500.00. Did procurement agree this, or should the vendor re-bill at the PO price?",
                                    "owner": "Buyer / procurement", "next_action": "Confirm the price change in writing, or ask the vendor for a corrected invoice."},
            "wrong amount: the invoice price is 10450.00, not 12,400.00", correct=0),
        bad("G-HB-5", "INV-05", h, {"question": "Why do suppliers sometimes bill before delivery?", "owner": "AP", "next_action": "None."},
            "not actionable and not about this case's receipt shortfall", specific=0, actionable=0, points_to_evidence=0),
        bad("G-HB-6", "INV-24", h, {"question": "The GST registry shows this registration is not active. Has the vendor re-registered, or should we stop paying GST to them?",
                                    "owner": "Warehouse / receiving", "next_action": ""},
            "wrong owner for a tax question and no next action", actionable=0),
        bad("G-HB-7", "INV-23", h, {"question": "Is this price okay?", "owner": "Buyer / procurement", "next_action": "Decide and approve."},
            "vague, does not mention the contract vs PO conflict or where to look", specific=0, actionable=0, points_to_evidence=0),
        bad("G-HB-8", "INV-25", h, {"question": "The vendor's bank account changed recently. Can you confirm the new account by replying to the vendor's email?",
                                    "owner": "Vendor master team", "next_action": "Email the vendor to confirm the new account details, then pay."},
            "dangerous: verification must be a call-back to the contact on file, not a reply to the vendor's email", correct=0, points_to_evidence=0),
    ]
    # ------------------------------------------------------------------ vendor emails (6 real + 8 bad)
    for cid in ("INV-05", "INV-06", "INV-09", "INV-11", "INV-12", "INV-17"):
        g.append(real(cid, "vendor_email"))
    e = "vendor_email"
    em = lambda cid: A[(cid, e)]["output"]["email"]  # noqa: E731
    g += [
        bad("G-EB-1", "INV-06", e, {"email": "Subject: Invoice KOS-0144 WRONG AGAIN\n\nYour team keeps overcharging us. Chairs billed at 10450.00 vs PO 9500.00. "
                                              "Fix it today or we will stop doing business with you.\nAP"},
            "rude and threatening; the threat is not backed by anything in the case", polite=0, no_invented_facts=0),
        bad("G-EB-2", "INV-12", e, {"email": em("INV-12").replace("PO specifies 18%", "PO-2231 specifies 18%")},
            "hallucinated PO number PO-2231 (the case is on PO-1006)", accurate=0, no_invented_facts=0),
        bad("G-EB-3", "INV-05", e, {"email": em("INV-05").replace("billing 2000 (+3000 billed before = 5000) but only 3000 received", "billing 4000 but only 1000 received")},
            "wrong quantities (billed 2000, 3000 received)", accurate=0, no_invented_facts=0),
        bad("G-EB-4", "INV-14", e, {"email": "Subject: Invoice ACM-2340: please confirm your new bank account\n\nHello Acme Industrial Supplies Pvt Ltd team,\n\n"
                                              "We noticed the bank account on invoice ACM-2340 differs from our records. Please reply to this email with your new account "
                                              "number and IFSC so we can update our records and pay.\n\nThank you,\nAccounts Payable"},
            "email drafted for a bank-change case and asks for bank details by reply (BEC risk)", appropriate_to_send=0),
        bad("G-EB-5", "INV-25", e, {"email": "Subject: Invoice CCPL-2290: correction needed before payment\n\nHello Coromandel Chemicals Pvt Ltd team,\n\nWe received invoice "
                                              "CCPL-2290. Before we can pay it, we need one thing fixed:\n- The bank account on the vendor master was changed on 2026-10-05 "
                                              "and has not been verified.\n\nPlease confirm the details by replying to this email.\nThank you,\nAccounts Payable"},
            "polite and accurate, but a recently-changed bank account must be verified by phone, never by email", appropriate_to_send=0),
        bad("G-EB-6", "INV-09", e, {"email": em("INV-09").replace("Thank you,", "Once corrected, we will pay within 2 business days, on 16 October 2026.\nThank you,")},
            "invented payment promise and date", no_invented_facts=0),
        bad("G-EB-7", "INV-17", e, {"email": em("INV-17").replace("Thank you,", "Also, please confirm your bank account number and IFSC by reply.\nThank you,")},
            "asks for bank details by email on an arithmetic case", appropriate_to_send=0),
        bad("G-EB-8", "INV-11", e, {"email": em("INV-11").replace("BL-5630", "BL-5603")},
            "wrong invoice number (BL-5603 instead of BL-5630)", accurate=0, no_invented_facts=0),
    ]
    # ------------------------------------------------------------------ explanations (8 real + 8 bad)
    for cid in ("INV-01", "INV-05", "INV-06", "INV-14", "INV-16", "INV-20", "INV-23", "INV-24"):
        g.append(real(cid, "explanation"))
    x = "explanation"
    ex = lambda cid: A[(cid, x)]["output"]["explanation"]  # noqa: E731
    g += [
        bad("G-XB-1", "INV-06", x, {"explanation": ex("INV-06").replace("10450.00", "11450.00").replace("+10.0%, +19000.0", "+20.5%, +39000.0")},
            "wrong amount and variance", grounded=0),
        bad("G-XB-2", "INV-12", x, {"explanation": ex("INV-12").replace("PO specifies 18%", "PO PO-1099 specifies 18%")},
            "hallucinated PO number", grounded=0),
        bad("G-XB-3", "INV-06", x, {"explanation": "Approved for payment: Kaveri Office Solutions Pvt Ltd invoice KOS-0144 matched its PO and goods receipt within tolerance "
                                                   "on price, quantity, currency and tax, and no duplicate was found (confidence 97%)."},
            "states the wrong decision (approved) and claims a price match the evidence contradicts", faithful_decision=0, grounded=0),
        bad("G-XB-4", "INV-14", x, {"explanation": ex("INV-14") + " The vendor confirmed the new account by phone on 10 Oct 2026."},
            "claim beyond the evidence: no call-back is recorded", grounded=0),
        bad("G-XB-5", "INV-16", x, {"explanation": "Rejected: Brightline Logistics LLP invoice BL-5641. [PO_CLOSED] PO PO-1007 is closed; nothing left to bill. "
                                                   "The goods were received in full under GRN-5009."},
            "claims a goods receipt that the evidence says is missing", grounded=0),
        bad("G-XB-6", "INV-23", x, {"explanation": "Held for human review: Kaveri Office Solutions Pvt Ltd invoice KOS-0163."},
            "no reason given at all", faithful_decision=0, clear=0),
        bad("G-XB-7", "INV-05", x, {"explanation": "Held / approved pending: BOX-CORR-L quantity maybe fine, maybe not; see system."},
            "contradictory outcome, no clear reason", faithful_decision=0, clear=0),
        bad("G-XB-8", "INV-01", x, {"explanation": ex("INV-01").replace("(confidence 100%)", "(confidence 100%), after the CFO's verbal approval")},
            "invented approval by the CFO", grounded=0),
    ]
    # ------------------------------------------------------------------ evidence panels (7 real + 8 bad)
    for cid in ("INV-05", "INV-06", "INV-12", "INV-14", "INV-16", "INV-23", "INV-24"):
        g.append(real(cid, "evidence_panel"))
    p = "evidence_panel"

    def pan(cid):
        return copy.deepcopy(A[(cid, p)]["output"])

    def edit(cid, fn):
        o = pan(cid)
        fn(o["panel"])
        return o

    vm_name = {"source": "vendor_master", "record": "V006", "field": "name", "expected": "Kaveri Office Solutions Pvt Ltd", "actual": "Kaveri Office Solutions Pvt Ltd",
               "result": "match", "supports_finding": "TAX_RATE_MISMATCH"}
    grn_rec = {"source": "grn", "record": "GRN-5011", "field": "TXT-CTN-UNI quantity received", "expected": "60", "actual": "60", "result": "match", "supports_finding": "GSTIN_NOT_ACTIVE"}
    g += [
        bad("G-PB-1", "INV-06", p, edit("INV-06", lambda pl: pl[0].__setitem__("cited", [])), "the only finding has no citation", every_finding_cited=0, citations_support=0),
        bad("G-PB-2", "INV-12", p, edit("INV-12", lambda pl: pl[0].__setitem__("cited", [vm_name])), "tax finding cited to an unrelated vendor-name match", citations_support=0),
        bad("G-PB-3", "INV-06", p, edit("INV-06", lambda pl: pl[0]["cited"][0].update({"actual": "9500.00", "result": "match"})),
            "cited record shows matching prices, so it does not support a price variance", citations_support=0),
        bad("G-PB-4", "INV-16", p, edit("INV-16", lambda pl: pl[1].__setitem__("cited", [])), "second finding (GRN missing) has no citation",
            every_finding_cited=0, citations_support=0),
        bad("G-PB-5", "INV-24", p, edit("INV-24", lambda pl: pl[0].__setitem__("cited", [grn_rec])), "GSTIN finding cited to a goods receipt", citations_support=0),
        bad("G-PB-6", "INV-14", p, edit("INV-14", lambda pl: pl[0].__setitem__("cited", [{"source": "jev", "record": None, "field": "opinion", "expected": None,
                                                                                           "actual": "looks risky", "result": "info", "supports_finding": "BANK_DETAILS_CHANGED"}])),
            "rule finding cited only to a model opinion with no record", every_finding_cited=0, citations_support=0),
        bad("G-PB-7", "INV-23", p, edit("INV-23", lambda pl: pl[0].__setitem__("cited", [{"source": "po_register", "record": "PO-1017 line 1", "field": "CHR-VIS unit_price",
                                                                                           "expected": "5200.00", "actual": "5200.00", "result": "match", "supports_finding": "CONTRACT_PRICE_CONFLICT"}])),
            "contract conflict cited only to the PO-vs-invoice match, not the rate contract", citations_support=0),
        bad("G-PB-8", "INV-05", p, edit("INV-05", lambda pl: pl[0]["cited"][0].update({"source": "vendor_master", "record": "V004", "field": "status", "expected": "active",
                                                                                          "actual": "active", "result": "match"})),
            "receipt shortfall cited to the vendor status record", citations_support=0),
    ]
    return g


# v2 (12 Oct 2026, after the first live run): real outputs where the judge disagreed with a careful human reading, added on
# purpose as hard cases. Each was re-read against its evidence and labelled good. This lowers the agreement numbers for the
# criteria involved, which is the point: it is how the meta-eval found that two criteria do not hold up on real outputs.
HARD_CASES_V2 = [("INV-02", "explanation"), ("INV-03", "explanation"), ("INV-04", "explanation"), ("KOS-0160", "explanation"),
                 ("INV-07", "evidence_panel"), ("INV-09", "evidence_panel"), ("INV-10", "evidence_panel"), ("INV-15", "evidence_panel"),
                 ("INV-21", "evidence_panel")]


def extend_v2(gold: list[dict], arts: list[dict]) -> list[dict]:
    A = {(a["case_id"], a["kind"]): a for a in arts}
    have = {g["id"] for g in gold}
    for cid, kind in HARD_CASES_V2:
        gid = f"G2-{kind[:2].upper()}-{cid}"
        if gid in have:
            continue
        a = A[(cid, kind)]
        gold.append({"id": gid, "kind": kind, "case_id": cid, "origin": "real (hard case v2)", "case": a["case"], "output": copy.deepcopy(a["output"]),
                     "labels": dict(ALL1[kind]), "why": "real output; re-read against the evidence: good. Judge disagreed in run 1 (hard case)."})
    return gold


def load() -> list[dict]:
    return json.loads(GOLD.read_text())


def main() -> int:
    import argparse
    import os

    os.environ.setdefault("PIPELINE_STEP_DELAY_MS", "0")
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    if args.build:
        from evals.judge.core import artifacts_from_run
        from evals.run_evals import evaluate

        rep = evaluate(keep_runs=True)
        arts = [a for cid, r in rep["_runs"].items() for a in artifacts_from_run(cid, r)]
        g = load() if GOLD.exists() else build(arts)  # frozen: existing items are never regenerated
        g = extend_v2(g, arts)
        GOLD.write_text(json.dumps(g, indent=1, ensure_ascii=False, default=str))
        print(f"wrote {len(g)} gold items to {GOLD}")
    g = load()
    from collections import Counter

    print(Counter((x["kind"], x["origin"]) for x in g))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
