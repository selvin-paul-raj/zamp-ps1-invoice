"""Storage layer: SQLAlchemy Core, SQLite by default, Postgres by setting DATABASE_URL.

JSON payloads are stored as TEXT so the same schema works on both engines.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

vendors = Table(
    "vendors", metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("tax_id", String(64)),
    Column("currency", String(8)),
    Column("bank_account", String(64)),
    Column("status", String(16), default="active"),
    Column("aliases", Text, default="[]"),
)

purchase_orders = Table(
    "purchase_orders", metadata,
    Column("po_number", String(32), primary_key=True),
    Column("vendor_id", String(32), nullable=False),
    Column("currency", String(8)),
    Column("status", String(16)),
    Column("po_date", String(10)),
    Column("lines", Text, nullable=False),
    Column("updated_at", String(32)),  # last change in the PO register (freshness check)
    Column("revision", Integer, default=1),
)

grns = Table(
    "grns", metadata,
    Column("grn_id", String(32), primary_key=True),
    Column("po_number", String(32), nullable=False, index=True),
    Column("received_date", String(10)),
    Column("lines", Text, nullable=False),
)

invoice_history = Table(
    "invoice_history", metadata,
    Column("ref", String(32), primary_key=True),
    Column("vendor_id", String(32)),
    Column("invoice_number", String(64)),
    Column("po_number", String(32)),
    Column("invoice_date", String(10)),
    Column("currency", String(8)),
    Column("total", String(32)),
    Column("status", String(16)),
    Column("lines", Text),
)

invoices = Table(
    "invoices", metadata,
    Column("id", String(40), primary_key=True),
    Column("ingest_key", String(64), nullable=False, unique=True),  # idempotency key for ingest
    Column("fingerprint", String(64), index=True),
    Column("invoice_number", String(64)),
    Column("vendor_name", String(255)),
    Column("po_number", String(32)),
    Column("currency", String(8)),
    Column("total", String(32)),
    Column("source", String(32)),
    Column("received_at", String(40)),
    Column("payload", Text, nullable=False),
    Column("expected", Text),  # label (seed data only)
    Column("created_at", String(40)),
)

runs = Table(
    "runs", metadata,
    Column("id", String(40), primary_key=True),
    Column("invoice_id", String(40), nullable=False, index=True),
    Column("idempotency_key", String(128), unique=True),
    Column("status", String(16), nullable=False),  # queued | running | completed | failed
    Column("attempt", Integer, default=0),
    Column("decision", String(24)),
    Column("confidence", Float),
    Column("primary_reason", String(48)),
    Column("reasons", Text),
    Column("explanation", Text),
    Column("review_status", String(16)),  # none | pending | resolved
    Column("final_decision", String(24)),
    Column("vendor_id", String(32)),
    Column("matched_po", String(32)),
    Column("ruleset_version", String(24)),
    Column("error", Text),
    Column("created_at", String(40)),
    Column("started_at", String(40)),
    Column("finished_at", String(40)),
    Column("duration_ms", Integer),
)

run_events = Table(
    "run_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(40), nullable=False, index=True),
    Column("seq", Integer, nullable=False),
    Column("step", String(32)),
    Column("status", String(16)),
    Column("title", String(128)),
    Column("message", Text),
    Column("data", Text),
    Column("attempt", Integer),
    Column("ts", String(40)),
    UniqueConstraint("run_id", "seq", name="uq_run_seq"),
)

reviews = Table(
    "reviews", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(40), nullable=False, index=True),
    Column("action", String(16)),  # approve | reject | override
    Column("final_decision", String(24)),
    Column("reviewer", String(64)),
    Column("note", Text),
    Column("created_at", String(40)),
)

jobs = Table(
    "jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(40), nullable=False, unique=True),
    Column("status", String(16), nullable=False),  # queued | running | done | dead (dead-letter)
    Column("attempts", Integer, default=0),
    Column("available_at", Float, default=0.0),
    Column("last_error", Text),
    Column("lease_until", Float),  # a running job whose lease expired is reclaimed (worker crashed mid-run)
    Column("worker_id", String(64)),
    Column("updated_at", String(40)),
)

# ---------------------------------------------------------------- other systems of record (rules-v3)
source_snapshots = Table(
    "source_snapshots", metadata,
    Column("system", String(32), primary_key=True),
    Column("name", String(128)),
    Column("as_of", String(40)),
    Column("owner", String(64)),
)

contracts = Table(
    "contracts", metadata,
    Column("contract_id", String(40), primary_key=True),
    Column("vendor_id", String(32), nullable=False, index=True),
    Column("sku", String(64), nullable=False),
    Column("unit_price", String(32), nullable=False),
    Column("currency", String(8)),
    Column("valid_from", String(10)),
    Column("valid_to", String(10)),
)

gst_registry = Table(
    "gst_registry", metadata,
    Column("gstin", String(15), primary_key=True),
    Column("legal_name", String(255)),
    Column("status", String(16)),
    Column("state_code", String(2)),
    Column("status_date", String(10)),
)

bank_changes = Table(
    "bank_changes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vendor_id", String(32), nullable=False, index=True),
    Column("new_account_last4", String(8)),
    Column("changed_at", String(10)),
    Column("changed_by", String(128)),
    Column("callback_verified", Integer),
)

payments = Table(
    "payments", metadata,
    Column("payment_id", String(32), primary_key=True),
    Column("vendor_id", String(32), nullable=False, index=True),
    Column("account_last4", String(8)),
    Column("paid_on", String(10)),
    Column("amount", String(32)),
    Column("invoice_ref", String(40)),
)

# ---------------------------------------------------------------- audit: evidence and model calls per run
evidence = Table(
    "evidence", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(40), nullable=False, index=True),
    Column("attempt", Integer),
    Column("step", String(32)),
    Column("rule_id", String(48)),
    Column("source_system", String(32)),
    Column("record_id", String(96)),
    Column("field", String(64)),
    Column("expected", Text),
    Column("actual", Text),
    Column("result", String(16)),
    Column("source_as_of", String(40)),
    Column("doc_ref", Text),
    Column("note", Text),
    Column("finding_code", String(48)),
    Column("read_at", String(40)),
)

model_calls = Table(
    "model_calls", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(40), index=True),
    Column("step", String(32)),
    Column("role", String(32)),  # duplicate_judgement | choose_po | field_check
    Column("provider", String(32)),  # jev
    Column("mode", String(16)),  # live | replay | fake
    Column("model", String(48)),  # versioned id the API returned, e.g. jev-1.13.0
    Column("question_type", String(16)),  # noul | choice | score
    Column("question", Text),
    Column("options", Text),
    Column("answer", Text),
    Column("probability", Float),
    Column("confidence", Float),
    Column("threshold", Float),
    Column("outcome", String(32)),  # what the gate did: hold_added | suggestion | no_action | to_human
    Column("latency_ms", Integer),
    Column("input_tokens", Integer),
    Column("cost_usd", Float),
    Column("request_hash", String(64)),
    Column("ts", String(40)),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def dumps(x: Any) -> str:
    return json.dumps(x, default=str)


def loads(x: str | None, default: Any = None) -> Any:
    return json.loads(x) if x else default


def make_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _):  # WAL lets the SSE reader and the worker run concurrently
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    metadata.create_all(eng)
    add_missing_columns(eng)
    return eng


def add_missing_columns(eng: Engine) -> list[str]:
    """Additive, nullable column upgrades for databases created by an older version (SQLite demo files, Render
    disks). Alembic (backend/migrations) is the real migration path for Postgres; this keeps `create_all` mode safe."""
    from sqlalchemy import inspect, text

    added = []
    insp = inspect(eng)
    with eng.begin() as c:
        for t in metadata.sorted_tables:
            if not insp.has_table(t.name):
                continue
            have = {col["name"] for col in insp.get_columns(t.name)}
            for col in t.columns:
                if col.name not in have and not col.primary_key:
                    ctype = col.type.compile(dialect=eng.dialect)
                    c.execute(text(f'ALTER TABLE {t.name} ADD COLUMN {col.name} {ctype}'))
                    added.append(f"{t.name}.{col.name}")
    return added
