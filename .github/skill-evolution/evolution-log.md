# Skill Evolution Log

Auditable changelog of changes applied by the `skill-evolver` system. Each entry links a
captured-friction finding to the concrete edit that addressed it, with a rollback reference.

Newest entries on top. See `.github/skills/skill-evolver/references/edit-safety-rules.md` for the
entry format.

<!-- New entries go below this line -->

## 2026-06-16 — skill-evolver: add anti-bloat guardrails (#1 prune, #2 tripwire, #3 consolidate, #4 references)

- **Target:** skill-evolver → `journal-utils.js`, `SKILL.md`, `references/edit-safety-rules.md`,
  `references/bloat-control.md` (new)
- **Root cause:** design risk (user-raised) — the loop has an addition bias; every retrospective
  tends to *add* a rule, so skills bloat over time and pay a per-trigger token tax. Nothing in the
  loop pruned, consolidated, or measured skill weight.
- **Evidence:** in one session skill-evolver took 4 edits, all additions; largest skills already
  320–369 lines vs the 500-line guideline.
- **Change:**
  - **#2 tripwire:** new `journal-utils.js skill-sizes` command scans every SKILL.md and flags
    body >400/500 lines and description >900/1024 chars.
  - **#1 prune:** SKILL.md §4 renamed "Measure & prune" — run `skill-sizes` each retro; every ~5th
    retro (or when flagged) propose *removals*, not just additions.
  - **#3 + #4:** new edit-safety rule 6 (consolidate over append; references over body; don't add
    to an over-budget skill without pruning).
  - New `references/bloat-control.md` holds the budgets + prune procedure (kept out of the
    always-loaded body — practicing #4).
- **Validation:** `quick_validate.py` passes; `skill-sizes --md` runs and correctly flags
  skill-evolver's own description (1019 chars, DESC_WARN). SKILL.md body grew only 104→111 lines
  because detail went into the reference.
- **Commit:** branch `skill-evolution/copilot-cli-active-capture` (rollback: `git revert <sha>`).
- **Follow-up:** skill-evolver's description (1019/1024) should be trimmed at the next pass — the
  tripwire is already flagging the tool's own author.


## 2026-06-16 — skill-evolver: require proposals to name target skill + file

- **Target:** skill-evolver → `.github/skills/skill-evolver/SKILL.md` (§3 Propose, review, apply)
- **Root cause:** skill defect (medium). The Propose section required "concrete diffs" but did
  not require each proposal to state *which skill and file* it targets. Since this skill evolves
  many skills, reviewers couldn't tell at a glance what each fix changed.
- **Evidence:** `missing_context`, medium (user-reported) — "I didn't see which skill the fix was
  for" during retro #1/#2 proposals.
- **Change:** §3 now mandates a per-proposal header
  `Target: <skill> → <file> · <root-cause> · <severity>`, a summary table (# · Target skill · File ·
  Root cause · Severity) when proposing multiple fixes, and naming the target skill in per-fix
  approval questions.
- **Validation:** `quick_validate.py` passes.
- **Commit:** branch `skill-evolution/copilot-cli-active-capture` (rollback: `git revert <sha>`).
- **Result/trend:** future retrospective proposals will be unambiguous about scope per skill.


## 2026-06-16 — skill-evolver: clarify git branch creation uses powershell tool (retro #2)

Source: retrospective #2. 7 events in journal (4 carried from retro #1 — all confirmed fixed,
no recurrence). 3 new events captured. 1 skill defect actioned.

### 4. skill-evolver: `git checkout -b` clarification in edit-safety-rules
- **Root cause:** skill defect (medium). edit-safety-rules said `git checkout -b` without
  specifying *which* tool — I used `gitkraken-git_checkout` (doesn't support `-b`) when
  the `powershell` tool works fine with native git.
- **Evidence:** `tool_error`, medium — had to use an unnecessary two-step workaround
  (git_branch create + git_checkout), costing an extra turn. Verified: `git checkout -b`
  works perfectly via the powershell tool.
- **Change:** one-line clarification in Workflow step 1 of
  `.github/skills/skill-evolver/references/edit-safety-rules.md`: specify
  "via the powershell tool (not gitkraken-git_checkout, which doesn't support -b)".
- **Not actioned:** event #6 (`ask_user` interruption — environmental, no fix) and
  event #7 (dirty workspace file — environmental, user skipped the doc nudge).

- **Validation:** `quick_validate.py` passes.
- **Commit:** see branch `skill-evolution/copilot-cli-active-capture` (rollback: `git revert <sha>`).
- **Result/trend:** 4/4 carried-over defects still resolved; 1 new defect fixed; 2 environmental.
  Velocity: retro #2 closed faster than retro #1 — journal patterns are getting cleaner.


## 2026-06-16 — skill-evolver: make active capture first-class, quarantine non-firing hook (Option A)

Source: investigation of "why isn't PostToolUse/Stop firing". Root cause: the GitHub
Copilot CLI runtime has no hooks system, and `orchestrator.json` used the Claude Code hook
schema, so `friction-capture.js` never fired. Developer chose **Option A** (Copilot CLI only).

- **Root cause:** environmental / `skill_step_mismatch` (high) — automatic capture was
  presented as primary but cannot fire on this runtime.
- **Evidence:** empty journal despite real tool failures; CLI docs show no hooks feature;
  no runtime config references `orchestrator.json`.
- **Change:**
  - `orchestrator.json`: removed the `PostToolUse`/`Stop` and the second `SubagentStop`
    `friction-capture.js` registrations (kept the orchestrator's own subagent hooks).
  - `friction-capture.js`: marked DORMANT with a header banner — Claude Code-only, not
    registered on Copilot CLI; documents how to enable via `.claude/settings.json`.
  - `skill-evolver/SKILL.md`: reframed capture so **active capture is the primary mechanism**
    (Architecture, Capture section table, attribution note, non-intrusiveness + off-switch
    wording all updated to stop implying an automatic hook runs here).
- **Validation:** `quick_validate.py` passes; `orchestrator.json` parses and no longer
  references friction-capture; CLI `record`/`stats` still work (active capture intact).
- **Commit:** see branch `skill-evolution/copilot-cli-active-capture` (rollback: `git revert <sha>`).
- **Result/trend:** capture now honestly reflects the runtime; no false reliance on a hook
  that never fires.


## 2026-06-16 — skill-creator, skill-evolver: first retrospective (3 fixes)

Source: retrospective run over `~/.skill-evolution/journal.jsonl` (3 active-captured events
from the build session). All fixes approved individually by the developer.

### 1. skill-creator: document PyYAML prerequisite
- **Root cause:** skill defect (missing context). `quick_validate.py`/`package_skill.py` import
  `yaml`, but the skill never states the dependency.
- **Evidence:** `missing_context`, medium — `ModuleNotFoundError: No module named 'yaml'` hit while
  validating skill-evolver; required `pip install pyyaml` (1 extra turn).
- **Change:** added a "Prerequisite: requires PyYAML" note to Step 5 (Packaging) in
  `.github/skills/skill-creator/SKILL.md`.

### 2. skill-evolver: clarify automatic capture is best-effort
- **Root cause:** environmental (this runtime did not fire `PostToolUse`/`Stop`), not a code bug.
  Doc-clarification only.
- **Evidence:** `trigger_miss`, medium — journal was empty despite real tool failures this session.
- **Change:** sharpened the Architecture bullet in `.github/skills/skill-evolver/SKILL.md` to mark
  automatic capture best-effort and active capture the PRIMARY path.

### 3. skill-evolver: make the 1024-char description limit explicit
- **Root cause:** skill defect (low). `edit-safety-rules.md` said "keep under the size limits"
  without the number or a check command.
- **Evidence:** `retry`, low — description overshot 1024 (1184 → 1054) twice before fitting (2 turns).
- **Change:** added explicit ≤1024 limit + a PowerShell length-check command to rule 5 in
  `.github/skills/skill-evolver/references/edit-safety-rules.md`.

- **Validation:** `quick_validate.py` passes for both skill-evolver and skill-creator.
- **Commit:** see branch `skill-evolution/retro-2026-06-16` (rollback: `git revert <sha>`).
- **Result/trend:** to be measured on the next retrospective (expect these signatures not to recur).

