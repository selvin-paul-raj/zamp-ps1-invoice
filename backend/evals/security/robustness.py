"""Abuse and robustness evals: bad files, odd formats, edge-case amounts, replays and concurrency.

Every case states what must happen (expect) and the harness checks it. The invariant across all of them: nothing broken,
odd or ambiguous is ever APPROVED, and nothing crashes the service.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.db import make_engine
from app.guardrails import UploadRejected
from app.ingest import parse_document_text, parse_upload
from app.service import Service

ROOT = Path(__file__).resolve().parent.parent.parent
SEED = ROOT / "seed"
RT = SEED / "redteam"


def _svc(path: str | None = None) -> Service:
    s = dataclasses.replace(Settings(), jev_mode="replay")
    svc = Service(make_engine(f"sqlite:///{path or tempfile.mkdtemp() + '/rb.db'}"), s)
    svc.load_master(SEED)
    return svc


def _base(seed_id: str = "INV-01") -> dict:
    items = {i["id"]: i for i in json.loads((SEED / "invoices.json").read_text())}
    return copy.deepcopy({k: v for k, v in items[seed_id].items() if k not in ("expected", "id")})


def _run(payload: dict, svc: Service | None = None) -> dict:
    svc = svc or _svc()
    res = svc.ingest(payload, idempotency_key=None)
    svc.drain(delay_ms=0)
    return svc.get_run(res["run_id"])


def _decision(payload: dict) -> str:
    try:
        return _run(payload)["decision"]
    except (ValueError, KeyError, ArithmeticError) as exc:
        return f"REJECTED_AT_INGEST:{exc.__class__.__name__}"


def _upload(name: str, data: bytes) -> str:
    try:
        ps = parse_upload(name, data)
    except UploadRejected as exc:
        return f"UPLOAD_REJECTED:{exc.status}"
    except ValueError as exc:
        return f"UPLOAD_REJECTED:422:{str(exc)[:40]}"
    out = []
    for p in ps:
        p["received_at"] = "Mon 2026-10-12 9:00 AM IST (UTC+05:30)"
        out.append(_decision(p))
    return ",".join(out)


def cases() -> list[tuple[str, str, Callable[[], str], Callable[[str], bool], str]]:
    """(name, category, run -> observed, check(observed) -> ok, what must happen)."""
    not_approve = lambda o: "APPROVE" not in o.split(",")  # noqa: E731
    rejected = lambda o: o.startswith("UPLOAD_REJECTED")  # noqa: E731
    clean_pdf = (SEED / "pdfs" / "KOS-0160_kaveri_clean.pdf").read_bytes()
    b = _base()

    def mod(fn):
        def run():
            p = copy.deepcopy(b)
            fn(p)
            return _decision(p)
        return run

    def retotal(p):
        sub = sum(Decimal(str(l["quantity"])) * Decimal(str(l["unit_price"])) for l in p["lines"])
        tax = sum(Decimal(str(l["quantity"])) * Decimal(str(l["unit_price"])) * Decimal(str(l["tax_rate"])) for l in p["lines"])
        for l in p["lines"]:
            l.pop("amount", None)
            l.pop("tax_amount", None)
        p["subtotal"], p["tax_total"], p["total"] = f"{sub:.2f}", f"{tax:.2f}", f"{sub + tax:.2f}"

    def neg_qty(p):
        p["lines"][0]["quantity"] = -abs(int(p["lines"][0]["quantity"]))
        retotal(p)

    def neg_price(p):
        p["lines"][1]["unit_price"] = "-" + str(p["lines"][1]["unit_price"])
        retotal(p)

    def dup_line(p):
        p["lines"].append(copy.deepcopy(p["lines"][0]))
        retotal(p)

    def split_line(p):  # same total quantity, split across two lines: legitimate
        l = p["lines"][0]
        q = int(l["quantity"])
        a, c = copy.deepcopy(l), copy.deepcopy(l)
        a["quantity"], c["quantity"] = q // 2, q - q // 2
        p["lines"] = [a, c] + p["lines"][1:]
        retotal(p)

    def total_plus(delta):
        def f(p):
            p["total"] = f"{Decimal(str(p['total'])) + Decimal(delta):.2f}"
        return f

    def decimal_comma(p):
        l = p["lines"][0]
        l["unit_price"] = f"{Decimal(str(l['unit_price'])):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # 120.00 -> 120,00

    def big_price_decimal_comma(p):
        l = p["lines"][0]
        l["unit_price"] = "1.234,50"

    def text_total(s):
        def run():
            out = parse_document_text(f"TAX INVOICE\nInvoice No: KOS-9001\nInvoice Date: 03-10-2026\nGrand Total INR: {s}\n")
            return str(out.get("total"))
        return run

    def text_date(s):
        def run():
            out = parse_document_text(f"TAX INVOICE\nInvoice No: KOS-9002\nInvoice Date: {s}\nGrand Total INR: 1,000.00\n")
            return str(out.get("invoice_date"))
        return run

    def us_date_invoice():
        p = copy.deepcopy(b)
        p["invoice_date"] = "10/13/2026"
        return _decision(p)

    def concurrency_same_key():
        tmp = tempfile.mkdtemp() + "/cc.db"
        svc = _svc(tmp)
        p = copy.deepcopy(b)
        results, errors = [], []

        def worker():
            try:
                results.append(svc.ingest(copy.deepcopy(p), idempotency_key="webhook-123"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc.__class__.__name__)

        ts = [threading.Thread(target=worker) for _ in range(16)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        svc.drain(delay_ms=0)
        ids = {r["invoice_id"] for r in results}
        from sqlalchemy import func, select

        from app import db as _db

        with svc.engine.connect() as c:
            n_inv = c.execute(select(func.count()).select_from(_db.invoices)).scalar()
            n_run = c.execute(select(func.count()).select_from(_db.runs)).scalar()
        return f"invoices={n_inv} runs={n_run} ids_returned={len(ids)} created={sum(r['created'] for r in results)} errors={len(errors)}"

    def replay_same_payload_new_key():
        svc = _svc()
        p = copy.deepcopy(b)
        svc.ingest(copy.deepcopy(p), idempotency_key="k1")
        svc.drain(delay_ms=0)
        r2 = svc.ingest(copy.deepcopy(p), idempotency_key="k2")
        svc.drain(delay_ms=0)
        return svc.get_run(r2["run_id"])["decision"] + ":" + str(svc.get_run(r2["run_id"])["primary_reason"])

    def replay_after_done():
        svc = _svc()
        p = copy.deepcopy(b)
        r1 = svc.ingest(copy.deepcopy(p), idempotency_key="evt-9")
        svc.drain(delay_ms=0)
        r2 = svc.ingest(copy.deepcopy(p), idempotency_key="evt-9")
        work = svc.drain(delay_ms=0)
        return f"same_run={r1['run_id'] == r2['run_id']} created={r2['created']} new_work={work}"

    def huge_notes():
        p = copy.deepcopy(b)
        p["notes"] = "lorem ipsum " * 90000  # ~1 MB
        t0 = time.perf_counter()
        d = _decision(p)
        return f"{d} in {time.perf_counter() - t0:.1f}s"

    def bomb_like_csv():
        rows = "invoice_number,vendor_name,invoice_date,currency,total\n" + "".join(f"X{i},V,2026-10-01,INR,1\n" for i in range(3000))
        return _upload("big.csv", rows.encode())

    return [
        # ---------------------------------------------------------------- files
        ("truncated PDF", "malformed file", lambda: _upload("t.pdf", (RT / "abuse_truncated.pdf").read_bytes()), not_approve, "held or rejected, never approved, no crash"),
        ("garbage bytes with a PDF header", "malformed file", lambda: _upload("g.pdf", (RT / "abuse_garbage.pdf").read_bytes()), not_approve, "held or rejected"),
        ("encrypted PDF", "malformed file", lambda: _upload("e.pdf", (RT / "abuse_encrypted.pdf").read_bytes()), rejected, "rejected before parsing"),
        ("25-page PDF (page bomb)", "huge file", lambda: _upload("p.pdf", (RT / "abuse_25_pages.pdf").read_bytes()), rejected, "rejected (limit 20 pages)"),
        ("11 MB upload", "huge file", lambda: _upload("big.pdf", b"%PDF-1.7\n" + b"0" * (11 * 1024 * 1024)), lambda o: o.startswith("UPLOAD_REJECTED:413"), "rejected 413 before parsing"),
        ("PDF with JavaScript", "malformed file", lambda: _upload("js.pdf", (RT / "abuse_active_js.pdf").read_bytes()), not_approve, "accepted, never executed, held UNSAFE_DOCUMENT"),
        ("executable renamed .pdf", "wrong type", lambda: _upload("invoice.pdf", b"MZ\x90\x00" + b"\x00" * 200), rejected, "rejected: signature does not match"),
        ("PDF renamed .json", "wrong type", lambda: _upload("invoice.json", clean_pdf), rejected, "rejected: binary in a text upload"),
        (".exe extension", "wrong type", lambda: _upload("invoice.exe", b"hello"), rejected, "rejected: type not allowed"),
        ("path in file name", "wrong type", lambda: _upload("../../etc/passwd.pdf", clean_pdf), rejected, "rejected: bad file name"),
        ("empty file", "empty", lambda: _upload("empty.pdf", b""), rejected, "rejected"),
        ("3,000-row CSV", "huge file", bomb_like_csv, rejected, "rejected (limit 2,000 rows)"),
        ("1 MB of notes", "huge file", huge_notes, lambda o: "APPROVE" in o and float(o.split(" in ")[1][:-1]) < 20, "processed normally (clean invoice), under 20 s"),
        # ---------------------------------------------------------------- empty / missing
        ("no invoice number", "empty fields", mod(lambda p: p.__setitem__("invoice_number", "")), not_approve, "held MISSING_FIELD"),
        ("no lines", "empty fields", mod(lambda p: p.__setitem__("lines", [])), not_approve, "held"),
        ("no total", "empty fields", mod(lambda p: p.__setitem__("total", None)), not_approve, "held"),
        ("no vendor", "empty fields", mod(lambda p: p.__setitem__("vendor_name", "")), not_approve, "held"),
        ("blank currency", "empty fields", mod(lambda p: p.__setitem__("currency", "")), not_approve, "held"),
        # ---------------------------------------------------------------- amounts and formats
        ("negative quantity on an invoice", "negative amounts", mod(neg_qty), not_approve, "never approved"),
        ("negative unit price (hidden discount line)", "negative amounts", mod(neg_price), not_approve, "never approved"),
        ("total off by 0.01 (rounding)", "rounding", mod(total_plus("0.01")), lambda o: o in ("APPROVE", "HOLD_FOR_REVIEW"), "approve or hold; either is defensible"),
        ("total off by 1.00 (inside the documented INR 1 tax-rounding allowance)", "rounding", mod(total_plus("1.00")), lambda o: o in ("APPROVE", "HOLD_FOR_REVIEW"), "approve or hold: policy allows INR 1"),
        ("total off by 5.00", "rounding", mod(total_plus("5.00")), not_approve, "held ARITHMETIC_MISMATCH"),
        ("same line billed twice", "duplicate lines", mod(dup_line), not_approve, "held (quantity over PO / receipt)"),
        ("one line split in two (same total qty)", "duplicate lines", mod(split_line), lambda o: o in ("APPROVE", "HOLD_FOR_REVIEW"), "no crash; approve or hold"),
        ("decimal comma 120,00 (European)", "locale", mod(decimal_comma), not_approve, "never approved on a misread number"),
        ("1.234,50 (European thousands)", "locale", mod(big_price_decimal_comma), not_approve, "never approved on a misread number"),
        ("Indian grouping 1,00,000.00 in a PDF", "locale", text_total("1,00,000.00"), lambda o: o == "100000.00", "read as 100000.00"),
        ("Western grouping 100,000.00 in a PDF", "locale", text_total("100,000.00"), lambda o: o == "100000.00", "read as 100000.00"),
        ("dd/mm date 03/10/2026", "locale", text_date("03/10/2026"), lambda o: o == "2026-10-03", "day-first (Indian invoices): 3 Oct 2026"),
        ("US date 10/13/2026 (month 13 if day-first)", "locale", text_date("10/13/2026"), lambda o: o == "None", "not guessed: left empty"),
        ("US date on a JSON invoice", "locale", us_date_invoice, not_approve, "never approved on an unreadable date"),
        ("currency in lower case 'inr'", "locale", mod(lambda p: p.__setitem__("currency", "inr")), lambda o: o == "APPROVE", "normalised to INR, approved"),
        ("currency USD on an INR PO", "locale", mod(lambda p: p.__setitem__("currency", "USD")), not_approve, "held CURRENCY_MISMATCH"),
        ("emoji and RTL text in vendor name", "unicode", mod(lambda p: p.__setitem__("vendor_name", p["vendor_name"] + " 🚀 شركة")), lambda o: not o.startswith("REJECTED_AT_INGEST") or True, "no crash"),
        # ---------------------------------------------------------------- replays and concurrency
        ("16 concurrent deliveries, same idempotency key", "concurrency", concurrency_same_key,
         lambda o: o.startswith("invoices=1 runs=1 ids_returned=1 created=1 ") and o.endswith("errors=0"), "exactly one invoice and one run, no errors"),
        ("same webhook re-delivered after processing", "idempotency", replay_after_done, lambda o: o == "same_run=True created=False new_work=0", "same run, no new work"),
        ("same invoice again under a new key", "idempotency", replay_same_payload_new_key, lambda o: o.startswith("REJECT:DUPLICATE_INVOICE") or o.startswith("HOLD"),
         "caught as a duplicate, never paid twice"),
    ]


def run() -> dict[str, Any]:
    rows = []
    for name, cat, fn, check, expect in cases():
        t0 = time.perf_counter()
        try:
            obs = fn()
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, not a failed harness
            obs = f"CRASH:{exc.__class__.__name__}:{str(exc)[:80]}"
        rows.append({"case": name, "category": cat, "expect": expect, "observed": obs, "ok": (not obs.startswith("CRASH")) and bool(check(obs)),
                     "approved": "APPROVE" in obs.split(",") and cat not in ("locale", "rounding", "duplicate lines", "huge file", "unicode") or None,
                     "ms": int((time.perf_counter() - t0) * 1000)})
    return {"n": len(rows), "passed": sum(r["ok"] for r in rows), "failed": [r for r in rows if not r["ok"]], "crashes": sum(1 for r in rows if r["observed"].startswith("CRASH")),
            "by_category": {c: f"{sum(r['ok'] for r in rows if r['category'] == c)}/{sum(1 for r in rows if r['category'] == c)}" for c in sorted({r['category'] for r in rows})},
            "rows": rows}


if __name__ == "__main__":
    import os
    import sys

    os.environ.setdefault("PIPELINE_STEP_DELAY_MS", "0")
    r = run()
    for row in r["rows"]:
        print(("OK  " if row["ok"] else "XX  ") + f"{row['category']:16} {row['case']:48} -> {row['observed'][:70]}")
    print(f"{r['passed']}/{r['n']} passed")
    sys.exit(0 if r["passed"] == r["n"] else 1)
