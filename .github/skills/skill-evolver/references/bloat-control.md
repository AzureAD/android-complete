# Bloat Control

The evolver has a built-in **addition bias**: every retrospective tends to *add* a rule.
Without counter-pressure, skills grow into unreadable caveat-soup and pay a per-trigger token
tax. These guardrails keep skills lean. Read this when running the Prune phase or when
`skill-sizes` flags a skill.

## Size budget (tripwire)

Run the automated check during every retrospective:

```powershell
node .github/hooks/journal-utils.js skill-sizes --md
```

Thresholds (enforced by the tool):

| Dimension | Warn | Over |
|-----------|------|------|
| SKILL.md body lines | > 400 | > 500 (skill-creator's stated max) |
| `description` chars | > 900 | > 1024 (hard limit) |

Any skill flagged `BODY_OVER` / `BODY_WARN` / `DESC_*` is a candidate for pruning or relocation
**before** adding anything new to it.

## Prune procedure

When a skill is flagged (or every ~5th retrospective), look for and propose **removals**, not just
additions:

1. **Obsolete** — rules for a path/API/tool that no longer exists. Delete.
2. **Redundant** — two bullets saying the same thing, or a rule the model would follow anyway. Merge or drop.
3. **One-off `low`-severity notes** — caveats added for a single incident that never recurred (check the journal: if the signature appears once and is old, expire it).
4. **Contradictions** — a newer rule that supersedes an older one. Keep one, remove the other.

Propose prunes through the same review gate as any edit (lead with `Target:`, get approval, log it).
A retrospective that removes a stale rule is as valuable as one that adds a needed rule.

## Append discipline (stop new bloat at the source)

- **Consolidate over append.** Prefer editing or tightening an existing instruction over adding a
  new bullet. Two short rules that overlap should become one.
- **References over body.** Put detailed caveats, examples, and edge-case handling in `references/`
  (progressive disclosure), not in the always-loaded SKILL.md body. The body stays a lean index;
  the detail loads only when needed. This is skill-creator's core principle.
- **Earn the line.** Every line added to a SKILL.md body costs tokens on every trigger. Only add to
  the body if the lesson is core and high-frequency; otherwise it goes in a reference or is dropped.
