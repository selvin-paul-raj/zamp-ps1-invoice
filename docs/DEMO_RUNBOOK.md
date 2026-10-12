# Live interview demo runbook

The brief: "You'll run your process live — a happy path and the edge cases you've prepared — and walk us through your decisions. No slides." A smooth demo shows you know the build.

## T-60 minutes
- [ ] Laptop charged, on mains. Wi-Fi + phone hotspot ready. Do Not Disturb on.
- [ ] Start the app locally: `docker run -p 8000:8000 zamp-ps1` (or the uvicorn command in the README). Open http://localhost:8000.
- [ ] If they need a link: also open the hosted URL (Render) once, so it wakes up (free tier sleeps; first load ~30–60 s).
- [ ] Click **Run demo seed**. Wait for 22 rows. Check: KPI "False approvals = 0".
- [ ] Have these open in tabs: (1) dashboard, (2) `docs/ARCHITECTURE.md` on GitHub, (3) `backend/app/decision.py`, (4) a terminal in `backend/`.
- [ ] Run `pytest -q` once so the output is in the terminal (39 passed).

## Running order (about 12 minutes, they will interrupt; that's good)
| # | Click | What to say in one line | Expected |
|---|---|---|---|
| 1 | Try a real input → **KOS-0160** | "A normal PDF, end to end." | APPROVE 100%, extraction card, 3 checks ✓ |
| 2 | **KOS-0161** (scanned) | "No text layer, OCR path; OCR reads are confirmed by a person in v1." | HOLD · LOW_EXTRACTION_CONFIDENCE |
| 3 | Row **ACM-2340** | "Everything matches except where the money goes." | HOLD · BANK_DETAILS_CHANGED 99%, no email draft |
| 4 | Row **ACM/2301** then **5521-OPS** | "Certain duplicate rejects; uncertain one holds." | REJECT · DUPLICATE_INVOICE / HOLD · POSSIBLE_DUPLICATE |
| 5 | Row **ACM-2330** | "Each invoice looks fine alone; the third pushes the PO to 1,050 of 1,000 kg." | HOLD · PO_QTY_OVERRUN |
| 6 | **BL-5651** | "Wrong GST head blocks the tax credit." | HOLD · GST_TYPE_MISMATCH + vendor email draft |
| 7 | **DE-0413** | "Everything matches, but ₹6.8 lakh needs the Controller." | HOLD · APPROVAL_LIMIT, owner Finance Controller |
| 8 | Row **ACM-CN-018** | "Credit notes are applied, never paid out." | HOLD · CREDIT_NOTE_TO_APPLY |
| 9 | Review queue → **KOS-0150** → note → Approve | "A person answers one question; it's audited." | resolved, audit trail shows note |
| 10 | Try a real input → **ERP timeout (JSON)** | "Retries with backoff; if it keeps failing it holds, never approves." | yellow retry banner, then APPROVE |
| 11 | Business impact card → change 1000 to 5000 | "Every assumption is editable." | numbers update |

## If they ask you to change something live (likely)
- **Tolerance:** in `backend/app/config.py` set `price_pct = Decimal("0.15")`, restart, re-run INV-06 (KOS-0144): it now approves. Explain why, then revert.
- **Approval gate:** start with `APPROVE_CONFIDENCE_THRESHOLD=0.96`: INV-03 (fuzzy vendor, 0.95) becomes HOLD · LOW_CONFIDENCE.
- **Approval limit:** `APPROVAL_LIMIT_INR=50000` → several clean invoices route to the Controller.
- **New edge case:** copy an item in `seed/make_seed.py`, change one field, `python seed/make_seed.py`, add the label, run `pytest -q`.

## Backup plan (in order)
1. **A run looks stuck** → refresh. The worker keeps going on the server; SSE resumes.
2. **UI won't load** → check the terminal; restart uvicorn; Run demo seed.
3. **Docker broken** → run without Docker: `cd backend && pip install -r requirements.txt && uvicorn app.main:app --port 8000` (OCR needs `tesseract` installed; if not, skip the scanned PDF and say so).
4. **Laptop/network dies** → share the hosted URL from your phone, or play your Loom video and talk over it.
5. **Something genuinely fails** → say what failed and why in one sentence, show the test that covers it (`pytest -q -k <name>`), move on. Calm beats perfect.

## Things not to say
- Don't claim the confidences are calibrated probabilities (they are hand-set; calibration is next).
- Don't claim the LLM is used in the demo (it is an optional fallback, off by default).
- Don't claim it handles every invoice layout (3 layouts + OCR tested; unknown layouts go to the LLM fallback or a person).
