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
- Defense-in-depth gap. Exploitation needs **unlikely preconditions**: root, a debuggable build,
  physical device access, or a non-default config.
- **Evidence required:** the cited precondition (e.g. file only readable on rooted device; component
  `android:exported="false"`) that blocks mass exploitation.

### Low / Won't-Fix
- Not reachable in the shipping config, OR already gated off by default.
- **Evidence required:** citation proving non-reachability — flight defaulting off, non-exported
  component, sibling handler enforcing an allow-list the finding assumed was missing, dead code, etc.

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


## Defense-in-depth checklist (the "look beyond" sweep)

The past failure mode was stopping too early. For **every** finding, explicitly check each layer and record
what you found (or the search that proves absence):

| Layer | What to look for | Where |
|-------|------------------|-------|
| Component export | `android:exported`, intent-filter, permission | `AndroidManifest.xml` (all modules) |
| IPC boundary | caller package / signature / UID validation | Common IPC layer, Broker operation dispatch |
| Sibling handlers | do adjacent methods enforce allow-lists this sink skips? | same file as the sink |
| Flight gates | `CommonFlight*`, ECS default state | flight managers, `*FlightsManager` |
| Upstream validation | scheme/host/path allow-lists before the sink | the dispatcher / classifier feeding the sink |
| Build/config gating | debug-only, test-only, emulator-only paths | `BuildConfig`, gradle, `if (DEBUG)` |
| Reachability conditions | what must be true at runtime to hit the sink | call-graph from a real entry point |

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

Apply a **simple severity cutoff** to **our** final tier so the on-call engineer can delegate safely:

| Our Tier | Assignment | Rationale |
|----------|-----------|-----------|
| **Low / Won't-Fix** | `Intern-eligible` | Bounded write-up/close or a small, well-understood gate. |
| **Moderate** | `Intern-eligible` | Defense-in-depth gap with unlikely preconditions; lower blast radius. |
| **Important** | `Engineer-owned` | Real weakness; needs judgment + a coordinated fix. |
| **CRITICAL** | `Engineer-owned` | Must-fix; coordinated release + MSRC process. |

Caveat: if an **Intern-eligible** finding is **Low confidence**, flag it for an engineer sanity-check before
handing it off — the cutoff is on severity, but we don't delegate something we're unsure about.

For every **Engineer-owned** finding, produce a dispatch-ready Remediation Spec
(see [remediation-spec.md](remediation-spec.md)). Intern-eligible findings get lighter **Fix Notes**.

## Eng-days heuristic (for roll-up)

Rough, author-adjustable, keyed off **our** tier:
- CRITICAL: 5–8 (fix + tests + coordinated release + MSRC process)
- Important: 3–5
- Moderate: 1–3
- Low / Won't-Fix: 0.5–1 (write-up + close)

Always flag these as estimates; the on-call engineer adjusts.
