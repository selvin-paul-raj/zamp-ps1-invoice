# How I would roll this out at a customer in 30 days

Written for an AP lead or CFO. Plain words. Mirrors the safe sequence Zamp itself recommends: start with read-and-propose, add write access action by action once supervised results meet the bar.

| Week | Goal | What happens | Exit check |
|---|---|---|---|
| **1. Learn and baseline** | Understand the real process | Shadow two AP clerks. Collect last month's invoices, PO and GRN exports, vendor master, approval matrix, tolerances. Write down every exception type and who owns it. Measure today: cost per invoice, cycle time, touchless rate, exception rate. | Process map signed off by the AP lead. Baseline numbers agreed. |
| **2. Replay (read-only)** | Prove it on their data, risk-free | Run last month's invoices through the process in shadow mode. Compare each decision with what the team actually did. Review every disagreement together. Tune tolerances per vendor, add their layouts. | Zero false approvals on the replay. Hold reasons make sense to the AP lead. |
| **3. Supervised live** | Real invoices, a person confirms everything | New invoices flow in. The process proposes; AP confirms every decision for one vendor group. Track reviewer agreement, time per review, and corrections. | Reviewer agreement above the agreed bar (for example 98%) for the clean group. |
| **4. Switch on auto-approve for the clean group** | Hours back | Auto-approve only for that group within tolerance and under the approval limit. Everything else still goes to the queue with one question. Weekly report: hours saved, money stopped, hold reasons by vendor. | Signed-off weekly report. Plan for the next vendor group. |

**What I would watch every week:** false approvals (must stay 0), touchless rate, hold rate by reason and vendor, median time to resolve a hold, reviewer overrides, OCR share.

**What could go wrong and the plan:**
- *Messy master data* (duplicate vendors, stale bank details): clean up in week 1; the process will surface it anyway.
- *Too many holds at first*: expected; tune tolerances per vendor from the replay, not by guessing.
- *Trust*: never switch on writes the team has not watched work. The audit trail and the one-question handoff make that easy.
