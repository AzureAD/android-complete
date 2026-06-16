# Skill Evolution Log

Auditable changelog of changes applied by the `skill-evolver` system. Each entry links a
captured-friction finding to the concrete edit that addressed it, with a rollback reference.

Newest entries on top. See `.github/skills/skill-evolver/references/edit-safety-rules.md` for the
entry format.

<!-- New entries go below this line -->

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

