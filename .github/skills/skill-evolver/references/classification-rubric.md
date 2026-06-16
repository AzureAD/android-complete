# Classification Rubric

Most friction is **not** a skill defect. Classify before editing, or you will pollute skills
with noise. For each recurring group from `journal-utils.js stats`, assign one root cause.

## Root-cause categories

| Category | Signals | Action |
|----------|---------|--------|
| **Skill defect** | Documented step is wrong/outdated; path/API/command no longer exists; missing a step the task always needs; description too narrow to trigger | **Edit the skill.** This is the only category that normally changes a skill. |
| **Global-convention gap** | The same lesson would apply to many skills/tasks (e.g. a repo-wide path move, a naming rule) | **Edit `copilot-instructions.md`**, not a single skill. |
| **Model mistake** | The skill was correct; the agent misread or skipped it; one-off reasoning slip | **No edit.** Optionally tighten wording only if the instruction was genuinely easy to misread. |
| **Environment issue** | Network/auth failure, missing local tool, transient flake, permissions | **No skill edit.** Note it; route to setup docs if recurring. |
| **Novel task** | Legitimately new scenario the skill never claimed to cover | **No edit** for a small case (add a section if now in-scope). For a substantial out-of-scope task → **Needs a new skill** (below). |
| **Needs a new skill** | No existing skill fits a substantial task; or an over-budget skill is doing two distinct jobs and should be **split** | **Don't force-fit.** Recommend creating a new skill via the `skill-creator` skill, then hand off. Editing an unrelated skill here just causes bloat and trigger confusion. |

## Decision heuristics

- **Frequency × severity first.** Use the ranked `recurring` list; start at the top. A single
  low-severity event is rarely worth a change.
- **Reproducibility.** If the documented step demonstrably contradicts the current repo/codebase,
  it's a skill defect — verify against the actual file/path/API before editing.
- **Was the instruction present and correct?** If yes and the agent still erred → model mistake,
  not a skill defect. Don't bloat the skill to patch a one-off.
- **Trigger misses are description bugs.** If the right skill didn't fire, the fix is almost always
  the `description` frontmatter (add the missing trigger phrasing/scenario), not the body.
- **One lesson, right home.** If a fix would need to be copied into 3+ skills, it belongs in
  `copilot-instructions.md` instead.

## Output of classification

For each group produce: `{ skill, eventType, rootCause, evidence (event ids/quotes), target file,
proposed change, severity }`. Carry this into the propose/review step.
