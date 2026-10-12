"""Sources of truth and evidence records.

Every check in the pipeline writes one evidence record: which system of record it read, which record
and field, what that source says (expected) versus what the invoice says (actual), which rule compared
them, when the source snapshot was taken, and where on the document the invoice value was read.

The decision cites these records, they are stored with the run (audit trail), returned by the API
(`GET /runs/{id}` -> `evidence`, `GET /runs/{id}/evidence`) and shown in the UI's Evidence panel.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# The systems of record this process reads. In production each is an ERP / bank / GSTN integration;
# here each is a seeded table with an "as of" snapshot time (seed/sources.json).
SOURCE_SYSTEMS: dict[str, str] = {
    "invoice_doc": "Invoice document (as received)",
    "vendor_master": "Vendor master (ERP)",
    "po_register": "Purchase order register (ERP)",
    "grn": "Goods receipts (warehouse / ERP)",
    "contracts": "Rate contracts / price list (procurement)",
    "gst_registry": "GST registry (GSTN snapshot)",
    "bank_change_log": "Vendor bank-change log (vendor master audit)",
    "payment_history": "Payment history (bank)",
    "ap_ledger": "Prior invoices (AP ledger + this system)",
    "policy": "AP policy (approval matrix, tolerances)",
    "jev": "Jev model (TypeSafe), advisory only",
}

RESULTS = ("match", "mismatch", "missing", "info")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Evidence:
    rule_id: str  # the check that produced it, e.g. R-3WAY-PRICE
    source_system: str  # key of SOURCE_SYSTEMS
    record_id: Optional[str]  # e.g. PO-1001 line 2, V001, GRN-5001
    field: str  # e.g. unit_price
    expected: Any  # what the system of record says
    actual: Any  # what the invoice says
    result: str  # match | mismatch | missing | info
    step: str = ""
    source_as_of: Optional[str] = None  # snapshot time of the source
    doc_ref: Optional[str] = None  # where on the document the invoice value was read (page / line / field)
    note: str = ""
    finding_code: Optional[str] = None  # the finding this evidence supports, if any
    read_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_name"] = SOURCE_SYSTEMS.get(self.source_system, self.source_system)
        for k in ("expected", "actual"):
            v = d[k]
            if v is not None and not isinstance(v, (str, int, float, bool, list, dict)):
                d[k] = str(v)
        return d


def doc_ref_for(extraction: dict[str, Any] | None, field_name: str, source: str) -> str:
    """Where the invoice value came from: a PDF page/line (text or OCR) or a structured payload field."""
    ex = extraction or {}
    reg = (ex.get("regions") or {}).get(field_name)
    if reg:
        how = {"ocr": "OCR", "text": "PDF text", "llm": "LLM read"}.get(ex.get("method"), ex.get("method") or "document")
        return f"{how}, page {reg.get('page', 1)}, line {reg.get('line')}: \"{(reg.get('text') or '')[:60]}\""
    if ex.get("method"):
        return f"{ex.get('method')} extraction, field {field_name}"
    return f"{source} payload, field {field_name}"
