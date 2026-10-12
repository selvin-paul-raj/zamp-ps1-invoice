"""FastAPI app: ingest, runs, live SSE stream, run history, review queue, stats."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import time

from .auth import RateLimiter, authenticate, parse_keys
from .config import settings
from .db import make_engine
from .ingest import parse_upload
from .service import Service
from . import telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
TRACING = telemetry.setup()
log = logging.getLogger("zamp_ps1.api")
SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


def _engine():
    if settings.db_migrate == "alembic":  # production path: versioned migrations (backend/migrations), Postgres-ready
        from .migrate import upgrade_head

        upgrade_head(settings.database_url)
    return make_engine(settings.database_url)


engine = _engine()
svc = Service(engine, settings)
API_KEYS = parse_keys(settings.api_keys) if settings.auth_mode == "apikey" else []
LIMITER = RateLimiter(settings.rate_limit_per_min)
HEARTBEAT = {"worker": None}


async def worker_loop(stop: asyncio.Event) -> None:
    """In-process worker. Each job runs in a thread so the event loop keeps serving SSE."""
    while not stop.is_set():
        HEARTBEAT["worker"] = time.time()
        job = await asyncio.to_thread(svc.claim_job)
        if job is None:
            await asyncio.sleep(0.2)
            continue
        await asyncio.to_thread(svc.run_job, *job)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.connect() as c:
        from sqlalchemy import func, select

        from . import db

        if not c.execute(select(func.count()).select_from(db.vendors)).scalar():
            svc.load_master(SEED_DIR)
    stop = asyncio.Event()
    inline = settings.worker_enabled and settings.worker_mode == "inline"
    task = asyncio.create_task(worker_loop(stop)) if inline else None
    yield
    stop.set()
    if task:
        await task


app = FastAPI(title="Zamp PS-1: Invoice -> PO decision", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"], allow_headers=["*"])


API_PREFIXES = ("/invoices", "/runs", "/demo", "/samples", "/impact", "/stats", "/master", "/review-queue", "/jobs", "/sources")
OPEN_PATHS = ("/health", "/ready", "/docs", "/openapi.json", "/redoc")


def _required_role(method: str, path: str) -> str:
    if method in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    if path.startswith("/jobs") or path.startswith("/master"):
        return "admin"
    if path.endswith("/review"):
        return "reviewer"  # releasing money (APPROVE) is checked again in the handler: approver role
    return "operator"


@app.middleware("http")
async def guard(request: Request, call_next):
    """API key + role check, rate limit, and one structured log line per API request. UI files and health are open."""
    path = request.scope["path"]
    is_api = path.startswith(API_PREFIXES) and not path.startswith(OPEN_PATHS)
    t0 = time.perf_counter()
    if is_api:
        presented = request.headers.get("x-api-key") or (request.headers.get("authorization", "")[7:] if request.headers.get("authorization", "").lower().startswith("bearer ") else None)
        principal = authenticate(settings.auth_mode, API_KEYS, presented)
        if principal is None:
            return JSONResponse({"detail": "missing or invalid API key"}, status_code=401)
        need = _required_role(request.method, path)
        if not principal.has(need):
            return JSONResponse({"detail": f"role '{principal.role}' cannot do this; needs '{need}'"}, status_code=403)
        ok, retry = LIMITER.allow(presented or (request.client.host if request.client else "anon"))
        if not ok:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": str(retry)})
        request.state.principal = principal
    resp = await call_next(request)
    if is_api and not path.endswith("/stream"):
        log.info("%s %s -> %s", request.method, path, resp.status_code,
                 extra={"path": path, "status": resp.status_code, "duration_ms": int((time.perf_counter() - t0) * 1000),
                        "client": getattr(getattr(request.state, "principal", None), "name", None)})
    return resp


@app.middleware("http")
async def api_prefix(request: Request, call_next):
    """The bundled UI calls the API under /api/... on the same origin; strip the prefix so one server serves both."""
    path = request.scope["path"]
    if path == "/api" or path.startswith("/api/"):
        request.scope["path"] = path[4:] or "/"
    return await call_next(request)


SAMPLE_FILES = {
    "KOS-0160_kaveri_clean.pdf": "Happy path: clean text PDF, CGST+SGST, full 3-way match",
    "KOS-0161_kaveri_scanned.pdf": "Scanned paper invoice (image only): OCR path",
    "BL-5651_brightline_igst.pdf": "Different layout; IGST charged on an intra-state supply",
    "DE-0413_deccan_high_value.pdf": "Inter-state, INR 6.8 lakh: above the auto-approval limit",
    "sample_flaky.json": "Simulated ERP timeout: watch the retry, then the decision",
    "samples_upload.csv": "Clean CSV invoice",
}


def _sample_path(name: str) -> Path:
    if name not in SAMPLE_FILES:
        raise HTTPException(404, "unknown sample")
    p = SEED_DIR / "pdfs" / name
    return p if p.exists() else SEED_DIR / name


class ReviewIn(BaseModel):
    action: str  # approve | reject | override
    reviewer: str = "reviewer"
    note: str = ""
    final_decision: Optional[str] = None


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness: the process is up. Cheap, no dependencies (used by Render's health check)."""
    return {"ok": True, "ruleset": settings.ruleset_version, "llm_adapter": svc.llm.name, "worker": settings.worker_enabled,
            "jev_mode": settings.jev_mode}


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness: can this instance do real work? Database reachable, master data loaded, worker alive."""
    from sqlalchemy import func, select, text

    from . import db

    checks: dict[str, Any] = {}
    ok = True
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
            checks["master_data_vendors"] = c.execute(select(func.count()).select_from(db.vendors)).scalar()
        checks["database"] = engine.dialect.name
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc.__class__.__name__}"
        ok = False
    if settings.worker_enabled and settings.worker_mode == "inline":
        age = None if HEARTBEAT["worker"] is None else round(time.time() - HEARTBEAT["worker"], 1)
        checks["worker_heartbeat_age_s"] = age
        ok = ok and age is not None and age < 30
    else:
        checks["worker"] = settings.worker_mode if settings.worker_enabled else "disabled"
    checks["queue"] = svc.queue_depth()
    checks.update({"jev_mode": settings.jev_mode, "auth": settings.auth_mode, "rate_limit_per_min": settings.rate_limit_per_min,
                   "tracing": TRACING, "db_migrate": settings.db_migrate, "ruleset": settings.ruleset_version})
    return JSONResponse({"ready": ok, **checks}, status_code=200 if ok else 503)


@app.post("/invoices")
def ingest_invoice(payload: dict[str, Any], idempotency_key: Optional[str] = Header(default=None)) -> dict[str, Any]:
    try:
        return svc.ingest(payload, idempotency_key=idempotency_key)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/invoices/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    try:
        payloads = parse_upload(file.filename or "", data)
        return {"results": [svc.ingest(p) for p in payloads]}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/invoices/{invoice_id}/runs")
def rerun(invoice_id: str, idempotency_key: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """Re-run an invoice. Same Idempotency-Key -> same run (no double processing)."""
    try:
        return svc.create_run(invoice_id, idempotency_key=idempotency_key)
    except KeyError as exc:
        raise HTTPException(404, "invoice not found") from exc


@app.post("/demo/seed")
def demo_seed(reset: bool = True) -> dict[str, Any]:
    """Reload master data and push every labelled seed invoice through the pipeline."""
    if reset:
        svc.reset()
    svc.load_master(SEED_DIR)
    items = json.loads((SEED_DIR / "invoices.json").read_text())
    out = []
    for it in sorted(items, key=lambda x: x["received_at"]):
        res = svc.ingest(it, idempotency_key=f"seed:{it['id']}", expected={**it["expected"], "seed_id": it["id"]})
        out.append({"seed_id": it["id"], **res})
    return {"queued": len(out), "items": out}


@app.get("/samples")
def samples() -> list[dict[str, str]]:
    """Sample input files a viewer can download or run with one click."""
    labels = {"sample_flaky.json": "ERP timeout (JSON)", "samples_upload.csv": "CSV invoice"}
    return [{"name": n, "label": labels.get(n, n.split("_")[0]), "description": d, "kind": n.rsplit(".", 1)[-1]}
            for n, d in SAMPLE_FILES.items() if _sample_path(n).exists()]


@app.get("/samples/{name}")
def sample_file(name: str) -> FileResponse:
    return FileResponse(_sample_path(name), filename=name)


@app.post("/demo/sample/{name}")
def run_sample(name: str) -> dict[str, Any]:
    """Run a sample file through exactly the same path as an upload (parse -> ingest -> pipeline)."""
    p = _sample_path(name)
    try:
        payloads = parse_upload(name, p.read_bytes())
        return {"results": [svc.ingest(x) for x in payloads]}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/impact")
def impact(monthly_invoices: int = 1000, manual_minutes: float = 12, review_minutes: float = 5, hourly_cost_inr: float = 600, target_stp: float = 0.7) -> dict[str, Any]:
    """Business view: hours and cost saved, straight-through rate, money stopped. Every assumption is a parameter."""
    return svc.impact(monthly_invoices, manual_minutes, review_minutes, hourly_cost_inr, target_stp)


@app.get("/runs")
def list_runs(limit: int = 200, decision: Optional[str] = None, review_status: Optional[str] = None) -> list[dict[str, Any]]:
    return svc.list_runs(limit=limit, decision=decision, review_status=review_status)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    r = svc.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return r


@app.get("/runs/{run_id}/stream")
async def stream(run_id: str, request: Request, last_event_id: Optional[str] = Header(default=None)) -> StreamingResponse:
    """Server-Sent Events: one `step` event per pipeline step, then `done`. Resumes via Last-Event-ID."""
    if not await asyncio.to_thread(svc.get_run, run_id):
        raise HTTPException(404, "run not found")
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    async def gen():
        nonlocal after
        idle = 0
        yield "retry: 1500\n\n"
        while True:
            if await request.is_disconnected():
                break
            events = await asyncio.to_thread(svc.events_after, run_id, after)
            for e in events:
                after = e["seq"]
                kind = "done" if e["step"] == "done" else "step"
                yield f"id: {e['seq']}\nevent: {kind}\ndata: {json.dumps(e, default=str)}\n\n"
                if kind == "done":
                    return
            idle = 0 if events else idle + 1
            if idle and idle % 40 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/review-queue")
def review_queue() -> list[dict[str, Any]]:
    return svc.list_runs(limit=500, review_status="pending")


@app.get("/runs/{run_id}/evidence")
def run_evidence(run_id: str) -> dict[str, Any]:
    """The evidence behind a decision: one record per check (system of record, record id, field, expected vs actual,
    rule, snapshot time, document line) plus every model call (question, answer, probability, latency, tokens, cost)."""
    if not svc.get_run(run_id):
        raise HTTPException(404, "run not found")
    return svc.evidence_for(run_id)


@app.get("/sources")
def sources() -> dict[str, Any]:
    """Systems of record this process reads, with their snapshot time."""
    from .evidence import SOURCE_SYSTEMS

    md = svc.master_data(exclude_invoice_id=None, before=None)
    return {"systems": [{"id": k, "name": v, **(md.sources.get(k) or {})} for k, v in SOURCE_SYSTEMS.items()],
            "counts": {"vendors": len(md.vendors), "purchase_orders": len(md.pos), "contracts": len(md.contracts), "gst_registry": len(md.gst_registry),
                       "bank_changes": len(md.bank_changes), "payments": len(md.payments), "prior_invoices": len(md.priors)}}


@app.get("/jobs")
def jobs(status: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
    """Queue view. status=dead lists the dead-letter queue (jobs that failed every retry; their runs were held, fail-closed)."""
    return {"depth": svc.queue_depth(), "jobs": svc.list_jobs(status, limit)}


@app.post("/jobs/{job_id}/requeue")
def requeue(job_id: int) -> dict[str, Any]:
    try:
        return svc.requeue_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, "job not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/runs/{run_id}/review")
def review(run_id: str, body: ReviewIn, request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    releases_money = body.action == "approve" or (body.action == "override" and body.final_decision == "APPROVE")
    if releases_money and principal is not None and not principal.has("approver"):
        raise HTTPException(403, "only the approver role can release an invoice for payment")
    reviewer = principal.name if (principal is not None and settings.auth_mode == "apikey") else body.reviewer
    try:
        return svc.submit_review(run_id, body.action, reviewer, body.note, body.final_decision)
    except KeyError as exc:
        raise HTTPException(404, "run not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/stats")
def stats() -> dict[str, Any]:
    return svc.stats()


@app.get("/master")
def master(include_history: bool = Query(True)) -> dict[str, Any]:
    md = svc.master_data(exclude_invoice_id=None, before=None)
    return {
        "vendors": [v.__dict__ | {"bank_account": "•••" + (v.bank_account or "")[-4:]} for v in md.vendors.values()],
        "purchase_orders": [{"po_number": p.po_number, "vendor_id": p.vendor_id, "currency": p.currency, "status": p.status, "total": float(p.total)} for p in md.pos.values()],
    }


# Bundled UI (static export of the Next.js app). Mounted last so every API route above wins.
_UI_DIR = Path(__import__("os").environ.get("FRONTEND_DIR", str(Path(__file__).resolve().parent.parent / "static")))
if _UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
