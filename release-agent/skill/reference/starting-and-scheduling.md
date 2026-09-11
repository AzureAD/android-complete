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

Right after `init`, make sure the **push-reminder automation** exists for THIS release so reminders reach the user even with Scout closed. Per-release: created at start, removed at close.
1. `m_list_automations`. If **"`<YYYY-MM> · Release-wide — push reminders`"** exists AND `automation list --release <YYYY-MM> --json` scopes it to this release, do not duplicate it. Inspect its prompt; report outdated protocols for the deliberate deployment checklist above, do not auto-update live settings.
2. If missing, `m_create_automation`:
   - **name:** `<YYYY-MM> · Release-wide — push reminders`  (fill `<YYYY-MM>` with the release id — the standard `<release-id> · <scope> — <purpose>` title)
   - **schedule:** `every hour`
   - **teamsNotify:** `never`
   - **prompt:** From `<AGENT_ROOT>` (**substitute the absolute confirmed release-agent path** — see SKILL.md → FIRST RUN — since this runs headless with no one to resolve a placeholder; e.g. run `paths --json` and paste the real `agent_root`), advance the active release **AUTONOMOUSLY** (no user is watching) and send the daily digest:
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
     4. ALWAYS, in a finally block (including silence/errors), run
        `automation cleanup --release <YYYY-MM> --json`. Delete each live automation,
        then deregister only after success. Failed deletions remain registered.
3. **Register it** so it's torn down at close: `automation register --id <id> --name "<YYYY-MM> · Release-wide — push reminders" --release <YYYY-MM> --cleanup-when release_done --purpose "hourly autonomous advance (runs scout steps) + phase digest to owner (email + Teams)"` — no `--step`, so it's recorded as a **release-level** automation (it advances the whole release, owns no step).

Do it silently as part of start (the user already opted into push). **Why hourly, not once at 9am:** `tick` is idempotent (advancing no-ops once holding; digest de-dupes to one email/day), so a tick missed while the machine was off is picked up by the next. A single daily trigger would be skipped that day.

## Ensure the daily partner status email exists (per release — provisioned at start, closed at end of Phase 4)

Alongside the push reminders, provision the **partner status email** — an **end-of-day** business-day email to the release DLs (`authsdkrelease@`, `androididentity@`) with the milestone dashboard, sent while the release is in flight (**Phase 2 build_verify through Phase 4 finalize**). EOD so it reports the day's SETTLED progress (matches the checklist's "📧 End-of-day: send status email").
1. `m_list_automations`. If **"`<YYYY-MM> · Phases 2–4 — daily status email`"** is already scoped to this release, leave it.
2. If missing, `m_create_automation`:
   - **name:** `<YYYY-MM> · Phases 2–4 — daily status email`  (fill `<YYYY-MM>` with the release id — the standard `<release-id> · <scope> — <purpose>` title)
   - **schedule:** `every weekday at 5pm`  (end of day; weekends are skipped natively; the command also skips US holidays and the window)
   - **teamsNotify:** `never`
   - **prompt:** From `<AGENT_ROOT>` (**substitute the absolute confirmed release-agent path** — see SKILL.md → FIRST RUN — since this runs headless; `paths --json` prints the real `agent_root`), send the daily partner status email if one is due:
     1. Run `notification prepare --release <YYYY-MM> --source status-email`.
        For an isolated TEST release, add `--send-to <verified-test-address>`.
     2. For each eligible notification, use SKILL.md's exact prepare/claim/result protocol.
        Empty/stopped/claimed/uncertain/sent means no send, never an automatic retry.
     3. ALWAYS run `automation cleanup --release <YYYY-MM> --json` in a finally block,
        even when no email is due. Delete first, deregister only on success.
3. **Register it** for teardown: `automation register --id <id> --name "<YYYY-MM> · Phases 2–4 — daily status email" --release <YYYY-MM> --cleanup-when phase_done:finalize --purpose "business-day partner status email (Phase 2-4)"`.

**Closing it (end of Phase 4).** `finalize.final_status_email` uses source step and the
same claim/result protocol; only acknowledged delivery completes the step. Cleanup
then retires the daily worker. Known-not-sent failures remain retryable while its
scope is open; explicit owner skip or phase completion ends delivery eligibility.


## Provision the timed phase automations (config-driven, per release)

Some steps must fire at a specific time of day (not just "on their date") — e.g. the CCD-day comms at 09:00 and the localization trigger at noon. These are declared as DATA in `config/automations.yaml`, which maps each automation to the exact steps it drives; the fire time is derived from each step module's `fire_at_local`. **Provision them only once the CCD is CONFIRMED** — the schedules are cron-pinned to the CCD date, so a wrong/unsettled CCD pins them to the wrong day. Concretely: wait until the CCD is settled (`status --json` shows no `ccd_conflict`, and — for a normal start — the entry gate's `ccd_confirmed` item has passed). Then:

1. `python -m orchestrator.cli automation plan --release <YYYY-MM> --json` — returns **startup automations only**; `on_demand:true` pollers are intentionally excluded. It returns (`name`, `schedule`, `steps`, `slug`, `purpose`, `cleanup_when`, `prompt`, `registration`). If `problems` is non-empty, STOP and report — the config/step mapping drifted. If `ccd` is null, STOP — set the CCD first.
2. For each automation in the result, skip if `automation list --release <YYYY-MM> --json` already has one with the same `slug` (don't duplicate). Otherwise `m_create_automation`:
   - **name / schedule / prompt:** exactly the values from the plan (the schedule is a **cron pinned to the exact CCD date** — e.g. `cron: 0 9 26 8 *` for 09:00 on Aug 26 — NOT `every wednesday`, which fires the next weekday and would run the CCD-day comms a week early; set **oneShot:true** for the cron ones).
   - **teamsNotify:** `never` (it emails/posts via the steps themselves).
3. **Register it WITH its steps, slug, schedule, and cleanup rule** — copy the plan spec's `registration` fields, filling the real Scout id:
   `automation register --id <scout-id> --name "<name>" --release <YYYY-MM> --slug <slug> --schedule "<schedule>" --cleanup-when "<rule>" [--cleanup-when "<OR-rule>"] --purpose "<purpose>" --step <phase.step> [--step …]`

### Provision an on-demand poller
When a step/command asks for an on-demand poller, run `automation plan --release
<YYYY-MM> --on-demand <slug> --json`; create exactly that returned automation and
register all `registration` fields. Current slugs: `build-verify-rc-poller` after
`rc-retriggered`, `ccd-localization-poller` after the noon localization trigger,
and `bug-bash-update-poller` after the first Bug Bash update.
Never create these during release initialization.

The localization poller runs hourly. After the pipeline creates a PR, it posts the
initial Code Reviews request once, monitors that PR until ADO reports it completed,
and keeps Phase 1 open until merge or the omission cutoff. At 4:00 PM
America/Los_Angeles on CCD, an unmerged
PR causes one additional Code Reviews warning that the translated strings are at risk.
If the PR is still unmerged at 6:00 PM America/Los_Angeles, the command marks localization
skipped/omitted so Phase 2 proceeds without those strings and the poller is cleaned up.
Initial PR, deadline warning and timeout email are staged per-channel notifications.
Use source pending and claim/result. Timeout does not block the step before required
delivery is acknowledged. A merge, owner skip or closed phase cancels stale follow-ups.

**Traceability:** every timed step is owned by exactly one automation (a guardrail test enforces this). Each registry entry has a **kind** — `step-driving` (owns steps, e.g. the CCD automations) or `release-level` (whole-release, no steps, e.g. push reminders), auto-derived from whether you pass `--step`. To answer "which automation runs step X?" → `automation list --release <YYYY-MM> --step-filter <phase.step>`. To see "what does this automation drive?" → `automation list --release <YYYY-MM>` (each row shows its `[kind]` and `drives: …`, or `(release-level — no steps)`). At runtime each step-driving automation journals `<slug> ran <step>` into the release event log, so the whole chain (config → registered automation → step execution) is inspectable.

### Any automation you provision MUST be registered (for teardown)
- **Per-release** (normal, e.g. push reminders, the CCD phase automations) → `--release <YYYY-MM>` (+ `--step` for step-driving ones). **Removed when that release closes.**
- **Shared/persistent** (rare — genuinely meant to outlive every release) → `--shared`. Not torn down. Default per-release.

At **release close** (status complete / Release Close phase / user asks to "clean up automations"):
1. `automation list --release <YYYY-MM> --json` — the release's automations.
2. Run `automation cleanup --release <YYYY-MM> --json`; for each removal in order,
   `m_delete_automation` (id from entry), then `automation deregister --id <id>`.
   The universal release-complete backstop includes explicitly release-scoped nonmanual entries.
3. Preserve shared/manual entries. Missing/invalid lifecycle metadata requires owner review,
   not inferred ownership or automatic migration. A halted release suspends, not deletes.

## Code Complete Date (CCD) & phase scheduling

Phases are **anchored to the CCD**, not started on demand. **The CCD is the 2nd Wednesday of the release month — the canonical default.** `init` computes it and prints when Phase 0 opens.

`init` also *reads* the pipeline (ADO 3038 `overrideCodeCompleteDate`) but **does not silently adopt it.** A **different in-month date** is a **conflict to resolve**: status shows *"⚠ Confirm the date"* and `status --json` sets `ccd_conflict`. Ask the user which is the real CCD via `m_ask_user`:
- **"Use the 2nd-Wednesday default (`<default>`)"** — then offer to sync the pipeline: `set-ccd --release <id> --default --reason "<why>"` (preview) → show it → `--confirm` to clear the override.
- **"Use the pipeline date (`<pipeline>`)"** — `set-ccd --release <id> --date <pipeline> --reason "confirmed CCD is <pipeline>" --confirm`.

Either resolution clears the conflict. Never pick for the user.

- **Phase 0 opens at CCD‑7.** You can `init` anytime, but until CCD‑7 the release sits in **`scheduled`** — the engine runs nothing. Status says *"🗓 Scheduled — Pre‑flight opens `<date>` (in N days)."* Relay plainly; don't force it.
- At CCD‑7, `next` opens Phase 0 and runs to the first gate.
- **Testing the clock:** read/advance previews and `notification prepare` accept `--as-of`
  to simulate the date. `notification claim/result/finalize` reject overrides and use the
  trusted current clock; tests patch clocks only in isolated fixtures.

**Changing the CCD (real production change).** `set-ccd` **writes the pipeline override** — gated: run without `--confirm` first (preview) → present → explicit yes (a `--reason` is always required) → re-run with `--confirm`. Month-scoped (date must be in the release month). `--default` reverts to 2nd-Wednesday.

**After ANY confirmed CCD change, re-sync the CCD-day automations.** Their cron schedules are pinned to the old CCD, so a moved CCD leaves them firing on the wrong day (`set-ccd` prints a ⚠ reminder when step-driving automations are registered). Run **`automation sync --release <YYYY-MM> --json`** → `{ccd, updates:[{id, name, slug, cleanup_when, current_schedule, desired_schedule, changed}]}`. For every entry with **`changed: true`**: call **`m_update_automation`** with `id` and `schedule: desired_schedule`, then **re-register** it so the stored schedule and lifecycle match: `automation register --id <id> --name "<name>" --release <YYYY-MM> --slug <slug> --schedule "<desired_schedule>" --cleanup-when "<cleanup_when>" --step <…>`. Entries with `changed:false` are already correct — skip them. (The interval poller never changes.) Do this silently as part of the CCD change.

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
