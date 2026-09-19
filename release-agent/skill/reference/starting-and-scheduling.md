# Reference — Starting a release, CCD scheduling & push reminders

_Loaded on demand. Covers `init`, the push-reminder automation, CCD anchoring/conflicts, and the daily digest._

## Starting a release (don't make the user type a date format)

The release id is just `YYYY-MM`. **You compute it — never ask the user to type the format.** Work out the current month from today (e.g. today 2026-07 → `2026-07`).

When no release is active (or the user says "start a release"), call **`m_ask_user`** with clickable options:
- **"Current month (`<YYYY-MM>`)"** ← recommended
- **"A different month"**

Pick current → `init --release <id>` immediately. Pick different → follow-up `m_ask_user` free-text (hint: "e.g. next month, or 2026-08"); accept natural answers ("this month", "August") and do the date math yourself. Runs are real — for test runs the engineer keeps a `mocks.local.yaml` (skip/redirect/inject per step; see `mock-spec`).

`init` records the **release owner** (the engineer running it) from the signed-in `az` user; reminders email that address. Pass `--owner-email`/`--owner-name` for a richer profile (e.g. from `workiq_get_my_profile`), or change later with `set-owner`. Never hardcode a recipient.

**The release NAME (ship month) is confirmed UP FRONT, in the single start prompt — not again after `init`.** A release is named for the month it **ships**, which is the code-complete month **+ 1** — a release whose CCD is in September is the **"October" release**. The start prompt (see SKILL.md → "start a release") already shows the user the ship-month name **and** the CCD date together (from `preview-release`) and gets their confirmation, so by the time you call `init` the name is settled. `init` stores it as `target_month` (e.g. `2026-10`) and prints it ("Release name: October 2026 release") — **do NOT ask a second time.** This name is what every doc/comms step uses (bug-bash invite, announcements, the payload wiki page). If the user later wants a *non-standard* display name (rare — the ship-month default is almost always right), override it with `set-target-month --release <id> --month <YYYY-MM>` (display-only — it does NOT touch `release_id`, the CCD, branches, or scheduling).

## Ensure push reminders exist (per release — provisioned at start, torn down at close)

### Deployment checklist (owner-controlled, never an automatic migration)
1. Stop old runners. Review interrupted sends and existing legacy checkpoints with the
   owner; incomplete records are not permission to resend. Do not import/infer receipts.
2. Install matching source and skill. A git pull does NOT update stored Scout prompts.
   Schema-v3 releases are pinned to a workflow revision. On mismatch, stop dispatch
   and use `workflow-adopt --release <id> --json` for an owner review. Show the exact
   old/new revision plus `invalidation.summary`, every completed/blocked step that will
   reset, removed gate decisions/offers, and blockers before asking. A status request or
   generic approval of a “new version” is not consent to an undisclosed reset. Confirm the
   exact hash with `--approve-hash`, `--by`, and `--reason`. Restore the pinned runtime first
   if an execution, resource creation, delivery completion or automation claim needs
   recovery. Never migrate old schemas or overwrite a release to bypass these checks.
3. With the owner's explicit deployment authorization, replace stored worker prompts
   deliberately using this contract and the current `automation plan`. Do not silently
   migrate existing registrations or settings; preserve deliberate shared/manual workers.
4. All senders must use ONE authoritative state directory. Copies, including production
   snapshots used for investigation, are read-only and must not become additional senders.
5. Never delete `.state.lock` to break a live lock, or steal a claim by age. Verify the
   original runner has stopped before owner-reviewed recovery.
6. Claims/results/finalization use trusted current time. Re-prepare and review a new hash
   for changed never-claimed work (including an expired invitation). Never refresh an
   ever-claimed payload automatically, even after a known-not-sent result. Investigate
   suppressed source-changed completion without re-sending.

For every automation below, use this **recoverable provisioning protocol**:
1. Obtain the canonical `provider_spec` and `registration` from `automation plan --json`.
   All seven workers, including push/daily email, are defined there; do not reconstruct
   prompts from prose. A custom worker needs an explicit complete spec. Persist its
   Write the exact `provider_spec` once to a fresh temporary JSON file outside the
   release run directory, then persist its hash-bound identity with `automation prepare --release <id>
   --slug <slug> --name "<name>" --schedule "<schedule>" --cleanup-when "<rule>"
   --purpose "<purpose>" [--step <phase.step> ...] --spec-file <temporary-spec.json> --json`.
   Use inline `--spec-json` only for small manual inputs; large generated prompts are not
   safe to quote on Windows. Never write a spec/prompt copy into release state, journal,
   registry, or receipts.
2. Call `m_list_automations` and obtain full details with `m_get_automation` as needed.
   Losslessly normalize ALL rows to `{observed_at:"<UTC read time>",complete:true,
   automations:[{id:"<id>",spec:{<complete tool-compatible kwargs>}}]}`.
   Write that envelope to a second temporary JSON file. Pass both files to
   `automation reconcile-create --release <id> --slug <slug>
   --spec-file <SAME temporary-spec.json> --observed-file <temporary-observations.json>
   --claim --executor <session> --json`.
   Reads must be exhaustive, at most five minutes old, and after the latest operation.
   Never guess defaults or omit unmatched workers when asserting a complete list.
3. An exact complete-spec match is adoptable only without unresolved ownership.
   Recorded ID OR reviewed name matches prevent renamed workers appearing absent.
   Partial, mismatched, missing, duplicate, creating/uncertain/deleting/delete_uncertain
   observations grant no create permission. Call `m_create_automation` only when
   `permission_to_create:true`, passing **exactly** returned `spec`, no registry-only kwargs.
4. Immediately record `automation create-result --release <id> --slug <slug>
   --attempt-id <attempt> --outcome created --id <provider-id> --evidence
   "<receipt identifying the exact authorized invocation>"
   --spec-file <SAME temporary-spec.json>`. Delete both temporary files only after this
   owning result is durably recorded.
   Never paste prompts or raw responses into evidence; only its digest is stored.
   Record positive non-creation as `not_created`; timeouts or
   unknown outcomes as `uncertain`. Never automatically retry an unresolved claim.
5. Identical prepare preserves the entire existing entry, including owner/ID/status.
   Changed specs cannot replace an existing intent, even active/prepared. Restore the
   exact reviewed source/spec to acknowledge an old claim, or safely retire it with
   the owner. Registry schema v3 rejects older schemas; no silent migration/rehash.
   Provider defaults are explicit in the plan (including model, enabled, oneShot,
   triggerType, conditionCheckInterval, browserHeadless and teamsNotify).

Right after `init`, reconcile the **push-reminder automation** for THIS release so reminders reach the user even with Scout closed. Per-release: created at start, removed at close.
Use `automation plan --release <id> --slug push-reminders --json` before the CCD gate.
- **slug:** `push-reminders`
- **name:** `<YYYY-MM> · Release-wide — push reminders`
- **schedule:** `every hour`
- **teamsNotify:** `never`
- **cleanup_when:** `release_done`
- **prompt:**
  From `<AGENT_ROOT>` (**substitute the absolute confirmed release-agent path**), advance the active release **AUTONOMOUSLY**:
     1. Run `python -m orchestrator.cli status --release <YYYY-MM> --json`.
        Missing, unsigned, halted or complete means skip work, NOT cleanup.
     2. Loop `next --release <YYYY-MM> --json` and resolve `scout_pending` with
        `step-action --release <YYYY-MM> --phase <phase> --step <step>`. For notifications
        use the universal `notification prepare` (source step), claim/result protocol
        from SKILL.md. RC reports and Native Auth use this same protocol. Non-notification
        gather/trigger actions retain their domain follow-ups. Never blind-record pass.
     3. Run `tick --release <YYYY-MM> --json`, then
        `notification prepare --release <YYYY-MM> --source digest`. Independently deliver
        eligible owner email, owner Teams and Core alerts through claim/result, not raw
        message blocks. Core alerts are scoped to active preflight after 9 AM on the
        previous business day or CCD only. Inspect source pending for completion/provisioning
        recovery; never resend claimed/uncertain/sent records. Do not add courtesy copies.
     4. ALWAYS run `automation cleanup --release <YYYY-MM> --json`, then the
        claimed cleanup protocol below, in a finally block.

Do it silently as part of start (the user already opted into push). **Why hourly, not once at 9am:** `tick` is idempotent (advancing no-ops once holding; digest de-dupes to one email/day), so a tick missed while the machine was off is picked up by the next. A single daily trigger would be skipped that day.

## Ensure the daily partner status email exists (per release — provisioned at start, closed at end of Phase 4)

Alongside the push reminders, reconcile the **partner status email** through the same protocol.
Use `automation plan --release <id> --slug daily-status-email --json`. The detailed
flow below documents the canonical code, not an independently editable provider spec.
- **slug:** `daily-status-email`
- **name:** `<YYYY-MM> · Phases 2–4 — daily status email`
- **schedule:** `every 1 hour`
- **teamsNotify:** `never`
- **cleanup_when:** `phase_done:finalize`
- **prompt:** From `<AGENT_ROOT>`, send the daily partner status email if one is due:
     1. Run `notification prepare --release <YYYY-MM> --source status-email`.
        The command gates on the stored owner's timezone, business day and 17:00.
        Delivery occurs at the first eligible hourly tick at/after 17:00, not necessarily
        exactly 17:00. Missing timezone fails clearly. Never use `--force` in this worker.
        For an isolated TEST release, add `--send-to <verified-test-address>`.
     2. For each eligible notification, use SKILL.md's exact prepare/claim/result protocol.
        Empty/stopped/claimed/uncertain/sent means no send, never an automatic retry.
     3. ALWAYS run the claimed cleanup protocol, even when no email is due.

**Closing it (end of Phase 4).** `finalize.final_status_email` uses source step and the
same claim/result protocol; only acknowledged delivery completes the step. Cleanup
then retires the daily worker. Known-not-sent failures remain retryable while its
scope is open; explicit owner skip or phase completion ends delivery eligibility.


## Provision the timed phase automations (config-driven, per release)

Some steps must fire at a specific time of day (not just "on their date") — e.g. the CCD-day comms at 09:00 and the localization trigger at noon. These are declared as DATA in `config/automations.yaml`, which maps each automation to the exact steps it drives; the fire time is derived from each step module's `fire_at_local`. **Provision them only once the CCD is CONFIRMED** — the schedules are cron-pinned to the CCD date, so a wrong/unsettled CCD pins them to the wrong day. Concretely: wait until the CCD is settled (`status --json` shows no `ccd_conflict`, and — for a normal start — the entry gate's `ccd_confirmed` item has passed). Then:

1. `python -m orchestrator.cli automation plan --release <YYYY-MM> --json` — returns **startup automations only**, including push/daily workers; `on_demand:true` pollers are excluded. It returns metadata, `registration` and complete `provider_spec`. Non-empty `problems` is a stop, never a success-shaped schedule fallback.
2. For each automation in the result, execute the recoverable provisioning protocol above:
   - **Complete kwargs:** exactly `provider_spec`. The owner-local CCD/fire datetime
     is converted to the scheduler host's IANA timezone before generating cron;
     host and owner dates can differ. Keep **oneShot:true** (cron has no year).
     Missing zones, ambiguous/nonexistent times, past targets and targets more than
     a year ahead fail clearly. Never turn a missed target into next year's send.
   - **teamsNotify:** `never` (it emails/posts via the steps themselves).

### Provision an on-demand poller
When a step/command asks for an on-demand poller, run `automation plan --release
<YYYY-MM> --on-demand <slug> --json`; reconcile exactly that returned automation
through prepare/list/reconcile-create/create-result. Current slugs: `build-verify-rc-poller` after
`rc-retriggered`, `ccd-localization-poller` after the noon localization trigger,
and `bug-bash-update-poller` after the first Bug Bash update.
Never create these during release initialization.
Include `--on-demand <slug>` on prepare/reconcile claims; the CLI rejects implicit
on-demand creation. CCD one-shot preparation/claims require a passed `ccd_confirmed`
readiness item and no conflict. Owning result acknowledgements do not re-evaluate a
new plan: they must remain possible after time/source/CCD changes.

The noon worker runs `launch-localization --execute --auto-approve --executor
localization-automation`. This localization-specific path computes the current
provider/source/variable plan, checkpoints its review hash, fences exactly one
provider request, and verifies the actual build receipt before attaching a run.
It must not wait for human approval or call a raw trigger. Interrupted/uncertain
launches remain owned. Recover only a matching build with `record-localization-run`;
do not launch another to repair a receipt. The auto-approved hash binds the workflow
revision; adoption never automatically drains work.

The same auto-approved checked-write contract applies to the scheduled finalize/rollout
writers `create-integration-prs`, `create-oneauth-common-pr`, `create-payload-wiki`, and
`start-release-signoff`. The signoff writer is long-running: after the stage is started
it leaves `rollout_start.signoff_start` in-flight, and normal step polling marks it done
only when ADO reports `Release Sign Off` completed successfully.
`distribute-tests` remains human-reviewed because it applies live ADO assignment changes.

The localization poller runs hourly, reading the exact recorded run's `status` AND
`result` on every poll (including PR monitoring). `--complete true` alone is not success.
Discovery requires `--run-result succeeded`, the full nonblank OneLocBuild task log,
and `--logs-complete`. The supported proof is `Pull request created with ID '<number>'`.
A successful complete task log with `/createpr: True` and no PR line is the
supported no-strings outcome: Scout stages a Code Reviews notice that there are
no strings to localize, and the notification finalizer marks localization done.
Missing/partial/unrecognized logs still never mean no strings, and logs with
`/createpr: False` block as a misconfigured/test run. Only an owner who reviewed
an unsupported full successful output may supply `--no-change-confirmation
"<explanation>"`; the worker must not invent it.
After verified PR discovery, it posts the initial Code Reviews request once and
monitors that PR until ADO reports it completed (still requiring successful run evidence),
and keeps Phase 1 open until merge or the omission cutoff. At 4:00 PM
America/Los_Angeles on CCD, an unmerged
PR causes one additional Code Reviews warning that the translated strings are at risk.
If the PR is still unmerged at 6:00 PM America/Los_Angeles, the command marks localization
skipped/omitted so Phase 2 proceeds without those strings and the poller is cleaned up.
Initial PR, deadline warning and timeout email are staged per-channel notifications.
Use source pending and claim/result. Timeout does not block the step before required
delivery is acknowledged. A merge, owner skip or closed phase cancels stale follow-ups.
Confirmed merge with a successful run wins over the 6 PM cutoff. Missing/unknown
results or absent/partial/unrecognized logs wait with an explanation, then escalate
after the existing 3h timeout even when the pipeline finished. Failed/canceled runs
and `/createpr: False` runs block with a run link, never “no strings.” After failure or acknowledged timeout,
the owner must inspect the old run and explicitly reopen before reserving another
trigger; the new receipt archives prior run evidence in existing `previous_runs`.
In-flight work only polls the original execution and never offers a duplicate trigger.

**Traceability:** every timed step is owned by exactly one automation (a guardrail test enforces this). Each registry entry has a **kind** — `step-driving` (owns steps, e.g. the CCD automations) or `release-level` (whole-release, no steps, e.g. push reminders), auto-derived from whether you pass `--step`. To answer "which automation runs step X?" → `automation list --release <YYYY-MM> --step-filter <phase.step>`. To see "what does this automation drive?" → `automation list --release <YYYY-MM>` (each row shows its `[kind]` and `drives: …`, or `(release-level — no steps)`). At runtime each step-driving automation journals `<slug> ran <step>` into the release event log, so the whole chain (config → registered automation → step execution) is inspectable.

### Any automation you provision MUST complete the registry lifecycle
- **Per-release** (normal, e.g. push reminders, the CCD phase automations) → `--release <YYYY-MM>` (+ `--step` for step-driving ones). **Removed when that release closes.**
- **Shared/persistent** (rare — genuinely meant to outlive every release) → `--shared`. Not torn down. Default per-release.

At **release close** (status complete / Release Close phase / user asks to "clean up automations"):
1. `automation list --release <YYYY-MM> --json` — the release's automations.
2. Run `automation cleanup --release <YYYY-MM> --json`; for each removal in order,
   claim with `automation claim-delete --id <id> --executor <session> --json`.
   Call `m_delete_automation` only when `permission_to_delete:true`, then record
   `automation delete-result --id <id> --attempt-id <attempt>
   --outcome deleted|not_deleted|uncertain --evidence "<provider evidence>"`.
   **Stop on a denied claim/barrier, error or uncertain result.** Ordering is not authority:
   the registry lock bars release-level deletion while nonmanual child intents or
   unresolved sibling operations remain; helpers precede the `push-reminders` recovery
   worker. Reverse barriers prohibit new workers while release-level deletion is
   unresolved. Owning receipts/absence recovery remain usable.
3. Preserve shared/manual entries. Missing/invalid lifecycle metadata requires owner review,
   not inferred ownership or automatic migration. A halted release suspends, not deletes.
4. ID-less terminal intents are not invisible cleanup successes. After proving the
   original runner AND provider operation terminated, uncertain/missing/blocked entries
   can use `automation confirm-absent --release <id> --slug <slug> --observed-file
   <fresh-observations.json> --reason "<owner evidence>"
   --confirm-absent --confirm-no-inflight`. Live creating/deleting claims must first
   record their owning result; absence alone cannot fence a running provider call.
   Verified absence of delete_uncertain removes that entry; creation recovery returns
   prepared. Then `automation abandon-prepared --release <id> --slug <slug>
   --observed-file <new-fresh-observations.json> --reason "<owner evidence>" --confirm-absent`
   removes only an ID-less prepared intent in a complete/cancelled release. Manual/shared
   intents are exempt. No tombstone, payload copy or direct deregister is created.

## Code Complete Date (CCD) & phase scheduling

Phases are **anchored to the CCD**, not started on demand. **The CCD is the 2nd Wednesday of the release month — the canonical default.** `init` computes it and prints when Phase 0 opens.

`init` also *reads* the pipeline (ADO 3038 `overrideCodeCompleteDate`) but **does not silently adopt it.** A **different in-month date** is a **conflict to resolve**: status shows *"⚠ Confirm the date"* and `status --json` sets `ccd_conflict`. Ask the user which is the real CCD via `m_ask_user`:
- **"Use the 2nd-Wednesday default (`<default>`)"** — then offer to sync the pipeline: `set-ccd --release <id> --default --reason "<why>"` (preview) → show it → `--confirm` to clear the override.
- **"Use the pipeline date (`<pipeline>`)"** — `set-ccd --release <id> --date <pipeline> --reason "confirmed CCD is <pipeline>" --confirm`.

Either resolution clears the conflict. Never pick for the user.

- **Phase 0 opens at CCD‑7.** You can `init` anytime, but until CCD‑7 the release sits in **`scheduled`** — the engine runs nothing. Status says *"📅 Scheduled — Pre‑flight opens `<date>` (in N days)."* Relay plainly; don't force it.
- At CCD‑7, `next` opens Phase 0 and runs to the first gate.
- **Testing the clock:** read/advance previews and `notification prepare` accept `--as-of`
  to simulate the date. `notification claim/result/finalize` reject overrides and use the
  trusted current clock; tests patch clocks only in isolated fixtures.

**Use the engine's shared scheduling result, not a second readiness rule in the skill.**
`next`, status and `scout_pending` share the same frontier, dependencies and clock.
A pending parallel gate is the presentation focus, but independent automatic/Scout
work can still run. A denied gate, halt, cancellation or unsatisfied readiness gate
stops dispatch; do not treat a displayed gate as permission to bypass any of those.
Timed new work (including outcome mocks) waits for its fire time in both sequential
and parallel phases. In-flight external work needs its declared poll path, not a
new reservation. Existing engine-owned effects use their declared recovery path,
at most once per drain, without relinquishing ownership or starting another write.
Poll/refresh commands retain their own eligibility checks; absence from
`scout_pending` is not evidence that an existing operation has finished.

**Changing the CCD (real production change).** `set-ccd` **writes the pipeline override** — gated: run without `--confirm` first (preview) → present → explicit yes (a `--reason` is always required) → re-run with `--confirm`. Month-scoped (date must be in the release month). `--default` reverts to 2nd-Wednesday.

**After a confirmed CCD change, review automation drift.** `automation sync --release
<id> --json` reports full-intent drift and `permission_to_update:false`. There is no
crash-safe in-place update protocol: **never** call update then register. Have the
owner review claimed delete/result and fresh prepare/list/reconcile/create-result,
respecting all barriers. Unchanged workers stay untouched. Past targets must be
handled as missed work, not pinned to next year. A source/prompt change also changes
the hash; never silently replace it or overwrite old owner receipts.

**Skipping/cancelling the release.** Same gated pattern: `skip-release` sets the pipeline `skipRelease` switch (preview → confirm, reason required); `--clear` re-enables. Suppresses the monthly trigger — confirm before `--confirm`.

**Ongoing conflict detection.** `status`/`resume` re-read the pipeline; a later differing override re-surfaces `ccd_conflict` — ask again. (`--no-pipeline-check` only if offline.)

## Push reminders — the daily phase digest (reaching the user when Scout is closed)

Everything else is **pull** (seen when the user opens Scout). The **push** layer is a **daily phase status digest** delivered to the release owner — by **email and (if enabled) Teams**:
- **Setup is interactive — no push.** Readiness checklist + establishing CCD happen live in Scout; never pushed. Unsigned / blocked / halted releases stay silent.
- **The first push is a phase opening.** Phase 0 opens at CCD‑7 — the first digest. Nothing before a phase opens.
- **Not while Scout still owes steps on the open phase.** A phase's digest reports the *settled* "here's what needs **you**" picture, so it holds while the phase has un‑run Scout steps (`scout_pending` — e.g. Phase 0's notice / feature‑owner reminders / lockdown). The digest fires once Scout has drained its own steps, so the owner never gets a premature email that lists Scout's undone work. (A step that *blocks* becomes a real user task and does push.)
- **Daily while a phase has outstanding work.** Once open (and Scout's steps drained), the owner gets a **once‑per‑day** digest (progress + what needs them) until the phase's actions are done; the next phase's digest takes over when it opens.
- **Channels** are set in `config/notifications.yaml` (`channels.email`, `channels.teams`; `teams.target: scout` → the **Scout Teams bot** DM via `m_send_teams_message`, or an explicit chat id for a shared chat). Purpose: keep the **release owner** aware and pull them in when a step needs them. Anyone else is notified only when a specific step requires it (that's the step-driving automations, e.g. the CCD reminders) — not this digest.

`tick --release <id> --json` advances runnable work, then returns preview notifications.
`notify --release <id> --json` is read-only: no advancement or send stamp.
Use `notification prepare --release <id> --source digest` and independent channel
claim/result calls to send. The owner's timezone determines the day; an acknowledgement
after midnight retains the prepared date. `--force` never bypasses dedup or lifecycle.

The hourly worker advances its explicitly pinned release, drains eligible Scout work,
then prepares the owner digest. Email and Teams acknowledge separately: failure on one
does not erase the other. No ad-hoc courtesy copies or automatic summary-to-Teams;
`teamsNotify: never` avoids leaking payloads to the runner's owner. The bot transport
requires verified runner/owner identity. Every exit runs cleanup, even silence.

If the user asks "how will I be reminded" / "set up notifications," explain this; create the automation if missing. Keep the email subject/body exactly as the successful claim returns — don't embellish or send the raw `tick` preview.
