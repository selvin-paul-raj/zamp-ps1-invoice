"""Invoice -> PO matching pipeline (pure, deterministic, testable).

Steps (each emits one StepResult, streamed to the UI as an SSE event):
  1 intake        required fields, extraction confidence
  2 arithmetic    line math, subtotal, tax total, grand total
  3 vendor_match  tax-id exact -> fuzzy name; blocked vendor; bank-detail change (BEC)
  4 po_lookup     explicit PO / implied PO from the vendor's open POs; PO status + vendor
  5 currency      invoice vs PO vs vendor master currency
  6 three_way     invoice vs PO vs GRN per line: price tolerance, received qty, cumulative PO usage
  7 tax           tax rate vs PO, tax amount vs rate x base
  8 duplicates    fingerprint / normalised number / reference drift + amount + date window
  9 decide        rules engine -> APPROVE | HOLD_FOR_REVIEW | REJECT with reasons + confidence
 10 explain       optional LLM adapter (unconfigured by default) else template explanation
"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict
from decimal import Decimal
from typing import Callable, Optional

from .config import Settings, Tolerances
from .decision import LLMAdapter, NullLLMAdapter, RulesDecisionEngine, handoff_for, template_explanation
from .domain import (
    Decision,
    Finding,
    Invoice,
    MasterData,
    PurchaseOrder,
    StepResult,
    Vendor,
    gstin_valid,
    invoice_number_core,
    looks_like_gstin,
    mask_account,
    money,
    normalize_invoice_number,
)

URGENCY_RE = re.compile(r"\b(urgent|immediately|new (bank )?account|changed? (our )?bank|asap|pay today)\b", re.I)


class TransientStepError(RuntimeError):
    """Raised to simulate a flaky dependency (OCR/LLM/ERP timeout). The worker retries it."""


class Ctx:
    def __init__(self, inv: Invoice, md: MasterData, tol: Tolerances, settings: Optional[Settings] = None):
        self.settings = settings or Settings()
        self.buyer_gstin = self.settings.buyer_gstin
        self.inv = inv
        self.md = md
        self.tol = tol
        self.vendor: Optional[Vendor] = None
        self.po: Optional[PurchaseOrder] = None
        self.line_map: dict[int, int] = {}  # invoice line index -> PO line_no


# ---------------------------------------------------------------- helpers
_SUFFIXES = r"\b(pvt|private|ltd|limited|llp|llc|inc|co|company|corp|corporation|ab|gmbh|plc)\b"


def _norm_name(s: str) -> str:
    s = re.sub(r"[.']", "", (s or "").lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(_SUFFIXES, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _name_score(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio()


def _priors_for_vendor(ctx: Ctx):
    vid = ctx.vendor.id if ctx.vendor else None
    return [p for p in ctx.md.priors if vid and p.vendor_id == vid]


def _extraction_message(inv: Invoice) -> str:
    ex = inv.extraction or {}
    method = ex.get("method")
    if method == "ocr":
        return (f"Read by OCR from a scanned PDF (mean OCR confidence {ex.get('ocr_mean_confidence') or 0:.0f}%, extraction confidence "
                f"{inv.extraction_confidence:.2f}). Policy: OCR reads are confirmed by a person before payment in v1.")
    if method == "llm":
        return f"Fields were read by the LLM fallback (unknown layout, confidence {inv.extraction_confidence:.2f}); a person confirms them before payment."
    failed = [k for k, v in (ex.get("checks") or {}).items() if not v]
    return (f"Fields were extracted with confidence {inv.extraction_confidence:.2f}"
            + (f"; cross-checks failed: {', '.join(failed)}" if failed else "") + ". A person should confirm them.")


# ---------------------------------------------------------------- steps
def step_intake(ctx: Ctx) -> StepResult:
    inv = ctx.inv
    f: list[Finding] = []
    missing = [
        k
        for k, v in {
            "invoice_number": inv.invoice_number,
            "vendor_name": inv.vendor_name,
            "invoice_date": inv.invoice_date,
            "currency": inv.currency,
            "total": inv.total,
        }.items()
        if v in (None, "")
    ]
    if not inv.lines:
        missing.append("lines")
    for k in missing:
        f.append(Finding("MISSING_FIELD", "hold", f"Required field '{k}' is missing or unreadable.", "intake", 0.99, {"field": k}))
    conf = 1.0
    if inv.extraction_confidence < 0.9:
        f.append(
            Finding(
                "LOW_EXTRACTION_CONFIDENCE",
                "hold",
                _extraction_message(inv),
                "intake",
                0.9,
                {"extraction_confidence": inv.extraction_confidence},
            )
        )
        conf = inv.extraction_confidence
    if inv.doc_type == "credit_note":
        f.append(Finding("CREDIT_NOTE", "info", "Document is a credit note (money owed back to us), not a bill to pay. It follows the credit-note path.", "intake", 0.95))
    elif inv.doc_type not in ("invoice", "tax_invoice"):
        f.append(Finding("UNSUPPORTED_DOC_TYPE", "hold", f"Document type '{inv.doc_type}' is not an invoice (e.g. a quote or proforma); nothing should be paid on it.", "intake", 0.95))
    blocking = [x for x in f if x.severity != "info"]
    status = "fail" if blocking else "pass"
    return StepResult(
        "intake",
        "Intake & required fields",
        status,
        f"{len(inv.lines)} line(s), {inv.currency} {money(inv.total) if inv.total is not None else '?'} from {inv.source}"
        + (f"; missing {', '.join(missing)}" if missing else ""),
        f,
        {"invoice_number": inv.invoice_number, "vendor_name": inv.vendor_name, "source": inv.source, "doc_type": inv.doc_type,
         "extraction": inv.extraction or None},
        conf,
    )


def step_arithmetic(ctx: Ctx) -> StepResult:
    inv, tol = ctx.inv, ctx.tol
    f: list[Finding] = []
    slack = tol.rounding_per_line * max(1, len(inv.lines))
    computed_sub = Decimal("0")
    computed_tax = Decimal("0")
    for i, ln in enumerate(inv.lines, start=1):
        amt = ln.quantity * ln.unit_price
        if ln.amount is not None and abs(ln.amount - amt) > tol.rounding_per_line:
            f.append(Finding("ARITHMETIC_MISMATCH", "hold", f"Line {i}: {ln.quantity} x {ln.unit_price} = {money(amt)} but invoice shows {money(ln.amount)}.", "arithmetic", 0.98))
        computed_sub += amt
        computed_tax += (ln.tax_amount if ln.tax_amount is not None else amt * ln.tax_rate)
    if inv.subtotal is not None and abs(inv.subtotal - computed_sub) > slack:
        f.append(Finding("ARITHMETIC_MISMATCH", "hold", f"Subtotal {money(inv.subtotal)} != sum of lines {money(computed_sub)}.", "arithmetic", 0.98))
    tax = inv.tax_total if inv.tax_total is not None else computed_tax
    sub = inv.subtotal if inv.subtotal is not None else computed_sub
    if inv.total is not None and abs(inv.total - (sub + tax)) > slack + tol.tax_abs:
        f.append(Finding("ARITHMETIC_MISMATCH", "hold", f"Total {money(inv.total)} != subtotal {money(sub)} + tax {money(tax)} = {money(sub + tax)}.", "arithmetic", 0.98))
    return StepResult(
        "arithmetic",
        "Arithmetic validation",
        "fail" if f else "pass",
        "Line math and totals reconcile" if not f else f"{len(f)} arithmetic issue(s)",
        f,
        {"computed_subtotal": money(computed_sub), "computed_tax": money(computed_tax)},
    )


def step_vendor(ctx: Ctx) -> StepResult:
    inv, md = ctx.inv, ctx.md
    f: list[Finding] = []
    method, score, vendor = None, 0.0, None
    if inv.vendor_tax_id:
        for v in md.vendors.values():
            if v.tax_id and v.tax_id.replace(" ", "").upper() == inv.vendor_tax_id.replace(" ", "").upper():
                vendor, method, score = v, "tax_id", 1.0
                break
    if vendor is None:
        best = None
        for v in md.vendors.values():
            s = max([_name_score(inv.vendor_name, v.name)] + [_name_score(inv.vendor_name, a) for a in v.aliases])
            if best is None or s > best[0]:
                best = (s, v)
        if best and best[0] >= 0.88:
            score, vendor, method = best[0], best[1], "fuzzy_name"
        elif best and best[0] >= 0.7:
            f.append(Finding("VENDOR_AMBIGUOUS", "hold", f"'{inv.vendor_name}' only loosely matches vendor master '{best[1].name}' (similarity {best[0]:.2f}).", "vendor_match", 0.8, {"candidate": best[1].id, "score": round(best[0], 3)}))
        else:
            f.append(Finding("VENDOR_UNKNOWN", "reject", f"'{inv.vendor_name}' is not in the vendor master.", "vendor_match", 0.95, {"best_score": round(best[0], 3) if best else 0}))
    if inv.vendor_tax_id and looks_like_gstin(inv.vendor_tax_id) and not gstin_valid(inv.vendor_tax_id):
        f.append(Finding("INVALID_GSTIN", "hold", f"GSTIN {inv.vendor_tax_id} fails the GST checksum (typo or fake registration). Input tax credit would be at risk.", "vendor_match", 0.97, {"gstin": inv.vendor_tax_id}))
    ctx.vendor = vendor
    conf = 1.0 if method == "tax_id" else (0.9 + 0.1 * score if vendor else 1.0)
    if vendor:
        if vendor.status != "active":
            f.append(Finding("VENDOR_BLOCKED", "reject", f"Vendor {vendor.id} ({vendor.name}) is {vendor.status} in the vendor master.", "vendor_match", 0.99, {"vendor_id": vendor.id}))
        if inv.bank_account and vendor.bank_account and inv.bank_account.replace(" ", "") != vendor.bank_account.replace(" ", ""):
            urgency = bool(URGENCY_RE.search(inv.notes or ""))
            f.append(
                Finding(
                    "BANK_DETAILS_CHANGED",
                    "hold",
                    f"Remit-to account {mask_account(inv.bank_account)} differs from vendor master {mask_account(vendor.bank_account)}"
                    + (" and the invoice uses urgency / new-account language" if urgency else "")
                    + ". Verify out-of-band with the contact on file before paying (BEC control).",
                    "vendor_match",
                    0.99,
                    {"invoice_account": mask_account(inv.bank_account), "master_account": mask_account(vendor.bank_account), "urgency_language": urgency},
                )
            )
    summary = (
        f"Matched {vendor.id} {vendor.name} via {method} (score {score:.2f})" if vendor else "No confident vendor match"
    )
    return StepResult("vendor_match", "Vendor master match", "fail" if any(x.severity != "info" for x in f) else "pass", summary, f,
                      {"vendor_id": vendor.id if vendor else None, "method": method, "score": round(score, 3)}, conf)


def step_po(ctx: Ctx) -> StepResult:
    inv, md = ctx.inv, ctx.md
    f: list[Finding] = []
    data: dict = {}
    conf = 1.0
    if inv.po_number:
        po = md.pos.get(inv.po_number.strip().upper())
        if po is None:
            f.append(Finding("PO_NOT_FOUND", "hold", f"PO {inv.po_number} is not in the PO register.", "po_lookup", 0.95))
        else:
            ctx.po = po
            data["method"] = "explicit"
    elif ctx.vendor:
        # Implied PO: vendor's open POs that contain every invoiced SKU with enough open quantity.
        skus = {l.sku for l in inv.lines if l.sku}
        cands = []
        for po in md.pos.values():
            if po.vendor_id != ctx.vendor.id or po.status != "open":
                continue
            po_skus = {l.sku for l in po.lines}
            if skus and skus <= po_skus:
                cands.append(po)
        data["candidates"] = [c.po_number for c in cands]
        if len(cands) == 1:
            ctx.po = cands[0]
            data["method"] = "inferred"
            conf = 0.7
            f.append(Finding("PO_INFERRED", "hold", f"Invoice has no PO reference; inferred {cands[0].po_number} as the only open PO for this vendor covering these items. Confirm the match.", "po_lookup", 0.85, {"inferred_po": cands[0].po_number}))
        else:
            f.append(Finding("PO_MISSING", "hold", f"Invoice has no PO reference and {len(cands)} open POs could fit; PO not determinable.", "po_lookup", 0.95, {"candidates": data["candidates"]}))
    else:
        f.append(Finding("PO_MISSING", "hold", "Invoice has no PO reference and the vendor is unknown.", "po_lookup", 0.9))

    po = ctx.po
    if po:
        if po.status in ("closed", "cancelled"):
            f.append(Finding("PO_CLOSED", "reject", f"PO {po.po_number} is {po.status}; nothing left to bill.", "po_lookup", 0.97))
        if ctx.vendor and po.vendor_id != ctx.vendor.id:
            f.append(Finding("PO_VENDOR_MISMATCH", "reject", f"PO {po.po_number} belongs to vendor {po.vendor_id}, not {ctx.vendor.id}.", "po_lookup", 0.97))
        data.update({"po_number": po.po_number, "po_status": po.status, "po_total": money(po.total), "grns": md.grn_refs.get(po.po_number, [])})
    summary = f"PO {po.po_number} ({data.get('method')}, {po.status})" if po else "No PO resolved"
    return StepResult("po_lookup", "PO lookup", "fail" if any(x.severity != "info" for x in f) else "pass", summary, f, data, conf)


def step_currency(ctx: Ctx) -> StepResult:
    inv = ctx.inv
    f: list[Finding] = []
    if ctx.po and inv.currency and inv.currency != ctx.po.currency:
        f.append(Finding("CURRENCY_MISMATCH", "hold", f"Invoice is in {inv.currency} but PO {ctx.po.po_number} is in {ctx.po.currency}; amounts cannot be compared without an agreed FX rate.", "currency", 0.97, {"invoice": inv.currency, "po": ctx.po.currency}))
    elif ctx.vendor and not ctx.po and inv.currency and inv.currency != ctx.vendor.currency:
        f.append(Finding("CURRENCY_MISMATCH", "hold", f"Invoice is in {inv.currency} but vendor {ctx.vendor.id} bills in {ctx.vendor.currency}.", "currency", 0.9))
    if not ctx.po:
        return StepResult("currency", "Currency check", "fail" if f else "skip", "No PO to compare against" if not f else f[0].message, f)
    return StepResult("currency", "Currency check", "fail" if f else "pass", f"{inv.currency} matches PO" if not f else f[0].message, f)


def step_three_way(ctx: Ctx) -> StepResult:
    inv, md, tol, po = ctx.inv, ctx.md, ctx.tol, ctx.po
    if po is None:
        return StepResult("three_way", "3-way match (invoice / PO / GRN)", "skip", "Skipped: no PO resolved")
    f: list[Finding] = []
    conf = 1.0
    rows = []
    by_no = {l.line_no: l for l in po.lines}
    by_sku = {l.sku: l for l in po.lines}

    # Quantity / amount already consumed on this PO by prior invoices (paid or approved).
    used_qty: dict[int, Decimal] = defaultdict(Decimal)
    used_amt = Decimal("0")
    for p in md.priors:
        if p.po_number != po.po_number or p.status not in ("paid", "approved"):
            continue
        for pl in p.lines:
            pline = by_no.get(pl.get("line_no")) or by_sku.get(pl.get("sku"))
            if pline:
                used_qty[pline.line_no] += Decimal(str(pl.get("quantity", 0)))
        used_amt += sum((Decimal(str(pl.get("amount", 0))) for pl in p.lines), Decimal("0"))

    this_amt = Decimal("0")
    # Prices / amounts in a different currency than the PO are not comparable (CURRENCY_MISMATCH holds it);
    # quantities still are.
    same_ccy = not inv.currency or inv.currency == po.currency
    for idx, ln in enumerate(inv.lines):
        pl = (by_no.get(ln.line_no) if ln.line_no else None) or (by_sku.get(ln.sku) if ln.sku else None)
        if pl is None:
            f.append(Finding("LINE_NOT_ON_PO", "hold", f"Invoice line '{ln.description or ln.sku}' has no matching line on PO {po.po_number}.", "three_way", 0.9))
            continue
        ctx.line_map[idx] = pl.line_no
        this_amt += ln.quantity * ln.unit_price
        # Price
        var = ln.unit_price - pl.unit_price
        var_pct = (var / pl.unit_price) if pl.unit_price else Decimal("0")
        var_amt = var * ln.quantity
        price_ok = not same_ccy or var <= 0 or (var_pct <= tol.price_pct and var_amt <= tol.price_abs_per_line)
        if not price_ok:
            f.append(Finding("PRICE_VARIANCE", "hold", f"{pl.sku}: unit price {ln.unit_price} vs PO {pl.unit_price} (+{float(var_pct):.1%}, +{money(var_amt)} on the line) exceeds tolerance ({float(tol.price_pct):.0%} / {money(tol.price_abs_per_line)}).", "three_way", 0.97, {"sku": pl.sku, "variance_pct": round(float(var_pct), 4), "variance_amount": money(var_amt)}))
        elif var > 0 and same_ccy:
            conf *= 0.95
            f.append(Finding("PRICE_VARIANCE_WITHIN_TOLERANCE", "info", f"{pl.sku}: +{float(var_pct):.2%} over PO price, within tolerance.", "three_way", 0.9))
        # Quantity vs received (GRN) and ordered, cumulative across invoices
        received = md.received.get((po.po_number, pl.line_no), Decimal("0"))
        prior = used_qty[pl.line_no]
        cum = prior + ln.quantity
        if received == 0:
            f.append(Finding("GRN_MISSING", "hold", f"{pl.sku}: no goods receipt recorded against PO {po.po_number} line {pl.line_no}.", "three_way", 0.95))
        elif cum > received:
            f.append(Finding("QTY_EXCEEDS_RECEIPT", "hold", f"{pl.sku}: billing {ln.quantity} (+{prior} billed before = {cum}) but only {received} received.", "three_way", 0.96, {"invoiced_cumulative": float(cum), "received": float(received)}))
        if cum > pl.quantity:
            f.append(Finding("PO_QTY_OVERRUN", "hold", f"{pl.sku}: cumulative billed quantity {cum} exceeds PO quantity {pl.quantity} (split invoices across the PO).", "three_way", 0.97, {"prior_billed": float(prior), "this_invoice": float(ln.quantity), "po_qty": float(pl.quantity)}))
        if received and cum <= received < pl.quantity:
            f.append(Finding("PARTIAL_DELIVERY", "info", f"{pl.sku}: partial delivery ({received} of {pl.quantity} received); invoice bills within what was received.", "three_way", 0.9))
        rows.append({
            "sku": pl.sku, "po_line": pl.line_no, "inv_qty": float(ln.quantity), "po_qty": float(pl.quantity),
            "received": float(received), "prior_billed": float(prior), "inv_price": float(ln.unit_price), "po_price": float(pl.unit_price),
        })
    cap = po.total * (1 + tol.po_total_pct)
    if same_ccy and used_amt + this_amt > cap and not any(x.code == "PO_QTY_OVERRUN" for x in f):
        f.append(Finding("PO_AMOUNT_OVERRUN", "hold", f"Cumulative billed {money(used_amt + this_amt)} exceeds PO value {money(po.total)} + {float(tol.po_total_pct):.0%}.", "three_way", 0.95))
    blocking = [x for x in f if x.severity != "info"]
    return StepResult("three_way", "3-way match (invoice / PO / GRN)", "fail" if blocking else ("warn" if f else "pass"),
                      f"{len(rows)} line(s) matched" + (f"; {len(blocking)} exception(s)" if blocking else ""), f,
                      {"lines": rows, "po_billed_before": money(used_amt), "this_invoice": money(this_amt), "po_total": money(po.total)}, conf)


def step_tax(ctx: Ctx) -> StepResult:
    inv, po, tol = ctx.inv, ctx.po, ctx.tol
    f: list[Finding] = []
    expected_tax = Decimal("0")
    for idx, ln in enumerate(inv.lines):
        base = ln.quantity * ln.unit_price
        expected_tax += base * ln.tax_rate
        if po and idx in ctx.line_map:
            pl = next(l for l in po.lines if l.line_no == ctx.line_map[idx])
            if ln.tax_rate != pl.tax_rate:
                f.append(Finding("TAX_RATE_MISMATCH", "hold", f"{pl.sku}: invoice charges {float(ln.tax_rate):.0%} tax but PO specifies {float(pl.tax_rate):.0%}.", "tax", 0.96, {"invoice_rate": float(ln.tax_rate), "po_rate": float(pl.tax_rate)}))
        if ln.tax_amount is not None and abs(ln.tax_amount - base * ln.tax_rate) > tol.tax_abs:
            f.append(Finding("TAX_AMOUNT_MISMATCH", "hold", f"Line {idx + 1}: tax {money(ln.tax_amount)} != {float(ln.tax_rate):.0%} of {money(base)}.", "tax", 0.95))
    gst_note = ""
    seller_gstin = (ctx.vendor.tax_id if ctx.vendor and looks_like_gstin(ctx.vendor.tax_id) else None) or (inv.vendor_tax_id if looks_like_gstin(inv.vendor_tax_id) else None)
    if inv.gst_type and seller_gstin and looks_like_gstin(ctx.buyer_gstin):
        intra = seller_gstin[:2] == ctx.buyer_gstin[:2]
        want = "CGST_SGST" if intra else "IGST"
        gst_note = f"; GST type {inv.gst_type} ({'intra' if intra else 'inter'}-state supply, state {seller_gstin[:2]} -> {ctx.buyer_gstin[:2]})"
        if inv.gst_type != want:
            f.append(Finding("GST_TYPE_MISMATCH", "hold",
                             f"Invoice charges {inv.gst_type.replace('_', '+')} but supplier (state {seller_gstin[:2]}) and buyer (state {ctx.buyer_gstin[:2]}) are "
                             f"{'in the same state, so CGST+SGST applies' if intra else 'in different states, so IGST applies'}. Wrong tax head blocks our input tax credit; ask for a corrected invoice.",
                             "tax", 0.97, {"charged": inv.gst_type, "expected": want}))
    if inv.tax_total is not None and abs(inv.tax_total - expected_tax) > tol.tax_abs * max(1, len(inv.lines)):
        f.append(Finding("TAX_AMOUNT_MISMATCH", "hold", f"Stated tax {money(inv.tax_total)} != computed {money(expected_tax)} from line rates.", "tax", 0.95))
    return StepResult("tax", "Tax validation", "fail" if f else "pass", (f"Tax {money(expected_tax)} consistent with rates" + gst_note) if not f else f"{len(f)} tax issue(s)", f, {"expected_tax": money(expected_tax), "gst_type": inv.gst_type})


def step_duplicates(ctx: Ctx) -> StepResult:
    inv, tol = ctx.inv, ctx.tol
    f: list[Finding] = []
    fp = inv.fingerprint()
    norm = normalize_invoice_number(inv.invoice_number)
    core = invoice_number_core(inv.invoice_number)
    checked = 0
    for p in ctx.md.priors:
        same_vendor = ctx.vendor is not None and p.vendor_id == ctx.vendor.id
        checked += 1
        if p.fingerprint and p.fingerprint == fp:
            f.append(Finding("DUPLICATE_INVOICE", "reject", f"Identical document already received as {p.ref} ({p.status}).", "duplicates", 0.99, {"prior": p.ref}))
            break
        if same_vendor and normalize_invoice_number(p.invoice_number) == norm:
            f.append(Finding("DUPLICATE_INVOICE", "reject", f"Vendor already submitted invoice number {p.invoice_number} ({p.ref}, {p.status}).", "duplicates", 0.98, {"prior": p.ref, "prior_status": p.status}))
            break
        if same_vendor and inv.total is not None and p.total:
            amt_close = abs(inv.total - p.total) <= p.total * tol.dup_amount_pct
            days = abs((inv.invoice_date - p.invoice_date).days) if inv.invoice_date and p.invoice_date else 999
            drift = core and core == invoice_number_core(p.invoice_number)
            if amt_close and (drift or days <= tol.dup_days):
                why = "same number core (reference drift)" if drift else f"invoice dates {days} days apart"
                f.append(Finding("POSSIBLE_DUPLICATE", "hold", f"Looks like {p.invoice_number} ({p.ref}, {p.status}): same vendor, same amount {money(p.total)}, {why}.", "duplicates", 0.92 if drift else 0.8, {"prior": p.ref, "prior_number": p.invoice_number}))
    return StepResult("duplicates", "Duplicate detection", "fail" if f else "pass", f"No duplicates among {checked} known invoices" if not f else f[0].message, f, {"fingerprint": fp[:12], "normalized_number": norm})


def step_credit_note(ctx: Ctx) -> StepResult:
    """Credit notes reduce what we owe. Never paid out; matched to the invoice they credit and applied by a human."""
    inv = ctx.inv
    f: list[Finding] = []
    ref = inv.reference_invoice
    vid = ctx.vendor.id if ctx.vendor else None
    match = None
    if ref:
        norm = normalize_invoice_number(ref)
        match = next((p for p in ctx.md.priors if p.vendor_id == vid and normalize_invoice_number(p.invoice_number) == norm), None)
    if match is None:
        f.append(Finding("CREDIT_NOTE_UNMATCHED", "hold", f"Credit note references {ref or 'no invoice'}, which is not among this vendor's known invoices. Find the original before applying it.", "credit_note", 0.9, {"reference": ref}))
    elif inv.total is not None and match.total and inv.total > match.total:
        f.append(Finding("CREDIT_EXCEEDS_INVOICE", "hold", f"Credit {money(inv.total)} is larger than the original invoice {match.invoice_number} ({money(match.total)}).", "credit_note", 0.95, {"reference": match.ref}))
    else:
        f.append(Finding("CREDIT_NOTE_TO_APPLY", "hold", f"Credit note for {inv.currency} {money(inv.total or Decimal('0'))} matches {match.invoice_number} ({match.status}). Apply it against the vendor's open payables; it is never paid out.", "credit_note", 0.9, {"reference": match.ref, "reference_status": match.status, "credit_amount": money(inv.total or Decimal('0'))}))
    return StepResult("credit_note", "Credit-note match", "warn", f[0].message, f, {"reference_invoice": ref, "matched": match.ref if match else None})


def step_policy(ctx: Ctx) -> StepResult:
    """Approval matrix: who must sign off, based on amount. Runs after the matching checks."""
    inv, st = ctx.inv, ctx.settings
    f: list[Finding] = []
    rate = st.fx_to_inr.get(inv.currency or "INR")
    amount_inr = (inv.total or Decimal("0")) * rate if rate is not None else None
    approver = "AP auto-approval"
    if amount_inr is not None and amount_inr > st.approval_limit_inr and inv.doc_type != "credit_note":
        approver = "Finance Controller"
        f.append(Finding("APPROVAL_LIMIT", "hold", f"Amount about INR {money(amount_inr):,.0f} is above the auto-approval limit of INR {money(st.approval_limit_inr):,.0f}; policy needs a Finance Controller sign-off.", "policy", 0.9,
                         {"amount_inr": money(amount_inr), "limit_inr": money(st.approval_limit_inr), "approver": approver}))
    summary = f"Approver: {approver}" + (f" (about INR {money(amount_inr):,.0f})" if amount_inr is not None else " (no FX rate for this currency)")
    return StepResult("policy", "Approval policy", "warn" if f else "pass", summary, f, {"approver": approver, "amount_inr": money(amount_inr) if amount_inr is not None else None})


def _skipped(step: str, title: str, why: str) -> Callable[[Ctx], StepResult]:
    def fn(ctx: Ctx) -> StepResult:
        return StepResult(step, title, "skip", why)
    fn.__name__ = f"skip_{step}"
    return fn


STEPS: list[Callable[[Ctx], StepResult]] = [
    step_intake, step_arithmetic, step_vendor, step_po, step_currency, step_three_way, step_tax, step_duplicates, step_policy,
]

_CN = "Not applicable to a credit note"
CREDIT_NOTE_STEPS: list[Callable[[Ctx], StepResult]] = [
    step_intake, step_arithmetic, step_vendor, step_credit_note,
    _skipped("currency", "Currency check", _CN), _skipped("three_way", "3-way match (invoice / PO / GRN)", _CN),
    step_tax, _skipped("duplicates", "Duplicate detection", "Checked inside the credit-note match"), step_policy,
]


def run_pipeline(
    inv: Invoice,
    md: MasterData,
    settings: Settings,
    on_step: Optional[Callable[[StepResult], None]] = None,
    llm: Optional[LLMAdapter] = None,
    attempt: int = 1,
) -> tuple[list[StepResult], Decision]:
    ctx = Ctx(inv, md, settings.tolerances, settings)
    llm = llm or NullLLMAdapter()
    results: list[StepResult] = []
    for fn in (CREDIT_NOTE_STEPS if inv.doc_type == "credit_note" else STEPS):
        if inv.simulate_transient_failure and attempt == 1 and fn is step_three_way:
            raise TransientStepError("ERP goods-receipt lookup timed out (simulated)")
        r = fn(ctx)
        results.append(r)
        if on_step:
            on_step(r)

    findings = [x for r in results for x in r.findings] + llm.extra_findings(inv)
    approve_conf = 1.0
    for r in results:
        approve_conf *= r.confidence
    decision = RulesDecisionEngine(settings.approve_threshold).decide(findings, approve_conf)
    decide_step = StepResult(
        "decide", "Decision (rules engine)", {"APPROVE": "pass", "HOLD_FOR_REVIEW": "warn", "REJECT": "fail"}[decision.decision],
        f"{decision.decision} ({decision.confidence:.0%})" + (f" - {decision.primary_reason}" if decision.primary_reason else ""),
        [], {**decision.to_dict(), "handoff": handoff_for(decision, inv), "info": [x.to_dict() for x in findings if x.severity == "info"]},
    )
    results.append(decide_step)
    if on_step:
        on_step(decide_step)

    text = llm.explain(inv, decision)
    decision.explanation = text or template_explanation(inv, decision)
    explain_step = StepResult(
        "explain", "Explanation",
        "pass" if text else "skip",
        decision.explanation if text else "Written from a fixed template, so the same facts always give the same words. An LLM can rephrase it if one is configured.",
        [], {"adapter": llm.name, "explanation": decision.explanation},
    )
    results.append(explain_step)
    if on_step:
        on_step(explain_step)
    return results, decision
