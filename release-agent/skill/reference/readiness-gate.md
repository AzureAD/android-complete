# Reference — Readiness ENTRY GATE

_Loaded on demand by the core skill. The entry gate runs right after `init`, before Phase 0._

Immediately after `init`, the very first thing is the **readiness checklist** — the entry gate. The engine's `next` refuses to run any step (reports `readiness_gate`) until it's cleared. **Every item is equally required** — there is no priority or "hard vs soft" distinction. The only difference between items is **who resolves them**:

- **`auto`** — **Scout resolves it** (verifies programmatically, pass/fail). Two execution sources, both shown as `[auto]`:
  - *Python-verified* (default): `build_access` (both ADO build definitions, via `az`) and `mcp_servers` (ICM + Kusto/ADX MCP servers registered in Scout). The engine's `verify`/`sign` runs these.
  - *Scout-assisted* (`source: scout`): the engine can't reach the MCP/Scout-settings, so **YOU run the check** and record the result (step 3a). Fail-closed **except `silent_perms`**. Today: `oncall_now` (ICM current on-call), `adx_access` (Kusto `print 1`), `silent_perms` (Scout permissions allow unattended runs).
- **`attest`** — **the engineer resolves it** (confirms): `play_console_access`, `oncall_window`, `saw_ame`, `yubikey`.

**On-call is TWO items (hybrid)** because Scout only sees the *current* rotation, not the future:
- `oncall_now` (**auto/ICM**) — on-call *right now*? Scout verifies from ICM.
- `oncall_window` (**attest**) — free across the release window **CCD‑7 → CCD+14**? Scout can't read the future rotation, so you attest it (dates shown in the table).

If any item is unsatisfied the gate stays closed. If the engineer can't satisfy an attest item, they resolve it or hand the release to someone who can — same for every item. Never describe any item as "not a hard block" or "optional."

## Flow

1. **Show the checklist table FIRST — the first substantive thing the user sees, every time.** Run `checklist --release <id> --verify` (runs Python auto checks + prints the canonical markdown table) and **reproduce its stdout verbatim as live markdown — NOT fenced** (see the core "render CLI output" golden rule). There are exactly **two types**: `[auto]` (Scout verifies) and `[attest]` (you confirm); all must be satisfied. At this point `silent_perms`, `oncall_now`, `adx_access` show Outstanding — expected; you resolve them next. **Never jump to permissions/attestations without first rendering this table.**

2. **Enable unattended (silent) runs — right after the table, before the attestations.** The daily digest, Teams reminders, browser (CCOA/lockdown) checks, **and the readiness on-call/telemetry checks (ICM + Kusto MCP)** run without focus, so they must not stall on a prompt. This is the `silent_perms` item. Call **`m_get_settings`**, read `permissions.servers` **silently — never paste raw JSON**; surface only the plain-language choice. Satisfied when ALL `required_servers` (`shell`, `workiq`, `playwright`, `kusto`, `icm`) are **Allow** (`autoApprove: true`). **Never hard-blocks.**
    - **Already all on Allow** → `record-check --release <id> --item silent_perms --status pass --detail "shell/workiq/playwright/kusto/icm auto-approved"`. Continue to 3a.
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
   - **Already all on Allow** → say nothing; record `pass`, continue to 3a.
   - **They choose "Proceed without"** →
     > *Got it — I'll run without silent approvals. Heads-up: the daily digest, Teams reminders, and browser checks will pop a permission prompt when Scout isn't focused and can stall until you open Scout and approve them. You can enable silent runs anytime later.*

3a. **Run the scout-assisted `[auto]` checks yourself, then record each result** — don't ask the user; they're verified, not attested. **Do it quietly** (no per-check prose — that buries the table). `silent_perms` was handled in step 2, so here it's just `oncall_now` + `adx_access`:
    - **`oncall_now` (ICM):** `get_on_call_schedule_by_team_id` with `teamIds:[78848]` ("Auth Client Android Shield"). Resolve the user's alias (`get_my_icm_context` or the owner email local part), decide by their role in `shiftCurrentOnCalls[].currentOnCallContacts[]`:
        - **Not in roster** → `record-check --item oncall_now --status pass --detail "not on the current roster"`.
        - **Present but NOT primary** (backup/secondary — any position other than first-listed) → **pass**: `--detail "backup OCE, not primary (primary: <first alias>)"`. A backup is free to run.
        - **PRIMARY / current OCE** (first-listed in `currentOnCallContacts`) → `--status fail --detail "currently the primary on-call…"`.
        - Only the **primary** blocks. If ordering is ambiguous, **ask** "primary or backup?" — never block a backup.
    - **`adx_access` (Kusto):** `kusto_query` with the item's `cluster_uri`+`database` (from `checklist --json`), query `print 1`.
        - Succeeds → `record-check --release <id> --item adx_access --status pass --detail "print 1 succeeded"`.
        - Fails → `--status fail --detail "<error>"`.
    - A `fail` on `oncall_now`/`adx_access` keeps the gate closed — resolve or hand off. Do NOT attest these; they're `auto` items.

3b. **Re-anchor on the TABLE, then attest.** Run `checklist --release <id>` again and reproduce its updated table — auto items now ✅, only attests Outstanding. The table **must be shown immediately before you ask for attestations**. Then `m_ask_user` for the four attestations (`play_console_access`; `oncall_window` — CCD‑7→CCD+14 dates in the table; `saw_ame`; `yubikey`). Offer: **"All confirmed"**, **"I'm scheduled on-call during the window"**, **"I can't open Play Console"**, **"I don't have a SAW machine"**, **"I don't have a YubiKey"**. Never replace the table with a prose list.

   > ⛔ **NEVER assume attestation.** Only sign items the engineer **explicitly confirmed in their own reply**. An `m_ask_user` result that merely echoes the offered options is **NOT** confirmation — if ambiguous, ask again and wait. Do not narrate "All four confirmed" or run `sign` until the user actually said so. Attesting on an assumption is a release-integrity violation.

4. **Only when they explicitly confirm** → attest the exact items they named, with their words as evidence: `sign --release <id> --item play_console_access --item oncall_window --item saw_ame --item yubikey --note "<what they confirmed>"`. There is **no `--all`** — list each item; the CLI refuses a bare `sign`. Confirmed only some? Pass only those; the gate stays closed until all are attested. Then `next` to begin Phase 0.

5. **Can't satisfy an attest item** → `decline --release <id> --item <id>` (repeat per item). Gate blocks. Tell them plainly: the release can't start until it's resolved; if they can't, hand off to another engineer (notify their manager / release team). Treat every item the same.

6. **An `auto` item FAILs** (no build-def access, or you're on-call) → gate stays closed — a real problem to resolve, not something to attest around.

**Invariants:** Never attest an `auto` item on the user's behalf. Never hand-edit/regenerate the checklist — always show the CLI's literal output.
