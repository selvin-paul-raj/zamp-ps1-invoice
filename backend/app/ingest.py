"""Turning uploaded files into invoice payloads.

* JSON: one invoice object, or a list of them.
* CSV : one row per invoice line; header columns repeat on every row and rows are grouped by
        invoice_number. Columns: invoice_number, vendor_name, vendor_tax_id, invoice_date, currency,
        po_number, bank_account, notes, line_no, sku, description, quantity, unit_price, tax_rate,
        subtotal, tax_total, total
* PDF : STUB. Text is pulled with pypdf (native PDFs only, no OCR) and header fields are found with
        regexes; line items are not extracted. The result carries extraction_confidence 0.6 so the
        pipeline always holds it for review. Replace `parse_pdf_text` with a real extractor
        (LiteParse/Docling + OCR, or an LLM with structured output) behind the same function.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

HEADER_FIELDS = ["invoice_number", "vendor_name", "vendor_tax_id", "invoice_date", "currency", "po_number", "bank_account", "notes", "subtotal", "tax_total", "total"]
LINE_FIELDS = ["line_no", "sku", "description", "quantity", "unit_price", "tax_rate", "amount", "tax_amount"]


def parse_json(data: bytes) -> list[dict[str, Any]]:
    obj = json.loads(data)
    return obj if isinstance(obj, list) else [obj]


def parse_csv(data: bytes) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    grouped: dict[str, dict[str, Any]] = {}
    for row in reader:
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        key = row.get("invoice_number") or f"row{len(grouped)}"
        inv = grouped.setdefault(key, {**{f: row.get(f) or None for f in HEADER_FIELDS}, "lines": [], "source": "csv"})
        inv["lines"].append({f: row.get(f) or None for f in LINE_FIELDS})
    return list(grouped.values())


_PATTERNS = {
    "invoice_number": r"invoice\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{2,})",
    "po_number": r"(?:p\.?o\.?|purchase\s+order)\s*(?:no\.?|number|#)?\s*[:\-]?\s*(PO-?\d{3,})",
    "invoice_date": r"(?:invoice\s+)?date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
    "total": r"(?:grand\s+)?total(?:\s+due)?\s*[:\-]?\s*(?:[A-Z]{3}|₹|\$|€)?\s*([\d,]+\.\d{2})",
    "currency": r"\b(INR|USD|EUR|GBP)\b",
    "vendor_tax_id": r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z])\b",
}


def parse_pdf_text(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"source": "pdf", "extraction_confidence": 0.6, "lines": []}
    for field, pat in _PATTERNS.items():
        m = re.search(pat, text, re.I)
        if m:
            out[field] = m.group(1).replace(",", "") if field == "total" else m.group(1)
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    out["vendor_name"] = first[:120]
    return out


def parse_pdf(data: bytes) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ValueError("PDF parsing needs `pip install pypdf`") from exc
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    if not text.strip():
        raise ValueError("No text layer found (scanned PDF). OCR is not wired in this stub.")
    return [parse_pdf_text(text)]


def parse_upload(filename: str, data: bytes) -> list[dict[str, Any]]:
    name = (filename or "").lower()
    if name.endswith(".json"):
        return parse_json(data)
    if name.endswith(".csv"):
        return parse_csv(data)
    if name.endswith(".pdf"):
        return parse_pdf(data)
    if name.endswith(".txt"):
        return [parse_pdf_text(data.decode("utf-8", "ignore"))]
    raise ValueError("Unsupported file type; use .json, .csv, .pdf (stub) or .txt")
