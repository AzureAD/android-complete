# Release Orchestrator (`/release-agent`)

The **conductor backbone** for the Android monthly release (ADO items **X4** + **X5**).
It drives the whole release as a state-aware smart checklist: it knows the phases,
runs each step's agent, and **holds at gates** for the release engineer to decide.

> **Implementation coverage.** Phases **0-4** have provider-backed handlers, Scout
> actions, and human gates: pre-flight, CCD communications/localization, RC
> verification, Bug Bash, and finalization/publication. Phases **5-9** retain
> explicit read-only **dummies** for unfinished automatic operations, alongside
> real human actions and approval gates. A `[DUMMY]` completion means only that
> workflow traversal advanced; it does not prove publication, monitoring,
> partner-store delivery, hotfix execution, or close-out occurred.
> `config/phases.yaml` identifies each dummy with `implementation: dummy`.

## Architecture (thin skill over a deterministic engine)

```
 you ──/release-agent──▶  SKILL (conversation layer)  ──shell──▶  ENGINE (Python, deterministic)
                          presents gate briefs,                    state machine + dispatch + run-state
                          relays your approve/deny                 the BRAIN — fully unit-tested
```

- **Engine = the brain.** Selects eligible work, invokes catalog-validated handlers, holds at gates, and persists execution ownership and outcomes. Recovery follows each capability's protocol; uncertain writes are not blindly replayed.
- **Skill = the mouth & ears.** Presents the gate, collects your decision, relays it. Never decides the flow.

## Layout

```
release-agent/                     COMMITTED (distributed with android-complete)
├─ config/
│  ├─ phases.yaml                  the state machine COMPOSITION (phases → steps → gates, order, deps), as data
│  ├─ readiness.yaml               the entry-gate checklist, as data
│  ├─ knowledge.yaml               per-step help (what/where/how/links/faqs) for `step-info` — data (a step module may override via KNOWLEDGE)
│  ├─ schedule.yaml                where CCD comes from (pipeline 3038 coords), as data
│  └─ requirements.yaml            external dependencies (CLIs, extensions, MCP servers) — single source of truth
├─ orchestrator/                   three layers: logic → data → presentation
│  ├─ workflow.py                  compiles phases.yaml into typed, validated phase/step definitions
│  ├─ handlers.py                  validates and binds discovered modules and explicit built-ins
│  ├─ step_context.py              immutable release/evidence views, injected clock, parameters, and services
│  ├─ outcomes.py                  canonical Done / Blocked / InProgress / NeedsHuman / NeedsSkill results
│  ├─ effects.py                   auto-handler effect modes, operation identity, and recovery dispatch
│  ├─ approvals.py                 frozen external-gate reviews, one-attempt submission and exact receipt recovery
│  ├─ projection.py                pure derived lifecycle: completion, frontier, holds, status, pending actions
│  ├─ transitions.py               generic eligibility, reservations, lifecycle mutations, and invalidation
│  ├─ invariants.py                persisted-fact integrity checks at transition boundaries
│  ├─ engine.py                    the conductor: dispatch over compiled workflow/projections
│  ├─ readiness.py                 ReadinessGate: entry-gate logic (verify/sign/decline) → structured data
│  ├─ schedule.py                  CCD math (2nd Wednesday, override, phase anchors) — pure, no IO
│  ├─ mocks.py                     local test overlay loader (mocks.local.yaml): canonical outcomes / readiness
│  ├─ knowledge.py                 step knowledge resolver (config/knowledge.yaml + module KNOWLEDGE overlay)
│  ├─ infra.py                     infra preflight: check CLIs + register/verify MCP servers into Scout config
│  ├─ render.py                    presentation only: structured data → text/markdown (swap for other UIs)
│  ├─ state.py                     schema-v3 Release State Record: facts, evidence, execution ownership
│  ├─ revision.py                  compact runtime/phase identity and reviewed workflow adoption
│  ├─ discovery.py                 find releases (none / one / many)
│  ├─ registry.py                  automation registry (track provisioned automations for teardown)
│  ├─ eventlog.py                  per-release interaction + event log
│  ├─ cli.py                       thin entry point: builds the parser from commands/, dispatches
│  ├─ cli_common.py                shared CLI plumbing (load state, emit, event log, advance block)
│  └─ commands/                    one module per command domain (self-registering subparsers)
│     ├─ release.py                lifecycle + overrides: init/list/status/next/approve/deny/done/skip/reopen/halt/resume/activate
│     ├─ readiness.py              entry gate: checklist/verify/sign/decline
│     ├─ pipeline.py               real pipeline writes (gated): set-ccd / skip-release
│     ├─ notify.py                 daily phase digest (tick advances + reports; notify = read-only) + set-owner
│     ├─ lockdown.py               CCOA overlap recorder: check-lockdown
│     ├─ step_action.py            generic scout-step dispatcher + mock-spec + step-info
│     ├─ notice.py                 record-step (scout-step recorder)
│     ├─ logs.py                   event log: log / journal
│     ├─ automation.py             automation registry command
│     └─ infra_cmd.py              infra preflight command
├─ steps/                          THE STEP HOME — one self-contained module per step (auto-discovered)
│  ├─ __init__.py                  discover()/get_step(): scans steps/<phase>/*.py — NO hand-maintained registry
│  ├─ lib/                         shared step helpers (context views, templating, mock inputs)
│  └─ preflight/                   Phase-0 step modules — each declares ID/KIND/build + optional MOCKABLE/KNOWLEDGE/CONFIG
│     ├─ notice.py  flight_reminder.py  lockdown.py   (scout)
│     ├─ breaking.py  cg.py  cron.py                  (read-only auto)
│     ├─ oneauth_access.py                           (idempotent auto effect)
│     └─ confirm_reminders.py  vitals.py               (attest)
├─ phases/
│  ├─ stub_runner.py               [DUMMY] no-op used only for explicitly configured read-only placeholders
│  └─ readiness_verifiers.py       auto verifiers for the entry gate (pass/fail)
├─ tools/checks.py                 real IO (az / http), isolated
├─ skill/SKILL.md                  the /release-agent Scout skill
├─ mocks.local.example.yaml        template → copy to mocks.local.yaml (gitignored) for local testing
├─ setup/bootstrap.ps1             one-time setup (infra preflight, installs skill)
└─ tests/                          pytest unit, flow-replay, extension, and structural guardrail coverage

.release-runs/<YYYY-MM>/           GENERATED, gitignored (per-release working state)
├─ release-state.json              the per-release metadata + run-state (owner, CCD, steps, gates, …)
├─ events.jsonl                    the per-release event/interaction log
└─ _automations.json               registry of THIS release's provisioned Scout automations (owned by the release; removed at close)
.release-runs/_automations.json    GENERATED, gitignored — registry of SHARED (machine-wide) automations only
```

## Adding a step (the modular contract)

A step is **one self-contained module** plus one configuration entry; sequencing and
lifecycle policy remain data-driven. Adding a step touches **2 files**:

1. **`steps/<phase>/<step>.py`** — the handler. Declare `ID`, the handler `KIND`
   (`agent`|`scout`|`human`|`attest`|`gate`), the functions required by its compiled workflow
   kind, and any configured command capability. Every implemented auto handler declares
   `EFFECT_MODE`; effectful handlers implement `prepare_effect` and `execute`, and
   transactional handlers also implement `reconcile`. The engine hashes and freezes the
   prepared provider input as the operation identity. `effect_recovery` explicitly chooses
   frozen-input recovery or current-input matching. Optional `MOCKABLE`, `KNOWLEDGE`, and
   `CONFIG` remain co-located. It is auto-discovered and bound by `HandlerCatalog`.
2. **`config/phases.yaml`** — place the step in the flow with an explicit `kind`,
   dependencies, and reusable capabilities such as `approval_command`, `write_command`,
   `effect_mode`, `repeatable`, `pollable`, and `refresh_invalidation`.

**Auto execution uses one result contract:** `AutoOutcome = Done | Blocked | InProgress`
from `orchestrator.outcomes`. A read-only handler implements `build(context)` directly;
there is no `run` wrapper or `StepResult` adapter. The compiler requires a callable
`build` and matching effect declarations. The engine rejects unsupported return types
before applying an outcome, rather than treating them as completion.

Command and delivery adapters consume the public `orch.scheduling()` snapshot
for frontier, readiness, completion, and holds, and `orch.handler()` for bound
capabilities. They must not call private engine helpers or mutate lifecycle fields.

```python
from orchestrator.outcomes import AutoOutcome, Blocked, Done
from orchestrator.step_context import StepContext

ID = "ccd_recorded"
KIND = "agent"  # config/phases.yaml uses kind: auto
EFFECT_MODE = "read_only"

def build(context: StepContext) -> AutoOutcome:
    if not context.release.ccd:
        return Blocked("Record the Code Complete Date first.")
    return Done("Code Complete Date is recorded.")
```

Return `Done(note, links=...)` for completion, `Blocked(reason, links=...)` for an
owner-resolvable problem, or `InProgress(note, poll_in_min=..., links=...)` while
underlying work runs. `Done.by` and `Blocked.by` default to `agent`; outcome mocks use
`mock`. Effectful handlers return the same outcomes from `execute`/`reconcile`; the
engine calls those hooks, not their `build`, to preserve durable effect ownership.
`prepare_effect` still returns the provider-input mapping or `Blocked`.

**Handlers receive invocation-local inputs, not mutable release state.** Every implemented
`build`, `prepare_effect`, `execute`, `reconcile`, `authorize_retry`, `prepare_approval`,
`submit_approval`, and `reconcile_approval`
accepts only `StepContext`; there is no old-signature fallback. It contains immutable
`release` and `evidence` views, a fixed invocation `clock`, typed `parameters`, explicit
offline `inputs`, and named read-only domain `services`. Read a step with
`context.evidence.step(phase, step)`; detach data with `thaw` before a pure transformation.
Pure validators, renderers, mappers, and allocation functions remain ordinary functions.
Only IO uses injected ports. Tests can inject service callables, a clock, and an ID source.
The final-status-email service receives a prepared immutable read model, never an engine.

**Parameters belong to the module and hook, not a global option bag.** Omit `PARAMETERS`
for hooks without inputs (the default is `NoParameters`). Declare a frozen dataclass
for each hook that accepts public input; adding a field requires no core registry change:

```python
from dataclasses import dataclass
from orchestrator.outcomes import NeedsHuman
from orchestrator.step_context import StepContext

@dataclass(frozen=True)
class BuildParameters:
    ticket: str
    reviewers: tuple[str, ...] = ()

PARAMETERS = {"build": BuildParameters}

def build(context: StepContext[BuildParameters]) -> NeedsHuman:
    return NeedsHuman(f"Review {context.parameters.ticket}.")
```

Schema keys are configured hook names: `build`, `prepare_effect`, `execute`, `reconcile`,
`prepare_approval`, `submit_approval`, `reconcile_approval`, or `authorize_retry`.
Defaults and supplied values are strictly
validated before binding IO or opening an evidence session; unknown and irrelevant
parameters fail with the step and hook name. Supported field types are `str`, `bool`,
`int`, `float`, `None`, `Literal`, unions, `tuple[T, ...]`, and `Mapping[str, T]`;
collections are copied into immutable tuples/mappings. Factories/defaults are captured
at catalog compilation. Dataclass initialization may enforce domain constraints;
revalidation of an already typed model does not repeat initialization transformations.

`step-action --param` and step-source notifications target only the `build` model.
Text fields preserve literal text; non-text fields take JSON, for example
`--param enabled=true` or `--param 'reviewers=["verified@example.com"]'` if those fields
are declared. Repeated or empty parameter names are errors. Domain commands map their
existing flags into their module model. `prepare_approval` owns `comment`; retry owns
`reason`, never a disguised approval comment. Execute/reconcile models must have no
input fields: auto writes consume the reviewed, frozen `effect_input`. External-gate
`submit_approval` and `reconcile_approval` also receive `NoParameters`: their coordinates,
approval id, build, stage and comment come only from `context.approval.request`.
Mock inputs remain separate under `MOCKABLE`/`context.inputs`; they are not public parameters.

Capability protocols in `orchestrator.handler_contracts` describe build, auto,
prepare, approval, and retry hooks and their module combinations. The catalog validates
context-only signatures (hook annotations are optional; optional closure defaults are
not CLI parameters). Bound hooks reject a wrong step, role, parameter model or write
authority and malformed canonical results before evidence/lifecycle application.

| Hook | Input / authority | Result |
| --- | --- | --- |
| `build` | Its declared parameters; read services | `AutoOutcome` for auto steps, otherwise `Outcome` |
| `prepare_effect` | Its declared preparation parameters; read services | `dict \| Blocked` |
| `execute`, `reconcile` | Frozen effect input and scoped `context.effect` | `AutoOutcome` |
| `authorize_retry` | Its declared reason model; evidence-only recovery | `RetryDecision` |
| `prepare_approval` | `ApprovalParameters(comment: str = "")`; read services only | Frozen `ApprovalRequest \| Blocked` |
| `submit_approval` | `NoParameters`; `context.approval.submit()` is the only fenced writer | `(bool, str)` |
| `reconcile_approval` | `NoParameters`; frozen request, read services, `submit=None` | `(bool, str)` |

External gates implement all three approval hooks in addition to their unchanged build.
`ApprovalRequest` carries `{org, project, build_id, stage, approval_id, comment}`; core
validates it. The read port `context.services.pipelines.get_pipeline_approval(org,
project, approval_id)` returns `(ok, approval_dict | None, detail)`. Reconciliation
must positively match the exact requested id, expected build owner and approved status;
it cannot rediscover the newest run or infer a receipt from stage completion.

Return typed transient `updates` alongside the outcome: `StepData`, `PipelineEvidence`,
`ReleaseVersions`, `BrokerResource`, or the constrained `UIFailureReminder` producer
contribution. The orchestrator validates the issuer-bound permit, generation, evidence
baseline, and allowed producer/target before applying existing serialized fields.
`as_dict(outcome)` omits these internal updates; external tool payloads stay ordinary JSON.
Diagnostic `rc-report` reads do not replace authoritative verifier evidence.

**Evidence and write authority belong to the module, not its step ID.** Declarations
are source configuration compiled into the frozen handler catalog; they add no
persisted fields. Both default to empty (deny), including for a new auto handler:

```python
from orchestrator.authority import OwnStepData
from orchestrator.evidence import StepData

EVIDENCE = (OwnStepData(),)

def build(context):
    return Done("Observed", updates=(StepData({"observed": True}),))
```

`OwnStepData` replaces only the invoking step's existing `data` map. Shared producer
scopes are `PipelineScope(PipelineSlot.CHECKER|ORCHESTRATOR|ECS|LOCAL|AUTH)`,
`VersionEvidence()`, and `BrokerPlanEvidence()` (the existing `broker_test_plan`
resource). Each shared scope has one configured producer. Pipeline scope preserves
other lanes and RC iterations. `UIFailureContribution("bug_bash.ui_failures")`
allows only the existing generated UI contribution on a configured human-review
step, retaining human notes, links, data, status and attribution. No declaration
grants arbitrary paths or lifecycle writes; permit, baseline and generation checks
still apply. Reusing a scope with a new step ID requires no core change.

For an effect handler, declare only its required named ports:

```python
from orchestrator.authority import BrokerPlanEvidence, OwnStepData, WriteOperation

EVIDENCE = (OwnStepData(), BrokerPlanEvidence())
WRITES = (WriteOperation.ENSURE_BROKER_PLAN,)
```

`WRITES` requires a compatible effect mode; external approval handlers require only
`WriteOperation.SUBMIT_PIPELINE_APPROVAL`. Broker creation additionally requires
`BrokerPlanEvidence`, Auth suite creation requires `OwnStepData`, and both require
transactional effects. Unknown, duplicate or incompatible declarations fail startup.
Only declared ports are installed; injected bundles cannot broaden authority.
Production functions are bound at invocation time, respecting test provider guards.
New behavior families may require domain adapters; reusing existing ports does not
require engine edits. `STATUS_EMAIL = True` requests the immutable status-email
read model without exposing an engine to the handler.

`orchestrator/command_catalog.py` is the single CLI registrar/capability source,
with lazy command imports and registration-parity checks. Command modules retain
their verbs and argument semantics. RC polling uses shared scheduling eligibility,
not a verifier-ID allowlist; Phase-2 scoping, RC verdict aggregation, and the
30-minute/6-hour courtesy policy remain intentional domain logic. The consolidated
`rc_report` applies its prepared quality verdict after confirmed delivery; there is
no separate Bug Bash approval handler or report-recovery gate.

Only an authorized effect invocation has `context.effect`: its frozen execution/intent,
declared write ports, and a narrow durable `commit`/`read` evidence interface.
`context.effect.commit.commit(update)` returns a fresh immutable recovery snapshot only
after a successful checkpoint. Broker create intent is saved before POST and its returned
ID before suite construction; Authenticator create intent and absence-verified retry
evidence remain durable; UI publication saves actual applied points before assignments.
Checkpoint failure restores in-memory evidence and prevents the next provider operation.
Exact owned receipts can still be saved during suspension, but new provider writes cannot.
`authorize_retry(context)` returns `RetryDecision`, not an untyped tuple.
Owner recovery can receive a transient evidence-only permit; it cannot invoke writers or
complete lifecycle state. Contexts, permits and capability bundles are never persisted;
external approval requests/receipts use the separate existing execution/data slots below.

For a new phase, also add `steps/<phase>/__init__.py` so discovery includes its modules.
Missing real handlers are configuration errors, even if an outcome mock is supplied.
An unfinished auto step must explicitly declare `implementation: dummy` and have no
handler module:

```yaml
- { id: placeholder, name: "Implementation deferred", kind: auto, implementation: dummy }
```

Dummy steps are read-only no-op shells; their `[DUMMY]` completion means traversal
only, not actual verification or publication. Dummies cannot replace human gates,
external actions, or effectful auto work. When implementing a dummy, remove its
`implementation: dummy`, add the real module and declare its effect policy. Ordinary
human actions and local gates may use built-in prompts without a module; they still
require explicit completion or approval.

### Validated handler catalog

`HandlerCatalog.compile(workflow, resolver)` binds each configured step once to its
entry points and capabilities. It validates module `ID`/`KIND`, effect and command
declarations, required hooks, module-owned parameter schemas, and scheduled `CONFIG.fire_at_local` values. A
discovered module with an `ID` but no valid `build` is an error, not an absent step.
Unconfigured modules do not become executable merely because discovery finds them.

The engine and generic execution adapters use `orch.handler(phase, step)` rather
than rediscovering modules. Descriptor callables and copied capability/timing/mock
metadata are immutable; projection receives the bound timing lookup. Effect inputs
are still prepared and frozen at execution time, not catalog compilation.

For isolated execution, pass `handler_resolver=` to `Orchestrator`; the default is
package discovery. New CLI invocations bind fresh descriptors. In-memory workflow
edits compile a replacement workflow and catalog together; if either is invalid,
neither replaces the previous pair and no work runs against the invalid edit.
Existing releases are pinned to their persisted workflow revision. A different
runtime or executable phase contract requires explicit reviewed adoption below.

That's it. Build mocking uses `outcome`/knobs; production external-gate approval
preview/submission/reconciliation rejects any nonempty gate-local mocks.
`step-info` shows its knowledge;
`step-action`/`mock-spec` find it. The **`test_step_modules_and_config_stay_in_sync`**
guardrail and workflow compiler fail loudly if a handler and `phases.yaml` drift.

Phase-2 ownership follows the same rule: `steps/build_verify/rc_report.py` owns the
captured report model, readiness, and consolidated gate decisions; `auth_ecs.py` owns
Authenticator collection, evidence checks, and the informational suite verdict.
The ECS/Local MRWP steps share `_mrwp.py`. `_rc_report_rendering.py` renders the
evaluated report as HTML/plain text (and supplies display helpers to the CLI);
it receives decisions from `rc_report`, rather than importing the step.
`_common.py` holds only shared snapshot storage, evidence primitives, and recovery/link
utilities. Underscore-prefixed helpers are intentionally excluded from step discovery.

MRWP RC reports count **distinct tests with any-pass-wins reconciliation** for unit,
instrumented and UI categories. One exact title within one normalized suite in the
current build/provider counts once: any `Passed` attempt is success, even if a failure
comes later. A real non-NA result with no pass is one failure. The denominator is
`passed + failed`; titles with only `NotExecuted`, `NotApplicable`, `None`/null,
`Inconclusive` or `Warning` outcomes are excluded. Parameterized titles and suite/API/device
distinctions remain separate; providers and RC builds are never combined for reconciliation.

One complete paged read of all runs/results (including clean reruns) produces category
counts, full failed/recovered lists and audit evidence. Stored `tests.suites[].test_results`
records each title's verdict, outcome counts and run/result IDs for every attempt;
`count_basis: distinct_tests_pass_any` identifies this policy. HTML, plain text and CLI
show all unresolved failures and all recovered successes, with provider/suite context.
Historical failure attempts are informational, not extra gate failures. The UI threshold
remains 90%; the separate Authenticator Firebase gate retains its existing count/build policy.
Unreadable, missing or invalid result pages are unavailable evidence, never aggregate
fallback or zero failures. Stale raw-count snapshots must be refreshed before gating/reporting:
there is no relabeling, migration or backwards-compatibility interpretation.

Phase-3 `ui_test_status` uses `tools/pipelines/ui_projection.py` to project those **same
current-RC snapshots** onto Broker plan points; it never refetches MRWP results or redoes
retry math. Both snapshots, policy/build attribution, counts and per-test evidence are
validated before any external mutation. Refresh missing/stale evidence in Phase 2.
Several distinct titles/API suites may map to one case/config: **any Failed wins**;
otherwise Passed wins over NA, and NA-only sets NotApplicable. ECS/Local and PROD/RC-MSAL
remain separate. Unknown mappings are diagnosed, unmatched/manual points stay untouched.
The step records compact RC/build/policy provenance and mapping/partial-write status;
failure reassignment and the generated human reminder use the same current verdicts.
Recovered tests disappear from that reminder on rerun without changing human completion
or unrelated notes. Authenticator's separate selected-run best-effort fill and Firebase
gate are unchanged.

**Two homes for data (by lifetime):**
- **Release metadata + run-state** → `.release-runs/<id>/release-state.json` (per-release; the `ReleaseState` record). Holds `owner_email`/`owner_name` (the release owner, resolved from the signed-in `az` user at `init`; reminders email this person), `ccd`/`ccd_source`/`ccd_conflict`, step completion, gate decisions, `last_notified_date`, etc. Add release-scoped fields here.
- **Tool config** → `release-agent/config/*.yaml` (not release-specific; committed): `phases.yaml`, `readiness.yaml`, `schedule.yaml`, `requirements.yaml`.

## Architecture — three layers (so it adapts to other interfaces)

1. **Logic** (`workflow.py`, `handlers.py`, `effects.py`, `projection.py`, `transitions.py`, `invariants.py`, `engine.py`, `readiness.py`, `schedule.py`, `state.py`, `revision.py`) — compiled workflow and handler catalog, bound effect capabilities, pure lifecycle projections, one generic transition kernel, and schema-v3 persisted facts. A gate is complete only when its step is `done` and its latest decision is approved.
2. **Presentation** (`render.py`) — pure functions: structured data → text/markdown. A different interface (web UI, TUI) swaps this layer and reuses everything else.
3. **Interface** (`cli.py` + `cli_common.py` + `commands/` + `skill/SKILL.md`) — the CLI is a thin assembler: `cli.py` builds the parser from the self-registering modules in `commands/` (one per domain), and shared plumbing lives in `cli_common.py`. Adding a command is a localized change to one module.

IO lives in `tools/` and `phases/` (pluggable). Config is data in `config/`.


## Run-state: two kinds (the X5 idea)

- **Derived** — recomputed from workflow + persisted facts (frontier, lifecycle status, current hold, pending owner actions) and from external systems of record (ADO/Git/Play Console/ADX). Derived lifecycle projections are never authoritative state.
- **Persisted** — decisions/intent, readiness facts, step outcomes, evidence, execution
  reservations, invalidation history, delivery receipts, external resource identities,
  cancellation, and emergency halt. Stored in `release-state.json`.

The conductor is **stateless**: on each invocation it loads the record, (later) reconciles against live systems, decides, acts, writes back. That's what lets a release resume across days/sessions.

`release-state.json` is **schema v3** and intentionally breaking. Older records are
rejected, never migrated or reinitialized to bypass ownership. Preserve the record
and recover old work with its original supported runtime.

### Workflow revision and adoption

`init` explicitly binds a new release to `workflow_revision`: a `runtime_hash`, ordered
`phases` containing `{id, definition_hash, step_keys}`, and `last_adoption` (initially
null). Hashes use `sha256:<64 lowercase hex>`. The overall revision ID is derived from
the runtime hash and ordered manifest, excluding the adoption receipt; no redundant
ID, source snapshot, payload or revision journal is stored.

Runtime identity reads production core, handlers, providers, command code, execution
configuration/assets and supported Python/PyYAML/tzdata identity afresh. It excludes
tests, docs, run artifacts, local mocks/secrets and raw `phases.yaml` bytes. Phase
identity uses resolved executable defaults, ordering, timing, evidence, parameter,
write and effect contracts; display labels do not invalidate work. Changed source
requires a fresh process, not hot-reloading an owned execution.

The precise file inputs are Python files under `orchestrator`, `steps`, `tools` and
`phases`; shipped files under `templates` and `config`; and root
`requirements*.txt`, `pyproject.toml`, `poetry.lock`, `uv.lock` when present. Hidden
paths, caches, tests/docs/scenarios, local mocks and non-Python secret/credential
assets are excluded. Custom workflow directories contribute their sibling execution
YAML files and the selected readiness file, never the selected workflow YAML itself.
Relative names and SHA-256 content digests are combined with Python implementation/
full version and installed PyYAML/tzdata versions (explicit absence included).
No provider calls, persisted cache or historical code loader are involved. This is
compatibility identity, not reproducible live ADO state or reconstruction of old source.

Unbound schema-v3 state remains inspectable but cannot dispatch, reserve, approve,
write or claim notifications. Loading never binds a record. Tests creating fresh
state explicitly call `revision.bind_initial(orch)`; loaded records cannot use that
helper. Status on a mismatch reports a block, not stale completion.

```powershell
python -m orchestrator.cli workflow-adopt --release <id> --json
python -m orchestrator.cli workflow-adopt --release <id> --approve-hash <reviewed-hash> --by <owner> --reason "<reviewed change>"
```

The preview lists old/new revision IDs, exact conservative invalidations and ownership
blockers. Confirmation recomputes the review under the release lock, then the automation
registry lock, and rejects stale hashes. Runtime changes invalidate all workflow
results; phase changes/reordering invalidate the earliest changed phase and all later
phases. New steps are pending and removed-step history, evidence, resources and terminal
write-review receipts are retained. Affected gate decisions are cleared. Never-claimed
notification offers (including release-scoped offers) are removed without fabricating
`not_sent` receipts; previously attempted offers retain their revision scope fence.

Any stored execution—even under a removed step—unresolved resource creation, active or
uncertain delivery/automation claim, or unfinished sent-notification completion blocks
adoption. Restore the pinned runtime for code-dependent recovery; never force-clear
ownership. Adoption saves the binding, bounded `{from_revision, at, by, reason}` receipt
and invalidations atomically, rolls back on save failure, and never drains or calls a provider.
Reconcile changed automation provider specs through the existing reviewed registry
protocol before resuming their schedules; adoption does not update old worker prompts.

### Exact write reviews

The five checked commands are `distribute-tests`, `create-integration-prs`,
`create-oneauth-common-pr`, `create-payload-wiki` and `launch-localization`.
Their default read-only preview prints a versioned transient operation plan and
`review_hash`. `distribute-tests` remains human-reviewed because it applies live ADO assignment changes:
repeat selection flags with `--apply --review-hash <approved hash> --approved-by
<reviewer>`. `--executor <session>` optionally identifies the claiming runner.
`--reserve` accepts the review without executing; subsequently repeat the same
command/flags/hash/reviewer with `--execute` and its `--execution-id`, omitting
`--reserve`.

The scheduled release writers `launch-localization`, `create-integration-prs`,
`create-oneauth-common-pr`, and `create-payload-wiki` run with
`--execute --auto-approve --executor <automation-id>`. The command recomputes the
current provider/source/content plan, checkpoints its hash, fences exactly one provider
write, then verifies/read-backs the receipt before attaching completion. Generic
`reserve-step`, `step-action --reserve`, raw provider tools and `record-step` are not
alternatives.

The envelope binds release, qualified step, generation, workflow revision, command,
normalized actual parameters, exact targets/content/ordered operations and concurrency
preconditions. Under the existing release lock the adapter replans before accepting
the review, saves `execution.write_review = {hash, approved_by}`, then replans again
before durably fencing one attempt as `in_flight`. Only the captured operation
sequence executes. Save failure returns no permission; interruption or uncertain
results retain ownership, never authorize automatic compound replay.
Adapters construct immutable `write_review.WritePlan` / `WriteOperation` values,
call `preview` for inspection and `authorize(..., planner)` for execution. The
returned `WriteAuthorization` validates the exact owner/generation before each operation.
Domain planners own resolution; the core never routes or invents writes by step ID.

- Distribution binds availability, roster, live case revisions and point-tester
  snapshots/IDs. Previews do not save availability; repeat those flags on execution.
  Only availability evidence, result notes and the review digest persist, never an
  assignment array. Final fresh ADO validation determines success.
- Integration PRs bind selected repositories, hosting targets, branch tips, existing
  PR choice/content, labels, exact RI merge/Gradle edits and PBI mode/id/title. Only
  the explicitly reviewed new-PBI-ID placeholder may be substituted into PR bodies.
- OneAuth uses a clean local checkout (`--repo-dir` if needed), verified remote and
  existing Git objects to calculate the full merged tree plus four version/changelog
  edits in isolation. Both Git adapters preserve the user's checkout/refs, review exact
  deterministic commit bytes, and CAS-push from the reviewed parent. Dirty/conflicted
  work, missing objects or unsupported paths hold before writes; fetch/resolve separately
  and review again. No unreviewed server-side merge occurs.
- Wiki writes bind organization/project/wiki/path, create-vs-update, full body and
  existing page ETag/content. Creates require absence; updates use the reviewed ETag,
  never a freshly fetched overwrite token.
- Localization queues only the reviewed pipeline revision/repository/source commit/
  variables. It verifies the provider build receipt before attaching the existing
  build-id/run-link fields. `record-localization-run` is matching-receipt recovery
  only, including an execution-bound launch-time window—not launch permission.

Before any execution is closed, including explicit owner `done`/`skip`/`reopen`
resolution with provider evidence, the latest authorization is retained in
`data.last_write_review = {execution_id, hash, approved_by, approved_at, workflow_revision}`.
`approved_at` is the reservation's `started_at`. It survives invalidation and is
overwritten only by the next closed reviewed execution. It proves authorization,
**not** success of every provider operation, and can never authorize another attempt.
There is no exactly-once guarantee: after a timeout inspect provider results and resolve
the owner before obtaining a fresh review. No prompts, write payloads or receipt journal
are added to release storage.

### Approved external-gate lifecycle and recovery

`finalize.remove_rc_tags_gate` approves **Remove RC Tags**, enabling the orchestrator's publish
stages. `finalize.publish_notes_gate` separately approves **Publish GitHub Release Notes**
after integration PRs merge. Neither plain `approve` nor generic `done`/`skip` may complete
these externally backed gates.

```powershell
python -m orchestrator.cli approve-orchestrator-gate --release <id> --phase finalize --step remove_rc_tags_gate --preview --comment "<reviewed comment>"
python -m orchestrator.cli approve-orchestrator-gate --release <id> --phase finalize --step remove_rc_tags_gate --comment "<same comment>" --review-hash <hash> --approved-by <reviewer> --executor <session>
```

`--phase`/`--step` may select an explicit eligible gate; otherwise the command resolves the
holding gate. Preview returns the exact request and `review_hash` without persisting or
submitting anything. Review the coordinates, build, stage, approval id and comment with
the human. The write needs the same comment, approved hash and reviewer; the executor is
optional. Add `--reserve` to checkpoint ownership/authorization only, without provider IO.
To submit that **unattempted** reservation, omit `--reserve` and supply its `--execution-id`
with the original hash/reviewer/comment. `--as-of` affects scheduling checks only; ownership,
attempt and receipt timestamps always use the trusted current clock.

Core checkpoints this exact nested authorization in the existing schema-v3 step record;
there are **no new top-level fields**:

```json
{
  "execution": {
    "id": "<execution-id>",
    "owner": "<executor>",
    "started_at": "<UTC timestamp>",
    "approval": {
      "request": {
        "org": "https://<ADO organization>",
        "project": "<project>",
        "build_id": 123,
        "stage": "<exact stage>",
        "approval_id": "<provider approval id>",
        "comment": "<reviewed comment>"
      },
      "request_hash": "<request binding hash>",
      "workflow_revision": "<revision binding hash>",
      "approved_by": "<reviewer>",
      "submission_started_at": null,
      "receipt": null
    }
  }
}
```

Before the writer can run, `submission_started_at` is durably set to the actual attempt
time. The only submission hook call is `context.approval.submit()`, which uses the exact
frozen coordinates/id/comment. Missing identity, a different stage or ambiguous same-build
approvals block preparation; no provider id is fabricated.

After an interrupted/uncertain attempt, recover the **same** owner:

```powershell
python -m orchestrator.cli approve-orchestrator-gate --release <id> --phase finalize --step remove_rc_tags_gate --execution-id <owned-id>
```

An attempted execution only reads the frozen approval; it never resubmits. Success requires
the exact approval id, its expected build owner and `status: approved`. Pending, missing,
rejected, canceled or unknown results retain the hold. A newer run, a later parked gate or
a completed stage alone cannot settle it. The receipt is only
`{approval_id, status: "approved", observed_at}` from positive provider evidence.
Core can save receipt evidence during halt/cancellation, but completion waits for current
eligibility. Finalization atomically moves `{execution_id, ...approval}` into
`data.last_approval`, clears `execution`, and records the human gate decision **before**
draining downstream work. A downstream failure cannot erase that checkpoint.

Approval ownership blocks reopen, upstream invalidation and workflow adoption. Keep the
record and pinned runtime for exact recovery; there is no automatic destructive reset,
timeout-based ownership theft, generic completion bypass or second-request retry.
Production rejects all nonempty gate-local mocks before approval preview/submission/
reconciliation. Existing BUILD `approval`/`stage_state` previews remain available.
Legacy `submit: skip` cannot authorize a receipt; offline lifecycle tests inject fake
provider ports and an immutable `ApprovalContext` directly.

### Generic execution and transition contract

- Approval commands save the accepted local gate decision before draining any later
  steps. A failed save or rejected local decision stops advancement; a later handler
  failure cannot lose an already-saved approval. External approvals first checkpoint
  their exact authorization and attempt boundary, then call the fenced provider writer.
  Exact-ID recovery retains receipts across local save failures; stage completion is
  never a substitute. See the external-gate lifecycle below.
- Parallel phases attempt independent ready auto work once per drain, including
  effect recovery. `InProgress` remains a `waiting` result, but does not stop ready
  siblings or newly unblocked dependents of completed siblings. Waiting/blocked
  work does not satisfy its own dependencies. Once no more auto work is eligible
  in this pass, the engine returns the outstanding gate, action, reservation, or
  in-flight hold. A later invocation may poll/reconcile again; there is no busy loop.
  Sequential phases still stop immediately at a wait or action hold.
- `NextAction.continue_drain` is transient dispatch control, independent of the
  displayed action kind. It is not persisted in release state. Direct `step_once`
  callers may use it to distinguish an observed step wait from the settled hold;
  the simulator honors step-local waits without inventing completed work.
- `next` executes only `auto` steps. Every implemented auto handler has an explicit,
  orthogonal effect policy:
  - `read_only` calls `build(context)` directly under the release lock.
  - `idempotent` persists an engine-owned execution and stable operation key before the
    first write; an interrupted run repeats only that same provider operation.
  - `transactional` persists the same ownership boundary, but an interrupted run calls
    the handler's `reconcile(context)` instead of its create path.
  Resolved provider inputs are frozen in the execution record. `effect_recovery: frozen`
  always finishes that exact operation; `match_current` requires the newly prepared input
  to retain the same hash. Input drift and unresolved provider outcomes block with the
  original execution preserved. `done`, `skip`, and `reopen` cannot clear ownership.
  `retry-effect` requires provider-proven absence for non-idempotent creates;
  `supersede-effect` replaces only a declared idempotent desired-state operation.
  Human actions, attestations, approval gates, and external actions use their own eligible
  transition paths.
- Configured external writers use the exact checked-command review contract above.
  Generic non-notification reservations remain only for actions without a configured
  write adapter; receipts and pollers must present their exact execution ID.
- Notification sends use the independent prepare/claim/result contract. Reopen and refresh
  create a new generation; stale prepared messages and stale execution IDs cannot complete it.
- `reopen` invalidates configured downstream work. Sequential phases invalidate later steps
  and phases; parallel phases invalidate transitive dependents and later phases. An active
  engine-owned auto effect or external approval anywhere in that closure rejects the **entire** operation before
  evidence, lifecycle, gate decisions or invocation permits change. Settle its current
  execute/reconcile operation first; no force-clear is available. Independent same-phase
  parallel work outside the closure does not block reopening. Other active external
  dependent executions still become blocked for owner review instead of being silently discarded.
- Cancellation and emergency halt are independent facts. Cancellation retires
  release-scoped workers; reactivation requires provisioning the needed automations again.

**Outcome authorization is separate from application.** Handlers return
`Done | Blocked | InProgress`; they do not set lifecycle state. Production adapters use
`Orchestrator.authorize_outcome(intent, phase, step, execution_id=...)` **before**
invoking a handler, then `apply_outcome(permit, outcome, data=...)`. The permit is
in-memory, issuer-bound, single-use, and tied to the existing step/execution generation;
it adds no persisted fields. `validate_outcome_permit(permit)` rechecks a prepared
action before further work. The unchecked application helper is private.
`validate_outcome_application(permit, outcome, data=...)` additionally preflights the
proposed lifecycle/invalidation without consuming its permit or granting another provider
call. Both automatic and explicit outcome application perform this check **before** typed
evidence updates; a rejected result can be retried after the blocking effect settles.
Already-durable effect evidence/checkpoints are not rolled back by an upstream rejection.

Conditional activation and refreshed outcomes use the same protected-effect boundary.
`refresh_invalidation: always` denies refresh invocation/reservation up front when its
closure contains an active effect. `status` permits observation, but rejects application
of a status-changing result until the effect settles; a status-preserving refresh is
allowed. `never` does not invalidate dependent work. These policies also govern owned
refresh completion and poll omission. Already-invalidated/corrupt effect owners are
diagnosed for owner-reviewed recovery or repair, never silently reset or migrated.

- Explicit intents distinguish auto `EXECUTE`, configured non-gate `MOCK`, owned
  `EFFECT` recovery, external `PREPARE`, `POLL`, `REFRESH`, `WRITE`, and generic
  external `RECORD`. Attribution (`by`) never grants authority. Human actions and
  gates cannot be completed by generic handler results; use their human operations.
- New calls require the appropriate kind, prerequisites, owning frontier, phase
  date/fire time, and release readiness. Halt, cancellation, unsigned/blocked
  readiness, or denial forbids new calls. Reserved polls and writes require the
  exact existing execution ID; observation permits cannot bypass effect checkpoints.
- Already-invoked observations/effects may settle after suspension begins, using their
  unchanged permit or `settle_execution(phase, step, execution_id, outcome, data=...)`
  for an exact reserved receipt. Settlement is **not** permission to call the provider
  again: scheduling remains suspended and drain continuation stays disabled. Changed
  generations/prerequisites/frontiers, invalidated owners, and duplicate terminal
  results are rejected; they cannot complete or clear replacement work.
- Notification transport receipts remain evidence-only during suspension; `finalize`
  waits for resume and requires the matching acknowledged delivery. Never replay the
  send to finish lifecycle application.
- A configured poller may use `omit_execution(permit, reason, links=...)` for its
  existing omission policy (for example, an unmerged localization PR past cutoff).
  Unlike receipt settlement, omission requires a still-valid owned `POLL` permit;
  suspension or invalidation defers/rejects it without clearing ownership.
- Malformed outcomes and unexpected handler failures propagate without completing the
  step or discarding effect ownership. Rejected transitions do not mutate state;
  `get_step` returns detached nested data. Persist intended evidence explicitly rather
  than modifying a returned alias. Data-only `annotate_step` remains separate.

### Public lifecycle and recovery boundary

Command/delivery adapters use `Orchestrator`, never the private transition kernel
or raw step lifecycle setters. The public lifecycle operations return
`TransitionResult` (`changed`, `message`, and, for `reopen`, `affected`):
Rejected and already-applied operations return unchanged results with explanations;
rejection never modifies evidence through a borrowed state alias.

- `annotate_step(phase, step, *, data=None, links=None, note=None, by=None)` updates
  detached evidence only; it cannot change status or execution ownership.
- `reopen(phase, step, reason="")`, `cancel(reason)`, and `reactivate(reason)` retain
  the existing invalidation and independent suspension rules.
- `retry_effect(phase, step, execution_id, reason, *, confirm_absent=False)` checks
  the exact blocked transactional owner and input metadata before asking the bound
  handler to prove absence, then rechecks its generation before clearing it.
  `supersede_effect(..., confirm_idempotent=False)` applies only to blocked
  `match_current` idempotent owners. Unexpected verification errors propagate.
- `claim_notification_step(id, approved_hash, executor)` atomically binds the
  reservation and ledger claim. `release_notification_step(id, execution_id)`
  releases only that current owner, with matching descriptor/hash/generation and
  explicit `not_sent` evidence. Stale receipts remain transport evidence without
  resetting another execution; `sent` and `uncertain` never release an owner.
  `record_notification_evidence(id)` records confirmed delivery dates/checkpoints,
  not arbitrary release fields.

Owner-reviewed retry/supersede and proven-not-sent release may remove an old owner
while suspended; they do not run/replay a write or enable downstream dispatch.
Notification success still waits for suspension to clear before finalization.
Save failures never grant send permission, and failed first-effect checkpoints
restore only the reserved step. These recovery operations add no persisted tokens;
external approval authorization/receipts use the existing nested execution/data slots.

Malformed execution hashes/modes/recovery/owners, contradictory gate metadata and
malformed nested records produce invariant diagnostics rather than new dispatch.
Status renders a reduced safe diagnostic view for invalid snapshots. Targeted
effect recovery remains usable when unrelated records are corrupt; it never clears
an invalid target owner. Existing legacy unapproved-gate warnings remain warnings.

### Shared scheduling query

`Orchestrator.scheduling(attempted=...)` exposes the pure
`StateProjection.scheduling()` result. Its frozen `SchedulingResult` contains the
frontier, global suspension and status, phase/step readiness, ordered `runnable`
auto/recovery candidates, `action_holds`, and the presentation `focus_hold`.
`current_hold`, `pending_actions`, status reports, and `scout_pending` derive from
this same selection; generic eligibility uses its phase/time/dependency facts.
These are transient queries, not additional persisted fields or write permissions.

- Presentation prioritizes a denied gate, then pending gates, then human actions
  over Scout work. A pending gate or blocked sibling does **not** veto independent
  parallel auto work. A denied frontier gate, cancellation, halt, or unsatisfied
  readiness gate stops dispatch and suppresses pending actions.
- Sequential predecessors and explicit dependencies remain authoritative. Phase
  dates and new-work fire times apply equally to both execution modes, including
  local outcome mocks. An unopened timed parallel step is `scheduled`, not an
  unexplained prerequisite wait. External in-flight work is a wait, not new work.
- Owned effect recovery precedes new auto candidates and respects the frontier,
  phase date, dependencies and global suspension. It may recover before a step's
  new-work fire time, retaining the original execution and declared `frozen` or
  `match_current` recovery contract. It is not a new write reservation. All
  candidates, including recovery, honor the caller's per-drain `attempted` keys.
- External polling and completed-observation refresh retain their distinct intent
  rules: neither reopens new-work eligibility, and each new invocation requires
  current owning-phase, prerequisite, date, and fire-time permission. Neither is inferred
  from `scout_pending`, nor flattened into the automatic candidate list.

## Quick start

Run the one-time setup from the **`release-agent` folder of your `android-complete` clone**,
using PowerShell 7 (`pwsh`):

```powershell
# one-time — from the release-agent folder
cd C:\repos\android-complete\release-agent    # adjust to your clone location
pwsh .\setup\bootstrap.ps1
```

`bootstrap.ps1` runs an **infrastructure preflight** first (`python -m orchestrator.cli infra`),
driven by **`config/requirements.yaml`** (the single source of truth for external
dependencies). It checks each CLI/host dependency and prints an `install:` hint for
anything missing, then **registers any required MCP servers into Scout's config**
(backing the file up first) and tells you to **restart Scout** so they load. Keep
`requirements.yaml` up to date whenever a new dependency (CLI, package, or MCP
server) is introduced.

You can run the preflight any time on its own:

```powershell
python -m orchestrator.cli infra              # check + auto-register MCP servers (restart Scout after)
python -m orchestrator.cli infra --no-register  # report only
```

```powershell
# drive a release (runs are real; keep a mocks.local.yaml for safe testing)
cd release-agent
python -m orchestrator.cli init   --release 2026-07
python -m orchestrator.cli next   --release 2026-07     # runs until the first gate
python -m orchestrator.cli approve --release 2026-07 --comment "flags reviewed"
python -m orchestrator.cli status --release 2026-07
```

Or in Scout: **`/release-agent`**.

## Time anchoring — phases open relative to the Code Complete Date (CCD)

Phases don't fire on demand; they're anchored to the **CCD**. **The CCD is
canonically the 2nd Wednesday of the month.** The orchestrator still reads ADO
pipeline **3038 "Code Complete Calendar Checker"**, but it does **not** silently
adopt the pipeline's `overrideCodeCompleteDate`: if that override is a *different*
in-month date, the tool flags a **conflict** (`ccd_conflict`) and asks the user
which date is real — the default or the pipeline's. `init` computes the default
and reports any conflict.

- **Phase 0 opens at `CCD-7`** (declared as `anchor: "CCD-7"` on the phase in
  `phases.yaml`). Before then the release is **`scheduled`** — the engine runs
  nothing and status shows *"opens `<date>` (in N days)"*. Other phases are
  dependency-driven for now; add an `anchor:` to any phase to time-gate it too.
- **Simulated clock:** every read/advance command takes `--as-of YYYY-MM-DD` so a
  `--as-of` can jump to CCD-7 and prove a phase opens on schedule. Normal runs use today.
  Notification preparation can preview that clock; claim/result/finalize cannot. Send
  authorization always checks the trusted current clock.
- **Resolving a conflict / changing the CCD.** `set-ccd` and `skip-release`
  **write back** to pipeline 3038 (override / `skipRelease`) — real production
  changes, so they're gated: preview first, then re-run with `--confirm` (a
  `--reason` is always required and audited). Pick the default → `set-ccd --default`
  clears the pipeline override so they match; pick the pipeline date → `set-ccd
  --date <that>`. `status` re-reads the pipeline and re-flags any new conflict.

```powershell
python -m orchestrator.cli set-ccd --release 2026-07 --date 2026-07-15 --reason "more bake time"   # preview
python -m orchestrator.cli set-ccd --release 2026-07 --date 2026-07-15 --reason "more bake time" --confirm
python -m orchestrator.cli status  --release 2026-07 --as-of 2026-07-01   # jump the clock
python -m orchestrator.cli done    --release 2026-07 --note "China upload complete"   # clear a reminder hold
```

## Push reminders — daily phase digest (reaching you when Scout is closed)

Everything the engine surfaces is **pull** — you see it when you open Scout. The
**push** layer is a **daily phase status digest** emailed to the release owner:

- **Setup is interactive → no push.** Readiness + establishing the CCD happen in
  Scout, so they're never emailed (unsigned / blocked / halted = silent).
- **First push = a phase opening** (Phase 0 at CCD‑7). Nothing before it.
- **Daily while a phase is open with outstanding work** — once/day, progress +
  what still needs you, until the phase's actions are done; then the next phase's
  digest takes over when it opens (each phase notifies on open).

```powershell
python -m orchestrator.cli tick --release 2026-08 --json   # advance + preview notifications
python -m orchestrator.cli tick --release 2026-08 --as-of 2026-08-06   # preview clock
python -m orchestrator.cli notify --release 2026-08 --json # read-only; no send stamps
```

A **Scout automation** runs **`tick --release <id> --json` hourly**. Its output is only
a preview: `notification prepare --release <id> --source digest`, review, then
`notification claim` with the approved hash/executor. Send only the returned payload
when `permission_to_send:true`, then acknowledge that channel with `notification result`.
Email and Teams deduplicate independently using the owner's day. `notify` is read-only.
`--force` changes cadence, never lifecycle or acknowledged identities. Unknown transport
outcomes require owner review, not automatic retries; there is no exactly-once guarantee.
Every worker discovers saved pending delivery/completion and runs cleanup even on silence.
See [the command contract](skill/reference/commands.md) and
[deployment checklist](skill/reference/starting-and-scheduling.md): updating git does not
update stored Scout prompts. Older release-state schemas are rejected; retain their
records and use the matching original runtime for recovery, never reinitialize them.

**Automation registry (schema v3, distinct from release-state schema).** All seven
workers are canonical data/code in `config/automations.yaml` and `automations.py`.
`plan` returns registry metadata and complete tool-compatible `provider_spec` kwargs.
Write the exact reviewed spec once to a temporary JSON file and pass the same
`--spec-file` to `prepare`, `reconcile-create`, and the owning `create-result`.
Write fresh exhaustive provider observations to a second file and pass
`--observed-file`; inline JSON remains available only for small/manual inputs because
large generated prompts are fragile under Windows command-line quoting. Delete both
temporary files only after the owning result is durably recorded. The existing
`intent_hash` binds metadata plus **all** provider kwargs; no prompt, spec payload,
or raw receipt is persisted. Existing evidence strings contain only SHA-256 digests.
Old registry schemas are rejected, never rehashed or stamped automatically.

Identical prepare is a byte-preserving replay in every state. Changed intents
require safe retirement first; active schedule/prompt updates are not inferred.
Fresh exhaustive provider list **and detail** reads must be losslessly normalized
to `{observed_at:<UTC>,complete:true,automations:[{id,spec}]}`. Reads expire after
five minutes and must postdate preparation/the latest operation. Match by recorded
ID **or** name; adopt only an exact complete-spec match without unresolved ownership.
Unknown settings, partial observations, mismatches, missing or duplicate providers
never permit recreation. Only an owning claim permits creation; observations never
take over creating/uncertain/deleting/delete_uncertain. Late owning receipts remain valid.

**Per-release** automations
(`--release <id>`, the default) live in `.release-runs/<id>/_automations.json` —
co-located with that release's state. Cleanup similarly requires `claim-delete`, the
provider deletion, and `delete-result`; direct registration/deregistration is disabled.
Under the registry lock, release-level deletion waits for nonmanual child intents
(including ID-less ones) and unresolved siblings. New creation/preparation is blocked
while release-level deletion is outstanding. Retire helpers before `push-reminders`;
stop the cleanup loop on barriers/errors/uncertainty. An owner can confirm absence only
after proving the original runner **and** provider operation finished; live claims must
first record their owning result. `abandon-prepared` removes only ID-less, never-created
or verified-absent intents in terminal releases, with fresh zero observations and
explicit owner evidence/confirmation. No tombstones or extra registry fields are added.
`sync` is a drift report, never permission to update in place: review delete/recreate.
Push reminders
are per-release too. A `--shared` scope (stored
machine-wide at `.release-runs/_automations.json`) exists for the rare automation meant
to outlive every release. All registry files share one OS-held `.automations.lock`.
Shared/manual workers are exempt from automatic retirement. CCD one-shots convert
the owner-local CCD/fire instant to the scheduler host IANA timezone, retaining
`oneShot:true`; missing zones and past/ambiguous targets fail instead of scheduling
next year. Daily status polls hourly, guarded by owner-local business day and 17:00
(first eligible tick at/after 17:00); LA localization/bug-bash windows are unchanged.

## Two kinds of human step

- **Gate** (`kind: approval_gate`) — a *decision*: the conductor holds for a human. Local
  gates use `approve`/`deny`; external gates require their checked `approval_command`
  preview/hash/reviewer protocol, never plain `approve`.
- **Reminder/attestation** (`kind: human_action` or `attestation`) — a *to-do*: the conductor holds
  ("ACTION NEEDED"), you go do it, then `done` it. Not a decision — just done / not-yet.

## Event log (for analysis & improvement)

Every action is recorded to an append-only JSONL event log so we can improve the
process across engineers and months. The highest-value signal is the **decision
driver** — the reason attached to each gate approve/deny/decline.

- Per-release trace: `.release-runs/<id>/events.jsonl` (one log per release; there is no machine-wide aggregate).

```powershell
python -m orchestrator.cli log --release 2026-07              # this release's trace
python -m orchestrator.cli log --release 2026-07 --analyze    # rolled-up summary
```

Events captured include: `release_started`, `readiness_verified/signed/declined`,
`step_ran`, `gate_hold` + `gate_approved`/`gate_denied` (with `driver`),
`reminder_hold`/`reminder_done`, `scheduled_hold`, `ccd_changed`,
`release_skip_set`/`release_skip_cleared`, `step_skipped`/`step_reopened`,
`release_halted`/`release_resumed`, `release_complete`, plus interaction events
(what Scout showed / what the user chose). Logging never breaks the flow (best-effort).

> The log lives under the gitignored `.release-runs/`, so it's per-machine. Shipping
> logs to a shared store (Kusto/ADO/wiki) for cross-engineer analysis is a future step.

## Tests

Run from `release-agent/`. **Directory/default runs now select the daily core suite**,
capped at 1,086 cases (half the previous 2,172-case suite). The reviewed selection in
`tests/_suite.py` keeps engine/lifecycle, ownership and write fences, approvals,
workflow revisions, handler/parameter contracts, scheduling, and UI result ownership,
plus named phase smoke cases and one complete release replay.

Broad phase/provider matrices, renderer variants, overlapping legacy flows, and
real-Git integration cases remain in the **extended** suite; they are not deleted.
This deliberately trades exhaustive per-change coverage for a smaller daily run.
No random sampling or every-Nth-case filtering is used. New unclassified tests enter
the core by default, and a regression guard enforces its size budget and critical
selectors. Output always identifies the selected suite and deferred case count.

**Per-change rule: validate every completed change within 15 minutes total.**
Use the core suite and relevant targeted regressions, not repeated full-suite passes.
If validation exceeds that budget, profile the slow cases and reduce/consolidate
overlapping coverage in the daily set while retaining critical safety regressions.
Do not increase/disable the timeout or split routine validation into repeated
15-minute runs to evade the budget. Moving more cases to extended coverage is an
explicit, reviewed change; a timed-out or incomplete run is never reported as passing.

| Scope | Arguments after `python -m pytest` |
| --- | --- |
| Daily default | `-q tests` (also the default with no path or a directory path) |
| Affected file or case | `-q tests\test_finalize.py` or a `file.py::test_name` selector |
| Extended regressions only | `-vv tests --validation-suite=extended` |
| Complete suite, only when warranted | `-vv tests --validation-suite=full` |
| Real Git only | `-vv tests -m git_integration` |

Explicit file/node, `-k`, and `-m` selectors run **all matching requested cases**,
including extended cases, so a targeted regression is never silently filtered out.
`--validation-suite=core` can explicitly combine the core with additional filters.
For Git planning/transport changes, run the affected Git cases; for phase/provider
changes, run the affected module even if much of it is extended. Test provider calls
are blocked unless explicitly replaced with fakes.

```powershell
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("release-agent-tests-" + [guid]::NewGuid())
$oldTemp, $oldTmp, $oldBytecode = $env:TEMP, $env:TMP, $env:PYTHONDONTWRITEBYTECODE
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
    $env:TEMP = $env:TMP = $tempRoot
    $env:PYTHONDONTWRITEBYTECODE = "1"
    python -m pytest -q -p no:cacheprovider --basetemp "$tempRoot\pytest" --durations=10 tests
    if ($LASTEXITCODE -ne 0) { throw "Release-agent tests failed ($LASTEXITCODE)" }
} finally {
    $env:TEMP, $env:TMP, $env:PYTHONDONTWRITEBYTECODE = $oldTemp, $oldTmp, $oldBytecode
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
```

This confines pytest fixtures and `TemporaryDirectory` state/locks to one cleaned
directory without touching `.release-runs`. Replace `tests` with relevant file/node
selectors for small changes. Add `--validation-suite=full` only for a deliberate
complete run; `tests` alone no longer means the full regression suite. Do not invoke
live release commands as tests. Real-Git cases build repositories and exercise merges,
pushes and write-fence rechecks; their many Windows subprocess launches are expensive.
Use `-vv` for extended/full runs to show the current case instead of buffered dots.
Do not repeatedly launch full suites for small edits or overlap validation runs.
Finish the edits and review first; use targeted cases for review fixes instead of
waiting for an obsolete full run and then starting another. Reserve one complete
run for changes whose scope genuinely requires it. When a run is backgrounded,
retain its process/session ID and retrieve its result; an incomplete log without
a running process is not a reason to keep waiting.
Coordinator hash/payload/ownership tests use in-memory Git ports and reject actual
subprocesses; real tree construction and a full coordinator-plan case retain Git coverage.
Ordinary tests use one captured immutable runtime identity per pytest worker so unit
coverage does not repeatedly hash the repository. Tests of runtime drift and workflow
adoption use the `real_revision` marker and retain content-based filesystem validation.
Each test has a 180-second fail-fast guard that prints all Python thread stacks and
terminates pytest with exit code 124 instead of hanging indefinitely. Override it
with `--test-timeout=<seconds>` or use `@pytest.mark.timeout(<seconds>)` for an
intentionally longer test; `0` disables that guard. There is also a **15-minute total
suite budget**, including collection, because thousands of tests can each stay below
180 seconds while adding up to hours. `--suite-timeout=<seconds>` explicitly overrides
that budget (`0` disables it); a timeout reports the active test/collection and thread
stacks and is a failure, never a pass or a reason to silently skip remaining cases.
Overrides are for explicitly requested diagnostic runs, not ordinary per-change
validation. If the default run times out, reduce its cost rather than its protection.

## Design constraints honored (from §7.1 of the stabilization plan)
1. Real-by-default with a personal `mocks.local.yaml` (skip/redirect/inject per step) is the test method — never blast the real DL from a test (use a `send_to` redirect).
2. Run-state schema defined once, upfront (X5), shared by all agents.
3. Sequence by risk/value — agents are independent plug-ins on the backbone.
4. Manual overrides are first-class (approve/deny gates; activate conditional phases).
5. Conductor is stateless; minimize persisted state, derive the rest.
