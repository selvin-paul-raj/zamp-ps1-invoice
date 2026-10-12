"""Jev (TypeSafe) adapter: typed questions with calibrated probabilities, advisory only.

API (docs.typesafe.ai/api): POST {base}/v1/systemone, Bearer auth, body {state, model, questions};
question types: noul (yes/no -> probability of yes), choice (pick one -> probabilities + confidence),
score (ordered levels). The response carries the versioned model id (e.g. jev-1.13.0) and token usage.

Three roles, each gated by a calibrated threshold (docs/EVALS.md):
  1. duplicate_check  noul    "is this invoice a re-submission of that earlier one?"  p >= gate -> HOLD for a person
  2. choose_po        choice  "which open PO does this invoice belong to?"           p >= gate -> suggestion (still HOLD)
  3. field_check      noul xN "does the document text support this extracted value?" p <  gate -> field flagged to a person

The hard rule: a model can never approve. It can only add a hold, add a suggestion, or flag a field.
Deterministic rules still make every pay/hold decision; below a gate nothing happens and the rules stand.

Modes (JEV_MODE): off | replay (default; recorded real answers + deterministic fake, no network) | live.
Every call is logged: question, options, answer, probability, latency, tokens, cost, model id, mode.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Settings, jev_api_key
from .domain import Finding, PriorInvoice, PurchaseOrder, invoice_number_core, money
from .evidence import now_iso
from .safety import Redactor, as_data
from .telemetry import span

log = logging.getLogger("zamp_ps1.jev")
DEFAULT_REPLAY = Path(__file__).resolve().parent.parent / "seed" / "jev_replay.json"


class JevError(RuntimeError):
    pass


# --------------------------------------------------------------------------- transport
class JevClient:
    """Plain HTTP client (no SDK dependency). Retries 429/529/5xx with exponential backoff, honours retry-after."""

    def __init__(self, base_url: str, model: str, timeout_s: float = 10.0, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        import httpx

        key = jev_api_key()
        if not key:
            raise JevError("no JEV_API_KEY / TYPESAFE_API_KEY set")
        body = {"state": state, "model": self.model, "questions": questions}
        delay = 0.5
        for attempt in range(self.max_retries + 1):
            try:
                r = httpx.post(f"{self.base_url}/v1/systemone", json=body, timeout=self.timeout_s,
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            except httpx.HTTPError as exc:
                if attempt == self.max_retries:
                    raise JevError(f"network error: {exc.__class__.__name__}") from exc
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 529) or r.status_code >= 500:
                if attempt == self.max_retries:
                    raise JevError(f"HTTP {r.status_code} after {attempt + 1} attempts")
                ra = r.headers.get("retry-after")
                time.sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else delay)
                delay *= 2
                continue
            raise JevError(f"HTTP {r.status_code}: {r.text[:200]}")
        raise JevError("unreachable")


class ReplayStore:
    """Recorded real answers keyed by a hash of (model family, state, questions). Committed in seed/jev_replay.json."""

    _lock = threading.Lock()

    def __init__(self, path: Path):
        self.path = path
        self._data: Optional[dict[str, Any]] = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text()) if self.path.exists() else {}
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def get(self, h: str) -> Optional[dict[str, Any]]:
        return self._load().get(h)

    def put(self, h: str, entry: dict[str, Any]) -> None:
        with self._lock:
            d = self._load()
            d[h] = entry
            self.path.write_text(json.dumps(d, indent=1, sort_keys=True))


def request_hash(state: Any, questions: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"family": "jev", "state": state, "questions": questions}, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- helpers
def _inv_view(inv, red: Redactor) -> dict[str, Any]:
    return red.obj({
        "vendor": inv.vendor_name, "invoice_number": inv.invoice_number, "invoice_date": str(inv.invoice_date) if inv.invoice_date else None,
        "po_number": inv.po_number, "currency": inv.currency, "total": money(inv.total) if inv.total is not None else None,
        "lines": [{"sku": l.sku, "description": l.description, "quantity": str(l.quantity), "unit_price": str(l.unit_price)} for l in inv.lines],
        "notes": as_data(inv.notes, 500) or None,
    })


def _prior_view(p: PriorInvoice, red: Redactor) -> dict[str, Any]:
    return red.obj({
        "invoice_number": p.invoice_number, "invoice_date": str(p.invoice_date) if p.invoice_date else None, "po_number": p.po_number,
        "total": money(p.total), "status": p.status,
        "lines": [{"sku": l.get("sku"), "quantity": str(l.get("quantity")), "amount": str(l.get("amount"))} for l in p.lines],
    })


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


# --------------------------------------------------------------------------- deterministic fakes (unseen questions in replay mode)
def _fake_dup(state: dict[str, Any]) -> float:
    a, b = state["new_invoice"], state["earlier_invoice"]
    if invoice_number_core(a["invoice_number"] or "") and invoice_number_core(a["invoice_number"] or "") == invoice_number_core(b["invoice_number"] or ""):
        return 0.9
    ta, tb = float(a.get("total") or 0), float(b.get("total") or 0)
    close = 1 - min(1.0, abs(ta - tb) / max(tb, 1.0) * 4)
    qa = {(l["sku"], float(l["quantity"])) for l in a["lines"]}
    qb = {(l["sku"], float(l["quantity"])) for l in b["lines"]}
    overlap = len(qa & qb) / max(1, len(qa | qb))
    return round(0.03 + 0.85 * close * overlap, 3)


def _fake_choice(state: dict[str, Any], criteria: dict[str, Any]) -> dict[str, float]:
    iw = set().union(*[_words(l["description"]) for l in state["invoice"]["lines"]]) if state["invoice"]["lines"] else set()
    scores = {k: 1.0 + 3.0 * len(iw & _words(json.dumps(v))) for k, v in criteria.items()}
    tot = sum(scores.values())
    return {k: round(v / tot, 3) for k, v in scores.items()}


def _fake_field(text: str, value: str) -> float:
    norm = lambda s: re.sub(r"[\s,]", "", (s or "").lower())  # noqa: E731
    return 0.95 if value and norm(value) in norm(text) else 0.15


# --------------------------------------------------------------------------- the adapter used by the pipeline
class ModelAssist:
    provider = "jev"

    def __init__(self, settings: Settings, replay_path: Optional[Path] = None, client: Optional[JevClient] = None):
        self.s = settings
        self.mode = settings.jev_mode
        self.client = client or JevClient(settings.jev_base_url, settings.jev_model, settings.jev_timeout_s)
        self.store = ReplayStore(Path(settings.jev_replay_file) if settings.jev_replay_file else (replay_path or DEFAULT_REPLAY))

    # ---- core call
    def _call(self, ctx, role: str, state: Any, questions: dict[str, Any], fake: Callable[[], dict[str, Any]]) -> tuple[Optional[dict], dict]:
        h = request_hash(state, questions)
        meta = {"mode": self.mode, "model": None, "latency_ms": 0, "input_tokens": 0, "cost_usd": 0.0, "request_hash": h[:16], "error": None}
        resp = None
        with span("gen_ai.jev." + role, {"gen_ai.system": "typesafe", "gen_ai.operation.name": "systemone", "gen_ai.request.model": self.s.jev_model,
                                         "jev.role": role, "jev.mode": self.mode}) as sp:
            t0 = time.perf_counter()
            if self.mode == "live":
                try:
                    resp = self.client.ask(state, questions)
                    meta["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                    if self.s.jev_record:
                        self.store.put(h, {"response": resp, "latency_ms": meta["latency_ms"], "recorded_at": now_iso(), "role": role})
                except JevError as exc:
                    meta["error"] = str(exc)
                    log.warning("jev %s failed: %s (no action; rules decide)", role, exc)
            else:
                rec = self.store.get(h)
                if rec:
                    resp = rec["response"]
                    meta["mode"] = "replay"
                    meta["latency_ms"] = rec.get("latency_ms", 0)
                else:
                    resp = {"model": "fake-deterministic", "answers": fake(), "usage": {"input_tokens": 0, "output_tokens": 0}}
                    meta["mode"] = "fake"
            if resp:
                meta["model"] = resp.get("model")
                meta["input_tokens"] = int((resp.get("usage") or {}).get("input_tokens") or 0)
                meta["cost_usd"] = round(meta["input_tokens"] * self.s.jev_price_per_mtok_usd / 1e6, 8)
                sp.set_attribute("gen_ai.response.model", meta["model"] or "")
                sp.set_attribute("gen_ai.usage.input_tokens", meta["input_tokens"])
                sp.set_attribute("gen_ai.usage.output_tokens", int((resp.get("usage") or {}).get("output_tokens") or 0))
            sp.set_attribute("jev.latency_ms", meta["latency_ms"])
        return resp, meta

    def _log(self, ctx, role: str, qtype: str, question: str, options: Any, answer: Any, prob: Optional[float], conf: Optional[float],
             threshold: float, outcome: str, meta: dict, record_id: Optional[str], finding: Optional[str] = None) -> dict:
        call = {"role": role, "provider": "jev", "mode": meta["mode"], "model": meta["model"], "question_type": qtype, "question": question,
                "options": options, "answer": answer, "probability": prob, "confidence": conf, "threshold": threshold, "outcome": outcome,
                "latency_ms": meta["latency_ms"], "input_tokens": meta["input_tokens"], "cost_usd": meta["cost_usd"],
                "request_hash": meta["request_hash"], "error": meta["error"], "ts": now_iso()}
        ctx.callbuf.append(call)
        ctx.ev(f"M-JEV-{role.upper().replace('_', '-')}", "jev", record_id, role, f"gate {threshold:.2f}",
               "error: " + meta["error"] if meta["error"] else (f"{answer} (p={prob:.2f})" if prob is not None else str(answer)),
               "info", note=f"{meta['mode']} · {meta['model'] or '-'} · {meta['latency_ms']} ms · {meta['input_tokens']} tokens · outcome: {outcome}",
               finding=finding)
        return call

    # ---- role 1: duplicate judgement (noul)
    DUP_Q = {
        "type": "noul",
        "instructions": "Is `new_invoice` a re-submission of `earlier_invoice`: the same goods or service for the same delivery or period billed "
                        "again (possibly with a new or reformatted invoice number, a different date, or small edits), rather than a separate genuine charge?",
        "criteria": {"true": "The same charge billed again (a duplicate).",
                     "false": "A different delivery, quantity, period or service: a new, separate charge."},
    }

    def duplicate_check(self, ctx, f: list[Finding]) -> None:
        inv, vendor = ctx.inv, ctx.vendor
        if vendor is None or inv.total is None:
            return
        flagged = {x.evidence.get("prior") for x in f if x.code == "POSSIBLE_DUPLICATE"}
        cands = []
        for p in ctx.md.priors:
            if p.vendor_id != vendor.id or not p.total:
                continue
            days = abs((inv.invoice_date - p.invoice_date).days) if inv.invoice_date and p.invoice_date else 999
            amt_rel = abs(inv.total - p.total) / p.total
            same_po = bool(inv.po_number and p.po_number and inv.po_number.upper() == p.po_number.upper())
            core = invoice_number_core(inv.invoice_number)
            if days <= 120 and (amt_rel <= Decimal("0.25") or same_po or (core and core == invoice_number_core(p.invoice_number))):
                cands.append((float(amt_rel), p))
        for _, p in sorted(cands, key=lambda x: x[0])[:3]:
            red = Redactor()
            state = {"new_invoice": _inv_view(inv, red), "earlier_invoice": _prior_view(p, red)}
            qs = {"is_duplicate": self.DUP_Q}
            resp, meta = self._call(ctx, "duplicate_judgement", state, qs, lambda: {"is_duplicate": {"type": "noul", "noul": _fake_dup(state)}})
            gate = self.s.jev_dup_hold_p
            if not resp:
                self._log(ctx, "duplicate_judgement", "noul", self.DUP_Q["instructions"], None, None, None, None, gate, "error_no_action", meta, p.ref)
                continue
            prob = float(resp["answers"]["is_duplicate"]["noul"])
            if p.ref in flagged:
                outcome = "agrees_with_rule" if prob >= gate else "rule_hold_kept"
                for x in f:
                    if x.code == "POSSIBLE_DUPLICATE" and x.evidence.get("prior") == p.ref:
                        x.message += f" Model view (advisory): probability {prob:.2f} that it is the same charge."
                        x.evidence["model_probability"] = prob
            elif prob >= gate:
                outcome = "hold_added"
                f.append(Finding("POSSIBLE_DUPLICATE", "hold",
                                 f"Model judges this likely a re-submission of {p.invoice_number} ({p.status}, {money(p.total)}): probability {prob:.2f} "
                                 f"(gate {gate:.2f}). The rules did not flag it; a person compares the two before anything is paid.",
                                 "duplicates", round(min(prob, 0.9), 3), {"prior": p.ref, "prior_number": p.invoice_number, "raised_by": "jev",
                                                                          "model_probability": prob, "model": meta["model"]}))
            else:
                outcome = "no_action"
            self._log(ctx, "duplicate_judgement", "noul", self.DUP_Q["instructions"], None, "yes" if prob >= 0.5 else "no", prob, abs(2 * prob - 1),
                      gate, outcome, meta, f"{p.invoice_number}", "POSSIBLE_DUPLICATE" if outcome == "hold_added" else None)

    # ---- role 2: choose between candidate POs (choice)
    def choose_po(self, ctx, cands: list[PurchaseOrder]) -> Optional[dict]:
        inv = ctx.inv
        red = Redactor()
        criteria = {}
        for po in cands[:20]:
            criteria[po.po_number] = red.obj({"po_date": str(po.po_date), "lines": [{"sku": l.sku, "description": l.description, "quantity": str(l.quantity),
                                                                                       "unit_price": str(l.unit_price)} for l in po.lines]})
        state = {"invoice": _inv_view(inv, red)}
        q = {"type": "choice", "instructions": "Which purchase order does this invoice bill against? Use the items, descriptions, delivery location, "
                                               "quantities, prices and dates.", "criteria": criteria}
        resp, meta = self._call(ctx, "choose_po", state, {"po": q}, lambda: {"po": _choice_answer(_fake_choice(state, criteria))})
        gate = self.s.jev_po_min_p
        if not resp:
            self._log(ctx, "choose_po", "choice", q["instructions"], list(criteria), None, None, None, gate, "error_no_action", meta, None)
            return None
        a = resp["answers"]["po"]
        probs = a.get("probabilities") or {}
        top = a.get("choice") or max(probs, key=probs.get)
        p = float(probs.get(top, 0))
        outcome = "suggestion" if p >= gate else "to_human"
        self._log(ctx, "choose_po", "choice", q["instructions"], probs, top, p, a.get("confidence"), gate, outcome, meta, top,
                  "PO_SUGGESTED_BY_MODEL" if outcome == "suggestion" else None)
        if outcome != "suggestion":
            return None
        return {"po_number": top, "probability": round(p, 3), "confidence": a.get("confidence"), "probabilities": probs, "model": meta["model"], "mode": meta["mode"]}

    # ---- role 3: field-level confidence for low-confidence extraction (noul fan-out)
    FIELDS = ("invoice_number", "invoice_date", "total", "vendor_tax_id", "po_number", "bank_account")

    def field_check(self, ctx) -> Optional[list[str]]:
        inv = ctx.inv
        ex = inv.extraction or {}
        red = Redactor()
        text = red.text(as_data(ex.get("text"), 6000))
        values = {"invoice_number": inv.invoice_number, "invoice_date": str(inv.invoice_date) if inv.invoice_date else None,
                  "total": str(inv.total) if inv.total is not None else None, "vendor_tax_id": inv.vendor_tax_id, "po_number": inv.po_number,
                  "bank_account": inv.bank_account}
        qs = {}
        for k in self.FIELDS:
            if values.get(k):
                qs[f"supports_{k}"] = {"type": "noul", "instructions": {
                    "field": k.replace("_", " "), "extracted_value": red.text(values[k]),
                    "question": "Does the document text in the state show `extracted_value` as this invoice's `field`? Ignore formatting differences "
                                "(commas, spacing, date format such as 10-10-2026 vs 2026-10-10)."}}
        if not qs:
            return None
        state = {"document_text": text}
        resp, meta = self._call(ctx, "field_check", state, qs,
                                lambda: {k: {"type": "noul", "noul": _fake_field(text, q["instructions"]["extracted_value"])} for k, q in qs.items()})
        gate = self.s.jev_field_min_p
        if not resp:
            self._log(ctx, "field_check", "noul", "field support", list(qs), None, None, None, gate, "error_no_action", meta, inv.invoice_number)
            return None
        flagged = []
        for k in qs:
            p = float(resp["answers"][k]["noul"])
            name = k.removeprefix("supports_")
            if p < gate:
                flagged.append(name)
            self._log(ctx, "field_check", "noul", f"Does the document show {name} = extracted value?", None, "yes" if p >= 0.5 else "no", p, abs(2 * p - 1),
                      gate, "flag_to_human" if p < gate else "supported", {**meta, "input_tokens": meta["input_tokens"] if k == next(iter(qs)) else 0,
                                                                            "cost_usd": meta["cost_usd"] if k == next(iter(qs)) else 0.0}, f"{inv.invoice_number}:{name}")
        return flagged


def _choice_answer(probs: dict[str, float]) -> dict[str, Any]:
    top = max(probs, key=probs.get)
    n = len(probs)
    return {"type": "choice", "choice": top, "probabilities": probs, "confidence": round((probs[top] - 1 / n) / (1 - 1 / n), 3) if n > 1 else 1.0}


def get_assist(settings: Settings) -> Optional[ModelAssist]:
    return None if settings.jev_mode == "off" else ModelAssist(settings)
