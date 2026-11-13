# Agent Dispatch Spec — the machine-readable companion

Each finding produces **two coordinated artifacts**:

1. **The human report** (`README.md` → rendered to a curated HTML page) — readable, skimmable, leads with a
   **Bottom line** TL;DR + stat tiles, surfaces the key callouts (why-not-exploited, **Verification Gaps**,
   **Decisions Needed**), and keeps the heavy audit trail collapsed. For people.
2. **The agent dispatch spec** (`<slug>.agent.md`) — a compact, **machine-readable** distillation an AI
   coding agent (Copilot coding agent / `pbi-creator`) can parse and act on to open a PR **without scraping
   prose**. For agents.

The agent spec is **generated** from the README by
[`scripts/build_agent_spec.py`](../scripts/build_agent_spec.py) — never hand-authored — so the two artifacts
never drift. The human HTML links to it via a header button.

## Why two artifacts

The human report optimizes for *understanding and judgment*; the agent spec optimizes for *action*. Cramming
both into one file makes it worse at both. The split also enforces an honesty boundary: the agent spec tells
the coding agent **what it may do now** vs. **what is gated on human/owner confirmation** (the `blocked_on`
list), so an agent never "fixes" past an unverified severity decision.

## Front-matter schema (`<slug>.agent.md`)

```yaml
---
finding_id: "<IcM number>"
tag: ITD | MSRC
title: "<finding title>"
component: "<Authenticator | Broker | Common | MSAL | ADAL>"
filed_tier: "<as filed>"
our_tier: "<CRITICAL | Important | Moderate | Low>"
cwe: "CWE-xxx"
icm_sev: "Sev2 | Sev2.5 | Sev3 | Sev4"
confidence: "High | Medium | Low"
verdict: "<AGREE | DOWN-CLASSIFY | UP-CLASSIFY ...>"
disposition: "Keep | Won't-Fix (Already-Covered) | Won't-Fix (Fixed-Since-Filed) | Not-Fixable (By-Design)"
external_validation_needed: true | false
status: "ready-to-fix | blocked (decision or verification pending) | closed-out (no fix)"
risk_decision: "Must fix | Optional hardening | No fix | Needs review"
remediation_approval: "Approved | Pending | Not requested"
existing_work_approval: "Approved | Pending | Not applicable"
target_repos: [common, adal, broker, msal, authenticator]   # inferred from file paths
firewatch_id: "<guid>"                                       # ITD only
files_to_change:
  - path: "<repo-relative path#Lxx-Lyy>"
    change: "<what to change>"
blocked_on:
  - "<open evidence question, missing risk decision, approval, or implementation contract>"
---
```

Body sections (also generated): **Problem Statement** (root cause + fix approach), **Files to Change**,
**Acceptance Criteria** (the negative test as the contract), **Do NOT proceed past these without
human/owner confirmation** (from the Verification Gaps), and **Constraints** (public-repo safety, OneAuth
breaking-change coordination, reuse-the-sibling-control).

## Generating

```
# 1) agent specs (run first so the HTML can link them)
python scripts/build_agent_spec.py "<workspace>/msrc/**/README.md" --out "<workspace>/msrc/<run>/agent-specs"

# 2) human HTML, linked to the specs
python scripts/build_research_pages.py "<workspace>/msrc/**/README.md" \
    --out "<workspace>/msrc/<run>/research" --index --agent-dir "../agent-specs"
```

The generator parses the README's `**Label:**` fields, the Classification table, the Files-to-Change /
Fix-Notes table, the Test Plan (acceptance), and the Verification Gaps table (blocked_on). Keep those
sections well-formed (per [report-template.md](report-template.md)) so extraction stays clean.

## Dispatch gate

Generation never authorizes execution. A kept finding is `ready-to-fix` only when its risk decision is
`Must fix` or `Optional hardening`, remediation is explicitly `Approved`, existing work is `Approved`
by its owner or `Not applicable`, confidence is High/Medium, and files plus test acceptance criteria
exist. Any open verification gap or external-validation requirement blocks automated dispatch.
Low/unknown confidence blocks it too. Closed-out findings remain `closed-out (no fix)`.

Older reports missing these approval fields deliberately generate a **blocked** spec. Review the source
report, record the actual decisions (never invent approval to clear the gate), and regenerate. Keep
resolved questions in the evidence history rather than the open Verification Gaps table.

## Safety
The agent spec is a **private investigation output**, just like its source report. Fix instructions,
identifiers, and verification questions may be sensitive; generation does not sanitize them. Keep it
outside the repository under `VULN_TRIAGE_WORKSPACE`. Never inject PoC payloads or PII into source
sections. The public-repo scanner is a check, not permission to publish an investigation artifact.
