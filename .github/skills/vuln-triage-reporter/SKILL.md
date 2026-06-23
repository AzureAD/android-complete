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
>
> 🔒 **MANDATORY before ANY commit that touches this skill: run the public-repo safety check**
> (`scripts/safety_check.py`, see "Pre-Commit Safety Check" below). Never commit skill changes without it.
> This is non-negotiable — sensitive information committed to a public repo cannot be un-leaked.

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
3. **MANDATORY adversarial verification pass.** After the first investigation classifies a finding, dispatch
   a **second, independent `codebase-researcher`** whose only job is to **break the conclusion** — challenge
   every cited mitigation, hunt for a bypass, and try to reach the sink another way. Only after the
   challenger reports do you finalize. Record the outcome and set a **Confidence** level (High/Medium/Low).
   This is the core correction for the past failure — a single pass is not trustworthy. See
   "The two-pass model" below.
4. **Preserve the "Searches Run" audit trail VERBATIM.** Every investigation (both passes) must end with a
   `## Searches Run (audit trail)` section listing the actual search patterns/paths run and what each
   returned — especially the searches that returned **nothing** (the absence proofs behind every
   "no mitigation found" / "not reachable" claim). This is non-optional: the subagent's granular tool
   calls are not retained, so this section IS the audit trail. Copy it into the finding's report; do not
   summarize it away.
5. **Every severity call needs evidence.** Cite the sink AND every mitigating/aggravating control with
   `file:line`. No control found? Show the searches that prove the absence.
6. **Agree-or-rebut explicitly.** State FireWatch's filed classification, then state ours, then the delta
   and the evidence that justifies any change.
7. **Assign every finding, and solution the ones we keep.** Set an **Assignment** using the cutoff:
   **Intern-eligible when our tier is Moderate or lower (Moderate/Low/Won't-Fix) AND the component is the
   Authenticator app; everything else (Important+, or any Broker/Common/MSAL) → Engineer-owned.** For every
   engineer-owned
   (kept) finding, produce a **dispatch-ready Remediation Spec** (root cause, fix approach, files to change,
   test plan, risks/rollout) — see [references/remediation-spec.md](references/remediation-spec.md).
8. **No PoC payloads or PII** in committed artifacts. Keep detail at engineering-triage level.
9. **Scripts, not one-liners.** Use the committed scripts in `scripts/` for discovery, scaffolding,
   transcription, and roll-up so the weekly run is repeatable.
10. **Generate the HTML evidence record per finding.** The master report's table is a summary; the real
    proof lives in one HTML subpage per finding (sink + defense-in-depth sweep + remediation spec + the
    verbatim "Searches Run" audit). Generate them with `scripts/build_research_pages.py` and link each
    master-table row to its subpage. Reviewers must be able to verify every severity call without chat access.
11. **Run the public-repo safety check before committing.** Any commit touching this skill MUST be preceded
    by `scripts/safety_check.py` (see "Pre-Commit Safety Check"). A non-zero exit blocks the commit.
12. **Map to an IcM Sev, conservatively.** Translate the analytical tier to the team's IcM severity
    (Sev2/2.5/3/4) using the mapping in [references/severity-rubric.md](references/severity-rubric.md).
    **Sev2.5+ is a rare, high bar** — only when High confidence + proven shipping reachability + proven
    absence of any safeguard + not leaning on an unverifiable boundary. When in doubt, go lower.
13. **Capture learnings back into the skill.** When a run surfaces a reusable insight — a new tier→Sev
    calibration point, a recurring safeguard pattern, a codebase-search gotcha, an estimate heuristic —
    record it in the right place (the **calibration log** in `references/severity-rubric.md`, the relevant
    reference doc, or repo memory) and include it in the commit. The skill must get smarter every rotation.
14. **NEVER create ADO work items without explicit user approval.** Creating PBIs/bugs from findings is an
    **opt-in, separate step** (see "Step 6 — Create PBIs"). Always present the proposed items (titles,
    tier, parent, area/iteration, assignee) and **wait for the user to confirm** before creating anything.
    Never auto-create, never assume the parent or assignee. This is non-negotiable — unwanted work items
    are noise the team has to clean up.

## The two-pass model (verify before you trust)

A single investigation — however well-cited — is **not** sufficient, because the failure mode is *not knowing
what you missed*. Every finding goes through two independent `codebase-researcher` passes:

1. **Pass 1 — Investigator.** Finds the sink, runs the defense-in-depth sweep, proposes a classification with
   cited evidence (the existing workflow).
2. **Pass 2 — Challenger (adversarial).** A *separate* agent that receives Pass 1's conclusion and is
   instructed to **disprove it**: if Pass 1 said "mitigated by X", the challenger tries to bypass X; if Pass 1
   said "not reachable", the challenger hunts for another entry path; if Pass 1 down-classified, the challenger
   builds the strongest case that it's still exploitable. The challenger must cite `file:line` too and append
   its own "Searches Run" audit.

**Set Confidence from the result:**

| Confidence | When |
|------------|------|
| **High** | Challenger ran a genuine attempt and **could not** break Pass 1; both agree; mitigations independently re-confirmed. |
| **Medium** | Challenger surfaced a caveat / partial gap, or a control holds only under conditions we can see but not fully prove. |
| **Low** | Challenger found a plausible bypass, the two passes disagree, or the conclusion leans on an **unverifiable boundary** (downstream/server). Low-confidence findings need human review before action. |

Run the challenger passes in **parallel** across findings, just like Pass 1. Both passes' evidence and audits
go into the finding's report (Pass 2 under an `## Adversarial Verification` section).

## Timing & ETA (tell the user up front, and watch for hangs)

Each `codebase-researcher` pass is a deep investigation. **Observed timings** (one finding, against a
full local checkout): a single pass runs **~4–8 minutes** (typically ~3.5 min for a contained
Authenticator-app finding, up to ~7–8 min for a cross-module `common`/`broker` finding with many sinks).

Because Pass 1 and Pass 2 both run **in parallel across findings**, wall-clock time is roughly:

> **ETA ≈ (longest Pass 1 ≈ 8 min) + (longest Pass 2 ≈ 8 min) + reporting ≈ 5 min ≈ 20 minutes**, largely
> independent of how many findings (parallelism), as long as the agent fleet can run them concurrently.

**Always give the user an ETA before launching** (e.g. *"Investigating N findings in two parallel passes —
expect ~15–25 minutes"*) so they know what to expect.

**Hang detection — important.** Background agents can occasionally stall or be cleared (e.g. a long idle gap
between turns). Rules:
- If a pass has not returned in **~12 minutes** (≈1.5× the worst-case single-pass time), treat it as hung.
- Check status; if it is gone/stalled, **relaunch that specific pass** (the others' results are unaffected).
- Do **not** silently wait indefinitely — surface the stall to the user and restart the affected pass.
- Each pass is independent and idempotent, so relaunching one finding's pass does not disturb the others.

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

### On-call mode — pick an entry point first
This skill runs during an **on-call rotation** (primary is **Wednesday → Wednesday**). Before doing
anything, **ask the engineer which mode they want** — the right answer depends on where they are in the week:

| Mode | When to use | What it does |
|------|-------------|--------------|
| **(a) Triage one IcM now** | A new `[MSRC]`/`[ITD]` just landed | Research that single ID (two-pass) and **append** it to the current shift report. |
| **(b) Sweep my shift window** *(default)* | Catching up / mid-shift | Query the 4 teams for findings in `[shift-start … now]`, **diff against the manifest**, triage only the **new** ones, append. |
| **(c) Finalize / refresh roll-up** | End of shift, or after a hang | Re-render the master report + roll-up from existing findings — **no new research** (fast, safe). |
| **(d) Re-run one finding** | A pass hung or evidence looks thin | Re-investigate a single finding and replace its record. |

> **Recommended default = (b)**. If the engineer is unsure, offer (b) and tell them it only researches
> findings not already in the shift report.

**Shift report = an append model, not a fresh run each time.** The report is keyed to the **Wed→Wed
window** and findings accumulate into it as IcMs arrive. Maintain a small manifest so re-runs append
instead of overwrite:

- Manifest file: `$VULN_TRIAGE_WORKSPACE/msrc/<shift>/manifest.json` — a JSON map of
  `{ "<IcM id>": { "first_seen": "<ISO>", "slug": "<n-class-component>" } }`.
- In mode (a)/(b), **skip** any IcM already in the manifest (it's already triaged); add new ones with
  `first_seen = now`. Mode (c) just re-renders over whatever findings already exist.
- Render with the **shift flags** so the report is framed correctly and stamped:
  `build_master_report.py … --shift "Wed 2026-06-11 -> Wed 2026-06-18" --owner "<on-call label>"`.
  The header then shows the shift window, a **Generated <timestamp>** stamp (so a stale/hung run is
  obvious), and a "findings appended as IcMs arrive" note.

> ⚠️ **Owner label is workspace-only.** You may put a human name/alias in `--owner` because the report
> lives in the **private** `$VULN_TRIAGE_WORKSPACE` — **never** commit an alias into the skill repo.

### Step 0 — Scope the week & resolve IDs
Default window = **the current Wed→Wed shift** (≈ past 7 days). Query **all** of the IcM owning teams
below (missing a queue drops findings):

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

### Step 3.5 — Adversarial verification IN PARALLEL (codebase-researcher, second pass)
For each finding, dispatch a **second, independent** `codebase-researcher` (the **Challenger**) that
receives Pass 1's conclusion and tries to **break it**:
- If Pass 1 cited a mitigation, attempt to **bypass** it (find a path that skips the allow-list / flight /
  package check; check whether the control is itself reachable/poisonable).
- If Pass 1 said "not reachable", hunt for **another entry point** to the sink (other manifests, other
  callers, exported aliases, intent filters).
- If Pass 1 **down-classified**, build the strongest case that it is **still exploitable**.
- The Challenger cites `file:line` and appends its own "Searches Run" audit.

Then **reconcile**: keep, raise, or lower the Pass 1 verdict, and set **Confidence** (High/Medium/Low) per
the table in "The two-pass model". Disagreement or an unverifiable boundary ⇒ at most **Medium**, usually **Low**.

### Step 4 — Classify & assign (agree or rebut)
For each finding, produce our final classification and the agree/rebut delta vs. FireWatch, with evidence,
plus the **Confidence** from Step 3.5. Then set the **Assignment** using the cutoff:
- **`Intern-eligible`** — when our tier is **Moderate or lower (Moderate/Low/Won't-Fix) AND the component is
  the Authenticator app**. Contained to the app we fully own, lower blast radius — safe to delegate (MSRC or ITD).
- **`Engineer-owned`** — **everything else**: any **Important+** finding, or any **Broker/Common/MSAL**
  component. We keep these and solution them (Step 4.5). Library and broker-privileged findings always stay here.

> The cutoff is two-factor: an intern only takes a finding that is both lower-severity (≤ Moderate) **and**
> contained to the app we fully own (Authenticator). Confidence is advisory: if an intern-eligible finding is
> **Low confidence**, flag it for a quick engineer sanity-check before handing it off.

Use [references/report-template.md](references/report-template.md).

### Step 4.5 — Solution the kept findings (remediation spec)
For every **Engineer-owned** finding, produce a **dispatch-ready Remediation Spec**:
root cause, fix approach, exact files to change (`file:line`), test plan, and risks/rollout (flighting).
Use [references/remediation-spec.md](references/remediation-spec.md). It must be detailed enough to hand to
an engineer or the Copilot coding agent / `pbi-creator` without further investigation. For Intern-eligible
findings, a lighter **Fix Notes** block is sufficient.

### Step 5 — Report (two coordinated artifacts per finding)
Each finding yields a **human report** and a **machine-readable agent spec** — see
[references/agent-spec-template.md](references/agent-spec-template.md) for the dual-output rationale + schema.

- **Per-finding report (human source)** → the finding's folder `README.md` (or
  `msrc-investigations/<n>-<id>-<slug>.md`), including a `**Bottom line:**` TL;DR field, the
  `## Adversarial Verification` section (Pass 2), `## Verification Gaps & What We Need to Confirm`,
  `## Decisions Needed`, and ending with the verbatim `## Searches Run (audit trail)` section.
- **Agent dispatch spec (machine-readable)** → run `scripts/build_agent_spec.py` over each README to emit a
  `<slug>.agent.md`: YAML front-matter (`finding_id, our_tier, icm_sev, confidence, assignment,
  target_repos, files_to_change, external_validation_needed, status, blocked_on`) + a Dispatch Block
  (problem statement, acceptance criteria = the negative test, do-not-proceed-until gating, constraints).
  This is what the Copilot coding agent / `pbi-creator` consumes to open a PR **without scraping prose**.
  Generate the specs **first** so the HTML can link them.
- **HTML evidence subpages (human, curated)** → run `scripts/build_research_pages.py` with
  `--agent-dir ../agent-specs` to produce one self-contained, shareable HTML page each (CSS inlined;
  `file:line` citations as visible evidence chips) plus an `index.html`. Each page opens with a band of
  **colorful stat tiles** (Our Severity · **Component / Repo** · IcM Severity · Confidence · Verdict ·
  Investigation Passes · **External Validation** · Assignment — each kept concise; tiles with a ↓ jump to
  the matching detail section), a **Bottom line** TL;DR, then
  **Description** and **How It Can Be Exploited** (high-level, no PoC/PII). The heavy **Searches Run** audit
  is auto-collapsed into a `<details>` for readability, an **On this page** TOC links the major sections,
  and a header **"Fix this with an AI agent"** button links
  the `.agent.md`. A **Glossary** of the terms used is auto-appended from `references/glossary.md`.
  > **Surface what you could NOT test.** Many real exploits require conditions an AI agent cannot reproduce —
  > a runtime device repro, a specific tenant/server state, code in a downstream repo we don't own. The
  > **Verification Gaps** table makes each explicit (open question · *why* untestable statically · what we
  > confirmed instead · the concrete ask · severity effect) and the **Decisions Needed** box lists the
  > judgment calls a human must make. So the user knows exactly where to supply info, and neither a human nor
  > an agent stalls on a gap they can route around. Required whenever `External Validation = Yes`.
- **Master HTML report (self-contained)** → run `scripts/build_master_report.py` over the same finding
  markdown with `--out <run_dir> --research-dir research --agent-dir agent-specs` to emit
  `wbr-security-report.html` in the run dir. It has summary stat cards (incl. a **Needs external validation**
  count), the severity legend, and a master table: **IcM · Tag (MSRC/ITD) · Component · Filed · Ours · Conf ·
  Verdict · Owner (E/I) · Eng-days · Vulnerability · Research**. A ⚗ **ext** badge marks rows whose
  severity still hinges on a server/downstream control we can't statically verify, and an **Exports** strip
  links the roll-up + CSV that ship in the same folder. For an on-call **shift report**, add
  `--shift "Wed <start> -> Wed <end>" --owner "<label>"` — this re-frames the header, adds a **Generated
  <timestamp>** stamp (so a hung/stale run is obvious), and a "findings appended" note. The research subpages'
  "Back to WBR overview" link points here, so **the run folder is fully self-contained — never reuse a prior
  run's overview.** Generate it AFTER the subpages + specs exist.
- **Aggregate roll-up** → counts, severity breakdown (ours vs. filed), confidence + IcM-Sev breakdown, an
  **Intern Queue** (Moderate↓ + Authenticator, delegatable) vs. **Engineer-owned** (everything else, kept with
  remediation) split, estimated eng-days, and at-risk commitments — generated with
  `scripts/rollup.py classifications.csv --out <run_dir>/_ROLLUP.md`. (Owner E/I is the action split — the two
  sections ARE keep-&-fix vs delegate; no separate Action column.)
  > ⚠️ **Always pass `--out`** so the markdown is written as UTF-8. Do **not** use PowerShell `>` redirection —
  > it re-encodes through the console code page and corrupts the Unicode (`·` → `┬╖`, `—` → `ΓÇö`).
  For on-call handoff and the bi-monthly WBR.

### Step 6 — Create PBIs (OPTIONAL — only on request, ALWAYS with approval)

Creating ADO work items is a **separate, opt-in step** the user must ask for — never a default part of a
triage run. When asked to create PBIs/bugs from findings (see Non-Negotiable #14):

1. **Propose first.** Present the proposed items as a table — title, our tier, IcM, eng-days, target
   **parent**, area/iteration, assignee — and **wait for explicit approval.** Never assume the parent or
   the assignee; ask.
2. **Default parent = the team's standing "Keep the Lights On" (KTLO) feature** on the *Auth Client - Android*
   board — that is where ongoing security-triage/bug work belongs. **Look it up at creation time** (IDs and
   iterations rotate) and confirm the exact ID with the user. The Summer-2026 intern feature (an intern
   batch under `[Summer 2026] Deliverable Payload`) was a **one-time** exception — do not reuse it as the
   default.
3. **Inherit area + iteration from the chosen parent** unless the user overrides. Leave **unassigned**
   unless the user names an assignee (intern aliases are usually not known at creation time).
4. **One PBI per fix, not per IcM.** When two findings share a single root cause + fix (e.g. ITD 635330 +
   635488 both being the `activateMfa` deep link), create **one combined PBI** and count the eng-days once.
5. **Description = the report distilled** (NOT a copy): Summary, Security classification (filed vs. our
   verdict/confidence/IcM Sev), How it can be exploited, Fix approach, Files to change (`file:line`),
   Test plan, an Open-questions/external-validation call-out, and a **📎 Reports & spec (to be linked)**
   placeholder (research HTML · agent spec · WBR master report) for the user to paste links into later.
   Convert to **HTML** for `System.Description` (`"format": "Html"`).
6. **Tooling.** Prefer the **ADO MCP** (`mcp_ado_wit_*`) when connected; this is the same flow the
   `pbi-creator` skill uses, so hand off to it. If the MCP isn't connected, the ADO **REST API** with an
   `az account get-access-token` bearer works (the `az` CLI is a `.cmd` shim that can't pass HTML cleanly
   via `subprocess`, and `az boards` routes HTML through cmd.exe which corrupts `& < >`). Make creation
   **idempotent** (query by exact title before creating) so a mid-run failure can be safely re-run.
7. ADO citations + IcM IDs **are allowed** in work-item descriptions (corp Engineering project, not the
   public skill repo). The public-repo safety rules apply to the **skill files**, not to ADO items.

### Step 7 — Weekly status report (manager tracking — concise, email-ready)

The on-call's manager tracks these findings weekly. This is a **separate, high-level artifact** — NOT the
research report. It is a single compact table meant to paste into an email. Generate it with
`scripts/build_status_report.py` (reads the same `classifications.csv` plus live ADO state) — see
[references/status-report-template.md](references/status-report-template.md). Keep it minimal:

- **Columns:** IcM · Bug (one-line) · Severity (our tier) · Owner (E/I) · Status · Work Item · Updated.
- **Status** is the ADO work-item state mapped to: *Not started · In progress · Blocked · In review · Complete.*
- **No research detail** — no evidence, no file:line, no audit trail. Quick-glance only.
- Group/sort by status or severity; include a one-line header (window + counts). Plain HTML table that
  pastes cleanly into Outlook.

---

## Severity Classification (summary)

Full rubric + required evidence per tier: [references/severity-rubric.md](references/severity-rubric.md).

| Our Tier | Meaning | IcM Sev | Required evidence |
|----------|---------|---------|-------------------|
| **CRITICAL (must fix)** | Reachable in prod, no mitigating control, real-world exploitable | **Sev2** (active/mass) or **Sev2.5** (not active) | Sink `file:line` + confirmed reachability + proven absence of any gate |
| **Important** | Real weakness, but partial mitigation / elevated prerequisites | **Sev3** (Sev2.5 only at confirmed-reachable, no-safeguard top edge) | Sink + the specific mitigation limiting blast radius, cited |
| **Moderate** | Defense-in-depth gap; needs unlikely preconditions (root, debug build, physical access) | **Sev3 / Sev4** | Cited precondition that blocks mass exploitation |
| **Low / Won't-Fix** | Not reachable in shipping config, or already gated off | **Sev4** | Citation proving non-reachability (flight default off, non-exported, sibling allow-list, etc.) |

**A down-classification is only valid if the mitigating control is cited with `file:line`.** "I didn't find
an exploit path" is not evidence — show the control, or show the searches proving its absence.

> **IcM Sev = response urgency** (Sev2 = page on-call outside business hours · Sev2.5 = immediate, business
> hours · Sev3 = soon · Sev4 = hygiene). **Assigning Sev2.5+ is a high, rare bar** — our stack almost always
> has a safeguard. Only assign Sev2.5+ when **all** hold: High confidence (adversarial pass held) · reachable
> in shipping (proven) · no mitigating control (proven absent) · **not** leaning on an unverifiable
> downstream/server boundary (External Validation = Yes ⇒ cap at Sev3). When in doubt, go lower. Full gate +
> the evolving **calibration log**: [references/severity-rubric.md](references/severity-rubric.md).

### Confidence & Assignment (summary)

- **Confidence** (High/Medium/Low) comes from the **adversarial pass** (Step 3.5) — see "The two-pass model".
  It measures *how sure we are of the verdict*, independent of severity. Low-confidence findings get a human
  review before action.
- **Assignment** is a cutoff on **IcM Sev + component** (not tier alone):

| Condition | Assignment | What we do |
|-----------|-----------|------------|
| **Tier ≤ Moderate AND component = Authenticator app** | `Intern-eligible` | Contained, lower-severity fix — safe to delegate (Fix Notes). |
| **Tier ≥ Important, or any Broker/Common/MSAL** | `Engineer-owned` | We keep it and produce a dispatch-ready Remediation Spec (Step 4.5). |

> Rationale: an intern only takes a finding that is both **≤ Moderate** and **contained to the app we fully
> own (Authenticator)**. Library (Common/Broker/MSAL) and any Important+ finding needs engineer judgment.

---

## Pre-Commit Safety Check (MANDATORY)

This skill lives in a **public** repo. **Before every commit that touches this skill**, run the scanner and
review its output — no exceptions:

```
python .github/skills/vuln-triage-reporter/scripts/safety_check.py
```

It scans the **staged + modified** skill files (and warns if any investigation output under
`local-context/` or the private workspace is accidentally tracked) for:
- Telemetry **sampling rates / coverage percentages** (the evasion map) — forbidden.
- **Internal security-control logic** — flight constant names, bypass/skip conditions, and real
  `file:line` citations into private submodules — forbidden in docs/specs.
- **PII / tenant GUIDs / UPNs / aliases / internal hostnames** (`*.azurefd.net`, `firewatch-pilot`,
  `@microsoft.com`, `ame.gbl`) — forbidden.
- **Real long IcM numbers / FireWatch GUIDs paired with finding content** — forbidden in committed skill
  text (use placeholders like `NNNNNN` in examples).

Opaque routing IDs (team IDs, service-tree GUIDs, codenames) are allowed and are NOT flagged. A **non-zero
exit means do not commit** until the flagged content is removed or genericized. The agent must run this and
report the result before staging any commit; if asked to commit without it, run it first anyway.

---

## Output

Per-finding `README.md` + an aggregate roll-up. Both must make the **agree/rebut decision explicit** and
cite code evidence for every severity call. Keep the aggregate short enough to drop into a shared WBR.
