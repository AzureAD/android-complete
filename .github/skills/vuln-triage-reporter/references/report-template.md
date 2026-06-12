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
**Justification:** <1–3 sentences, anchored to the evidence below>

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

## Recommended Fix (high-level)
- <the control to add; mirror the sibling hardened handler if one exists>

## Estimated Eng-Days
<n> (ESTIMATE — on-call to adjust). Basis: <tier + fix complexity>.

## Searches Run (audit trail)
Verbatim list of the searches the investigation actually ran — ESPECIALLY the ones that returned nothing
(the absence proofs behind every "no mitigation found" / "not reachable" claim). Required, non-optional.
- `<pattern>` in `<path/scope>` → <what it returned, or "0 matches → proves X absent">
- ...
```

> A **Glossary** section is appended automatically by `build_research_pages.py` — it lists only the
> acronyms/concepts that actually appear on the page, sourced from `references/glossary.md`. Add new
> terms there (format `- **TERM** — definition`) rather than writing per-finding glossaries.
