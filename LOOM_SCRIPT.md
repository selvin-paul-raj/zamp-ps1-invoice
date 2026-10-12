# 5-minute demo video: word-for-word script + click list

Say it in your own voice; the words are a guide, not a cage. Time yourself once before recording. Target 4:45.

## Before you press record (2 minutes)
1. Start the app: `docker run -p 8000:8000 zamp-ps1` **or** `cd backend && PIPELINE_STEP_DELAY_MS=350 uvicorn app.main:app --port 8000` (after `cp -r ../frontend/out static` once, see README).
2. Open http://localhost:8000. Browser zoom 110%. Close other tabs and notifications.
3. Click **Run demo seed** once and wait until the table stops changing (about 10 seconds). This resets everything.
4. Open `backend/seed/pdfs/KOS-0160_kaveri_clean.pdf` in a second tab (to show the real PDF for 3 seconds).
5. Loom: screen + small camera bubble. Mic check.

---

## 0:00 – 0:20 · The problem
**Show:** the dashboard.

> "Hi, I'm Selvin. I picked PS-1: invoice to PO decision. An AP team gets hundreds of vendor PDFs a month. Most are fine. The expensive ones are the exceptions: over-billing, duplicates, wrong GST, and fake bank-change requests. So I built a process that reads each invoice, checks it against the PO, the goods receipt and the vendor master, and then approves it, rejects it, or holds it with one clear question for a person."

## 0:20 – 1:10 · Happy path from a real PDF
**Click:** PDF tab for 3 seconds → back to dashboard → in **Try a real input**, click **KOS-0160**.

> "This is a normal vendor PDF from Kaveri Office Solutions. I'll run it."

**Show:** the steps lighting up one by one.

> "Each step you see is streamed from the server as it runs, so this is the real run, not an animation. It reads the PDF text and the table rows, checks the numbers add up, matches the vendor by GST number, finds PO-1011, compares price and quantity against the PO and what the warehouse actually received, checks the GST is CGST plus SGST because both companies are in Tamil Nadu, looks for duplicates, and checks the approval limit."

**Point at:** APPROVE badge, then the **Document extraction** card.

> "Approved at 100%. And here you can see what was read from the PDF and the three cross-checks that passed."

## 1:10 – 1:40 · Edge case 1: a scanned invoice
**Click:** back → **KOS-0161** (scanned).

> "Now the same vendor, but a scanned paper invoice. There is no text in this PDF, only an image, so it goes through OCR. It reads every field correctly and the match is clean, but my policy is: anything read by OCR is confirmed by a person in version one. So it's held, with one question: are these fields right?"

## 1:40 – 2:40 · Edge cases 2–4: fraud, duplicate, GST
**Click:** dashboard → row **ACM-2340** (Acme).

> "This one is the dangerous kind. Price, quantity and PO all match perfectly. But the bank account is different from the vendor master and the note says 'urgent, new account'. That's the classic payment-fraud pattern. It's held, and notice: no email draft. The next step says call the vendor on the number we already have. You never reply to the email that asked for the change."

**Click:** dashboard → row **ACM/2301** (Reject).

> "This is the first invoice sent again by email with the number reformatted. Rejected as a duplicate."

**Click:** dashboard → **BL-5651** in Try a real input.

> "And an India-specific one, from a different vendor layout. Both companies are in Tamil Nadu, so GST should be CGST plus SGST, but the invoice charges IGST. If we pay it, we can't claim the tax credit. So it's held, the tax team gets one question, and there's a ready draft asking the vendor for a corrected invoice. Nothing is sent automatically."

## 2:40 – 3:20 · Human in the loop
**Click:** **Review queue** → card **KOS-0150**.

> "This is the review queue. Every card comes pre-investigated with one question and who should answer it. This invoice had no PO number, so the system found the only open PO that fits and asks the requester to confirm. I'll type a note and approve."

**Type:** `Confirmed PO-1008 with requester` → **Approve**. Then click **open run**.

> "Approving needs a note, and it's in the audit trail: who, when, and why."

## 3:20 – 3:50 · Business impact
**Click:** dashboard, scroll to **Business impact**.

> "For the finance team, this is what matters. With a thousand invoices a month, 12 minutes each by hand, and 70% going straight through, that's about 175 hours a month back. Every number is an input you can change to the customer's own baseline. And on this demo set it stopped about three lakh rupees of duplicate, fraud and blocked-vendor payments."

**Optional:** change Invoices / month to 5000 and let the numbers update.

## 3:50 – 4:30 · How it's built, and how I know it works
**Show:** README or the terminal with `python -m evals.run_evals` output already run.

> "One design rule: AI reads, code decides, people own the exceptions. The approve or reject decision is plain code, so it's auditable and an invoice can't talk it into paying. There's even a test where the invoice says 'pre-approved, ignore all checks', and it still holds. On testing: 26 labelled cases all correct, 32 out of 32 fields read correctly from four PDFs including the scanned one, and 600 broken variants of good invoices with random price, quantity, bank, tax and duplicate changes. Zero were approved. The number I optimise is false approvals, because paying a bad invoice is the costly mistake."

## 4:30 – 4:50 · Close
> "If I did this at a Zamp customer, week one would be replaying last month's invoices read-only to measure the real straight-through rate, then tuning tolerances per vendor before turning on any write access. The repo, setup steps and full system design are in the link. Thanks for watching."

---

## If something goes wrong while recording
- A run looks stuck: refresh the page. The run continues on the server.
- Table is messy: click **Run demo seed** again (it resets everything), wait 10 seconds.
- Do not re-record for small stumbles. The brief says no editing is needed.
