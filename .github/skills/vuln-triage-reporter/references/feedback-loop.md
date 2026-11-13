<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->
# Post-Shift Feedback & Weekly Skill Impact

## Contents
- [Scope and privacy](#scope-and-privacy)
- [Validate the intake](#validate-the-intake)
- [Calculate defensible metrics](#calculate-defensible-metrics)
- [Prioritize and implement improvements](#prioritize-and-implement-improvements)
- [Weekly manager report](#weekly-manager-report)
- [Close the loop](#close-the-loop)

## Scope and privacy

Use this workflow for skill adoption, usefulness and improvement reporting, not to classify or fix
individual vulnerabilities. Confirm audience, reporting period, supplied feedback, allowed tracking
sources and output format. Never expand into incident, session-history or Teams searches without
permission. No product-source preflight is needed for feedback-only analysis.

Keep raw exports, respondent identities, quotes, case references, metrics and reports under
`VULN_TRIAGE_WORKSPACE` (default `~/vuln-triage-workspace`), outside git. Use
`skill-feedback/<report-date>/` for these artifacts, separate from per-finding shift reports.
Commit only generalized workflow improvements; retain row-to-change traceability privately.

## Validate the intake

1. Read the actual workbook/export schema and all supplied responses. Preserve the original file.
   Omit identity columns from aggregate reports; use response IDs or source row numbers for provenance.
2. State the sample size, submission dates, known shift dates, and missing dates. Submission date is
   not the shift window. Do not infer distinct cases, unique users, or a weekly trend from row counts.
3. Require nonnegative integer counts and assisted cases <= received cases. Verify verdict-category
   totals against the stated denominator. Preserve missing values as unknown, not zero.
4. Establish whether outcome categories are exclusive or overlapping. If totals exceed assisted cases,
   flag the inconsistency; withhold closure/success rates rather than silently normalizing or deleting
   the row. Unaffected fields may still be reported with their own validation caveat.
5. Confirm the rating scale from the survey definition. If the export lacks it, report the numeric
   ratings without inventing a maximum, percentage, or favorable-rating threshold.
6. Record time-band labels exactly. If export encoding damages a separator, document any normalization
   and the resulting numeric endpoints. Ambiguous bands remain unquantified.

## Calculate defensible metrics

| Metric | Definition | Required qualifier |
|---|---|---|
| Sample case coverage | Sum(assisted) / Sum(received) | Respondent-reported coverage within this sample, not team adoption |
| Respondent usage | Responses with assisted > 0 / valid responses | Responses, not necessarily unique people; distinguish from case coverage |
| Reported verdict quality | Correct/minor, material correction, incorrect, unvalidated, no verdict | State counts and validated denominator; these are survey reports, not an independent accuracy audit |
| Helpfulness | Per-response ratings; mean only for a confirmed consistent numeric scale | State sample size and known scale; no extrapolation |
| Estimated time saved | Sum of lower endpoints to sum of upper endpoints for compatible total-per-shift bands | Self-reported range, not observed savings or ROI |
| Elapsed runtime | Actual recorded run/stage timestamps, or separately labelled recollection | Runs != cases; report overruns without claiming measured latency improvement |
| Improvement progress | Feedback theme -> change -> validation -> delivery state | Edited/validated != committed/merged/deployed |

Do not turn time bands into a precise mean, multiply total-per-shift savings by case counts, or infer
cost savings without a measured baseline and cost basis. Deduplicate overlapping shift/case records
before claiming unique workload. If overlap is unknown, say so and label totals as reported instances.
Report denominators alongside percentages. Missing outcome/contract evidence is not a negative result.

For week-over-week reporting, require comparable windows and definitions; otherwise publish a baseline
snapshot and mark trend unavailable. Keep survey opinions distinct from execution-tracker/ADO facts.
The finding-status script in Step 7 reports remediation, not skill adoption.

## Prioritize and implement improvements

Keep a private traceability table:

| Source row/theme | Observable gap | Existing safeguard | Proposed change | Priority | Verification | Delivery state |
|---|---|---|---|---|---|---|
| <response ID / theme> | <specific failure> | <current instruction or automation> | <generalized fix> | <risk/impact rationale> | <check or pending live trial> | <proposed / edited / validated / merged / adopted> |

Preserve valued outputs; improve the gaps without duplicating existing instructions. Prioritize wrong
or premature decisions before convenience. Check these recurring boundaries:
- Unmerged/reverted work is a candidate; owner confirmation precedes a landing recommendation.
- Risk assessment and prior-decision comparison precede remediation; intake and approval remain gates.
- Canonical developer documentation proves a contract, not runtime enforcement.
- Runtime budgets include queue/history/reconciliation/reporting; never omit independent verification
  to meet an ETA. Measure subsequent runs before claiming performance improved.

Use a confirmed isolated worktree for edits and a correctly-parented engineering-efficiency work item.
Do not modify product code or launch a triage run as an implicit "validation" of feedback changes.
Validate modified automation with synthetic, identity-free fixtures and existing checks. Run the
public-repository safety scanner. Commit/push only with permission; any requested PR is a draft.

## Weekly manager report

Produce a private self-contained HTML report plus a paste-ready update, if requested. Use these stable
sections; do not send mail or install a recurring schedule without approval:

1. **Dated summary:** usefulness signal, confidence/limitations, progress since the last report.
2. **Evidence scoreboard:** sample/denominators, case coverage, reported verdict corrections, estimated
   savings and rating scale. Keep delivery-state counts separate from survey metrics.
3. **Blocked / at risk:** inconsistent data, absent baseline, unverified behavior, or delivery blockers.
4. **Improvement progress:** preserve/changed/pending, source theme, check result and actual delivery
   state. Use verified work-item identities/owners; an unknown ETA is "Needs ETA", not a fabricated date.
5. **Next measurement:** missing fields, next-shift runtime evidence, independent reviewer validation.
6. **Provenance and limitations:** input filename/rows, submission vs shift dates, normalization,
   missing sources and sample bias.

Use the workstream-status-report conventions when available, but adapt to the approved feedback inputs.
Do not fetch meetings or chats just to fill its usual three-stream structure. A small survey snapshot
is not proof of organization-wide adoption, causal time savings, or the effectiveness of today's edits.

## Close the loop

Check every displayed metric against the source, verify links and artifact paths, and give the user the
absolute report path and worktree location. Explain which changes are local versus delivered.
At the next shift, collect actual advertised/elapsed timing, component and depth mode, reviewer verdict
corrections, and whether each changed gate was exercised. Retain the same metric definitions.

Recommend survey improvements when needed: explicit shift dates; declared exclusive/overlapping
outcomes; totals validated against assisted cases; a defined rating scale; run count distinct from case
count; advertised/actual duration; and baseline/manual plus review time. Do not silently edit the form
or invent missing historical data. Treat faster execution or fewer corrections as unverified until
comparable new evidence exists.
