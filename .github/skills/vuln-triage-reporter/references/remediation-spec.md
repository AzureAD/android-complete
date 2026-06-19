# Dispatch-Ready Remediation Spec

For every **Engineer-owned** finding (Important/Critical that we keep), produce this spec. The bar:
**detailed enough to hand to an engineer or the Copilot coding agent / `pbi-creator` without further
investigation.** It is grounded in the same cited evidence as the triage report — reuse the `file:line`
citations from the investigation; do not re-derive them loosely.

> **Safety:** no PoC payloads, no PII, no exploit walkthroughs. Describe the *fix*, not the attack. Keep it
> at engineering-implementation level. This is a public-repo skill — see the banner in `SKILL.md`.

## Template

```markdown
## Remediation Spec — [MSRC|ITD] <id> — <short title>

**Owner:** <engineer/team or TBD>  ·  **Our tier:** <Important|Critical>  ·  **Confidence:** <High|Medium|Low>
**Target repo(s):** <common | msal | broker | adal | authenticator>  ·  **Est. eng-days:** <n> (estimate)

### Root Cause
The underlying defect in 1–3 sentences — *why* the sink is exploitable, not just where. Tie it to the
missing/weak control identified in the investigation (e.g. "the `app_link` value reaches `startActivity`
without passing through the allow-list that the sibling install path uses").

### Fix Approach
The chosen strategy in plain terms, and *why* over alternatives. Prefer reusing an existing hardened
sibling control (cite it) over inventing a new one. State whether the fix is behind a **flight** (default
state) or unconditional.

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

- **Reuse, don't reinvent.** Most of these findings have a hardened *sibling* path already in the codebase
  (the investigation usually found it during the defense-in-depth sweep). The strongest fix mirrors that
  sibling — cite it so the implementer copies a proven control.
- **Make the negative test the contract.** The single most valuable artifact is a test that reproduces the
  *blocked* condition. It is the acceptance criterion for the fix and the proof for the MSRC close-out.
- **Confidence flows through.** If the finding is **Low confidence**, say so here — the fix may be
  premature until the verdict is confirmed; recommend the confirmation step first.
- **Cross-team awareness.** If a change touches `OneAuthSharedFunctions` or any IPC/Common surface consumed
  by 1P apps, flag the breaking-change + the need to notify the OneAuth team (per repo conventions).
