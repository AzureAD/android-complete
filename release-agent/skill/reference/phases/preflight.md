# Reference — Phase 0 (Pre-flight & Code Complete)

_Loaded on demand when advancing Phase 0. Phase 0 is `execution: parallel`._

## Parallel phases — process ALL the holds, not one at a time

A single `next` runs **every independent automated step at once** (breaking, CG, cron — all in one call) then surfaces **all the human/scout holds together** (e.g. *"4 item(s) need you: …"*). After `next`, read `status --json` → **`pending_human`** (and `active_phase.steps` with `status`/`needs_owner`) — the full outstanding set. Work through **all** of them this pass:
- **`source: scout`** steps → notices use notification prepare/claim/result; lockdown keeps its browser + `check-lockdown` follow-up (below). Independent — do them all.
- **`attest`** steps (confirm_reminders, vitals) → ask the owner to confirm, then `done --step <id>`.
- **`blocked`** steps (cg/cron on a real problem) → show the note; fix + rerun, or skip.

Dependencies still hold: `confirm_reminders` only appears **after** `flight_reminder` is sent. Call `next` again after clearing holds to surface newly-ready steps and advance.

> **State writes are safe to parallelize.** The CLI serializes every state read-modify-write per release with an exclusive lock, so firing several `record-step`/`record-check`/`done` calls at once (or an hourly `tick` overlapping) can't clobber — a second invocation waits for the first to save.

**Render the table ONCE per advance pass — at the END.** Within a single pass, do the work first: run `next`, execute every resulting scout step using its notification or domain protocol and clear the attest holds, THEN paste the `status` table once to show the settled state (see the presenting-status reference). Don't paste an interim table before/while you run the scout steps — that early render is stale the moment you act and just duplicates the final one. One pass → one table. (The only exception is the golden rule: if this pass ends by asking for an attestation/gate decision, that final table must be in the same message as the `m_ask_user`.) Never a bare prose list.

## `notice` & `flight_reminder` — shared notification delivery

Both are co-located step modules (`steps/preflight/`). `step-action` is a read-only preview,
not permission to send. Use the shared protocol in `commands.md`:

```
python -m orchestrator.cli notification prepare --release <id> --source step --phase preflight --step <notice|flight_reminder> [--param variant=update]
```

1. Review each eligible descriptor's exact destination/payload. `notice` targets the configured
   DL; `flight_reminder` targets the configured Android Core Team chat. Local test redirects
   (`send_to`) apply before hashing. A wording variant never authorizes a second acknowledged send.
2. **Claim:** `notification claim --release <id> --id <notification-id> --hash <approved-hash> --executor <session-id>`.
3. Only when the claim returns **`permission_to_send:true`**, execute its returned `tool`/`payload`
   verbatim. Never send raw `step-action` output or override recipients.
4. **Acknowledge:** `notification result --release <id> --id <notification-id> --execution-id <execution-id>
   --outcome sent --evidence "<provider-confirmed success>" [--receipt-file <JSON>]`.
   This completes the owning step; legacy `record-step` is not send evidence. Positive proof of
   no delivery permits `not_sent`; unknown outcomes are `uncertain`, never automatically retried.
   Retry acknowledgement after a failed ack, not the send.

Discover saved work (`notification prepare --release <id> --source pending`) even after silence.
Every worker exit runs cleanup; delete the live automation before deregistering it.

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
`breaking` (BREAKING-OneAuth scan + draft comms), `cg` (Component Governance alerts — blocks on High/Critical), `cron` (Calendar Checker pipeline scheduled). These run inside `next`; you just relay their results from the `status` table. `cg`/`cron` may **block** — see the core "blocked step" handling.
