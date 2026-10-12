"""Evidence-based evaluation of the model roles (Jev) against rules-only, on labelled sets.

    python -m evals.model_evals            # replay mode: recorded real Jev answers, no network, no cost
    python -m evals.model_evals --live     # real Jev calls (needs TYPESAFE_API_KEY); records answers for replay

Four sets, each labelled by construction (what the right answer is, independent of either system):
  1. duplicate pairs   (new invoice, earlier invoice) -> is it the same charge billed again?   [noul]
  2. PO choice         invoice without a PO + 2-3 open candidate POs -> which one (or "can't tell")  [choice]
  3. field support     OCR / PDF text + an extracted value (right or corrupted) -> does the text support it?  [noul]
  4. end-to-end challenge: disguised re-submissions and legitimate repeat invoices through the full pipeline,
     rules-only vs rules + Jev. The number that must stay 0: false approvals.

Metrics: precision / recall / false-hold rate at the production gate, calibration (expected calibration error,
reliability diagram), latency and token cost per call. Output: docs/EVALS.md (model section), docs/img/reliability.png,
evals/model_eval_report.json.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import os
import statistics
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PIPELINE_STEP_DELAY_MS", "0")

from app.config import Settings  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.domain import Invoice, MasterData, POLine, PriorInvoice, PurchaseOrder, Vendor  # noqa: E402
from app.jev import ModelAssist  # noqa: E402
from app.pipeline import Ctx, step_duplicates  # noqa: E402
from app.service import Service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed"
DOCS = ROOT.parent / "docs"


# ------------------------------------------------------------------ 1. duplicate pairs
BASES = [  # (vendor_id, vendor name, number, date, po, currency, [(sku, desc, qty, price)], service?)
    ("V002", "Brightline Logistics LLP", "BL-5602", "2026-10-02", "PO-1002", "INR", [("FRT-BLR-CHN", "Freight trip Bengaluru-Chennai", 3, "18000.00")], True),
    ("V001", "Acme Industrial Supplies Pvt Ltd", "ACM-2301", "2026-10-03", "PO-1001", "INR", [("STL-ROD-12", "Steel rod 12mm", 500, "120.00"), ("BOLT-M10", "Hex bolt M10", 2000, "8.50")], False),
    ("V006", "Kaveri Office Solutions Pvt Ltd", "KOS-0160", "2026-10-10", "PO-1011", "INR", [("PPR-A4-500", "A4 copier paper, ream of 500", 200, "260.00")], False),
    ("V004", "Pacific Packaging Co", "PP-7781", "2026-10-04", "PO-1004", "USD", [("BOX-CORR-L", "Corrugated box, large", 3000, "0.80")], False),
    ("V007", "Deccan Electricals Pvt Ltd", "DE/26-27/0412", "2026-10-08", "PO-1013", "INR", [("HT-CBL-240", "HT cable 240 sq mm (metre)", 300, "720.00")], False),
    ("V008", "Coromandel Chemicals Pvt Ltd", "CCPL-2290", "2026-10-09", "PO-1015", "INR", [("CHM-IPA-20", "Isopropyl alcohol, 20 L can", 20, "2150.00")], False),
    ("V003", "Nordic Sensors AB", "NS-2026-114", "2026-10-01", "PO-1003", "EUR", [("TMP-SNS-200", "Temperature sensor TS-200", 90, "42.00")], False),
    ("V009", "Vellore Textiles", "VT/118", "2026-10-08", "PO-1016", "INR", [("TXT-CTN-UNI", "Cotton uniform set", 60, "650.00")], False),
    ("V002", "Brightline Logistics LLP", "BL-5650", "2026-10-09", "PO-1012", "INR", [("FRT-CHN-CBE", "Freight trip Chennai-Coimbatore", 4, "15000.00")], True),
    ("V010", "Sundaram Facility Services", "SFS-0912", "2026-09-30", "PO-2001", "INR", [("FM-HK-MON", "Housekeeping services, Chennai office, September 2026", 1, "84000.00")], True),
    ("V011", "Annai IT Rentals", "AIR-311", "2026-10-01", "PO-2002", "INR", [("LAP-RENT", "Laptop rental, 40 units, October 2026", 40, "1450.00")], True),
    ("V012", "Mylapore Print House", "MPH-77", "2026-10-05", "PO-2003", "INR", [("PRT-BRO-A5", "A5 product brochure, 4 colour", 5000, "6.20")], False),
]


def _inv(vid, vname, num, dt, po, ccy, lines, notes=""):
    ls = [{"sku": s, "description": d, "quantity": str(q), "unit_price": p, "tax_rate": "0.18"} for s, d, q, p in lines]
    sub = sum(Decimal(l["quantity"]) * Decimal(l["unit_price"]) for l in ls)
    return {"vendor_id": vid, "vendor_name": vname, "invoice_number": num, "invoice_date": dt, "po_number": po, "currency": ccy, "lines": ls,
            "subtotal": f"{sub:.2f}", "tax_total": f"{sub * Decimal('0.18'):.2f}", "total": f"{sub * Decimal('1.18'):.2f}", "notes": notes}


def _shift(d, days):
    from datetime import date, timedelta
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def duplicate_pairs() -> list[dict]:
    pairs = []
    for i, (vid, vn, num, dt, po, ccy, lines, service) in enumerate(BASES):
        base = _inv(vid, vn, num, dt, po, ccy, lines)
        digits = "".join(ch for ch in num if ch.isdigit())
        alt_num = f"{num.split('-')[0].split('/')[0]}-{int(digits[-4:] or 0) + 37:04d}"
        pos = [
            ("reformatted_number", _inv(vid, vn, num.replace("-", "/").replace("/", " ", 1) if "-" in num else num.replace("/", "-"), dt, po, ccy, lines, "Reminder: payment pending")),
            ("new_number_same_bill", _inv(vid, vn, alt_num, _shift(dt, 4), po, ccy, lines, "Please process at the earliest")),
            ("new_number_price_nudged", _inv(vid, vn, alt_num, _shift(dt, 6), po, ccy, [(s, d, q, f"{Decimal(p) * Decimal('1.015'):.2f}") for s, d, q, p in lines], "Revised invoice")),
            ("resend_late_new_date", _inv(vid, vn, num + "-R1", _shift(dt, 40), po, ccy, lines, "Re-sent: original not paid")),
        ]
        if service:
            nxt = [(s, d.replace("September", "October").replace("October 2026", "November 2026") if ("2026" in d) else d + " (next month)", q, p) for s, d, q, p in lines]
            neg_recurring = _inv(vid, vn, alt_num, _shift(dt, 31), po, ccy, nxt, "Monthly bill for the next service period")
        else:
            neg_recurring = _inv(vid, vn, alt_num, _shift(dt, 33), "PO-" + str(9000 + i), ccy, lines, "Repeat order on the new PO")
        neg = [
            ("next_delivery_other_qty", _inv(vid, vn, alt_num, _shift(dt, 10), po, ccy, [(s, d, max(1, int(q * 0.4)), p) for s, d, q, p in lines])),
            ("recurring_or_repeat_order", neg_recurring),
            ("different_item_similar_amount", _inv(vid, vn, alt_num, _shift(dt, 12), po, ccy,
                                                   [("SVC-INST", "Installation and commissioning charges", 1, f"{sum(Decimal(str(q)) * Decimal(p) for _, _, q, p in lines):.2f}")])),
        ]
        for name, new in pos:
            pairs.append({"id": f"D{i:02d}-{name}", "label": 1, "kind": name, "base": base, "new": new})
        for name, new in neg:
            pairs.append({"id": f"D{i:02d}-{name}", "label": 0, "kind": name, "base": base, "new": new})
    return pairs


def _ctx_for_pair(pair, settings) -> Ctx:
    b, n = pair["base"], pair["new"]
    vendor = Vendor(b["vendor_id"], b["vendor_name"], None, b["currency"], None)
    prior = PriorInvoice("PRIOR-1", b["vendor_id"], b["invoice_number"], b["po_number"], Invoice.from_payload(b).invoice_date, Decimal(b["total"]), "paid",
                         Invoice.from_payload(b).fingerprint(), [{"sku": l["sku"], "quantity": l["quantity"], "amount": str(Decimal(l["quantity"]) * Decimal(l["unit_price"]))} for l in b["lines"]])
    md = MasterData({vendor.id: vendor}, {}, {}, {}, [prior])
    ctx = Ctx(Invoice.from_payload(n), md, settings.tolerances, settings)
    ctx.vendor = vendor
    return ctx


def eval_duplicates(assist: ModelAssist, settings) -> list[dict]:
    rows = []
    for pair in duplicate_pairs():
        ctx = _ctx_for_pair(pair, settings)
        rules = step_duplicates(ctx)  # rules only (ctx.assist is None)
        rule_flag = any(f.severity in ("hold", "reject") for f in rules.findings)
        ctx2 = _ctx_for_pair(pair, settings)
        f: list = []
        assist.duplicate_check(ctx2, f)
        call = next((c for c in ctx2.callbuf if c["role"] == "duplicate_judgement"), None)
        rows.append({"id": pair["id"], "kind": pair["kind"], "label": pair["label"], "rules_flag": rule_flag,
                     "p": call["probability"] if call else None, "asked": call is not None,
                     "latency_ms": call["latency_ms"] if call else None, "tokens": call["input_tokens"] if call else 0,
                     "mode": call["mode"] if call else None, "model": call["model"] if call else None})
    return rows


# ------------------------------------------------------------------ 2. PO choice
def _po(num, vid, ccy, date, lines):
    return PurchaseOrder(num, vid, ccy, "open", Invoice.from_payload({"invoice_date": date}).invoice_date,
                         [POLine(i + 1, s, d, Decimal(str(q)), Decimal(p), Decimal("0.18")) for i, (s, d, q, p) in enumerate(lines)])


PO_CASES = [
    # (id, invoice lines, [candidate POs], correct PO or None when it cannot be told from the documents)
    ("C01", [("TAPE-48", "Packing tape 48mm - delivered to Chennai DC2, Sriperumbudur", 300, "1.20")],
     [("PO-A1", [("TAPE-48", "Packing tape 48mm, ship to Bengaluru DC1", 600, "1.20")]), ("PO-A2", [("TAPE-48", "Packing tape 48mm, ship to Chennai DC2 (Sriperumbudur)", 300, "1.20")])], "PO-A2"),
    ("C02", [("TNR-HP26", "Toner HP 26A for the Guindy office", 10, "3200.00")],
     [("PO-B1", [("TNR-HP26", "Toner cartridge HP 26A - Guindy office", 10, "3200.00")]), ("PO-B2", [("TNR-HP26", "Toner cartridge HP 26A - Ambattur plant", 40, "3200.00")])], "PO-B1"),
    ("C03", [("STL-ROD-12", "Steel rod 12mm", 100, "118.00")],
     [("PO-C1", [("STL-ROD-12", "Steel rod 12mm", 100, "118.00")]), ("PO-C2", [("STL-ROD-12", "Steel rod 12mm", 500, "120.00")])], "PO-C1"),
    ("C04", [("CBL-CAT6", "Cat6 cable, project ORION fit-out, 2nd floor", 30, "5400.00")],
     [("PO-D1", [("CBL-CAT6", "Cat6 cable box 305 m - project HELIX", 30, "5400.00")]), ("PO-D2", [("CBL-CAT6", "Cat6 cable box 305 m - project ORION", 30, "5400.00")]),
      ("PO-D3", [("CBL-CAT6", "Cat6 cable box 305 m - general stock", 50, "5400.00")])], "PO-D2"),
    ("C05", [("FRT-CHN-CBE", "Freight trip Chennai-Coimbatore", 2, "15000.00")],
     [("PO-E1", [("FRT-CHN-CBE", "Freight trip Chennai-Coimbatore", 10, "15000.00"), ("FRT-CHN-MDU", "Freight trip Chennai-Madurai", 5, "17000.00")]),
      ("PO-E2", [("FRT-CHN-CBE", "Freight trip Chennai-Coimbatore", 10, "16500.00")])], "PO-E1"),
    ("C06", [("PPR-A4-500", "A4 copier paper, ream of 500", 50, "255.00")],
     [("PO-F1", [("PPR-A4-500", "A4 copier paper, ream of 500", 200, "260.00")]), ("PO-F2", [("PPR-A4-500", "A4 copier paper, ream of 500", 50, "255.00")])], "PO-F2"),
    ("C07", [("CHR-ERG", "Ergonomic chair, black mesh, HR floor", 12, "9500.00")],
     [("PO-G1", [("CHR-ERG", "Ergonomic chair - grey fabric - finance floor", 8, "9500.00")]), ("PO-G2", [("CHR-ERG", "Ergonomic chair - black mesh - HR floor", 12, "9500.00")])], "PO-G2"),
    ("C08", [("BOX-CORR-L", "Corrugated box, large, Coimbatore warehouse", 1000, "0.80")],
     [("PO-H1", [("BOX-CORR-L", "Corrugated box, large - Coimbatore WH", 1000, "0.80")]), ("PO-H2", [("BOX-CORR-L", "Corrugated box, large - Chennai DC2", 1000, "0.80")]),
      ("PO-H3", [("BOX-CORR-L", "Corrugated box, large - Hosur", 2000, "0.78")])], "PO-H1"),
    ("C09", [("LAP-RENT", "Laptop rental, 40 units, October 2026", 40, "1450.00")],
     [("PO-I1", [("LAP-RENT", "Laptop rental - Sep 2026", 40, "1450.00")]), ("PO-I2", [("LAP-RENT", "Laptop rental - Oct 2026", 40, "1450.00")])], "PO-I2"),
    ("C10", [("HT-CBL-240", "HT cable 240 sq mm, Sriperumbudur substation", 200, "720.00")],
     [("PO-J1", [("HT-CBL-240", "HT cable 240 sq mm - Hosur substation", 800, "720.00")]), ("PO-J2", [("HT-CBL-240", "HT cable 240 sq mm - Sriperumbudur substation", 200, "720.00")])], "PO-J2"),
    ("C11", [("CHM-IPA-20", "Isopropyl alcohol 20 L, QC lab", 10, "2150.00")],
     [("PO-K1", [("CHM-IPA-20", "IPA 20 L can - production", 40, "2150.00")]), ("PO-K2", [("CHM-IPA-20", "IPA 20 L can - QC lab", 10, "2150.00")])], "PO-K2"),
    ("C12", [("TXT-CTN-UNI", "Cotton uniform set, size M, plant staff", 120, "650.00")],
     [("PO-L1", [("TXT-CTN-UNI", "Cotton uniform set - plant staff", 120, "650.00")]), ("PO-L2", [("TXT-CTN-UNI", "Cotton uniform set - security staff", 30, "690.00")])], "PO-L1"),
    # Ambiguous on purpose: the candidates are identical, so the documents cannot tell. Right behaviour: no suggestion.
    ("C13", [("BOLT-M10", "Hex bolt M10", 500, "8.50")],
     [("PO-M1", [("BOLT-M10", "Hex bolt M10", 2000, "8.50")]), ("PO-M2", [("BOLT-M10", "Hex bolt M10", 2000, "8.50")])], None),
    ("C14", [("TNR-HP88", "Toner cartridge HP 88A", 10, "2900.00")],
     [("PO-N1", [("TNR-HP88", "Toner cartridge HP 88A", 30, "2900.00")]), ("PO-N2", [("TNR-HP88", "Toner cartridge HP 88A", 30, "2900.00")]),
      ("PO-N3", [("TNR-HP88", "Toner cartridge HP 88A", 30, "2900.00")])], None),
    ("C15", [("WLD-ROD-3", "Welding rod 3.15mm (kg)", 100, "45.00")],
     [("PO-O1", [("WLD-ROD-3", "Welding rod 3.15mm (kg)", 1000, "45.00")]), ("PO-O2", [("WLD-ROD-3", "Welding rod 3.15mm (kg)", 1000, "45.00")])], None),
]


def eval_po_choice(assist: ModelAssist, settings) -> list[dict]:
    rows = []
    vendor = Vendor("V900", "Test Vendor Pvt Ltd", None, "INR", None)
    for cid, lines, cands, correct in PO_CASES:
        pos = [_po(n, vendor.id, "INR", "2026-09-20", ls) for n, ls in cands]
        inv = Invoice.from_payload(_inv(vendor.id, vendor.name, f"TV-{cid}", "2026-10-09", None, "INR", lines))
        ctx = Ctx(inv, MasterData({vendor.id: vendor}, {p.po_number: p for p in pos}, {}, {}, []), settings.tolerances, settings)
        ctx.vendor = vendor
        sug = assist.choose_po(ctx, pos)
        call = ctx.callbuf[0]
        probs = call["options"] if isinstance(call["options"], dict) else {}
        top = call["answer"]
        # Raw model behaviour without the guards (for the report): ask once, plain order.
        raw = _raw_po(assist, ctx, pos) if call["outcome"] == "indistinguishable_to_human" else (top, call["probability"])
        rows.append({"id": cid, "correct": correct, "top": top or raw[0], "p_top": call["probability"] if call["probability"] is not None else raw[1],
                     "raw_top": raw[0], "raw_p": raw[1], "outcome": call["outcome"], "suggested": sug["po_number"] if sug else None,
                     "n_candidates": len(pos), "probabilities": probs, "latency_ms": call["latency_ms"], "tokens": call["input_tokens"], "mode": call["mode"],
                     "model": call["model"]})
    return rows


def _raw_po(assist, ctx, pos):
    """Ask once with no guard, to measure what the model would have done on indistinguishable candidates."""
    from app.safety import Redactor
    from app.jev import _inv_view, _choice_answer, _fake_choice

    red = Redactor()
    crit = {po.po_number: red.obj({"po_date": str(po.po_date), "lines": [{"sku": l.sku, "description": l.description, "quantity": str(l.quantity),
                                                                          "unit_price": str(l.unit_price)} for l in po.lines]}) for po in pos}
    state = {"invoice": _inv_view(ctx.inv, red)}
    q = {"type": "choice", "instructions": assist.PO_INSTR, "criteria": crit}
    resp, _ = assist._call(ctx, "choose_po", state, {"po": q}, lambda: {"po": _choice_answer(_fake_choice(state, crit))})
    a = resp["answers"]["po"]
    probs = a.get("probabilities") or {}
    top = a.get("choice") or max(probs, key=probs.get)
    return top, float(probs.get(top, 0))


# ------------------------------------------------------------------ 3. field support
def eval_fields(assist: ModelAssist, settings) -> list[dict]:
    from app.ingest import parse_upload

    rows = []
    labels = json.loads((SEED / "pdfs" / "labels.json").read_text())
    for name in labels:
        good = parse_upload(name, (SEED / "pdfs" / name).read_bytes())[0]
        bad1 = copy.deepcopy(good)
        t = bad1["total"]
        bad1["total"] = t[:-6] + t[-5] + t[-6] + t[-4:] if len(t) >= 7 and t[-6] != t[-5] else str(Decimal(t) + 900)
        bad1["invoice_number"] = bad1["invoice_number"][:-1] + ("7" if bad1["invoice_number"][-1] != "7" else "3")
        bad1["invoice_date"] = _shift(bad1["invoice_date"], 3)
        bad2 = copy.deepcopy(good)
        if bad2.get("vendor_tax_id"):
            g = bad2["vendor_tax_id"]
            bad2["vendor_tax_id"] = g[:5] + ("X" if g[5] != "X" else "Y") + g[6:]
        if bad2.get("po_number"):
            bad2["po_number"] = bad2["po_number"][:-1] + str((int(bad2["po_number"][-1]) + 1) % 10)
        if bad2.get("bank_account"):
            b = bad2["bank_account"]
            bad2["bank_account"] = b[:-3] + str((int(b[-3]) + 4) % 10) + b[-2:]
        for variant, payload in (("correct", good), ("corrupt_a", bad1), ("corrupt_b", bad2)):
            inv = Invoice.from_payload({**payload, "received_at": None})
            inv.extraction_confidence = min(inv.extraction_confidence, 0.85)
            ctx = Ctx(inv, MasterData({}, {}, {}, {}, []), settings.tolerances, settings)
            assist.field_check(ctx)
            for c in ctx.callbuf:
                fld = c["question"].split("show ")[1].split(" =")[0]
                truth = 1 if (str(payload.get(fld)) == str(good.get(fld))) else 0
                rows.append({"doc": name.split("_")[0], "method": good["extraction"]["method"], "variant": variant, "field": fld, "label": truth,
                             "p": c["probability"], "latency_ms": c["latency_ms"], "tokens": c["input_tokens"], "mode": c["mode"], "model": c["model"]})
    return rows


# ------------------------------------------------------------------ 4. end-to-end challenge set
CHALLENGE = [
    # (id, label, story, base seed id or payload, follow-up mutation)
    ("E1", "HOLD", "Freight bill re-sent under a new number with a 1.5% higher rate (inside price tolerance)", "INV-02",
     dict(invoice_number="BL-5617", invoice_date="2026-10-08", price_factor="1.015", notes="Revised invoice")),
    ("E2", "HOLD", "Same freight bill re-sent 5 days later under a new number, rate +1.9%", "INV-02",
     dict(invoice_number="BL-5629", invoice_date="2026-10-07", price_factor="1.019", notes="")),
    ("E3", "HOLD", "Tape invoice re-sent with a new number and a 1% higher price", "TAPE",
     dict(invoice_number="PP-7841", invoice_date="2026-10-12", price_factor="1.01", notes="Please process")),
    ("E4", "APPROVE", "Legitimate second freight invoice: 2 more trips, new number, a week later", "INV-02",
     dict(invoice_number="BL-5640", invoice_date="2026-10-09", qty="2", notes="")),
    ("E5", "APPROVE", "Legitimate second tape delivery: 100 more rolls, new number", "TAPE",
     dict(invoice_number="PP-7850", invoice_date="2026-10-14", qty="150", notes="")),
    ("E6", "APPROVE", "Legitimate freight invoice on a different route PO", "INV-02",
     dict(invoice_number="BL-5655", invoice_date="2026-10-10", po="PO-1012", sku=("FRT-CHN-CBE", "Freight trip Chennai-Coimbatore", "15000.00"), qty="2", notes="")),
]
TAPE_BASE = {"vendor_name": "Pacific Packaging Co", "invoice_number": "PP-7820", "invoice_date": "2026-10-09", "currency": "USD", "po_number": "PO-1019",
             "lines": [{"line_no": 1, "sku": "TAPE-48", "description": "Packing tape 48mm", "quantity": "100", "unit_price": "1.20", "tax_rate": "0"}],
             "received_at": "2026-10-10T09:00:00", "source": "email"}


def _retotal(p):
    sub = tax = Decimal("0")
    for ln in p["lines"]:
        amt = Decimal(str(ln["quantity"])) * Decimal(str(ln["unit_price"]))
        rate = Decimal(str(ln["tax_rate"]))
        ln["amount"], ln["tax_amount"] = f"{amt:.2f}", f"{(amt * rate).quantize(Decimal('0.01')):.2f}"
        sub += amt
        tax += Decimal(ln["tax_amount"])
    p["subtotal"], p["tax_total"], p["total"] = f"{sub:.2f}", f"{tax:.2f}", f"{sub + tax:.2f}"


def eval_challenge(mode: str) -> list[dict]:
    items = {i["id"]: i for i in json.loads((SEED / "invoices.json").read_text())}
    rows = []
    for cid, label, story, base_id, mut in CHALLENGE:
        settings = dataclasses.replace(Settings(), jev_mode=mode)
        svc = Service(make_engine(f"sqlite:///{tempfile.mkdtemp()}/c.db"), settings)
        svc.load_master(SEED)
        base = copy.deepcopy(TAPE_BASE if base_id == "TAPE" else {k: v for k, v in items[base_id].items() if k not in ("expected", "id")})
        _retotal(base)
        svc.ingest(base, idempotency_key="base")
        svc.drain(delay_ms=0)
        new = copy.deepcopy(base)
        new.update({"invoice_number": mut["invoice_number"], "invoice_date": mut["invoice_date"], "notes": mut.get("notes", ""),
                    "received_at": "2026-10-15T09:00:00"})
        if mut.get("po"):
            new["po_number"] = mut["po"]
        for ln in new["lines"]:
            if mut.get("sku"):
                ln["sku"], ln["description"], ln["unit_price"] = mut["sku"]
                ln["line_no"] = 1
            if mut.get("price_factor"):
                ln["unit_price"] = f"{Decimal(ln['unit_price']) * Decimal(mut['price_factor']):.2f}"
            if mut.get("qty"):
                ln["quantity"] = mut["qty"]
        _retotal(new)
        res = svc.ingest(new, idempotency_key="new")
        svc.drain(delay_ms=0)
        run = svc.get_run(res["run_id"])
        rows.append({"id": cid, "label": label, "story": story, "decision": run["decision"], "reason": run["primary_reason"],
                     "model_calls": len(run["model_calls"]), "max_p": max([c["probability"] or 0 for c in run["model_calls"]], default=None)})
    return rows


# ------------------------------------------------------------------ metrics
def binary_metrics(rows, pred_key, label_key="label"):
    tp = sum(1 for r in rows if r[pred_key] and r[label_key] == 1)
    fp = sum(1 for r in rows if r[pred_key] and r[label_key] == 0)
    fn = sum(1 for r in rows if not r[pred_key] and r[label_key] == 1)
    tn = sum(1 for r in rows if not r[pred_key] and r[label_key] == 0)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "false_hold_rate": round(fp / (fp + tn), 3) if fp + tn else None}


def ece(pairs, bins=10):
    """Expected calibration error: |mean predicted p - observed frequency| per bin, weighted by bin size."""
    pairs = [(p, y) for p, y in pairs if p is not None]
    if not pairs:
        return None, []
    out, total = [], 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, y) for p, y in pairs if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if not sel:
            continue
        mp = sum(p for p, _ in sel) / len(sel)
        fy = sum(y for _, y in sel) / len(sel)
        total += len(sel) / len(pairs) * abs(mp - fy)
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel), "mean_p": round(mp, 3), "observed": round(fy, 3)})
    return round(total, 3), out


def auc(pairs):
    pos = [p for p, y in pairs if y == 1 and p is not None]
    neg = [p for p, y in pairs if y == 0 and p is not None]
    if not pos or not neg:
        return None
    wins = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pos for b in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def brier(pairs):
    pairs = [(p, y) for p, y in pairs if p is not None]
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def plot_svg(rel: dict, path: Path) -> None:
    """Dependency-free reliability diagram (SVG). GitHub and the PDF report both render it."""
    W, H, L, B, T, R = 460, 400, 55, 50, 30, 20
    sx = lambda v: L + v * (W - L - R)  # noqa: E731
    sy = lambda v: H - B - v * (H - B - T)  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Helvetica,Arial,sans-serif" font-size="11">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           f'<text x="{W / 2}" y="18" text-anchor="middle" font-size="13" font-weight="bold">Reliability of Jev answers on our labelled sets</text>']
    for v in (0, 0.2, 0.4, 0.6, 0.8, 1.0):
        out.append(f'<line x1="{sx(v)}" y1="{sy(0)}" x2="{sx(v)}" y2="{sy(1)}" stroke="#eee"/><line x1="{sx(0)}" y1="{sy(v)}" x2="{sx(1)}" y2="{sy(v)}" stroke="#eee"/>')
        out.append(f'<text x="{sx(v)}" y="{sy(0) + 15}" text-anchor="middle">{v:.1f}</text><text x="{sx(0) - 8}" y="{sy(v) + 4}" text-anchor="end">{v:.1f}</text>')
    out.append(f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(1)}" y2="{sy(1)}" stroke="#999" stroke-dasharray="5,4"/>')
    out.append(f'<text x="{W / 2}" y="{H - 12}" text-anchor="middle">Jev probability (binned)</text>')
    out.append(f'<text x="14" y="{H / 2}" text-anchor="middle" transform="rotate(-90 14 {H / 2})">Observed frequency (yes / correct)</text>')
    colours = {"duplicate (noul)": "#c2410c", "field support (noul)": "#1d4ed8", "PO choice (top prob.)": "#15803d"}
    ly = T + 12
    out.append(f'<line x1="{L + 10}" y1="{ly}" x2="{L + 30}" y2="{ly}" stroke="#999" stroke-dasharray="5,4"/><text x="{L + 36}" y="{ly + 4}">perfect calibration</text>')
    for name, bins_ in rel.items():
        if not bins_:
            continue
        c = colours.get(name, "black")
        pts = " ".join(f"{sx(b['mean_p']):.1f},{sy(b['observed']):.1f}" for b in bins_)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="1.6"/>')
        for b in bins_:
            out.append(f'<circle cx="{sx(b["mean_p"]):.1f}" cy="{sy(b["observed"]):.1f}" r="4" fill="{c}"/>'
                       f'<text x="{sx(b["mean_p"]) + 6:.1f}" y="{sy(b["observed"]) + 12:.1f}" font-size="9" fill="{c}">n={b["n"]}</text>')
        ly += 16
        out.append(f'<line x1="{L + 10}" y1="{ly}" x2="{L + 30}" y2="{ly}" stroke="{c}" stroke-width="2"/><text x="{L + 36}" y="{ly + 4}">{name}</text>')
    out.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out))


def plot(rel: dict, path: Path):
    plot_svg(rel, path.with_suffix(".svg"))
    try:
        import matplotlib
    except ImportError:
        return

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.2, 4.6), dpi=150)
    ax.plot([0, 1], [0, 1], ls="--", c="#999", lw=1, label="perfect calibration")
    styles = {"duplicate (noul)": ("o", "#c2410c"), "field support (noul)": ("s", "#1d4ed8"), "PO choice (top prob.)": ("^", "#15803d")}
    for name, bins_ in rel.items():
        if not bins_:
            continue
        m, c = styles.get(name, ("o", "k"))
        ax.plot([b["mean_p"] for b in bins_], [b["observed"] for b in bins_], marker=m, c=c, lw=1.2, label=name)
        for b in bins_:
            ax.annotate(str(b["n"]), (b["mean_p"], b["observed"]), textcoords="offset points", xytext=(4, -10), fontsize=6, color=c)
    ax.set_xlabel("Jev probability (binned)")
    ax.set_ylabel("Observed frequency of 'yes' / correct")
    ax.set_title("Reliability of Jev answers on our labelled sets", fontsize=10)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="call the real Jev API (records answers for replay)")
    ap.add_argument("--out", default=str(ROOT / "evals" / "model_eval_report.json"))
    args = ap.parse_args()
    mode = "live" if args.live else "replay"
    if args.live:
        os.environ["JEV_RECORD"] = "1"
    settings = dataclasses.replace(Settings(), jev_mode=mode, jev_record=args.live)
    assist = ModelAssist(settings)

    dup = eval_duplicates(assist, settings)
    po = eval_po_choice(assist, settings)
    fields = eval_fields(assist, settings)
    chall = {m: eval_challenge(m) for m in ("off", mode)}

    gate_d, gate_p, gate_f = settings.jev_dup_hold_p, settings.jev_po_min_p, settings.jev_field_min_p
    for r in dup:
        r["jev_flag"] = r["p"] is not None and r["p"] >= gate_d
        r["combined_flag"] = r["rules_flag"] or r["jev_flag"]
    sweep = []
    for g in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        for r in dup:
            r["_f"] = r["rules_flag"] or (r["p"] is not None and r["p"] >= g)
        sweep.append({"gate": g, **binary_metrics(dup, "_f")})
    for r in dup:
        r.pop("_f", None)
    labelled_po = [r for r in po if r["correct"]]
    amb_po = [r for r in po if not r["correct"]]
    for r in fields:
        r["flag"] = r["p"] is not None and r["p"] < gate_f
        r["wrong"] = 1 - r["label"]
    e_d, rel_d = ece([(r["p"], r["label"]) for r in dup])
    e_f, rel_f = ece([(r["p"], r["label"]) for r in fields])
    e_p, rel_p = ece([(r["p_top"], 1 if r["top"] == r["correct"] else 0) for r in labelled_po], bins=5)
    calls = [r for r in dup if r["asked"]] + po + list({(r["doc"], r["variant"]): r for r in fields}.values())
    lat = [r["latency_ms"] for r in calls if r.get("latency_ms")]
    toks = sum(r["tokens"] or 0 for r in dup + po + fields)
    models = sorted({r.get("model") for r in dup + po + fields if r.get("model")})
    modes = sorted({r.get("mode") for r in dup + po + fields if r.get("mode")})
    rep = {
        "mode_requested": mode, "modes_seen": modes, "models_seen": models,
        "gates": {"duplicate_hold_p": gate_d, "po_min_p": gate_p, "field_min_p": gate_f},
        "duplicates": {"n": len(dup), "positives": sum(r["label"] for r in dup), "rules_only": binary_metrics(dup, "rules_flag"),
                       "jev_only": binary_metrics(dup, "jev_flag"), "rules_plus_jev": binary_metrics(dup, "combined_flag"),
                       "gate_sweep_rules_plus_jev": sweep, "ece": e_d, "auc": auc([(r["p"], r["label"]) for r in dup]),
                       "jev_added_true_holds": sum(1 for r in dup if r["jev_flag"] and not r["rules_flag"] and r["label"] == 1),
                       "jev_added_false_holds": sum(1 for r in dup if r["jev_flag"] and not r["rules_flag"] and r["label"] == 0),
                       "rule_false_holds_jev_would_clear": sum(1 for r in dup if r["rules_flag"] and r["label"] == 0 and r["p"] is not None and r["p"] < gate_d), "brier": brier([(r["p"], r["label"]) for r in dup]),
                       "by_kind": {k: {"n": len(v), "label": v[0]["label"], "rules_flagged": sum(x["rules_flag"] for x in v),
                                       "jev_flagged": sum(x["jev_flag"] for x in v), "mean_p": round(statistics.mean(x["p"] for x in v if x["p"] is not None), 3)}
                                   for k in sorted({r["kind"] for r in dup}) for v in [[r for r in dup if r["kind"] == k]]}},
        "po_choice": {"n": len(po), "labelled": len(labelled_po), "ambiguous": len(amb_po),
                      "top1_accuracy": round(sum(r["top"] == r["correct"] for r in labelled_po) / len(labelled_po), 3),
                      "suggested_at_gate": sum(1 for r in labelled_po if r["suggested"]),
                      "suggestion_accuracy": round(sum(r["suggested"] == r["correct"] for r in labelled_po if r["suggested"]) / max(1, sum(1 for r in labelled_po if r["suggested"])), 3),
                      "ambiguous_sent_to_person": sum(1 for r in amb_po if not r["suggested"]),
                      "ambiguous_raw_model_confident": sum(1 for r in amb_po if (r["raw_p"] or 0) >= gate_p),
                      "ambiguous_raw_p": [r["raw_p"] for r in amb_po],
                      "ece_top_prob": e_p},
        "fields": {"n": len(fields), "wrong_values": sum(r["wrong"] for r in fields),
                   "wrong_value_caught": binary_metrics([{**r, "label": r["wrong"]} for r in fields], "flag"), "ece": e_f,
                   "brier": brier([(r["p"], r["label"]) for r in fields])},
        "challenge_end_to_end": {m: {"rows": rows, "false_approvals": sum(1 for r in rows if r["label"] != "APPROVE" and r["decision"] == "APPROVE"),
                                     "false_holds": sum(1 for r in rows if r["label"] == "APPROVE" and r["decision"] != "APPROVE")} for m, rows in chall.items()},
        "cost": {"calls": len(calls), "input_tokens": toks, "usd": round(toks * settings.jev_price_per_mtok_usd / 1e6, 6),
                 "latency_ms_p50": int(statistics.median(lat)) if lat else None, "latency_ms_p95": int(sorted(lat)[int(0.95 * (len(lat) - 1))]) if lat else None},
        "reliability": {"duplicate (noul)": rel_d, "field support (noul)": rel_f, "PO choice (top prob.)": rel_p},
        "rows": {"duplicates": dup, "po_choice": po, "fields": fields},
    }
    Path(args.out).write_text(json.dumps(rep, indent=1, default=str))
    plot(rep["reliability"], DOCS / "img" / "reliability.png")
    d, p, f_ = rep["duplicates"], rep["po_choice"], rep["fields"]
    print(f"mode={mode} models={models} modes={modes}")
    print(f"DUPLICATES n={d['n']} pos={d['positives']}  rules={d['rules_only']}  jev={d['jev_only']}  rules+jev={d['rules_plus_jev']}  ECE={d['ece']} Brier={d['brier']}")
    for k, v in d["by_kind"].items():
        print(f"   {k:32} {v}")
    print(f"DUP extra: AUC={d['auc']} jev_added_true={d['jev_added_true_holds']} jev_added_false={d['jev_added_false_holds']} rule_false_holds_jev_would_clear={d['rule_false_holds_jev_would_clear']}")
    print(f"PO raw on ambiguous: confident={p['ambiguous_raw_model_confident']} p={p['ambiguous_raw_p']}")
    print(f"PO CHOICE top1={p['top1_accuracy']} suggested={p['suggested_at_gate']}/{p['labelled']} acc_when_suggested={p['suggestion_accuracy']} ambiguous_to_person={p['ambiguous_sent_to_person']}/{p['ambiguous']} ECE={p['ece_top_prob']}")
    print(f"FIELDS n={f_['n']} wrong={f_['wrong_values']} caught={f_['wrong_value_caught']} ECE={f_['ece']}")
    for m, v in rep["challenge_end_to_end"].items():
        print(f"CHALLENGE [{m}] false_approvals={v['false_approvals']} false_holds={v['false_holds']}  " + ", ".join(f"{r['id']}:{r['decision'][:4]}/{r['reason']}" for r in v["rows"]))
    print(f"COST {rep['cost']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
