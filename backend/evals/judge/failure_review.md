# Hand review of judge failures on the real outputs (12 Oct 2026)

Every time the judge failed a real pipeline output, I re-read that output against its case evidence and decided who was
right. This is how two criteria were found to be unreliable on real outputs even though they looked fine on the first gold
set, and how two real weaknesses in the handoff text were found and fixed.

## Run 1 (before fixes)

| Output | Criterion failed | Who was right | Action |
|---|---|---|---|
| Handoff INV-20, DE-0413 (approval limit) | specific, correct, points_to_evidence | **Judge.** "Everything matched. Do you approve payment of this amount?" named neither the amount nor the limit | Fixed: generic templates now put the finding's own facts first, and every handoff names the record to open ("Evidence to open: ...") |
| Handoff INV-10, INV-11, INV-17, INV-18, INV-22, INV-27 | points_to_evidence / actionable | **Judge** (mostly): the question did not name the PO / figures / record | Same fix |
| Evidence panel INV-15 | every_finding_cited | **Judge.** The PO_MISSING citation had no record id | Fixed: the citation now names "open POs of V005 (none fit)" |
| Explanation INV-01..04, KOS-0160 (approvals) | faithful_decision, grounded | **Human.** The approval text matches the decision and the evidence; the judge fails every approval explanation (likely because there is no "main reason" to compare) | Added as hard cases to the gold set (v2): both criteria now show as **advisory** |
| Evidence panel INV-07, INV-09, INV-10, INV-16, INV-21 | citations_support | **Human.** Each citation is the right record and shows the problem (or, for routing findings, the match that justifies them) | Added as hard cases (v2): criterion now **advisory** |

## Run 2 (after fixes; the committed scorecard)

| Output | Criterion failed | Who was right |
|---|---|---|
| Handoff INV-20, DE-0413 | specific (advisory), correct, points_to_evidence | **Human.** The question now states the amount and the INR 5,00,000 limit and names the policy record; the judge still marks "do you approve payment?" as not specific. Left as is (not tuned to please the judge) |
| Handoff INV-26 | correct | **Human.** The question matches the PO-revision evidence exactly |
| Handoff INV-10, INV-19, INV-25, INV-27 | actionable (advisory) | **Human** in 4 of 4 |
| Evidence panels INV-09, INV-10, INV-15, INV-16 | citations_support (advisory) | **Human** |
| Explanations INV-01..04 | faithful_decision, grounded (advisory) | **Human** |

Conclusion: on real outputs the judge is useful as a **flag for a person to look at**, not as an oracle. Only criteria that
clear every bar on the gold set gate the build, the gate threshold is 0.85, and the exact checks (groundedness of numbers
and ids, citation completeness, no email for bank-change cases) run beside it and are the ones that must be 100%.
