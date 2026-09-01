# Authenticator playbook — weekly OCE telemetry report

> Invoked by [`SKILL.md`](../../SKILL.md) in `authapp` or `both` mode. **Read the router first** —
> it owns the reporting-window resolution, output paths, mode routing, and the shared hard rules
> (UTF-8 trap, never-carry-a-number-forward, never bulk-regex-edit HTML, never-commit-the-report).
> This file owns everything Authenticator-specific.

**Cluster:** `https://idsharedeus2.eastus2.kusto.windows.net` ·
**Database:** `d496be22d62a46b0a3cf67ea2e736fd8` ·
**Time column:** `EventDate` (MVs) · **Device columns:** `sum(XxxDCount)` — matches the dashboard, but over-counts; see AB#3739409

> ### ⚠️ Do not carry Broker conventions into this report
> The Broker playbook's first four hard rules are **actively wrong** here. There are no HLL
> columns (use `sum(SucceededDCount)` — but read the device-count caveat below), no TDigest
> sketches, no `Merge*` helper functions,
> and views are referenced by bare name, not `materialized_view('…')`. The slicing space is 3
> dimensions, not 7. If a query you are about to run looks like a Broker query, stop.
> **Before writing any KQL, read [`../docs/authapp-kusto-cheatsheet.md`](../docs/authapp-kusto-cheatsheet.md).**

> ### ⚠️ Device counts are relative, not absolute — AB#3739409
> `sum(…DCount)` sums a per-(hour × dimension) distinct count, so it re-counts any device seen in
> more than one cell — measured **+22.6%** on Passkey Registration and **3.93×** on Entra MFA
> PN+CFA. The skill keeps this idiom deliberately, to stay numerically consistent with the Livesite
> Dashboard (which uses it in 78 places); the fix belongs upstream in the MVs. Use device columns
> for **ranking, direction and week-over-week movement** — never quote one as an absolute
> population ("N devices affected") or as a per-device rate denominator without flagging it.
> Rates, event counts and deltas come from `countif` columns and are unaffected.

## What this report is about

The Broker report is organised around **error codes** — a flat, high-volume failure taxonomy.
The Authenticator report is organised around **scenario funnels**: 13 user-visible flows
(Passkey, Entra MFA, Entra PSI, MSA NGC/SA — registration, authentication, and push-notification
variants).

> ### ⚠️ There are TWO funnel shapes. Do not apply one to the other.
>
> The 13 scenarios do **not** share a single outcome model. Assuming they do is a real, observed
> failure mode: a run mapped push-notification outcomes onto the registration vocabulary by
> guesswork (`Approved`→"success", `Cancelled`→"unknown") and reported invented rates.
>
> | Shape | Applies to | Outcome model |
> |---|---|---|
> | **Outcome funnel** | the 9 registration / authentication scenarios | `Initiated → Succeeded / Failed`, with `Unknown = max(0, Initiated − (Succeeded + Failed))` |
> | **Reaction funnel** | the 4 `… PN+CFA` push-notification scenarios | two-stage: `Initiated → Reacted`, then `Reacted` split by `FinalResult` |
>
> **The PN scenarios have no `Succeeded`, no `Failed`, and no `Unknown` column.** Their
> `FinalResult` domain is `Approved · Denied · Error · Cancelled · ""` — verified live across all
> four families. `Approved`/`Denied`/`Error` are always present; `Cancelled` and `""` appear in
> some families only (MFA has both, PSI has `""`, the MSA pair had neither in a 14-day window), so
> never write a `case()` that assumes a fixed set. `pn-completion-wow.kql` deliberately keeps only
> `{Approved, Denied, Error}`; `Cancelled` and empty together are ~0.1% of MFA reacted volume and
> are not reactions that complete an auth. The canonical rates are:
>
> ```text
> CompletionRate = (Approved + Denied) / Initiated     # share of notifications acted on
> ApprovedRate   = Approved / (Approved+Denied+Error)  # share of REACTIONS, not of Initiated
> DeniedRate     = Denied   / (Approved+Denied+Error)
> ErrorRate      = Error    / (Approved+Denied+Error)
> ```
>
> **Denied is not a failure.** A user declining a push is a correct, healthy outcome — often the
> security-positive one. Never fold `Denied` into a failure rate, and never headline a rising
> `DeniedRate` as a regression without saying what it means.
>
> A drop in `CompletionRate` with a flat `ApprovedRate` means notifications are not reaching or not
> being acted on (delivery / lifecycle) — **not** that auth is failing. Say which one in the report.

That difference drives everything. A finding here is "the Entra PSI registration funnel lost 3
points of success rate and the loss landed in Unknown, concentrated on Android 14 Samsung
devices" — not "error code X rose". Write the report in those terms.

### The canonical 13

These labels are the contract between the queries, the template, and the validator. Use them
**verbatim** — the validator fails a report where any one of them is missing from the scoreboard,
and renaming one here without renaming it in the query pack silently drops a row.

| # | Scenario | Source |
|---|---|---|
| 1 | `Passkey WebAuthN Registration` | `scenario-outcomes-wow.kql` |
| 2 | `Passkey InApp Registration` | `scenario-outcomes-wow.kql` |
| 3 | `Passkey WebAuthN Authentication` | `scenario-outcomes-wow.kql` |
| 4 | `Entra MFA Registration (QR)` | `scenario-outcomes-wow.kql` |
| 5 | `Entra MFA Registration (No-QR)` | `scenario-outcomes-wow.kql` (union of Manual + Non-QR MVs) |
| 6 | `Entra PSI Registration` | `scenario-outcomes-wow.kql` |
| 7 | `Entra PSI PN Registration` | `scenario-outcomes-wow.kql` (initiate column is `RegistrationStarted`) |
| 8 | `MSA NGC Registration` | `scenario-outcomes-wow.kql` |
| 9 | `MSA SA Registration` | `scenario-outcomes-wow.kql` |
| 10 | `Entra MFA PN+CFA` | `pn-completion-wow.kql` |
| 11 | `Entra PSI PN+CFA` | `pn-completion-wow.kql` |
| 12 | `MSA NGC PN+CFA` | `pn-completion-wow.kql` (`IsNGC == "true"`) |
| 13 | `MSA SA PN+CFA` | `pn-completion-wow.kql` (`IsNGC == "false"`) |

Rows 12 and 13 read the **same** MV pair, split only by `IsNGC`. That filter has to be applied on
both sides of the init↔results join or the two funnels quietly contaminate each other.

## Authenticator asset map

| File | Purpose |
|---|---|
| [`authapp-report-template.html`](../templates/authapp-report-template.html) | Canonical layout — a realistic populated example report. **Edit in place**; do not restyle. Shares the Broker report's CSS/sparkline system. |
| [`authapp-kusto-cheatsheet.md`](../docs/authapp-kusto-cheatsheet.md) | Scenario→MV catalog, column names, the Unknown metric, Errors views, `brokeroperations` traps, volume floor |
| [`queries/authapp/`](../queries/authapp/) | The eight canonical KQL templates — see [`queries/authapp/README.md`](../queries/authapp/README.md) |
| [`bucket-trends.js`](../scripts/bucket-trends.js) | 60-day trend bucketing. Run with `--key=scenario`. |
| [`classify-novelty.js`](../scripts/classify-novelty.js) | Splits movers into NEW / ACCELERATING / ONGOING / VOLATILE / RECOVERY / IMPROVING / STABLE against their own baseline. Run with `--family-sep=none`. **This is what makes Section 3 readable** — and it is the noise gate: its `attention` set (`NEW` + `ACCELERATING`), plus at most 2 wins, is all that renders visibly with charts. |
| [`agg.js`](../scripts/agg.js) | Per-key per-dim top-N rollup with WoW deltas |
| [`find-suspect-prs.ps1`](../scripts/find-suspect-prs.ps1) | `git log -S` / `--grep` — run with `-Repos authenticator` |
| [`bootstrap-report.ps1`](../scripts/bootstrap-report.ps1) | Bootstrap the report. Run with `-App authapp`. |
| [`validate-report.ps1`](../scripts/validate-report.ps1) | Pre-publish validator. Run with `-App authapp`. |
| [`run-kql.ps1`](../scripts/run-kql.ps1) | Direct-REST Kusto helper. Run with `-App authapp`. Signature is `-Query <kql-string> -Out <path.json>` (**not** `-File`/`-OutFile`). |
| [`fetch-appcenter-crashes.js`](../../../release-monitoring-report/assets/scripts/fetch-appcenter-crashes.js) | Crash clusters — **App Center only, not Kusto** |

---

## Required sections (in order)

1. **Top-line health KPIs** — telemetry-active devices, total scenario initiates, overall success
   rate, overall **Unknown rate**, Broker-API success rate, crashes per 1,000 devices. WoW delta
   on each, inline SVG sparkline on each.

2. **Scenario scoreboard** — one table, **all 13 scenarios, every week, no exceptions**. Even
   scenarios that did not move get a row; a silent scenario disappearing from the table is
   indistinguishable from a scenario that was never checked. Columns: scenario, initiated,
   success rate, Δ success (pts), failure rate, unknown rate, Δ unknown (pts), devices, 8-week
   sparkline, status pill. Rows under the volume floor carry a `low-volume` tag.

   > **The 4 push-notification rows cannot fill the success/failure/unknown columns — that is
   > expected, not a gap.** PN has no success/failure/Unknown model (see the outcome-model warning
   > above; `Denied` is a healthy outcome, so a "failure rate" would be a lie). For the 4 PN rows
   > put the **completion rate** in the success-rate column, the **error rate** in the failure
   > column, and a literal `n/a` in the unknown-rate and Δ-unknown cells. Do **not** leave the cells
   > blank (blank reads as "not measured") and do **not** synthesise an Unknown bucket for them.
   > Footnote the table once: *"PN scenarios report completion/error; they have no Unknown state."*

3. **Needs attention** — callouts using the `.item` flat-row pattern, ordered by **novelty, not
   volume** (see Step 4b). Render the classifier's `attention` set (`NEW` + `ACCELERATING`) at the
   top level, plus **at most 2** wins, and nothing else; `ONGOING` goes in a collapsed fold.
   Budget **≤ 8 visible rows total, wins included** (`validate-report.ps1` check 17 warns above it
   and counts every visible `.item` row in the section, wins among them).
   - **🔴 New this week** — classifier label `NEW`: a flat baseline that just stepped, and that
     cleared the volume floor. If nothing is `NEW`, say *"nothing new this week"* — do not backfill
     with `ACCELERATING` or `ONGOING` scenarios.
   - **🟠 Getting worse** — label `ACCELERATING`: already degraded **and still sliding**. This is
     the only multi-week bucket that stays visible, because "is it getting worse?" is the one
     question a known issue can still answer usefully. Delete the callout if the set is empty.

     > **When the classifier and the headline delta disagree, keep the row here and show both.**
     > The classifier's "not falling" gate runs on **complete Sun–Sat calendar weeks**; the headline
     > percentage-point delta runs on the **rolling 7-day** window. Different bases, and they
     > legitimately disagree. That is not a reason to demote the row, rename the group, or hedge the
     > heading — keep it exactly **"Getting worse"** and resolve it *in the row body*: *"Down 2.1 pp
     > across the last three complete weeks; the rolling window shows +0.4 pp as the slide flattens.
     > Still 3.8 pp below its own 60-day median."* The sparkline settles it visually. Do **not**
     > invent a "needs verification" group.
   - **🔵 Ongoing / known** — label `ONGOING`: degraded but level or easing. **Collapse into a
     `<details class="fold">`** with a one-line summary ("N scenarios still below baseline, none
     accelerating") and each row's `weeksElevated`. Still in the report, no longer competing with
     the finding.
   - **🟢 Wins** — scenarios that improved, with the cause where identifiable. **Cap at 2 rows, and
     they count against the ≤ 8 visible-row budget.** A win is worth showing; a list of wins is padding.
   - `VOLATILE` / `RECOVERY` ride as clearly-labelled trailing rows in the 🔴 callout, never
     headlining a percentage (check 15).

   **⚠️ Every visible row carries its own 9-week `.item-spark`** holding the scenario's success-rate
   series — including the wins, because a recovery is a shape claim too. Check 16 hard-fails a
   visible row without one. Rows inside the fold are exempt.
   ```html
   <span class="item-name">Passkey WebAuthN Authentication</span>
   <span class="item-spark trend" data-trend="[96.4,96.2,96.3,96.4,96.1,96.3,96.2,94.1]"
         data-w="120" data-h="22" data-color="#cf222e"></span>
   <span class="spark-cap">9 wk</span>
   ```
   Colour: `#cf222e` worsening, `#bc4c00` accelerating, `#1a7f37` improving. Tag each row with its
   label (`tag-new` / `tag-accel` / `tag-ongoing`) plus `elevated Nw` where `weeksElevated > 1`.

   **A quiet week is a valid outcome.** If the classifier reports `quietWeek: true`, lead with the
   quiet-week banner from the template, keep the fold closed, and keep the report short. Do not
   promote the biggest degraded-but-flat scenario to have something to show.

   **Low-volume scenarios do not belong here at all**, however large the percentage move — a 2×
   swing on 820 devices goes in the appendix. Promoting it is exactly what teaches a reader to skim.

   Each row: name + sparkline + inline metric chips + tags pushed right + a one-line body + an
   `Attribution card →` foot link. **At-a-glance only** — the dimension slicing and verdict live
   in Section 5. Do not duplicate them here.

   > **Every row body must be specific to that row** — which scenario, what moved, from what to what,
   > and whether it's news. One generic sentence repeated across rows makes the section unreadable;
   > `validate-report.ps1` fails the report for it.

4. **60-day per-scenario trend** — weekly-bucketed sparkline per scenario, first→last delta, and
   a classification pill (regression / spike / improvement / flat) from `bucket-trends.js`.

5. **Error attribution cards** — one `.attr-card` per regressed scenario. Each card carries:
   (a) top error reasons with WoW deltas and a NEW flag, (b) **all three** dimension bars
   (`AppVersion`, `OsLevel`, `DeviceInfoMake`), (c) a 4-field attribution block — Likely cause /
   Concentration / Suspect PRs / Next step with a named owner.

6. **Unknown / abandonment** — its own section. Define the metric inline for the reader, then
   table it per scenario with Δ pts, devices affected, and an 8-week sparkline.

7. **Push-notification reacted split** — the 4 PN families: initiated, reacted, completion rate,
   Approved / Denied / Error shares with a stacked split bar, WoW deltas on each.

8. **Broker API responsiveness** — per `BrokerApiName`: volume, success rate, p50/p95/p99, devices,
   WoW deltas. **Must carry a visible cross-check note** pointing at the companion Broker report.

   > **The note is mandatory even when there is nothing to correlate — "no matching finding" is
   > itself the finding.** Three cases, all of which produce a note:
   > - **Match found** — name the Broker error code/type and its delta: *"`acquireTokenSilent`
   >   success −1.8 pp here; the Broker report flags `ipc_return_null_cursor` +52% over the same
   >   window. Same root cause, tracked there."*
   > - **No match** — say so explicitly and draw the conclusion: *"No Broker-side error code moved
   >   materially this window, so this looks client-side (Authenticator's own IPC path or a caller
   >   change) rather than a Broker regression."* A silent omission reads as "not checked".
   > - **Companion report unavailable** (`authapp`-only run, or the Broker run failed) — state that
   >   plainly: *"Broker report not generated this run; Broker-side correlation not performed."*
   >   Never imply a cross-check happened when it did not.

9. **Crash & stability (App Center)** — crashes per 1,000 active devices and top crash clusters.
   Rendered as "Not collected this run" when the App Center token is unavailable.

10. **Version adoption + PR attribution** — version share table plus PR cards. PR links use the
    **Azure DevOps** URL pattern, not GitHub.

11. **Appendix** — query provenance: the `.kql` files used, cluster, database, resolved windows.

---

## Step-by-step workflow

### Step 1 — Bootstrap

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\bootstrap-report.ps1 -App authapp
# Optional: explicit end-date (curEnd, exclusive) + force overwrite
# .\bootstrap-report.ps1 -App authapp -EndDate 2026-07-02 -Force
```

It creates `$env:USERPROFILE\android-oce-reports\authapp-wow-report-<curEnd>.html` from
[`authapp-report-template.html`](../templates/authapp-report-template.html), creates
`_data/authapp-<curEnd>/`, stamps the window into the header, prunes data folders older than 60
days, and prints every resolved token value. **Copy those token values down** — every query in
`queries/authapp/` consumes them.

In `both` mode the router has already bootstrapped this file with a shared `-EndDate`. Do not
re-bootstrap; you would reset the header and lose the shared window.

Edit the bootstrapped file **in place**. The template ships as a realistic populated report, not
a skeleton — walk it top to bottom and replace every example date, KPI, table row, verdict, and
PR citation with current data. The CSS, sparkline JS, section order, and card markup are
canonical: do not redesign them.

### Step 2 — Denominator first

Run [`version-share-wow.kql`](../queries/authapp/version-share-wow.kql) **before anything else**.

A build ramping from 5% to 60% of the population moves every scenario rate at once without a
single line of scenario code having changed. If the version mix moved materially, every
downstream verdict has to be read against that fact — and you want to know before you write
thirteen verdicts, not after.

Record the top 3 versions and their share delta. If a cohort moved more than ~10 share points,
say so explicitly in Section 1 and reference it from every affected card.

### Step 3 — The scoreboard

Run [`scenario-outcomes-wow.kql`](../queries/authapp/scenario-outcomes-wow.kql) and
[`pn-completion-wow.kql`](../queries/authapp/pn-completion-wow.kql). Between them they cover all
13 scenarios in two round-trips.

For each scenario compute Δ success rate and Δ unknown rate **in percentage points, not percent
of percent**. A move from 92% to 89% is `−3.0 pts`, never `−3.3%`. Mixing the two is the fastest
way to make a report untrustworthy.

Apply the volume floor: **< ~1,000 initiates in the window is noise.** Tag the row `low-volume`,
keep it in the scoreboard, and keep it out of the regression callout. A 12-initiate scenario
going 100% → 50% is two users.

> A scenario **dropping into** low-volume is itself a finding — instrumentation may have broken.
> Flag that case explicitly rather than letting the row quietly go grey.

### Step 4 — 60-day trend

Run [`scenario-60d-trend.kql`](../queries/authapp/scenario-60d-trend.kql), then:

```pwsh
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\bucket-trends.js $data\scenario-60d.json `
     --key=scenario --metric=devs --end=<startofweek(curEnd)> --include-partial-end `
     --peak-floor=1000 --summary
```

The query maps `errs` / `devs` to **bad outcomes** (`Failed + Unknown`), so the bucketer's
"rising = regression" semantics come out correct with no script change. Run it for **both**
`--metric=devs` and `--metric=reqs` and report the union of what each flags — a scenario where
device count is flat but event count explodes is a retry storm and only shows on one axis.

Do **not** filter the partial current week at the source; `--end` excludes it from the delta math
while `--include-partial-end` keeps it as the chart's final bar.

### Step 4b — Classify novelty (mandatory)

`bucket-trends.js` says *what moved*. It cannot say *whether the movement is news* — and ranking the
attention section by volume is a known, reported defect that buries real step-changes under flat-but-huge
rows. Add `--json=` to the Step 4 runs, then classify:

```pwsh
node .github\skills\oncall-weekly-telemetry-report\assets\scripts\classify-novelty.js `
     $data\bucket-trends-devices.json --summary --floor=1000 --family-sep=none `
     --json=$data\novelty-devices.json
```

`--family-sep=none` is required for AuthApp: scenario names contain spaces (`Entra MFA Registration (QR)`),
so prefix-clustering on `_` is meaningless here. AuthApp findings are per-scenario, not per-family.

Each scenario is classified against **its own history** on complete weeks only (first match wins):
`VOLATILE` (cv > 0.60) → `RECOVERY` (bouncing off a suppressed week) → `NEW` (ratio > 1.15 **and** cv < 0.25)
→ rising over the window (climb > 1.15), which splits into `ACCELERATING` (still deteriorating:
ratio > 1.10 **and** recent-block ratio > 1.10 **and** not falling this week) vs `ONGOING` (elevated but
level) → `IMPROVING` (ratio < 0.8) → `STABLE`.

**⚠️ The `ACCELERATING` / `ONGOING` split is the noise gate.** Both mean "degraded for a while";
only `ACCELERATING` means "and getting worse", which is the only reason to re-surface a known issue.
`attention = NEW ∪ ACCELERATING` — that set, and only that set, is rendered visibly with sparklines.
Read it straight off the sidecar:

```jsonc
{ "attention": ["Passkey WebAuthN Authentication"],
  "attentionLabels": { "Passkey WebAuthN Authentication": "NEW" },
  "quietWeek": false,
  "counts": { "NEW": 1, "ACCELERATING": 0, "ONGOING": 2, "STABLE": 9, "VOLATILE": 1, "IMPROVING": 0 } }
```

If your Needs-attention section is longer than the `attention` array, you promoted rows the
classifier did not — that is the defect this step exists to prevent.

> **Which series get classified: the outcome funnels only — the PN funnel is NOT run through the
> classifier.** Feed `classify-novelty.js` the **9 outcome-funnel bad-outcome series** and nothing
> else. The 13 scenarios in Section 5's scoreboard are **9 outcome funnels + 4 push-notification
> families**; only the 9 are classifiable. The PN families (Section 7) are deliberately excluded for
> two reasons: their `FinalResult` set has **two shapes** across the window so a weekly series is not
> comparable week-to-week, and **`Denied` is a healthy outcome** — a rising `Denied` share is a user
> correctly rejecting a prompt, which the classifier would read as a regression. Never let a PN
> family appear in Section 3's `attention` set.
>
> PN still gets trend treatment, just not novelty classification: chart each family's **completion
> rate** in Section 7 with its own sparkline and report the WoW delta there. If a PN family moves
> enough to be this week's story, say so in Section 7 and, if it warrants top-level visibility,
> reference it from the Section 1 executive summary — not by inserting it into Section 3.

`weeksElevated` is **derived from the 9-week series, never persisted** — it counts consecutive recent
weeks above the early-window baseline, so it is identical on any machine and needs no state file.
Where the whole visible window is elevated the classifier sets `sustainedFullWindow: true`; phrase
that as *"below baseline for the entire visible window"* rather than inventing a week count.

**Rows carrying `suppressRatio: true` (`VOLATILE`, `RECOVERY`) must not carry a WoW-percentage chip at all —
replace it with a `vs 60d median` chip.** Their WoW %
is an artifact of a depressed prior week, and a caveat in the body does not undo a large number sitting in the
chip row (`validate-report.ps1` check 15 hard-fails it). Give the absolute level and where it sits against the 60-day median.
AuthApp scenarios are mostly low-variance (cv 0.02–0.2), so a `VOLATILE` scenario is itself worth a sentence —
it usually means instrumentation is flapping, not that users are failing.

**Two different WoW bases exist — do not conflate them.** The report's headline Δ is the rolling 7-day window;
the classifier's `WoW` is calendar Sun–Sat weeks. They legitimately disagree. Use novelty as *context*
("flat for seven weeks, first slip this week"), never as a competing delta number.

### Step 5 — Sparklines

Run [`scenario-sparkline-series.kql`](../queries/authapp/scenario-sparkline-series.kql) once. It
returns 8 **complete** weeks per scenario and feeds every `data-spark` / `data-trend` array in
Sections 1, 2, 4, and 6.

Sparklines are **mandatory**, not decorative — the validator fails a report whose sparkline
coverage drops, because a body rebuild silently dropping them has happened before.

### Step 6 — Attribution, per regressed scenario

For each scenario in the 🔴 or 🟠 callouts (volume floor cleared):

**6a.** [`scenario-errors-wow.kql`](../queries/authapp/scenario-errors-wow.kql) with
`<ERRORS_MV>` set to that scenario's Errors companion (insert `Errors` before `_MV_V1`).

> ⚠️ The Errors views carry **counts only, no denominator**. Always divide by the scenario's
> `Initiated` from Step 3 before calling anything a rate. An error count up 30% alongside
> initiates up 30% is traffic growth. This is the most common false positive in this report.

**6b.** [`scenario-errors-by-dim.kql`](../queries/authapp/scenario-errors-by-dim.kql), filtered
via `<REASON_FILTER>` to the reasons that actually moved. Read the output as **concentration**:

| Pattern | Reading |
|---|---|
| One `AppVersion` holds most of the delta | Client regression — go find the PR (Step 7) |
| One `DeviceInfoMake` / `OsLevel` holds most of the delta | OEM or OS-version specific — often a platform API behaviour change, not our code |
| Delta spread evenly across all three dims | Service-side or population change, **not** a client regression — say so and do not manufacture a PR |

**6c.** Where the reason is a coarse bucket, drill into raw `passkeyoperations` per the cheatsheet
§ 5. For Passkey scenarios specifically, check `DeviceUnauthenticatedErrorCode` before calling
anything a failure: **5 / 10 / 13 / 14 are user abandonment** (cancel, timeout, negative button);
**1 / 7 / 9 are real device errors**. Reporting abandonment as failure is the classic Passkey
false alarm.

**6d.** Write the 4-field attribution block. Every field must be populated:

| Field | Requirement |
|---|---|
| **Likely cause** | A specific mechanism, not a restatement of the metric. "Success rate fell" is not a cause. |
| **Concentration** | Which dimension, which value, what share of the delta. Quote the number. |
| **Suspect PRs** | ADO PR links with a confidence level and one line on *why* it is the suspect. `none` is a legitimate answer — write it rather than inventing a PR. |
| **Next step** | An action with a **named owner**. |

### Step 7 — PR attribution (Azure DevOps, not GitHub)

The Authenticator source lives at `authenticator/` in this workspace, backed by
**Azure DevOps**: `msazure / One / AD-MFA-phonefactor-phoneApp-android`.

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\find-suspect-prs.ps1 `
    -Repos authenticator -Symbol <ClassOrMethodName> -Since <curStart-30d>
```

Cite PRs with the full ADO URL:

```
https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/<id>
```

**Never emit a `github.com` URL for an Authenticator PR** — it will 404 and it signals the
attribution was pattern-matched from the Broker playbook rather than actually researched.

Scope the search window to changes that shipped in the version cohort that moved (from Step 2),
not to the reporting window — a regression surfaces when a build reaches users, which lags the
merge by weeks.

### Step 8 — Broker API responsiveness

Run [`broker-api-responsiveness-wow.kql`](../queries/authapp/broker-api-responsiveness-wow.kql).
It is the slowest query in the run — start it while writing up Step 6.

Traps (all three are silent failures): the time column is **`PipelineInfo_IngestionTime`**;
`BrokerApiName` and `BrokerApiElapsedTimeMs` must be **extracted** from `AdditionalProperties`;
if the Kusto MCP times out, fall back to `run-kql.ps1 -App authapp`. Its parameters are
`-Query <kql-string>` and `-Out <path.json>` — both mandatory, and it takes the **query text**, not
a `.kql` file path, so read the file in first:

```pwsh
$S = '.github\skills\oncall-weekly-telemetry-report\assets\scripts'
$q = [IO.File]::ReadAllText("$A\queries\authapp\scenario-outcomes-wow.kql")   # then substitute the window tokens
& "$S\run-kql.ps1" -App authapp -Query $q -Out "$D\scenario-outcomes-wow.json"
```

**This section is the seam between the two reports.** A regression here is a shared finding:
check the companion Broker report for the same window before attributing it to Authenticator, and
name which report the evidence came from. In `both` mode, if the Broker agent found a matching
error-code spike, both reports should reference each other.

### Step 9 — Crash & stability

```pwsh
# The script lives in the sibling release-monitoring-report skill and takes a SUBCOMMAND
# (groups | enrich | diff) plus --owner/--app/--version. There is no bare "--days" form.
# --version is the current production Authenticator version from Step 2's version-share query.
$FC = '.github\skills\release-monitoring-report\assets\scripts\fetch-appcenter-crashes.js'
node $FC groups --owner authapp-t7qc `
     --app Microsoft-Authenticator-Android-Prod-App-Center `
     --version <CURRENT_PROD_VERSION> --days 14 --top 15 `
     --out "$env:USERPROFILE\android-oce-reports\_data\authapp-<curEnd>\crash-groups.json"
```

Auth comes from `--token-file <path>`, `$APPCENTER_API_TOKEN`, or
`~/.android-release-reports/appcenter.token`, in that order. **If none is present the section is
skipped — that is expected and is not a failure.** Render the empty state (below) and move on;
never block the report on the crash layer.

Crash data is **App Center only — there is no Kusto source.** If the App Center token is
unavailable, render the section's "Not collected this run" empty state. Do **not** omit the
section (the reader cannot tell omission from zero crashes) and do **not** estimate a crash rate
from Kusto. There is no proxy; inventing one is worse than the gap.

Normalise to **crashes per 1,000 telemetry-active devices** using the Step 2 device count, so the
number is comparable week over week as the population changes.

### Step 10 — Validate

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\validate-report.ps1 -App authapp
# defaults to the most recent authapp-wow-report-*.html under ~/android-oce-reports/
```

Then verify by hand:
- Every PR link is an ADO URL and resolves.
- No stale example text from the template survives (scenario names from the template's sample
  data, example version numbers, example PR ids).
- All 13 scenarios are present in the scoreboard.

---

## Hard rules

> **Shared hard rules live in the router** — [`SKILL.md` § Shared hard rules](../../SKILL.md).
> They apply here too and are NOT repeated below: never carry a telemetry number forward between
> runs · never hardcode the Generated date · never compose report HTML via a PowerShell `@'...'@`
> heredoc (UTF-8 strip) · never bulk-regex-edit balanced HTML · no `devs`/`reqs` in user-facing
> text · same-end-date collision requires an explicit delta statement · no separate Markdown
> summary · never commit the report. **Read them before writing any HTML.**
>
> The rules below are Authenticator-specific and do **not** transfer to the Broker playbook.

- **Novelty classification is mandatory, and Section 3 is ordered by it — never by volume.** Run
  [`classify-novelty.js`](../scripts/classify-novelty.js) (Step 4b) and lead with `NEW`. Volume-ranking
  the attention section is a known, reported defect — it puts flat-but-huge rows above real step-changes.
  If `NEW` is empty, write "nothing new this week"; do not backfill it with `ONGOING` scenarios.
- **Section 3's visible rows are the classifier's `attention` set plus at most 2 wins — nothing else.**
  `NEW` + `ACCELERATING` visible with sparklines; `ONGOING` inside a collapsed `<details class="fold">`.
  Budget **≤ 8 visible rows total, wins included** (check 17 counts wins). The Broker report this
  replaces shipped 13 visible rows with zero charts while the section below it carried 38 — the reader
  could not tell which row was that week's story.
- **Every visible attention row carries a 9-week `.item-spark`**, wins included. Check 16 hard-fails
  otherwise. The series is already in the trend sidecar — no extra query. Charts belong beside the
  claim they support.
- **A quiet week is a valid outcome — publish it as one.** If `quietWeek: true`, show the quiet-week
  banner and keep the report short. Padding the list with the biggest degraded-but-flat scenario is the
  exact behaviour that teaches readers to skim.
- **The 60-day per-scenario table is the scoreboard, not a catalog — charting all ~13 scenarios there
  is correct.** Check 18 allows 16 visible charts for `-App authapp` (vs 6 for broker, whose universe is
  40–50 error codes). Do **not** fan per-error-reason charts into that section; those belong in the
  attribution cards.
- **A `VOLATILE`/`RECOVERY` scenario must not carry a WoW-percentage chip — swap it for `vs 60d median`.** Their WoW %
  is an artifact of a depressed prior week, not a regression. Tagging the row and caveating in the body is **not
  sufficient** — the chip row is read first. A Broker run shipped `429` tagged `VOLATILE`, body reading *"classified
  volatile"*, and still rendered `+401.8%` in `metric up` styling; `validate-report.ps1` check 15 now hard-fails that
  shape on both apps. Report the absolute level and its position against the 60-day
  median instead. AuthApp scenarios normally sit at cv 0.02–0.2, so a genuinely `VOLATILE` scenario usually
  means flapping instrumentation — call that out rather than reporting it as a user-facing failure.
- **No boilerplate in Section 3.** Every row body must be specific to that scenario — what moved, from what
  to what, and whether it's news. One generic sentence repeated across rows makes the section unreadable and
  `validate-report.ps1` fails the report for it.
- **Never `dcount_hll` / `hll_merge` / `percentile_tdigest` / `materialized_view('…')` /
  `MergeAccountType` here.** None of them exist in this database. Device columns are the per-cell
  `…DCount` integers: `sum(SucceededDCount)` — which matches the dashboard but over-counts, so
  treat the result as a relative indicator only. See AB#3739409.
- **The MV time column is `EventDate`.** Raw tables use `EventInfo_Time`, except
  `brokeroperations`, which the dashboard filters on `PipelineInfo_IngestionTime`. Using the wrong
  one returns an empty or skewed window with **no error** — this is a silent failure, always
  sanity-check the row count of the first query.
- **Registration / Authentication MVs have exactly `Initiated / Succeeded / Failed` (+`DCount`)
  and `TotalUniqueDevices`.** There is no `Cancelled` and no `PartiallySucceeded`. Do not invent
  a column; if the numbers do not reconcile, the shortfall is Unknown.
- **The 4 `… PN+CFA` scenarios do NOT have these columns.** They are a two-stage reaction funnel
  keyed on `FinalResult` (`Approved · Denied · Error · Cancelled · ""`). Never compute a
  success/failure/Unknown rate for a PN scenario, never fold `Denied` into failures, and never
  reuse a registration-shaped rate formula on them. See § There are TWO funnel shapes.
- **`Unknown = max(0, Initiated − (Succeeded + Failed))` is a first-class metric.** Report it.
  A scenario with flat success rate and rising Unknown is degrading — the failures simply are not
  being recorded as failures. A small Unknown floor from window-edge truncation is expected; only
  a *change* is a finding.
- **Errors views carry counts only.** Always pair with the outcome MV's `Initiated` before
  quoting a rate. Never report a bare error-count delta as a regression.
- **Volume floor: < ~1,000 initiates is noise.** Tag `low-volume`, keep in the scoreboard, keep
  out of the regression callout. A scenario *dropping into* low-volume is a separate finding.
- **Apply the MSA `IsNGC` filter on BOTH sides of the PN join.** Filtering only the init side or
  only the results side silently mixes NGC and SA populations and the funnel stops reconciling.
- **Rate deltas are in percentage points.** 92% → 89% is `−3.0 pts`, never `−3.3%`.
- **Passkey abandonment is not failure.** Check `DeviceUnauthenticatedErrorCode` — 5/10/13/14 are
  user abandonment, 1/7/9 are device errors — before writing a Passkey regression verdict.
- **Only three dimensions exist** (`AppVersion`, `OsLevel`, `DeviceInfoMake`). Do not go looking
  for calling-app, account-type, or shared-device slices; they are Broker concepts.
- **PR links are Azure DevOps URLs**, never `github.com`.
- **All 13 scenarios appear in the scoreboard every week**, including flat and low-volume ones.
- **A regression spread evenly across all three dims is not a client regression.** Say
  "service-side or population change, no client concentration" rather than manufacturing a PR.
- **Crash data comes from App Center or not at all.** No Kusto proxy, no estimate, no omission —
  render the empty state.
- **Broker-API findings are cross-checked against the Broker report** before attribution.

---

## Output checklist

- [ ] New `authapp-wow-report-YYYY-MM-DD.html` (where `YYYY-MM-DD` is the resolved `curEnd`) exists
      at `$env:USERPROFILE\android-oce-reports\` (NOT at repo root). If a file for this end-date
      already existed, the chat session explicitly stated what changed before regenerating.
- [ ] All 11 sections present and populated. Sections with nothing to report render an explicit
      "None this week" / "Not collected this run" state rather than being omitted.
- [ ] **Version-share query ran first** and its result is reflected in Section 1. If a cohort moved
      >10 share points, the caveat is stated and referenced from every affected card.
- [ ] **All 13 scenarios** are in the scoreboard, each with initiated, success rate, Δ pts,
      unknown rate, Δ pts, devices, sparkline, and a status pill.
- [ ] Volume floor applied — `low-volume` rows tagged, excluded from the regression callout, and
      any scenario that *dropped into* low-volume is flagged as its own finding.
- [ ] **60-day bucketing run on both axes** (`--metric=devs` AND `--metric=reqs`, `--key=scenario`),
      union of regressions reported, partial week charted but excluded from delta classification.
- [ ] **Novelty classification run** ([`classify-novelty.js`](../scripts/classify-novelty.js), Step 4b,
      `--family-sep=none`). Section 3 leads with `NEW`, `ACCELERATING` sits in the 🟠 Getting-worse
      callout, `ONGOING` is inside a collapsed fold, and no `VOLATILE`/`RECOVERY` row headlines a
      percentage. Every row body is specific — no sentence repeats across rows.
- [ ] **Attention section is short and charted.** Visible rows == the classifier's `attention` set
      (`NEW` + `ACCELERATING`), ≤ 8 of them, each with an `.item-spark` 9-week sparkline (wins too).
      If `quietWeek: true`, the quiet-week banner is shown and nothing was promoted to fill the gap.
- [ ] Every regressed scenario that cleared the volume floor has an attribution card with **all
      three** dimension bars and a fully populated 4-field block (Likely cause / Concentration /
      Suspect PRs / Next step with a named owner). "No PR identified, suspect X" is acceptable;
      an empty field is not.
- [ ] Error counts are normalised against `Initiated` — no bare count deltas presented as rates.
- [ ] Unknown/abandonment section populated with per-scenario rates, Δ pts, and sparklines.
- [ ] PN section covers all 4 families with completion rate **and** the Approved/Denied/Error split.
      MSA NGC and SA are separated, with `IsNGC` applied on both join sides.
- [ ] Broker API section populated and carries the cross-check note naming the companion report.
- [ ] Crash section either populated from App Center or rendered as "Not collected this run".
      No Kusto-derived crash estimate anywhere.
- [ ] Every PR link is `https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/<id>`.
      (`Select-String -Pattern 'github\.com'` finds nothing in the PR sections.)
- [ ] Rate deltas expressed in **percentage points** throughout.
- [ ] Sparklines rendered — every KPI tile has `data-spark`; every scoreboard, trend, and unknown
      row has `data-trend`. The validator's coverage check passes.
- [ ] No `\bdevs\b` or `\breqs\b` in user-facing text.
- [ ] No stale template example content (sample version numbers, sample PR ids, sample verdicts).
- [ ] `validate-report.ps1 -App authapp` passes.
- [ ] `get_errors` clean on the HTML file.
