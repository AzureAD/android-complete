# Release Agent

An Android release assistant with automatic guided progression by default. The
Python engine owns classification, facts, business dependencies, provider receipts
and release gates; the skill executes eligible work and collects genuine decisions.

**New standard:** state schema **5**, execution contract **4**. Older runs are not
migrated or rewritten. Use `inspect-state` for read-only version diagnostics and
retain old bundles separately. Never change a version stamp or reset a live run
to make it load.

## Install or refresh the Scout skill

From this directory, refresh an existing installation without rerunning machine
setup:

```powershell
pwsh .\setup\bootstrap.ps1 -SkillOnly
```

This overwrites the installed instructions and reference resources from **this
checkout**, verifies every copied file with SHA-256, and prints the source and
destination. It also creates or updates Scout's local skill registry record,
including the instruction body. Existing IDs, creation times, disabled settings
and unrelated skill records are preserved; registry changes are atomic and retain
the previous file as a backup. Files alone no longer count as successful registration.
The default destination is
`%USERPROFILE%\.scout\m-skills\release-agent`; use `-ScoutRoot` for a different
existing profile or `-ScoutSkillsDir` for its skills directory.

Reopen **Extensions > Skills** in Scout and verify that `release-agent` is listed
and enabled; do not infer recognition from a copied folder alone. Start a **new
conversation** afterward, since copying files does not replace instructions
already loaded into an ongoing conversation. `-SkillOnly`
does not check/install Python or Agency, register MCP servers, change connections,
or restart the application.

The regular `pwsh .\setup\bootstrap.ps1` also refreshes the skill **first**, then
continues full infrastructure setup. A later prerequisite failure still returns
a nonzero exit code, but no longer prevents the skill copy. `-SkipSkillInstall`
opts out and cannot be combined with `-SkillOnly`. Disabled skills or unreadable
registration metadata are reported explicitly, not silently enabled/repaired.

## Automatic guided progression (default)

Run commands from this directory. `paths --json` resolves installation/run paths.

```powershell
python -m orchestrator.cli init --release 2026-12 --owner-email owner@example.test --timezone America/Los_Angeles --json
python -m orchestrator.cli status --release 2026-12
python -m orchestrator.cli status --release 2026-12 --details
python -m orchestrator.cli task --release 2026-12 --phase preflight --step cg
```

While the agent is present, it executes all currently eligible agent-owned steps,
including host-assisted steps, and continues across phases without asking you to
say "next" after each result. A pending human step does not stop independent work.
It pauses only for genuine input/approval/repair, a scheduled or external wait,
or completion. **After exhausting eligible work, it always prints the existing
full status table before a user question or wait handoff.**

Starting/resuming a release, rerunning a step, recording a fact or completing an
owner action resumes that guided flow. Status-only, explanation/setup-only and
explicit "only this step" requests remain scoped.

The low-level CLI commands below stay atomic so tools can safely consume one
actual result at a time. The conversation adapter continues with `next --json`
and `work_dispatch`. Task/result JSON exposes the automatic-progression policy,
continuation queue and handoff cues; a settled report contains `display_markdown`
for the full table. Tool output alone is not proof the owner saw it.

The pipeline runs as a single **unified state-driven hybrid model**: interactive
sessions and optional background accelerators (like the daily 7:00 AM
`morning-step-drain`) share one `.state.lock`-serialized, claim-fenced state machine.
Disabling or deleting background automations never blocks or breaks progression.
Initialization records context, not proof a release executed. Status remains local/read-only.

| Intent | Command | Scope |
|---|---|---|
| Inspect | `status [--details] [--json]` | Read the local brief or full checklist |
| Execute one task | `task --phase <p> --step <s> [--retry]` | The selected operation, not sibling work |
| Start a fresh attempt | `rerun --phase <p> --step <s> --reason "<why>" [--param key=value]` | New validated inputs; original outputs/receipts retained |
| Complete outside the tool | `complete-manually --phase <p> --step <s> --note "<what was done>"` | Owner-attested completion of any non-gate task |
| Waive a task | `skip --phase <p> --step <s> --reason "<why>" --json` | Any non-gate task, including blocked/out-of-order work |
| Inspect attempts | `task-history --phase <p> --step <s> [--details]` | Read-only prior inputs, results, resources and receipts |
| Record a fact | `record-fact --phase <p> --step <s> --note "<owner statement>" --json` | Persist the confirmation without advancing |
| Continue release | `next --json` | Drain eligible fresh work to a real boundary |
| Retry after repair | `next --retry` or the task's checked continuation | Reconcile once; never blindly replay an uncertain write |
| Diagnose a version | `inspect-state --release <id>` | Inspect metadata without loading executable state |
| Check installation | `onboarding --json` | Explicit setup diagnostics, not release admission |

Use the subcommand's `--help` for actual parameters. Read-only previews do not
authorize sends, uploads, approval submissions, or other provider mutations.

## Small outcomes, not a recovery ceremony

Tasks expose `done`, `needs_input`, `waiting`, or `failed`. Host-assisted results
also return the exact `kind`, tool, payload, and owning follow-up.

- **Done:** state the result/link and verification provenance.
- **Needs input:** identify the specific missing decision, consent, or external repair.
- **Waiting:** identify what is awaited and when/how it will be checked.
- **Failed:** explain the cause, verified partial results, and supported recovery.

Before an owner prompt or wait, use `status --details --handoff --json` and print
its full `display_markdown` table as normal Markdown. If automatic work remains,
continue it first. Exact `--hold-step` reasons identify host operations that need
actual input/consent/recovery while allowing other independent work to finish.
A status-only request does not authorize an approval or drain the work queue.

`next` defaults to pending-only work. It does not automatically retry failed reads;
interrupted running/in-flight engine effects use their owning reconciliation protocol
once, while blocked effects need an explicit repair/retry. The skill finishes
independent eligible work, then stops at an unchanged failure/no-progress boundary
with the status table rather than looping until the owner intervenes.
`task --retry` can explicitly recheck a completed read-only operation. Identical
business facts preserve downstream work and frozen evidence; a real change
invalidates only declared consumers. This never repeats completed external effects.

## Owner controls at any workflow point

Every configured step supports an explicit new attempt. Every **non-gate** step
can also be skipped or recorded as manually completed, including blocked tasks
and tasks outside the current phase. The atomic operation does not drain siblings;
the default guided conversation then continues newly eligible work, including
affected consumers after a rerun, unless you explicitly requested only that step.
Approval gates still require their genuine decision or provider approval receipt;
skip/manual completion cannot substitute for approval.

```powershell
python -m orchestrator.cli step-info --phase bug_bash --step clone_plans_broker --json
python -m orchestrator.cli rerun --release 2026-12 --phase bug_bash --step clone_plans_broker --reason "Build a separately named plan" --param "name=December Broker - second pass"
python -m orchestrator.cli complete-manually --release 2026-12 --phase monitor --step health_report --note "Reviewed this release's crash report manually" --evidence https://example.test/release-review/123
python -m orchestrator.cli skip --release 2026-12 --phase partner --step samsung --reason "Not applicable to this release" --json
python -m orchestrator.cli task-history --release 2026-12 --phase bug_bash --step clone_plans_broker
```

`task --rerun` is equivalent to `rerun`. `step-info --json` lists the selected
step's typed public inputs; steps without configurable inputs accept no `--param`.
Effectful tasks accept **prepare** inputs, then freeze them before executing.
Omitted inputs reuse the previous attempt's selected values; provide only changes.
Test mocks such as `clone_id`, `fail`, or synthetic provider responses are not
public inputs. Broker `name` names the **test plan**, not its individual suites.
Authenticator creation exposes `name` for its release parent **suite**, plus `layout`.

A new attempt retains the previous state, frozen inputs/receipts, relevant resource
binding, pipeline snapshots and invalidated dependent results. It receives fresh
ownership and any required new write/send review. A differently named Broker plan
starts with a fresh binding; the original plan is neither renamed nor deleted.
`--retry` instead reconciles/rechecks the **same** attempt and cannot change its
frozen write inputs.

Explicit reruns ignore unrelated phase/frontier timing, not required readiness,
business prerequisites, halt/cancellation or genuine approvals. Conditional work
must be activated first. Active downstream effects/approvals must be settled before
their source can change.

Owner controls default `--confirmed-by` to the configured release owner and require
an explicit reason/note. If the previous runner or notification remains owned or
uncertain, obtain the owner's stopped-runner confirmation and add
`--prior-writer-stopped`. This archives uncertainty; it does **not** certify that
the old write succeeded or failed. Late results cannot complete the new attempt.
`reopen --force-owner` and `step-action --owner-force-rerun` use this same audited
path, not an unchecked ownership reset.

Manual completion is `owner_attested`; a skip is `waived`. Neither is a provider
receipt. Optional `--result-file` accepts `{"outputs": {...}, "links": [...]}` as
owner context, not executable state or machine evidence. When later automation
needs a provider-bound plan/event/build identity, use the owning bind/adoption or
readback command; do not promote raw owner outputs into verified evidence.
The conversation adapter gathers these details rather than asking owners to
construct JSON or operate engine state.

## Record facts when they arrive

```powershell
python -m orchestrator.cli record-fact --release 2026-12 --phase bug_bash --step native_auth_signoff --note "Owner received the release-specific Native Auth sign-off" --json
```

A sign-off can be recorded while unrelated UI review remains pending. It is bound
to the relevant release, plan, engineer, and notification evidence. It does not
complete UI review or approve bug bash. If source evidence is missing, the engine
retains an **unverified owner claim** and states what still needs verification.
It does not pretend a claim is verified completion.

`done` is an alias for fact recording. `done`, `approve`, and `skip` record their
own operation; they do not automatically execute sibling tasks.

## Accept owner-completed results

Automatic receipts and owner-selected results are separate paths. A selected
artifact need not have the original automatic filename/bytes; its provenance and
applicability remain explicit.

### Existing RC report

```powershell
python -m orchestrator.cli publish-rc-report --release 2026-12 --adopt-file <report-observations.json> --confirmed-by owner@example.test --adoption-reason "Use the report I reviewed for this release and RC"
```

The agent prepares `release`, `rc`, actual `sha256`/`size`, `item_url`, and exact
fresh resolve/download observations. The command verifies the selected provider
bytes and configured destination. Applicability to the captured release/RC is
owner-attested, not falsely certified against the original uploader's payload.

The report's link notification and RC quality gate remain separate. Adoption
uploads nothing and approves no gate. An owned prior uploader additionally requires
`--prior-writer-stopped`; its execution and uncertainty remain in `prior_results`.
The original handoff is never reused as authorization for another upload.

### Existing Bug Bash invitation

```powershell
python -m orchestrator.cli adopt-bugbash-invite --release 2026-12 --result-file <event-observations.json> --confirmed-by owner@example.test --reason "Use my existing release invitation"
```

The result identifies the release, CCD, exact event ID, and owner-bound profile/event
observations. The command verifies organizer, complete typed attendees, times, and
the Teams meeting URL. Chat binding consumes that selected event without fabricating
an engine calendar-send receipt. No new invitation or message is sent.

Do not ask owners to construct observation JSON, operate hashes, or reset ownership.
The conversation adapter gathers actual tool results and asks only for the real choice
or stopped-writer confirmation.

## Preserve partial successes

`distribute-tests` retains its approved operation scope and OOF/OCE selections in
the owning verified-progress record. On restart it:

1. Reconciles attempted provider operations against exact release points.
2. Preserves verified applied changes.
3. Validates business inputs and original target identities.
4. Executes only still-needed approved corrections.
5. Rechecks receipts and live validity before completion.

Unchanged remaining work can reuse the original approval. A material source/selection
change needs explicit review; an ambiguous outcome remains held, not replayed.
If the owner completed all remaining original work outside the tool, the same
command accepts result-only `--manual-completion <evidence-url> --confirmed-by
<owner> --recovery-reason "<stopped-writer and completion confirmation>"`, without
provider reads. Uncertain attempts remain in history and the result is owner-attested,
not machine-verified.
The regression suite exercises interruption after 13 of 45 changes, including lost
readback, and proves the remaining 32 are applied without repeating those 13.

Integration/OneAuth writers retain their existing checked compound continuation and
owner-confirmed manual recovery options. Different adapters have different proof
requirements. The generic owner controls above are separate from provider-verified
continuation and never turn an uncertain operation into a verified success.

## Invalidate business consumers, not positional successors

[The workflow](config/phases.yaml) declares `depends_on` using a local ID or
qualified `phase.step`. These are business prerequisites and invalidation edges.
Sequential phase order remains scheduling order, not an implicit data dependency.

Reopening an assignment does not erase unrelated invitations, sign-offs, or later
work. Communications bind stable plan/RC identities, not mutable UI counts.
Changing actual RC/plan identity invalidates its real consumers.
Gate decisions are retained with invalidation metadata rather than deleted.

## Mandatory readiness & step-level tool validation

Every item in [readiness](config/readiness.yaml) and every step in [phases](config/phases.yaml)
is mandatory (`required: false` optional steps are deprecated and removed). Each step declares an
explicit `depends_on` list (`[]` = no prerequisites, runs immediately) and `required_tools`.
If a step fails because a required tool or connector is missing or misconfigured, only that
specific step fails with its explicit reason while all other independent and satisfied-dependency
steps continue executing to completion.

`sign` records only the explicitly named human confirmations; it does not probe
integrations. Use `verify` for requested automatic verification. A successful setup
or read is never blanket permission to write.

Physical readiness, on-call policy, relevant source identity, actual scheduling,
halt/cancellation, and human release gates remain enforced. No generic "continue"
message approves quality, publication, Remove RC Tags, or Beta Play Store start.

## Optional 7:00 AM background accelerator (`morning-step-drain`)

```powershell
python -m orchestrator.cli automation plan --release 2026-12 --json
python -m orchestrator.cli automation obligations --release 2026-12 --json
```

Automations in `config/automations.yaml` act purely as non-blocking convenience
accelerators rather than hard dependencies:
- **`morning-step-drain`** runs once daily at **7:00 AM** (`every day at 7am`) to
  execute and drain all eligible non-blocking steps (`next --deliver`), reconcile
  in-flight async pipelines once, deliver any due morning owner digest (`tick --deliver`),
  and park cleanly at the next human-in-the-loop gate before the workday starts.
- Disabling or deleting any automation in Scout never blocks readiness, `status`,
  or interactive progression, and background runs never auto-resurrect deleted workers.
- Interactive sessions and `--scheduled` background runs serialize through
  `<runs-root>/<release>/.state.lock` and durable pre-IO claims (`D.claim` /
  `write_review.authorize`), preventing race conditions or duplicate execution.

## Connector routing and compact state

Structured WorkIQ fetch accepts actual supported exposed tool names or a verified
existing native launcher. The adapter preserves wrapper argv, `shell=False`, literal
URLs, exact owner/resource binding, JSON validation, and explicit denial.

`workiq-read --route-file <temporary-route.json>` reuses a fresh discovery descriptor
across pagination calls. Launcher changes or expiration require rediscovery, never
raw-binary bypass, synthesized directory evidence, or alternate identity after denial.

State contains compact facts/receipts. Immutable RC evidence lives under
`evidence\rc<N>`; bulky step, recovery, execution, resource, and delivery bodies
use content-addressed `evidence\records` files. Loading verifies hashes and identity
before hydrating original values. Missing/tampered evidence fails explicitly, with
no automatic provider refetch. Keep the whole release bundle together.

## Honest implementation coverage

Provider-backed operations exist through rollout-start work. Configured dummy
operations are **manual-only**, not automatic `Done` results. They stop with an
explicit unsupported-operation message and accept a specific owner-attested
`record-fact` completion when work was actually performed.

Progress distinguishes verified, owner-attested, waived, and manual-only coverage.
Checklist traversal is not proof of rollout, partner-store delivery, or hotfix execution.

## Extending and validating

- Add a co-located handler and [workflow entry](config/phases.yaml). Reuse typed
  outcomes, evidence authority, checked write ports, and explicit dependencies.
- `manual_result: true` enables only a domain-verified adoption path; it is not a
  generic completion bypass.
- Keep secrets/private observations outside source control. Use configured targets,
  not hardcoded recipients or identities.
- Update the [skill](skill/SKILL.md) and its focused references with public behavior.
  Older audit records in [docs](docs) are historical, not the current interaction contract.

```powershell
python -m pytest tests\test_phase3_human_contracts.py tests\test_rc_report_delivery.py tests\test_ado_write_review.py tests\test_structured_fetch.py -q
python -m pytest tests --validation-suite=core -q
python -m pytest tests --validation-suite=full -q
```

The existing harness blocks real provider/network calls. Use synthetic fixtures,
not the live release, for interruption, adoption, and replay tests. Setup/install
remains an explicit separate task through the existing bootstrap.
