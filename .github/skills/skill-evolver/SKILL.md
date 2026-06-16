---
name: skill-evolver
description: Closed-loop self-improvement for skills, prompts, and AI tools. Captures friction (tool errors, repeated retries, wrong or outdated instructions, missing context, missed or wrong skill triggers, user corrections) into a structured journal, then runs retrospectives that classify root causes and propose concrete, reviewable edits to the offending SKILL.md, references, or scripts (or copilot-instructions.md for global lessons). Use whenever the user wants to improve, evolve, tune, or fix a skill or its instructions; run a skill retrospective; review or analyze skill friction; note that something went wrong, didn't work, or was confusing; or says things like "improve my skills", "what went wrong with X skill", "why didn't skill Y trigger", "this skill is outdated or wrong", "fix the skill so this doesn't happen again", "you keep making the same mistake", or "that didn't go well". Also use PROACTIVELY at the end of any task that hit notable friction (repeated tool failures or a user correction) to log a note.
---

# Skill Evolver

Make skills and tools get better over time. The loop: **capture → analyze → propose → review → apply → validate → measure**.

## Architecture (already wired in this repo)

- **Store CLI**: `.github/hooks/journal-utils.js` — single writer for the JSONL friction journal (`~/.skill-evolution/journal.jsonl`) and the active-skill attribution marker.
- **Automatic capture**: `.github/hooks/friction-capture.js` runs on `PostToolUse`/`Stop` (registered in `.github/hooks/orchestrator.json`) and logs tool failures. If the runtime doesn't fire those events, automatic capture is silently skipped — **active capture (below) is the reliable fallback.**
- **Validation**: reuse `.github/skills/skill-creator/scripts/quick_validate.py` after every edit.
- **Changelog**: `.github/skill-evolution/evolution-log.md` records every applied change (for audit + rollback).

## Non-intrusiveness & controls

This system is designed to stay out of the way:

- **Silent capture.** The hook only writes to the journal file. It never interrupts, never asks questions, and always lets the tool flow continue (returns `{continue:true}`). It records **only on detected failure**, so successful work produces no noise.
- **No mid-task edits.** Skills are never auto-edited. Analysis and proposals happen only when you invoke a retrospective, and every behavior-affecting edit is gated on your approval.
- **Proactive logging must not derail the user.** If you log a friction note proactively at the end of a friction-heavy task, do it in **one line, recorded silently** via the CLI — do NOT ask the user a question, pause their task, or expand scope to discuss it. They review the journal later.
- **Off switch.** Set the environment variable `SKILL_EVOLUTION_DISABLE=1` to silence all capture (hook + CLI `record` become no-ops). Reviewing past data (`stats`, `list`) still works. Unset it to re-enable.

## 1. Capture

Three capture paths feed the same journal:

| Path | Who | How |
|------|-----|-----|
| Automatic | hook | Tool errors logged on `PostToolUse`. No action needed. |
| Active | you (agent) | When you notice friction mid-task, record it (see below). |
| User-flagged | user | "that didn't go well" → record the last friction with their context. |

**Record a friction event** (see [references/friction-schema.md](references/friction-schema.md) for the schema and the `eventType` catalog). Use single quotes around the JSON on PowerShell:

```powershell
node .github/hooks/journal-utils.js record '{"skill":"release-helper","tool":"powershell","eventType":"skill_step_mismatch","severity":"high","expected":"pipeline YAML under 1ES-Pipelines/","actual":"skill pointed to azure-pipelines/ which is deprecated","fixHint":"update path reference in SKILL.md step 3"}'
```

**Attribute events to a skill**: when you start working under a skill, optionally mark it active so hook-captured events get attributed:

```powershell
node .github/hooks/journal-utils.js set-active <skill-name>
# ... work ...
node .github/hooks/journal-utils.js clear-active   # (Stop hook also clears it)
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
   ```
   For raw events of one skill: `node .github/hooks/journal-utils.js list --skill <name>`.

2. **Classify each recurring group** using [references/classification-rubric.md](references/classification-rubric.md). The critical judgment: is this a **skill defect** (fixable by editing the skill), a **model mistake**, an **environment issue**, or a **genuinely novel task**? Only skill defects (and global-convention gaps) become edits.

3. **Decide the target** of each fix:
   - Single-skill defect → edit that skill's `SKILL.md` / `references/` / `scripts/`.
   - Cross-cutting lesson that applies to many skills → edit `.github/copilot-instructions.md` instead.
   - Trigger miss → tune the skill's `description` frontmatter (the activation mechanism).

## 3. Propose, review, apply

Follow [references/edit-safety-rules.md](references/edit-safety-rules.md) strictly. Summary:

1. **Propose concrete diffs** — never vague advice. Show the exact before/after for each file.
2. **Gate on human review** — present proposals and use `ask_user` to get approval. Never silently change behavior-affecting instructions.
3. **Apply on a branch** (`skill-evolution/<short-desc>`), one logical change per commit.
4. **Validate** every edited skill:
   ```powershell
   python .github/skills/skill-creator/scripts/quick_validate.py .github/skills/<edited-skill>
   ```
5. **Log it** — append an entry to `.github/skill-evolution/evolution-log.md` (issue, evidence, change, target, rollback ref).
6. **Offer a PR** for the branch when the user wants it.

## 4. Measure

After fixes land, re-run `stats` over time to confirm the friction rate for the edited skill is trending down. Note the trend in the evolution-log entry. If an edit didn't help, roll it back (see edit-safety-rules) and try a different fix.

## Scope notes

- This system also applies to non-skill assets: prompt templates, agent instruction files, and MCP-usage notes — the same capture/analyze/propose loop works for them.
- Do **not** mass-edit every skill to call `set-active`; attribution is opt-in. Unattributed events default to `skill: "unknown"` and are triaged during the retrospective.
