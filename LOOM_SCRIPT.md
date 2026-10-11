# 5-minute Loom: demo script (outline)

Prep: backend running with `PIPELINE_STEP_DELAY_MS=350`, UI at localhost:3000, review queue empty (`POST /demo/seed` resets everything). Close other tabs. Keep the browser zoom at 110%.

## 0:00–0:25 · The problem (talking head or title slide)
- "AP teams match every vendor invoice against the PO, the goods receipt and the vendor master before paying. Most invoices are clean. The expensive ones are the exceptions: over-billing, duplicates, and fraud."
- "I built a live process that decides **Approve / Hold / Reject** with visible reasons. It sends only the exceptions to a human."

## 0:25–1:15 · Happy path, live
- Dashboard → **Run demo seed**. Rows appear and decisions fill in as the worker processes them.
- Open **ACM-2301** (Acme, INV-01). Walk through the streamed steps: intake → arithmetic → vendor match (by GSTIN) → PO-1001 → currency → 3-way table (qty invoiced vs received vs ordered, price vs PO) → tax → duplicates → **APPROVE 100%**.
- One line: "Each step is a Server-Sent Event, so this is the actual run, not an animation."

## 1:15–2:45 · Edge cases (choose 3; keep each about 30 s)
1. **Bank details changed (ACM-2340):** "Everything matches, but the remit-to account differs from the vendor master and the note says 'urgent, new account'. That's the classic BEC pattern. It is held at 99% even though the price and quantity are perfect. Accounts are masked."
2. **Duplicate (ACM/2301 by email → REJECT):** the same invoice resent through a different channel with a reformatted number. "Normalised invoice-number match against what we already approved."
   - Then **5521-OPS → HOLD**: a different number, but the same amount and the same digits as a *paid* invoice. "Reference drift is how duplicates slip through ERPs. Here it isn't certain, so it goes to a human instead of being rejected."
3. **Split-PO overrun (ACM-2330):** each invoice looks fine on its own, but the third pushes the cumulative quantity to 1,050 against a 1,000 kg PO. "The check runs on the PO's history, not on one document."
- Optional: **PP-7790 (partial delivery)**, **KOS-0150 (no PO → inferred PO-1008, held for confirmation)**, **NS-2026-120 (currency)**.

## 2:45–3:30 · Human in the loop
- Review queue → **KOS-0150**: type the note "Confirmed PO-1008 with requester" → **Approve**. "Approving a hold needs a note, and it goes into the audit trail."
- Back on the dashboard: review shows *resolved* and the effective decision is APPROVE.

## 3:30–4:10 · Reliability
- Upload `seed/sample_flaky.json`. The live view shows "Transient failure, retrying", then attempt 2 → APPROVE. "Retries with backoff. If a run keeps failing it **fails closed** to HOLD, never approve."
- Mention idempotency: "Re-posting the same invoice returns the same run, and a re-run with the same key is a no-op."

## 4:10–4:45 · Evals + design
- Terminal: `pytest -q` (24 passed) and `python -m evals.run_evals`: 17/17, **0 false approvals**, confusion matrix. "This is a regression gate on labelled cases. The next step is a held-out set."
- "Design: **code takes the decision** (auditable, injection-proof; there's a test for that). Models plug in behind an adapter only to extract, break ties or explain. False approvals are the metric I optimise."

## 4:45–5:00 · Close
- "What I'd do next: real PDF extraction with per-field confidence, per-vendor tolerances, and a held-out eval. Repo link is in the email."

_Personalise the wording in your own voice and re-time it with a stopwatch before recording._
