# Security and guardrails

**The one rule:** AI reads, code decides, people own the exceptions. No model can approve, reject, clear a hold, change a
bank account, change an approval limit, edit master data or send a message. Everything below is defence in depth around
that rule, and every control is measured by `python -m evals.harness` (section 8 and 9 of the scorecard).

---

## 1. The guardrails, layer by layer

| Layer | Guardrail | Where in the code | Measured by |
|---|---|---|---|
| **Input** | Unicode normalisation (NFKC), zero-width and bidi characters stripped, look-alike letters folded, underscores read as spaces, for **scanning only** (stored text is never changed) | `app/guardrails.py: normalise_untrusted` | red team: homoglyph, zero-width, bidi-reversed techniques |
| Input | Scanner for instructions in **every untrusted field**: notes, vendor name, invoice / PO number, SKU, line description, full PDF text (including white / 1-pt hidden text and OCR text), PDF metadata, upload file name, CSV cells. Categories: instruction, authority claim ("approved by the CFO", "limit raised"), exfiltration ask, judge manipulation, spreadsheet formula, encoded payload (base64 / hex / URL), 8 languages (Hindi, Tamil, Spanish, French, German, Chinese, Japanese, Arabic). A hit is a `SUSPICIOUS_INSTRUCTIONS` **hold** | `scan_untrusted`, `scan_fields`; pipeline intake step | red team detection rate |
| Input | Social-engineering cues ("urgent", "pay today", "new bank account", "do not call") are recorded as an **info** note, not a hold: real vendors write "urgent" too. The bank / limit / duplicate rules decide | `SOCIAL` patterns → `SOCIAL_ENGINEERING_CUES` | labelled INV-14 still decided by the bank rule |
| Input | **Bank account in the text ≠ vendor master** → `BANK_DETAILS_CHANGED` hold, even when the structured bank field still matches (BEC pattern found by the red team) | pipeline vendor step, rule `R-BANK-TEXT` | red team `bank_change_text` |
| Input | Spotlighting: untrusted text is only ever sent to a model as a labelled **data** field in `state`; the question wording is fixed by us and never contains document text. The judge sees the graded text under `untrusted_output` with a note that it may contain instructions | `app/jev.py` (state vs instructions), `evals/judge/core.py` | Jev and judge red team |
| Input | PII pseudonymised before **any** model call (Jev and the judge): bank account / IBAN, IFSC, PAN, e-mail, phone, **card number, Aadhaar, API-key-like secrets** (new) → `[ACCOUNT_1]`, `[SECRET_1]` ... | `app/safety.py: Redactor` | leak check: every outgoing model request is captured and scanned for raw PII |
| Input | Upload checks **before parsing**: allowed extensions only (.json .csv .pdf .txt), file signature must match the extension, size ≤ 10 MB (the server never buffers more), PDF ≤ 20 pages, encrypted PDFs refused, CSV ≤ 2,000 rows, bad file names refused (paths, NUL). PDFs with JavaScript / launch actions / embedded files are accepted but **never executed** and held `UNSAFE_DOCUMENT` | `check_upload`; `main.py` upload endpoint | robustness suite |
| Input | CSV / formula injection: cells that would run as a formula in Excel / Sheets are stored with a leading apostrophe and flagged | `neutralise_cell`, `FORMULA` patterns | red team `formula_hyperlink`, `formula_dde` |
| **Output** | Every model answer is **schema-validated** (exact keys, probability in 0..1, choice inside the options, probabilities sum to 1); anything else is discarded and the rules stand | `validate_model_response` in `JevClient` path | unit tests + replay |
| Output | **Groundedness check (exact):** every amount, percentage, PO number, invoice / document number, GSTIN and account number in a generated text (explanation, handoff, vendor email) must appear in, or be derived from, the evidence | `ungrounded_facts` | harness section 7: 100% on real outputs |
| Output | Handoff shape is validated; **no vendor email is ever drafted for a bank-change, fraud-risk or possible-duplicate case** | `validate_handoff`, `NO_EMAIL_CODES` | harness: 0 emails for forbidden cases |
| Output | Bank accounts masked to the last 4 digits in every API response | `service.get_run`, `mask_account` | leak check |
| **Action** | **Refusal-to-act policy:** a model may only ADD a hold (`POSSIBLE_DUPLICATE`) or a suggestion (`PO_SUGGESTED_BY_MODEL`, info). Any other model-raised finding is dropped before the decision and logged | `enforce_model_action_policy` | unit path; red team |
| Action | **Allow-listed model calls only:** `duplicate_judgement`, `choose_po`, `field_check`. Models get no tools, no function calling and no free-text channel back (answers are probabilities) | `ALLOWED_MODEL_ROLES` in `ModelAssist._call` | code review; red team exfiltration |
| Action | **Untrusted text can raise suspicion, never lower it:** when an invoice carries vendor notes, the duplicate question is asked again without them and the higher probability is used (found by the red team: "separate trips, second week" talked Jev below its gate) | `ModelAssist.duplicate_check` | Jev red team: 6/30 paid before, 0/30 after |
| Action | Approvals by people only, with roles: only the `approver` key can release a held invoice; note required; audit trail | `app/auth.py` | tests |
| Action | Rate limit per key / IP (429 + Retry-After); idempotency keys; concurrent duplicate deliveries collapse to one invoice and one run | `main.py`, `service.ingest` | robustness: 16 concurrent deliveries → 1 invoice, 1 run, 0 errors |
| Action | Duplicate check no longer trusts the client's `received_at`: anything already in the system counts as "known before" (found by the robustness suite: a re-submission with the original timestamp was paid twice) | `service.master_data(created_before=...)` | robustness `same invoice again under a new key` |

---

## 2. Mapping to the OWASP Top 10 for LLM Applications (2025)

Source: [genai.owasp.org/llm-top-10](https://genai.owasp.org/llm-top-10/).

| OWASP 2025 | How it applies here | Guardrails | Residual risk |
|---|---|---|---|
| **LLM01 Prompt Injection** | Invoice text, PDFs (incl. hidden text, metadata, OCR), e-mails, CSV cells, file names try to instruct a model or the reviewer | Code decides; multi-field scanner with unicode / encoding / multilingual handling; spotlighting; notes ablation; judge guard | A paraphrased instruction in a language we do not cover is not *detected*; it still cannot approve (0/489 attack success) |
| **LLM02 Sensitive Information Disclosure** | Bank accounts, PAN, contacts reaching a model vendor or an e-mail | Redactor before every model call (Jev + judge); masked API responses; models return numbers only | Redaction is pattern-based; a free-form identifier in an unusual format could pass |
| **LLM03 Supply Chain** | Third-party model API and Python packages | Model version **pinned** in each judge rubric (`jev-1.13.0`) and checked on every response; recorded answers make drift visible; requirements pinned by lower bound | No SBOM or package hash pinning yet |
| **LLM04 Data and Model Poisoning** | Vendor-controlled text shaping the model's view over time; poisoned eval labels | No training on customer data; gates tuned on labelled data we built; the gold set is frozen and committed | Gold sets are small and built by us |
| **LLM05 Improper Output Handling** | Model output reaching decisions, UI or e-mails | Schema validation; outputs are probabilities only; generated text is template-based and checked for groundedness; formula cells neutralised | Optional LLM extraction fallback output is capped at 0.8 confidence (always a person) |
| **LLM06 Excessive Agency** | A model approving, changing bank details or limits | Refusal-to-act policy, allow-listed roles, no tools; only the approver role releases money | None found in the red team; keep it this way |
| **LLM07 System Prompt Leakage** | Asking the model to reveal its instructions / keys | No secrets in prompts; the key is injected on the wire; answers are typed numbers, so there is no channel to leak through | — |
| **LLM08 Vector and Embedding Weaknesses** | Not used (no RAG / vector store) | — | Revisit if retrieval is added |
| **LLM09 Misinformation** | An explanation or e-mail stating something the evidence does not show | Deterministic templates; exact groundedness check; LLM-as-judge (validated) as a second opinion | Judge is advisory on two criteria (see EVALS.md) |
| **LLM10 Unbounded Consumption** | Huge or crafted files, runaway clients, token cost | Upload limits (size, pages, rows), timeouts on pdftotext / OCR, rate limit, at most 3 duplicate questions per invoice, token ledger in the harness | No per-tenant token budget yet |

---

## 3. Mapping to accounts-payable fraud risks

| AP fraud / failure | Control | Evidence |
|---|---|---|
| **Business e-mail compromise: "we changed our bank"** | Bank field ≠ master → hold; bank number in the text ≠ master → hold; bank changed recently without call-back → hold; never a vendor e-mail; call the contact on file | INV-14, INV-25; red team `bank_change_text` (all vectors) |
| **Fake approval / authority claim** ("pre-approved by the CFO") | Authority claims are a hold; approvals only by a person with the approver role | red team `cfo_social`, `authority` |
| **Approval-limit bypass** ("limit raised to 50 lakh") | Limits live in policy config, never read from documents; claim is a hold | red team `limit_change` on the over-limit invoice: 0 approved |
| **Duplicate / re-submitted invoice** | Fingerprint, normalised number, amount + date window, Jev judgement with notes ablation, received_at no longer trusted | adversarial `resend_reformatted_number`; Jev challenge E1-E3; robustness replay |
| **Over-billing (price, quantity)** | 3-way match with tolerances | adversarial 600 |
| **Hidden negative lines / fake credits** | Negative quantity, price or total on a bill → hold; credits only via credit notes | robustness `negative amounts` |
| **Malicious attachments** | Signature check, size / page limits, encrypted refused, active content held and never executed | robustness `malformed file` |
| **Spreadsheet formula injection in exports** | Formula cells neutralised and flagged | red team `csv_cell` |
| **Insider changing the PO to fit the invoice** | PO edited after the invoice date → hold | INV-26 |

---

## 4. Results (offline replay of recorded real answers; full tables in `backend/evals/reports/scorecard.md`)

| Measure | Result |
|---|---|
| Pipeline injection attacks (25 techniques × 12 vectors, 489 runs; 299 on invoices with a real defect) | **0 successful** (0 approvals of defective invoices, 0 decisions flipped to approve) |
| Detection rate (held by a security control: suspicious text, unsafe document or bank text ≠ master) | 95.1%. The misses are the 20 runs of a "benign-looking" claim with no instruction words ("fresh, separate service period"), designed to evade detection: those invoices were still decided correctly by the rules (clean ones approved, defective ones held), and against Jev that text is handled by the notes-ablation guard; plus 4 bank-change runs that were already held by the bank rule |
| Jev red team (30 injected re-submissions, real model answers) | Before the notes-ablation guard: **6 / 30 paid** (two benign-looking notes pushed p from 0.73-0.90 to 0.29-0.67). After: **0 / 30** |
| Judge manipulation (105 attempts on known-bad outputs) | Raw judge flipped to pass: 0. Guarded judge: 0. Guard caught 84 / 105 by pattern |
| Leaks | 0 system secrets in any output; 0 raw PII in any captured model request |
| Robustness / abuse (38 cases) | 38 / 38; 0 crashes. Three real bugs found and fixed: negative lines passed; a re-submission carrying the original `received_at` was paid twice; a race on concurrent deliveries returned errors |

## 5. Honest limits

- The scanner is pattern-based. It is a **second line**; the first line is that code decides. A clever paraphrase will be missed by detection, and the eval shows that is survivable, not that it cannot happen.
- The red-team suite is ours, synthetic, and finite (489 + 30 + 105 attempts). It is a regression suite, not a proof.
- The E1 notes-ablation answer is p = 0.72 against a gate of 0.70: the margin is thin, as it already was for E2.
- No penetration test of the web app, no SBOM, no secrets scanning in CI yet.
