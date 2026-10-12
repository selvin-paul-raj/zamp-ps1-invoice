"""Safety layer for anything that leaves the process for a model.

1. PII redaction (pseudonymisation) before any model call: bank accounts, IFSC codes, PAN, e-mail
   addresses and phone numbers are replaced by stable placeholders ([ACCOUNT_1], [EMAIL_1] ...). The
   mapping stays in memory, so a model answer that refers to a placeholder can be mapped back.
2. Prompt-injection defence for document text:
   - detection: `scan_injection` finds text that tries to instruct the system ("ignore all checks",
     "pre-approved", "SYSTEM:"). The pipeline turns a hit into a SUSPICIOUS_INSTRUCTIONS hold.
   - neutralisation: `as_data` wraps document text as a quoted data field and strips control
     sequences; questions to a model never contain document text in their instructions.
   - architecture (the real defence): models never decide. Even a fully "convinced" model can only
     add a hold or a suggestion; the pay/hold decision is deterministic code.
"""
from __future__ import annotations

import re
from typing import Any

_INJECTION = [
    r"\bignore (all|any|the|previous|prior|above)\b[^.\n]{0,40}\b(instructions?|checks?|rules?|policy|policies)\b",
    r"\b(disregard|bypass|skip|override)\b[^.\n]{0,30}\b(checks?|rules?|validation|instructions?|policy|approval)\b",
    r"\bpre-?approved\b",
    r"\b(auto-?)?approve (this|immediately|now|the invoice)\b",
    r"^\s*(system|assistant|developer)\s*:",
    r"\byou are (now )?(an?|the) (ai|assistant|model|system)\b",
    r"<\s*/?\s*(system|instructions?|prompt)\s*>",
    r"\b(mark|set) (this|it|status) (as )?(paid|approved)\b",
]
_INJ_RX = [re.compile(p, re.I | re.M) for p in _INJECTION]

_PII = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("PAN", re.compile(r"(?<![0-9A-Z])[A-Z]{5}\d{4}[A-Z](?![0-9A-Z])")),
    ("ACCOUNT", re.compile(r"(?<![\w-])\d{9,18}(?![\w-])|\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),  # bank a/c or IBAN
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){10}(?!\d)")),
]


def scan_injection(text: str) -> list[str]:
    """Snippets of text that look like instructions aimed at an automated system."""
    hits = []
    for rx in _INJ_RX:
        for m in rx.finditer(text or ""):
            hits.append(m.group(0).strip()[:80])
    return hits


class Redactor:
    """Stable, reversible pseudonymisation for one model call (or one run)."""

    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}
        self._rev: dict[str, str] = {}
        self._n: dict[str, int] = {}

    def _token(self, kind: str, value: str) -> str:
        if value in self._rev:
            return self._rev[value]
        self._n[kind] = self._n.get(kind, 0) + 1
        tok = f"[{kind}_{self._n[kind]}]"
        self.mapping[tok] = value
        self._rev[value] = tok
        return tok

    def text(self, s: str | None) -> str:
        if not s:
            return s or ""
        out = s
        for kind, rx in _PII:
            out = rx.sub(lambda m, k=kind: self._token(k, m.group(0)), out)
        return out

    def obj(self, x: Any) -> Any:
        if isinstance(x, str):
            return self.text(x)
        if isinstance(x, dict):
            return {k: self.obj(v) for k, v in x.items()}
        if isinstance(x, list):
            return [self.obj(v) for v in x]
        return x

    def restore(self, s: str) -> str:
        for tok, val in self.mapping.items():
            s = s.replace(tok, val)
        return s


def as_data(text: str | None, limit: int = 4000) -> str:
    """Document text prepared for use as model *state* (data, never instructions)."""
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text or "")
    return t[:limit]
