"""rules-v3: systems of record (contracts, GST registry, bank-change log, payments, snapshots), evidence and model-call
audit tables, PO freshness columns, job leases (dead-letter + crash recovery).

Revision ID: 0002_sources_evidence
Revises: 0001_initial
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_sources_evidence"
down_revision = "0001_initial"
branch_labels = None
depends_on = None
S, T, I, F = sa.String, sa.Text, sa.Integer, sa.Float


def upgrade() -> None:
    with op.batch_alter_table("purchase_orders") as b:
        b.add_column(sa.Column("updated_at", S(32)))
        b.add_column(sa.Column("revision", I))
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("lease_until", F))
        b.add_column(sa.Column("worker_id", S(64)))
        b.add_column(sa.Column("updated_at", S(40)))
    op.create_index("ix_jobs_status_available", "jobs", ["status", "available_at"])
    op.create_table("source_snapshots", sa.Column("system", S(32), primary_key=True), sa.Column("name", S(128)), sa.Column("as_of", S(40)), sa.Column("owner", S(64)))
    op.create_table("contracts", sa.Column("contract_id", S(40), primary_key=True), sa.Column("vendor_id", S(32), nullable=False, index=True),
                    sa.Column("sku", S(64), nullable=False), sa.Column("unit_price", S(32), nullable=False), sa.Column("currency", S(8)),
                    sa.Column("valid_from", S(10)), sa.Column("valid_to", S(10)))
    op.create_table("gst_registry", sa.Column("gstin", S(15), primary_key=True), sa.Column("legal_name", S(255)), sa.Column("status", S(16)),
                    sa.Column("state_code", S(2)), sa.Column("status_date", S(10)))
    op.create_table("bank_changes", sa.Column("id", I, primary_key=True, autoincrement=True), sa.Column("vendor_id", S(32), nullable=False, index=True),
                    sa.Column("new_account_last4", S(8)), sa.Column("changed_at", S(10)), sa.Column("changed_by", S(128)), sa.Column("callback_verified", I))
    op.create_table("payments", sa.Column("payment_id", S(32), primary_key=True), sa.Column("vendor_id", S(32), nullable=False, index=True),
                    sa.Column("account_last4", S(8)), sa.Column("paid_on", S(10)), sa.Column("amount", S(32)), sa.Column("invoice_ref", S(40)))
    op.create_table("evidence", sa.Column("id", I, primary_key=True, autoincrement=True), sa.Column("run_id", S(40), nullable=False, index=True),
                    sa.Column("attempt", I), sa.Column("step", S(32)), sa.Column("rule_id", S(48)), sa.Column("source_system", S(32)),
                    sa.Column("record_id", S(96)), sa.Column("field", S(64)), sa.Column("expected", T), sa.Column("actual", T), sa.Column("result", S(16)),
                    sa.Column("source_as_of", S(40)), sa.Column("doc_ref", T), sa.Column("note", T), sa.Column("finding_code", S(48)), sa.Column("read_at", S(40)))
    op.create_table("model_calls", sa.Column("id", I, primary_key=True, autoincrement=True), sa.Column("run_id", S(40), index=True), sa.Column("step", S(32)),
                    sa.Column("role", S(32)), sa.Column("provider", S(32)), sa.Column("mode", S(16)), sa.Column("model", S(48)),
                    sa.Column("question_type", S(16)), sa.Column("question", T), sa.Column("options", T), sa.Column("answer", T),
                    sa.Column("probability", F), sa.Column("confidence", F), sa.Column("threshold", F), sa.Column("outcome", S(32)),
                    sa.Column("latency_ms", I), sa.Column("input_tokens", I), sa.Column("cost_usd", F), sa.Column("request_hash", S(64)), sa.Column("ts", S(40)))


def downgrade() -> None:
    for t in ("model_calls", "evidence", "payments", "bank_changes", "gst_registry", "contracts", "source_snapshots"):
        op.drop_table(t)
    op.drop_index("ix_jobs_status_available", "jobs")
    with op.batch_alter_table("jobs") as b:
        for c in ("updated_at", "worker_id", "lease_until"):
            b.drop_column(c)
    with op.batch_alter_table("purchase_orders") as b:
        b.drop_column("revision")
        b.drop_column("updated_at")
