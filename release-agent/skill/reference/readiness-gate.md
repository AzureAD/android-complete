# Reference — Readiness ENTRY GATE

_Loaded on demand by the core skill. The entry gate runs right after `init`, before Phase 0._

Immediately after `init`, the very first thing is the **readiness checklist** — the entry gate. The engine's `next` refuses to run any step (reports `readiness_gate`) until it's cleared. **Every item is equally required** — there is no priority or "hard vs soft" distinction. The only difference between items is **who resolves them**:

- **`auto`** — **Scout resolves it** (verifies programmatically, pass/fail). Two execution sources, both shown as `[auto]`:
  - *Python-verified* (default): `build_access` (both ADO build definitions, via `az`) and `mcp_servers` (ICM + Kusto/ADX MCP servers registered in Scout). The engine's `verify`/`sign` runs these.
  - *Scout-assisted* (`source: scout`): the engine can't reach the MCP/Scout-settings, so **YOU run the check** and record the result (step 3). Fail-closed **except the opt-out items `silent_perms` and `teams_notify`**. Today: `oncall_now` (ICM current on-call), `adx_access` (Kusto `print 1`), `silent_perms` (Scout permissions allow unattended runs), `teams_notify` (Scout Teams bot reachable for the digest — degrades to email-only).
- **`attest`** — **the engineer resolves it** (confirms): `play_console_access`, `oncall_window`, `saw_ame`, `yubikey`.

**On-call is TWO items (hybrid)** because Scout only sees the *current* rotation, not the future:
- `oncall_now` (**auto/ICM**) — on-call *right now*? Scout verifies from ICM.
- `oncall_window` (**attest**) — free across the release window **CCD‑7 → CCD+14**? Scout can't read the future rotation, so you attest it (dates shown in the table).

If any item is unsatisfied the gate stays closed. If the engineer can't satisfy an attest item, they resolve it or hand the release to someone who can — same for every item. Never describe any item as "not a hard block" or "optional."

## Flow

> **DISPLAY MODEL — read this.** The gate has ONE table render, and it comes **AFTER every automated check is evaluated** — so the user sees a **complete** picture (all `[auto]` items resolved to ✅/❌), never a half-evaluated table. All the auto checks in steps 1–3 run **silently** (no table, no partial renders). Then step 4 renders the full table once, immediately followed by the deterministic `m_ask_user` card. The **`m_ask_user` card is the guaranteed source of truth** (a Scout UI card, always rendered); the table is the rich view shown alongside it.

**Steps 1–3 are SILENT — do NOT paste any table or partial status while running them.** Narrate at most one short line; don't show the checklist until step 4.

1. **Silent: run the Python auto-verifiers.** `verify --release <id>` (resolves `build_access`, `mcp_servers`; prints terse `[OK]/[FAIL]` — don't paste). No table yet.

2. **Silent: enable unattended (silent) runs** (`silent_perms`). The daily digest, Teams reminders, browser (CCOA/lockdown) checks, and the on-call/telemetry (ICM + Kusto MCP) checks run without focus, so they must not stall on a prompt. Call **`m_get_settings`**, read `permissions.servers` **silently — never paste raw JSON**. Satisfied when ALL `required_servers` (`shell`, `workiq`, `playwright`, `kusto`, `icm`) are **Allow** (`autoApprove: true`). **Never hard-blocks.**
    - **Already all on Allow** → `record-check --release <id> --item silent_perms --status pass --detail "shell/workiq/playwright/kusto/icm auto-approved"`. Continue silently.
    - **One or more NOT on Allow** → offer via `m_ask_user`: **"Enable silent runs (recommended)"** vs **"Proceed without — I'll get occasional prompts"**. Explain the downside: those background checks pop a prompt when Scout isn't focused and can stall until you open Scout.
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
        - Only the **primary** blocks. If ordering is ambiguous, **ask** "primary or backup?" — never block a backup.
    - **`adx_access` (Kusto):** `kusto_query` with the item's `cluster_uri`+`database` (from `checklist --json`), query `print 1`.
        - Succeeds → `record-check --release <id> --item adx_access --status pass --detail "print 1 succeeded"`.
        - Fails → `--status fail --detail "<error>"`.
    - A `fail` on `oncall_now`/`adx_access` keeps the gate closed — resolve or hand off. Do NOT attest these; they're `auto` items.
    - **`teams_notify` (Scout Teams bot):** verifies the daily digest can also reach the user over Teams (email is the guaranteed channel; Teams is a bonus). Read `config/notifications.yaml` (or the `tick --json` payload's `channels`):
        - **`channels.teams` is OFF** → Teams isn't requested: `record-check --release <id> --item teams_notify --status pass --detail "teams channel disabled — email only"`.
        - **`channels.teams` is ON** → call **`m_relay_status`**; if not `connected`, call **`m_relay_connect`** and re-check. Then send a silent handshake via **`m_send_teams_message`** (e.g. "✅ Scout Teams notifications are set up for your release digests.").
            - Relay connected AND the handshake sends → `record-check … --item teams_notify --status pass --detail "relay connected; Scout bot reachable"`.
            - Relay won't connect, OR the handshake fails/404s (the Scout bot has no conversation yet — the user has never messaged it) → `record-check … --item teams_notify --status degraded --detail "Teams unreachable — digest will use email only"`. **`degraded` satisfies the gate** (shows ⚠️). Tell the user once: *"I couldn't reach the Scout Teams bot, so your release digest will come by email only. To also get it in Teams, open the Microsoft Scout chat in Teams and send it any message once, then it'll work next time."* Do NOT block.

4. **NOW render the fully-evaluated gate — the ONE table.** Every `[auto]` item is resolved, so the table shows the complete picture (auto items ✅, only `[attest]` items Outstanding). Run `checklist --release <id>` and **paste its full stdout verbatim as live markdown — NOT fenced, and do NOT summarize it into a plain list.** This is the gate presentation the user has been waiting for; it must be the actual table. Then immediately continue to the attestation card (step 5) in the same message.

5. **Present the attestation as a DETERMINISTIC `m_ask_user` card (the guaranteed source of truth).** Run `checklist --release <id> --attest-prompt --json` → `{ready, question, answers, confirm_items, recommendedIndex}` (the exact card content, engine-built):
    - `ready:false` → auto checks aren't all done (the `reason` says which). Go back and finish steps 1–3; do not prompt.
    - `ready:true` → call **`m_ask_user`** passing the returned `question`, `answers` (use each `title`+`description` verbatim as the answer cards), and `recommendedIndex`. **Do not hand-write or reword these** — the engine's strings are the canonical presentation, so the user gets a complete, actionable gate even if the table above didn't render.

   > ⛔ **NEVER assume attestation.** Only act on the option the user actually clicks. Do not narrate "All four confirmed" or run `sign` until they pick. Attesting on an assumption is a release-integrity violation.

6. **Map the card answer to the engine (using the payload's `action`/`item`):**
    - User picks the **`confirm_all`** answer → `sign --release <id>` with one `--item <id>` for **each** id in `confirm_items`, plus `--note "confirmed via readiness card"`. (No `--all` — list each; the CLI refuses a bare `sign`.) Then `next` to begin Phase 0.
    - User picks a **`decline`** answer → `decline --release <id> --item <that answer's item>`. Gate blocks (step 7).

7. **Can't satisfy an attest item** → `decline --release <id> --item <id>` (repeat per item). Gate blocks. Tell them plainly: the release can't start until it's resolved; if they can't, hand off to another engineer (notify their manager / release team). Treat every item the same.

8. **An `auto` item FAILs** (no build-def access, or you're on-call) → gate stays closed — a real problem to resolve, not something to attest around.

**Invariants:** Never attest an `auto` item on the user's behalf. Never hand-edit/regenerate the checklist — always show the CLI's literal output.
