# Release Orchestrator (`/release-agent`)

The **conductor backbone** for the Android monthly release (ADO items **X4** + **X5**).
It drives the whole release as a state-aware smart checklist: it knows the phases,
runs each step's agent, and **holds at gates** for the release engineer to decide.

> **Build status.** **Phase 0 (pre-flight) has real, tested agents** (early-notice,
> flight/string reminders, BREAKING-OneAuth detection, CG alerts, cron verify, wiki
> payload); the later phases are still **stubs** (mock actions) and get filled in one
> at a time (see the Release-Stabilization roadmap). Building on this shared backbone —
> not 50 one-off scripts — is what makes the "agent carries the knowledge" model real.

## Architecture (thin skill over a deterministic engine)

```
 you ──/release-agent──▶  SKILL (conversation layer)  ──shell──▶  ENGINE (Python, deterministic)
                          presents gate briefs,                    state machine + dispatch + run-state
                          relays your approve/deny                 the BRAIN — fully unit-tested
```

- **Engine = the brain.** Decides what's next, runs stubbed agents, holds at gates, persists run-state. No LLM — unit-tested and replayable.
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
│  ├─ engine.py                    the conductor: state machine + dispatch (resolves steps via steps.get_step) + gates + status_report
│  ├─ readiness.py                 ReadinessGate: entry-gate logic (verify/sign/decline) → structured data
│  ├─ schedule.py                  CCD math (2nd Wednesday, override, phase anchors) — pure, no IO
│  ├─ mocks.py                     local test overlay loader (mocks.local.yaml): outcome / readiness / stepresult
│  ├─ knowledge.py                 step knowledge resolver (config/knowledge.yaml + module KNOWLEDGE overlay)
│  ├─ infra.py                     infra preflight: check CLIs + register/verify MCP servers into Scout config
│  ├─ render.py                    presentation only: structured data → text/markdown (swap for other UIs)
│  ├─ state.py                     Release State Record / run-state (X5); StepState carries note + links
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
│  ├─ lib/                         shared step helpers (context, templating, agent adapter, mock-input ctx)
│  └─ preflight/                   Phase-0 step modules — each declares ID/KIND/build + optional MOCKABLE/KNOWLEDGE/CONFIG
│     ├─ notice.py  flight_reminder.py  lockdown.py   (scout)
│     ├─ breaking.py  cg.py  cron.py  wiki.py          (agent — run in-engine)
│     └─ confirm_reminders.py  vitals.py               (attest)
├─ phases/
│  ├─ stub_runner.py               generic runner for steps that have no module yet (later phases)
│  └─ readiness_verifiers.py       auto verifiers for the entry gate (pass/fail)
├─ tools/checks.py                 real IO (az / http), isolated
├─ skill/SKILL.md                  the /release-agent Scout skill
├─ mocks.local.example.yaml        template → copy to mocks.local.yaml (gitignored) for local testing
├─ setup/bootstrap.ps1             one-time setup (infra preflight, installs skill)
└─ tests/test_engine.py            unit + full flow-replay + structural guardrail tests

.release-runs/<YYYY-MM>/           GENERATED, gitignored (per-release working state)
├─ release-state.json              the per-release metadata + run-state (owner, CCD, steps, gates, …)
├─ events.jsonl                    the per-release event/interaction log
└─ _automations.json               registry of THIS release's provisioned Scout automations (owned by the release; removed at close)
.release-runs/_automations.json    GENERATED, gitignored — registry of SHARED (machine-wide) automations only
```

## Adding a step (the modular contract)

A step is **one self-contained module**; everything else is derived, so there are no
hand-maintained registries to forget. Adding a Phase-0 step touches **2 files**:

1. **`steps/preflight/<step>.py`** — the step. Declare `ID`, `KIND` (`agent`|`scout`|
   `attest`), `build(state) -> Outcome`, and optionally `MOCKABLE` (mock knobs),
   `KNOWLEDGE` (help), `CONFIG` (the step's config). Agent steps add `run = legacy_run(build)`.
   It is **auto-discovered** — no registry edit.
2. **`config/phases.yaml`** — one line placing the step in the flow (id, name, order,
   deps, gate/attest/source flags).

That's it. Mocking works automatically (`outcome`/knobs); `step-info` shows its knowledge;
`step-action`/`mock-spec` find it. The **`test_step_modules_and_config_stay_in_sync`**
guardrail fails loudly if a module and `phases.yaml` drift, so nothing silently breaks.

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

1. **Logic** (`engine.py`, `readiness.py`, `schedule.py`, `state.py`) — pure, deterministic, returns **structured data**. No formatting, no IO.
2. **Presentation** (`render.py`) — pure functions: structured data → text/markdown. A different interface (web UI, TUI) swaps this layer and reuses everything else.
3. **Interface** (`cli.py` + `cli_common.py` + `commands/` + `skill/SKILL.md`) — the CLI is a thin assembler: `cli.py` builds the parser from the self-registering modules in `commands/` (one per domain), and shared plumbing lives in `cli_common.py`. Adding a command is a localized change to one module.

IO lives in `tools/` and `phases/` (pluggable). Config is data in `config/`.


## Run-state: two kinds (the X5 idea)

- **Derived** — recomputed from systems of record (ADO/Git/Play Console/ADX). Never stored ⇒ never stale. *(reconcilers are stubbed for now.)*
- **Persisted** — decisions/intent, step completion, pending human actions. Stored in `release-state.json`.

The conductor is **stateless**: on each invocation it loads the record, (later) reconciles against live systems, decides, acts, writes back. That's what lets a release resume across days/sessions.

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
update stored Scout prompts, and legacy state is not automatically migrated.

**Automation registry.** Every automation the orchestrator provisions is recorded via
`cli automation register` so it can be torn down cleanly. **Per-release** automations
(`--release <id>`, the default) live in `.release-runs/<id>/_automations.json` —
co-located with that release's state so ownership is explicit and they're removed with
the release folder at close (`automation list --release <id>` → delete each →
`automation deregister`). Push reminders are per-release too. A `--shared` scope (stored
machine-wide at `.release-runs/_automations.json`) exists for the rare automation meant
to outlive every release.

## Two kinds of human step

- **Gate** (`gate: true`) — a *decision*: the conductor holds and you `approve`/`deny`.
- **Reminder** (`owner: human`, no gate) — a *to-do*: the conductor holds
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

```powershell
cd release-agent
python tests/test_engine.py        # unit + full flow replay + readiness + eventlog
```

## Design constraints honored (from §7.1 of the stabilization plan)
1. Real-by-default with a personal `mocks.local.yaml` (skip/redirect/inject per step) is the test method — never blast the real DL from a test (use a `send_to` redirect).
2. Run-state schema defined once, upfront (X5), shared by all agents.
3. Sequence by risk/value — agents are independent plug-ins on the backbone.
4. Manual overrides are first-class (approve/deny gates; activate conditional phases).
5. Conductor is stateless; minimize persisted state, derive the rest.
