# Weekly Status Report Template (manager tracking)

A **concise, email-ready** weekly snapshot of the security-triage findings the on-call is tracking.
This is **NOT** the research report — it is a single compact table for a manager to glance at and
forward. No evidence, no file:line, no audit trail.

> Generate it with [`scripts/build_status_report.py`](../scripts/build_status_report.py), which reads
> `classifications.csv`, auto-discovers a persisted `work-item-map.json` (IcM → AB#) beside it, reads the
> **execution tracker** (`EXECUTION-TRACKER.md`) for real remediation status, and (with `--auto-token`)
> falls back to live ADO work-item state. Output is a self-contained HTML table that pastes cleanly into
> Outlook.

## What it contains (and what it does NOT)

**Include — quick-glance only:**

| Column | Source | Notes |
|--------|--------|-------|
| **IcM** | finding | Linked to the IcM incident. |
| **Bug** | finding | A **one-line** plain-language description (the short title — NOT the full vuln writeup). |
| **Severity** | our tier | Critical / Important / Moderate / Low (our verdict, not the filed one). |
| **Status** | exec tracker → ADO | Mapped to: **Not started · In progress · Blocked · In review · Complete · Out of scope** (see mapping below). |
| **Work Item** | ADO | `AB#NNNN` linked to the PBI/bug. Blank if no work item created yet. |
| **Updated** | ADO | Date the work item last changed (so stale items are visible). |

> **Owner (engineer vs intern) is intentionally NOT a column** — the linked work item already shows the
> assignee. Keep the weekly table lean.

**Do NOT include:** owner/assignee, research evidence, file:line citations, defense-in-depth sweep,
adversarial pass, the "Searches Run" audit, remediation specs, eng-day estimates, confidence, or
external-validation prose. Those live on the work item or in the research report. This report is a
**status tracker**, not an investigation.

## Status mapping (execution tracker / ADO state → report status)

Keep the report's status vocabulary small and manager-friendly. **Status comes from the execution tracker
first** (`EXECUTION-TRACKER.md`, the source of truth for what's actually been *done*), then falls back to
live ADO state:

| Report status | Exec-tracker status | ADO `System.State` (fallback) | Meaning |
|---------------|---------------------|-------------------------------|---------|
| **Not started** | `NOT STARTED` | New, Approved, Proposed, (no work item yet) | Triaged, not yet picked up. |
| **In progress** | `IN PROGRESS` · `IMPLEMENTED (local)` · `PUSHED (no PR)` | Committed, Active, In Progress, Doing | Being worked. |
| **Blocked** | `BLOCKED` | tagged `Blocked`, or State=`On Hold` | Waiting on a dependency / external input (e.g. the ⚗ external-validation answer). |
| **In review** | `PR OPEN` | In Review, Code Review, Resolved (pending verify) | Fix up for review / PR open. |
| **Complete** | `MERGED` | Done, Closed, Completed | Shipped / verified. |
| **Out of scope** | `OUT OF SCOPE (intern)` | — | Intern-eligible; assigned to an intern who hasn't started yet. Tracked for completeness, sorted last. The report adds a one-line note explaining it. |

> A finding with an open **external-validation** question (⚗ in the research report) should show **Blocked**
> here if the severity/fix decision is actually waiting on that answer — otherwise In progress. The status
> report does not explain *why* it's blocked; that detail stays in the research report.

## Layout

- **One-line header:** report title + window (e.g. `Security Triage — Weekly Status · 2026-06-18 → 2026-06-25`)
  and a tiny count line (`8 findings · 3 in progress · 1 blocked · 4 not started`).
- **One table**, sorted by Status (active work first) then Severity (highest first).
- **Plain HTML** (inline styles only) so it survives an Outlook paste. No external CSS, no images.
- Fits on one screen — if there are many findings, it's still just rows; no nested detail.

## Example (rendered shape)

> **Security Triage — Weekly Status · 2026-06-18 → 2026-06-25**
> _8 findings · 1 in review · 3 not started · 4 out of scope_

| IcM | Bug | Sev | Status | Work Item | Updated |
|-----|-----|-----|--------|-----------|---------|
| NNNNNN | Intent-scheme parser differential | Moderate | In review | AB#NNNN | 06-23 |
| NNNNNN | Unvalidated app_link → arbitrary ACTION_VIEW launch | Important | Not started | AB#NNNN | 06-23 |
| NNNNNN | Plaintext TOTP seeds logged on JWE failure | Important | Not started | AB#NNNN | 06-23 |
| NNNNNN | activateMfa deep-link CSRF/SSRF + token exfil | Moderate | Out of scope | AB#NNNN | 06-23 |
| NNNNNN | NGC PendingIntent collision (session swap) | Low | Out of scope | AB#NNNN | 06-23 |

_Out of scope (4): intern-eligible items are out of scope for now — assigned to an intern who has not started yet._

## Cadence

Run it once a week (e.g. end of the on-call shift, Wednesday) and paste the table into the manager's
tracking email. Persist the IcM→work-item map **once** as `work-item-map.json` next to
`classifications.csv` (`{ "<IcM id>": <AB#> }`; both IcMs of a combined PBI point at the same id) — the
script auto-discovers it. Then the weekly refresh is a single command:

```
python scripts/build_status_report.py <run>/classifications.csv --auto-token \
    --out <run>/weekly-status.html --window "<Wed> -> <Wed>"
```

`--auto-token` reads live ADO work-item state via `az` (must be logged in), so re-running just refreshes
the statuses — no manual bookkeeping. The map lives in the **private workspace** (it pairs IcM ids with
work-item ids), never in the skill repo.
