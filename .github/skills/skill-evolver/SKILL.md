---
name: skill-evolver
description: Closed-loop self-improvement for skills, prompts, and AI tools. Captures friction (tool errors, repeated retries, wrong or outdated instructions, missing context, missed or wrong skill triggers, user corrections) into a structured journal, then runs retrospectives that classify root causes and propose concrete, reviewable edits to the offending SKILL.md, references, scripts, or copilot-instructions.md. Use whenever the user wants to improve, evolve, tune, or fix a skill or its instructions; run a skill retrospective; review or analyze skill friction; or says things like "improve my skills", "what went wrong with X skill", "why didn't skill Y trigger", "this skill is outdated or wrong", "you keep making the same mistake", or "that didn't go well". Also use PROACTIVELY at the end of any task that hit notable friction (repeated tool failures or a user correction) to log a note.
---

# Skill Evolver

Make skills and tools get better over time. The loop: **capture → analyze → propose → review → apply → validate → measure**.

## Architecture (already wired in this repo)

- **Store CLI**: `.github/hooks/journal-utils.js` — single writer for the JSONL friction journal (`~/.skill-evolution/journal.jsonl`) and the active-skill attribution marker.
- **Capture is ACTIVE on this runtime.** On the GitHub Copilot CLI there is **no hooks system**, so capture happens because **you (the agent) record friction yourself** via `journal-utils.js record`. This is the primary and only reliable mechanism here — treat logging friction as part of doing the task, not something a hook does for you.
- **Dormant auto-capture hook**: `.github/hooks/friction-capture.js` is a Claude Code-style `PostToolUse`/`Stop` hook. It does **not** fire on Copilot CLI and is intentionally **not** registered in `orchestrator.json`. It's kept only for teams running this repo under Claude Code (register it in `.claude/settings.json` there). Do not rely on it here.
- **Validation**: reuse `.github/skills/skill-creator/scripts/quick_validate.py` after every edit.
- **Changelog**: `.github/skill-evolution/evolution-log.md` records every applied change (for audit + rollback).

## Non-intrusiveness & controls

This system is designed to stay out of the way:

- **Silent capture.** Recording a friction event only appends one line to the journal file. It never interrupts the user, never asks a question, and never changes your task flow. Log **only real friction** (failures, retries, wrong instructions, corrections) so the journal stays high-signal.
- **No mid-task edits.** Skills are never auto-edited. Analysis and proposals happen only when you invoke a retrospective, and every behavior-affecting edit is gated on your approval.
- **Proactive logging must not derail the user.** If you log a friction note proactively at the end of a friction-heavy task, do it in **one line, recorded silently** via the CLI — do NOT ask the user a question, pause their task, or expand scope to discuss it. They review the journal later.
- **Off switch.** Set the environment variable `SKILL_EVOLUTION_DISABLE=1` to silence capture (CLI `record` becomes a no-op; the dormant hook is already inert). Reviewing past data (`stats`, `list`) still works. Unset it to re-enable.

## 1. Capture (active — this is the main job on Copilot CLI)

**You are the capture mechanism.** There is no background hook on this runtime, so friction is only recorded if you record it. Make this a habit: whenever you hit friction, append one line to the journal before moving on.

| Path | Who | How |
|------|-----|-----|
| **Active (primary)** | you (agent) | The moment you notice friction, record it via the CLI (below). |
| User-flagged | user | "that didn't go well" → record the last friction with their context. |
| Dormant hook | — | Not active on Copilot CLI; see Architecture. Ignore for capture here. |

**Record a friction event** (see [references/friction-schema.md](references/friction-schema.md) for the schema and the `eventType` catalog). Use single quotes around the JSON on PowerShell:

```powershell
node .github/hooks/journal-utils.js record '{"skill":"release-helper","tool":"powershell","eventType":"skill_step_mismatch","severity":"high","expected":"pipeline YAML under 1ES-Pipelines/","actual":"skill pointed to azure-pipelines/ which is deprecated","fixHint":"update path reference in SKILL.md step 3"}'
```

**Attribute events to a skill**: optionally mark the skill you're working under so events default to it (otherwise they record as `skill: "unknown"` and get triaged later):

```powershell
node .github/hooks/journal-utils.js set-active <skill-name>
# ... work ...
node .github/hooks/journal-utils.js clear-active
```

**When to actively record** (don't log noise — log signal):
- A skill step referenced a wrong/outdated path, file, command, or API.
- The skill that *should* have triggered didn't (`trigger_miss`) — the description needs tuning.
- You needed context the skill should have provided and had to go discover it (`missing_context`).
- The user corrected your approach in a way a better instruction would have prevented (`user_correction`).
- A documented step failed or contradicted reality (`skill_step_mismatch`, `dead_end`).

## 2. Retrospective (analyze)

Run when asked to improve/evolve skills or review friction.

1. **Pull the digest** (deterministic aggregation; ranks recurring issues by frequency × severity):
   ```powershell
   node .github/hooks/journal-utils.js stats --md
   node .github/hooks/journal-utils.js skill-sizes --md
   ```
   For raw events of one skill: `node .github/hooks/journal-utils.js list --skill <name>`.
   `skill-sizes` flags any skill over its body/description budget — those are pruning candidates (see step 4).

2. **Classify each recurring group** using [references/classification-rubric.md](references/classification-rubric.md). The critical judgment: is this a **skill defect** (fixable by editing the skill), a **model mistake**, an **environment issue**, or a **genuinely novel task**? Only skill defects (and global-convention gaps) become edits.

3. **Decide the target** of each fix:
   - Single-skill defect → edit that skill's `SKILL.md` / `references/` / `scripts/`.
   - Cross-cutting lesson that applies to many skills → edit `.github/copilot-instructions.md` instead.
   - Trigger miss → tune the skill's `description` frontmatter (the activation mechanism).
   - **Needs a new skill** → when a substantial novel task fits no existing skill, or an over-budget skill is really doing two jobs and should be **split**, don't force-fit it. Recommend creating a new skill via the **skill-creator** skill (`.github/skills/skill-creator/SKILL.md`) and hand off.

## 3. Propose, review, apply

Follow [references/edit-safety-rules.md](references/edit-safety-rules.md) strictly. Summary:

1. **Lead every proposal with its target.** Before the diff, each proposed fix MUST state, on one line:
   **`Target: <skill-name> → <relative/file/path>` · `<root-cause>` · `<severity>`**
   (for global lessons use `Target: copilot-instructions.md (all skills)`). This skill evolves *many*
   skills, so the reviewer must see at a glance which skill each fix changes — never bury it.
   When proposing multiple fixes, also include a one-row-per-fix summary table with columns
   **# · Target skill · File · Root cause · Severity** before the detailed diffs.
2. **Propose concrete diffs** — never vague advice. Show the exact before/after for each file.
3. **Gate on human review** — present proposals and use `ask_user` to get approval. When asking
   per-fix, name the target skill in the question (e.g. "Apply fix #2 to **release-helper**?").
   Never silently change behavior-affecting instructions.
4. **Apply on a branch** (`skill-evolution/<short-desc>`), one logical change per commit.
5. **Validate** every edited skill:
   ```powershell
   python .github/skills/skill-creator/scripts/quick_validate.py .github/skills/<edited-skill>
   ```
6. **Log it** — append an entry to `.github/skill-evolution/evolution-log.md` (issue, evidence, change, target, rollback ref).
7. **Offer a PR** for the branch when the user wants it.

## 4. Measure & prune

After fixes land, re-run `stats` over time to confirm the friction rate for the edited skill is trending down. Note the trend in the evolution-log entry. If an edit didn't help, roll it back (see edit-safety-rules) and try a different fix.

**Counter the addition bias.** The loop naturally *adds* rules; without pushback, skills bloat. So:
- Run `skill-sizes --md` each retrospective; any flagged skill is a **pruning candidate**.
- Every ~5th retrospective (or whenever a skill is flagged), propose **removals** — obsolete, redundant, one-off, or contradictory rules — not just additions. See [references/bloat-control.md](references/bloat-control.md) for the prune procedure and budgets. A prune goes through the same review gate as any edit.

## Scope notes

- This system also applies to non-skill assets: prompt templates, agent instruction files, and MCP-usage notes — the same capture/analyze/propose loop works for them.
- Do **not** mass-edit every skill to call `set-active`; attribution is opt-in. Unattributed events default to `skill: "unknown"` and are triaged during the retrospective.
