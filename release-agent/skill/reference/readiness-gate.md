# Required readiness and point-of-use capabilities

> **User asks ABOUT a gate item** ("what does `build_access` check?", "why do I need a YubiKey?", "how do I get Play Console access?", "what if I'm on-call?"): run `gate-info --item <id>` and answer from `config/knowledge.yaml` — never guess.

The checklist distinguishes **required release policy** (`required: true`) from **point-of-use integration capabilities** (`required: false`, labeled `"when used"`).
Use `checklist --release <id> --attest-prompt --json` for the exact required checks and outstanding human confirmations. Follow `pending_auto_checks` in the default guided release flow before prompting. Do not run onboarding or every capability probe merely for status, local report inspection, or recording a fact.

## Required vs. `"when used"` items

- **Required (`[auto]` — Scout resolves before Phase 0):**
  - `ccd_confirmed` — Code Complete Date is future-dated and reconciled with pipeline `3038`.
  - `oncall_now` — Release owner is not currently the **primary** Android on-call on ICM team `78848`.
- **Required (`[attest]` — release owner explicitly confirms):**
  - `play_console_access` — Play Console dashboard opens and loads.
  - `oncall_window` — Not scheduled Android on-call across `CCD-7` → `CCD+14`.
  - `saw_ame` — SAW desktop loads and AME sign-in works.
  - `yubikey` — YubiKey in hand.
- **Point-of-use (`required: false`, labeled `"when used"`):**
  - `build_access`, `mcp_servers`, `m365_access`, `silent_perms`, `teams_notify`, and `adx_access`.
  - Their failure is visible in the table when run, but does not block unrelated interactive work. Each consuming step still verifies its actual targets, evidence, and access before executing. Do not turn this distinction into an evidence-free capability pass.

No blanket write authority follows from readiness. Every owning write step needs its own exact preview, required review, host consent, durable attempt, and receipt. Do not send test messages, create probe invitations, upload test files, broaden permissions, or switch accounts to manufacture a readiness pass.

## Step-by-step entry gate execution (run immediately after `init`)

**IMPORTANT — Do NOT probe `"when used"` capabilities during release start!**
Do NOT run `onboarding`, `m365-capabilities`, `verify`, `m_get_settings`, `m_relay_status`, or Kusto during entry readiness unless the user explicitly asked for diagnostics. Execute **ONLY** the required `pending_auto_checks` returned by `checklist --release <id> --attest-prompt --json`:

1. **Execute ONLY the required `pending_auto_checks` (`ccd_confirmed` and `oncall_now`):**
   - **`ccd_confirmed`:** Run `python -m orchestrator.cli check-ccd --release <id> --json` → `{ccd, override, ccd_conflict, status, days_to_ccd, runway_days, compressed, phase0_open}`:
     - **`match`** → `record-check --release <id> --item ccd_confirmed --status pass --detail "CCD <ccd> reconciled with pipeline"` (if `compressed: true`, mention once that `<runway_days>` prep days remain instead of 7 and offer to proceed or shift with `set-ccd`).
     - **`past`** (`days_to_ccd < 0`) → record `needs_input` with the exact reason, finish `oncall_now`, then paste `display_markdown` and present an `m_ask_user` card: if `override` is a future date in the release month, offer **`Use pipeline date <override>`** vs **`Enter a different future date`**; otherwise collect a future `YYYY-MM-DD` date (or offer switching to the next release month), apply `set-ccd --release <id> --date <future> --confirm --reason "CCD was in the past"`, re-run `check-ccd`, and record `pass`.
     - **`conflict`** (`override` differs from `ccd`) → record `needs_input`, finish `oncall_now`, paste `display_markdown`, and offer via `m_ask_user`: **`Use pipeline date <override>`**, **`Keep release default <ccd>`**, or **`Use a different date`**. Apply with `set-ccd ... --confirm --reason "<why>"`, re-run `check-ccd` until `match`, then record `pass`.
     - **`unreadable`** (pipeline 3038 / `az` unreachable) → record `needs_input`, finish `oncall_now`, paste `display_markdown`, and offer **`Confirm CCD <ccd> manually`** vs **`Use a different date`** via `m_ask_user`. On manual confirm → `record-check --release <id> --item ccd_confirmed --status pass --detail "pipeline unreadable — CCD <ccd> confirmed manually by owner"`.
   - **`oncall_now` (ICM):** Call `get_on_call_schedule_by_team_id` (`teamIds: [78848]`, *"Auth Client Android Shield"*) and resolve the owner's alias (`get_my_icm_context` or `owner_email` local part) against `shiftCurrentOnCalls[].currentOnCallContacts[]`:
     - **Not in roster** → `record-check --release <id> --item oncall_now --status pass --detail "not on the current roster"`.
     - **Present as backup/secondary** (not first-listed) → `record-check --release <id> --item oncall_now --status pass --detail "backup OCE, not primary (primary: <first alias>)"`.
     - **First-listed PRIMARY on-call** → `record-check --release <id> --item oncall_now --status fail --detail "currently primary on-call for team 78848"`. (New releases must resolve primary duty or hand off; an **already-started** release whose owner rotated onto primary mid-release may continue only if the owner explicitly approves `override-oncall --release <id> --reason "<owner reason>"`.)
     - **ICM MCP unavailable / disconnected / tool error** (connector issue, NOT a primary-on-call result) → do NOT stall the start! Record `record-check --release <id> --item oncall_now --status needs_input --detail "ICM MCP unavailable (<concise error>); manual confirmation or ICM reconnect needed"`, paste `display_markdown`, and offer via `m_ask_user`: **`Confirm manually — I am NOT current primary on-call`** vs **`Reconnect ICM and retry`**. If the owner explicitly confirms they checked `aka.ms/androidoncall` and are not primary on-call, record `record-check --release <id> --item oncall_now --status pass --detail "ICM connector unavailable — owner explicitly confirmed not current primary on-call"`.
2. **Optional `"when used"` diagnostics (ONLY when explicitly requested by the user):**
   - Use `verify` explicitly only when automatic verification of `build_access` / `mcp_servers` is requested; use `adx_access`, `silent_perms`, `teams_notify`, or `m365-capabilities` only when the user asks to diagnose them or when their consuming step runs.
3. **Guarded handoff, full table display, and exact attestation card:**
   - Run `python -m orchestrator.cli checklist --release <id> --attest-prompt --handoff --json`.
   - `ready:false` can mean executable automatic checks remain (`must_continue_automatic_work:true` / exit code `2`): exhaust them first before prompting. Do not repeatedly retry unchanged `failed_auto_checks`.
   - At a permitted handoff, **always display `display_markdown` verbatim as live Markdown** (never fenced, never condensed) before asking the repair question or calling `m_ask_user`.
   - When `ready:true`, pass the returned `question`, `answers`, and `recommendedIndex` to `m_ask_user` (never assume confirmation):
     - **`confirm_all`** → run `sign --release <id>` with `--item <id>` for each item in `confirm_items` and `--note "confirmed via readiness card"` (`sign` records only named human facts and never probes integrations). If `remaining_items` is non-empty, fetch and display the updated table and ask the next batch; once signed, **immediately continue automatic guided progression** (`next --release <id> --json` + `work_dispatch`) in the same turn!
     - **`decline`** → run `decline --release <id> --item <declined_item> --reason "<reason>"`, paste the updated `checklist` table, and explain the handoff/repair requirement.

## Structured connector reads

Discover actual tools and use the supported canonical-to-exposed mapping. Directory/roster and exact SharePoint resource reads use structured WorkIQ fetch via an exposed MCP fetch tool or the selected profile's verified native CLI:

```powershell
python -m orchestrator.cli workiq-read --scout-root <profile> --account <owner> --entity-url '<exact Graph-relative URL>'
```

The adapter verifies the launcher/wrapper and keeps arguments literal. Reuse its fresh discovery descriptor with `--route-file <temporary-route.json>` for subsequent pages; a changed launcher or stale observation requires rediscovery.

The agent, not the owner, constructs temporary observation envelopes: `{version:1, tool, arguments, account, observed_at, result}`. Bind the exact requested account/resource, complete every pagination page, and preserve provider errors. Tool discovery/help is not access proof; WorkIQ Ask/people search is never roster or identity evidence; never acquire raw Graph tokens or switch transports after denial. Keep private responses outside the repository and delete temporary files when done.

## Optional unattended mode

Unattended startup (`--unattended` / `set-mode --mode unattended`) additionally requires its canonical driver's creation/adoption receipt (`push-reminders`) and actual provider health. Missing or uncertain startup is not success; honor host consent and the owning worker result. Interactive mode is the default and has no mandatory driver.

