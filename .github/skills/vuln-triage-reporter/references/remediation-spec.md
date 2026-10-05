# Dispatch-Ready Remediation Spec

For every **kept** finding (one we own and will fix), produce this spec. The bar:
**detailed enough to hand to an engineer or the Copilot coding agent / `pbi-creator` without further
investigation.** It is grounded in the same cited evidence as the triage report — reuse the `file:line`
citations from the investigation; do not re-derive them loosely.

> **Safety:** no PoC payloads, no PII, no exploit walkthroughs. Describe the *fix*, not the attack. Keep it
> at engineering-implementation level. This is a public-repo skill — see the banner in `SKILL.md`.

## Fix Options — produce this FIRST, before the spec below

**Do not fill in "Fix Approach" until the engineer has chosen an approach.** Real feedback: asked for help
with a fix, the skill produced one immediately; the engineer had to interrupt to ask *how* it intended to
fix the problem, and did not trust the answer it gave. Options first is not ceremony — it is how the
engineer stays the decision-maker on code shipping to >1B users.

```markdown
### Fix Options — [MSRC|ITD] <id>

**Root cause:** <1–2 sentences, cited>

| # | Approach | Closes | Does NOT close | Blast radius | Regression risk | Flightable | Effort |
|---|----------|--------|----------------|--------------|-----------------|-----------|--------|
| 1 | <reuse the hardened sibling control at the admission point> | … | … | <files/modules> | Low | Yes (default-OFF) | <n>d |
| 2 | <gate the component at the manifest/IPC boundary> | … | … | … | Med | Yes | <n>d |
| 3 | <accept the risk / no code change> | — | everything | none | none | — | 0d |

**Recommended: #<n>** — <why>
**Rejected #<n> because** — <the real reason>
**Open question for you:** <the judgment call that is genuinely the engineer's>

Which do you want?
```

Rules:

- **At least two materially different options** — not three phrasings of the same patch. If only one is
  viable, include "accept the risk / no change" and explain why it loses.
- **Name a recommendation and defend it.** A neutral menu just hands the work back.
- **"Does NOT close" is mandatory** — it is the column engineers actually read, and the one an eager fix
  silently omits.
- **Stop and wait.** No branch, no edit, no PR until an option is chosen.
- The chosen option — and *why the others lost* — becomes the **Fix Approach** section below.

---

## Template

```markdown
## Remediation Spec — [MSRC|ITD] <id> — <short title>

**Owner:** <engineer/team or TBD>  ·  **Our tier:** <Important|Critical>  ·  **Confidence:** <High|Medium|Low>
**Target repo(s):** <common | msal | broker | adal | authenticator>  ·  **Est. eng-days:** <n> (estimate)
**Chosen option:** #<n> from the Fix Options table  ·  **Approved by:** <engineer> on <date>

### Root Cause
The underlying defect in 1–3 sentences — *why* the sink is exploitable, not just where. Tie it to the
missing/weak control identified in the investigation (e.g. "the `app_link` value reaches `startActivity`
without passing through the allow-list that the sibling install path uses").

### Fix Approach
**The option the engineer chose** (state which number), in plain terms, and *why* it beat the alternatives
— carry the reasoning down from the Fix Options table rather than re-arguing it. Prefer reusing an
existing hardened sibling control (cite it) over inventing a new one. State whether the fix is behind a
**flight** (default state) or unconditional.

### Files to Change
Concrete, cited. Each row = a file + the change.

| File:line | Change |
|-----------|--------|
| `<repo>/.../Foo.java#Lxx` | <add allow-list check before sink / null-guard / export=false / …> |
| `<repo>/.../AndroidManifest.xml#Lxx` | <set `android:exported="false"` / add permission> |
| `<repo>/.../SomeFlight.kt#Lxx` | <add flight key, default OFF> |

### Test Plan
- **Unit:** <what to assert; name the test class to add/extend, cite an existing similar test>.
- **Instrumented / integration (if applicable):** <scenario>.
- **Negative test:** the exact case that *was* exploitable must now be **blocked** — assert the deny path.
- **Regression:** the legitimate path (e.g. real broker install / valid deep link) still works.

### Risks & Rollout
- **Breaking-change risk:** does this affect downstream consumers (Outlook/Teams/OneAuth) or the
  `OneAuthSharedFunctions` surface? If yes, note the coordination needed.
- **Flighting:** ship behind a flight defaulting OFF → enable progressively? Or safe to ship on?
- **Backport:** does a hotfix/older release branch need this too?
- **Validation owner / sign-off:** <who confirms before close>.

### Dispatch Notes (optional)
If handing to the Copilot coding agent / `pbi-creator`: a one-paragraph problem statement an agent can act
on, plus the acceptance criteria (the negative test passing). Do not include exploit detail.
```

## Guidance

- **Options before implementation, always.** The single most common complaint about this skill was that it
  jumped to a fix. Show the table, name a pick, wait. Even under "just fix it", show it and proceed —
  it costs seconds and lets the engineer catch a wrong approach before the diff exists.
- **Reuse, don't reinvent.** Most of these findings have a hardened *sibling* path already in the codebase
  (the investigation usually found it during the defense-in-depth sweep). The strongest fix mirrors that
  sibling — cite it so the implementer copies a proven control.
- **Make the negative test the contract.** The single most valuable artifact is a test that reproduces the
  *blocked* condition. It is the acceptance criterion for the fix and the proof for the MSRC close-out.
- **Confidence flows through.** If the finding is **Low confidence**, say so here — the fix may be
  premature until the verdict is confirmed; recommend the confirmation step first.
- **Cross-team awareness.** If a change touches `OneAuthSharedFunctions` or any IPC/Common surface consumed
  by 1P apps, flag the breaking-change + the need to notify the OneAuth team (per repo conventions).
