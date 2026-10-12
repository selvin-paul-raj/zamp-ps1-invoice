# Invoice → PO decision process (Zamp ASA case study, PS-1)

Repo: https://github.com/selvin-paul-raj/zamp-ps1-invoice (private)

**What it does, in one breath:** a vendor invoice arrives as a PDF (clean or scanned), CSV or JSON. The process reads it, matches it to the purchase order, the goods receipt and the vendor master, checks for duplicates, payment fraud and Indian GST errors, and ends with **APPROVE**, **HOLD with one clear question for a person**, or **REJECT**. You watch every step happen live, and every decision says why.

**Design rule:** *AI reads, code decides, people own the exceptions.* The approve/reject decision is plain, tested code, so it is auditable and an invoice cannot talk it into paying. Models only read, match and explain; a model can never approve; an uncertain answer goes to a person.

| | |
|---|---|
| Live run view | Each step streams to the browser as it runs (Server-Sent Events) |
| Dashboard | Run history, decisions, confidence, reasons, KPIs, exception reasons, business impact |
| Review queue | Every hold arrives pre-investigated: one question, who should answer, next step, draft vendor email |
| Real inputs | Text PDFs, **scanned PDFs (OCR)**, CSV, JSON; 4 sample PDFs in 3 vendor layouts |
| **Evidence panel** | Every check cites its source of truth: which system (vendor master, PO register, goods receipts, rate contracts, GST registry, bank-change log, payment history, prior invoices), which record and field, what the source says vs what the invoice says, the rule, the snapshot time, and the PDF line it was read from |
| **Jev model (TypeSafe), switchable** | Advisory only: flags likely duplicates, suggests the PO when it is missing, double-checks OCR fields, with probabilities and calibrated gates. **Replay mode by default** (recorded real Jev answers, no key needed); live with one env var |
| Evidence it works | 79 tests (59 + 20 guardrail tests); **31/31 labelled cases** (27 invoices + 4 PDFs); 32/32 PDF fields; **0 false approvals in 600 adversarial variants**; Jev live-tested on our eval sets; **LLM-as-judge validated against a human-labelled gold set**; **0 of 489 prompt-injection attacks changed a decision**; all in one command with a regression gate (`python -m evals.harness`, [docs/EVALS.md](docs/EVALS.md), [docs/SECURITY.md](docs/SECURITY.md)) |

> All data is synthetic. No API key or paid service is needed to run it.

---

## Run it

**Option A: one container (recommended, same as hosted)**
```bash
docker build -t zamp-ps1-invoice .
docker run -p 8000:8000 zamp-ps1-invoice
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

**Then:** click **Run demo seed** (loads 27 labelled invoices, ~10 s), and use **Try a real input** to run the sample PDFs one click at a time, or **Upload invoice** for your own file.

**Tests and evals:**
```bash
cd backend && python -m evals.harness      # everything + regression gate, offline (scorecard in backend/evals/reports/)
cd backend && pytest -q && python -m evals.run_evals && python -m evals.model_evals   # the pieces on their own
```

**Turn Jev on live** (optional; the demo uses recorded answers):
```bash
export TYPESAFE_API_KEY=...     # or JEV_API_KEY. From console.typesafe.ai. Never commit it.
export JEV_MODE=live            # off | replay (default) | live.  JEV_ENABLED=1 also means live.
```

---

## What happens to one invoice

`intake & extraction (+ injection scan) → arithmetic → vendor match (+ GSTIN checksum, GST registry status, blocked vendor, bank account vs master vs bank-change log vs payment history) → PO lookup (stated, inferred, or Jev-suggested; PO edited after the invoice?) → currency → 3-way match (price vs PO vs rate contract, quantity vs goods received, PO history) → tax & GST → duplicates (+ Jev judgement) → approval policy → decision (cites evidence) → explanation`

Credit notes take their own path: matched to the original invoice and applied, never paid out.

Full system design with diagrams (architecture, data model, sequence, rules engine, human-in-the-loop, retries, security, scaling, deployment, trade-offs, roadmap): **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

### Sources of truth and evidence
Each check writes an **evidence record**: source system, record id, field, expected (what the system of record says) vs actual (what the invoice says), rule id, the source's snapshot time, and the document page/line where the value was read. The decision cites the records behind each reason. Evidence is stored with the run (audit trail), returned by `GET /runs/{id}/evidence`, and shown in the **Evidence** panel. Four **freshness/conflict** checks hold an invoice when sources disagree: PO edited after the invoice date, vendor bank account changed recently without call-back, GSTIN cancelled in the registry, PO price above the rate contract. Details: [docs/ARCHITECTURE.md §3a](docs/ARCHITECTURE.md).

### The Jev model (advisory, switchable)
[Jev](https://docs.typesafe.ai/models) answers typed questions with probabilities. Three jobs, each behind a gate tuned on our data: **duplicate judgement** (p ≥ 0.70 adds a hold), **choose the PO** when it is missing (p ≥ 0.80 and the same answer with options reversed → a suggestion; the invoice stays held), **field check** for OCR reads (p < 0.80 flags the field). A model answer can only add caution, never approve. PII (bank accounts, PAN, IFSC, e-mail, phone) is replaced by placeholders before any call. Every call is logged (question, answer, probability, latency, tokens, cost, model version). Measured against rules-only in **[docs/EVALS.md](docs/EVALS.md)**: on a challenge set, rules alone approved 3 disguised re-submissions; with Jev, 0, and no false holds added.

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
| INV-23 | Invoice = PO, but PO is 8% above the rate contract | HOLD · CONTRACT_PRICE_CONFLICT |
| INV-24 | GSTIN valid, but cancelled in the GST registry | HOLD · GSTIN_NOT_ACTIVE |
| INV-25 | Remit-to = vendor master, but master bank changed 4 days ago, unverified | HOLD · VENDOR_BANK_RECENTLY_CHANGED |
| INV-26 | PO edited after the invoice date | HOLD · PO_CHANGED_AFTER_INVOICE |
| INV-27 | No PO, two open POs fit (Jev suggests the right one; still held) | HOLD · PO_MISSING |
| sample_flaky.json | ERP timeout on attempt 1 | retry → APPROVE |

Assumptions behind these choices: **[docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)**.

---

## Evaluation (honest version)

`python -m evals.run_evals` runs three checks and exits non-zero on any false approval:

1. **Labelled set:** 27 seed invoices + 4 PDFs in received order on a fresh database → **31/31 decisions and primary reasons correct, 0 false approvals** (same with Jev off, replay or live). The rules were written with these labels in mind, so this is a regression gate, not a claim about unseen data.
2. **PDF extraction:** **32/32 fields** (number, GSTIN, date, PO, total, GST type, bank account, line count) across 4 PDFs, 3 layouts, 1 scanned.
3. **Adversarial held-out set:** 600 broken variants of invoices that should be approved (random price rises of 3–30%, over-billing beyond the PO, bank change, currency swap, tax-rate change, totals off by 1–5%, unknown PO, resend with reformatted number, GSTIN typo, injected "pre-approved" text). **0 approved.** By the rule of three, the false-approval rate for these failure types is below ~0.5% with 95% confidence.

`python -m evals.model_evals` measures the Jev roles against rules-only on four labelled sets (84 duplicate pairs, 15 PO-choice cases, 72 field checks, 6 end-to-end challenge invoices): precision/recall, false-hold rate, calibration (ECE + reliability plot), latency and cost. The answers were recorded from the real Jev API (`jev-1.13.0`) on 12 Oct 2026 and replay exactly. Results and honest limits: **[docs/EVALS.md](docs/EVALS.md)**.

**One command for all of it:** `python -m evals.harness` runs the tests, the labelled decisions (per-class precision / recall / F1, confusion matrix), PDF fields, the adversarial set, the Jev role evals, an **LLM-as-judge** with its own validation, a **security red team** and **robustness** suite, and cost / latency, then applies a regression gate (`backend/evals/gate.yaml`) and writes a scorecard (`backend/evals/reports/scorecard.md` / `.html`). It runs offline from recorded real answers, so CI needs no secrets. Current result: **gate PASS**.

- **LLM-as-judge, only where there is no exact answer.** Rules decide pay / hold and are scored against labels. The judge (Jev, typed yes/no rubric questions with probabilities, model version pinned in versioned rubric files) grades only the text a person reads: the reviewer question, the vendor e-mail draft, the explanation and the evidence panel. It was validated against a 69-item human-labelled gold set (32 deliberately bad outputs, all caught; item-level kappa 0.66); criteria that did not hold up are marked advisory and are not gated. Exact checks run beside it (every number / id in a generated text must be in the evidence: 100%).
- **Security and guardrails.** Input (normalise unicode tricks, scan every field incl. hidden PDF text, metadata, OCR, file names, CSV cells; PII redaction; upload limits), output (schema validation, groundedness, no vendor e-mail for bank changes) and action (a model may only add a hold or a suggestion; allow-listed calls only). Red team: 489 pipeline attacks, 30 against Jev, 105 against the judge, **0 succeeded, 0 leaks**; 38/38 robustness cases. Mapped to the OWASP Top 10 for LLM Apps (2025) and AP fraud risks in [docs/SECURITY.md](docs/SECURITY.md).
- **What the harness caught (and we fixed):** negative lines were approved; a re-submission with the original timestamp was paid twice; concurrent deliveries raised errors; vendor notes could talk Jev out of a duplicate (6/30 paid → 0/30); the approval-limit question named no amount.

A lesson from building it: the first version of the adversarial generator reported one "false approval". It was the test that was wrong (it had created a legitimate invoice still within the goods received). Fixed the generator, kept the story.

---

## Business impact

The dashboard's **Business impact** card (and `GET /impact`) turns runs into hours, cost per invoice and money stopped. Every assumption is an input: invoices per month, manual minutes per invoice, review minutes per exception, loaded hourly cost, target straight-through rate.

Example with the defaults (1,000 invoices/month, 12 manual minutes, 5 review minutes per exception, ₹600/hour, 70% straight-through): **200 → 25 AP hours a month (~175 saved, about 1 FTE), cost per invoice ₹120 → ₹15.** After the demo seed plus the four sample PDFs it stopped about **₹5.1 lakh** of duplicate, fraud, blocked-vendor and invalid or cancelled-GSTIN payments, plus ₹19,000 of price over-billing. External benchmark: Ardent Partners 2024 puts average AP cost at USD 9.40 per invoice and best-in-class at USD 2.78.

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
| GET | `/runs/{id}/evidence` | Evidence records + model calls behind a decision |
| GET | `/sources` | Systems of record and their snapshot times |
| GET | `/jobs?status=dead` · POST `/jobs/{id}/requeue` | Queue depth, dead-letter queue, requeue (admin) |
| GET | `/health` · `/ready` | Liveness · readiness (database, worker heartbeat, queue, switches) |

The same routes are also served under `/api/...` for the bundled UI.

## Layout
```
backend/app/ingest.py      PDF text + OCR extraction (with page/line of each field), CSV/JSON parsing, optional LLM fallback
backend/app/pipeline.py    the matching steps; every check writes evidence
backend/app/evidence.py    systems of record + the evidence record
backend/app/jev.py         Jev (TypeSafe) adapter: live HTTP client, replay store, three gated roles
backend/app/safety.py      PII redaction before model calls, prompt-injection scan
backend/app/decision.py    rules engine, human handoff templates, explanations
backend/app/service.py     ingest, idempotency, durable job queue (leases, dead-letter), reviews, evidence, stats, impact
backend/app/main.py        FastAPI routes, auth/RBAC, rate limit, SSE, health/ready, worker, serves the bundled UI
backend/app/auth.py        API keys, roles, rate limiter      backend/app/telemetry.py  OpenTelemetry + JSON logs
backend/app/worker.py      standalone worker (WORKER_MODE=external)
backend/migrations/        Alembic migrations (DB_MIGRATE=alembic)
backend/seed/              synthetic vendors, POs, GRNs, contracts, GST registry, bank-change log, payments, history,
                           27 labelled invoices, pdfs/ (4 PDFs + labels), jev_replay.json (recorded real Jev answers)
backend/tests/             79 tests (incl. guardrails)
backend/evals/             harness (one command + gate), labelled evals, adversarial, model_evals (Jev vs rules-only),
                           judge/ (LLM-as-judge, gold set, meta-eval), rubrics/, security/ (red team, robustness), reports/
frontend/                  Next.js dashboard, live run view, review queue (static export)
docs/                      ARCHITECTURE, EVALS, SECURITY, ASSUMPTIONS, DEMO_RUNBOOK, ROLLOUT_30_DAYS
LOOM_SCRIPT.md             5-minute video script
```

## Production switches (off by default)
`DB_MIGRATE=alembic` (versioned migrations, Postgres-ready) · `WORKER_MODE=external` + `python -m app.worker` (durable Postgres queue with `SKIP LOCKED`, leases, retries, dead-letter) · `AUTH_MODE=apikey` + `API_KEYS` (roles; only the approver releases money) · `RATE_LIMIT_PER_MIN` · `OTEL_ENABLED=1` (OpenTelemetry traces, GenAI conventions for model calls; works with Langfuse's OTel endpoint) · `LOG_FORMAT=json` · `JEV_MODE`. Full list in `.env.example`. The free Render deploy runs with the defaults.

## Not built yet (and what I'd do next)
- Calibrate the hand-set rule certainties on real labelled data (the Jev probabilities are already measured for calibration); tune gates per vendor and per model version.
- Live connectors for the systems of record (ERP, GSTN via a GSP, bank statements) instead of seeded snapshots.
- PDF viewer beside the fields with highlights in the review screen.
- Per-vendor tolerances, freight and early-payment discounts, TDS and e-invoice (IRN) checks.
- Posting approved invoices to an ERP and reading the record back.
- Separate OCR workers. The Postgres path (`SKIP LOCKED` claiming, Alembic migrations) is written and the migrations are tested on SQLite, but it has **not been run against a real Postgres or load-tested** in this sandbox.
