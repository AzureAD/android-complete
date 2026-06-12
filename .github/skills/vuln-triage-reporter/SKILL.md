---
name: vuln-triage-reporter
description: Triage and classify MSRC/ITD security vulnerabilities filed against Android Authenticator & Broker, and produce evidence-based classification reports for on-call handoff and WBR. Use this skill when an on-call engineer needs to process recent [MSRC]- or [ITD]-tagged IcMs, decide whether to agree with the security team's filed severity or rebut it with code evidence, and generate per-finding + aggregate reports. Triggers include "triage MSRC", "classify these vulnerabilities", "investigate ITD findings", "on-call security report", "review FireWatch findings", "are these MSRCs really that severe", or any request to assess/right-size security vulnerability severity for Android Auth.
---

# Vulnerability Triage & Reporter

Right-size MSRC/ITD vulnerability severity for Android Authenticator & Broker using **deep,
evidence-based codebase analysis**, then produce on-call/WBR reports.

This skill is for **on-call engineers during their on-call week**. Default scope is the **past 7 days**
(the rotation length), parameterized so it can be widened.

> ⚠️ **PUBLIC SKILL — DO NOT COMMIT SENSITIVE INFORMATION HERE.** This repo is mirrored to a public
> GitHub repo. Apply this test to anything you add: *"could an outsider with **no** Microsoft access act
> on this?"* If yes, it does **not** belong in this skill.
>
> **NEVER put here (genuinely sensitive — actionable without access):**
> - **Telemetry sampling rates or per-product coverage percentages** (these are an evasion map).
> - **Internal security-control logic** — exact flight names, the precise conditions under which a
>   security check is bypassed/skipped, and `file:line` into private submodules describing such logic.
> - **PII / customer data / tenant GUIDs / UPNs / aliases**, and **finding content paired with an IcM ID**.
>
> **OK to include (opaque — useless without corp access):** IcM numbers, IcM team-routing IDs,
> service-tree GUIDs, team/service/codenames. These are inert to an outsider (IcM, ServiceTree, FireWatch,
> S360 are all corp-auth-gated).
>
> **All investigation OUTPUTS are sensitive and live OUTSIDE the repo** in the private workspace
> `$VULN_TRIAGE_WORKSPACE` (default `~/vuln-triage-workspace`) — never under the repo tree.
> **Any future edit to this skill must preserve these rules.**

> **Related skills.** This is the security-vulnerability counterpart to `incident-investigator` (which
> handles auth-failure/log incidents). For all codebase exploration you **MUST** use `codebase-researcher`
> — see the hard requirement in "Non-Negotiables" below.

---

## Why This Skill Exists (read this first)

The security team files MSRC/ITD vulnerabilities against us, each with a **pre-assigned classification**
(e.g. FireWatch/Glasswing: `IMPORTANT`, `Tier 1 — Direct Exploit`). **That classification is an input,
not a verdict.** Our job is to **agree with it or rebut it with documented code evidence**, so that
engineering effort is allocated to what actually matters versus competing priorities.

These findings are frequently **over-rated**: a real weakness exists, but the codebase already has
**defense-in-depth** (flight gates, allow-lists, package/signature checks, non-exported components,
root-only reachability) that prevents real-world mass exploitation.

### ⚠️ The failure we are correcting

**In past investigations, AI agents did NOT analyze deeply enough.** They read the vulnerable sink, saw a
plausible exploit, and either rubber-stamped the filed severity OR claimed defense-in-depth existed
without proving it. **Both are failures.** The recurring mistake: stopping at the first or second layer of
analysis and missing mitigating (or aggravating) controls that exist **beyond** the obvious code path.

**The rule: always look for coverage beyond.** For every finding, you must actively hunt for controls in
*adjacent* layers — the caller, the manifest, the IPC boundary, sibling handlers, flight defaults, build
config, and the runtime reachability conditions — before concluding anything. A shallow "no mitigation
found" is the exact error this skill exists to prevent. If you cannot find a control, you must show the
*searches you ran* that justify its absence (mirror `codebase-researcher`'s "Not Found" discipline).

### Tell the defense-in-depth story — but only what you can prove

Most findings filed against us are, in practice, **covered by some defense-in-depth mechanism**. When you
have **sufficient evidence**, say so explicitly in a **"Defense-in-Depth: Why Likely Not Exploited"**
section — the concrete reason real-world exploitation is unlikely (the gating flag, the server-validated
number-match, the non-default path, the signature allow-list, etc.). This is what right-sizes severity.

**Verification-boundary discipline (critical for honesty).** We own the **Authenticator client** and the
**Broker/Common libraries** — we can prove things about *that* code. We do **not** own:
- **Downstream consuming apps** (Outlook, Teams, OneAuth, other MSAL callers) — a caller may add its own
  validation, pick the browser path, pass a nonce, etc. We cannot observe this.
- **Server-side** (eSTS / MFA backend / issuance) — we can often only *infer* enforcement from the protocol.

For anything outside our boundary, **do not assert it as fact**. Add a **"Scope & Verification Boundary"**
disclaimer stating: it is possible downstream services or the server apply additional checks, but we cannot
conclude definitively, and it would be worth investigating. **Only confirm what you can.** This cuts both
ways — never claim "safe" *or* "exploitable" about a boundary you couldn't verify.

---

## Non-Negotiables

1. **Run investigations in PARALLEL.** Each finding is independent. Dispatch one investigation per finding
   concurrently (use the `codebase-researcher` subagent / `runSubagent`, or parallel `Explore` agents).
   Do **not** process findings sequentially when more than one is in scope.
2. **MUST use `codebase-researcher`** for every code-evidence step. Do not free-hand grep and call it
   analysis. The classification's credibility rests on cited `file:line` evidence gathered systematically.
3. **Preserve the "Searches Run" audit trail VERBATIM.** Every investigation must end with a
   `## Searches Run (audit trail)` section listing the actual search patterns/paths run and what each
   returned — especially the searches that returned **nothing** (the absence proofs behind every
   "no mitigation found" / "not reachable" claim). This is non-optional: the subagent's granular tool
   calls are not retained, so this section IS the audit trail. Copy it into the finding's report; do not
   summarize it away.
3. **Every severity call needs evidence.** Cite the sink AND every mitigating/aggravating control with
   `file:line`. No control found? Show the searches that prove the absence.
4. **Agree-or-rebut explicitly.** State FireWatch's filed classification, then state ours, then the delta
   and the evidence that justifies any change.
5. **No PoC payloads or PII** in committed artifacts. Keep detail at engineering-triage level.
6. **Scripts, not one-liners.** Use the committed scripts in `scripts/` for discovery, scaffolding,
   transcription, and roll-up so the weekly run is repeatable.
7. **Generate the HTML evidence record per finding.** The master report's table is a summary; the real
   proof lives in one HTML subpage per finding (sink + defense-in-depth sweep + recommended fix + the
   verbatim "Searches Run" audit). Generate them with `scripts/build_research_pages.py` and link each
   master-table row to its subpage. Reviewers must be able to verify every severity call without chat access.

## Searching the Authenticator app code (critical gotcha)

The Authenticator app + MSA SDK live in **git-ignored submodules** (`authenticator/PhoneFactor/`, and
broker app code under `broker/AADAuthenticator/`). Standard workspace search returns **zero** results for
these unless you pass `includeIgnoredFiles: true`. Rules the subagents MUST follow:
- Always set `includeIgnoredFiles: true` AND scope with `includePattern: authenticator/PhoneFactor/**`
  (or narrower) — an unscoped ignored-file regex grep times out.
- `file_search` does **not** see ignored files at all — use `grep_search` with an `includePattern` that
  names the file to locate it, then `read_file`.
- For **binary Maven dependencies** (e.g. `com.microsoft:tokenshare`), the in-tree source doesn't exist —
  verify the **actual shipped artifact** via `javap` on the cached `.aar` from the Gradle cache.
- See repo memory `/memories/repo/security-triage-metadata.md` and `/memories/repo/authenticator-search.md`
  for the verified path conventions and team/service-tree IDs.

---

## Workflow

### Step 0 — Scope the week & resolve IDs
Default window = **past 7 days**. Query **all** of the IcM owning teams below (missing a queue drops findings):

| Team ID | Name |
|---------|------|
| 65431 | Cloud Identity AuthN MSAL Android |
| 65436 | Cloud Identity AuthN ADAL Android |
| 78848 | Auth Client Android Shield |
| 148914 | Android Microsoft Authenticator App |

> These are routing integers (safe to list — inert without corp IcM access). **Refresh them at the start**
> of each run via the IcM MCP `get_teams_by_name` in case team routing changed, and cache the result to
> private repo memory (`/memories/repo/security-triage-metadata.md`). The fuller metadata (service-tree
> IDs, the ITD↔FireWatch GUID map) also lives in that memory file.

### Step 1 — Discover findings (scripted)
Query IcM for `[MSRC]` / `[ITD]` incidents in the window for both teams. Use the IcM MCP
(`search_incidents` with `owningTeamId` + `dateRange`, or `keywords`/`tags`), write results to the
session resource files, then summarize with [`scripts/discover_findings.py`](scripts/discover_findings.py)
to emit the inventory table (ID, tag, title, vuln class, component, sev, state, date).

### Step 2 — ITD manual intake (FireWatch is not MCP-reachable)
FireWatch/Glasswing findings are **not** available through the Security MCP server (confirmed). They must
be retrieved manually:
1. Agent scaffolds one folder per finding under `$VULN_TRIAGE_WORKSPACE/msrc/itd-investigations/`
   (out-of-repo; default `~/vuln-triage-workspace`) using
   [`scripts/scaffold_itd.py`](scripts/scaffold_itd.py).
2. **Ask the user** to open each FireWatch finding and **Save Page As → "Web Page, Complete"** into the
   matching folder. The saved `_files/report-content.html` holds the full report — it is required.
3. Agent transcribes each saved report with
   [`scripts/transcribe_finding.py`](scripts/transcribe_finding.py) into the folder's `README.md`.

> See [references/itd-intake.md](references/itd-intake.md) for the exact user instructions and the saved
> HTML structure.

### Step 3 — Investigate each finding IN PARALLEL (codebase-researcher)
For each finding, dispatch a `codebase-researcher` investigation that returns:
- **The sink** — the vulnerable code, cited `file:line`.
- **Reachability** — is the sink reachable in a *shipping* configuration? What conditions gate it?
- **Defense-in-depth sweep (look beyond!)** — actively search adjacent layers for mitigating controls:
  - Caller / entry wiring (is the component exported? `AndroidManifest.xml`)
  - Sibling handlers in the same file (do they enforce allow-lists this one skips?)
  - Flight/feature-flag gates (`CommonFlight*`, ECS defaults)
  - IPC boundary checks (package name, signature, caller UID)
  - Build/config gating (debug-only, test-only, root-only reachability)
  - Any validation upstream of the sink
- **Aggravating factors** — anything that makes it *worse* than filed (unflighted, exported, no allow-list).

Use the severity rubric in [references/severity-rubric.md](references/severity-rubric.md).

### Step 4 — Classify (agree or rebut)
For each finding, produce our classification and the agree/rebut delta vs. FireWatch, with evidence.
Use [references/report-template.md](references/report-template.md).

### Step 5 — Report
- **Per-finding report** → the finding's folder `README.md` (or `msrc-investigations/<n>-<id>-<slug>.md`),
  ending with the verbatim `## Searches Run (audit trail)` section.
- **HTML evidence subpages** → run `scripts/build_research_pages.py` over the per-finding markdown to
  produce one self-contained, shareable HTML page each (CSS inlined; `file:line` citations rendered as
  visible evidence chips, not broken links) plus an `index.html`. Each page must contain a **Description**
  and a **How It Can Be Exploited** section (high-level attack narrative, no PoC/PII). A **Glossary** of the
  acronyms/concepts used on that page is auto-appended from `references/glossary.md` — add new terms there.
- **Master HTML report** → overview table linking each row to its IcM, FireWatch finding, and its
  **Research** subpage; severity legend; scope summary; capacity narrative.
- **Aggregate roll-up** → counts, severity breakdown (ours vs. filed), estimated eng-days, at-risk
  commitments — generated with `scripts/rollup.py`. Suitable for on-call handoff and the bi-monthly WBR.

---

## Severity Classification (summary)

Full rubric + required evidence per tier: [references/severity-rubric.md](references/severity-rubric.md).

| Our Tier | Meaning | Required evidence |
|----------|---------|-------------------|
| **CRITICAL (must fix)** | Reachable in prod, no mitigating control, real-world exploitable | Sink `file:line` + confirmed reachability + proven absence of any gate |
| **Important** | Real weakness, but partial mitigation / elevated prerequisites | Sink + the specific mitigation limiting blast radius, cited |
| **Moderate** | Defense-in-depth gap; needs unlikely preconditions (root, debug build, physical access) | Cited precondition that blocks mass exploitation |
| **Low / Won't-Fix** | Not reachable in shipping config, or already gated off | Citation proving non-reachability (flight default off, non-exported, sibling allow-list, etc.) |

**A down-classification is only valid if the mitigating control is cited with `file:line`.** "I didn't find
an exploit path" is not evidence — show the control, or show the searches proving its absence.

---

## Output

Per-finding `README.md` + an aggregate roll-up. Both must make the **agree/rebut decision explicit** and
cite code evidence for every severity call. Keep the aggregate short enough to drop into a shared WBR.
