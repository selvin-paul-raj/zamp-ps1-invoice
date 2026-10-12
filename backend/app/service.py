"""Application service: ingest, runs, worker queue (idempotency + retries), reviews, stats."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import and_, delete, desc, func, insert, select, update
from sqlalchemy.engine import Engine

from . import db
from .config import Settings
from .decision import HOLD, get_llm_adapter, handoff_for
from .domain import (
    BankChange,
    Contract,
    Decision,
    GstRecord,
    Payment,
    Finding,
    Invoice,
    MasterData,
    POLine,
    PriorInvoice,
    PurchaseOrder,
    StepResult,
    Vendor,
    mask_account,
    parse_date,
)
from .pipeline import run_pipeline

log = logging.getLogger("zamp_ps1")

EFFECTIVE_STATUS = {"APPROVE": "approved", "HOLD_FOR_REVIEW": "on_hold", "REJECT": "rejected"}


def canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class Service:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings
        self.llm = get_llm_adapter(settings.llm_provider)
        from .jev import get_assist

        self.assist = get_assist(settings)

    # ------------------------------------------------------------ master data
    def load_master(self, seed_dir: Path) -> dict[str, int]:
        rd = lambda n: json.loads((seed_dir / f"{n}.json").read_text())  # noqa: E731
        opt = lambda n: rd(n) if (seed_dir / f"{n}.json").exists() else ([] if n != "sources" else {})  # noqa: E731
        vs, ps, gs, hs = rd("vendors"), rd("purchase_orders"), rd("grns"), rd("invoice_history")
        srcs, cts, gst, bcs, pays = opt("sources"), opt("contracts"), opt("gst_registry"), opt("bank_changes"), opt("payments")
        with self.engine.begin() as c:
            for t in (db.vendors, db.purchase_orders, db.grns, db.invoice_history, db.source_snapshots, db.contracts,
                      db.gst_registry, db.bank_changes, db.payments):
                c.execute(delete(t))
            c.execute(insert(db.vendors), [{**v, "aliases": db.dumps(v.get("aliases", []))} for v in vs])
            c.execute(insert(db.purchase_orders), [{**p, "lines": db.dumps(p["lines"]), "updated_at": p.get("updated_at") or p.get("po_date"),
                                                    "revision": p.get("revision", 1)} for p in ps])
            c.execute(insert(db.grns), [{**g, "lines": db.dumps(g["lines"])} for g in gs])
            c.execute(insert(db.invoice_history), [{**h, "lines": db.dumps(h["lines"])} for h in hs])
            if srcs:
                c.execute(insert(db.source_snapshots), [{"system": k, **v} for k, v in srcs.items()])
            for t, rows in ((db.contracts, cts), (db.gst_registry, gst), (db.payments, pays)):
                if rows:
                    c.execute(insert(t), rows)
            if bcs:
                c.execute(insert(db.bank_changes), [{**b, "callback_verified": int(bool(b["callback_verified"]))} for b in bcs])
        return {"vendors": len(vs), "purchase_orders": len(ps), "grns": len(gs), "invoice_history": len(hs), "contracts": len(cts),
                "gst_registry": len(gst), "bank_changes": len(bcs), "payments": len(pays)}

    def reset(self) -> None:
        with self.engine.begin() as c:
            for t in (db.run_events, db.reviews, db.jobs, db.runs, db.invoices, db.evidence, db.model_calls):
                c.execute(delete(t))

    def master_data(self, exclude_invoice_id: Optional[str], before: Optional[str]) -> MasterData:
        with self.engine.connect() as c:
            vendors = {
                r.id: Vendor(r.id, r.name, r.tax_id, r.currency, r.bank_account, r.status, db.loads(r.aliases, []))
                for r in c.execute(select(db.vendors))
            }
            pos = {}
            for r in c.execute(select(db.purchase_orders)):
                lines = [
                    POLine(l["line_no"], l["sku"], l.get("description", ""), Decimal(str(l["quantity"])), Decimal(str(l["unit_price"])), Decimal(str(l["tax_rate"])))
                    for l in db.loads(r.lines, [])
                ]
                pos[r.po_number.upper()] = PurchaseOrder(r.po_number, r.vendor_id, r.currency, r.status, parse_date(r.po_date), lines,
                                                         parse_date(r.updated_at) or parse_date(r.po_date), r.revision or 1)
            received: dict[tuple[str, int], Decimal] = {}
            grn_refs: dict[str, list[str]] = {}
            for r in c.execute(select(db.grns)):
                grn_refs.setdefault(r.po_number, []).append(r.grn_id)
                for l in db.loads(r.lines, []):
                    k = (r.po_number, int(l["line_no"]))
                    received[k] = received.get(k, Decimal("0")) + Decimal(str(l["quantity"]))
            priors = [
                PriorInvoice(
                    r.ref, r.vendor_id, r.invoice_number, r.po_number, parse_date(r.invoice_date), Decimal(r.total), r.status, None,
                    [{"line_no": l.get("line_no"), "sku": l.get("sku"), "quantity": l["quantity"], "amount": l.get("amount", 0)} for l in db.loads(r.lines, [])],
                )
                for r in c.execute(select(db.invoice_history))
            ]
            # Earlier invoices processed by this system count as "known" (latest run, effective decision).
            q = select(db.invoices)
            if before:
                q = q.where(db.invoices.c.received_at < before)
            for inv_row in c.execute(q):
                if inv_row.id == exclude_invoice_id:
                    continue
                run = c.execute(
                    select(db.runs).where(and_(db.runs.c.invoice_id == inv_row.id, db.runs.c.status == "completed")).order_by(desc(db.runs.c.created_at)).limit(1)
                ).first()
                if run is None:
                    continue
                eff = run.final_decision or run.decision
                status = EFFECTIVE_STATUS.get(eff, "on_hold")
                if status == "rejected":
                    continue
                inv = Invoice.from_payload(db.loads(inv_row.payload))
                if inv.doc_type not in ("invoice", "tax_invoice"):
                    continue  # proformas and credit notes are not payable bills: never a duplicate of, or usage on, a PO
                priors.append(
                    PriorInvoice(
                        inv_row.id, run.vendor_id, inv.invoice_number, run.matched_po or inv.po_number, inv.invoice_date, inv.total or Decimal("0"),
                        status, inv.fingerprint(),
                        [{"line_no": l.line_no, "sku": l.sku, "quantity": str(l.quantity), "amount": str(l.quantity * l.unit_price)} for l in inv.lines],
                    )
                )
            contracts = [Contract(r.contract_id, r.vendor_id, r.sku, Decimal(r.unit_price), r.currency, parse_date(r.valid_from), parse_date(r.valid_to))
                         for r in c.execute(select(db.contracts))]
            gst = {r.gstin: GstRecord(r.gstin, r.legal_name, r.status, r.state_code, parse_date(r.status_date)) for r in c.execute(select(db.gst_registry))}
            bank_changes = [BankChange(r.vendor_id, r.new_account_last4, parse_date(r.changed_at), r.changed_by, bool(r.callback_verified))
                            for r in c.execute(select(db.bank_changes))]
            payments = [Payment(r.payment_id, r.vendor_id, r.account_last4, parse_date(r.paid_on), Decimal(r.amount or "0"), r.invoice_ref)
                        for r in c.execute(select(db.payments))]
            sources = {r.system: {"name": r.name, "as_of": r.as_of, "owner": r.owner} for r in c.execute(select(db.source_snapshots))}
        return MasterData(vendors, pos, received, grn_refs, priors, contracts, gst, bank_changes, payments, sources)

    # ------------------------------------------------------------ ingest
    def ingest(self, payload: dict[str, Any], idempotency_key: Optional[str] = None, expected: Optional[dict] = None, auto_run: bool = True) -> dict[str, Any]:
        """Idempotent: the same key (or the same payload) returns the existing invoice and its run."""
        payload = {k: v for k, v in payload.items() if k not in ("expected", "id")}
        inv = Invoice.from_payload(payload)  # validates numbers / dates
        key = (idempotency_key or canonical_hash(payload))[:64]
        with self.engine.begin() as c:
            existing = c.execute(select(db.invoices).where(db.invoices.c.ingest_key == key)).first()
            if existing:
                run = c.execute(select(db.runs).where(db.runs.c.invoice_id == existing.id).order_by(desc(db.runs.c.created_at)).limit(1)).first()
                return {"invoice_id": existing.id, "run_id": run.id if run else None, "created": False}
            inv_id = "inv_" + uuid.uuid4().hex[:10]
            received_at = payload.get("received_at") or db.now_iso()
            payload["received_at"] = received_at
            c.execute(insert(db.invoices).values(
                id=inv_id, ingest_key=key, fingerprint=inv.fingerprint(), invoice_number=inv.invoice_number, vendor_name=inv.vendor_name,
                po_number=inv.po_number, currency=inv.currency, total=str(inv.total) if inv.total is not None else None, source=inv.source,
                received_at=received_at, payload=db.dumps(payload), expected=db.dumps(expected) if expected else None, created_at=db.now_iso(),
            ))
        run_id = self.create_run(inv_id, idempotency_key=f"auto:{inv_id}")["run_id"] if auto_run else None
        return {"invoice_id": inv_id, "run_id": run_id, "created": True}

    def create_run(self, invoice_id: str, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        with self.engine.begin() as c:
            if not c.execute(select(db.invoices.c.id).where(db.invoices.c.id == invoice_id)).first():
                raise KeyError(invoice_id)
            if idempotency_key:
                ex = c.execute(select(db.runs).where(db.runs.c.idempotency_key == idempotency_key)).first()
                if ex:
                    return {"run_id": ex.id, "created": False}
            run_id = "run_" + uuid.uuid4().hex[:10]
            c.execute(insert(db.runs).values(
                id=run_id, invoice_id=invoice_id, idempotency_key=idempotency_key, status="queued", attempt=0,
                review_status="none", ruleset_version=self.settings.ruleset_version, created_at=db.now_iso(),
            ))
            c.execute(insert(db.jobs).values(run_id=run_id, status="queued", attempts=0, available_at=0.0))
            self._event(c, run_id, "queued", "info", "Queued", "Run queued for processing", {}, 0)
        return {"run_id": run_id, "created": True}

    # ------------------------------------------------------------ events
    def _event(self, c, run_id: str, step: str, status: str, title: str, message: str, data: dict, attempt: int) -> int:
        seq = (c.execute(select(func.max(db.run_events.c.seq)).where(db.run_events.c.run_id == run_id)).scalar() or 0) + 1
        c.execute(insert(db.run_events).values(run_id=run_id, seq=seq, step=step, status=status, title=title, message=message, data=db.dumps(data), attempt=attempt, ts=db.now_iso()))
        return seq

    def emit(self, run_id: str, step: str, status: str, title: str, message: str, data: dict, attempt: int) -> None:
        with self.engine.begin() as c:
            self._event(c, run_id, step, status, title, message, data, attempt)

    def events_after(self, run_id: str, after_seq: int) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(db.run_events).where(and_(db.run_events.c.run_id == run_id, db.run_events.c.seq > after_seq)).order_by(db.run_events.c.seq)).all()
        return [self._event_dict(r) for r in rows]

    @staticmethod
    def _event_dict(r) -> dict[str, Any]:
        return {"seq": r.seq, "step": r.step, "status": r.status, "title": r.title, "message": r.message, "data": db.loads(r.data, {}), "attempt": r.attempt, "ts": r.ts}

    # ------------------------------------------------------------ processing
    def process_run(self, run_id: str, attempt: int, delay_ms: Optional[int] = None) -> dict[str, Any]:
        delay = (self.settings.step_delay_ms if delay_ms is None else delay_ms) / 1000.0
        with self.engine.begin() as c:
            run = c.execute(select(db.runs).where(db.runs.c.id == run_id)).first()
            if run is None:
                raise KeyError(run_id)
            if run.status == "completed":  # idempotent: never re-decide a finished run
                return {"run_id": run_id, "decision": run.decision, "skipped": True}
            inv_row = c.execute(select(db.invoices).where(db.invoices.c.id == run.invoice_id)).first()
            c.execute(update(db.runs).where(db.runs.c.id == run_id).values(status="running", attempt=attempt, started_at=run.started_at or db.now_iso()))
        if delay_ms is None and (inv_row.ingest_key or "").startswith("seed:"):
            delay = min(delay, 0.04)  # bulk demo seed: fast enough to fill the dashboard in ~10 s, still visibly live
        t0 = time.perf_counter()
        inv = Invoice.from_payload(db.loads(inv_row.payload))
        md = self.master_data(exclude_invoice_id=inv_row.id, before=inv_row.received_at)
        self.emit(run_id, "start", "info", "Run started", f"Attempt {attempt}: {inv.vendor_name} {inv.invoice_number}", {"invoice_id": inv_row.id}, attempt)

        def on_step(r: StepResult) -> None:
            if delay:
                time.sleep(delay)
            self.emit(run_id, r.step, r.status, r.title, r.summary, r.to_dict(), attempt)

        from .telemetry import span

        with span("pipeline.run", {"run.id": run_id, "invoice.id": inv_row.id, "run.attempt": attempt, "ruleset": self.settings.ruleset_version}) as sp:
            steps, decision = run_pipeline(inv, md, self.settings, on_step=on_step, llm=self.llm, attempt=attempt, assist=self.assist)
            sp.set_attribute("decision", decision.decision)
            sp.set_attribute("decision.primary_reason", decision.primary_reason or "")
        vendor_id = next((s.data.get("vendor_id") for s in steps if s.step == "vendor_match"), None)
        matched_po = next((s.data.get("po_number") for s in steps if s.step == "po_lookup"), None)
        dur = int((time.perf_counter() - t0) * 1000)
        with self.engine.begin() as c:
            res = c.execute(
                update(db.runs).where(and_(db.runs.c.id == run_id, db.runs.c.status != "completed")).values(
                    status="completed", decision=decision.decision, confidence=round(decision.confidence, 3), primary_reason=decision.primary_reason,
                    reasons=db.dumps([r.to_dict() for r in decision.reasons]), explanation=decision.explanation,
                    review_status="pending" if decision.decision == HOLD else "none", vendor_id=vendor_id, matched_po=matched_po,
                    finished_at=db.now_iso(), duration_ms=dur, error=None,
                )
            )
            if res.rowcount:
                ev_rows = [{k: (db.dumps(v) if k in ("expected", "actual") and v is not None and not isinstance(v, str) else v)
                            for k, v in e.items() if k in db.evidence.c.keys()} | {"run_id": run_id, "attempt": attempt}
                           for st in steps for e in st.evidence]
                if ev_rows:
                    c.execute(insert(db.evidence), ev_rows)
                mc_rows = [{"run_id": run_id, "step": st.step, **{k: (db.dumps(v) if k in ("options", "answer") and v is not None else v)
                                                                  for k, v in mc.items() if k in db.model_calls.c.keys()}}
                           for st in steps for mc in st.model_calls]
                if mc_rows:
                    c.execute(insert(db.model_calls), mc_rows)
                self._event(c, run_id, "done", "info", "Run completed", decision.decision, decision.to_dict(), attempt)
        return {"run_id": run_id, "decision": decision.decision, "primary_reason": decision.primary_reason, "confidence": decision.confidence}

    # ------------------------------------------------------------ worker queue
    def claim_job(self) -> Optional[tuple[int, str, int]]:
        now = time.time()
        with self.engine.begin() as c:
            row = c.execute(select(db.jobs).where(and_(db.jobs.c.status == "queued", db.jobs.c.available_at <= now)).order_by(db.jobs.c.id).limit(1)).first()
            if not row:
                return None
            # Atomic claim (works on SQLite and Postgres; on Postgres prefer SELECT ... FOR UPDATE SKIP LOCKED).
            res = c.execute(update(db.jobs).where(and_(db.jobs.c.id == row.id, db.jobs.c.status == "queued")).values(status="running", attempts=row.attempts + 1))
            if res.rowcount != 1:
                return None
            return row.id, row.run_id, row.attempts + 1

    def run_job(self, job_id: int, run_id: str, attempt: int, delay_ms: Optional[int] = None) -> None:
        try:
            self.process_run(run_id, attempt, delay_ms=delay_ms)
            with self.engine.begin() as c:
                c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(status="done"))
        except Exception as exc:  # noqa: BLE001 - every failure is retried, then fails closed
            log.warning("run %s attempt %s failed: %s", run_id, attempt, exc)
            if attempt < self.settings.max_attempts:
                backoff = 0.5 * (2 ** (attempt - 1))
                with self.engine.begin() as c:
                    c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(status="queued", available_at=time.time() + backoff, last_error=str(exc)))
                    c.execute(update(db.runs).where(db.runs.c.id == run_id).values(status="queued", error=str(exc)))
                    self._event(c, run_id, "retry", "warn", "Transient failure - retrying", f"{exc}. Retrying in {backoff:.1f}s (attempt {attempt + 1}/{self.settings.max_attempts}).", {"error": str(exc)}, attempt)
            else:
                # Fail closed: a system error is never an approval.
                with self.engine.begin() as c:
                    c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(status="dead", last_error=str(exc)))
                    reason = {"code": "SYSTEM_ERROR", "severity": "hold", "message": f"Processing failed after {attempt} attempts: {exc}", "step": "worker", "certainty": 1.0, "evidence": {}}
                    c.execute(update(db.runs).where(db.runs.c.id == run_id).values(
                        status="completed", decision=HOLD, confidence=1.0, primary_reason="SYSTEM_ERROR", reasons=db.dumps([reason]),
                        explanation="Held: processing failed; a human must review (fail-closed).", review_status="pending", error=str(exc), finished_at=db.now_iso()))
                    self._event(c, run_id, "done", "info", "Run completed (fail-closed)", HOLD, {"decision": HOLD, "primary_reason": "SYSTEM_ERROR"}, attempt)

    def drain(self, delay_ms: int = 0, max_loops: int = 10_000) -> int:
        """Process all queued jobs synchronously (CLI, tests, evals). Waits out retry backoff."""
        n = 0
        for _ in range(max_loops):
            job = self.claim_job()
            if job is None:
                with self.engine.connect() as c:
                    pending = c.execute(select(func.min(db.jobs.c.available_at)).where(db.jobs.c.status == "queued")).scalar()
                if pending is None:
                    break
                time.sleep(max(0.0, min(2.0, pending - time.time())) + 0.01)
                continue
            self.run_job(*job, delay_ms=delay_ms)
            n += 1
        return n

    # ------------------------------------------------------------ queries
    def _run_dict(self, r, inv_row=None) -> dict[str, Any]:
        d = {
            "id": r.id, "invoice_id": r.invoice_id, "status": r.status, "attempt": r.attempt, "decision": r.decision,
            "final_decision": r.final_decision, "effective_decision": r.final_decision or r.decision, "confidence": r.confidence,
            "primary_reason": r.primary_reason, "reasons": db.loads(r.reasons, []), "explanation": r.explanation,
            "review_status": r.review_status, "vendor_id": r.vendor_id, "matched_po": r.matched_po, "ruleset_version": r.ruleset_version,
            "error": r.error, "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at, "duration_ms": r.duration_ms,
        }
        if inv_row is not None:
            d["invoice"] = {
                "invoice_number": inv_row.invoice_number, "vendor_name": inv_row.vendor_name, "po_number": inv_row.po_number,
                "currency": inv_row.currency, "total": inv_row.total, "source": inv_row.source, "received_at": inv_row.received_at,
                "expected": db.loads(inv_row.expected),
            }
            if r.status == "completed" and r.decision:
                fs = [Finding(**{k: x.get(k) for k in ("code", "severity", "message", "step", "certainty", "evidence") if k in x}) for x in d["reasons"]]
                d["handoff"] = handoff_for(Decision(r.decision, r.confidence or 0, fs, r.primary_reason), None, inv_row.vendor_name, inv_row.invoice_number)
        return d

    def list_runs(self, limit: int = 200, decision: Optional[str] = None, review_status: Optional[str] = None) -> list[dict[str, Any]]:
        q = select(db.runs, db.invoices).join(db.invoices, db.invoices.c.id == db.runs.c.invoice_id).order_by(desc(db.runs.c.created_at)).limit(limit)
        if decision:
            q = q.where(db.runs.c.decision == decision)
        if review_status:
            q = q.where(db.runs.c.review_status == review_status)
        with self.engine.connect() as c:
            rows = c.execute(q).all()
        out = []
        for row in rows:
            m = row._mapping
            r = type("R", (), {k: m[db.runs.c[k]] for k in db.runs.c.keys()})
            i = type("I", (), {k: m[db.invoices.c[k]] for k in db.invoices.c.keys()})
            out.append(self._run_dict(r, i))
        return out

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as c:
            r = c.execute(select(db.runs).where(db.runs.c.id == run_id)).first()
            if not r:
                return None
            i = c.execute(select(db.invoices).where(db.invoices.c.id == r.invoice_id)).first()
            evs = c.execute(select(db.run_events).where(db.run_events.c.run_id == run_id).order_by(db.run_events.c.seq)).all()
            revs = c.execute(select(db.reviews).where(db.reviews.c.run_id == run_id).order_by(db.reviews.c.id)).all()
        d = self._run_dict(r, i)
        payload = db.loads(i.payload)
        if payload.get("bank_account"):  # PII: only the last 4 digits ever leave the API
            payload["bank_account"] = mask_account(payload["bank_account"])
        d["invoice"]["payload"] = payload
        d["events"] = [self._event_dict(e) for e in evs]
        d["reviews"] = [{"action": x.action, "final_decision": x.final_decision, "reviewer": x.reviewer, "note": x.note, "created_at": x.created_at} for x in revs]
        d.update(self.evidence_for(run_id))
        return d

    def evidence_for(self, run_id: str) -> dict[str, Any]:
        """Evidence records (latest attempt) and model calls for a run: the audit trail behind its decision."""
        with self.engine.connect() as c:
            evs = c.execute(select(db.evidence).where(db.evidence.c.run_id == run_id).order_by(db.evidence.c.id)).all()
            mcs = c.execute(select(db.model_calls).where(db.model_calls.c.run_id == run_id).order_by(db.model_calls.c.id)).all()
        last = max((e.attempt or 0 for e in evs), default=0)
        from .evidence import SOURCE_SYSTEMS

        def val(x):
            if x is None:
                return None
            try:
                return json.loads(x) if x[:1] in "[{" else x
            except ValueError:
                return x
        evidence = [{k: getattr(e, k) for k in db.evidence.c.keys() if k not in ("id", "run_id")} | {"source_name": SOURCE_SYSTEMS.get(e.source_system, e.source_system)}
                    for e in evs if (e.attempt or 0) == last]
        for e in evidence:
            e["expected"], e["actual"] = val(e["expected"]), val(e["actual"])
        calls = [{k: getattr(m, k) for k in db.model_calls.c.keys() if k not in ("id", "run_id")} for m in mcs]
        for m in calls:
            m["options"], m["answer"] = db.loads(m["options"]), db.loads(m["answer"])
        sources = sorted({e["source_system"] for e in evidence})
        return {"evidence": evidence, "model_calls": calls, "evidence_summary": {
            "records": len(evidence), "sources": sources,
            "mismatches": sum(1 for e in evidence if e["result"] in ("mismatch", "missing")),
            "model_calls": len(calls), "model_cost_usd": round(sum(m["cost_usd"] or 0 for m in calls), 6)}}

    def submit_review(self, run_id: str, action: str, reviewer: str, note: str = "", final_decision: Optional[str] = None) -> dict[str, Any]:
        mapping = {"approve": "APPROVE", "reject": "REJECT"}
        if action == "override":
            if final_decision not in ("APPROVE", "REJECT", "HOLD_FOR_REVIEW"):
                raise ValueError("override needs final_decision")
            final = final_decision
        elif action in mapping:
            final = mapping[action]
        else:
            raise ValueError("action must be approve | reject | override")
        if not note and final == "APPROVE":
            raise ValueError("a note is required to approve a held invoice (audit trail)")
        with self.engine.begin() as c:
            r = c.execute(select(db.runs).where(db.runs.c.id == run_id)).first()
            if r is None:
                raise KeyError(run_id)
            if r.status != "completed":
                raise ValueError("run not completed yet")
            c.execute(insert(db.reviews).values(run_id=run_id, action=action, final_decision=final, reviewer=reviewer, note=note, created_at=db.now_iso()))
            c.execute(update(db.runs).where(db.runs.c.id == run_id).values(final_decision=final, review_status="resolved"))
            self._event(c, run_id, "review", "info", "Human review", f"{reviewer}: {action} -> {final}", {"note": note, "final_decision": final}, r.attempt)
        return self.get_run(run_id)

    def stats(self) -> dict[str, Any]:
        runs = self.list_runs(limit=10_000)
        latest: dict[str, dict] = {}
        for r in sorted(runs, key=lambda x: x["created_at"]):
            latest[r["invoice_id"]] = r
        done = [r for r in latest.values() if r["status"] == "completed"]
        dec = Counter(r["decision"] for r in done)
        reasons = Counter(r["primary_reason"] for r in done if r["primary_reason"])
        labelled = [r for r in done if r.get("invoice", {}).get("expected")]
        correct = sum(1 for r in labelled if r["decision"] == r["invoice"]["expected"]["decision"])
        false_approve = sum(1 for r in labelled if r["decision"] == "APPROVE" and r["invoice"]["expected"]["decision"] != "APPROVE")
        durs = sorted(r["duration_ms"] for r in done if r["duration_ms"] is not None)
        return {
            "invoices": len(latest), "runs": len(runs), "completed": len(done),
            "decisions": dict(dec), "top_reasons": reasons.most_common(8),
            "pending_review": sum(1 for r in done if r["review_status"] == "pending"),
            "straight_through_rate": round(dec.get("APPROVE", 0) / len(done), 3) if done else None,
            "labelled": len(labelled), "accuracy_vs_labels": round(correct / len(labelled), 3) if labelled else None,
            "false_approvals": false_approve,
            "p50_ms": durs[len(durs) // 2] if durs else None,
        }

    # ------------------------------------------------------------ business impact
    RISK_CODES = {"DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE", "BANK_DETAILS_CHANGED", "VENDOR_BLOCKED", "PO_CLOSED", "INVALID_GSTIN"}

    def impact(self, monthly_invoices: int = 1000, manual_minutes: float = 12, review_minutes: float = 5,
               hourly_cost_inr: float = 600, target_stp: float = 0.7) -> dict[str, Any]:
        """Hours, cost and risk, from the runs in this system plus explicit assumptions (all overridable)."""
        latest: dict[str, dict] = {}
        for r in sorted(self.list_runs(limit=10_000), key=lambda x: x["created_at"]):
            latest[r["invoice_id"]] = r
        done = [r for r in latest.values() if r["status"] == "completed"]
        n = len(done)
        dec = Counter(r["decision"] for r in done)
        observed_stp = dec.get("APPROVE", 0) / n if n else 0.0
        fx = self.settings.fx_to_inr
        stopped, stopped_n, overbill = Decimal("0"), 0, Decimal("0")
        for r in done:
            ccy = r["invoice"]["currency"] or "INR"
            amount = Decimal(r["invoice"]["total"] or "0") * fx.get(ccy, Decimal("0"))
            if r["primary_reason"] in self.RISK_CODES:
                stopped += amount
                stopped_n += 1
            for f in r["reasons"]:
                if f.get("code") == "PRICE_VARIANCE":
                    overbill += Decimal(str(f.get("evidence", {}).get("variance_amount", 0))) * fx.get(ccy, Decimal("0"))

        def scenario(stp: float) -> dict[str, Any]:
            before_h = monthly_invoices * manual_minutes / 60
            after_h = monthly_invoices * (1 - stp) * review_minutes / 60
            saved_h = before_h - after_h
            return {
                "straight_through_rate": round(stp, 3),
                "hours_before": round(before_h, 1), "hours_after": round(after_h, 1), "hours_saved": round(saved_h, 1),
                "fte_freed": round(saved_h / 160, 2),
                "cost_per_invoice_before_inr": round(manual_minutes / 60 * hourly_cost_inr, 1),
                "cost_per_invoice_after_inr": round((1 - stp) * review_minutes / 60 * hourly_cost_inr, 1),
                "monthly_saving_inr": round(saved_h * hourly_cost_inr),
            }

        return {
            "assumptions": {
                "monthly_invoices": monthly_invoices, "manual_minutes_per_invoice": manual_minutes,
                "review_minutes_per_exception": review_minutes, "loaded_hourly_cost_inr": hourly_cost_inr, "target_stp": target_stp,
                "notes": [
                    "Manual minutes, review minutes and hourly cost are assumptions to replace with the customer's own baseline.",
                    "Review minutes are low because every hold arrives pre-investigated with one question.",
                    "External benchmark: Ardent Partners 2024 reports USD 9.40 average and USD 2.78 best-in-class cost per invoice (as cited by Zamp).",
                    "The demo set is exception-heavy on purpose, so its observed straight-through rate understates a real invoice mix.",
                ],
            },
            "observed": {"invoices": n, "decisions": dict(dec), "straight_through_rate": round(observed_stp, 3),
                         "money_stopped_inr": float(stopped.quantize(Decimal("1"))), "invoices_stopped": stopped_n,
                         "overbilling_caught_inr": float(overbill.quantize(Decimal("1")))},
            "scenario_observed": scenario(observed_stp),
            "scenario_target": scenario(target_stp),
        }
