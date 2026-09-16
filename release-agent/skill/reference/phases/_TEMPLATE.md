# Reference — Phase `<id>` (`<Phase Name>`)  — TEMPLATE

_Copy this to `reference/phases/<id>.md` when a phase gets real agents. Delete this line and fill in._

## Adding a phase
1. **`config/phases.yaml`** — add the phase block and steps with explicit `kind`, dependencies, and applicable effect/command capabilities.
2. **`steps/<phase>/__init__.py` and `steps/<phase>/<step>.py`** — discoverable handlers declaring `ID`, `KIND`, and `build(context: StepContext)`. Every hook accepts only the immutable injected context. Auto handlers declare `EFFECT_MODE` and return `Done`, `Blocked`, or `InProgress` with typed evidence updates; no mutable state or runner adapter is available. Effectful handlers also implement `prepare_effect`/`execute`, and transactional handlers implement `reconcile`. Use the scoped durable evidence committer before dependent provider writes; do not defer create intents or applied-result receipts until final return. Keep pure functions ordinary and inject only IO through named ports. See the README's modular contract.
3. **`skill/reference/phases/<id>.md`** — this file: the conversational guidance (below).
4. **Core `SKILL.md`** — add one row to the **Reference routing table** pointing at this file.

The handler catalog validates and binds configured modules before execution. Missing
real handlers, mismatched IDs/kinds/capabilities, and invalid scheduled times fail
clearly. Unfinished auto steps should declare `implementation: dummy` in YAML
without a module; remove that declaration when adding their implementation.
Never use dummy declarations for human gates or external writes.

Declare producer authority in the module with `EVIDENCE = (OwnStepData(),)` for
its own data, or a named scope from `orchestrator.authority`: `PipelineScope`
with a typed `PipelineSlot`, `VersionEvidence`, `BrokerPlanEvidence`, or
`UIFailureContribution("phase.human_review")`. Shared scopes have one producer;
UI contributions preserve human content/lifecycle. Missing declarations grant
no evidence writes, and none allows arbitrary paths or lifecycle updates.
Declare effect ports with `WRITES = (WriteOperation.CREATE_LIGHTWEIGHT_TAG,)`
(or the required named operations). Broker/Auth creation requires transactional
mode and its matching evidence scope. Read-only invocations never gain writers.
Reusing these capabilities needs no core step-ID registration. Set
`STATUS_EMAIL = True` only when the immutable status-email read model is needed.
New CLI writer/approval capabilities belong alongside their lazy registrar in
`orchestrator/command_catalog.py`, not a second allowlist.
Command and delivery adapters use `orch.scheduling()` for the shared immutable
readiness/hold snapshot and `orch.handler()` for bound capabilities. Never call
private engine query or transition helpers, or mutate lifecycle fields directly.

For public inputs, define a module-local frozen dataclass and declare
`PARAMETERS = {"build": BuildParameters}`; access it through
`build(context: StepContext[BuildParameters])` and `context.parameters.<field>`.
Without a declaration a hook accepts no parameters. Declare approval/retry models
separately under `prepare_approval`/`authorize_retry` (`comment` and `reason`,
respectively). Declare effect preparation inputs under `prepare_effect`; execute and
reconcile accept no live parameters and use only frozen `effect_input`.
Document each field's default/type here: text `--param` values remain text, while
booleans/numbers/collections require JSON. Unknown, irrelevant, duplicate or wrong-type
inputs fail before IO/evidence changes. Mock knobs stay in `MOCKABLE` and
`context.inputs`, separate from public inputs. See README for supported schema types
and capability protocols; no new core parameter registry is needed.

An external gate declares `APPROVAL_COMMAND` and
`WRITES = (WriteOperation.SUBMIT_PIPELINE_APPROVAL,)`, retaining its normal BUILD
preview while implementing all three context-only lifecycle hooks:

| Hook | Parameters and authority | Result |
| --- | --- | --- |
| `prepare_approval` | Frozen `ApprovalParameters(comment: str = "")`, read services | `ApprovalRequest \| Blocked` |
| `submit_approval` | `NoParameters`; only call `context.approval.submit()` | `(bool, str)` |
| `reconcile_approval` | `NoParameters`; `submit=None`, exact approval read service | `(bool, str)` |

Freeze `{org, project, build_id, stage, approval_id, comment}` in the core-validated
request; reject missing/different-stage/ambiguous identities. Read recovery via
`context.services.pipelines.get_pipeline_approval(request.org, request.project,
request.approval_id)` requires matching id, build owner and approved status. Never
rediscover a newer run, infer success from stage completion, or write during reconcile.
Core owns persistence/receipts; handlers never mutate lifecycle state.
Production rejects nonempty gate-local mocks during preview/submission/reconciliation.
Keep useful BUILD mock inputs documented, but use directly injected fake provider
ports and `ApprovalContext` for offline lifecycle tests; legacy `submit: skip` is not success.

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
  after explicit confirmation; relay local gates for Approve/Deny. External gates first
  use `approve-orchestrator-gate --preview --comment "<comment>"` to review the exact
  request/hash with the human, then the same comment plus `--review-hash` and
  `--approved-by` (optional `--phase`/`--step`, `--executor`). `--reserve` checkpoints
  only; unattempted reservations need their original authorization plus `--execution-id`.
  Attempted owners use that ID for read-only receipt recovery, never resubmission.
- **Lifecycle:** declare the owning step/phase/window, source checkpoint bindings and any
  expiry. All worker prompts discover pending notifications even on silence, then run cleanup
  in finally; delete live automation before deregistration. Never infer scope from a name.
- **Blocked?** If an agent step can block on a real problem, state the exit: fix + `next` (re-check), or `skip … --reason`.
  External approvals instead retain exact ownership while evidence is pending/missing/
  rejected/unknown. Receipt recording may continue during halt/cancel, but completion
  waits eligibility and is checkpointed before drain. Ownership blocks reopen,
  upstream invalidation and workflow adoption; never clear it or fabricate success.
  Describe the original provider identity and owner-recovery path, not a destructive reset.

## Automated steps (no skill action)
List the `kind: auto` steps that run inside `next`; relay their outcomes from the
`status` table. Explain which are read-only observations and which use durable
effect execution/reconciliation. Unfinished steps may remain no-op shells with
`[DUMMY]` completion notes; never describe those as actual verification or publication,
and never use them to approve human gates.

## External references
Any IDs/URLs/DLs this phase uses → add to `EXTERNAL-REFERENCES.md`, cite here by name.
