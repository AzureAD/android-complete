---
name: release-monitoring-report
description: Generate a version-over-version release-health report for the Android Broker and/or the Authenticator app as one polished self-contained HTML file. Use this skill when monitoring a rollout — triggers include "monitor this release", "release health report", "broker rollout health", "authenticator rollout", "is this release safe to widen", "what's changing this release and why", "crash regression", "stability report", or "compare a new version vs a previous version". Accepts a Broker version and/or an Authenticator version being rolled out, plus a previous/baseline version per app (or an all-versions baseline), runs Kusto queries for each app to quantify what changed and why, adds an Authenticator crash/stability layer from App Center (crashes per 1k active devices), and writes the HTML to the android-release-reports folder under the user's home directory (outside the workspace so reports are never committed).
---

# Release Monitoring Report

Produce a **version-over-version** release-health report covering the Android **Broker** and/or
the **Authenticator** app in one self-contained HTML file at
`$env:USERPROFILE\android-release-reports\release-report-broker-<bv>-auth-<av>-<YYYY-MM-DD>.html`
(home folder, **outside the workspace** — reports never get committed). Omitted apps drop out
of the filename, so broker-only or authenticator-only runs are first-class.

The report answers two questions per app: **what changed** this release (KPIs + tables vs the
baseline) and **why** (error-code movers / per-scenario deltas). It ends in a clear **verdict**
per app: SAFE to proceed / WATCH / HOLD.

The output mirrors the canonical template at
[`assets/templates/report-template.html`](assets/templates/report-template.html) — a real filled
example kept as the structural + visual reference. The Step 1 bootstrap script copies it into
`~/android-release-reports/…` and **you edit it in place** (versions, dates, KPI values, table
rows, verdict prose). Do **not** redesign the layout.

**Before writing any KQL, read the three reference files:**
- [`assets/docs/kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md) — how to run queries
  (auth, `run-kql.ps1` invocation, output JSON shape, token substitution, the end-to-end loop,
  version-resolution recipes, hard gotchas).
- [`assets/queries/README.md`](assets/queries/README.md) — the query catalog: what each `.kql`
  computes, the token convention, and the Authenticator scenario→MV→column map.
- [`assets/docs/investigation-patterns.md`](assets/docs/investigation-patterns.md) — **the diagnostic
  methodology**: the count-vs-rate / version-attribution / code-frozen-control / rollout-cohort /
  benign-vs-real / dimensional-decomposition / drill-to-sub-code / release-tag-diff / MV-introspection /
  crash-version-attribution (P10) patterns, plus the decision flow. A KPI delta — or a crash that looks
  new — is a question, not a verdict; run these before any table row becomes "regression" prose or a HOLD.

**For the Authenticator crash/stability layer, also read:**
- [`assets/docs/crash-sources.md`](assets/docs/crash-sources.md) — App Center crash pull (auth/token,
  `errorGroups` endpoint, the `nextLink` quirk), the **share-vs-rate trap**, the Kusto rate
  denominator, secret handling, and the deferred Play Console (Phase 2). Authenticator only — the
  Broker is not a store app and has no crash section.

## Inputs to confirm

Ask only for what's missing; infer the rest.

1. **Which app(s)** — Broker version rolling out, Authenticator version rolling out, or both.
   At least one is required.
2. **Baseline per app** — the previous release version to compare against, OR "auto-resolve the
   previous version" (pick the next-highest-volume recent version), OR "all-versions baseline".
3. **Window** — default last **30 days** (Authenticator MVs) / **14 days** (Broker). The new
   version is usually young, so a longer window mostly grows the baseline cohort.
4. **Report date** — defaults to today (used in the filename + the "Generated" banner).
5. **Authenticator crash layer (optional)** — if an Authenticator version is given and an App Center
   read-only token is available (`~/.android-release-reports/appcenter.token`, `$APPCENTER_API_TOKEN`,
   or `--token-file`), add the crash/stability section. Skip silently if no token. The token is a
   **secret** — never echo it or write it into the report.

If the user gives versions but not the baseline, run the resolution queries first and propose
`<FIRST>` (rolling out) / `<SECOND>` (previous) from volume, then continue.

## Data sources (summary — full detail in the cheatsheet)

| App | Cluster | Database | Version dim | Time col |
|-----|---------|----------|-------------|----------|
| Broker | `https://idsharedeus2.kusto.windows.net` | `ad-accounts-android-otel` | `broker_version` (`16.1.0`) | `EventInfo_Time` |
| Authenticator | `https://idsharedeus2.eastus2.kusto.windows.net` | `d496be22d62a46b0a3cf67ea2e736fd8` | `AppVersion` (`6.2606.3817`) | `EventDate` (MVs) |

`run-kql.ps1` defaults to Broker; pass `-Cluster`/`-Database` for Authenticator. Requires
`az login` (Android Auth Client SDK security group).

## Bundled assets

| File | Purpose |
|---|---|
| [`templates/report-template.html`](assets/templates/report-template.html) | Canonical layout — a real filled example. **Edit in place**; do not restyle. The CSS in `<head>` is canonical. |
| [`docs/kusto-cheatsheet.md`](assets/docs/kusto-cheatsheet.md) | Operational runbook: run-kql usage, JSON shape, token substitution, end-to-end loop, gotchas. |
| [`docs/investigation-patterns.md`](assets/docs/investigation-patterns.md) | **Diagnostic methodology** — count-vs-rate, version-attribution-vs-substitution, code-frozen control, rollout-cohort, benign-vs-real classification, dimensional decomposition, drill-to-sub-code, release-tag diff, MV introspection, **crash version-attribution (P10 — is a crash new/release-caused?)**, plus the Authenticator drill ladder (outcomes MV → `*_Errors_MV_V1` → raw `passkeyoperations`) and a decision flow. Apply before calling any delta a regression. |
| [`docs/crash-sources.md`](assets/docs/crash-sources.md) | Authenticator crash layer: App Center auth/endpoint, the three gotchas (`errorGroupId`-is-version-scoped, share-vs-rate, **`firstOccurrence`-is-rollout-date**), the **`newcrashes`/`signature` new-crash detection** flow, Kusto rate denominator, secret handling, Play Console Phase 2. |
| [`queries/README.md`](assets/queries/README.md) | Query catalog + Authenticator scenario→MV→column map. |
| [`queries/*.kql`](assets/queries/) | 6 Broker + 6 Authenticator scenario templates + `authenticator-crash-denominator.kql` (crash-rate denominator), all validated live. Substitute `<TOKENS>` before running. Includes `broker-errors-by-host-app-span.kql` — the per-`span_name` request-rate drill-down that complements the host-app device-share movers. |
| [`scripts/run-kql.ps1`](assets/scripts/run-kql.ps1) | Direct-REST Kusto helper. `-Query`/`-Out` mandatory; `-Cluster`/`-Database` for Authenticator. |
| [`scripts/find-suspect-prs.ps1`](assets/scripts/find-suspect-prs.ps1) | Release PR correlation. **Auth-code** (eSTS) attribution: defaults to `broker/`+`common/` over a broker tag range (`-Range v16.1.0..v16.2.0`; broker uses its own tags, common maps via the broker submodule pointer). **Crash** attribution: `-Repos authenticator` over the app tag range (`-Range 6.2606.3817..6.2606.4029`) — resolves the app's own tags and parses ADO `Merged PR NNNNNNNN:` → pullrequest URLs. Three search streams: `-S` pickaxe + `-DiffGrep` (`git log -G` over diff text) + `--grep` (subject). **For a crash, set `-Symbol` to the exception/API token from the stack (e.g. `EntryPoints.get`), not the crashing class, and always pass `-DiffGrep`** — the crashing class is the victim, the culprit is a caller whose subject rarely names the subsystem. Prints PR ids + URLs for attribution cards. |
| [`scripts/fetch-appcenter-crashes.js`](assets/scripts/fetch-appcenter-crashes.js) | Pull Authenticator crash clusters from App Center → run-kql array-form JSON. `groups` (one version) + `diff` (two versions, signature-joined, per-1k rate when given Kusto denominators) + `enrich` (top signatures' daily **trend** + instance-sampled **OS-major/device-model** concentration) + **`newcrashes`** (genuinely-new java-frame signatures via anti-join against a **union of priors**, native/hex frames split out as `new-native?`) + **`signature`** (cross-version presence of one signature + trend — "is crash X version-specific?"). Captures `exceptionMessage`/`appCodeFrame`/`firstOccurrence`, drops `hidden`/`Ignored` groups, and `--page-cap 0` exhausts paging for an accurate total. |
| [`scripts/bootstrap-report.ps1`](assets/scripts/bootstrap-report.ps1) | Copy the template to a version-named file, create `_data/<slug>-<date>/`, stamp the Generated date, prune old `_data`, detect unfilled-stub vs real-report collisions. |
| [`scripts/compare-versions.js`](assets/scripts/compare-versions.js) | Delta + classification engine over run-kql JSON. `rows` mode (version-per-row metrics) and `movers` mode (paired error-share rows). Thresholds + volume guard. |
| [`scripts/validate-report.ps1`](assets/scripts/validate-report.ps1) | Pre-publish validator: stale tokens, mojibake, raw-count leaks, version-string presence, both app sections, verdict callouts. |

## Workflow

### Step 1 — Bootstrap
```powershell
$S = ".github/skills/release-monitoring-report/assets/scripts"
$out = & "$S\bootstrap-report.ps1" -BrokerVersion 16.1.0 -AuthVersion 6.2606.3817
```
Omit either `-…Version` for a single-app run. The script prints the report path and creates the
`_data/<slug>-<date>/` folder (`$DATA`) for raw query payloads. Re-running on an unfilled stub
is silent; a populated report halts unless you pass `-Force`.

### Step 2 — Resolve versions
If the baseline wasn't given, run `broker-adoption.kql` and the cheap Authenticator resolver
(cheatsheet § "Version resolution recipes") to pick `<FIRST>`/`<SECOND>` by volume. For an
"all-versions baseline", drop the version filter / list every version in `<VERSIONS>`.

### Step 3 — Pull queries (both apps)
For each needed `.kql`: read it, substitute tokens (cheatsheet § "Filling tokens"), run via
`run-kql.ps1 -Out $DATA\<name>.json`. Run independent queries in parallel (PowerShell jobs).
Minimum useful set:
- **Broker:** `broker-adoption`, `broker-reliability-by-version`, `broker-error-rate-by-version`,
  `broker-top-errors-by-version` (the "why"), `broker-latency-by-version`.
- **Broker via Authenticator (when an Authenticator version is given):** also run
  `broker-by-host-app` and `broker-top-errors-by-host-app` with `<PACKAGE>=com.azure.authenticator`
  and `<FIRST>`/`<SECOND>` = the **Authenticator app versions** (these MVs key the host's broker on
  `AppInfo_Version`, which for that package is the app version). This isolates whether the broker
  regresses *because of this app rollout*, separate from the fleet-wide `broker_version` view.
- **Authenticator:** `auth-version-resolve` (or cheap fallback), then per top scenario:
  `auth-scenario-success-rate` (Registration/Auth) or `auth-pn-checkforauth-completion` (PN+CFA),
  always alongside `auth-scenario-initiates` for the volume guard. Use `auth-stats` for adoption.

### Step 3b — Authenticator crash layer (optional)
If an Authenticator version is given and an App Center token is available, **read
[`assets/docs/crash-sources.md`](assets/docs/crash-sources.md) first**, then:
1. Run `authenticator-crash-denominator.kql` (auth cluster) to get active devices for `<FIRST>`/`<SECOND>`.
2. Pull + pair crashes (signature-joined; pass the two device counts so it computes the per-1k rate).
   Use **`--page-cap 0`** for a verdict so the crash total isn't undercounted (there's no aggregate-total
   endpoint — the total is only the pages fetched, and a busy version has 1,300+ groups):
```powershell
node "$S\fetch-appcenter-crashes.js" diff --owner authapp-t7qc `
  --app Microsoft-Authenticator-Android-Prod-App-Center `
  --version 6.2606.3817 --base 6.2605.3042 --days 14 --page-cap 0 `
  --devices-new <DEV_FIRST> --devices-base <DEV_SECOND> --out "$DATA\crash-diff.json"
```
   The diff now also carries `exceptionMessage`, `appCodeFrame` (`class.method:line`), `firstOccurrence`,
   `appBuild`, `state`, and **drops team-muted `hidden`/`Ignored` groups** by default — fill these into
   the crash cards. **`diff status=new` is only a CANDIDATE, not a verdict** — it means "absent from the
   single `--base`," and `firstOccurrence` is the version's **rollout date**, not the signature's
   app-history first-seen (a years-old crash shows a first-seen inside your window). Confirm any "new"
   with step 4 below.
3. **Enrich the top movers** — the list view can't show a crash's **trend** or **which OS/OEM** it hits,
   so pull both (these answer the P4/P6 patterns for crashes):
```powershell
node "$S\fetch-appcenter-crashes.js" enrich --owner authapp-t7qc `
  --app Microsoft-Authenticator-Android-Prod-App-Center `
  --version 6.2606.3817 --days 14 --top 8 --out "$DATA\crash-enrich.json"
```
   It returns, per top signature, a **daily-trend tag** (`rising`/`decaying`/`spike-then-decay`/`steady`
   + peak/last day) and an instance-sampled **top OS-major + device-model concentration** (the aggregate
   OS/model endpoints 404, so this samples `errorGroups/{id}/errors`). Read it as: OS-concentrated +
   rising + broad-across-models ⇒ platform/release suspect; model-concentrated on rugged/obfuscated
   frames ⇒ tamper/sideload, not app quality; spike-then-decay ⇒ early-rollout churn, not a HOLD.
4. **Find genuinely-new crashes (P10)** — to answer "what crashes are new in this release," anti-join
   the new build's signatures against a **union of recent priors** (not just the baseline — `diff`'s
   single-baseline `new` flag false-positives a crash that skipped one version). Then cross-version-
   confirm any suspect with `signature`:
```powershell
# genuinely-new = java-frame signature absent from ALL priors in the 27-day window (native/hex frames
# are build-unique → tagged new-native?, judged by enrich not the anti-join). Triage on newDevices too.
node "$S\fetch-appcenter-crashes.js" newcrashes --owner authapp-t7qc `
  --app Microsoft-Authenticator-Android-Prod-App-Center `
  --version 6.2606.3817 --priors 6.2605.3042,6.2605.2973,6.2604.2550,6.2603.1485 `
  --days 14 --min-count 5 --devices-new <DEV_FIRST> --out "$DATA\new-crashes.json"
# is THIS crash version-specific or pre-existing? cross-version presence + trend on the new build:
node "$S\fetch-appcenter-crashes.js" signature --owner authapp-t7qc `
  --app Microsoft-Authenticator-Android-Prod-App-Center `
  --version 6.2606.3817 --priors 6.2605.3042,6.2605.2973,6.2604.2550,6.2603.1485 `
  --match "<codeRaw or class.method substring>" --days 27 --trend --out "$DATA\sig.json"
```
   Read `newcrashes` as: a `genuinely-new` **java** frame that is OS-broad and holds a steady gap is a
   real release suspect (prove it against the `authenticator/` tag diff, P8); a `new-native?` row, a
   `newDevices=1` crash-loop, or any signature `signature` finds still firing on prior versions is
   **not** release-caused. A young build inflates per-1k via a small device denominator — read the raw
   **count** beside the rate.

**Lead with the per-1k rate, not crash-share** — a signature can take a bigger share of a smaller
crash pool while its per-device rate falls (share alone invents phantom regressions). Skip this
step silently if no token exists.

5. **Attribute a confirmed new/rising crash to its code (P10 step 6).** Once a signature is confirmed
   genuinely-new (or a cross-version-confirmed rising per-1k gap) AND first-party + fleet-broad — not a
   `new-native?`, tampered, single-OEM, or pre-existing frame — find the change that introduced it.
   **Search the EXCEPTION TOKEN, not the crashing class:** a crash frame names the object the runtime was
   inspecting (the *victim*), which is usually not the file that broke — a *caller* handed it to a failing
   API. Set `-Symbol` to the exception/API token from the stack (`EntryPoints.get`, `GeneratedComponent`),
   and **always** add `-DiffGrep` (a `git log -G` diff-text search) because `-GrepRegex` matches only the
   commit subject and a culprit PR rarely names the subsystem it broke. Map the frame's package to its repo
   (`com.microsoft.authenticator.*`/`bastion.*`/`onlineid.*` → `authenticator/`; `identity.common…` →
   `common/`; broker → `broker/`; a `dagger.*`/`androidx.*` framework frame → the first-party caller in the
   app repo) and correlate over the **app's own tag range** (`find-suspect-prs.ps1` resolves app tags
   directly and parses ADO `Merged PR NNNNNNNN:` → pullrequest URLs):
```powershell
& "$S\find-suspect-prs.ps1" -Repos authenticator -Range 6.2606.3817..6.2606.4029 `
  -Symbol 'EntryPoints.get' -DiffGrep 'EntryPoints|GeneratedComponent'
# secondary: path-log the DI graph (a module changing what it provides breaks consumers silently):
git -C authenticator log --oneline 6.2606.3817..6.2606.4029 -- **/di/ **/dagger/ **/*Module.kt
```
   Emit a crash `code-attr` card in the **`#auth-stability`** section (Originator / Mechanism / Release
   range / Likely PRs with honest confidence / Next step), `origin-app` for a confirmed first-party
   regression. **Environmental is the LAST resort:** only after the exception-token pickaxe, the
   `-DiffGrep` scan, AND the DI path log all come back empty consider an **OS-major × build-config**
   interaction (shrinker/`targetSdk`/Play Services) — and an OS-concentration signal (e.g. "66.7% Android
   16") is **not** evidence for it (a real caller bug concentrates on the newest OS too). Verified the hard
   way: a `-Symbol MfaAuthDialogActivity` search missed the `dagger.hilt.EntryPoints.get` culprit and
   tempted a wrong "Android-16 × shrinker" verdict; `-Symbol 'EntryPoints.get' -DiffGrep` found PR 15896454
   ("TOTP Secret Fix") instantly. See `crash-sources.md` § "Crash → PR / code attribution" for the full
   frame→repo map and the verified `dagger.hilt`/Android-16 example.

### Step 3c — Broker-via-Authenticator span drill-down + release PR correlation (conditional)
The host-app movers table (`broker-top-errors-by-host-app`) is a **device-share** (devices hitting a
code anywhere ÷ devices on that version), which dedups a device across all spans and **masks a
per-span request-rate rise**. So a code can look flat-to-down there while it is climbing inside one
span (e.g. `invalid_grant` / `interaction_required` on the **silent** path). Whenever a server-returned
auth code is suspected — or proactively for `invalid_grant`/`interaction_required` — drill down:
1. Run `broker-errors-by-host-app-span.kql` with `<PACKAGE>=com.azure.authenticator`,
   `<FIRST>`/`<SECOND>` = the app versions, and `<CODES>` = the suspect codes (lower-cased,
   comma-separated). It returns per-`span_name` **request-level** rates (errored ÷ total in that span).
2. If a span rate is up, separate **early-rollout churn** from a real regression with a daily trend
   (rate by version by day): an upgrade spike *decays* toward baseline as the cohort re-auths; a true
   regression holds a steady gap. Report the residual, not just the headline window delta.
3. **Release PR correlation** (for codes whose Originator is **eSTS** — `invalid_grant`,
   `interaction_required`, `unauthorized_client`, etc. — the broker/common change is the *trigger*,
   not the source of the string). The window is the **bundled broker version range**, not a date
   window. Read `git log v<PREV_BROKER>..v<NEW_BROKER>` in `broker/` and `common/` **in full** (the
   range between two releases is small — tens of commits), then narrow with the pickaxe:
```powershell
& "$S\find-suspect-prs.ps1" -Symbol generateAsymmetricKey -GrepRegex Asymmetric `
  -Range v16.1.0..v16.2.0 -RepoRoot "<repo-root>"
```
   Weight **device-PoP / PRT / token-cache** changes for silent-auth credential rejections (re-keying
   or PRT churn makes eSTS reject device-bound RTs with `invalid_grant`, decaying as devices re-bind).
   Exclude PRs that address a *different* code, are a *fix that reduces* errors, or are gated to an
   SdkType the host app does not use (e.g. `MSAL_CPP` = OneAuth, not the Authenticator app's own MSAL).
   Emit a `code-attr` attribution card (Originator / Mechanism / Release range / Likely PRs with honest
   confidence / Next step) in the `#auth-broker` section.

### Step 3d — Diagnose an Authenticator scenario move before you verdict
A KPI delta (a success-rate drop, an error-count or error-share rise) is a **question**, not a verdict.
Before any scenario row becomes "regression" prose or a HOLD, run the diagnostic ladder from
[`assets/docs/investigation-patterns.md`](assets/docs/investigation-patterns.md). Climb only as far as
the question needs:
1. **Normalize, then attribute (P1–P3).** Convert counts to a **rate per initiation** (Errors-MV
   `ErrorCount` ÷ outcomes-MV `Initiated`, matched by version/day). Compare the **new-build rate vs the
   old-build rate** — equal rates with a rising new-build count is rollout **substitution**, not a
   regression. Then apply the **code-frozen control**: recompute the same rate on the **previous**
   version; if *its* rate also rose, the cause is **environmental** (a new Android major, Play
   Services / Credential Manager, server/eSTS), not this release.
2. **Decompose (P4–P6).** Re-check on **higher-volume days** (early-cohort skew + thin-day variance);
   break the move by `AppVersion × OsLevel × DeviceInfoMake` via the **`*_Errors_MV_V1`** companion.
   One OS across all OEMs ⇒ platform driver (often amplified by that OS's adoption growth); one OEM ⇒
   device bug; tracks the new version's ramp ⇒ substitution. Classify each reason as **benign**
   (duplicate / "already registered"), **abandonment** (user/system cancel), or **true defect**, and
   report a **defect-only** rate — a "drop" that is entirely benign/abandonment is **not** a quality
   regression.
3. **Drill + prove (P7–P9).** For a coarse reason, drill to the structured sub-code in raw
   `passkeyoperations.AllProperties` (e.g. `DeviceUnauthenticatedErrorCode` — soft codes 5/10/13/14 =
   abandonment vs hard 1/7/9 = device/defect). Use `.show materialized-view <name> | project Query`
   to confirm what the metric counts before drilling. If the app is still the suspect, **prove it
   against the diff**: the Authenticator is its own repo (`authenticator/`, base `working`, tags like
   `6.2606.3817` / `v6.2605.3042`) — `git --no-pager diff <prevTag>..<newTag> -- <feature paths>`.
   Gate logic unchanged ⇒ funnel/population/environment, not code; only a steady, code-correlated,
   defect-rate gap earns a WATCH/HOLD. Name in the verdict **which** rung explained the headline delta.

### Step 4 — Compare + classify
Feed payloads to `compare-versions.js`:
```powershell
node "$S\compare-versions.js" rows --file "$DATA\broker-reliability-by-version.json" `
  --version-col broker_version --first 16.1.0 --second 16.0.1 `
  --metrics SilentDevReliability,InteractiveDevReliability --threshold 1.0
node "$S\compare-versions.js" movers --file "$DATA\broker-top-errors-by-version.json" `
  --key-col error_code --delta-col shareDeltaPp --lower-is-better true --top 10
node "$S\compare-versions.js" movers --file "$DATA\crash-diff.json" `
  --key-col label --first-col basePer1k --second-col newPer1k `
  --delta-col rateDeltaPer1k --lower-is-better true --top 10
```
Use `--lower-is-better` for latency, error-rate, and error-share metrics (down = good). Pass
`--volume-col`/`--volume-floor` so low-volume scenarios classify as `low-volume`, not regression.

### Step 5 — Fill the report in place
Edit the bootstrapped HTML: version strings, window dates, KPI tiles (humanize counts — `585.3M`,
not `585300000`), table rows, and a **verdict callout per app** whose prose follows what the
deltas say — and, per Step 3d, **names which diagnostic rung** explains each headline move
(volume/substitution, code-frozen ⇒ environmental, early-cohort skew, benign/abandonment, or a real
defect-rate gap); prefer a **defect-only** rate over a raw success-rate "drop" that is entirely benign
or user-cancellation. Keep both app `<section>`s even if one is "clean/flat". For Authenticator, fill the
`#auth-stability` section from `crash-diff.json` (crashes/1k KPIs + the per-1k crash table — surface
`exceptionMessage` and `appCodeFrame` in each row, and flag rows whose `firstOccurrence` is inside the
window as **new**) and from `crash-enrich.json` (annotate the top crash cards with the trend tag + top
OS-major/device-model so the reader sees *why* it moved, per Step 3b); fold the stability verdict into
the Authenticator callout — a spike-then-decay or single-OEM/obfuscated mover is **not** a HOLD, an
OS-concentrated rising mover broad across models is. **For any confirmed new/rising first-party crash,
add a crash `code-attr` card** to `#auth-stability` (per Step 3b.5 / P10 step 6) — Originator (`origin-app`
for a confirmed first-party regression; reserve `origin-android`+`origin-env` for a genuine OS×build-config
interaction only after the exception-token + diff-grep + DI path-log searches all come up empty) /
Mechanism (name the victim frame and the culprit caller separately) / Release range / Likely PRs with
honest confidence / Next step; model it on the canonical card in the template's `#auth-stability` section.
If no crash token was available, leave
the section's appendix note that App Center crashes were not pulled. When an Authenticator version
is given, also fill the **`#auth-broker`** section ("Broker via Authenticator") from
`broker-by-host-app.json` + `broker-top-errors-by-host-app.json`: KPI tiles (device error rate +
silent/interactive reliability for the app-hosted broker) and the per-`error_code` movers table,
with a verdict on whether the broker regresses *because of this app release*. **Always span-drill
the silent path** (Step 3c): fill the span-breakdown sub-table (`broker-errors-by-host-app-span.json`)
and, when an eSTS code is elevated, the `code-attr` attribution card — do **not** write "no broker
regression" off the device-share table alone, since it masks per-span request spikes. If the
fleet-wide Broker section flags a code that this host-scoped view does **not**, call that out
explicitly (the fleet delta is driven by another host, e.g. Link to Windows). Mark a HOLD when a
regression dominates the headline; note early-rollout caveats (the new version's cohort skews toward
upgrade/network churn — an early spike that decays is "watch," not a clear HOLD). Leave the `<head>` CSS untouched.

### Step 6 — Validate
```powershell
& "$S\validate-report.ps1" -Path $out -BrokerVersion 16.1.0 -AuthVersion 6.2606.3817
```
Fix all ERRORS (exit 1). WARNINGS are advisory. Then open the file in a browser to eyeball it.

## Gotchas (full list in the cheatsheet)
- **Distinct devices (Broker):** `dcount_hll(hll_merge(countDevicesHll))` — never `sum(countDevices)`.
- **Percentiles (Broker):** `percentiles_array_tdigest(tdigest_merge(...))` — never average percentiles.
- **A delta is a question, not a verdict (see investigation-patterns.md):** before any Authenticator
  scenario row becomes "regression," normalize counts to a **rate per initiation** (P1); compare the
  **new-build rate vs the old-build rate** — a rising new-build *count* with equal rates is rollout
  **substitution**, not a regression (P2); and run the **code-frozen control** — if the same rate also
  rose on the **previous** version, it's environmental (OS / Play Services / Credential Manager /
  eSTS), not this release (P3). Prove a real suspect against `git diff <prevTag>..<newTag>` in
  `authenticator/` (P8) — unchanged gate logic ⇒ funnel/population, not code.
- **Benign failures inflate the "Failed" bucket:** the outcome MVs lump expected outcomes (duplicate /
  "already registered") and user/system **cancellation** into `Failed`, depressing the headline
  success rate without anything breaking. Decompose via the **`*_Errors_MV_V1`** companion (every
  scenario has one — `Error × OsLevel × AppVersion × DeviceInfoMake`, with `ErrorCount`/`ErrorDCount`)
  and report a **defect-only** rate; drill to the raw `passkeyoperations.AllProperties` sub-code (e.g.
  `DeviceUnauthenticatedErrorCode`: 5/10/13/14 = abandonment, 1/7/9 = device/hard) to separate cancel
  from defect. A "drop" that is entirely benign/abandonment is **not** a quality regression.
- **Authenticator outcomes:** Registration/Auth MVs have only `Initiated/Succeeded/Failed` (+DCount);
  PN completion needs the init-MV ⋈ `_Results_MV_V1` join. MSA NGC vs SA splits on `IsNGC` on **both** join sides.
- **Volume guard:** < ~1K initiates = noise, not a regression.
- **Broker per host app:** the Broker MVs carry `active_broker_package_name` (host) +
  `AppInfo_Version` (= the host app's version for that package, e.g. Authenticator `6.2606.3817`,
  not `broker_version`). Filter `active_broker_package_name == "com.azure.authenticator"` and
  compare by `AppInfo_Version` to attribute broker movement to the app rollout; never attribute a
  fleet-wide `broker_version` delta to an app (Link to Windows ≈122 M devices can dominate it).
- **Crashes — share ≠ rate:** lead with crashes per 1k active devices (App Center count ÷ Kusto
  devices), not crash-share; App Center `errorGroupId` is version-scoped so `diff` joins on the
  crash signature, and App Center's native crash-free metric is retired. See `crash-sources.md`.
- **Crashes — page to the total, then read the message and dimensions:** there is **no aggregate-total
  endpoint**, so the crash total (rate numerator + share denominator) is *only* the pages you fetch — a
  busy version has 1,300+ groups, so use `--page-cap 0` for a verdict or the rate is undercounted.
  `exceptionMessage` is usually the whole story (a `RemoteException` reading `validateForegroundServiceType`
  = Android FGS enforcement; a `NotSerializableException` naming the class). Don't verdict a crash mover on
  count alone — `enrich` its **trend** (spike-then-decay = rollout churn, not a HOLD) and **OS/model**
  (broad-across-models + OS-concentrated = platform/release; one rugged OEM on obfuscated frames = tamper/sideload).
- **Crashes — "new" is the #1 false alarm (`firstOccurrence` = rollout date):** App Center's
  per-version `firstOccurrence` is when that *version* shipped, NOT when the signature first appeared,
  so a years-old crash shows a first-seen inside your window and a per-1k rate that "rose" (young
  build = small device denominator). `diff status=new` only means "absent from the single baseline."
  To actually find new crashes use **`newcrashes --priors v1,v2,v3,v4`** (anti-join vs a *union* of
  priors; java-frame `genuinely-new` is the signal, native/hex `new-native?` is build-unique noise,
  triage on `newDevices`), and confirm any suspect with **`signature --match … --trend`** (cross-version
  presence). A signature still firing on prior versions is pre-existing/environmental, not this release.
  Verified: the okhttp `FileSystem$1.rename:87` IOException looked new+regressing on 6.2606 but exists
  on every version back to 6.2601. See `crash-sources.md` gotcha #3 and investigation-patterns P10.
- **Device-share masks per-span spikes:** the host-app movers table dedups a device across all spans,
  so a code can look flat/down there while its per-request rate climbs inside one span. Re-slice with
  `broker-errors-by-host-app-span.kql` (request-level rate by `span_name`) before declaring "no broker
  regression." Separate early-rollout decay from a steady gap with a daily trend.
- **Release PR correlation:** for eSTS-returned codes (`invalid_grant`, `interaction_required`) the
  trigger is in the bundled broker version range — read `git log v<PREV>..v<NEW>` in `broker/`+`common/`
  in full, then `find-suspect-prs.ps1 -Range`. Weight device-PoP/PRT/cache changes; exclude different-code
  fixes and SdkType-gated PRs (`MSAL_CPP` = OneAuth, not the Authenticator app).
- **Secrets:** the App Center token and anything under `~/.android-release-reports/` stay out of
  the repo and out of the report — never echo or paste them.
- **UTF-8 trap:** never write the HTML through a PowerShell `@'…'@` heredoc (it strips emoji/arrows).
  Use the `edit`/`create` tools or `[IO.File]::WriteAllText($p,$t,[System.Text.UTF8Encoding]::new($false))`.
- **Filename collision:** a populated report is never silently overwritten — bootstrap halts unless `-Force`.
