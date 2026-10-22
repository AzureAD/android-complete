# Reference — Readiness ENTRY GATE

_Loaded on demand by the core skill. The entry gate runs right after `init`, before Phase 0._

> **User asks ABOUT a gate item** ("what does build_access check?", "why do I need YubiKey?", "how do I get Play Console access?", "who fixes this?", "what if I'm the primary on-call?"): run `gate-info --item <id>` and answer from it — do NOT guess. It returns the item's what/who/where/how/links/FAQs (curated in `config/knowledge.yaml` under `readiness.<id>`). If it says "no knowledge entry yet", say so rather than inventing. Item ids: build_access, mcp_servers, m365_access, ccd_confirmed, silent_perms, teams_notify, adx_access, oncall_now, play_console_access, oncall_window, saw_ame, yubikey.

Immediately after `init`, the very first thing is the **readiness checklist** — the entry gate. The engine's `next` refuses to run any step (reports `readiness_gate`) until it's cleared. **Every item is equally required** — there is no priority or "hard vs soft" distinction. The only difference between items is **who resolves them**:

- **`auto`** — **Scout resolves it** (verifies programmatically, pass/fail). Two execution sources, both shown as `[auto]`:
  - *Python-verified* (default): `build_access` (both ADO build definitions, via `az`) and `mcp_servers` (ICM + Kusto/ADX MCP servers registered in Scout). The engine's `verify`/`sign` runs these.
  - *Scout-assisted* (`source: scout`): the engine can't reach the MCP/Scout-settings, so **YOU run the check** and record the result (step 3). Fail-closed **except the opt-out items `silent_perms` and `teams_notify`**. Today: `oncall_now` (ICM current on-call), `adx_access` (Kusto `print 1`), `ccd_confirmed` (CCD reconciled with pipeline 3038's override), `silent_perms` (Scout permissions allow unattended runs), `teams_notify` (Scout Teams bot reachable for the digest — degrades to email-only).
- **`attest`** — **the engineer resolves it** (confirms): `play_console_access`, `oncall_window`, `saw_ame`, `yubikey`.

**On-call is TWO items (hybrid)** because Scout only sees the *current* rotation, not the future:
- `oncall_now` (**auto/ICM**) — on-call *right now*? Scout verifies from ICM.
- `oncall_window` (**attest**) — free across the release window **CCD‑7 → CCD+14**? Scout can't read the future rotation, so you attest it (dates shown in the table).

If any item is unsatisfied the gate stays closed. If the engineer can't satisfy an attest item, they resolve it or hand the release to someone who can — same for every item. Never describe any item as "not a hard block" or "optional."

## Microsoft 365 capabilities before release entry

Machine setup first checks a fresh actual-tool inventory through
`infra --no-register --tools-file <inventory.json> --json`, including the discovered
`structured_fetch` descriptor separately when CLI fetch is used. Its `tools_available` result
does NOT satisfy `m365_access`, prove a live/authenticated MCP session or authorize
any write. Keep the release-bound probes below even after setup prerequisites pass.
If a skill-only reload occurred, do not assume it configured missing connectors.

`m365_access` is a scout-assisted auto item in the full checklist. There is no
blanket send/upload authorization item at entry. The four human attestations are
Play Console access, availability during the release window, SAW + AME, and YubiKey.
The engine batches at most four attestations per prompt to keep its confirmation
plus decline choices within the host's five-answer limit. Confirm only the
returned `confirm_items`; if `remaining_items` is nonempty, render fresh readiness
and obtain the next card's separate answer. Do not start Phase 0 until signed.

Run `m365-capabilities --release <id>` and inspect the actual loaded tool
inventory, not registered server names or `az login`. Execute its exact read-only
requests for owner/profile, calendar, exact configured roster and complete member
pagination, representative Teams chat/owner membership, and report site/library.
The custom remote Hosted WorkIQ registration stays removed; logical `workiq.fetch`
is still required through an actual exposed MCP tool OR the selected existing
profile's native WorkIQ CLI. Inspect actual tools first; load
`agency-workiq-workiq` instructions if useful. If fetch is absent from MCP tools,
use `infra --json`/`onboarding --json` per-profile CLI discovery and `fetch --help`.
CLI help is syntax proof, never live access proof. Execute exact planned URLs
with `workiq-read --scout-root <selected-existing-profile>
[--workiq-path <existing-workiq-launcher>] --account <verified-owner>
--entity-url '<exact Graph-relative URL>'`; preserve wrapper argv, never shell
interpolate or bypass its runner. Missing launcher/version support holds with
diagnostic/explicit-launcher guidance, not an unconditional unavailable claim.
Replan after exact group/site reads for `roster_next` and `report_library` requests
using observed IDs. Current configured roster is androididentity@microsoft.com;
do not substitute historical targets. People search and WorkIQ Ask are not
substitutes for group membership or verified mention identities.

For each call save `{version:1,tool:<canonical requested name>,arguments:<exact
requested arguments>,account:<verified owner>,observed_at:<actual offset-aware
observation time>,result:<complete structured response>}`. The final temporary
evidence is `{binding:<plan binding>,available_tools:{<canonical>:<actual loaded
tool name>},structured_fetch:<actual descriptor when needed>,
observations:{profile,calendar,roster:[<group/member page envelopes>],chat,members,sharepoint,report_library:[<library page envelopes>]}}`.
Use the planner's actual observation keys and page requests. Both fetch routes
record `tool:"workiq.fetch",arguments:{entityUrls:[<exactPath>]}` and the actual
transport route where available. A CLI descriptor is not an `available_tools`
mapping. Auxiliary built-in SharePoint reads retain their own tool/arguments
and response shape; listing Documents alone cannot pass every Graph site/drive check.
Run `record-check --release <id> --item m365_access
--status pass --evidence-file <file> --detail "exact structured reads verified"`.
The engine rejects bare passes, missing tools, wrong account/targets, partial or
failed reads, and observations older than 15 minutes or future-dated. Keep only
compact summaries/digests; remove temporary private responses after acknowledgement.

Missing or denied capabilities record `fail` and hold entry until setup/consent is
corrected. Never invent an endpoint, switch accounts or use raw Azure CLI Graph
as a workaround. Setup registers only declared supported MCP connections through
`infra`. Existing conflicts or disabled entries require review; do not overwrite them. Registration/reload is
not authenticated access proof; sign-in/consent remains host-owned, never tokens
written into a file. Do not ask the owner to inspect or fill out Add Server.
Future invitation/chat destinations are verified again when created.
The compact account/targets/route/how-to recipe saved under the selected profile
after onboarding is not permission or fresh evidence. Rediscover and reread;
never import a cached setup pass into release readiness.

Reads do not prove write permission. Check actual destination access and
authorization at each owning write step (send/upload), with its exact
destination/payload preview, required confirmation, host consent and owning receipt.
Neither readiness nor silent-run settings grant blanket approval. A denial holds
that step; never claim permission from a successful read or switch transports after
an unknown outcome. Do not send test messages/invitations or upload test files just
to satisfy readiness. Native `workiq_upload_file` only replaces existing items.
New immutable report creation uses the checked SharePoint browser upload and its
exact owning receipt, never an invented uploader.

## Flow

**Start includes entry-gate execution, not just initialization.** After `init` and
required reminder registration, run this flow immediately in the same user turn.
Do not return "Started" with only `build_access`/`mcp_servers` evaluated. Future
pre-flight/CCD dates defer phase work, not readiness. Machine onboarding results
and a saved routing recipe do not replace fresh release-bound checks.

Every entry surface uses the same queue: `readiness_dispatch` on init/status/next,
`dispatch` on checklist JSON, and the top-level attestation payload. Python-only
`checklist --verify` also prints this queue; it is NOT readiness completion.
Read `checklist --release <id> --attest-prompt --json` while executing.
The payload carries actionable `pending_auto_checks` (id/source/verifier/probe/
record_command/input_command), `failed_auto_checks`, `input_required_auto_checks`,
`next_action`, `continue_automatically` and `may_return_to_user`:
- `run_auto_checks` / `continue_automatically:true`: perform all available pending
  checks NOW. Python-source checks use `verify`; Scout-source checks use the exact
  procedures below and the M365 section above. Record each actual result. Re-read
  the payload after changes. Do not prompt the owner to run Scout's checks.
- `resolve_auto_checks`: checks were attempted but need repair. Do not blindly
  repeat failed reads or fabricate passes. Explain the actual failure and required fix.
- `resolve_readiness_block`: the gate is declined/blocked; ask for the actual repair
  rather than overriding it or starting phase work.
- `resolve_auto_input`: no automatic probe remains executable; ask for the recorded
  exact consent/input. `needs_input` never satisfies a check or signs readiness.
- `attest` / `ready:true`: all auto checks are satisfied; paste `display_markdown`
  and ask the returned exact human confirmation card.
- `complete`: no confirmations remain; advance using the normal engine flow.

Attempt ALL independent pending checks before stopping for an input or failure.
When a probe discovers a user decision is needed, record it with
`record-check --item <id> --status needs_input --detail "<actual missing input/consent>"`.
Continue every independent probe, then run
`checklist --release <id> --attest-prompt --handoff --json`.
Exit 2 / `may_return_to_user:false` refuses the handoff: execute remaining probes,
not a bare retry of the guard. An actual host/tool consent prompt must still be
honored immediately; never bypass it, nor continue after calling `m_ask_user`.
At a permitted question, show the current table then resume the SAME dispatch
loop after the answer. Explicit halt/cancellation/runtime-integrity holds stop dispatch.
Do not
turn `ready:false`, successful reminder registration, or the final-table rule into
permission for a premature end-of-turn summary.

> **DISPLAY MODEL — mandatory at every handoff.** Run automatic checks quietly within
> the current iteration, then paste ONE full current checklist as live markdown before
> returning or asking the user anything. The checklist supplies context; the card
> collects a decision and never replaces the table. Normally the checks finish first.
> If a permission, missing input, failed check or interruption requires an earlier pause,
> show the current checklist with pending/failing rows at that boundary instead.
> A turn is not complete merely because the tool printed a table or a card appeared.

**Steps 1–3 are quiet only while execution continues within the same iteration.**
Do not render after every tool call. At every final response or `m_ask_user` boundary,
fetch and paste the current checklist once, even if checks remain unresolved.

1. **Silent: run pending Python auto-verifiers.** Use the queued `verify --release <id> --pending-only` (resolves `build_access`, `mcp_servers` without retrying recorded failures). The output reports unperformed Scout probes as PENDING, not FAIL. Continue that queue; no final table yet.

2. **Silent: enable unattended (silent) runs** (`silent_perms`). The daily digest, Teams reminders, browser (CCOA/lockdown) checks, and the on-call/telemetry (ICM + Kusto MCP) checks run without focus, so they must not stall on a prompt. Call **`m_get_settings`**, read `permissions.servers` **silently — never paste raw JSON**. Satisfied when ALL `required_servers` (`shell`, `workiq`, `playwright`, `kusto`, `icm`) are **Allow** (`autoApprove: true`). **Never hard-blocks.**
    - **Already all on Allow** → `record-check --release <id> --item silent_perms --status pass --detail "shell/workiq/playwright/kusto/icm auto-approved"`. Continue silently.
    - **One or more NOT on Allow** → record `needs_input` with the exact missing approvals, finish independent probes, then at a permitted handoff offer via `m_ask_user`: **"Enable silent runs (recommended)"** vs **"Proceed without — I'll get occasional prompts"**. Explain the downside: those background checks pop a prompt when Scout isn't focused and can stall until you open Scout.
        - **Enable** → if `permissions.allowModelPermissionsChange` is `false`, ask them to turn on **Settings → Permissions → "When blocked, let Scout ask instead of stopping"** (read-only from the model — only they can flip it). Once `true` (often already on), call **`m_request_permission_escalation`** with `servers: { workiq:{autoApprove:true}, playwright:{autoApprove:true}, kusto:{autoApprove:true}, icm:{autoApprove:true} }` (add `shell` if off); they click **Allow** once → re-read `m_get_settings` → `record-check … --status pass --detail "enabled silent runs"`.
        - **Proceed without** (or won't flip the switch) → `record-check … --item silent_perms --status degraded --detail "proceeding without silent runs — …will prompt & may stall"`. **`degraded` satisfies the gate** (shows ⚠️ *Proceeding (not silent)*).

   **Canonical silent-runs wording** (near-verbatim; no jargon, never show raw JSON):
   - **Master switch ON, WorkIQ/browser not on Allow** →
     > *Before we go further, I'd like to enable **silent runs** so the release keeps working when Scout isn't in focus. The daily status digest, Teams reminders, the browser (CCOA/lockdown) checks, and the on-call/telemetry (ICM + Kusto) checks all run on a background schedule — if they aren't auto-approved, they hit a permission prompt and stall until you open Scout. I can set that up now: I'll request auto-approval for **WorkIQ** (email + Teams), the **browser**, and the **ICM + Kusto** servers, and you just click **Allow** once. Want me to go ahead?*
     Chips: **"Enable silent runs (recommended)"** · **"Proceed without — I'll get occasional prompts"**. On Enable, go straight to `m_request_permission_escalation`.
   - **Master switch OFF** (`allowModelPermissionsChange:false`) →
     > *To enable silent runs I need to request auto-approval for WorkIQ and the browser — but first Scout needs one switch turned on that only you can flip: **Settings → Permissions → "When blocked, let Scout ask instead of stopping."** Once that's on, tell me and I'll request the approvals (you'll click **Allow** once). Prefer not to? I'll record this as running without silent approvals — you'll just get an occasional prompt when Scout isn't focused.*
     Chips: **"I turned it on — go ahead"** · **"Proceed without — I'll get occasional prompts"**.
   - **Already all on Allow** → say nothing; record `pass`, continue silently.
   - **They choose "Proceed without"** →
     > *Got it — I'll run without silent approvals. Heads-up: the daily digest, Teams reminders, and browser checks will pop a permission prompt when Scout isn't focused and can stall until you open Scout and approve them. You can enable silent runs anytime later.*

3. **Silent: run the scout-assisted `[auto]` checks and record each** — don't ask the user; they're verified, not attested. **Never ask permission to run the ADX/on-call MCP calls — they're auto-approved.** No table, no per-check prose:
    - **`oncall_now` (ICM):** `get_on_call_schedule_by_team_id` with `teamIds:[78848]` ("Auth Client Android Shield"). Resolve the user's alias (`get_my_icm_context` or the owner email local part), decide by their role in `shiftCurrentOnCalls[].currentOnCallContacts[]`:
        - **Not in roster** → `record-check --item oncall_now --status pass --detail "not on the current roster"`.
        - **Present but NOT primary** (backup/secondary — any position other than first-listed) → **pass**: `--detail "backup OCE, not primary (primary: <first alias>)"`. A backup is free to run.
        - **PRIMARY / current OCE** (first-listed in `currentOnCallContacts`) → `--status fail --detail "currently the primary on-call…"`.
        - Only the **primary** blocks. If ordering is ambiguous, record `needs_input`, finish independent probes, then **ask** "primary or backup?" — never block a backup.
    - **`adx_access` (Kusto):** `kusto_query` with the item's `cluster_uri`+`database` (from `checklist --json`), query `print 1`.
        - Succeeds → `record-check --release <id> --item adx_access --status pass --detail "print 1 succeeded"`.
        - Fails → `--status fail --detail "<error>"`.
    - A `fail` on `oncall_now`/`adx_access` keeps the gate closed — resolve or hand off. Do NOT attest these; they're `auto` items.
    - **`ccd_confirmed` (CCD validity — temporal + pipeline):** run **`check-ccd --release <id> --json`** → `{ccd, override, ccd_conflict, status, days_to_ccd, runway_days, compressed, phase0_open}`. Act on `status` (checked in priority order):
        - **`past`** (CCD is before today — `days_to_ccd` < 0; e.g. a current-month release whose 2nd-Wednesday default already passed) → **BLOCK; must reschedule to a future date.** `--default` won't help (the default IS the past date). If `override` is present AND itself in the future, offer to adopt it; otherwise ask the user for a real future CCD (`m_ask_user`: "CCD `<ccd>` is `<days_to_ccd humanized>` — pick a future Code Complete Date."). Apply with **`set-ccd --release <id> --date <future> --confirm --reason "CCD was in the past"`**, **re-run `check-ccd`** until it's no longer `past`, then continue to whatever status it now reports. Do NOT record pass while `past`.
        - **`conflict`** (pipeline override is an in-month date that DIFFERS) → **surface both to the user** and ask which is right (`m_ask_user`: "Pipeline says `<override>`, this release is anchored to `<ccd>` — which is the real Code Complete Date?"). Then apply their choice with **`set-ccd --release <id> … --confirm --reason "<why>"`** (`--date <override>` to adopt the pipeline, or `--default` to keep the 2nd-Wednesday default), **re-run `check-ccd`** until `match`, then `record-check … --item ccd_confirmed --status pass --detail "reconciled to <ccd>"`. Do NOT record pass while it still conflicts.
        - **`unreadable`** (couldn't read pipeline 3038) → **attest fallback**: ask the user to confirm the CCD by hand ("I couldn't read the pipeline; is `<ccd>` the correct Code Complete Date?"). On yes → `record-check … --item ccd_confirmed --status pass --detail "pipeline unreadable — CCD <ccd> confirmed manually"`. On a different date → `set-ccd … --date <their date> --confirm --reason` first, then record pass.
        - **`match`** (future-dated CCD, no override or override equals it) → `record-check --release <id> --item ccd_confirmed --status pass --detail "CCD <ccd> reconciled with pipeline"`.
        - **`unset`** (release id isn't YYYY-MM, so no CCD) → keeps the gate closed; the release needs a proper month id.
        - **`compressed: true` (WARN, never blocks) — applies on ANY non-past status:** Phase 0 normally runs CCD-7→CCD (7 days) but only `runway_days` remain (Phase 0 opens `phase0_open`). Tell the user once — *"Heads up: your CCD is `<ccd>` (`<days_to_ccd humanized>`), so Phase 0 has only `<runway_days>` day(s) of prep instead of 7. Proceed, or move the CCD out with set-ccd?"* If they move it → `set-ccd … --confirm --reason` then re-check. If they proceed → still `record-check … --status pass --detail "CCD <ccd> — compressed Phase 0 (<runway_days>d), user accepted"`. A squeezed window is a risk to flag, not a blocker.
    - **`teams_notify` (Scout Teams bot):** an interactive setup diagnostic, not a recurring
      release notification. Its one-time relay handshake contains no release payload and
      precedes readiness signing; never invoke it from a scheduled worker or as a delivery
      fallback. It verifies the daily digest can also reach the user over Teams (email is the
      primary channel; Teams is a bonus). Read `config/notifications.yaml` (or the
      `tick --release <id> --json` payload's `channels`):
        - **`channels.teams` is OFF** → Teams isn't requested: `record-check --release <id> --item teams_notify --status pass --detail "teams channel disabled — email only"`.
        - **`channels.teams` is ON** → call **`m_relay_status`**; if not `connected`, call **`m_relay_connect`** and re-check. Then send a silent handshake via **`m_send_teams_message`** (e.g. "✅ Scout Teams notifications are set up for your release digests.").
            - Relay connected AND the handshake sends → `record-check … --item teams_notify --status pass --detail "relay connected; Scout bot reachable"`.
            - Relay won't connect, OR the handshake fails/404s (the Scout bot has no conversation yet — the user has never messaged it) → `record-check … --item teams_notify --status degraded --detail "Teams unreachable — digest will use email only"`. **`degraded` satisfies the gate** (shows ⚠️). Tell the user once: *"I couldn't reach the Scout Teams bot, so your release digest will come by email only. To also get it in Teams, open the Microsoft Scout chat in Teams and send it any message once, then it'll work next time."* Do NOT block.

4. **Render the current gate — the mandatory table.** Run
   `checklist --release <id> --attest-prompt --handoff --json`. Refuse to return while
   exit 2 / `may_return_to_user:false`: exhaust remaining probes first.
   At a permitted handoff, paste its **full `display_markdown`**
   verbatim as live markdown, not fenced or summarized. The engine builds it from the
   same checklist snapshot as the question/answers. Do not also print a second checklist.
   Even if `ready:false`, show this table before ending or asking for a repair.

5. **Then collect the decision with the engine's exact `m_ask_user` card.** Use the
   SAME payload from step 4: `{ready, display_markdown, question, answers, confirm_items,
   remaining_items, recommendedIndex}`.
    - `ready:false` → follow `next_action`. Continue executable `pending_auto_checks`
      in the same turn; show the table and stop only at a genuine repair/input boundary
      or completed entry flow. Do not ask for attestation while checks are incomplete.
    - `ready:true` → only AFTER pasting `display_markdown`, call **`m_ask_user`** with the
      exact `question`, answer `title`/`description` and `recommendedIndex`. The table must
      appear in the same assistant message before the card. Do not hand-write the card,
      send display metadata to the question tool, or say only "choose an option."

   > ⛔ **NEVER assume attestation.** Only act on the option the user actually clicks. Do not narrate "All four confirmed" or run `sign` until they pick. Attesting on an assumption is a release-integrity violation.

6. **Map the card answer to the engine (using the payload's `action`/`item`):**
    - User picks the **`confirm_all`** answer → `sign --release <id>` with one `--item <id>` for **each** id in `confirm_items`, plus `--note "confirmed via readiness card"`. (No `--all` — list each; the CLI refuses a bare `sign`.) Then `next` to begin Phase 0.
    - User picks a **`decline`** answer → `decline --release <id> --item <that answer's item>`. Gate blocks (step 7).

7. **Can't satisfy an attest item** → `decline --release <id> --item <id>` (repeat per item). Gate blocks. Tell them plainly: the release can't start until it's resolved; if they can't, hand off to another engineer (notify their manager / release team). Treat every item the same.

8. **An `auto` item FAILs** (no build-def access, or you're on-call) → gate stays closed — a real problem to resolve, not something to attest around.

After any answer, sign/decline, failure or repair, end the next iteration with the
fresh resulting checklist (or phase status once signed). A prior table or card does
not carry context into a new turn. A request to run pre-flight while entry remains
unsigned must show the checklist and explain that pre-flight has not started, not
return only an acknowledgement or a scheduled-phase table.

**Invariants:** Never attest an `auto` item on the user's behalf. Never hand-edit/regenerate the checklist — always show the CLI's literal output.
