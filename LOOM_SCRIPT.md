# 5-minute demo video: word-for-word script + click list

Say it in your own voice; the words are a guide, not a cage. Time yourself once before recording. Target 4:50.
Repo: https://github.com/selvin-paul-raj/zamp-ps1-invoice

## Before you press record (2 minutes)
1. Start the app: `docker run -p 8000:8000 zamp-ps1-invoice` **or** `cd backend && PIPELINE_STEP_DELAY_MS=350 uvicorn app.main:app --port 8000` (after `cp -r ../frontend/out static` once, see README). Jev runs in **replay** mode by default (recorded real answers), so no key is needed.
2. Open http://localhost:8000. Browser zoom 110%. Close other tabs and notifications.
3. Click **Run demo seed** once and wait until the table stops changing (about 10 seconds). This resets everything.
4. Open `backend/seed/pdfs/KOS-0160_kaveri_clean.pdf` in a second tab (to show the real PDF for 3 seconds).
5. Have `docs/EVALS.md` open on GitHub in a third tab (the challenge-set table, section 3d).
6. Loom: screen + small camera bubble. Mic check.

---

## 0:00 – 0:20 · The problem
**Show:** the dashboard.

> "Hi, I'm Selvin. I picked PS-1: invoice to PO decision. An AP team gets hundreds of vendor PDFs a month. Most are fine. The expensive ones are the exceptions: over-billing, duplicates, wrong GST, and fake bank-change requests. So I built a process that reads each invoice, checks it against the company's systems of record, and then approves it, rejects it, or holds it with one clear question for a person."

## 0:20 – 1:10 · Happy path from a real PDF, with evidence
**Click:** PDF tab for 3 seconds → back → **Try a real input** → **KOS-0160**.

> "This is a normal vendor PDF. Each step you see is streamed from the server as it runs. It reads the PDF, checks the numbers add up, matches the vendor, finds the PO, compares price and quantity with the PO and what the warehouse received, checks GST, looks for duplicates, and checks the approval limit."

**Scroll to:** the **Evidence** panel.

> "Approved. And here's the part I care most about: every decision cites its evidence. Each row says which system I checked, the vendor master, the PO, the goods receipt, the rate contract, the GST registry, which record, what that system says versus what the invoice says, the rule, and even the line on the PDF the value was read from. An auditor can ask 'who said this was OK?' and the answer is a row, not a guess."

## 1:10 – 2:10 · Edge cases: fraud, sources that disagree, duplicate
**Click:** dashboard → row **ACM-2340**.

> "Price, quantity and PO all match. But the bank account differs from the vendor master and the note says 'urgent, new account'. Classic payment fraud. Held, no email draft; call the vendor on the number we already have."

**Click:** dashboard → row **CCPL-2290**.

> "This one is sneakier. The bank account matches the vendor master, so a simple check passes it. But the bank-change log says the master itself was changed four days ago through self-service, never verified, and the payment history says we've never paid that account. Three systems, one question. Held."

**Click:** dashboard → row **DE/26-27/0431**.

> "Here everything matches, but the PO register says the PO was edited after the invoice date. Someone may have changed the PO to fit the bill. Held for the buyer."

**Click:** dashboard → row **ACM/2301**.

> "And the first invoice sent again with the number reformatted: rejected as a duplicate."

## 2:10 – 3:00 · Where AI helps: the Jev model, switchable and advisory
**Click:** dashboard → row **PP-7815**. Point at the **Jev model** card.

> "No PO number on this invoice, and two open POs could fit. Rules can't pick, so it's held. I added TypeSafe's Jev model for narrow judgement calls like this. Jev answers typed questions with a probability. Here it says PO-1019, the Chennai warehouse one, at 98%, and it gave the same answer when I asked again with the options reversed. But look: the invoice is still on hold. The model suggests; a person confirms. A model can never approve in this design."

> "In this demo Jev runs in replay mode: these are real answers I recorded from the live Jev API, so the demo works with no key. Switching to live is one setting."

**Click:** GitHub tab → `docs/EVALS.md` section 3d.

> "I didn't just plug it in, I measured it. On a challenge set of re-sent bills with a slightly changed price, the rules alone approved three. With Jev, zero, and it added no false holds. I also found where Jev fails: when two POs are identical, it still picked one at about 95%. So I added a guard, and those now go to a person."

## 3:00 – 3:30 · Human in the loop
**Click:** **Review queue** → card **KOS-0150**.

> "Every card comes pre-investigated with one question and who should answer it. I'll confirm the PO with a note and approve. Approving needs a note, and it's in the audit trail: who, when and why. In production mode only the approver role can release money."

**Type:** `Confirmed PO-1008 with requester` → **Approve**.

## 3:30 – 3:55 · Business impact
**Click:** dashboard, scroll to **Business impact**.

> "With a thousand invoices a month, 12 minutes each by hand, and 70% going straight through, that's about 175 hours a month back. Every number is an input you can change to the customer's own baseline."

## 3:55 – 4:35 · How it's built, and how I know it works
**Show:** README or the terminal with `python -m evals.run_evals` output.

> "And it's all one command: `python -m evals.harness` runs every test, the labelled decisions, a red team of about 600 injection attacks and an LLM judge that I validated against human labels first, and the build fails if a single bad invoice would get paid."

> "One design rule: AI reads, code decides, people own the exceptions. The decision is plain code, so it's auditable and an invoice can't talk it into paying. Testing: every labelled case is correct, 31 out of 31, 32 of 32 fields read right from four PDFs including a scanned one, and 600 broken variants of good invoices. Zero approved. It's built production-ready but switchable: database migrations, a durable queue with retries and a dead-letter queue, OpenTelemetry tracing including the model calls, API keys with roles, rate limiting, PII redaction before any model call. All off by default so the free demo stays simple."

## 4:35 – 4:50 · Close
> "At a Zamp customer, week one would be replaying last month's invoices read-only, measuring the real straight-through rate, and re-running this same eval on their data before trusting any gate. The repo and full design are in the link. Thanks for watching."

---

## Honest lines to keep (do not overclaim)
- Jev is **advisory** and runs in **replay mode** in the demo. It was **live-tested on our eval sets** on 12 Oct 2026 (model jev-1.13.0). The eval sets are small and synthetic.
- The Postgres path (migrations, queue locking) is written and tested on SQLite, not run on a real Postgres here.

## If something goes wrong while recording
- A run looks stuck: refresh the page. The run continues on the server.
- Table is messy: click **Run demo seed** again (it resets everything), wait 10 seconds.
- Do not re-record for small stumbles. The brief says no editing is needed.
