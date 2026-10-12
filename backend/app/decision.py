"""Pluggable decision layer.

The final APPROVE / HOLD_FOR_REVIEW / REJECT is always taken by deterministic code
(`RulesDecisionEngine`). Models may only *add evidence* (findings) or *write prose*
(explanations); they never take the decision. That keeps the decision auditable and
immune to prompt injection inside invoice text ("pre-approved, pay immediately").

`LLMAdapter` is an interface left unconfigured. To plug in a model (an OpenAI-compatible
endpoint, Claude, Gemini, or a typed decision model), implement `explain()` and/or
`extra_findings()` and register it in `get_llm_adapter()`. No third-party API or key is
required to run this project.
"""
from __future__ import annotations

from typing import Optional, Protocol

from .domain import SEVERITY_ORDER, Decision, Finding, Invoice

APPROVE = "APPROVE"
HOLD = "HOLD_FOR_REVIEW"
REJECT = "REJECT"
ROUTING_CODES = {"APPROVAL_LIMIT"}  # policy routing, not a defect in the invoice


class DecisionEngine(Protocol):
    def decide(self, findings: list[Finding], approve_confidence: float) -> Decision: ...


class RulesDecisionEngine:
    """Most severe finding wins: REJECT > HOLD > APPROVE. Approve only above a confidence gate."""

    def __init__(self, approve_threshold: float = 0.85):
        self.approve_threshold = approve_threshold

    def decide(self, findings: list[Finding], approve_confidence: float) -> Decision:
        blocking = [f for f in findings if f.severity in ("hold", "reject")]
        if blocking:
            top_sev = max(SEVERITY_ORDER[f.severity] for f in blocking)
            top = [f for f in blocking if SEVERITY_ORDER[f.severity] == top_sev]
            # Policy routing (who signs off) ranks below real exceptions, so a possible duplicate is never hidden behind it.
            top.sort(key=lambda f: (f.code in ROUTING_CODES, -f.certainty))
            decision = REJECT if top_sev == SEVERITY_ORDER["reject"] else HOLD
            # Confidence in a non-approve decision = how sure we are of the strongest blocking finding.
            conf = top[0].certainty
            reasons = sorted(blocking, key=lambda f: (-SEVERITY_ORDER[f.severity], f.code in ROUTING_CODES, -f.certainty))
            return Decision(decision, conf, reasons, top[0].code)

        if approve_confidence < self.approve_threshold:
            low = Finding(
                code="LOW_CONFIDENCE",
                severity="hold",
                message=(
                    f"All checks passed but combined confidence {approve_confidence:.2f} is below the "
                    f"auto-approve gate {self.approve_threshold:.2f}."
                ),
                step="decide",
                certainty=round(1 - approve_confidence, 3),
            )
            return Decision(HOLD, 1 - approve_confidence, [low], "LOW_CONFIDENCE")

        return Decision(APPROVE, approve_confidence, [], None)


class LLMAdapter(Protocol):
    name: str

    def explain(self, invoice: Invoice, decision: Decision) -> Optional[str]: ...

    def extra_findings(self, invoice: Invoice) -> list[Finding]: ...


class NullLLMAdapter:
    """Default: no model configured. Explanations come from the deterministic template."""

    name = "none"

    def explain(self, invoice: Invoice, decision: Decision) -> Optional[str]:
        return None

    def extra_findings(self, invoice: Invoice) -> list[Finding]:
        return []


def get_llm_adapter(provider: str) -> LLMAdapter:
    # Register real adapters here, e.g. {"openai": OpenAIAdapter, ...}. Nothing is wired by default.
    return NullLLMAdapter()


def template_explanation(invoice: Invoice, decision: Decision) -> str:
    head = {
        APPROVE: "Approved for payment",
        HOLD: "Held for human review",
        REJECT: "Rejected",
    }[decision.decision]
    who = f"{invoice.vendor_name} invoice {invoice.invoice_number}"
    if any(r.code.startswith("CREDIT") for r in decision.reasons):
        parts = "; ".join(f"[{r.code}] {r.message}" for r in decision.reasons[:3])
        return f"Credit note from {invoice.vendor_name} ({invoice.invoice_number}): not paid out. {parts}"
    if decision.decision == APPROVE:
        return (
            f"{head}: {who} matched its PO and goods receipt within tolerance on price, quantity, "
            f"currency and tax, and no duplicate was found (confidence {decision.confidence:.0%})."
        )
    parts = "; ".join(f"[{r.code}] {r.message}" for r in decision.reasons[:4])
    return f"{head}: {who}. {parts}"


# --------------------------------------------------------------------------- human handoff
# Every HOLD reaches a person "pre-investigated": the evidence is already on the run page, plus ONE specific
# question, who should answer it, the next action, and (where the vendor must fix something) a draft email.
# Deterministic templates: no model writes these, so they are reproducible and safe to show an auditor.
_HANDOFF: dict[str, tuple[str, str, str, bool]] = {
    # code: (question, owner, next action, vendor must act?)
    "PRICE_VARIANCE": ("Did procurement agree the higher unit price, or should the vendor re-bill at the PO price?", "Buyer / procurement", "Confirm the price change in writing, or send the vendor the draft below asking for a corrected invoice.", True),
    "QTY_EXCEEDS_RECEIPT": ("Have the extra goods actually arrived (GRN not yet posted), or is the vendor billing ahead of delivery?", "Warehouse / receiving", "Ask receiving to post the GRN if goods arrived; otherwise ask the vendor to bill only what was received.", True),
    "PO_QTY_OVERRUN": ("Should the PO be amended to cover the extra quantity, or is this invoice over-billing?", "Buyer / procurement", "Amend the PO (with approval) or reject the excess quantity.", True),
    "PO_AMOUNT_OVERRUN": ("Should the PO value be increased, or is this invoice over-billing?", "Buyer / procurement", "Amend the PO value or reject the excess.", True),
    "DUPLICATE_INVOICE": ("Is this a resend of an invoice we already have?", "AP", "Reject and tell the vendor which earlier invoice it duplicates.", True),
    "POSSIBLE_DUPLICATE": ("Is this a genuinely new charge, or the earlier invoice sent again under a new number?", "AP", "Compare it with the earlier invoice on the run page; reject if it is the same service period.", False),
    "BANK_DETAILS_CHANGED": ("Did the vendor really change bank accounts (verify by phone with the contact already on file)?", "Vendor master team", "Do NOT reply to the invoice email or use any number on it. Call the known contact; update the vendor master only after verification.", False),
    "CURRENCY_MISMATCH": ("Which currency was agreed on the PO, and at what rate?", "Buyer / procurement", "Ask the vendor to re-issue in the PO currency, or record an agreed FX rate.", True),
    "PO_INFERRED": ("Is this invoice for the inferred PO shown on the run page?", "Requester", "Confirm the PO with the requester, then approve with a note.", False),
    "PO_MISSING": ("Which PO does this invoice belong to?", "Requester", "Ask the requester for the PO number; ask the vendor to quote it on future invoices.", True),
    "PO_NOT_FOUND": ("The PO number on the invoice does not exist. Is it a typo, or was no PO raised?", "Buyer / procurement", "Ask the vendor for the correct PO number.", True),
    "TAX_RATE_MISMATCH": ("Is the tax rate on the invoice correct for these goods, or is the PO right?", "Tax team", "Ask the vendor for a corrected invoice at the PO tax rate, or fix the PO.", True),
    "TAX_AMOUNT_MISMATCH": ("The tax amount does not match the tax rate. Which one is right?", "Tax team", "Ask the vendor for a corrected invoice.", True),
    "GST_TYPE_MISMATCH": ("The wrong GST head was charged. Can the vendor issue a corrected tax invoice?", "Tax team", "Do not book input tax credit on this invoice; request a corrected one.", True),
    "INVALID_GSTIN": ("The vendor's GSTIN fails the checksum. Is it a typo or an unregistered vendor?", "Vendor master team", "Verify the GSTIN on the GST portal before paying.", True),
    "ARITHMETIC_MISMATCH": ("The totals do not add up. Which figure is right?", "AP", "Ask the vendor for a corrected invoice.", True),
    "MISSING_FIELD": ("A required field is missing. Can it be confirmed from the document?", "AP", "Fill it from the PDF or ask the vendor.", True),
    "LOW_EXTRACTION_CONFIDENCE": ("The fields were read from a scanned or unusual document. Are they right?", "AP", "Check the extracted fields against the PDF, then approve with a note.", False),
    "LINE_NOT_ON_PO": ("This invoice line is not on the PO. Was it ordered?", "Requester", "Confirm with the requester or reject the line.", True),
    "GRN_MISSING": ("No goods receipt exists yet. Have the goods or services been delivered?", "Warehouse / receiving", "Ask receiving to post the GRN.", False),
    "VENDOR_AMBIGUOUS": ("Which vendor in the master is this?", "Vendor master team", "Pick the vendor or start onboarding.", False),
    "APPROVAL_LIMIT": ("Everything matched. Do you approve payment of this amount?", "Finance Controller", "Review the evidence and approve or reject.", False),
    "CREDIT_NOTE_TO_APPLY": ("Apply this credit against the vendor's open payables?", "AP", "Apply the credit to the next payment run.", False),
    "CREDIT_NOTE_UNMATCHED": ("Which invoice does this credit note relate to?", "AP", "Ask the vendor for the original invoice reference.", True),
    "CREDIT_EXCEEDS_INVOICE": ("The credit is bigger than the invoice. Is the amount right?", "AP", "Ask the vendor to confirm the credit amount.", True),
    "UNSUPPORTED_DOC_TYPE": ("This is not an invoice. Wait for the final tax invoice?", "AP", "Do not pay on a quote or proforma.", True),
    "LOW_CONFIDENCE": ("All checks passed, but on fuzzy matches. Are the vendor and PO right?", "AP", "Check the highlighted matches, then approve with a note.", False),
    "CONTRACT_PRICE_CONFLICT": ("The PO price is above the rate contract. Which is right: the contract or the PO?", "Buyer / procurement", "If the contract is right, amend the PO and ask the vendor to re-bill at the contract price; if the PO is right, update the contract with approval.", False),
    "GSTIN_NOT_ACTIVE": ("The GST registry shows this registration is not active. Has the vendor re-registered, or should we stop paying GST to them?", "Tax team", "Check the GSTIN on the GST portal; ask the vendor for a valid registration and a fresh invoice. Do not claim input tax credit on this one.", True),
    "VENDOR_BANK_RECENTLY_CHANGED": ("The vendor's bank account on file changed a few days ago and was never verified. Did the vendor really change it?", "Vendor master team", "Call the contact already on file (not any number on the invoice or the change request) and verify; until then, do not pay.", False),
    "PO_CHANGED_AFTER_INVOICE": ("The PO was edited after the invoice date. Was the change legitimate and approved?", "Buyer / procurement", "Open the PO change history, confirm who changed it and why, then approve with a note or reject.", False),
    "SUSPICIOUS_INSTRUCTIONS": ("The document contains text that tries to instruct the system. Is this a genuine invoice?", "AP", "Treat the text as a red flag; verify the invoice with the vendor through known contacts before paying.", False),
    "NEGATIVE_AMOUNT": ("The bill has a negative line or total. Is this really a credit, and if so, against which invoice?", "AP", "Ask the vendor for a proper credit note referencing the original invoice and a bill without negative lines.", True),
    "UNSAFE_DOCUMENT": ("The PDF carries active content (script, launch action or embedded file). Did it really come from this vendor?", "AP", "Do not open it outside the viewer; confirm the invoice with the vendor through the contact on file, then process a clean copy.", False),
    "SYSTEM_ERROR": ("Processing failed. Re-run, or review by hand?", "AP / on-call engineer", "Re-run the invoice; if it fails again, process it manually.", False),
}


# Templates that ask a generic question get the finding's own facts in front of the question (found by the LLM-as-judge:
# "Everything matched. Do you approve payment of this amount?" named neither the amount nor the limit).
_FACTS_FIRST = {"APPROVAL_LIMIT", "PO_NOT_FOUND", "ARITHMETIC_MISMATCH", "PO_INFERRED", "PO_MISSING", "GST_TYPE_MISMATCH", "TAX_AMOUNT_MISMATCH",
                "MISSING_FIELD", "INVALID_GSTIN", "UNSUPPORTED_DOC_TYPE", "CREDIT_NOTE_TO_APPLY", "LINE_NOT_ON_PO", "GRN_MISSING"}


def _pointer(code: str, evidence: Optional[list]) -> str:
    """One line naming the record to open, taken from the evidence that supports the primary reason (deterministic)."""
    from .evidence import SOURCE_SYSTEMS

    for e in evidence or []:
        if e.get("finding_code") == code and e.get("source_system") not in (None, "jev"):
            name = SOURCE_SYSTEMS.get(e["source_system"], e["source_system"])
            rec = f" {e['record_id']}" if e.get("record_id") else ""
            return f" Evidence to open: {name}{rec} ({e.get('field')}: expected {e.get('expected')}, invoice {e.get('actual')})."
    return ""


def handoff_for(decision: Decision, invoice: Optional[Invoice] = None, vendor_name: str = "", invoice_number: str = "",
                evidence: Optional[list] = None) -> Optional[dict]:
    """The one question a human must answer, who answers it, and a draft vendor email when the vendor must act."""
    if decision.decision != HOLD and decision.primary_reason not in ("DUPLICATE_INVOICE",):
        return None
    code = decision.primary_reason or ""
    q, owner, action, vendor_acts = _HANDOFF.get(code, ("Review the reasons and decide.", "AP", "Approve with a note, or reject.", False))
    primary = next((r for r in decision.reasons if r.code == code), None)
    if code in _FACTS_FIRST and primary is not None and primary.message:
        q = f"{primary.message.rstrip()} {q}"
    action = action + _pointer(code, evidence)
    vname = invoice.vendor_name if invoice else vendor_name
    num = invoice.invoice_number if invoice else invoice_number
    email = None
    if vendor_acts and decision.reasons:
        issue = decision.reasons[0].message
        email = (
            f"Subject: Invoice {num}: correction needed before payment\n\n"
            f"Hello {vname} team,\n\n"
            f"We received invoice {num}. Before we can pay it, we need one thing fixed:\n"
            f"- {issue}\n\n"
            f"Please send a corrected invoice (or confirm the details) by replying to this email.\n"
            f"Thank you,\nAccounts Payable"
        )
    return {"code": code, "question": q, "owner": owner, "next_action": action, "vendor_email_draft": email}
