# Remediation Execution (implement the fix & open the PR)

The rest of this skill stops at a **dispatch-ready spec** — handing the work to an engineer or the Copilot
coding agent. But the skill can also **execute the fix itself**: implement the change, write tests, build,
and open the PR. This doc captures the hard-won rules for doing that **safely on a PUBLIC repo**.

> ⚠️ Three of our four target repos (`common`, `msal`, `adal`) are **public on GitHub**. A security fix's
> *branch name, commit message, code comments, and test fixtures are all public the moment you push.* The
> diff itself is unavoidably public — so the discipline is to make the change read like routine hardening,
> never like a vulnerability writeup. Treat this as non-negotiable, same as the skill's commit banner.

---

## Pre-flight: re-verify the finding is STILL LIVE on the current base branch (do this FIRST)

**Before writing a single line of fix code, reproduce the gap on the current base-branch HEAD.** Findings are
investigated on a *snapshot*; the codebase moves between investigation and remediation, and a control may have
landed in the meantime. The spec's `file:line` citations can be stale.

Concretely, for the cited sink(s):
1. **Trace the untrusted input backwards** from the sink to the point it is *admitted/classified* (the
   redirect→result-code classifier, the dispatcher, the IPC entry). Confirm the sink is **reachable with
   attacker-controlled input on HEAD** — i.e., that no allow-list/validator upstream already rejects it. (Use
   `codebase-researcher`; this is the same "Upstream validation" trace from the defense-in-depth checklist.)
2. If an upstream validator/allow-list (e.g. an `is*Safe*`/`is*Allowed*` gate at the classifier) **already
   gates the sink**, the finding is **already mitigated** → **STOP. Do not ship a redundant fix.** Report it
   back as already-covered-by-defense-in-depth and recommend re-triage to Won't-Fix/Low (this should ideally
   have been caught in the report — see the calibration-log entry in `severity-rubric.md`).
3. Only proceed to implement when you can point to the still-open path on HEAD.

> Real example: a 3–4-sink "unvalidated `app_link` → `ACTION_VIEW`" finding was **already neutralized on dev**
> by a shared allow-list at the redirect classifier (`RawAuthorizationResult.fromRedirectUri` →
> `is*Safe*BrokerInstallLink`), making the whole fix redundant. It surfaced only when the fix's tests failed
> *because* the existing validator rejected the test input. Catch this in pre-flight, not after writing code.

## Prime directive: regression-safety over everything (these libraries ship to >1 billion users)

`common`, `msal`, `adal`, and the broker are consumed by Authenticator, Outlook, Teams, OneAuth and the
broader MSAL ecosystem — a regression here can break sign-in for **over a billion users**. Therefore, when
remediating, **the safest change that closes the gap always wins over the cleverer or more complete one.**
Concretely, in priority order:

1. **Smallest possible diff.** Touch the fewest lines/files that fix the issue. If a sub-change adds no
   security once the real guard exists, **delete it** — every extra changed line is regression surface.
2. **Gate behind a default-OFF ECS flight, flight-off = byte-for-byte legacy.** The fix must be a pure
   kill-switch: disabling the flight restores the exact prior behavior, so a field regression is a config
   flip, not a redeploy/rollback. Ramp progressively (1% → 10% → 100%) while watching success telemetry.
3. **Reuse an existing hardened sibling control** over inventing a new one (cite it). Match the existing
   contract (e.g. the value the pre-existing gate already required) rather than broadening it speculatively.
4. **Don't widen scope on a guess.** Allow-list exactly the **proven-legit** target(s). "It might also need
   X" is not a reason to permit X — name it as an open question instead (see boundary discipline below).
5. **Preserve behavior you can't fully verify.** If downstream consumers or the server might depend on the
   old behavior, the flight default-OFF + ramp is what makes that safe — never assume and never hard-cut.
6. **Prove the safe path with a rollback test** (`...legacyBehavior_whenValidationDisabled`) *and* a
   regression test that the **normal, legitimate** flow still works — not just the negative/blocked case.

When in doubt, ship **less**, **darker**, and **more reversible**. A finding can always be hardened further
in a follow-up; a sign-in outage for a billion users cannot be undone.

---

## Operate on the codebase via the right skills & conventions (don't free-hand it)

Remediation is still a **code change in this repo**, so it must follow the same grounding and conventions as
any other change here:

- **Ground every edit in cited evidence with `codebase-researcher`** before touching code — confirm the sink,
  the sibling controls, the flight mechanism, and the call sites at `file:line` (the investigation passes
  already did this; reuse their citations, don't re-derive loosely). Never edit a sink you haven't traced.
- **Follow the repo's custom instructions** (`.github/copilot-instructions.md`): use the `Logger` class (never
  `android.util.Log`); never log tokens/PII; respect the multi-repo flow (MSAL/OneAuth → Common → Broker);
  honor `build.gradle` dependency boundaries; and if you ever touch `OneAuthSharedFunctions`, **flag the
  breaking change to the OneAuth team** (per the instructions' nudge). New code is Kotlin-first, but **match
  the existing file's language/style** when editing — a security fix is not the time to convert Java↔Kotlin.
- **Prefer the related skills** where they fit: `codebase-researcher` for tracing, `incident-investigator`
  for the auth-failure side, and the `pbi-creator`/`pbi-dispatcher` path when handing off rather than
  self-implementing. This skill's remediation step is the "self-implement" branch of that same toolset.

---

## Golden rule: the public artifacts must not reveal the vulnerability

When you fix a security finding in a public repo, **four surfaces leak to the world**. Sanitize every one:

| Surface | ❌ Don't | ✅ Do |
|---------|---------|------|
| **Branch name** | `cesaracosta/itd-635851-intent-allowlist-bypass` | `cesaracosta/webview-intent-validation` (neutral feature name) |
| **Commit title/body** | "Fix MSRC intent-scheme bypass / CWE-939", IcM/MSRC numbers, "vulnerability/exploit" | Generic one-liner ("Add flighted validation in WebView intent handling") + **only** a corp-gated ADO work-item link |
| **Code comments** | "the intent:// scheme lets the page embed a component that redirects to an *attacker-chosen* activity" (teaches the attack) | State what the code *enforces*: "activity resolution is driven solely by the validated package" |
| **Test fixtures / names** | `TEST_INTENT_SPOOFED_PACKAGE`, `com.attacker.evil/.EvilActivity`, "decoy substrings satisfy the gate" | `TEST_INTENT_WITH_NON_ALLOWLISTED_PACKAGE`, `com.example.unrelatedapp/.SampleActivity`, neutral comments |

**The only sanctioned pointer to the sensitive context is the work-item link.** ADO (`dev.azure.com/IdentityDivision/...`),
IcM, and FireWatch are all corp-auth-gated, so a bare WI/IcM number is inert to an outsider — but the *finding
content paired with that ID* is what you must keep out of the public commit. Put the detail behind the link, not in the diff.

### Run the sweep before every push
Before pushing a security fix, grep the **branch name + commit message + full diff** for: `MSRC`, `IcM`,
the long IcM number prefix, `vulnerab`, `exploit`, `attacker`, `bypass`, `CWE`, `spoof`, `evil`, `PoC`,
`firewatch`, `glasswing`, `@microsoft.com`, tenant GUIDs. A hit on anything but the approved WI number = stop
and sanitize. (The unavoidable exception: the WI link in the commit body, which is allowed.)

---

## Process discipline (learned from a real run)

1. **Always present the diff and ask go/no-go before any push.** The user wants to verify the skill can
   execute one item end-to-end before trusting it. Default to **commit locally only**; never push or open a
   PR without explicit approval. "Share the branch" usually means *push the branch*, **not** *open a PR* —
   confirm which.
2. **Interrogate the change like a reviewer before shipping it** — the user's questions ("why this operand?",
   "how do we know X is the only valid value?", "what regression could this cause?") are the bar. Pre-empt them:
   - **Minimal surface.** If a sub-change adds no security once the real guard exists, **revert it.** (We
     reverted a validate-operand tweak because the post-parse allow-list was the actual guard — the tweak only
     added a case-sensitivity regression surface for zero security gain.)
   - **Don't widen allow-lists speculatively.** Allow-list exactly the **proven-legit** target(s), not every
     value that *might* be valid. Adding "known broker packages" to a Play-Store-only path was speculative; we
     narrowed it to the one package the pre-existing gate already required.
   - **Name the boundaries you can't verify** (server/eSTS emits the value, downstream consumers depend on old
     behavior) instead of asserting safety.
3. **All new behavior goes behind the feature flag — and flight-OFF must be byte-for-byte legacy.** Structure
   the sink so that when the flight is disabled, control flow is *identical* to the original (wrap the new
   logic in `if (flightEnabled) { ... }`; don't refactor the surrounding early-returns). This makes the flag a
   true kill-switch and shrinks the rollback risk to zero.
4. **Default the flight OFF for a security/behavioral change in `common`.** `common` ships to broker **and**
   MSAL **and** OneAuth consumers, so a regression hits everyone. Ship dark, ramp via ECS (1% → 10% → 100%)
   while watching telemetry. (Earlier guidance to default-ON "to mirror a sibling flight" was wrong — the
   sibling `ENABLE_PLAYSTORE_URL_LAUNCH` defaults to **`false`**. Verify the sibling's actual default; don't
   assume.)
5. **ECS = the repo's flight enum.** In `common`, `CommonFlight` *is* the ECS mechanism: the flights provider
   applies the ECS override at runtime and falls back to the enum default only when ECS has no value.
   Default-OFF therefore means **ECS must explicitly enable it** — exactly the "always gate behind ECS"
   convention. Don't invent a new flag system; add a `CommonFlight` enum constant with `("<EcsKey>", false)`.

---

## Building & testing the `common` module (gradle credentials gotcha)

`common/settings.gradle` reads `project.findProperty(...)` inside `pluginManagement`, which **throws
"Could not get unknown property 'project'"** unless two env vars are set (a ternary short-circuits on them).
The real creds live in `~/.gradle/gradle.properties` as `vstsUsername` / `vstsMavenAccessToken`. Working recipe:

```powershell
$gp = "$env:USERPROFILE\.gradle\gradle.properties"
$env:ENV_VSTS_MVN_CRED_USERNAME   = (Select-String -Path $gp -Pattern '^vstsUsername=(.+)$').Matches.Groups[1].Value.Trim()
$env:ENV_VSTS_MVN_CRED_ACCESSTOKEN= (Select-String -Path $gp -Pattern '^vstsMavenAccessToken=(.+)$').Matches.Groups[1].Value.Trim()
.\gradlew.bat :common:testLocalDebugUnitTest --tests "*YourTestClass" --console=plain
```

- Must run **online** — opentelemetry/androidx artifacts proxy through the authenticated ADO feed, not the
  offline cache. **Never print the token.**
- Use the **`local`** flavor (`:common:testLocalDebugUnitTest`): it builds `:common4j` from in-tree source via
  `localApi(project(":common4j"))`, so an edit to a `common4j` flight constant compiles without publishing.
- The real source checkout is the **main checkout** (`C:\src\android-complete\common`), *not* a skill worktree.
- Confirm your new tests actually ran by parsing the results XML
  (`common/build/test-results/testLocalDebugUnitTest/TEST-*.<TestClass>.xml`) — a JUnit4 test method missing
  `@Test` silently never runs (we found a pre-existing orphan test this way).

---

## Per-repo platform & identity (where the PR goes, and which credential opens it)

The four target repos do **not** all live on the same platform or use the same GitHub identity model. Pick the
right destination + credential **before** you push, or the PR step fails (or lands in the wrong place):

| Module | PR platform | Identity model | How to open the PR |
|--------|-------------|----------------|--------------------|
| **common** / common4j | **public GitHub** (`AzureAD/microsoft-authentication-library-common-for-android`) | **non-EMU** | Use the **local Git Credential Manager** identity (the non-EMU `gho_` token that pushes). |
| **msal** | **public GitHub** (`AzureAD/microsoft-authentication-library-for-android`) | **non-EMU** | Same as common — local non-EMU credential. |
| **broker** / broker4j | **GitHub Enterprise** (`identity-authnz-teams/ad-accounts-for-android`, GHE) | **EMU** (Enterprise Managed User) | Use the **EMU** identity for this org. |
| **authenticator** | **Azure DevOps** (not GitHub at all) | ADO | Open the PR in **ADO**, not GitHub — use the ADO MCP / `az repos pr`. |

**The MCP GitHub tool is bound to an EMU identity.** That makes it the right tool for **broker** (EMU/GHE) but
it **403s on the public `AzureAD` repos** (common/msal) with *"As an Enterprise Managed User, you cannot access
this content."* So:

- **common / msal (public, non-EMU):** the MCP `create_pull_request` tool will 403. **Fall back to the local
  non-EMU credential** — retrieve it from Git Credential Manager and call the GitHub REST API directly:

  ```powershell
  $cred = "protocol=https`nhost=github.com`n`n"
  $tok  = (($cred | git credential fill 2>$null) | Where-Object { $_ -like 'password=*' }).Substring(9)  # gho_…, has push
  # POST https://api.github.com/repos/AzureAD/<repo>/pulls  with Authorization: Bearer $tok  (draft=true)
  ```
  (`git push` already uses this identity, which is why the push succeeds even when the MCP PR call fails.)
  **Never print the token.** `gh` CLI is not installed in this environment — use `curl`/`Invoke-RestMethod`.
- **broker (EMU/GHE):** the MCP GitHub tool / EMU identity is correct here — the local non-EMU token will not
  have access to the GHE org.
- **authenticator (ADO):** there is no GitHub PR — branch/push and open the PR in Azure DevOps.

> Net: **match the credential to the repo's identity model.** Public AzureAD (common/msal) = local non-EMU
> token; GHE broker = EMU/MCP; authenticator = ADO. A 403 on a public repo almost always means you used the
> EMU identity by mistake — switch to the local credential, don't assume the push itself failed.

---

## Push & PR mechanics

- Commit locally first; include the standard **`Co-authored-by: Copilot` trailer** the repo mandates (see the
  repo commit guidelines for the exact address).
- After a sanctioned change to an already-pushed branch, prefer `git push --force-with-lease` (safe overwrite).
- Pushing a branch ≠ opening a PR. GitHub prints a "create a PR" link on push; that does **not** open one.
  Only open the PR on explicit approval, and keep the PR title/body under the same public-safe rules (vague
  title, WI link, surface any `external_validation_needed` "do NOT close until confirmed" question in the body).
- Open security PRs as **draft** so reviewers aren't auto-requested before the external-validation answers land.

---

## Cross-session execution tracker (resume work across sessions)

Remediation is frequently done **one MSRC per session**, often days apart. Without a durable record, each new
session re-derives "what's done vs. not" from git archaeology. Avoid that: keep a single
**`EXECUTION-TRACKER.md` in the workspace** (`$VULN_TRIAGE_WORKSPACE/msrc/<window>/EXECUTION-TRACKER.md` —
**not** in the repo). It is the **bridge** between the sanitized repo artifacts and the real finding identity:
because it lives in the private workspace, it *may* hold the real IcM↔WI↔branch↔commit↔PR linkage (that is its
purpose).

**Create it on first execution; update it at every milestone.** Status vocabulary:
`NOT STARTED` → `IN PROGRESS` → `IMPLEMENTED (local)` → `PUSHED (no PR)` → `PR OPEN` → `MERGED` /
`BLOCKED` / `OUT OF SCOPE (intern)`.

Each finding gets an at-a-glance row **and** a detail block recording: spec path, WI, repo(s) + PR platform,
branch + checkout path, commit SHA, PR URL + draft state, a **What was done** checklist, a **What is NOT done /
pending** list (esp. `external_validation_needed` gates), and a one-line **Resume hint**. Suggested skeleton:

```markdown
## <IcM ID> — <short title>  ·  <STATUS>
- Spec: agent-specs/<slug>.agent.md · WI: <n> · Repo(s): <…> · <Sev> / <Assignment>
- PR platform: <public GitHub non-EMU | GHE EMU | ADO>
- Branch: <name> (in <checkout path>) · Commit: <sha> · PR: <url> (<draft?>)
- What was done: [x] … [x] …
- What is NOT done / pending: [ ] external validation … [ ] merge/ramp …
- Resume hint: <one line a fresh session can act on>
```

Keep intern-eligible findings in an **Out of scope** section so the next session doesn't accidentally pick one
up. On every push/PR action, update the matching row **and** detail block in the same turn — a stale tracker is
worse than none.
