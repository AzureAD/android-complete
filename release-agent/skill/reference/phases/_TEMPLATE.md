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
- **Notifications:** `notification prepare --release <id> --source step --phase <phase> --step <step_id>`,
  review target/payload, then `notification claim` with the approved hash and executor.
  Only `permission_to_send:true` authorizes the returned transport payload. Acknowledge each
  result with `notification result`; never blind-record a notification pass. Unknown outcomes
  need owner review, not automatic retries. See `commands.md` for exact flags.
- **Non-notification actions:** use `step-action` and its existing reservation/domain follow-up
  for MCP/browser work. Attestations use `done --release <id> --step <step_id> --note "…"` only
  after explicit confirmation; relay gates for Approve/Deny.
- **Lifecycle:** declare the owning step/phase/window, source checkpoint bindings and any
  expiry. All worker prompts discover pending notifications even on silence, then run cleanup
  in finally; delete live automation before deregistration. Never infer scope from a name.
- **Blocked?** If an agent step can block on a real problem, state the exit: fix + `next` (re-check), or `skip … --reason`.

## Automated steps (no skill action)
List the `agent:` steps that run inside `next`; you just relay their results from the `status` table.

## External references
Any IDs/URLs/DLs this phase uses → add to `EXTERNAL-REFERENCES.md`, cite here by name.
