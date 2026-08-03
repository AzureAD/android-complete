---
name: code-coverage-onboarding
description: >-
  Onboard a team/repo to automated code-coverage tracking and reporting. Use when a user wants
  to set up code coverage measurement, add coverage to CI/CD, track coverage over time
  (week-over-week), ingest coverage into Kusto/Azure Data Explorer, generate a coverage report
  or email, wire coverage into a weekly/scheduled pipeline, port an existing coverage setup to
  another team, or raise/increase coverage. Handles JaCoCo (Gradle/Maven/Android),
  Cobertura (.NET/Python/JS/Go), and LCOV (JS/TS/C++) reports, Azure DevOps and GitHub Actions
  pipelines, Kusto ingestion with cost control, report/email integration (including Azure
  Communication Services), and ranking uncovered classes/files to boost coverage. Triggers
  include "set up code coverage", "add coverage tracking", "coverage
  report", "coverage in the pipeline", "track coverage in Kusto", "onboard to code coverage",
  "weekly coverage email", "increase/raise code coverage", "where should I add tests",
  "code coverage skill".
---

# Code Coverage Onboarding

Set up end-to-end code-coverage tracking for a repo: generate coverage → parse to a normalized
schema → publish a summary → (scheduled) ingest into Kusto → surface a week-over-week trend in
a report/email. Portable across CI systems and languages. The core is `scripts/coverage_report.py`
plus a Kusto table; the pipeline wiring is adaptable to Azure DevOps or GitHub Actions.

## Bundled resources
- `scripts/coverage_report.py` — the portable engine. Four subcommands: `parse` (coverage
  report → normalized NDJSON), `report` (NDJSON → Markdown summary), `gaps` (report → ranked
  worklist of the least-covered classes/files, for raising coverage), `wow` (Kusto → HTML trend
  fragment with grouped tables + Overall rows). Parses JaCoCo XML, Cobertura XML, and LCOV.
  Stdlib only; runs on any image with Python 3.
- `references/coverage-generation.md` — make the build emit JaCoCo/Cobertura/LCOV (Gradle,
  Android AGP, Maven, .NET, Python, Node, C/C++, Go).
- `references/kusto-setup.md` — table schema, WIF/MI auth, ingestor grants, inline ingest, and
  the Sunday-only cost-control gate.
- `references/pipeline-integration.md` — Azure DevOps and GitHub Actions patterns, plus how to
  create a scheduled build if none exists.
- `references/report-integration.md` — inject the trend into an existing report, a Kusto
  dashboard, or a new ACS email; grouped-table configuration.
- `references/increasing-coverage.md` — the find-gaps → write-tests → gate loop for *raising*
  coverage. Read when the user wants to boost numbers, not just track them.

## Workflow

This skill has five capabilities. Confirm scope with the user, then implement in order. Steps
1–4 are the tracking pipeline; step 5 (raising coverage) is optional and builds on them.

### Step 0 — Scope the setup
Ask (only what's not already clear):
- **Build tool & language** → determines report format (JaCoCo / Cobertura / LCOV).
- **CI system** (Azure DevOps / GitHub Actions / other).
- **Is there a recurring/scheduled build?** If not, one is needed (see step 2).
- **Track history in Kusto?** Default **yes**; a team can opt for summary-artifact-only.
- **Existing reporting** to inject into, or start fresh?

### Step 1 — Generate coverage
Get the build to emit JaCoCo, Cobertura, or LCOV output. See `references/coverage-generation.md`.
**Reuse an existing test run** rather than adding a second one — re-running tests just for
coverage doubles CI time. Gate coverage behind a flag so normal builds aren't slowed.

### Step 2 — Parse & summarize in CI
Add steps that run `coverage_report.py parse` (per module → shared NDJSON) then `report`
(NDJSON → Markdown summary), and publish both as an artifact. These run every build and are
**non-fatal** (warn, don't fail). See `references/pipeline-integration.md`. If the team has no
scheduled build, create a minimal weekly one whose job is: test + parse + ingest.

### Step 3 — Ingest into Kusto (default on, cost-gated)
Create the `CodeCoverageData` table, grant the CI identity Ingestor, and add an ingest step
that converts NDJSON → CSV and POSTs an inline `.ingest`. **Only ingest on the scheduled
reporting run** (e.g. Sunday) with an operator override parameter — see the gate in
`references/kusto-setup.md`. Ingestion **should fail loudly** if it can't write (unlike the
reporting steps). Skip this whole step only if the team declined history.

### Step 4 — Surface the trend
Render the week-over-week trend with `coverage_report.py wow` and get it in front of the team.
Priority: (1) inject the HTML fragment into an existing recurring report, (2) a Kusto
dashboard, (3) a new ACS email. See `references/report-integration.md`. Use `--group-file` to
split modules into labeled tables, each with an Overall row.

### Step 5 — Increasing coverage (integrated, optional)
When the user wants to *raise* coverage (not just track it), run the find-gaps loop: use
`coverage_report.py gaps` on the latest report to rank the least-covered classes/files, write
tests for the highest-value targets, re-run to confirm, then lock gains in with a no-regression
gate. See `references/increasing-coverage.md`. This is a separate effort from steps 1–4 — do it
once a baseline is visible so progress can be verified.

## Key conventions (carry these when porting)
- **Normalized schema** is fixed: `Date, Repo, Module, Metric, Covered, Missed, Percentage,
  CommitId, BuildId, Branch`. The KQL and ingest CSV depend on these names.
- **Reporting is non-fatal; ingestion is fatal.** The report/email must still ship on a
  coverage hiccup, but a silent ingest failure that greens the run is a bug.
- **WoW uses calendar semantics**, not a rolling 7-day window: the baseline is last week's
  start-of-week run (`startofweek()`), so same-week manual re-runs don't skew the delta.
- **Overall rows sum Covered/Total** and derive the delta from summed prior Covered/Missed —
  never average per-module percentages.
- **Ingest on a schedule only** to control Kusto cost; keep cheap parse/publish per build.

## Validating the script
`coverage_report.py` is stdlib-only. Sanity-check after any edit:
```bash
python3 -m py_compile scripts/coverage_report.py
python3 scripts/coverage_report.py parse --input sample.xml --repo r --module m --out rows.ndjson
python3 scripts/coverage_report.py report --input rows.ndjson --metric LINE --goal 75
python3 scripts/coverage_report.py gaps --input sample.xml --top 10
```
The `wow` subcommand needs a live Kusto table + token; test rendering logic with mock rows if
no cluster is available.
