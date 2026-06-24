# Severity Classification Rubric

Right-size each finding against **our** codebase reality, not the filed severity. The filed
classification (MSRC severity, FireWatch/Glasswing tier) is an **input**. Our classification is the output.

## Core principle: evidence or it didn't happen

Every tier assignment requires **cited code evidence** (`file:line`). This cuts both ways:
- To **down-classify**, cite the mitigating control that blocks real-world exploitation.
- To **up-classify** (or confirm CRITICAL), cite the absence of any gate AND confirmed reachability.
- "I couldn't find an exploit path" / "I didn't see a mitigation" is **not** evidence. Show the control,
  or show the systematic searches (`codebase-researcher` style) that prove its absence.

## The tiers

### CRITICAL — must fix
- Reachable in a **shipping** configuration (not debug/test/root-only).
- **No** mitigating control found after a deep, beyond-the-obvious sweep.
- Real-world mass exploitation is plausible.
- **Evidence required:** sink `file:line` + reachability proof + the searches that establish no flight
  gate / allow-list / export restriction / IPC check exists.

### Important
- A genuine weakness exists, but blast radius is limited by a **partial** mitigation or **elevated
  prerequisites** (attacker must already control a federated IdP page, must win a race, etc.).
- **Evidence required:** sink `file:line` + the specific mitigation/precondition that limits it, cited.

### Moderate
- Defense-in-depth gap. Exploitation needs **unlikely but non-root** preconditions: a narrow race, a
  non-default (but shipping) config, an attacker who already controls a federated/IdP page, etc.
- **Evidence required:** the cited precondition (e.g. component `android:exported="false"`, a race window,
  a non-default flag) that blocks mass exploitation.
- **Note:** if the *only* precondition is **root / physical / debug-build / `adb`**, it is **not** Moderate
  — it is **out of scope (Won't-Fix / Sev4)**; see "Out-of-scope threat boundary" below. A root reader is
  Moderate-or-higher only when a **non-root** path also reaches it.

### Low / Won't-Fix
- Not reachable in the shipping config, OR already gated off by default.
- **Evidence required:** citation proving non-reachability — flight defaulting off, non-exported
  component, sibling handler enforcing an allow-list the finding assumed was missing, dead code, etc.

## Out-of-scope threat boundary (root / physical / debug-build) — **Won't-Fix**

Some findings are only exploitable once the **OS security boundary is already defeated**. These are
**out of scope** for the app and classified **Won't-Fix** (IcM **Sev4**), because no app-level control can
defend a platform that is already compromised. A finding is out of scope when its **SOLE** exploitation
path requires any of:

- **A rooted / jailbroken device** — root can read app-private storage, hook the process, and bypass any
  client-side check, so an app control cannot meaningfully mitigate it.
- **Physical / forensic access** to an unlocked device or a device image (e.g. reading
  `filesDir`, a disk dump, or a backup off the device).
- **A debuggable / test / engineering build**, or an attacker with **`adb`/developer-mode** access (USB
  debugging is an explicit user opt-in that grants debug-bridge privileges).

> Map: **Won't-Fix → Sev4.** Still write the finding up (one-liner + the citation proving the precondition)
> and record the threat-boundary rationale — "out of scope" is a *verdict with evidence*, not a hand-wave.

### ⚠️ The SOLE-path rule — do NOT dismiss a finding that ALSO has a non-root path
Root/physical/debug is only out of scope when it is the **only** way in. If **any** of these
non-privileged paths exists, the finding is **in scope** and root is merely an *aggravating reader*, not
the gate — classify on the non-root path:

- another **app on the device** can reach it (exported component, Intent, deep link, IPC) — no root needed;
- a **network / remote** attacker or a **zero-click** vector can trigger it;
- the sensitive data **egresses off-device** by a non-root channel (e.g. bundled into a **diagnostics /
  log upload**, sent to a server, written to shared/world-readable storage).

**Worked example (why this nuance matters):** the plaintext-TOTP-seed log finding (an ITD)
writes seeds to an **app-private** file — reading that file directly *does* need root/forensic/ADB. If that
were the only path it would be Won't-Fix. But the same log is **harvested into a PowerLift diagnostics
upload** (a non-root, off-device egress), so it stayed **Important** — the non-root path governs. Always
finish the defense-in-depth sweep for a non-root path **before** ruling something out of scope.

## Team IcM Severity mapping (Sev2 / Sev2.5 / Sev3 / Sev4)

Our analytical tier (CRITICAL/Important/Moderate/Low) must be expressed as the team's **IcM severity** so
on-call knows the response urgency. The two are different axes — tier is *how bad the weakness is*, IcM Sev
is *how fast we must act*:

| IcM Sev | Urgency (response SLA) | Maps from |
|---------|------------------------|-----------|
| **Sev2** | **Immediately, outside business hours** (page on-call now) | CRITICAL **and** actively/mass-exploitable in shipping with **no** mitigating control |
| **Sev2.5** | **Immediately, but within business hours** | CRITICAL not actively exploited, **or** the very top of Important where reachability is confirmed and no safeguard was found |
| **Sev3** | Soon, not drop-everything | Important (genuine weakness, partial mitigation / elevated prereq); upper Moderate |
| **Sev4** | Low priority / hygiene | Moderate (defense-in-depth gap, unlikely preconditions) and Low / Won't-Fix |

> Default crosswalk: **CRITICAL → Sev2** (active/mass) or **Sev2.5** (not active) · **Important → Sev3**
> (or Sev2.5 only at the confirmed-reachable, no-safeguard top edge) · **Moderate → Sev3/Sev4** ·
> **Low → Sev4**.

### ⚠️ The Sev2.5+ gate — be conservative

Assigning **Sev2.5 or above is a high bar** and is *rare*, because our stack almost always has a
safeguard (flight default-off, allow-list, signature/UID check, server-side number-match, non-exported
component). **Do not assign Sev2.5+ unless ALL of these hold, each with cited evidence:**

1. **High confidence** — the adversarial Pass 2 *held* (it could not break the verdict). Medium/Low
   confidence ⇒ cannot be Sev2.5+.
2. **Reachable in a shipping configuration** — proven, not assumed (no debug/test/root-only caveat).
3. **No mitigating control** — proven *absent* with the searches that establish it (not merely "I didn't
   find one"), across the full defense-in-depth sweep.
4. **Not leaning on an unverifiable boundary** — if the verdict depends on whether a downstream consumer
   or eSTS does/doesn't enforce something we can't see (the **External Validation Needed = Yes** case), it
   is **at most Sev3** until that boundary is confirmed. A "partly theoretical" finding is never Sev2.5+.

If any one fails, cap at **Sev3**. When in doubt, go lower and say why — over-escalation burns on-call
capacity, which is the exact problem this skill exists to manage.

### Calibration log — refine the mapping as we go

The tier↔Sev mapping is **expected to evolve** as we triage more findings. Whenever a run produces a
mapping decision worth remembering (a new edge case, a borderline tier→Sev call, a safeguard pattern that
should pin something to Sev3/Sev4), **record it here** so future runs are consistent — and treat that as a
**skill learning to commit** (see SKILL.md "Capture learnings"). Format: `- <finding/class> → SevX because
<reason + evidence pattern>`.

- _AAD NGC PendingIntent collision (CWE-451) → Sev4_ — server-validated session-bound number-matching
  neutralizes the swap; collision alone is a robustness defect. Pattern: **a server-side approval gate that
  binds to the displayed session caps notification/intent-collision findings at Sev4.**
- _Unvalidated `app_link` → `ACTION_VIEW` (CWE-601) → Sev3_ — real unmitigated library gap, but attacker
  control of `app_link` depends on unverifiable eSTS/redirect behavior (External Validation = Yes), so it
  is held at Sev3 (not Sev2.5) despite default-path reachability. Pattern: **open-redirect/intent-launch
  gated by a server-emitted value is capped at Sev3 until the server side is confirmed.**
- _⚠️ "Embedded WebView is non-default" is a TRAP for broker findings (Sev calibration)_ — for the MSAL SDK,
  the auth WebView is non-default (browser/Custom Tabs default), which tempts a down-classify to Low. But the
  **Broker forces `AuthorizationAgent.WEBVIEW` by default** (`MsalAndroidBrokerCommandParameterAdapter` /
  `MsalBrokerRequestAdapter` / `BrokerTokenCommandParametersUtil` → `authorizationAgent == null` → WEBVIEW),
  so any auth-WebView sink IS on the default path for **broker-mediated** auth (the highest-value PRT/SSO
  context). Pattern: **never down-classify an auth-WebView finding on "non-default WebView" without checking
  the broker adapter — the broker default is WEBVIEW, which keeps such findings at Sev3, not Sev4.** (Caught
  twice by the adversarial pass: intent-scheme differential Low→Moderate, origin-blind NTLM.)
- _CSRF/SSRF via auto-firing deep link (FCM token + activation code) → Sev3_ — the deep-link activation
  **auto-fires zero-click** (the external link is treated as a QR scan) AND the CSRF **completes** (the
  server PAD push legitimately reaches the victim's own device). But it caps at Moderate because the injected
  account is **attacker-owned** (the submitted device token is the victim's own → no reverse account-binding)
  and the FCM token isn't weaponizable. Pattern: **a completable, zero-click CSRF still caps at Sev3 when the
  injected artifact is attacker-owned and the exfiltrated data is non-weaponizable — likelihood is high but
  impact is bounded.**
- _Plaintext secret logged to an app-private file → Sev3 (not lower)_ — the usual down-classifier ("logging
  compiled out in release") must be PROVEN, not assumed. Here release level = INFO, ERROR still writes to the
  on-disk file, no `-assumenosideeffects` strip, scrub off-by-default. Pattern: **a log-leak of durable
  secrets (TOTP seeds) holds at Sev3/Important when the release-suppression control is proven ABSENT — verify
  `BuildConfig.DEBUG` gating, proguard `-assumenosideeffects`, and the scrub default before down-classifying.**
- _"TLSBypass"-labeled finding with no actual TLS bypass → recategorize, don't just re-sev_ — verify the
  filed CATEGORY, not just severity. Here TLS was fully enforced (HTTPS-forced cast, system CAs, zero
  trust-all); the real issue was SSRF/exfil. Pattern: **a mislabeled finding needs the category corrected in
  the report + ITD title, or the fix gets mis-scoped.**
- _Root-only / physical / debug-build exploitability → out of scope (Won't-Fix / Sev4)_ — once the OS
  boundary is already defeated, no app control can mitigate. **But only when it's the SOLE path:** finish
  the defense-in-depth sweep for a non-root path first (other app via IPC/Intent/deep-link, network/
  zero-click, or off-device egress such as a diagnostics/log upload). Pattern: **"needs root to read" is
  out of scope; "needs root to read OR ships off-device via diagnostics" is in scope and governed by the
  non-root path** (see the TOTP-seed log example — stayed Important on its diagnostics egress).
- _Execution/rollout calibration (intent-scheme allow-list hardening)_ — when we **fix** an Important
  finding in `common`, default the new ECS flight **OFF** and ramp progressively, because `common` ships to
  broker + MSAL + OneAuth and a regression hits all of them; flight-OFF must be byte-for-byte legacy.
  Pattern: **a behavioral/security fix in a shared library is shipped dark behind a default-OFF ECS flight,
  not default-ON — verify the sibling flight's actual default before copying it.** Execution playbook:
  [references/remediation-execution.md](remediation-execution.md).
- _⚠️ "Already mitigated upstream" must be caught in the REPORT, not discovered mid-remediation_ — an
  unvalidated-`app_link`→`ACTION_VIEW` finding (cited at 3–4 sinks) turned out to be **already neutralized on
  current `dev`**: a shared allow-list validator (`is*Safe*BrokerInstallLink`) at the **redirect→result-code
  classifier** (`RawAuthorizationResult.fromRedirectUri`) rejects any non-allow-listed `app_link` *before* the
  install-result code is emitted, so none of the sinks are reachable with attacker input. The
  defense-in-depth sweep's **"Upstream validation"** row should have traced the data back to that classifier
  and flagged the finding **Won't-Fix / already-mitigated** — saving a redundant 5-file fix. Two failure modes
  to guard against: (1) the sweep stopped at the sink + immediate caller instead of tracing to where input is
  admitted; (2) the finding was **investigated on a stale snapshot** and the control landed afterward. Pattern:
  **for any "sink reached without validation" finding, trace untrusted input back to its admission/classifier
  point and check for an allow-list there; and at remediation time, re-verify the gap still reproduces on the
  current base-branch HEAD before writing code** (see remediation-execution.md "Pre-flight").
- _"Already-Covered / Won't-Fix" is a FIRST-CLASS category and the FIRST gate — high filing volume_ — a large
  and growing share of MSRC/ITD findings filed against us turn out to be **already covered by existing
  defense-in-depth** (upstream allow-list/validator, flight default, signature/package check, non-exported
  component, server-side number-match). Treat **Gate 0** ("is the sink already neutralized by a cited control
  on current HEAD?") as the *first* classification step, before the Engineer/Intern split. If yes →
  **`Won't-Fix (Already-Covered)`**, close it out, ship nothing, and it gets its own **Already Covered /
  Won't-Fix** section in the roll-up (0 eng-days). Pattern: **the safest change is the one we don't make** — a
  redundant "belt-and-suspenders" fix in a >1B-user shared library is regression risk for zero security gain.
  Conservatism cuts both ways: the category requires a **cited** covering control (not a hunch), because
  **not** everything is covered — when you can't prove a control, the finding is live and we solution it.

### Reporting / tooling calibration (report-UX learnings)

- _"Needs external validation" is ORTHOGONAL to owner/action — keep them as separate signals._ Folding
  external-validation into the Action column collapsed every row to "Needs input" (all 8 findings had
  `External validation: Yes`), erasing the Keep-&-fix vs Delegate distinction. A finding can need a
  server/downstream confirmation for its **severity** while its **fix still proceeds now** (e.g. the TOTP
  log-leak: fix unblocked, only the diagnostics-egress magnitude gated). Pattern: **Action = ownership
  (Keep & fix / Delegate); external-validation = its own ⚗ badge + count tile. Never let one override the other.**
- _Roll-up/CSV markdown must be written as UTF-8 via `--out`, never PowerShell `>` redirection._ `>` re-encodes
  through the console code page and corrupts Unicode (`·`→`┬╖`, `—`→`ΓÇö`). Pattern: **scripts that emit
  Unicode markdown take an `--out` path and write `encoding="utf-8"` directly.**
- _On-call is a Wed→Wed rotation; the report is an append model, not a fresh run._ Findings accumulate into a
  shift-scoped report keyed to the window; a manifest (`manifest.json`) lets re-runs skip already-triaged IcMs
  and append new ones. Always offer the engineer the entry-mode choice (triage-one / sweep-window / finalize /
  re-run-one) before doing work. A **Generated <timestamp>** stamp on the header makes a hung/stale run obvious.


## Defense-in-depth checklist (the "look beyond" sweep)

The past failure mode was stopping too early. For **every** finding, explicitly check each layer and record
what you found (or the search that proves absence):

| Layer | What to look for | Where |
|-------|------------------|-------|
| Component export | `android:exported`, intent-filter, permission | `AndroidManifest.xml` (all modules) |
| IPC boundary | caller package / signature / UID validation | Common IPC layer, Broker operation dispatch |
| Sibling handlers | do adjacent methods enforce allow-lists this sink skips? | same file as the sink |
| Flight gates | `CommonFlight*`, ECS default state | flight managers, `*FlightsManager` |
| Upstream validation | scheme/host/path allow-lists before the sink — **trace all the way to where untrusted input is ADMITTED/classified, not just the sink's immediate caller.** If a validator/allow-list at the input-admission point (e.g. a redirect→result-code classifier, an `is*Safe*`/`is*Allowed*` gate) already rejects attacker input before the sink is reachable, the finding may be **already mitigated → Won't-Fix / Low** — say so in the report instead of proposing a redundant sink-level fix | the dispatcher / **result-code / redirect classifier** feeding the sink (follow the data backwards to its entry) |
| Build/config gating | debug-only, test-only, emulator-only paths | `BuildConfig`, gradle, `if (DEBUG)` |
| Reachability conditions | what must be true at runtime to hit the sink | call-graph from a real entry point |
| **Threat boundary (scope)** | **is the ONLY path root / physical / debug-build / `adb`?** If so → out of scope (Won't-Fix). But first prove there is **no** non-root path (another app via IPC/Intent/deep-link, network/zero-click, or off-device egress like a diagnostics/log upload) | exported components, deep links, IPC dispatch, log/diagnostics egress |

A finding is only **CRITICAL** if **all** of these come back empty after a genuine search — and you can
show the searches.

## Agree / Rebut record

For each finding capture:
- **Filed:** `<source>` → `<their tier>` (e.g. Glasswing → IMPORTANT, Tier 1).
- **Ours:** `<our tier>`.
- **Delta:** AGREE / DOWN-CLASSIFY / UP-CLASSIFY.
- **Justification:** 1–3 sentences anchored to the cited evidence above.

## Adversarial verification & confidence

A classification is not final until a **second, independent `codebase-researcher` (the Challenger)** has
tried to **break it** (Step 3.5 in the skill). The Challenger attacks the weakest link of Pass 1:

- Mitigation cited? → find a path that **bypasses** it; check whether the control is itself
  reachable/poisonable (e.g. a remotely-updatable allow-list).
- "Not reachable"? → hunt for **another entry point** (other manifests, exported aliases, sibling callers).
- Down-classified? → build the strongest case it is **still exploitable**.

The Challenger must cite `file:line` and append its own "Searches Run" audit. Then set **Confidence**:

| Confidence | When | Effect |
|------------|------|--------|
| **High** | Challenger genuinely tried and **could not** break Pass 1; mitigations independently re-confirmed. | Verdict is trustworthy. |
| **Medium** | Challenger surfaced a caveat / partial gap, or a control holds only under conditions we can see but not fully prove. | Note the caveat; usable. |
| **Low** | Challenger found a plausible bypass, the passes disagree, or the verdict leans on an **unverifiable boundary** (downstream/server). | Human review before action. |

A finding that depends on a **Scope & Verification Boundary** disclaimer cannot be **High** confidence.

## Assignment cutoff (intern vs. engineer)

Apply this **two-factor cutoff** (our tier **and** component) so the on-call engineer only delegates
contained, lower-severity work:

| Condition | Assignment | Rationale |
|-----------|-----------|-----------|
| **Our tier ≤ Moderate (Moderate / Low / Won't-Fix) AND component = Authenticator app** | `Intern-eligible` | Contained to the app we fully own, lower blast radius — a bounded, well-understood fix (MSRC or ITD). |
| **Our tier ≥ Important** (regardless of component) | `Engineer-owned` | Real weakness; needs judgment + a coordinated fix. |
| **Any Broker / Common / MSAL component** (even Moderate/Low) | `Engineer-owned` | Library / cross-module / broker-privileged code — needs engineer ownership and downstream-impact judgment. |

So **Intern-eligible requires BOTH** a Moderate-or-lower tier **and** the Authenticator app. Important and
above go to engineers; anything in Broker/Common/MSAL goes to engineers. The component is the canonical repo
(Authenticator / Broker / Common / MSAL), derived from the finding's `**Component:**` field.

Caveat: if an **Intern-eligible** finding is **Low confidence**, flag it for an engineer sanity-check before
handing it off — we don't delegate something we're unsure about.

For every **Engineer-owned** finding, produce a dispatch-ready Remediation Spec
(see [remediation-spec.md](remediation-spec.md)). Intern-eligible findings get lighter **Fix Notes**.

## Eng-days heuristic (for roll-up)

Rough, author-adjustable, keyed off **our** tier:
- CRITICAL: 5–8 (fix + tests + coordinated release + MSRC process)
- Important: 3–5
- Moderate: 1–3
- Low / Won't-Fix: 0.5–1 (write-up + close)

Always flag these as estimates; the on-call engineer adjusts.
