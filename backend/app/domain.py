"""Pure domain types used by the matching pipeline (no DB, no HTTP)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

SEVERITY_ORDER = {"info": 0, "hold": 1, "reject": 2}


def D(x: Any) -> Decimal:
    if x is None or x == "":
        return Decimal("0")
    try:
        return Decimal(str(x).replace(",", "").strip())
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"not a number: {x!r}") from exc


def money(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.01")))


def parse_date(x: Any) -> Optional[date]:
    if not x:
        return None
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def normalize_invoice_number(num: str) -> str:
    """'INV-0005521' -> '5521'; 'ACM/2301' -> 'ACM2301'. Used for exact-after-normalisation duplicates."""
    s = re.sub(r"[^A-Z0-9]", "", (num or "").upper())
    s = re.sub(r"^(INVOICE|INV|BILL|TAX)", "", s)
    return s.lstrip("0") or s


def invoice_number_core(num: str) -> str:
    """Longest digit run, leading zeros stripped: catches reference drift ('BL-5521' vs '5521-OPS')."""
    runs = re.findall(r"\d+", num or "")
    if not runs:
        return ""
    return max(runs, key=len).lstrip("0")


GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def looks_like_gstin(tax_id: Optional[str]) -> bool:
    """Indian GST registration number shape: 2-digit state + PAN (10) + entity no. + 'Z' + check char."""
    return bool(tax_id) and bool(GSTIN_RE.match(re.sub(r"\s", "", tax_id.upper())))


def gstin_check_char(first14: str) -> str:
    """GSTIN check character (mod-36 Luhn-style checksum over the first 14 characters)."""
    total = 0
    for i, ch in enumerate(first14.upper()):
        prod = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += prod // 36 + prod % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def gstin_valid(tax_id: Optional[str]) -> bool:
    if not looks_like_gstin(tax_id):
        return False
    t = re.sub(r"\s", "", tax_id.upper())
    return gstin_check_char(t[:14]) == t[14]


def mask_account(acct: Optional[str]) -> str:
    if not acct:
        return "-"
    a = re.sub(r"\s", "", acct)
    return "•" * max(0, len(a) - 4) + a[-4:]


def _norm_gst_type(x: Any) -> Optional[str]:
    if not x:
        return None
    t = re.sub(r"[^A-Z]", "", str(x).upper())
    if t == "IGST":
        return "IGST"
    if t in ("CGSTSGST", "SGSTCGST", "CGSTUTGST", "CGST", "SGST"):
        return "CGST_SGST"
    return None


@dataclass
class InvoiceLine:
    line_no: Optional[int]
    sku: Optional[str]
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal  # fraction, e.g. 0.18
    amount: Optional[Decimal] = None  # stated line amount (pre-tax), if the invoice shows one
    tax_amount: Optional[Decimal] = None


@dataclass
class Invoice:
    invoice_number: str
    vendor_name: str
    invoice_date: Optional[date]
    currency: str
    lines: list[InvoiceLine]
    subtotal: Optional[Decimal]
    tax_total: Optional[Decimal]
    total: Optional[Decimal]
    po_number: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    bank_account: Optional[str] = None
    notes: str = ""
    source: str = "api"
    received_at: Optional[str] = None
    extraction_confidence: float = 1.0
    simulate_transient_failure: bool = False
    doc_type: str = "invoice"  # invoice | credit_note
    reference_invoice: Optional[str] = None  # for credit notes: the invoice being credited
    gst_type: Optional[str] = None  # IGST (inter-state) | CGST_SGST (intra-state) | None (not stated / non-GST)
    extraction: dict[str, Any] = field(default_factory=dict)  # per-field confidence etc. from document parsing
    upload_meta: dict[str, Any] = field(default_factory=dict)  # upload facts: file name, PDF metadata, guardrail flags

    @staticmethod
    def from_payload(p: dict[str, Any]) -> "Invoice":
        lines = []
        for i, ln in enumerate(p.get("lines") or [], start=1):
            rate = ln.get("tax_rate", 0)
            rate_d = D(rate)
            if rate_d > 1:  # accept 18 as well as 0.18
                rate_d = rate_d / Decimal(100)
            lines.append(
                InvoiceLine(
                    line_no=int(ln["line_no"]) if ln.get("line_no") not in (None, "") else None,
                    sku=(ln.get("sku") or None),
                    description=ln.get("description", ""),
                    quantity=D(ln.get("quantity")),
                    unit_price=D(ln.get("unit_price")),
                    tax_rate=rate_d,
                    amount=D(ln["amount"]) if ln.get("amount") not in (None, "") else None,
                    tax_amount=D(ln["tax_amount"]) if ln.get("tax_amount") not in (None, "") else None,
                )
            )
        opt = lambda k: D(p[k]) if p.get(k) not in (None, "") else None  # noqa: E731
        return Invoice(
            invoice_number=str(p.get("invoice_number") or "").strip(),
            vendor_name=str(p.get("vendor_name") or "").strip(),
            invoice_date=parse_date(p.get("invoice_date")),
            currency=str(p.get("currency") or "").upper().strip(),
            lines=lines,
            subtotal=opt("subtotal"),
            tax_total=opt("tax_total"),
            total=opt("total"),
            po_number=(str(p["po_number"]).strip() or None) if p.get("po_number") else None,
            vendor_tax_id=p.get("vendor_tax_id") or None,
            bank_account=p.get("bank_account") or None,
            notes=p.get("notes", "") or "",
            source=p.get("source", "api") or "api",
            received_at=p.get("received_at"),
            extraction_confidence=float(p.get("extraction_confidence", 1.0)),
            simulate_transient_failure=bool(p.get("_simulate_transient_failure", False)),
            doc_type=(str(p.get("doc_type") or "invoice").lower().replace(" ", "_").replace("-", "_")),
            reference_invoice=(str(p["reference_invoice"]).strip() or None) if p.get("reference_invoice") else None,
            gst_type=_norm_gst_type(p.get("gst_type")),
            extraction=p.get("extraction") or {},
            upload_meta=p.get("upload_meta") or {},
        )

    def fingerprint(self) -> str:
        """Content fingerprint independent of channel / received time (used for duplicate detection)."""
        body = {
            "v": self.vendor_name.lower(),
            "n": normalize_invoice_number(self.invoice_number),
            "d": str(self.invoice_date),
            "t": str(self.total),
            "l": [[l.sku, str(l.quantity), str(l.unit_price)] for l in self.lines],
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass
class Vendor:
    id: str
    name: str
    tax_id: Optional[str]
    currency: str
    bank_account: Optional[str]
    status: str = "active"  # active | blocked
    aliases: list[str] = field(default_factory=list)


@dataclass
class POLine:
    line_no: int
    sku: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal


@dataclass
class PurchaseOrder:
    po_number: str
    vendor_id: str
    currency: str
    status: str  # open | closed | cancelled
    po_date: Optional[date]
    lines: list[POLine]
    updated_at: Optional[date] = None  # last change in the PO register (freshness / conflict check)
    revision: int = 1

    @property
    def total(self) -> Decimal:
        return sum((l.quantity * l.unit_price for l in self.lines), Decimal("0"))


@dataclass
class PriorInvoice:
    """An invoice already known to AP: paid history, or an earlier invoice in this system."""

    ref: str
    vendor_id: Optional[str]
    invoice_number: str
    po_number: Optional[str]
    invoice_date: Optional[date]
    total: Decimal
    status: str  # paid | approved | on_hold | rejected
    fingerprint: Optional[str] = None
    lines: list[dict[str, Any]] = field(default_factory=list)  # {sku, line_no, quantity, amount}


@dataclass
class Contract:
    """Rate contract / price list line: the price procurement agreed with the vendor for a SKU."""

    contract_id: str
    vendor_id: str
    sku: str
    unit_price: Decimal
    currency: str
    valid_from: Optional[date]
    valid_to: Optional[date]


@dataclass
class GstRecord:
    """Snapshot row from the GST registry (in production: a GSTN lookup through a GSP)."""

    gstin: str
    legal_name: str
    status: str  # Active | Cancelled | Suspended
    state_code: str
    status_date: Optional[date] = None


@dataclass
class BankChange:
    """Vendor master change log: when the bank account on file changed and whether it was verified by call-back."""

    vendor_id: str
    new_account_last4: str
    changed_at: Optional[date]
    changed_by: str
    callback_verified: bool


@dataclass
class Payment:
    """Payment history from the bank: what we actually paid, and to which account."""

    payment_id: str
    vendor_id: str
    account_last4: str
    paid_on: Optional[date]
    amount: Decimal
    invoice_ref: str


@dataclass
class MasterData:
    vendors: dict[str, Vendor]
    pos: dict[str, PurchaseOrder]
    received: dict[tuple[str, int], Decimal]  # (po_number, line_no) -> qty received across GRNs
    grn_refs: dict[str, list[str]]  # po_number -> grn ids
    priors: list[PriorInvoice]
    contracts: list[Contract] = field(default_factory=list)
    gst_registry: dict[str, GstRecord] = field(default_factory=dict)
    bank_changes: list[BankChange] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    sources: dict[str, dict] = field(default_factory=dict)  # system id -> {name, as_of, owner}


@dataclass
class Finding:
    code: str
    severity: str  # info | hold | reject
    message: str
    step: str
    certainty: float = 0.95  # how sure we are this finding is real
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepResult:
    step: str
    title: str
    status: str  # pass | warn | fail | skip
    summary: str
    findings: list[Finding] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0  # multiplicative factor on approve-confidence
    evidence: list[dict[str, Any]] = field(default_factory=list)  # one record per check (see app.evidence)
    model_calls: list[dict[str, Any]] = field(default_factory=list)  # Jev / LLM calls made in this step

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        return d


@dataclass
class Decision:
    decision: str  # APPROVE | HOLD_FOR_REVIEW | REJECT
    confidence: float
    reasons: list[Finding]
    primary_reason: Optional[str]
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": round(self.confidence, 3),
            "primary_reason": self.primary_reason,
            "reasons": [r.to_dict() for r in self.reasons],
            "explanation": self.explanation,
        }
