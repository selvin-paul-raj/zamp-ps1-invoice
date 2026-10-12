"""rules-v2: real PDF extraction (text + OCR), India GST checks, credit notes, approval matrix, handoff, impact."""
import json
import shutil
from pathlib import Path

import pytest

from conftest import SEED, run_in_order, run_one

from app.domain import gstin_check_char, gstin_valid
from app import ingest

PDFS = SEED / "pdfs"
LABELS = json.loads((PDFS / "labels.json").read_text())


def test_gstin_checksum_known_sample():
    assert gstin_check_char("27AAPFU0939F1Z") == "V"  # widely published sample GSTIN 27AAPFU0939F1ZV
    assert gstin_valid("29AABCA1234F1Z5")
    assert not gstin_valid("29AABCA1234F1Z6")


@pytest.mark.parametrize("name", [n for n in LABELS if "scanned" not in n])
def test_text_pdf_fields_extracted_exactly(name):
    p = ingest.parse_upload(name, (PDFS / name).read_bytes())[0]
    for k, v in LABELS[name]["fields"].items():
        got = len(p["lines"]) if k == "line_count" else p.get(k)
        assert str(got) == str(v), (k, got, v)
    assert p["extraction"]["method"] == "text" and all(p["extraction"]["checks"].values())


@pytest.mark.skipif(not (shutil.which("tesseract") and shutil.which("pdftoppm")), reason="OCR tools not installed")
def test_scanned_pdf_goes_through_ocr_and_is_held(svc):
    name = "KOS-0161_kaveri_scanned.pdf"
    p = ingest.parse_upload(name, (PDFS / name).read_bytes())[0]
    assert p["extraction"]["method"] == "ocr"
    assert p["invoice_number"] == "KOS-0161" and p["total"] == "102660.00"
    assert p["extraction_confidence"] < 0.9  # policy: OCR never auto-approves in v1
    run = run_one(svc, p)
    assert run["decision"] == "HOLD_FOR_REVIEW" and run["primary_reason"] == "LOW_EXTRACTION_CONFIDENCE"


def test_clean_pdf_end_to_end_approves(svc):
    name = "KOS-0160_kaveri_clean.pdf"
    run = run_one(svc, ingest.parse_upload(name, (PDFS / name).read_bytes())[0])
    assert run["decision"] == "APPROVE", run["reasons"]


def test_gst_type_mismatch_intra_state_igst(svc, seed_invoices):
    run = run_one(svc, seed_invoices["INV-18"])
    assert run["primary_reason"] == "GST_TYPE_MISMATCH"


def test_inter_state_igst_is_correct_and_approval_limit_routes(svc, seed_invoices):
    run = run_one(svc, seed_invoices["INV-20"])
    codes = [r["code"] for r in run["reasons"]]
    assert codes == ["APPROVAL_LIMIT"]
    assert run["handoff"]["owner"] == "Finance Controller"


def test_approval_limit_never_hides_a_real_exception(svc, seed_invoices):
    big = dict(seed_invoices["INV-20"], bank_account="12345678901234")
    run = run_one(svc, big)
    assert run["primary_reason"] == "BANK_DETAILS_CHANGED"
    assert "APPROVAL_LIMIT" in [r["code"] for r in run["reasons"]]


def test_credit_note_is_matched_and_never_paid(svc, seed_invoices):
    run = run_one(svc, seed_invoices["INV-21"])
    assert run["decision"] == "HOLD_FOR_REVIEW" and run["primary_reason"] == "CREDIT_NOTE_TO_APPLY"
    unmatched = dict(seed_invoices["INV-21"], reference_invoice="ACM-9999", invoice_number="ACM-CN-019")
    assert run_one(svc, unmatched)["primary_reason"] == "CREDIT_NOTE_UNMATCHED"


def test_proforma_is_not_paid(svc, seed_invoices):
    assert run_one(svc, seed_invoices["INV-22"])["primary_reason"] == "UNSUPPORTED_DOC_TYPE"


def test_every_hold_has_one_question_and_owner(svc, seed_invoices):
    runs = run_in_order(svc, seed_invoices, list(seed_invoices))
    for sid, r in runs.items():
        if r["decision"] == "HOLD_FOR_REVIEW":
            h = r["handoff"]
            assert h and h["question"].endswith("?") and h["owner"], sid
    bec = runs["INV-14"]["handoff"]
    assert bec["vendor_email_draft"] is None and "Do NOT reply" in bec["next_action"]  # never email a possible fraudster
    assert "corrected invoice" in runs["INV-06"]["handoff"]["vendor_email_draft"]


def test_llm_fallback_is_capped_below_auto_approve(monkeypatch):
    monkeypatch.setenv("EXTRACTION_LLM_URL", "http://example.invalid/v1")
    monkeypatch.setenv("EXTRACTION_LLM_KEY", "test")
    monkeypatch.setattr(ingest, "_call_llm", lambda url, key, prompt: json.dumps({"invoice_number": "X-1", "vendor_name": "Odd Layout Co", "total": "10.00", "lines": []}))
    out = ingest.parse_pdf(b"%PDF-1.4 not really a pdf")  # no text, no OCR result -> parser misses -> LLM fallback
    assert out[0]["extraction"]["method"] in ("llm", "none", "ocr")
    obj = ingest.llm_extract("some text")
    assert obj["extraction_confidence"] == 0.8 < 0.9


def test_api_samples_impact_and_api_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/api2.db")
    import importlib

    import app.config as cfg
    importlib.reload(cfg)
    import app.main as main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        names = [s["name"] for s in client.get("/api/samples").json()]
        assert "KOS-0160_kaveri_clean.pdf" in names
        assert client.get("/api/samples/KOS-0160_kaveri_clean.pdf").content[:4] == b"%PDF"
        res = client.post("/api/demo/sample/KOS-0160_kaveri_clean.pdf").json()["results"][0]
        main.svc.drain(delay_ms=0)
        assert client.get(f"/api/runs/{res['run_id']}").json()["decision"] == "APPROVE"
        imp = client.get("/api/impact?monthly_invoices=2000&target_stp=0.8").json()
        assert imp["assumptions"]["monthly_invoices"] == 2000
        assert imp["scenario_target"]["hours_saved"] > 0
        assert client.get("/health").json()["ruleset"] == "rules-v2"


def test_final_invoice_after_proforma_is_not_a_duplicate(svc, seed_invoices):
    from conftest import run_in_order
    run_in_order(svc, seed_invoices, ["INV-22"])  # proforma for the same toners
    name = "KOS-0161_kaveri_scanned.pdf"
    if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
        pytest.skip("OCR tools not installed")
    run = run_one(svc, ingest.parse_upload(name, (PDFS / name).read_bytes())[0])
    assert [r["code"] for r in run["reasons"]] == ["LOW_EXTRACTION_CONFIDENCE"]
