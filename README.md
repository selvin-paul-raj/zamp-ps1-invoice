# Invoice → PO decision process (Zamp ASA case study, PS-1)

**What it does, in one breath:** a vendor invoice arrives as a PDF (clean or scanned), CSV or JSON. The process reads it, matches it to the purchase order, the goods receipt and the vendor master, checks for duplicates, payment fraud and Indian GST errors, and ends with **APPROVE**, **HOLD with one clear question for a person**, or **REJECT**. You watch every step happen live, and every decision says why.

**Design rule:** *AI reads, code decides, people own the exceptions.* The approve/reject decision is plain, tested code, so it is auditable and an invoice cannot talk it into paying.

| | |
|---|---|
| Live run view | Each step streams to the browser as it runs (Server-Sent Events) |
| Dashboard | Run history, decisions, confidence, reasons, KPIs, exception reasons, business impact |
| Review queue | Every hold arrives pre-investigated: one question, who should answer, next step, draft vendor email |
| Real inputs | Text PDFs, **scanned PDFs (OCR)**, CSV, JSON; 4 sample PDFs in 3 vendor layouts |
| Evidence it works | 39 tests; 26/26 labelled cases; 32/32 PDF fields; **0 false approvals in 600 adversarial variants** |

> All data is synthetic. No API key or paid service is needed.

---

## Run it

**Option A: one container (recommended, same as hosted)**
```bash
docker build -t zamp-ps1 .
docker run -p 8000:8000 zamp-ps1
# open http://localhost:8000   (API docs at /docs)
```

**Option B: without Docker**
```bash
# needs Python 3.12, Node 22, and (for scanned PDFs) poppler-utils + tesseract-ocr
cd frontend && npm ci && npm run build && cd ..
cp -r frontend/out backend/static
cd backend && pip install -r requirements.txt
PIPELINE_STEP_DELAY_MS=350 uvicorn app.main:app --port 8000
# open http://localhost:8000
```

**Developing the UI:** run the API as above and `cd frontend && npm run dev` (UI on :3000 calls the API on :8000).

**Then:** click **Run demo seed** (loads 22 labelled invoices, ~10 s), and use **Try a real input** to run the sample PDFs one click at a time, or **Upload invoice** for your own file.

**Tests and evals:**
```bash
cd backend && pytest -q && python -m evals.run_evals
```

---

## What happens to one invoice

`intake & extraction → arithmetic → vendor match (+ GSTIN checksum, blocked vendor, bank-account change) → PO lookup (stated or inferred) → currency → 3-way match (price vs PO, quantity vs goods received, PO history) → tax & GST (rate, amount, CGST+SGST vs IGST) → duplicates → approval policy → decision → explanation`

Credit notes take their own path: matched to the original invoice and applied, never paid out.

Full system design with diagrams (architecture, data model, sequence, rules engine, human-in-the-loop, retries, security, scaling, deployment, trade-offs, roadmap): **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

### How the decision is made
- Every check produces findings with a severity (`info`, `hold`, `reject`), a certainty and evidence.
- **Most severe finding wins:** REJECT > HOLD > APPROVE. Policy routing (approval limit) ranks below real exceptions so it never hides a duplicate or fraud.
- **Approval also needs confidence ≥ 0.85**, the product of step confidences (fuzzy vendor name, price within tolerance, inferred PO, OCR read).
- **Fail closed:** a system error becomes HOLD, never APPROVE. **Idempotent:** the same invoice sent twice is processed once.

### Reading documents
- **Text PDFs:** `pdftotext -layout` keeps table rows together; header fields, GST type and line items are parsed with patterns.
- **Scanned PDFs:** `pdftoppm` + `tesseract` OCR, same parser.
- **Cross-checks** catch reading errors without a model: rows × price = amount, rows sum to subtotal, subtotal + tax = total. Per-field confidence travels with the invoice.
- **OCR reads are confirmed by a person in v1**, even when everything matches (trust is earned with data).
- **Optional LLM fallback** for unknown layouts (`EXTRACTION_LLM_URL`, OpenAI-compatible). Its output is capped at 0.8 confidence, so a model-read invoice is always confirmed by a person. Off by default.

---

## Edge cases (all labelled, all tested)

Lead edge cases for the demo, each showing a different kind of judgment:

| Case | Input | Result | Why it matters |
|---|---|---|---|
| **Payment fraud (BEC)** | ACM-2340: everything matches, bank account changed, "URGENT new account" | HOLD · BANK_DETAILS_CHANGED, no email draft, "call the contact on file" | Matched is not safe. Never reply to the email asking for the change. |
| **Split-PO overrun** | ACM-2330: third invoice takes 1,050 kg against a 1,000 kg PO | HOLD · PO_QTY_OVERRUN | Checks the PO's history, not one document. |
| **Scanned invoice** | KOS-0161: image-only PDF | OCR reads all fields → HOLD · LOW_EXTRACTION_CONFIDENCE | Messy real input handled, with a deliberate trust policy. |
| **Wrong GST head** | BL-5651 (PDF): IGST on a Tamil Nadu → Tamil Nadu supply | HOLD · GST_TYPE_MISMATCH + vendor email draft | Adds up, but the buyer loses the tax credit. |

Everything else covered:

| Seed | Scenario | Expected |
|---|---|---|
| INV-01 | Clean 3-way match, two lines | APPROVE |
| INV-02 | Second invoice on a partly paid PO | APPROVE |
| INV-03 | Fuzzy vendor name; +1.19% price, inside tolerance | APPROVE (lower confidence) |
| INV-04 | Partial delivery billed correctly | APPROVE |
| INV-05 | Partial delivery billed beyond receipt | HOLD · QTY_EXCEEDS_RECEIPT |
| INV-06 | Price +10% | HOLD · PRICE_VARIANCE |
| INV-07 | Duplicate resent with reformatted number | REJECT · DUPLICATE_INVOICE |
| INV-08 | Near-duplicate, reference drift | HOLD · POSSIBLE_DUPLICATE |
| INV-09 | EUR PO billed in USD | HOLD · CURRENCY_MISMATCH |
| INV-10 | No PO: only fitting open PO inferred | HOLD · PO_INFERRED |
| INV-11 | PO not in register | HOLD · PO_NOT_FOUND |
| INV-12 | 28% tax where PO says 18% | HOLD · TAX_RATE_MISMATCH |
| INV-13 | Split-PO overrun | HOLD · PO_QTY_OVERRUN |
| INV-14 | Bank details changed + urgency | HOLD · BANK_DETAILS_CHANGED |
| INV-15 | Blocked vendor | REJECT · VENDOR_BLOCKED |
| INV-16 | Closed PO | REJECT · PO_CLOSED |
| INV-17 | Totals don't add up | HOLD · ARITHMETIC_MISMATCH |
| INV-18 | IGST on an intra-state supply | HOLD · GST_TYPE_MISMATCH |
| INV-19 | GSTIN typo (checksum fails) | HOLD · INVALID_GSTIN |
| INV-20 | ₹7.55 lakh, everything matches | HOLD · APPROVAL_LIMIT (Finance Controller) |
| INV-21 | Credit note against a paid invoice | HOLD · CREDIT_NOTE_TO_APPLY |
| INV-22 | Proforma sent to AP | HOLD · UNSUPPORTED_DOC_TYPE |
| KOS-0160.pdf | Clean PDF, CGST+SGST | APPROVE |
| KOS-0161.pdf | Scanned PDF (OCR) | HOLD · LOW_EXTRACTION_CONFIDENCE |
| BL-5651.pdf | Different layout, IGST intra-state | HOLD · GST_TYPE_MISMATCH |
| DE-0413.pdf | Inter-state IGST (correct), ₹6.8 lakh | HOLD · APPROVAL_LIMIT |
| sample_flaky.json | ERP timeout on attempt 1 | retry → APPROVE |

Assumptions behind these choices: **[docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)**.

---

## Evaluation (honest version)

`python -m evals.run_evals` runs three checks and exits non-zero on any false approval:

1. **Labelled set:** 22 seed invoices + 4 PDFs in received order on a fresh database → **26/26 decisions and primary reasons correct, 0 false approvals.** The rules were written with these labels in mind, so this is a regression gate, not a claim about unseen data.
2. **PDF extraction:** **32/32 fields** (number, GSTIN, date, PO, total, GST type, bank account, line count) across 4 PDFs, 3 layouts, 1 scanned.
3. **Adversarial held-out set:** 600 broken variants of invoices that should be approved (random price rises of 3–30%, over-billing beyond the PO, bank change, currency swap, tax-rate change, totals off by 1–5%, unknown PO, resend with reformatted number, GSTIN typo, injected "pre-approved" text). **0 approved.** By the rule of three, the false-approval rate for these failure types is below ~0.5% with 95% confidence.

A lesson from building it: the first version of the adversarial generator reported one "false approval". It was the test that was wrong (it had created a legitimate invoice still within the goods received). Fixed the generator, kept the story.

---

## Business impact

The dashboard's **Business impact** card (and `GET /impact`) turns runs into hours, cost per invoice and money stopped. Every assumption is an input: invoices per month, manual minutes per invoice, review minutes per exception, loaded hourly cost, target straight-through rate.

Example with the defaults (1,000 invoices/month, 12 manual minutes, 5 review minutes per exception, ₹600/hour, 70% straight-through): **200 → 25 AP hours a month (~175 saved, about 1 FTE), cost per invoice ₹120 → ₹15.** On the demo set it stopped about **₹3.3 lakh** of duplicate, fraud, blocked-vendor and invalid-GSTIN payments, plus ₹19,000 of price over-billing. External benchmark: Ardent Partners 2024 puts average AP cost at USD 9.40 per invoice and best-in-class at USD 2.78.

How I'd prove this at a real customer in 30 days: **[docs/ROLLOUT_30_DAYS.md](docs/ROLLOUT_30_DAYS.md)**.

---

## Hosting (free)

- **Render (free web service):** this repo has `render.yaml`. Render → New → Blueprint → choose the repo → Apply. One container serves UI + API. Free instances sleep after ~15 minutes idle (first load then takes ~30–60 s) and the demo database resets on redeploy; **Run demo seed** reloads it.
- **Any Docker host** (Hugging Face Spaces with Docker, Fly, Koyeb): the root `Dockerfile` listens on `$PORT`.
- **Instant temporary link from a laptop, no account:** run the app, then `cloudflared tunnel --url http://localhost:8000` prints a public `trycloudflare.com` link that works while the laptop is on.

---

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/invoices` | Ingest one invoice (JSON). Optional `Idempotency-Key` |
| POST | `/invoices/upload` | Upload `.pdf` (text or scanned), `.csv`, `.json`, `.txt` |
| POST | `/invoices/{id}/runs` | Re-run (same key = same run) |
| GET | `/runs`, `/runs/{id}` | History; run detail with events, reviews and the handoff |
| GET | `/runs/{id}/stream` | Live SSE stream: `step` events then `done` |
| GET | `/review-queue` | Pending holds |
| POST | `/runs/{id}/review` | `approve` / `reject` / `override` with note |
| GET | `/stats` | Dashboard KPIs |
| GET | `/impact` | Business impact with editable assumptions |
| GET | `/samples`, `/samples/{name}` | Sample inputs; download originals |
| POST | `/demo/sample/{name}` | Run a sample through the upload path |
| POST | `/demo/seed` | Reset and load the labelled demo set |

The same routes are also served under `/api/...` for the bundled UI.

## Layout
```
backend/app/ingest.py      PDF text + OCR extraction, CSV/JSON parsing, optional LLM fallback
backend/app/pipeline.py    the matching steps (pure functions)
backend/app/decision.py    rules engine, human handoff templates, explanations
backend/app/service.py     ingest, idempotency, job queue, retries, fail-closed, reviews, stats, impact
backend/app/main.py        FastAPI routes, SSE, worker, serves the bundled UI
backend/seed/              synthetic vendors, POs, GRNs, history, 22 labelled invoices, pdfs/ (4 PDFs + labels)
backend/tests/             39 tests
backend/evals/             labelled evals + adversarial held-out generator
frontend/                  Next.js dashboard, live run view, review queue (static export)
docs/                      ARCHITECTURE, ASSUMPTIONS, DEMO_RUNBOOK, ROLLOUT_30_DAYS
LOOM_SCRIPT.md             5-minute video script
```

## Not built yet (and what I'd do next)
- Calibrate the hand-set certainties on real labelled data; tune the gate per vendor.
- PDF viewer beside the fields with highlights in the review screen.
- Per-vendor tolerances, freight and early-payment discounts, TDS and e-invoice (IRN) checks.
- Posting approved invoices to an ERP and reading the record back.
- Postgres job claiming with `SELECT … FOR UPDATE SKIP LOCKED` and separate OCR workers (the Postgres path in `docker-compose.yml` has not been load-tested).
