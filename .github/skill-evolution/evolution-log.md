# Skill Evolution Log

Auditable changelog of changes applied by the `skill-evolver` system. Each entry links a
captured-friction finding to the concrete edit that addressed it, with a rollback reference.

Newest entries on top. See `.github/skills/skill-evolver/references/edit-safety-rules.md` for the
entry format.

<!-- New entries go below this line -->

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

