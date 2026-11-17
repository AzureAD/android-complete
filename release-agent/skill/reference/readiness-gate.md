# Mandatory Readiness Entry Gate — exhaustive, non-blocking execution

> **User asks ABOUT a gate item** ("what does `build_access` check?", "why do I need a YubiKey?", "how do I get Play Console access?", "what if I'm on-call?"): run `gate-info --item <id>` and answer from `config/knowledge.yaml` — never guess.

Every item in `config/readiness.yaml` is **mandatory** (optional `required: false` items are deprecated and forbidden). Each item is a standalone execution unit declaring `depends_on` (`[]` = runs immediately) and `required_tools`.
Use `checklist --release <id> --attest-prompt --json` to inspect the queue. **If any `[auto]` check fails because a required tool or connector is missing or misconfigured, fail ONLY that specific check with its explicit reason and continue executing every remaining runnable `[auto]` check.** Never halt early on a single check failure!

No blanket write authority follows from readiness. Every owning write step needs its own exact preview, required review, host consent, durable attempt, and receipt. Do not send test messages, create probe invitations, upload test files, broaden permissions, or switch accounts to manufacture a readiness pass.

## Mandatory `[auto]` and `[attest]` items

- **Mandatory `[auto]` checks (Scout evaluates ALL 7 before returning control):**
  - `build_access` (`required_tools: [az]`, Python verifier via `verify`) — Release build definitions `#2828` and `#397224` accessible.
  - `mcp_servers` (`required_tools: []`, Python verifier via `verify`) — ICM, Kusto/ADX, and Teams MCP servers registered in Scout config.
  - `m365_access` (`required_tools: [workiq]`, Python verifier via `verify`) — Profile/calendar, configured roster (all member pages), and Teams membership verified directly in Python via native WorkIQ CLI (`shell=False`, no PowerShell `$filter`/`$select` mangling).
  - `ccd_confirmed` (`required_tools: [az]`, `check-ccd`) — Code Complete Date is valid and reconciled with pipeline `3038`.
  - `teams_notify` (`required_tools: [scout_teams_relay]`, `m_relay_status`) — Scout Teams relay & bot reachability checked (`opt_out: true` allows `degraded` email-only fallback).
  - `adx_access` (`required_tools: [kusto]`, Kusto `print 1`) — ADX release telemetry cluster queryable.
  - `oncall_now` (`required_tools: [icm]`, ICM `teamIds: [78848]`) — Release owner is not currently **primary** Android on-call.
- **Mandatory `[attest]` items (release owner explicitly confirms):**
  - `play_console_access`, `oncall_window` (`CCD-7` → `CCD+14`), `saw_ame`, `yubikey`.

## Step-by-step entry gate execution (run immediately after `init`)

1. **Run Python `[auto]` verifiers (`build_access`, `mcp_servers`, and `m365_access`):**
   - Run `python -m orchestrator.cli verify --release <id> --pending-only`. Python executes all three verifiers directly (`shell=False`, with full automatic Graph pagination for `m365_access`). If any verifier fails, only that item records `fail` with its specific error while the others still run.
2. **Exhaust ALL remaining `pending_auto_checks` from `checklist --release <id> --attest-prompt --json` (do NOT halt on failure):**
   - **`ccd_confirmed` (`az`):** Run `python -m orchestrator.cli check-ccd --release <id> --json`:
     - `match` → `record-check --release <id> --item ccd_confirmed --status pass --detail "CCD <ccd> reconciled with pipeline"` (note if `compressed: true`).
     - `past` / `conflict` / `unreadable` → record `--status fail` (or `needs_input` when awaiting an owner choice between pipeline override vs custom date) with the exact reason, **and continue running the remaining `[auto]` checks first**!
   - **`teams_notify` (`m_relay_status`):** Check `m_relay_status`. If connected/reachable → `pass`; if disconnected or unavailable → record `degraded` (`"Teams relay unavailable — proceeding with email-only fallback"`) or `fail` with the exact reason, and **continue**.
   - **`adx_access` (`kusto`):** Run `print 1` against `cluster_uri` + `database`. On success → `pass`; if `kusto` MCP is missing or errors → record `record-check --release <id> --item adx_access --status fail --detail "Tool/connector failure (kusto): <exact error>"` and **continue**.
   - **`oncall_now` (`icm`):** Call `get_on_call_schedule_by_team_id` (`teamIds: [78848]`). Not in roster or backup → `pass`; primary on-call → `fail` (`"currently primary on-call for team 78848"`; mid-release continuation supports `override-oncall`); if `icm` MCP is missing/disconnected → `record-check --release <id> --item oncall_now --status fail --detail "Tool/connector failure (icm): <exact error>"`.
3. **Present comprehensive results & prompt once ALL runnable checks are exhausted:**
   - Run `python -m orchestrator.cli checklist --release <id> --attest-prompt --handoff --json`.
   - `ready:false` while `continue_automatically:true` (exit code `2`) means runnable `[auto]` checks still remain — finish them first without retrying `failed_auto_checks`.
   - Once all runnable `[auto]` checks have finished, **paste `display_markdown` verbatim as live Markdown** before prompting so the user sees every check's result (`PASS` / `FAIL`) and specific failure reason.
   - If any `[auto]` check failed due to a missing/broken tool, present an `m_ask_user` card alongside the outstanding attestations allowing the owner to **Fix & retry failed check(s)**, **Complete manually (`complete-readiness-manually`)**, **Skip/waive failed check (`skip-readiness --reason ...`)**, and **Confirm human `[attest]` items (`sign --item ...`)**. Once all items are satisfied, immediately continue automatic guided progression (`next --release <id> --json`)!

## Structured connector reads

Discover actual tools and use the supported canonical-to-exposed mapping. Directory/roster and exact SharePoint resource reads use structured WorkIQ fetch via an exposed MCP fetch tool or the selected profile's verified native CLI:

```powershell
python -m orchestrator.cli workiq-read --scout-root <profile> --account <owner> --entity-url '<exact Graph-relative URL>'
```

The adapter verifies the launcher/wrapper and keeps arguments literal. Reuse its fresh discovery descriptor with `--route-file <temporary-route.json>` for subsequent pages; a changed launcher or stale observation requires rediscovery.

The agent, not the owner, constructs temporary observation envelopes: `{version:1, tool, arguments, account, observed_at, result}`. Bind the exact requested account/resource, complete every pagination page, and preserve provider errors. Tool discovery/help is not access proof; WorkIQ Ask/people search is never roster or identity evidence; never acquire raw Graph tokens or switch transports after denial. Keep private responses outside the repository and delete temporary files when done.

## Optional unattended mode

Unattended startup (`--unattended` / `set-mode --mode unattended`) additionally requires its canonical driver's creation/adoption receipt (`push-reminders`) and actual provider health. Missing or uncertain startup is not success; honor host consent and the owning worker result. Interactive mode is the default and has no mandatory driver.

