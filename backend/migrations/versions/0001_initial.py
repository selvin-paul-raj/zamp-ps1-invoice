"""rules-v2 schema: master data, invoices, runs, events, reviews, job queue.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None
S, T, I, F = sa.String, sa.Text, sa.Integer, sa.Float


def upgrade() -> None:
    op.create_table("vendors", sa.Column("id", S(32), primary_key=True), sa.Column("name", S(255), nullable=False), sa.Column("tax_id", S(64)),
                    sa.Column("currency", S(8)), sa.Column("bank_account", S(64)), sa.Column("status", S(16)), sa.Column("aliases", T))
    op.create_table("purchase_orders", sa.Column("po_number", S(32), primary_key=True), sa.Column("vendor_id", S(32), nullable=False),
                    sa.Column("currency", S(8)), sa.Column("status", S(16)), sa.Column("po_date", S(10)), sa.Column("lines", T, nullable=False))
    op.create_table("grns", sa.Column("grn_id", S(32), primary_key=True), sa.Column("po_number", S(32), nullable=False, index=True),
                    sa.Column("received_date", S(10)), sa.Column("lines", T, nullable=False))
    op.create_table("invoice_history", sa.Column("ref", S(32), primary_key=True), sa.Column("vendor_id", S(32)), sa.Column("invoice_number", S(64)),
                    sa.Column("po_number", S(32)), sa.Column("invoice_date", S(10)), sa.Column("currency", S(8)), sa.Column("total", S(32)),
                    sa.Column("status", S(16)), sa.Column("lines", T))
    op.create_table("invoices", sa.Column("id", S(40), primary_key=True), sa.Column("ingest_key", S(64), nullable=False, unique=True),
                    sa.Column("fingerprint", S(64), index=True), sa.Column("invoice_number", S(64)), sa.Column("vendor_name", S(255)),
                    sa.Column("po_number", S(32)), sa.Column("currency", S(8)), sa.Column("total", S(32)), sa.Column("source", S(32)),
                    sa.Column("received_at", S(40)), sa.Column("payload", T, nullable=False), sa.Column("expected", T), sa.Column("created_at", S(40)))
    op.create_table("runs", sa.Column("id", S(40), primary_key=True), sa.Column("invoice_id", S(40), nullable=False, index=True),
                    sa.Column("idempotency_key", S(128), unique=True), sa.Column("status", S(16), nullable=False), sa.Column("attempt", I),
                    sa.Column("decision", S(24)), sa.Column("confidence", F), sa.Column("primary_reason", S(48)), sa.Column("reasons", T),
                    sa.Column("explanation", T), sa.Column("review_status", S(16)), sa.Column("final_decision", S(24)), sa.Column("vendor_id", S(32)),
                    sa.Column("matched_po", S(32)), sa.Column("ruleset_version", S(24)), sa.Column("error", T), sa.Column("created_at", S(40)),
                    sa.Column("started_at", S(40)), sa.Column("finished_at", S(40)), sa.Column("duration_ms", I))
    op.create_table("run_events", sa.Column("id", I, primary_key=True, autoincrement=True), sa.Column("run_id", S(40), nullable=False, index=True),
                    sa.Column("seq", I, nullable=False), sa.Column("step", S(32)), sa.Column("status", S(16)), sa.Column("title", S(128)),
                    sa.Column("message", T), sa.Column("data", T), sa.Column("attempt", I), sa.Column("ts", S(40)),
                    sa.UniqueConstraint("run_id", "seq", name="uq_run_seq"))
    op.create_table("reviews", sa.Column("id", I, primary_key=True, autoincrement=True), sa.Column("run_id", S(40), nullable=False, index=True),
                    sa.Column("action", S(16)), sa.Column("final_decision", S(24)), sa.Column("reviewer", S(64)), sa.Column("note", T),
                    sa.Column("created_at", S(40)))
    op.create_table("jobs", sa.Column("id", I, primary_key=True, autoincrement=True), sa.Column("run_id", S(40), nullable=False, unique=True),
                    sa.Column("status", S(16), nullable=False), sa.Column("attempts", I), sa.Column("available_at", F), sa.Column("last_error", T))


def downgrade() -> None:
    for t in ("jobs", "reviews", "run_events", "runs", "invoices", "invoice_history", "grns", "purchase_orders", "vendors"):
        op.drop_table(t)
