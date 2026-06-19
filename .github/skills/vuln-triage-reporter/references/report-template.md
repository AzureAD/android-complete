# Per-Finding Report Template

Write one of these into each finding's folder `README.md`. Keep it engineering-triage level — **no PoC
payloads, no PII**.

```markdown
# [MSRC|ITD] [<id or finding GUID>] — <short vuln title>

**Component:** Authenticator | Broker | MSAL | Common | ADAL
**Linked IcM:** <icm id / link>  ·  **FireWatch finding:** <guid> (if ITD)

## Classification

| | Source | Tier | Class / CWE |
|---|--------|------|-------------|
| **Filed** | <MSRC / Glasswing / Codealorian> | <IMPORTANT / Tier 1 / …> | <CWE-xxx> |
| **Ours** | this investigation | <CRITICAL / Important / Moderate / Low> | <CWE-xxx> |

**Verdict:** AGREE | DOWN-CLASSIFY | UP-CLASSIFY
**Confidence:** High | Medium | Low  _(set by the adversarial pass — see below)_
**IcM Severity:** Sev2 | Sev2.5 | Sev3 | Sev4  _(team response-urgency mapping — see severity-rubric.md; Sev2.5+ is a rare, high bar)_
**Assignment:** Intern-eligible | Engineer-owned  _(Low/Moderate → Intern-eligible; Important/Critical → Engineer-owned)_
**External validation:** Yes | No — _one line: do we need facts outside the code we own (downstream consumers / server-side eSTS) to be sure? If the verdict leans on a server/downstream safeguard we can only infer, say "Yes" and name it — the impact is partly theoretical until confirmed._
**Justification:** <1–3 sentences, anchored to the evidence below>

> These `**Label:**` fields drive the colorful **stat tiles** at the top of the generated HTML page
> (Severity, Confidence, Verdict, Passes, External-Validation, Assignment). Keep each on its own line so
> the generator can parse them.

## Description
Plain-English: what the component is and what the weakness is. 2–4 sentences. Name the acronyms/concepts
(they get auto-linked into the page Glossary).

## How It Can Be Exploited
Numbered, high-level attack narrative (preconditions → steps → outcome). **No literal PoC payloads or PII.**
If the finding is refuted/by-design, state "Not exploitable as filed" and the reason.

## The Vulnerability
Plain-English: what the weakness is and what an attacker could do. 2–4 sentences.

## Sink (cited)
- **<file>**:<lines> — the vulnerable code. 1–2 sentence description.

## Reachability
- Reachable in shipping config? YES / NO / CONDITIONAL — and the conditions.
- Entry point → sink call path (cite `file:line` at each hop).

## Defense-in-Depth Sweep (look beyond)
For each layer: what was found, or the search that proves absence.

| Layer | Finding | Evidence |
|-------|---------|----------|
| Component export | <exported? permission?> | `AndroidManifest.xml#Lxx` |
| IPC boundary | <package/sig check?> | `<file>#Lxx` |
| Sibling handlers | <allow-list this sink skips?> | `<file>#Lxx` |
| Flight gates | <flighted? default?> | `<file>#Lxx` |
| Upstream validation | <scheme/host allow-list?> | `<file>#Lxx` |
| Build/config gating | <debug/test/root-only?> | `<file>#Lxx` |

## Aggravating Factors
- <anything that makes it worse than filed — unflighted, exported, no allow-list>

## Defense-in-Depth: Why Likely Not Exploited
Include this ONLY when you have sufficient evidence. State the concrete control(s) that make real-world
exploitation unlikely, cited. If a control is partial, title it "Defense-in-Depth: partial — do NOT treat
as safe" and say what is NOT covered. If there is no sufficient DiD evidence (a genuine Important finding),
OMIT this section rather than inventing a reason.

## Scope & Verification Boundary
What we own and verified (Authenticator client / Broker / Common) vs. what we **cannot** confirm:
- **Downstream consumers** (Outlook/Teams/OneAuth/other MSAL callers) may add their own checks — unverifiable.
- **Server-side** (eSTS / MFA backend) enforcement may only be inferred from the protocol.
State plainly: it is possible there are downstream/server checks but we cannot conclude definitively — worth
investigating. Only confirm what you can; do not assert "safe" or "exploitable" about an unverified boundary.

## Adversarial Verification
The second, independent `codebase-researcher` (Challenger) pass that tried to **break** the Pass 1 verdict.
- **What the Challenger attempted:** <bypass of the cited mitigation / alternate entry path / case for still-exploitable>
- **Result:** HELD (could not break it) | WEAKENED (found a caveat/partial gap) | OVERTURNED (verdict changed)
- **What changed (if anything):** <new evidence, with `file:line`>
- **Confidence set:** High | Medium | Low — <one line: why this level>

> Append the Challenger's own "Searches Run" lines into the audit-trail section below (label them `[challenger]`).

## Verification Gaps & What We Need to Confirm
**Required whenever any part of the verdict could not be settled by static code analysis.** Some conditions
an AI agent *cannot* test — they need a runtime repro, a specific device/tenant state, server-side
visibility, or code in a repo we don't own. Surface each as an explicit, actionable row so the engineer
knows exactly what to confirm and how it moves the severity. **Be honest: never imply a runtime/server claim
was verified when it was only reasoned about.**

| # | Open question (unverified) | Why it can't be statically verified | What we checked instead | What we need (who/how) | If confirmed → effect |
|---|----------------------------|--------------------------------------|--------------------------|------------------------|-----------------------|
| 1 | <the precise claim we could not settle> | <runtime / server-side / downstream-repo / device-state / rooted / timing — name it> | <the static fact we DID establish, cited> | <the person, repro, or data that would close it> | <Sev/verdict change if confirmed> |

> **Can proceed now vs. blocked:** one line — which parts of the fix an engineer/intern can start immediately
> on the confirmed-in-code facts, and which decisions must wait for the answers above. Never stall on a gap
> you can route around; never over-claim a gap you can't.

## Remediation
Pick ONE based on Assignment:

### If Engineer-owned (Important/Critical) — Dispatch-ready Remediation Spec
Fill out the full spec from [remediation-spec.md](remediation-spec.md): Root Cause · Fix Approach ·
Files to Change (`file:line`) · Test Plan · Risks & Rollout (flighting). Must be detailed enough to hand to
an engineer or the Copilot coding agent / `pbi-creator` without further investigation.

### If Intern-eligible (Low/Moderate) — Fix Notes
- <the control to add or the close-out action; mirror the sibling hardened handler if one exists>
- Scope: single repo? bounded? any cross-team coordination needed (if yes, reconsider Engineer-owned).

## Estimated Eng-Days
<n> (ESTIMATE — on-call to adjust). Basis: <tier + fix complexity>.

## Searches Run (audit trail)
Verbatim list of the searches BOTH passes actually ran — ESPECIALLY the ones that returned nothing
(the absence proofs behind every "no mitigation found" / "not reachable" claim). Required, non-optional.
Label challenger (Pass 2) searches so the adversarial coverage is visible.
- `<pattern>` in `<path/scope>` → <what it returned, or "0 matches → proves X absent">
- `[challenger] <pattern>` in `<path/scope>` → <result of the bypass/alternate-path attempt>
- ...
```

> A **Glossary** section is appended automatically by `build_research_pages.py` — it lists only the
> acronyms/concepts that actually appear on the page, sourced from `references/glossary.md`. Add new
> terms there (format `- **TERM** — definition`) rather than writing per-finding glossaries.
