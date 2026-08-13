# Reference — Starting a release, CCD scheduling & push reminders

_Loaded on demand. Covers `init`, the push-reminder automation, CCD anchoring/conflicts, and the daily digest._

## Starting a release (don't make the user type a date format)

The release id is just `YYYY-MM`. **You compute it — never ask the user to type the format.** Work out the current month from today (e.g. today 2026-07 → `2026-07`).

When no release is active (or the user says "start a release"), call **`m_ask_user`** with clickable options:
- **"Current month (`<YYYY-MM>`)"** ← recommended
- **"A different month"**

Pick current → `init --release <id>` immediately. Pick different → follow-up `m_ask_user` free-text (hint: "e.g. next month, or 2026-08"); accept natural answers ("this month", "August") and do the date math yourself. Default to **dry-run**; only `--live` if explicitly asked.

`init` records the **release owner** (the engineer running it) from the signed-in `az` user; reminders email that address. Pass `--owner-email`/`--owner-name` for a richer profile (e.g. from `workiq_get_my_profile`), or change later with `set-owner`. Never hardcode a recipient.

## Ensure push reminders exist (per release — provisioned at start, torn down at close)

Right after `init`, make sure the **push-reminder automation** exists for THIS release so reminders reach the user even with Scout closed. Per-release: created at start, removed at close.
1. `m_list_automations`. If **"Release push reminders"** exists AND `automation list --release <YYYY-MM> --json` has it scoped to this release, **leave it** — don't duplicate.
2. If missing, `m_create_automation`:
   - **name:** `Release push reminders`
   - **schedule:** `every hour`
   - **teamsNotify:** `never`
   - **prompt:** from `C:\repos\android-complete\release-agent` run `python -m orchestrator.cli tick --json` (ADVANCES the active release — runs agent steps, holds at gates/actions — then returns `{message, html, subject, owner_email, owner_name, release}`); if `message` non-empty and `owner_email` set, email via `workiq_send_email` (`to:[owner_email]`, `subject:` the value, `body:` the `html` with `isHtml:true` — fall back to plain `message`/`isHtml:false` only if `html` empty); if `message` empty, do nothing. (Recipient from `owner_email` — never hardcode. Do NOT use `m_send_teams_message` (bot relay 404s) or the Teams self-chat (delivers silently).)
3. **Register it** so it's torn down at close: `automation register --id <id> --name "Release push reminders" --release <YYYY-MM> --purpose "hourly advance + phase digest email to owner"`

Do it silently as part of start (the user already opted into push). **Why hourly, not once at 9am:** `tick` is idempotent (advancing no-ops once holding; digest de-dupes to one email/day), so a tick missed while the machine was off is picked up by the next. A single daily trigger would be skipped that day.

### Any automation you provision MUST be registered (for teardown)
- **Per-release** (normal, e.g. push reminders, a phase watcher) → `--release <YYYY-MM>`. **Removed when that release closes.**
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
- **Testing the clock:** every read/advance command accepts `--as-of YYYY-MM-DD` (dry-run only). Real runs use today.

**Changing the CCD (real production change).** `set-ccd` **writes the pipeline override** — gated: run without `--confirm` first (preview) → present → explicit yes (a `--reason` is always required) → re-run with `--confirm`. Month-scoped (date must be in the release month). `--default` reverts to 2nd-Wednesday.

**Skipping/cancelling the release.** Same gated pattern: `skip-release` sets the pipeline `skipRelease` switch (preview → confirm, reason required); `--clear` re-enables. Suppresses the monthly trigger — confirm before `--confirm`.

**Ongoing conflict detection.** `status`/`resume` re-read the pipeline; a later differing override re-surfaces `ccd_conflict` — ask again. (`--no-pipeline-check` only if offline.)

## Push reminders — the daily phase digest (reaching the user when Scout is closed)

Everything else is **pull** (seen when the user opens Scout). The **push** layer is a **daily phase status digest** emailed to the owner:
- **Setup is interactive — no push.** Readiness checklist + establishing CCD happen live in Scout; never emailed. Unsigned / blocked / halted releases stay silent.
- **The first push is a phase opening.** Phase 0 opens at CCD‑7 — the first email. Nothing before a phase opens.
- **Daily while a phase has outstanding work.** Once open, the owner gets a **once‑per‑day** digest (progress + what needs them) until the phase's actions are done; the next phase's digest takes over when it opens.

`tick` is the deterministic automation half: `tick --json` first **advances** the release (runs runnable steps, holds at gates/actions — idempotent), then returns `{message, html, subject, owner_email, owner_name, release}` — `message` plain-text digest, `html` rich version, both empty when nothing's due or already sent today. (`notify --json` is the read-only variant — same payload, does NOT advance.) `--as-of <date>` debug clock; `--force` bypasses once‑per‑day.

The **"Release push reminders"** automation runs `tick --json` (discovery mode) **hourly** and emails non-empty `message` to `owner_email` (subject from JSON) — never a hardcoded address; empty → silent. Per‑release: auto‑provisioned at start, torn down at close. Email is the channel because it reliably notifies (the `m_send_teams_message` bot relay 404s without a conversation reference; the Teams self‑chat delivers silently).

If the user asks "how will I be reminded" / "set up notifications," explain this; create the automation if missing. Keep the email subject/body exactly as `tick` returns — don't embellish.
