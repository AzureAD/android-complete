# Reference — Phase 0 (Pre-flight & Code Complete)

_Loaded on demand when advancing Phase 0. Phase 0 is `execution: parallel`._

## Parallel phases — process ALL the holds, not one at a time

A single `next` runs **every independent automated step at once** (breaking, CG, cron, wiki — all in one call) then surfaces **all the human/scout holds together** (e.g. *"4 item(s) need you: …"*). After `next`, read `status --json` → **`pending_human`** (and `active_phase.steps` with `status`/`needs_owner`) — the full outstanding set. Work through **all** of them this pass:
- **`source: scout`** steps (notice, flight_reminder, lockdown) → run each via MCP/browser + `record-step` (below). Independent — do them all.
- **`attest`** steps (confirm_reminders, vitals) → ask the owner to confirm, then `done --step <id>`.
- **`blocked`** steps (cg/cron on a real problem) → show the note; fix + rerun, or skip.

Dependencies still hold: `confirm_reminders` only appears **after** `flight_reminder` is sent. Call `next` again after clearing holds to surface newly-ready steps and advance.

> **State writes are safe to parallelize.** The CLI serializes every state read-modify-write per release with an exclusive lock, so firing several `record-step`/`record-check`/`done` calls at once (or an hourly `tick` overlapping) can't clobber — a second invocation waits for the first to save.

Always show the `status` table each iteration (see the presenting-status reference) — never a bare prose list.

## `notice` — early code-complete email (WorkIQ)

When `status --json` shows current step **`notice`** (`awaiting_action`):
1. **Prepare.** `prepare-notice --release <id>` — fills `templates/early-code-complete-notice.md` with CCD/owner, returns `{subject, body, html, recipients, dry_run, recipients_note}`.
2. **Send.** `workiq_send_email` with the returned `subject`/`recipients` exactly, **`body:` the `html`, `isHtml:true`** (fall back to plain `body`/`isHtml:false` only if `html` empty). **Recipients are already resolved:** dry-run → owner only (subject prefixed `[DRY-RUN → owner]`); live → the real DL (androididentity@microsoft.com, jialh@microsoft.com — see EXTERNAL-REFERENCES.md). Never override recipients.
3. **Record.** `record-step --release <id> --step notice --status pass --detail "sent to <recipients>"`; on failure `--status attention --detail "<why>"`.

## `flight_reminder` — flight & string reminders (Teams)

Posts a combined 4-in-1 reminder (update local flights · flight pre-mortem docs · merge user-facing strings by CCD-7 · Auth App feature-flag freeze / default-OFF review) as a **Teams message** to the Android Core Team. When current step **`flight_reminder`**:
1. **Prepare.** `prepare-flight-reminder --release <id>` → `{content, content_type:"html", dry_run, send_to, owner_email, chat_id, target_note}`.
2. **Resolve chat + send.**
   - **Dry-run** (`send_to:"owner"`): `workiq_create_chat_by_email` (email = `owner_email`) → `workiq_send_chat_message` with that `chatId`, `content` = the HTML, `contentType:"html"`. (Prefixed `[DRY-RUN → owner]`.)
   - **Live** (`send_to:"group"`): `workiq_send_chat_message` with `chatId` = `chat_id` (Android Core Team thread), `content`, `contentType:"html"`.
   Never override the target — `prepare-flight-reminder` already picked owner-vs-group from `dry_run`.
3. **Record.** `record-step --release <id> --step flight_reminder --status pass --detail "posted to <target_note>"`; on failure `--status attention --detail "<why>"`.

## `confirm_reminders` — attestation (after flight_reminder)

Sending is fire-and-forget — it doesn't prove the work got done. The next step `confirm_reminders` (`awaiting_action`) holds. Ask the owner (via `m_ask_user`) to confirm the reminded work is actually done — feature owners updated local flights, wrote pre-mortem docs, merged user-facing strings by CCD-7, all features default-OFF (or default-ON approved in the wiki). Only on confirmation: `done --release <id> --step confirm_reminders --note "<what they confirmed>"`. If they can't confirm, leave it holding.

## `lockdown` — CCOA overlap check (browser)

Reads an AAD-gated source the engine can't reach, so **you run it via the browser and record**. When `lockdown` is a pending hold — handle silently unless there's an overlap:
1. **Scrape.** Navigate (Playwright) to `https://prod.change-manager.msidentity.com/ccoa-periods`. On AAD picker, click the user's own account (Windows-SSO, no password). Wait for "CCOA Periods".
2. **Extract.** From **"Upcoming CCOA periods"** and the current-year **"Past NoFly Zones"** table, read each row's Name, Environment, Start (UTC), End (UTC). Build `[{"name","environment","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}, …]`.
3. **Let the engine decide.** `check-lockdown --release <id> --periods-json '<json>'` — computes the window (CCD‑7…CCD+14), keeps **Production**-env periods, checks overlap, records: **pass** (no overlap → done) or **attention** (overlap → holds).
4. **Relay.** Pass → continue (`next`). Attention → name the overlapping lockdown(s) + window; tell them to **shift CCD** past it (`set-ccd`) if they want to proceed (no partners to notify here).

Can't reach browser/SSO? Leave the step held — don't mark it done without running the check.

## `vitals` — Play Console attestation

Play Console has no API for Policy issues/warnings (Reporting API covers only technical vitals; the Console UI is behind a Google login Scout can't automate). Manual check: when current step `vitals`, ask the owner to open Play Console, review **Android vitals** (crash/ANR) and **Policy status** (issues/warnings), confirm acceptable. On confirmation: `done --release <id> --step vitals --note "<what they saw>"`. Unresolved policy issue / vitals regression → leave holding.

## Automated Phase-0 steps (real agents, no scout action)
`breaking` (BREAKING-OneAuth scan + draft comms), `cg` (Component Governance alerts — blocks on High/Critical), `cron` (Calendar Checker pipeline scheduled), `wiki` (create payload wiki subpage). These run inside `next`; you just relay their results from the `status` table. `cg`/`cron` may **block** — see the core "blocked step" handling.
