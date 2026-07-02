# Crash & stability sources — Authenticator release monitoring

How to add an **Authenticator crash/stability** section to the release report. Scope is
**Authenticator only** — the Broker is not a store app and has no App Center / Play presence.
Crash data is **not** mirrored into Kusto, so it is pulled live from **App Center**; Kusto
supplies only the rate **denominator** (active devices per version).

## TL;DR pull (two steps)

```bash
# 1) NUMERATOR — App Center crash clusters, paired across versions (signature-joined diff)
#    --page-cap 0 exhausts paging so the crash total (rate numerator) isn't undercounted.
node assets/scripts/fetch-appcenter-crashes.js diff \
  --owner authapp-t7qc --app Microsoft-Authenticator-Android-Prod-App-Center \
  --version <FIRST> --base <SECOND> --days 14 --page-cap 0 \
  --devices-new <DEV_FIRST> --devices-base <DEV_SECOND> --out crash-diff.json

# 2) DENOMINATOR — Kusto active devices per version (run FIRST to get <DEV_*> above)
./assets/scripts/run-kql.ps1 -Query (Get-Content assets/queries/authenticator-crash-denominator.kql -Raw) `
  -Cluster https://idsharedeus2.eastus2.kusto.windows.net `
  -Database d496be22d62a46b0a3cf67ea2e736fd8 -Out denom.json
# (substitute <FIRST>/<SECOND>/<START>/<END> in the .kql first)

# Rank by the honest per-1k RATE delta (lower is better):
node assets/scripts/compare-versions.js movers --file crash-diff.json \
  --key-col label --first-col basePer1k --second-col newPer1k \
  --delta-col rateDeltaPer1k --lower-is-better true --top 10

# 3) ENRICH the top movers — per-group daily TREND (rising/decaying/spike-then-decay) + an
#    instance-sampled OS-major & device-model CONCENTRATION (the list view can't show either):
node assets/scripts/fetch-appcenter-crashes.js enrich \
  --owner authapp-t7qc --app Microsoft-Authenticator-Android-Prod-App-Center \
  --version <FIRST> --days 14 --top 8 --out crash-enrich.json

# 4) NEW-CRASH SCAN — "what crashes are genuinely new in this release?" Anti-join the new build's
#    signatures against a UNION of recent priors (NOT just the baseline — see gotcha #3). Defaults to
#    --page-cap 0 so a "new" verdict can't miss a low-count prior occurrence.
node assets/scripts/fetch-appcenter-crashes.js newcrashes \
  --owner authapp-t7qc --app Microsoft-Authenticator-Android-Prod-App-Center \
  --version <FIRST> --priors <PREV1>,<PREV2>,<PREV3>,<PREV4> \
  --days 14 --min-count 5 --devices-new <DEV_FIRST> --out new-crashes.json

# 5) IS THIS CRASH VERSION-SPECIFIC? — cross-version presence of ONE signature (+ trend on the new
#    build). Use to confirm a "watch"/"new" row before you verdict it.
node assets/scripts/fetch-appcenter-crashes.js signature \
  --owner authapp-t7qc --app Microsoft-Authenticator-Android-Prod-App-Center \
  --version <FIRST> --priors <PREV1>,<PREV2>,<PREV3> \
  --match "<codeRaw or class.method substring>" --days 27 --trend --out sig.json
```

Order matters: run the denominator query first, read the two device counts out of `denom.json`,
then pass them as `--devices-new` / `--devices-base` so the diff can compute the rate. Then run
`enrich` on the rolling-out version to get the trend + dimensions for the signatures the diff flagged.

## Why App Center (and not Play Console)

| Source | Per-crash detail? | Filter by version? | Verdict |
|--------|-------------------|--------------------|---------|
| **App Center Diagnostics** (`errors/errorGroups`) | **Yes** — exception type, crashing class/method/line (`codeRaw`), count, deviceCount | Yes (`?version=`) | **Use this** |
| Play Console UI | No — aggregate numbers only | Partial | Numbers only, no clusters |
| Play Console export (Reporting API / BigQuery / GCS) | No detail; needs a GCP-created service account (gated, centrally owned at Microsoft) | n/a | **Deferred — Phase 2** |

Play Console service-account export is a future enhancement (see "Phase 2" below). For now,
App Center is the only source that returns actionable crash clusters.

## Authentication (secret handling)

`fetch-appcenter-crashes.js` needs an App Center **read-only User API token**. Resolution order:

1. `--token-file <path>`
2. `$APPCENTER_API_TOKEN`
3. `~/.android-release-reports/appcenter.token` (default; 40-char value)

The token is a **SECRET**. Keep it out of the repo, never echo or paste it into the report,
and never commit any file under `~/.android-release-reports/`. Create one at App Center →
**Account settings → API tokens** (read-only is sufficient).

## App slug

- Owner (org): `authapp-t7qc`
- App: `Microsoft-Authenticator-Android-Prod-App-Center`

(A Dev variant and two iOS apps also exist under the same owner; use the Prod Android app.)

## Endpoint & paging

`GET https://api.appcenter.ms/v0.1/apps/{owner}/{app}/errors/errorGroups?version=<v>&start=<ISO>&$top=100&$orderby=count desc`
with header `X-API-Token: <token>`. Returns `{ errorGroups: [...], nextLink }`.

**nextLink quirk (critical):** `nextLink` comes back as a relative path **with an extra `/api`
prefix**, e.g. `/api/v0.1/apps/...&$token=<cont>`. Prefixing the host verbatim →
`https://api.appcenter.ms/api/v0.1/...` → **404**. Correct handling (already in the script):
strip the leading `/api/` → `/v0.1/...`, then prefix `https://api.appcenter.ms`.

**Paging completeness drives total accuracy.** Page size is 100 and a busy version has **1,300+**
groups (verified: 12 pages = 1,293 groups / 46,947 crashes, *still* more pages). There is **no working
aggregate-total endpoint** for this app (see below), so the crash **total** — the denominator for
crash-share and the numerator for the per-1k rate — is *only* the sum of the pages you fetch. The
default `--page-cap` is now **12**; for a release verdict pass **`--page-cap 0`** (exhaust, hard stop
100 pages) so the total isn't silently undercounted. The script prints a `WARNING … total is
UNDERCOUNTED` to stderr when it stops with pages remaining — heed it.

## What App Center exposes (capture map)

The `errorGroups` list response carries more than the headline count — capture it, it's free:

| Field | What it is | Used for |
|-------|-----------|----------|
| `count`, `deviceCount` | crashes + (sub-group) devices | rate (P1/P2) — but `deviceCount` summed across sub-groups **over-counts** (a device can hit several); it's an upper bound, so lead with crashes-per-1k, not "devices crashed". |
| `codeRaw` (`label`) | crashing class.method signature | the cross-version join key |
| `exceptionType` | e.g. `NotSerializableException`, `RemoteException` | triage |
| `exceptionMessage` | the actual message — often the whole story (e.g. a `RemoteException` whose message is `validateForegroundServiceType` = an Android FGS-type enforcement crash; a `NotSerializableException` naming the unserializable class) | root-cause |
| `exceptionClassName` + `exceptionMethod` + `exceptionLine` | precise crash site → `class.method:line` (`appCodeFrame`) | attribution. **NB** `exceptionClassMethod` / `exceptionAppCode` are **boolean flags**, *not* frame strings — don't use them as the frame. |
| `firstOccurrence` | the **version's rollout date** (NOT the signature's app-history first-seen — see gotcha #3) | a *candidate* "new", never a verdict — confirm with `newcrashes`/`signature` |
| `appBuild`, `state`, `hidden` | build no.; Open/Closed/Ignored; muted flag | filtering |

The script now emits `appCodeFrame`, `exceptionMessage`, `firstOccurrence`, `appBuild`, `state`
(groups + diff) and **drops `hidden==true` / `state=="Ignored"` groups by default** so team-muted
noise doesn't inflate the rate (`--include-hidden` to keep them).

### What the list view CAN'T show — the `enrich` mode

Two diagnostics the patterns need (`investigation-patterns.md` P4/P6) are not in the list response:

- **Per-group daily trend** — `GET …/errors/errorGroups/{id}/errorCountsPerDay?version=<v>&start=<ISO>`
  → `{ errors:[{datetime,count}…] }`. Separates an **early-rollout spike that decays** from a
  **sustained regression** (P4). `enrich` classifies it `rising` / `decaying` / `spike-then-decay` /
  `steady` and reports the peak/last day.
- **OS-major & device-model concentration** — the aggregate `operatingSystemCounts` / `modelCounts`
  endpoints **404** for this app, so dimensions come from a **capped sample** of
  `GET …/errors/errorGroups/{id}/errors` instances (each carries `osVersion`, `deviceName`, `country`).
  `enrich` reports the dominant OS major + model with their sampled % share (P6) — e.g. it instantly
  separates *"100% Android 14, ~98% Zebra rugged devices"* (tamper/repackaging) from *"Android 16,
  broad across models"* (a real platform-driven crash).

Run it on the rolling-out version after the diff: `fetch-appcenter-crashes.js enrich --version <FIRST>
--top 8 --out crash-enrich.json`. Fold the trend + OS/model into each crash card; an OS-concentrated,
rising, broad-across-models signature is a platform/release suspect, while a model-concentrated one
on rugged/obfuscated frames is a tamper/sideload signal, not app quality.

## Endpoints that DON'T work for this app (don't waste a call)

| Endpoint | Result | Use instead |
|----------|--------|-------------|
| `errors/errorCounts`, `errors/affectedDeviceCounts`, `errors/errorCountsPerDevice` | **404** | sum paged `errorGroups` (exhaust with `--page-cap 0`) |
| `errors/errorGroups/{id}/operatingSystemCounts`, `…/modelCounts` | **404** | instance sample via `…/{id}/errors` (the `enrich` mode) |
| `errors/availableVersions` | **404** | resolve versions from a high-volume Kusto MV (queries/README) |
| **version-level** `errors/errorCountsPerDay` | **200 but drains to 0** (like retired Analytics) | the **per-group** `…/{id}/errorCountsPerDay` works — sum those if a version-level trend is needed |
| `errorGroups?errorType=handlederror` | **200 but empty** — Authenticator tracks no handled (non-fatal) errors | n/a; crashes = unhandled only, which is what we want |

## Three gotchas the script already handles

### 1. `errorGroupId` is version-scoped — join on the SIGNATURE
Verified empirically: **0** `errorGroupId` overlap between two versions, but **116**
`codeRaw`/`label` (crash-signature) overlap. So the cross-version `diff` joins on the
**signature** (`labelOf` = `codeRaw` / crashing frame), aggregating sub-groups that share a
frame. Never diff on `errorGroupId` — it would mark every cluster as brand-new.

### 2. The share-vs-rate trap — ALWAYS lead with the per-1k rate
Crash **share** (a signature's % of a version's total crashes) is misleading when the two
versions' total crash pools differ in size. Real example (6.2606.3817 vs 6.2605.3042):

- `ValidationCheckType$5.resetCache`: share **5.98% → 12.36%** (looks like a +6.4 pp regression)
- …but absolute crashes **29,052 → 1,336**, and rate **0.388 → 0.106 /1k (−73%)** — an *improvement*.

It only took a bigger slice of a **much smaller pie** (overall rate fell 6.49 → 0.86 /1k). With
denominators supplied, `diff` derives `status` and ranking from the **per-1k rate**, not share.
Report the rate as the headline; keep share only as a *composition* signal ("what dominates the
remaining crashes"). Without denominators, status falls back to share — flag that as provisional.

### 3. `firstOccurrence` is the version's ROLLOUT date — NOT the signature's app-history first-seen
The single most dangerous crash trap, and why `diff`'s `status=new` (absent from the **single**
baseline) is only a *candidate*, never a verdict. App Center's `firstOccurrence` is scoped to the
version-scoped group, so it equals roughly **when that version started rolling out** — a crash that
has existed for many releases shows a `firstOccurrence` *inside* your window on the new build.

Real example (the okhttp HTTP-cache journal-rename `IOException`,
`com.android.okhttp.internal.io.FileSystem$1.rename:87`): on 6.2606.3817 its group reports
`firstOccurrence = 2026-06-11` (rollout day) and the per-1k rate rose 0.016 → 0.043 — it *looks*
brand-new and regressing. Cross-version `signature` scan proves it is present on **every** recent
version and still actively firing on each through today:

| version | crashes (27d) | devices | last seen |
|---|--:|--:|---|
| 6.2606.3817 (new) | 1,536 | 1,208 | today |
| 6.2605.3042 (base) | 1,344 | 1,098 | today |
| 6.2604.2550 | 285 | 234 | today |
| 6.2603.1485 | 916 | 743 | today |
| 6.2602.0889 | **8,300** | 6,984 | today |
| 6.2601.0189 | 2,183 | 1,947 | today |

So it is **pre-existing and environmental** (Android system okhttp `DiskLruCache` journal rename
failing under disk pressure), not a 6.2606 regression. The apparent "rise" is the **young-cohort
ramp + App Center upload lag** (the daily series climbs 6→418 over the window because adoption +
uploads are still filling in), and the per-1k bump is a **denominator artifact** — a comparable raw
count over the young build's much smaller active-device base. **Rule:** never call a crash new or
regressed off `firstOccurrence`-in-window or a single-baseline `diff` alone. Confirm with the
`newcrashes` anti-join (union of priors) and/or the `signature` cross-version scan, and read the raw
**count** alongside the per-1k rate (a young build inflates per-1k even when the count is in-family).

## Is a crash NEW, or just new to this version's group? (`newcrashes` + `signature`)

Two modes operationalize gotcha #3 so "find the new crashes in this release" is one command, not a
manual probe:

- **`newcrashes`** — anti-joins the new build's signatures against the **union of several recent
  prior versions** (pass `--priors v1,v2,v3,v4`), not just the immediate baseline. A signature is
  `genuinely-new` only when it is absent from **all** listed priors within the 27-day API window AND
  present on the new build. Still-active priors keep throwing structural/environmental crashes, so a
  27-day anti-join reliably catches them; a defect introduced *this* release is the residue.
  - **Native/hex-frame caveat (built in):** a native crash whose only frame is a raw address
    (`0x1d0c37a8 + 481192`) or a bare signal (`SIGABRT`/`SIGSEGV`/`minidump`) has a signature that
    **differs per build** (the address relocates), so it *always* anti-joins as "absent from priors."
    The mode tags those `frameKind=native` with verdict `new-native?` and floats the actionable
    **java-frame** `genuinely-new` rows to the top. On 6.2606.3817 this separated **12** java-frame
    new signatures (e.g. a cluster of `com.wolfssl…`/`org.bouncycastle…` `NoSuchFieldError` /
    `NoSuchMethodError` / `OutOfMemoryError` — smells like a crypto-lib bump this release) from **133**
    native-unsymbolized suspects. Judge a `new-native?` row by `enrich` (OS/model + count + trend),
    **not** the signature anti-join.
  - **Device count matters as much as crash count:** many "new" rows are a single device crash-looping
    (high `newCount`, `newDevices=1`) — low fleet impact. Sort/triage on `newDevices`, not just `newCount`.
  - It still can't see beyond the 27-day API window — a signature dormant on priors >27d ago can't be
    distinguished from truly new. For java frames that's rare; corroborate a high-impact one against
    the `authenticator/` `git diff <prevTag>..<newTag>` (P8) before you call it a release defect.
- **`signature`** — cross-version presence of **one** signature (`--match <substring>`), plus the
  daily trend on the new build (`--trend`). This is the "is crash X version-specific or pre-existing?"
  confirmation — run it on any `watch`/`new`/`regressed` crash row before it earns prose in the verdict.

## Crash → PR / code attribution (for a NEW or RISING crash)

Once P10 confirms a crash is genuinely **new** (java-frame `genuinely-new` vs a union of priors) or
**rising** (a cross-version-confirmed worsening per-1k gap, not a young-cohort/upload-lag artifact),
attribute it to the code that owns it — same idea as the Step 3c eSTS `code-attr` card, but the
mechanics differ in one decisive way:

> **A crash names its own code.** The crashing **frame is first-party code directly** (`class.method:line`),
> so you map the frame → its repo and look for the change *in that repo's* release range. Contrast the
> eSTS path, where `invalid_grant` is a **server-returned string** and the broker/common change is only
> the upstream *trigger*. For a crash there is no server in the loop — the suspect PR lives in the repo
> that owns the crashing class.

**Don't attribute** a `new-native?` (build-unique address) frame, an obfuscated/tampered frame
(`com.c.b.b.…`), a `newDevices=1` crash-loop, or any signature `signature` still finds firing on prior
versions — those are not release-introduced (gotcha #3 / P10). Attribution is for a confirmed,
fleet-broad, first-party new/rising signature only.

### 1. Map the crashing frame → repo — but the frame names the VICTIM, not always the culprit

| Frame package prefix | Owning repo | `-Repos` |
|---|---|---|
| `com.microsoft.authenticator.*`, `com.azure.authenticator.*`, `bastion.*`, `onlineid.*` | Authenticator app (`authenticator/`) | `authenticator` |
| `com.microsoft.identity.common.*` (`identity.common…`) | common (`common/`) | `common` |
| `com.microsoft.identity.broker*`, broker-service classes | broker (`broker/`) | `broker` |
| `dagger.*`, `androidx.*`, `kotlin.*`, `okhttp.*`, raw-address/native | a **framework/dep API** — the throwing frame is library code; the culprit is the **first-party CALLER** that invoked it with bad input. Search the app repo for the **API token**, not the framework. |

> **Victim vs culprit — the rule that matters.** A crash frame names the object/API the runtime was
> *inspecting when it threw* (e.g. `…MfaAuthDialogActivity does not implement GeneratedComponent`). That
> object is often **not** the file that broke — it was *handed* to a failing API by some other code. The
> culprit is the **caller** that introduced the bad call, which can live in a completely different file
> with an unrelated PR title. Searching for the crashing class name will miss it (see §2).

### 2. Correlate over the APP's own release range — search the EXCEPTION TOKEN, with `-DiffGrep`

The window is the **rolling-out app version range**, expressed in the app's own tags
(`<prevAppTag>..<newAppTag>`, e.g. `6.2606.3817..6.2606.4029`) — **not** a date window and **not** the
broker tag range. `find-suspect-prs.ps1` resolves an app/broker tag range against the repo's own tags
first, so `-Repos authenticator` with an app-tag range scans exactly that release's commits:

```powershell
# Crash: dagger.hilt.EntryPoints.get:62 IllegalStateException on MfaAuthDialogActivity
& "$S\find-suspect-prs.ps1" -Repos authenticator -Range 6.2606.3817..6.2606.4029 `
  -Symbol 'EntryPoints.get' -DiffGrep 'EntryPoints|GeneratedComponent'
```

- **`-Symbol` = the exception / API token from the stack** (`EntryPoints.get`, `GeneratedComponent`),
  **not** the crashing class. `git log -S` is a content pickaxe — pointed at the API token it finds the
  **caller** that introduced the failing call. Pointed at the crashing class (the victim) it finds
  nothing when, as is usual, the Activity itself was never edited.
- **`-DiffGrep` = the same token(s)** — runs `git log -G` over the **diff text**. This is mandatory for
  crashes: `-GrepRegex` matches only the commit **subject**, and a culprit PR almost never advertises the
  subsystem it broke (the real example below was titled *"TOTP Secret Fix - Phase 1"* yet added a Hilt
  `EntryPoints.get` call). Subject-grep had zero chance; diff-grep caught it instantly.
- **Secondary searches** — also pickaxe the crashing class (`-Symbol MfaAuthDialogActivity`) to catch the
  rarer case where the Activity *was* directly edited, and **path-log the DI graph**, not just the
  crashing file: `git -C authenticator log --oneline <range> -- **/di/ **/dagger/ **/*Module.kt`. A
  module that changes what it provides (a `ContextModule` returning the wrong `Context`) breaks consumers
  without touching either the consumer or the victim.
- The Authenticator repo is **ADO**; merge commits read `Merged PR NNNNNNNN: <title>` (8-digit PR ids,
  no `#`). The script parses that and emits
  `https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/NNNNNNNN` URLs.

### 3. Emit a crash `code-attr` card (in `#auth-stability`)

Same card shape as the eSTS one (Originator / Mechanism / Release range / Likely PRs with honest
confidence / Next step), placed in the **`#auth-stability`** section after the crash table:

- **Originator** — tag the true source: `origin-app` (red) for a confirmed first-party Authenticator-code
  regression; `origin-thirdparty` for a dep bump; `origin-broker`/`origin-common` if a broker/common frame
  regressed; reserve `origin-android` + `origin-env` for a genuine OS × build-config interaction (the
  *last* resort — see the caveat, and do not reach for it until §2's exception-token + diff-grep + DI path
  log all come back empty).
- **Mechanism** — what the runtime is doing at the frame, *which caller* fed it the bad input, and why
  *this release* introduced it. Name the victim and the culprit separately.
- **Release range** — the app tag range + how you found it (exception-token pickaxe + diff-grep + DI path
  log). If a naïve crashing-class search missed the culprit, say so — it documents the method.
- **Likely PRs** — each with an honest `pr-conf` badge keyed to the **caller** PR. A confirmed
  `EntryPoints.get`/module-provider change that the fix later reverts is `pr-conf-high`.
- **Next step** — the landed/queued fix (PR + work item) and the regression test that pins it.

### The build-config / environment caveat — the LAST resort, not the first

Only after the §2 exception-token pickaxe, the `-DiffGrep` diff scan, **and** the DI-graph path log all
come back empty should you consider an environmental cause — an **OS-major × build-config** interaction
(code-shrinker DexGuard/R8 stripping/renaming a generated class, a `targetSdk` bump, a Play Services /
Credential Manager change) surfacing as that OS's adoption grows. It is real but **rare**, and an
OS-concentration signal (e.g. "66.7% Android 16") is **not** sufficient evidence for it — a genuine
caller bug can concentrate on the newest OS too (the path that triggers it is simply exercised more
there). Tag `origin-android`+`origin-env`, confidence **low**, and explicitly note you ruled out a code
caller — never let an empty *crashing-class* search alone (the easiest search to get wrong) justify an
environmental verdict.

> **Verified example (6.2606.4029) — a caller bug that first looked environmental.** The
> `dagger.hilt.EntryPoints.get:62` `IllegalStateException` ("Given component holder class
> `…MfaAuthDialogActivity` does not implement interface `dagger.hilt.internal.GeneratedComponent`") was
> genuinely-new (0 on 6.2606.3817), 66.7% Android 16. A `-Symbol MfaAuthDialogActivity` search found
> nothing, and the OS concentration tempted an "Android-16 × DexGuard shrinker" verdict — **which was
> wrong.** Re-running with the exception token, `-Symbol 'EntryPoints.get' -DiffGrep 'EntryPoints'`,
> surfaced **PR 15896454** *("[MSRC] [110950] - TOTP Secret Fix - Phase 1"*, Cesar Acosta, `7d3da30b13`)
> on the first try. It added `OathSecretEncryptionUseCase`, which resolves its ECS dependency via
> `EntryPoints.get(applicationContext, SecureTotpEcsDependency::class.java)` — but `applicationContext`
> is bound from the **legacy Dagger `ContextModule.provideContext()`**, which returned *the context the
> component was built with*. The MFA dialog fragments build it with `requireContext()` — i.e. the
> **`MfaAuthDialogActivity`** — so `EntryPoints.get(activity, …)` ran against an Activity, which is not a
> Hilt generated component holder → the exact crash. The Activity was the **victim**; the culprit was the
> new `EntryPoints.get` caller in a different file under an unrelated PR title. Fix: **PR 16249408**
> (`023aec8abd`, "normalize ContextModule to application context", **AB#3677526**) →
> `provideContext(): Context = context.applicationContext`. This is `origin-app`, **high** confidence — a
> real release regression, not an OS/shrinker interaction.

## App Center Analytics is RETIRED — no native crash-free %

`analytics/crash_counts` → **410 Gone**; `analytics/crashfree_users` / `crashfree_devices` →
**404**; `session_counts` / `active_device_counts` respond but drain to ~0. So App Center cannot
give a crash-free percentage. The **only** way to a true rate is App Center crash counts
(numerator) ÷ Kusto active devices (denominator). Diagnostics/`errorGroups` itself remains alive.

## Rate caveats (state these in the report)

- **Population mismatch:** App Center counts devices whose App Center SDK reported a crash;
  Kusto counts devices emitting product telemetry. The ratio is a directional rate, **not** an
  exact crash-free %.
- **Early-rollout numerator lag:** on a freshly-rolling-out build, App Center crash uploads are
  still accumulating, so a very low new-build rate can be partly an artifact — confirm the trend
  as adoption grows (same caveat as the Broker early-rollout cohort).
- **"New" clusters need the union anti-join, not a single-baseline diff:** `diff`'s `status=new`
  only means "absent from the one `--base` version" — a signature missing from the immediate baseline
  but present two releases back is falsely flagged. Use `newcrashes --priors v1,v2,v3,v4` for a real
  new-crash list, treat `frameKind=native` (`new-native?`) rows as unconfirmed (build-unique
  signatures), and triage on `newDevices` (a 1-device crash-loop ≠ a fleet regression). Treat
  java-frame `genuinely-new` with `newPer1k < ~0.02` as low-priority, not a HOLD.
- **Obfuscated frames** (e.g. `com.c.b.b.bSS.loadClass` → `ClassNotFoundException "Didn't find
  class …"`) are typically **repackaged/tampered APKs**, not first-party bugs. Call them out as
  such; their movement is about sideload/tamper prevalence, not app quality.

## Phase 2 (deferred): Google Play Console crash export

Adds a second store-side source. Requires a **GCP-created service account** with Play Console
access (centrally owned at Microsoft — request through the Play Console admin). Once granted,
pull either the Reporting API (`vitals.errors`) or the GCS/BigQuery crash export. Play still
gives weaker per-crash detail than App Center, so it would supplement, not replace, App Center.
Not implemented yet — leave a "Play Console: not yet wired" note in the report's appendix.
