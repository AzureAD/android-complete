# Reference — Starting a release, CCD scheduling & push reminders

_Loaded on demand. Covers `init`, the push-reminder automation, CCD anchoring/conflicts, and the daily digest._

## Starting a release (don't make the user type a date format)

The release id is just `YYYY-MM`. **You compute it — never ask the user to type the format.** Work out the current month from today (e.g. today 2026-07 → `2026-07`).

When no release is active (or the user says "start a release"), call **`m_ask_user`** with clickable options:
- **"Current month (`<YYYY-MM>`)"** ← recommended
- **"A different month"**

Pick current → `init --release <id>` immediately. Pick different → follow-up `m_ask_user` free-text (hint: "e.g. next month, or 2026-08"); accept natural answers ("this month", "August") and do the date math yourself. Runs are real — for test runs the engineer keeps a `mocks.local.yaml` (skip/redirect/inject per step; see `mock-spec`).

`init` records the **release owner** (the engineer running it) from the signed-in `az` user; reminders email that address. Pass `--owner-email`/`--owner-name` for a richer profile (e.g. from `workiq_get_my_profile`), or change later with `set-owner`. Never hardcode a recipient.

## Ensure push reminders exist (per release — provisioned at start, torn down at close)

Right after `init`, make sure the **push-reminder automation** exists for THIS release so reminders reach the user even with Scout closed. Per-release: created at start, removed at close.
1. `m_list_automations`. If **"Release push reminders"** exists AND `automation list --release <YYYY-MM> --json` has it scoped to this release, **leave it** — don't duplicate.
2. If missing, `m_create_automation`:
   - **name:** `Release push reminders`
   - **schedule:** `every hour`
   - **teamsNotify:** `never`
   - **prompt:** from `C:\repos\android-complete\release-agent` run `python -m orchestrator.cli tick --json` (ADVANCES the active release — runs agent steps, holds at gates/actions — then returns `{message, html, subject, owner_email, owner_name, release, channels, teams}`). If `message` is empty, do nothing. Otherwise deliver on every enabled channel:
     - **Email** (when `channels.email`): `workiq_send_email` (`to:[owner_email]`, `subject:` the value, `body:` the `html` with `isHtml:true` — fall back to plain `message`/`isHtml:false` only if `html` empty). Recipient from `owner_email` — never hardcode.
     - **Teams** (when `channels.teams` and `teams` is non-null): dispatch on `teams.via` —
       - `"scout_bot"` (default): `m_send_teams_message` with `message:` the `teams.text` value (the **markdown** digest, sent verbatim). This is the **Scout Teams bot** DM — the owner's Scout notification channel. Requires the Teams relay connected (`m_relay_status`); if it's down, email still covers it.
       - `"chat"`: `workiq_send_chat_message` with **exactly** the `teams` block fields (`chatId`, `content`, `contentType`) — only used when a specific shared chat is configured.

     The `teams` descriptor is only present when a digest is actually due (it respects the same once-per-day de-dup), so delivering it never double-notifies. The digest is identical across channels — send it verbatim, don't embellish. Channels are configured in `config/notifications.yaml`.
3. **Register it** so it's torn down at close: `automation register --id <id> --name "Release push reminders" --release <YYYY-MM> --purpose "hourly advance + phase digest to owner (email + Teams)"` — no `--step`, so it's recorded as a **release-level** automation (it advances the whole release, owns no step).

Do it silently as part of start (the user already opted into push). **Why hourly, not once at 9am:** `tick` is idempotent (advancing no-ops once holding; digest de-dupes to one email/day), so a tick missed while the machine was off is picked up by the next. A single daily trigger would be skipped that day.

## Provision the timed phase automations (config-driven, per release)

Some steps must fire at a specific time of day (not just "on their date") — e.g. the CCD-day comms at 09:00 and the localization trigger at noon. These are declared as DATA in `config/automations.yaml`, which maps each automation to the exact steps it drives; the fire time is derived from each step module's `fire_at_local`. Provision them **after `init` and once the CCD is set**:

1. `python -m orchestrator.cli automation plan --release <YYYY-MM> --json` — returns the concrete automations to create (`name`, `schedule`, `steps`, `purpose`, `prompt`). If `problems` is non-empty, STOP and report — the config/step mapping drifted.
2. For each automation in the result, skip if `automation list --release <YYYY-MM> --json` already has one whose `steps` match (don't duplicate). Otherwise `m_create_automation`:
   - **name / schedule / prompt:** exactly the values from the plan (schedule is a one-shot on the CCD date; set **oneShot:true**).
   - **teamsNotify:** `never` (it emails/posts via the steps themselves).
3. **Register it WITH its steps** so the linkage is recorded and it's torn down at close — copy the plan's `register:` line, filling the real Scout id:
   `automation register --id <scout-id> --name "<name>" --release <YYYY-MM> --purpose "<purpose>" --step <phase.step> [--step …]`

**Traceability:** every timed step is owned by exactly one automation (a guardrail test enforces this). Each registry entry has a **kind** — `step-driving` (owns steps, e.g. the CCD automations) or `release-level` (whole-release, no steps, e.g. push reminders), auto-derived from whether you pass `--step`. To answer "which automation runs step X?" → `automation list --release <YYYY-MM> --step-filter <phase.step>`. To see "what does this automation drive?" → `automation list --release <YYYY-MM>` (each row shows its `[kind]` and `drives: …`, or `(release-level — no steps)`). At runtime each step-driving automation journals `<slug> ran <step>` into the release event log, so the whole chain (config → registered automation → step execution) is inspectable.

### Any automation you provision MUST be registered (for teardown)
- **Per-release** (normal, e.g. push reminders, the CCD phase automations) → `--release <YYYY-MM>` (+ `--step` for step-driving ones). **Removed when that release closes.**
- **Shared/persistent** (rare — genuinely meant to outlive every release) → `--shared`. Not torn down. Default per-release.

At **release close** (status complete / Release Close phase / user asks to "clean up automations"):
1. `automation list --release <YYYY-MM> --json` — the release's automations.
2. For each: `m_delete_automation` (id from entry), then `automation deregister --id <id>`.
3. Shared automations aren't in the release-scoped list — leave them. Confirm before deleting; report what was removed.

## Code Complete Date (CCD) & phase scheduling

Phases are **anchored to the CCD**, not started on demand. **The CCD is the 2nd Wednesday of the release month — the canonical default.** `init` computes it and prints when Phase 0 opens.

`init` also *reads* the pipeline (ADO 3038 `overrideCodeCompleteDate`) but **does not silently adopt it.** A **different in-month date** is a **conflict to resolve**: status shows *"⚠ Confirm the date"* and `status --json` sets `ccd_conflict`. Ask the user which is the real CCD via `m_ask_user`:
- **"Use the 2nd-Wednesday default (`<default>`)"** — then offer to sync the pipeline: `set-ccd --release <id> --default --reason "<why>"` (preview) → show it → `--confirm` to clear the override.
- **"Use the pipeline date (`<pipeline>`)"** — `set-ccd --release <id> --date <pipeline> --reason "confirmed CCD is <pipeline>" --confirm`.

Either resolution clears the conflict. Never pick for the user.

- **Phase 0 opens at CCD‑7.** You can `init` anytime, but until CCD‑7 the release sits in **`scheduled`** — the engine runs nothing. Status says *"🗓 Scheduled — Pre‑flight opens `<date>` (in N days)."* Relay plainly; don't force it.
- At CCD‑7, `next` opens Phase 0 and runs to the first gate.
- **Testing the clock:** every read/advance command accepts `--as-of YYYY-MM-DD` to simulate the date. Normal runs use today.

**Changing the CCD (real production change).** `set-ccd` **writes the pipeline override** — gated: run without `--confirm` first (preview) → present → explicit yes (a `--reason` is always required) → re-run with `--confirm`. Month-scoped (date must be in the release month). `--default` reverts to 2nd-Wednesday.

**Skipping/cancelling the release.** Same gated pattern: `skip-release` sets the pipeline `skipRelease` switch (preview → confirm, reason required); `--clear` re-enables. Suppresses the monthly trigger — confirm before `--confirm`.

**Ongoing conflict detection.** `status`/`resume` re-read the pipeline; a later differing override re-surfaces `ccd_conflict` — ask again. (`--no-pipeline-check` only if offline.)

## Push reminders — the daily phase digest (reaching the user when Scout is closed)

Everything else is **pull** (seen when the user opens Scout). The **push** layer is a **daily phase status digest** delivered to the release owner — by **email and (if enabled) Teams**:
- **Setup is interactive — no push.** Readiness checklist + establishing CCD happen live in Scout; never pushed. Unsigned / blocked / halted releases stay silent.
- **The first push is a phase opening.** Phase 0 opens at CCD‑7 — the first digest. Nothing before a phase opens.
- **Daily while a phase has outstanding work.** Once open, the owner gets a **once‑per‑day** digest (progress + what needs them) until the phase's actions are done; the next phase's digest takes over when it opens.
- **Channels** are set in `config/notifications.yaml` (`channels.email`, `channels.teams`; `teams.target: scout` → the **Scout Teams bot** DM via `m_send_teams_message`, or an explicit chat id for a shared chat). Purpose: keep the **release owner** aware and pull them in when a step needs them. Anyone else is notified only when a specific step requires it (that's the step-driving automations, e.g. the CCD reminders) — not this digest.

`tick` is the deterministic automation half: `tick --json` first **advances** the release (runs runnable steps, holds at gates/actions — idempotent), then returns `{message, html, subject, owner_email, owner_name, release, channels, teams}` — `message` plain-text digest, `html` rich version, `channels` the enabled map, `teams` a delivery descriptor (`{via:"scout_bot", text}` for the Scout bot, or `{via:"chat", chatId, content, contentType}` for an explicit chat, or null); all empty/null when nothing's due or already sent today. (`notify --json` is the read-only variant — same payload, does NOT advance.) `--as-of <date>` debug clock; `--force` bypasses once‑per‑day.

The **"Release push reminders"** automation runs `tick --json` (discovery mode) **hourly** and delivers a non-empty digest to the owner on every enabled channel (`channels` in the payload): **email** (`html`/`message` → `owner_email`, subject from JSON — never a hardcoded address) and, when `teams` is non-null, **Scout Teams** (`m_send_teams_message` with the markdown `teams.text` for the `scout_bot` target, or `workiq_send_chat_message` for an explicit chat). Empty `message` → silent. Per‑release: auto‑provisioned at start, torn down at close. Email is the guaranteed floor; Teams is a bonus channel that degrades gracefully (the `teams_notify` readiness item records `degraded` = email-only if the Scout bot isn't reachable).

If the user asks "how will I be reminded" / "set up notifications," explain this; create the automation if missing. Keep the email subject/body exactly as `tick` returns — don't embellish.
