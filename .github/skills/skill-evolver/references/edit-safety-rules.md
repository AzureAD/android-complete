# Edit Safety Rules

Skills change agent behavior. Treat every edit as a reviewed code change.

## Hard rules

1. **Propose, don't silently edit.** Always show concrete before/after diffs and get explicit
   human approval (`ask_user`) before applying a behavior-affecting change. Trivial fixes (typos,
   a dead path → correct path) may be batched, but still listed for review.
2. **Verify against reality first.** Before claiming a step is wrong, confirm the correct
   path/API/command exists in the current codebase. Never "fix" based on the journal alone.
3. **Smallest change that resolves the issue.** Don't rewrite a skill to patch one defect. Prefer
   editing the specific step/reference over restructuring.
4. **Right target.** Single-skill defect → that skill. Cross-cutting → `copilot-instructions.md`.
   Trigger miss → the skill `description`.
5. **Preserve the skill contract.** Keep `SKILL.md` frontmatter to allowed keys only
   (`name`, `description`, `license`, `allowed-tools`, `metadata`); no angle brackets in
   `description`; `description` must be **≤1024 characters**. Check before saving:
   `(Select-String -Path <SKILL.md> -Pattern '^description:').Line.Length` (subtract the
   `description: ` prefix). Keep it under the size limits.
6. **Consolidate over append (anti-bloat).** Prefer editing or merging an existing instruction
   over adding a new bullet. Put detailed caveats/examples in `references/`, not the always-loaded
   SKILL.md body. Don't add to a skill already over budget (`skill-sizes`) without pruning first.
   See [bloat-control.md](bloat-control.md).

## Workflow

1. Create a branch: run `git checkout -b skill-evolution/<short-desc>` via the **powershell tool** (not `gitkraken-git_checkout`, which doesn't support `-b`).
2. Make one logical change per commit; reference the journal event ids in the commit body.
3. **Validate** each edited skill:
   ```powershell
   python .github/skills/skill-creator/scripts/quick_validate.py .github/skills/<edited-skill>
   ```
   For larger changes also run the packager validation:
   `python .github/skills/skill-creator/scripts/package_skill.py .github/skills/<edited-skill>`.
4. Append an evolution-log entry (format below).
5. Offer to open a PR. Do not auto-merge.

## Rollback

- Each evolution-log entry records the commit SHA. To revert: `git revert <sha>` (or restore the
  pre-change version of the file from that commit) and add a follow-up log entry noting the revert
  and why the fix didn't help.

## Evolution-log entry format

Append to `.github/skill-evolution/evolution-log.md`:

```markdown
## <ISO date> — <skill or target>: <one-line summary>
- **Root cause:** skill defect | global-convention gap | ...
- **Evidence:** event ids / quotes from the journal (frequency × severity)
- **Change:** what was edited (file + nature of change)
- **Target:** path to the edited file(s)
- **Commit:** <sha>  (rollback: `git revert <sha>`)
- **Result/trend:** (fill in after measuring) friction for this skill before vs after
```
