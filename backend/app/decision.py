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
            top.sort(key=lambda f: -f.certainty)
            decision = REJECT if top_sev == SEVERITY_ORDER["reject"] else HOLD
            # Confidence in a non-approve decision = how sure we are of the strongest blocking finding.
            conf = top[0].certainty
            reasons = sorted(blocking, key=lambda f: (-SEVERITY_ORDER[f.severity], -f.certainty))
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
    if decision.decision == APPROVE:
        return (
            f"{head}: {who} matched its PO and goods receipt within tolerance on price, quantity, "
            f"currency and tax, and no duplicate was found (confidence {decision.confidence:.0%})."
        )
    parts = "; ".join(f"[{r.code}] {r.message}" for r in decision.reasons[:4])
    return f"{head}: {who}. {parts}"
