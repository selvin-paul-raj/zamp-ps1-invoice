import json
"""Idempotency, retries / fail-closed, review queue, ingest formats, API + SSE."""
import copy

from conftest import run_one

from app.ingest import parse_csv, parse_pdf_text


def test_ingest_is_idempotent(svc, seed_invoices):
    a = svc.ingest(seed_invoices["INV-01"])
    b = svc.ingest(seed_invoices["INV-01"])
    assert a["created"] and not b["created"]
    assert a["invoice_id"] == b["invoice_id"] and a["run_id"] == b["run_id"]
    assert svc.drain(delay_ms=0) == 1


def test_rerun_with_same_key_returns_same_run(svc, seed_invoices):
    run = run_one(svc, seed_invoices["INV-01"])
    r1 = svc.create_run(run["invoice_id"], idempotency_key="rerun-1")
    r2 = svc.create_run(run["invoice_id"], idempotency_key="rerun-1")
    assert r1["run_id"] == r2["run_id"] and r1["created"] and not r2["created"]
    svc.drain(delay_ms=0)
    # Re-running the same invoice gives the same decision (it does not see itself as a duplicate).
    assert svc.get_run(r1["run_id"])["decision"] == "APPROVE"
    # Processing a completed run again is a no-op.
    assert svc.process_run(r1["run_id"], attempt=9)["skipped"] is True


def test_transient_failure_is_retried(svc, seed_invoices):
    inv = copy.deepcopy(seed_invoices["INV-01"])
    inv["_simulate_transient_failure"] = True
    run = run_one(svc, inv)
    assert run["decision"] == "APPROVE"
    assert run["attempt"] == 2
    assert any(e["step"] == "retry" for e in run["events"])


def test_permanent_failure_fails_closed(svc, seed_invoices, monkeypatch):
    import app.service as service_mod

    def boom(*a, **k):
        raise RuntimeError("ERP down")

    monkeypatch.setattr(service_mod, "run_pipeline", boom)
    run = run_one(svc, seed_invoices["INV-01"])
    assert run["decision"] == "HOLD_FOR_REVIEW"
    assert run["primary_reason"] == "SYSTEM_ERROR"
    assert run["attempt"] == svc.settings.max_attempts


def test_review_queue_approve_and_override(svc, seed_invoices):
    held = run_one(svc, seed_invoices["INV-10"])
    queue = svc.list_runs(review_status="pending")
    assert [r["id"] for r in queue] == [held["id"]]
    try:
        svc.submit_review(held["id"], "approve", "palak")
        raise AssertionError("approve without a note must fail")
    except ValueError:
        pass
    out = svc.submit_review(held["id"], "approve", "reviewer", note="Confirmed PO-1008 with requester")
    assert out["effective_decision"] == "APPROVE" and out["review_status"] == "resolved"
    assert svc.list_runs(review_status="pending") == []
    rej = run_one(svc, seed_invoices["INV-06"])
    out = svc.submit_review(rej["id"], "override", "reviewer", note="vendor to reissue", final_decision="REJECT")
    assert out["effective_decision"] == "REJECT"


def test_csv_ingest_groups_lines():
    csv_text = (
        "invoice_number,vendor_name,invoice_date,currency,po_number,line_no,sku,description,quantity,unit_price,tax_rate,subtotal,tax_total,total\n"
        "ACM-9001,Acme Industrial Supplies Pvt Ltd,2026-10-09,INR,PO-1001,1,STL-ROD-12,Steel rod,10,120,18,1370,246.60,1616.60\n"
        "ACM-9001,Acme Industrial Supplies Pvt Ltd,2026-10-09,INR,PO-1001,2,BOLT-M10,Bolt,20,8.5,18,1370,246.60,1616.60\n"
    )
    invs = parse_csv(csv_text.encode())
    assert len(invs) == 1 and len(invs[0]["lines"]) == 2


def test_pdf_text_stub_extracts_headers_and_holds(svc):
    text = "Brightline Logistics LLP\nInvoice No: BL-7777\nInvoice Date: 2026-10-09\nPO Number: PO-1002\nCurrency INR\nGrand Total: INR 20,160.00\n"
    payload = parse_pdf_text(text)
    assert payload["invoice_number"] == "BL-7777" and payload["po_number"] == "PO-1002" and payload["total"] == "20160.00"
    run = run_one(svc, payload)
    assert run["decision"] == "HOLD_FOR_REVIEW"  # stub extraction never auto-approves


def test_api_end_to_end_with_sse(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import importlib

    import app.config as cfg
    importlib.reload(cfg)
    import app.main as main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        assert client.get("/health").json()["ok"]
        seeded = client.post("/demo/seed").json()
        n_seed = len(json.loads((main.SEED_DIR / "invoices.json").read_text()))
        n_hold = sum(1 for i in json.loads((main.SEED_DIR / "invoices.json").read_text()) if i["expected"]["decision"] == "HOLD_FOR_REVIEW")
        assert seeded["queued"] == n_seed
        main.svc.drain(delay_ms=0)
        runs = client.get("/runs").json()
        assert len(runs) == n_seed and all(r["status"] == "completed" for r in runs)
        rid = runs[0]["id"]
        with client.stream("GET", f"/runs/{rid}/stream") as resp:
            body = "".join(resp.iter_text())
        assert "event: step" in body and "event: done" in body
        stats = client.get("/stats").json()
        assert stats["accuracy_vs_labels"] == 1.0 and stats["false_approvals"] == 0
        q = client.get("/review-queue").json()
        assert len(q) == n_hold
        r = client.post(f"/runs/{q[0]['id']}/review", json={"action": "reject", "reviewer": "t", "note": "x"})
        assert r.status_code == 200 and r.json()["effective_decision"] == "REJECT"
