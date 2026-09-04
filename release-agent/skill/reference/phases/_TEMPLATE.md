# Reference — Phase `<id>` (`<Phase Name>`)  — TEMPLATE

_Copy this to `reference/phases/<id>.md` when a phase gets real agents. Delete this line and fill in._

## Adding a phase = 3 parallel files + 1 core edit
1. **`config/phases.yaml`** — add the phase block + its steps (id, name, agent, owner, gate/attest/source, depends_on, maps_to).
2. **`phases/agents/<id>.py`** — the real agent(s) that replace `agent: stub`, merged into the registry via `phases/agents/__init__.py`.
3. **`skill/reference/phases/<id>.md`** — this file: the conversational guidance (below).
4. **Core `SKILL.md`** — add one row to the **Reference routing table** pointing at this file.

## Execution model
- Is the phase `execution: parallel` or sequential? (Parallel → process ALL holds per pass; see phases/preflight.md.)
- CCD anchor / window if any.

## Steps (one subsection each)
For every step that needs the skill to act (`source: scout`, `attest`, or a gate):
### `<step_id>` — `<what it does>` (`<scout|attest|gate|agent>`)
- **Trigger:** when `status --json` shows current step `<step_id>` (state …).
- **Prepare (deterministic):** `python -m orchestrator.cli <prepare-cmd> --release <id>` → returns `{…}`.
- **Act:** the MCP/browser action (WorkIQ email/Teams, Playwright, ICM/Kusto), using the prepared payload. Never override resolved recipients/targets.
- **Record:** `record-step --release <id> --step <step_id> --status pass\|attention --detail "…"` (scout steps) OR `done --step <step_id> --note "…"` (attestations) OR relay the gate for Approve/Deny.
- **Blocked?** If an agent step can block on a real problem, state the exit: fix + `next` (re-check), or `skip … --reason`.

## Automated steps (no skill action)
List the `agent:` steps that run inside `next`; you just relay their results from the `status` table.

## External references
Any IDs/URLs/DLs this phase uses → add to `EXTERNAL-REFERENCES.md`, cite here by name.
