# Investigation patterns — is the move real, and is it the release?

The diagnostic methodology the report applies whenever a metric moves between two versions.
A KPI delta is a *question*, not a verdict. Before any table row turns into "regression" prose
(or a HOLD), run the relevant patterns below to separate a **real, release-caused** regression
from volume growth, population skew, a benign outcome, or an environment shift the app didn't cause.

These complement the broker-specific drill-downs already in the workflow (Step 3c, host-app
device-share vs per-span, release-PR correlation). The patterns here generalize to **both** apps
and are the default lens for the Authenticator scenario/crash sections.

## The one-line tests

| # | Pattern | The test | Reads as "not the release" when… |
|---|---------|----------|----------------------------------|
| P1 | **Count vs rate** | Normalize every error/outcome count to its initiation denominator before reacting. | Raw count is up but **rate per initiation is flat**. |
| P2 | **Version attribution vs substitution** | Compare the **rate on the new build vs the old build**, not the new build's count over time. | New-build rate ≈ old-build rate — the new version is just absorbing rollout volume. |
| P3 | **Code-frozen control** | Recompute the *same* rate on the **previous/stable version** (its code is frozen). | The rate **also rose on the frozen build** → environmental (OS / Play Services / Credential Manager / server / eSTS), not this release. |
| P4 | **Rollout-cohort effect** | Re-check on **matched higher-volume days** and after the cohort broadens. | Gap shrinks as volume grows / cohort widens → early-adopter skew, not a defect. |
| P5 | **Benign-vs-real classification** | Decompose the failure numerator by reason; split benign/expected and abandonment from true defects. | The growth is **benign** (duplicate / user-cancelled), and the **defect-only** rate is flat. |
| P6 | **Dimensional decomposition** | Break the metric by `AppVersion × OsLevel × DeviceInfoMake`. | Concentrated on **one OS across all OEMs** → platform driver; tracks the **new version's ramp** → substitution. |
| P7 | **Drill to the sub-code** | Go from the MV `Error` reason down to the raw structured sub-code. | The movement is a **soft/user code** (cancel) not a **hard** one (lockout / hw / crypto). |
| P8 | **Telemetry ↔ code via release-tag diff** | `git diff <prevTag>..<newTag>` over the feature paths to see if the **decision/gate logic actually changed**. | Gate logic is **byte-for-byte unchanged** → the shift is funnel/population/environment, not logic. |
| P9 | **MV introspection** | Read the MV definition before drilling so you query the right raw events. | (enabler for P5–P8 — tells you exactly what each metric counts.) |

---

## P1 — Count vs rate
Raw error/failure **counts rise with traffic**. A bigger day, a marketing push, or a new OS cohort
all inflate counts without anything regressing. **Always** divide by the matching denominator
(initiations for that scenario; active devices for crashes) and reason about the **rate**.
- The Authenticator `*_Errors_MV_V1` views give `ErrorCount` only — pair every Errors-MV pull with
  the outcomes MV's `Initiated` (or `auth-scenario-initiates.kql`) for the same version/day grain.
- If you only have a count time series and it is climbing, that is the *start* of an investigation,
  never the conclusion.

## P2 — Version attribution vs substitution
During a rollout the new build's **count** climbs simply because it is taking over traffic; the old
build's count falls in lockstep. This is **substitution**, not regression. The honest comparison is
the **per-initiation rate on the new build vs the old build over the same window**. If
`rate(new) ≈ rate(old)`, the release did not move the metric — you are watching the denominator
move. Only a genuine **rate** gap on the new build is attributable to the release.

## P3 — Code-frozen control (the most decisive test)
The previous stable version is a **natural control**: its code is frozen, so anything that moves *its*
rate over the same window is environmental. So when the new build's rate is up:
1. Recompute the identical rate for the **previous version** over the same days.
2. If the previous build's rate **also rose**, the cause is outside the app — an OS update wave,
   Google Play Services / Credential Manager change, a server/eSTS change, or a fleet-wide condition.
   Do **not** attribute it to the release.
3. If the previous build's rate is **flat** while the new build's is up, the release is now a real
   suspect — proceed to P5–P8.

## P4 — Rollout-cohort effect
A young version's users are **not representative**: early adopters skew toward engaged/power users and
specific device/OS segments, and early daily volume is small (high variance). Both inflate or distort
rates (e.g. already-enrolled users disproportionately hitting "already registered"; a thin day swinging
several points on a handful of events).
- Re-measure on **matched, higher-volume days**; widen the window so the baseline cohort grows.
- Expect an upgrade-driven spike to **decay** toward baseline as the cohort re-auths/re-syncs; a true
  regression holds a **steady gap**. Report the **residual after decay**, not the headline-day delta.

## P5 — Benign-vs-real classification
An MV's `Failed`/`Succeeded` split is a **funnel outcome**, not a defect count. `Failed` routinely
bundles outcomes that are *working as intended*. Decompose the failure numerator (via the
`*_Errors_MV_V1` companion, then raw) and bucket each reason:
- **Benign / expected** — e.g. a duplicate/"already registered" outcome (the user already has the
  credential). Up because more users are *already enrolled*, not because anything broke.
- **Abandonment** — user/system **cancellation** (e.g. a biometric prompt dismissed). A UX/engagement
  signal, not a code defect.
- **True defect** — keystore/ECC/crypto, network, server, serialization, null-state, timeout.

Report a **defect-only rate** (exclude benign + abandonment from the numerator). A headline
success-rate "drop" that is **entirely** benign/abandonment is not a quality regression — say so, and
keep the raw rate in an appendix for transparency.

## P6 — Dimensional decomposition
Break the moving metric by `AppVersion × OsLevel × DeviceInfoMake` (the Errors MVs carry all three).
The shape of the concentration is the attribution:
- **Concentrated on one `OsLevel`, broad across OEMs** → an **OS-platform** driver (new Android major,
  Play Services, Credential Manager), often amplified by that OS version's **adoption growth** — so
  confirm with P3 (frozen build rose too) and check whether the OS cohort itself is expanding.
- **Concentrated on one `DeviceInfoMake`/model** → a **device/vendor** bug (keystore, biometric HAL).
- **Tracks the new `AppVersion`'s ramp** (up on new, down on old, rate flat) → **substitution** (P2).
- **Broad and proportional everywhere** → a real, code-caused regression candidate → P7/P8.

## P7 — Drill to the sub-code
The Errors MV's `Error` is often a coarse bucket (e.g. a single "device auth failed" reason) that hides
very different root causes. The raw `passkeyoperations` table carries the **structured sub-code** in
`AllProperties` (e.g. `DeviceUnauthenticatedErrorCode` = the Android `BiometricPrompt` code). Drill in
to tell a **soft** outcome from a **hard** one:

| Android `BiometricPrompt` code | Meaning | Bucket |
|---|---|---|
| 10 `ERROR_USER_CANCELED`, 13 `ERROR_NEGATIVE_BUTTON`, 14 `ERROR_NO_DEVICE_CREDENTIAL` | user dismissed | abandonment (P5) |
| 5 `ERROR_CANCELED` | system cancelled (e.g. app backgrounded) | abandonment (P5) |
| 1 `ERROR_HW_UNAVAILABLE`, 2 `ERROR_UNABLE_TO_PROCESS`, 11 `ERROR_NO_BIOMETRICS` | hardware/enrollment | true defect/device (P6) |
| 7 `ERROR_LOCKOUT`, 9 `ERROR_LOCKOUT_PERMANENT` | too many attempts | true (often device/user) |

If ~all of a reason's growth is the soft codes, the "failure" rise is abandonment, not a defect.

## P8 — Telemetry ↔ code via release-tag diff
When P3 says the new build's rate genuinely moved and you suspect the app, **prove it against the
diff** before naming it a code regression. The Authenticator app is its **own git repo**
(`authenticator/`, base branch `working`; tags like `6.2606.3817` and `v6.2605.3042` — note the
inconsistent `v` prefix). Diff the two release tags scoped to the feature's source paths:

```powershell
cd authenticator
git --no-pager diff v6.2605.3042..6.2606.3817 -- `
  PhoneFactor/app/src/main/java/com/microsoft/authenticator/passkeys/
```

- If the **decision/gate logic is unchanged** (the file that emits the moving reason isn't in the
  diff, or only unrelated lines changed — a flight rename, a validator swap), the shift is
  **funnel/population/environment**, not this release. State that explicitly.
- If the gate logic **did** change, you now have a concrete suspect commit/PR to name with honest
  confidence. (For broker/common-triggered eSTS codes, use the existing Step 3c PR-correlation flow —
  `find-suspect-prs.ps1 -Range v<PREV>..v<NEW>` over `broker/`+`common/`.)

## P9 — MV introspection
Before drilling, read what a metric actually counts so you query the right raw events:

```kusto
.show materialized-view Passkey_WebAuthN_Registration_MV_V1 | project Query
```

This reveals the **source table**, the `OperationName`/`requestType`/`PasskeyFlow` filters, and how
`Initiated/Succeeded/Failed` are derived — e.g. the Registration MVs count only
`CreatePasskeyCredentialRequest`, the Authentication views only `GetPasskeyCredentialRequest`. Knowing
the exact filter prevents drilling into the wrong request family when you go to the raw table.

## P10 — Crash version-attribution (is a crash NEW / caused by this release?)
The crash analogue of P2/P3. A crash that *looks* new on the rolling-out build is the #1 false alarm,
because App Center's per-version `firstOccurrence` is the version's **rollout date**, not the
signature's app-history first-seen — so a years-old crash shows a first-seen *inside* your window and
a per-1k rate that "rose." Climb this before any crash row becomes "new"/"regressed" prose:

1. **Anti-join against a UNION of priors, not the single baseline (the crash P2).** `diff status=new`
   only means "absent from `--base`"; a signature can skip the immediate baseline yet live two releases
   back. Run `fetch-appcenter-crashes.js newcrashes --version <new> --priors v1,v2,v3,v4`. Only
   `genuinely-new` (absent from **all** priors in the 27-day window) is a real new-crash candidate.
2. **Discount native/hex-frame signatures.** A raw-address frame (`0x… + …`) or bare signal
   (`SIGABRT`/`SIGSEGV`/`minidump`) relocates per build, so it *always* anti-joins as new — `newcrashes`
   tags these `new-native?`. Judge them by `enrich` (OS/model + count + trend), never the signature.
3. **Cross-version confirm (the crash P3 / code-frozen control).** For any suspect signature run
   `signature --match <frame> --version <new> --priors …`. If it is present and still firing on the
   **previous** versions, it is **pre-existing/environmental** (okhttp disk-cache, OEM/OS, tamper APK),
   not this release. Real example: the okhttp `FileSystem$1.rename:87` `IOException` showed
   `firstOccurrence=` rollout day and per-1k 0.016→0.043 on 6.2606, but `signature` found it on every
   version back to 6.2601 (peaking at 8,300 crashes on 6.2602) — a denominator/upload-lag artifact, not
   a regression.
4. **Read count + devices next to the rate.** A young build's small active-device base inflates per-1k
   even when the raw **count** is in-family with priors; and a high `newCount` on `newDevices=1` is a
   single device crash-looping, not a fleet regression. Lead with per-1k, but corroborate with both.
5. **If a java-frame `genuinely-new` survives, prove it (P8).** A real, high-impact new java signature
   (e.g. a crypto-lib `NoSuchFieldError` cluster) should correlate to a dependency/code change in the
   `authenticator/` `git diff <prevTag>..<newTag>` before it earns a WATCH/HOLD.
6. **Attribute it to the CALLER, searching the exception token — not the crashing class.** A crash
   frame names the object the runtime inspected when it threw (the **victim**), which is usually *not*
   the file that broke — some **caller** handed it to a failing API. So set `-Symbol` to the
   **exception/API token from the stack** (`EntryPoints.get`, `GeneratedComponent`), not the crashing
   class, and **always** add `-DiffGrep` (a `git log -G` over the diff text) because `--grep` sees only
   the commit subject and a culprit PR rarely names the subsystem it broke. Map the frame's package to
   its repo (`com.microsoft.authenticator.*`/`bastion.*`/`onlineid.*` → `authenticator/`;
   `identity.common…` → `common/`; broker → `broker/`; a `dagger.*`/`androidx.*` framework frame → the
   first-party caller in the app repo) and correlate over the **app's own tag range**:
   ```powershell
   & "$S\find-suspect-prs.ps1" -Repos authenticator -Range 6.2606.3817..6.2606.4029 `
     -Symbol 'EntryPoints.get' -DiffGrep 'EntryPoints|GeneratedComponent'
   # secondary: path-log the DI graph (a module changing what it provides breaks consumers silently):
   git -C authenticator log --oneline 6.2606.3817..6.2606.4029 -- **/di/ **/dagger/ **/*Module.kt
   ```
   Emit a crash `code-attr` card in `#auth-stability` (`origin-app` for a confirmed first-party
   regression). **Environmental is the LAST resort, not the first:** only after the exception-token
   pickaxe, the `-DiffGrep` scan, AND the DI path log all come back empty consider an OS × build-config
   interaction (shrinker/`targetSdk`/Play Services) — and an OS-concentration signal alone is **not**
   evidence for it (a real caller bug concentrates on the newest OS too). Verified the hard way: the new
   `dagger.hilt.EntryPoints.get` crash on `MfaAuthDialogActivity` (6.2606.4029, 66.7% Android 16) *looked*
   like an "Android-16 × shrinker" issue and a `-Symbol MfaAuthDialogActivity` search found nothing — but
   `-Symbol 'EntryPoints.get' -DiffGrep` surfaced **PR 15896454** ("TOTP Secret Fix") on the first try: it
   added an `EntryPoints.get(applicationContext, …)` call whose context, bound from a legacy `ContextModule`,
   was actually the dialog **Activity** → the victim Activity isn't a Hilt component holder → crash (fix:
   PR 16249408, AB#3677526). A real `origin-app` regression, not environmental. (Full recipe + victim-vs-
   culprit rule in `crash-sources.md` § "Crash → PR / code attribution".)

---

## The drill ladder (Authenticator)
Each scenario has three layers — climb down only as far as the question needs:

1. **Outcomes MV** (`*_MV_V1`) — `Initiated/Succeeded/Failed (+DCount)` → the headline rate (P1/P2).
2. **Errors MV** (`*_Errors_MV_V1`) — `Failed` broken by `Error × OsLevel × AppVersion × DeviceInfoMake`
   (`ErrorCount`/`ErrorDCount`) → reason + dimensional attribution (P5/P6). Pair with the outcomes MV
   for the denominator.
3. **Raw `passkeyoperations`** — `OperationName`, `AppInfo_Version`, `DeviceInfo_OsVersion`,
   `DeviceInfo_Make`, `DeviceInfo_Id`, and the `AllProperties` JSON (`RequestType`, `PasskeyFlow`,
   `Error`, `ErrorSource`, `IsCrossDevice`, `DeviceUnauthenticatedErrorCode`, …) → the structured
   sub-code (P7). `osLevel = tostring(split(DeviceInfo_OsVersion, " ")[0])`; `todynamic(AllProperties)`
   to read keys.

## Decision flow (apply to any moving KPI)
```
metric moved
  → P1 normalize to a rate ─ rate flat? ─────────────────► volume only, not the release
  → P2 new-build rate vs old-build rate ─ equal? ────────► substitution, not the release
  → P3 did the frozen (previous) build's rate move too? ─ yes? ► environmental (OS/PlayServices/eSTS)
  → P4 holds on higher-volume days / after broadening? ── no? ─► early-cohort skew, re-check later
  → P5 decompose reasons ─ growth is benign/abandonment? ► not a quality regression (report defect-only)
  → P6 one OS across OEMs? one OEM? tracks new version? ─► platform / device / substitution
  → P7 drill to sub-code ─ soft (cancel) vs hard? ───────► classify
  → P8 release-tag diff ─ gate logic unchanged? ─────────► funnel/population, not code
  → still a steady, code-correlated, defect rate gap? ──► REAL regression → verdict WATCH/HOLD
```
Only the bottom rung earns regression prose. Everything above it is a reason the headline delta is
**not** a release-caused quality regression — name which one in the verdict.

**For a moving crash** (App Center), apply **P10** instead of P5–P7: anti-join `newcrashes` against a
union of priors, discount native/hex frames, cross-version-confirm with `signature`, and read
count+devices beside the per-1k rate. A spike-then-decay trend, a pre-existing cross-version signature,
a single-OEM/obfuscated frame, or a `newDevices=1` crash-loop is **not** a HOLD; an OS-concentrated,
broad-across-models, java-frame `genuinely-new` signature that holds a steady gap is. Once confirmed
new/rising, **attribute it** (P10 step 6): set `-Symbol` to the **exception token** (not the crashing
class — that's the victim), add `-DiffGrep`, correlate over the app's own tag range with
`find-suspect-prs.ps1 -Repos authenticator`, and emit an `origin-app` crash `code-attr` card. Treat an
`origin-android`+`origin-env` (OS × build-config) verdict as the **last** resort — only after the
exception-token, diff-grep, and DI path-log searches all come back empty — never off an OS-concentration
signal or an empty crashing-class search alone.
