# Assumptions and decisions log

The brief says: "treat ambiguity as part of the exercise. Make an assumption, note it somewhere you can reference in the live pitch, and move on." These are mine.

## The business
| # | Assumption | Why | What would change it |
|---|---|---|---|
| A1 | The buyer is an Indian mid-size company in Tamil Nadu (GSTIN 33AABCZ4321P1Z4, synthetic) with some foreign vendors (EUR, USD). | Makes GST rules (CGST+SGST vs IGST) meaningful and matches how Zamp works with Indian and global customers. | A different buyer state/country is one setting (`BUYER_GSTIN`). |
| A2 | Paying a bad invoice costs far more than holding a good one for a few minutes. | Fraud and duplicate payments are often unrecoverable; a hold costs a person a few minutes. | If the customer is penalised heavily for late payment, raise tolerances on trusted vendors. |
| A3 | Three-way match (invoice, PO, goods receipt) is the policy for PO-backed goods. | Standard AP control. | Services without GRNs would use two-way match plus requester confirmation. |
| A4 | Price tolerance: +2% and at most 5,000 per line; cumulative PO +1%. Under-billing passes. | Common AP policy. | Per vendor / category settings. |
| A5 | Approval matrix: above ₹5 lakh, a Finance Controller signs off even if everything matches. | Real companies have amount-based authority. | `APPROVAL_LIMIT_INR`. |
| A7 | Systems of record are read as snapshots with an "as of" time (vendor master, PO register, GRNs, rate contracts, GST registry, bank-change log, payment history). | In production each is an ERP / GSTN / bank integration; snapshots make the demo reproducible and make staleness visible. | Live connectors. |
| A8 | A vendor-master bank change younger than 30 days that was not verified by call-back holds the invoice, even if the invoice matches the master. | Fraudsters change the master first, then send a "matching" invoice. | `BANK_CHANGE_COOLDOWN_DAYS`. |
| A9 | A PO edited after the invoice date holds the invoice. | The PO may have been changed to fit the bill. | Allow edits by named roles with a reason code. |
| A10 | A PO price above the rate contract (beyond 2%) holds, even when the invoice matches the PO. | Two systems of record disagree; procurement decides which is right. | Per-category contract tolerance. |
| A11 | The Jev model is advisory: it may add a hold or a suggestion, never approve. Gates (duplicate 0.70, PO 0.80, field 0.80) are set from our labelled sets. | A model's probability is a signal, not an authority; see docs/EVALS.md. | Re-tune on each customer's data and each pinned model version. |
| A6 | FX rates (USD 84, EUR 92 to INR) are used **only** to apply the approval limit, never to compare invoice and PO amounts. | Comparing amounts across currencies needs an agreed rate, so a currency mismatch is held. | Daily rate feed. |

## The documents
| # | Assumption | Why |
|---|---|---|
| D1 | Dates on Indian invoices are day-first (10-10-2026 = 10 Oct 2026). | Indian convention. ISO dates are also read. |
| D2 | OCR reads are always confirmed by a person in v1, even when every check passes. | Prove accuracy on the customer's scans before trusting them. Loosen with data. |
| D3 | Invoice text is data, never instructions. | Prompt-injection defence. |
| D4 | An invoice without line items, number, date, vendor or total is held (MISSING_FIELD), never guessed. | The brief mentions missing critical information. |

## The build
| # | Decision | Alternative | Why |
|---|---|---|---|
| B1 | Plain Python + FastAPI + Next.js | n8n / Make / Zapier | Exact money maths (`Decimal`), idempotency, tests, evals. |
| B2 | Code decides; models only read/explain | LLM decides | Auditable, reproducible, can't be talked into paying. |
| B3 | Free, local OCR (tesseract) | Paid OCR APIs | Runs anywhere with no keys; swappable later. |
| B4 | All data synthetic | Real invoices | The FAQ allows it; no customer data risk. |
| B5 | One container (UI bundled into the API) | Two services | Free hosting with one service, no cross-origin issues. |
| B6 | Jev (typed questions with probabilities) for judgement calls, replay mode by default | Chat LLM with free-text answers | We gate on a number; replay keeps the demo free and reproducible. |
| B7 | Production features behind switches, all off by default | Always-on | The demo stays one free container; production turns them on. |

## Edge cases and why I picked them
The brief asks for 2–4 non-trivial edge cases. I built many, and I lead with these four because each shows a different kind of judgment:
1. **Bank details changed + urgency (BEC fraud):** everything matches except where the money goes. Shows that "matched" is not "safe".
2. **Split-PO overrun:** each invoice is fine alone; the third breaks the PO. Shows matching on history, not one document.
3. **Scanned PDF via OCR:** messy real input; read correctly, still confirmed by a person. Shows a deliberate trust policy.
4. **Wrong GST head (IGST on an intra-state supply):** India-specific; the invoice adds up but costs the buyer its tax credit.

Also covered: duplicate resent with a reformatted number (reject), reference-drift near-duplicate (hold, not reject), partial delivery billed correctly and beyond receipt, price variance in and beyond tolerance, currency mismatch, missing PO (inferred, confirm), PO not found, tax rate mismatch, blocked vendor, closed PO, totals don't add up, invalid GSTIN checksum, credit note, proforma, approval limit, transient ERP failure with retry, and fail-closed.
