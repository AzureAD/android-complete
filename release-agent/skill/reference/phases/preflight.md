# Reference — Phase 0 (Pre-flight & Code Complete)

_Loaded on demand when advancing Phase 0. Phase 0 is `execution: parallel`._

## Parallel phases — process ALL the holds, not one at a time

A single `next` runs **every independent automated step at once** (breaking, CG, cron, wiki — all in one call) then surfaces **all the human/scout holds together** (e.g. *"4 item(s) need you: …"*). After `next`, read `status --json` → **`pending_human`** (and `active_phase.steps` with `status`/`needs_owner`) — the full outstanding set. Work through **all** of them this pass:
- **`source: scout`** steps (notice, flight_reminder, lockdown) → run each via MCP/browser + `record-step` (below). Independent — do them all.
- **`attest`** steps (confirm_reminders, vitals) → ask the owner to confirm, then `done --step <id>`.
- **`blocked`** steps (cg/cron on a real problem) → show the note; fix + rerun, or skip.

Dependencies still hold: `confirm_reminders` only appears **after** `flight_reminder` is sent. Call `next` again after clearing holds to surface newly-ready steps and advance.

> **State writes are safe to parallelize.** The CLI serializes every state read-modify-write per release with an exclusive lock, so firing several `record-step`/`record-check`/`done` calls at once (or an hourly `tick` overlapping) can't clobber — a second invocation waits for the first to save.

**Render the table ONCE per advance pass — at the END.** Within a single pass, do the work first: run `next`, execute every resulting scout step (MCP/browser + `record-step`) and clear the attest holds, THEN paste the `status` table once to show the settled state (see the presenting-status reference). Don't paste an interim table before/while you run the scout steps — that early render is stale the moment you act and just duplicates the final one. One pass → one table. (The only exception is the golden rule: if this pass ends by asking for an attestation/gate decision, that final table must be in the same message as the `m_ask_user`.) Never a bare prose list.

## `notice` & `flight_reminder` — migrated scout comms steps (use `step-action`)

Both are now co-located step modules (`steps/preflight/`). Don't use their old `prepare-*` commands — run the **generic** dispatcher and execute the `needs_skill` payload it returns (see the `step-action` section in `commands.md`):

```
python -m orchestrator.cli step-action --release <id> --step <notice|flight_reminder> [--param variant=update]
```

It returns `{"kind":"needs_skill","tool":..., "payload":{...}, "record_as":..., "dry_run":...}` fully resolved (recipients/chat target already picked by mode). Steps:
1. **Run `step-action`** for the current step. `kind:"blocked"` (e.g. no CCD) → surface `reason`, stop. `kind:"needs_skill"` → continue.
2. **Execute `tool` with `payload` verbatim** — don't override recipients/chatId/body; the mode already decided them:
   - **`notice`** → `workiq_send_email` (payload has `to`, `subject`, `body` (HTML), `isHtml:true`). Dry-run → owner only, subject prefixed `[DRY-RUN → owner]`; live → the real DL (see EXTERNAL-REFERENCES.md). `--param variant=update` swaps the CCD-day wording.
   - **`flight_reminder`** → `workiq_send_chat_message` (payload has `chatId`, `content` (HTML), `contentType:"html"`). Dry-run `chatId` is `48:notes` (owner's own Teams chat); live is the Android Core Team thread — both directly sendable, no `workiq_create_chat_by_email` needed.
3. **Record.** `record-step --release <id> --step <record_as> --status pass --detail "<note>"`; on failure `--status attention --detail "<why>"`.

## `confirm_reminders` — attestation (after flight_reminder)

Sending is fire-and-forget — it doesn't prove the work got done. `confirm_reminders` (`awaiting_action`) holds. Run `step-action --release <id> --step confirm_reminders` → `needs_human` with the exact `prompt` (the four-point checklist: local flights updated, pre-mortem docs, strings merged by CCD-7, features default-OFF/approved). Put that `prompt` in front of the owner via `m_ask_user`. Only on confirmation: `done --release <id> --step confirm_reminders --note "<what they confirmed>"`. If they can't confirm, leave it holding.

## `lockdown` — CCOA overlap check (gather-then-decide, browser)

A **two-hop scout step**: you scrape an AAD-gated source, the engine decides overlap deterministically. When `lockdown` is a pending hold — handle silently unless there's an overlap:
1. **Get the gather directive.** `step-action --release <id> --step lockdown` → `needs_skill` with `payload._gather` (`url`, `window` = CCD‑7…CCD+14, `instructions`) and `payload.followup_command`.
2. **Scrape.** Navigate (Playwright) to `payload._gather.url`. On the AAD picker, click the user's own account (Windows-SSO, no password). Wait for "CCOA Periods".
3. **Extract.** From **"Upcoming CCOA periods"** and the current-year **"Past NoFly Zones"** table, read each row's Name, Environment, Start (UTC), End (UTC). Build `[{"name","environment","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}, …]`.
4. **Let the engine decide + record.** Run the follow-up: `check-lockdown --release <id> --periods-json '<json>'` — computes the window, keeps **Production**-env periods, checks overlap, and records: **pass** (no overlap → done) or **attention** (overlap → holds). *(The decision is deterministic — never decide overlap yourself.)*
5. **Relay.** Pass → continue (`next`). Attention → name the overlapping lockdown(s) + window; tell them to **shift CCD** past it (`set-ccd`) if they want to proceed (no partners to notify here).

Can't reach browser/SSO? Leave the step held — don't mark it done without running the check.

## `vitals` — Play Console attestation

Play Console has no API for Policy issues/warnings (Reporting API covers only technical vitals; the Console UI is behind a Google login Scout can't automate). When current step `vitals`, run `step-action --release <id> --step vitals` → `needs_human` with the `prompt` (review **Android vitals** crash/ANR + **Policy status** issues/warnings). Put that `prompt` in front of the owner via `m_ask_user`. On confirmation: `done --release <id> --step vitals --note "<what they saw>"`. Unresolved policy issue / vitals regression → leave holding.

## Automated Phase-0 steps (real agents, no scout action)
`breaking` (BREAKING-OneAuth scan + draft comms), `cg` (Component Governance alerts — blocks on High/Critical), `cron` (Calendar Checker pipeline scheduled), `wiki` (create payload wiki subpage). These run inside `next`; you just relay their results from the `status` table. `cg`/`cron` may **block** — see the core "blocked step" handling.
