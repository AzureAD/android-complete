# Starting a release, CCD scheduling & optional unattended operation

## Starting a release — mandatory folder & release-date confirmation BEFORE `init`

**NEVER initialize a release automatically from `/release-agent start a release`, `/release-agent`, or an ambiguous month phrase (e.g. `"nov"`, `"oct 26"`).**
The **release label** always matches the **calendar month of the elected release date** (e.g. a release date in November 2026 has `release_id = "2026-11"` and is labeled **`November`** / **`November 2026`** — never offset by `+1` month). Before calling `init`, you **MUST** explicitly confirm **both** the **`android-complete` folder** and the **elected release date (`YYYY-MM-DD`)** with the user via `m_ask_user`.

### Step-by-step start sequence

1. **Resolve & explicitly confirm `<REPO_ROOT>` (`m_ask_user` #1):**
   - Discover candidate clones where `<X>\release-agent\orchestrator\cli.py` exists (`m_recall`, current working directory/ancestors, `C:\repos\android-complete`, `~\repos\android-complete`, `~\source\repos\android-complete`, `~\git\android-complete`, `~\src\android-complete`, or bounded `$HOME` search).
   - Run `python -m orchestrator.cli paths --json` → `{agent_root, repo_root, runs_root}`.
   - Always confirm the folder before `init` via `m_ask_user`:
     - **`Use <repo_root> (Recommended)`** — *"Run from `<agent_root>` (state in `<runs_root>`)"*
     - **`Use a different folder`** — *"Enter another `android-complete` clone path"*
   - If they choose a different folder, prompt via free-text `m_ask_user`, verify `<path>\release-agent\orchestrator\cli.py` exists, re-run `paths --json`, and persist `<agent_root>` with `m_remember`.
   - Run `python -m orchestrator.cli list --json`. If an active release already exists (`resolution` is `"one"` or `"ambiguous"`), ask via `m_ask_user` whether to continue that existing release or start a different month — existing releases are never overwritten.
2. **Offer the default release date or a custom date (`m_ask_user` #2):**
   - Run `python -m orchestrator.cli preview-release --json` (returns `{release_id, label_month, label, ship_label, ccd, ccd_pretty, ccd_past, is_default, recommended}`).
   - **Default Release Date Rule:**
     - Offer the **2nd Wednesday of the current month** by default (`candidates[0]` when `"ccd_past": false`).
     - If the 2nd Wednesday of the current month has **already passed** (`candidates[0]["ccd_past"] == true`), offer the **2nd Wednesday of the next month** (`rec`, where `"recommended": true`).
   - **Release Labeling Rule:**
     - The release label directly matches the calendar month of the elected release date (e.g., `2026-11-18` → **`November`** / **`November 2026`**; `2026-12-09` → **`December`** / **`December 2026`**). Do NOT add `+1` month.
   - Present an `m_ask_user` option card (`recommendedIndex: 0`):
     1. **`<rec.ccd_pretty> (<rec.ccd>) — <rec.label> release` *(Recommended)*** — if `candidates[0].ccd_past` is `false`: *"2nd Wednesday of this month (`<rec.ship_label>`)"*; if `candidates[0].ccd_past` is `true`: *"2nd Wednesday of next month (`<rec.ship_label>`, since `<candidates[0].ccd_pretty>` already passed)"*.
     2. **`Custom day in <candidates[0].label> (<candidates[0].release_id>)`** — *"Specify any other custom day within `<candidates[0].ship_label>` (labeled `<candidates[0].label>`)"*.
     3. **`Any date in the future`** — *"Specify any custom future `YYYY-MM-DD` release date (labeled for that date's calendar month)"*.
   - When option 2 or 3 is chosen, collect the custom `YYYY-MM-DD` date via free-text `m_ask_user`, verify it falls within the current calendar month OR is in the future (`>= today`), and derive `<id> = <YYYY-MM>` and `<label> = <calendar month of YYYY-MM-DD>`.
3. **Initialize, sync any custom date override, and run ONLY the required Readiness Entry Gate checks in the SAME turn:**
   - Run `python -m orchestrator.cli init --release <id> --json` (adding `--ccd <YYYY-MM-DD>` when a custom date was elected; `init` resolves `owner_email` and `timezone` locally without calling M365/WorkIQ MCP, check `--help` when flags are uncertain, and use `set-owner` if `owner_email` is empty).
   - Guided foreground operation (`interactive`) is the default; no background worker is created unless `--unattended` was explicitly requested.
   - If a custom date (other than the 2nd Wednesday of `<id>`) was elected, apply `set-ccd --release <id> --date <YYYY-MM-DD> --reason "Owner selected release date <YYYY-MM-DD> at release start"` (preview, then `--confirm` when pipeline 3038 is reachable).
   - **Initialization is NOT a stopping point, and you must NOT run `onboarding`, `m365-capabilities`, or `verify` on optional `"when used"` items.** Immediately run `checklist --release <id> --attest-prompt --json` per [readiness-gate.md](readiness-gate.md), execute ONLY its required `pending_auto_checks` (`ccd_confirmed` and `oncall_now`), record real results (using the manual confirmation fallback if ICM MCP or pipeline 3038 is unreachable), fetch `checklist --release <id> --attest-prompt --handoff --json`, paste `display_markdown` verbatim as live Markdown, and prompt with the readiness attestation/repair card.
4. **Continue automatic guided progression after readiness clears:**
   - Once readiness is signed, immediately run `next --release <id> --json` and execute all eligible tasks in `work_dispatch` across phases without asking the owner to say `"next"`.
   - Capabilities marked `"when used"` (`required: false`) do not block unrelated work. Future phase dates (`CCD`, `CCD+1`) defer scheduled phase execution — never move the clock (`--as-of`) or rewrite source state to force early sends.
   - Stop at a genuine human input/approval, scheduled/external wait, or completion. Exhaust independent siblings first, then fetch `status --release <id> --details --handoff --json` and paste its full `display_markdown` table before prompting. Status-only and explicitly one-task requests remain scoped.

## Code Complete Date (CCD) & pipeline conflicts

Phases are anchored to the CCD (`CCD-7`, `CCD`, `CCD+1`). By default, CCD is the **2nd Wednesday** of `<release_id>`.
`init` and `check-ccd` read ADO pipeline `3038` (`overrideCodeCompleteDate`) without silently adopting a differing date:
- If pipeline `3038` has a different in-month date (`status: "conflict"`), present an `m_ask_user` card with **`Use pipeline date <override>`**, **`Keep release default <ccd>`**, and **`Use a different date`**.
- Changing the pipeline CCD is a checked external write: run `set-ccd --release <id> [--date <YYYY-MM-DD> | --default] --reason "<why>"` (preview), then with `--confirm`. Re-run `check-ccd` and record `ccd_confirmed`.

## Guided onboarding on request (`/release-agent check setup`)

`onboarding --json` diagnoses the selected installation and its supported integrations without initializing a release, signing readiness, sending messages, uploading files, or creating workers. Use `--help` for `--scout-root`, `--agency-path`, and `--workiq-path` flags.

1. Run `onboarding --json` from `<AGENT_ROOT>`. Preserve existing connections, authentication, and permissions; obtain required host/user consent before installation or configuration changes. Do not assume source edits synchronized an already-installed skill (`setup/bootstrap.ps1 -SkillOnly` refreshes the installed skill).
2. A `register_mcp` action runs `infra --json` to register declared missing command-based MCP servers automatically (backed-up create-if-absent); never restore the removed custom remote Hosted WorkIQ registration.
3. Discover actual loaded tools; missing MCP fetch does not prove native CLI fetch is absent. Keep verified `structured_fetch` routes (`workiq-read`) separate from `available_tools`. Execute exact owner-bound read requests (`workiq_get_my_profile`, calendar, configured `androididentity@microsoft.com` roster + all pages, Teams chat/members, SharePoint site/library, ICM, Kusto `print 1`) and pass `--observation-file <temp.json>`.
4. After one real reconnect/reload and one retry, an unchanged failure stops with the returned `support` handoff (no retry loop or alternate account). On `complete`, save the compact recipe via `onboarding --observation-file <temp.json> --scout-root <profile> --record-routing --json` and state that no release was started.

No blanket write authority comes from successful setup. Each owning write step still checks its target, required review, access, host consent, and receipt.

## Opt in to unattended progression

Foreground `interactive` mode is the default. Only when the owner explicitly requests unattended background workers, use `init --unattended` or:

```powershell
python -m orchestrator.cli set-mode --release <id> --mode unattended
python -m orchestrator.cli automation obligations --release <id> --json
```

For each required worker (`push-reminders`, `daily-status-email`, and — only **after** `ccd_confirmed` passes — the CCD-pinned timed workers from `automation plan --release <id> --json`, plus state-triggered `--on-demand <slug>` pollers):
1. Write the exact `provider_spec` from `automation plan --json` to a temporary JSON file outside `.release-runs`.
2. Read fresh exhaustive `m_list_automations` + `m_get_automation` details into `{observed_at:<UTC>, complete:true, automations:[{id, spec}]}`.
3. Run `automation reconcile-create --release <id> --slug <slug> --ensure --claim --executor <session> --spec-file <spec.json> --observed-file <obs.json> --json` (adding `--on-demand <slug>` for on-demand pollers).
4. Call `m_create_automation` **only** when `permission_to_create:true` (passing the exact returned `spec`), then immediately acknowledge with `automation create-result`. Reuse an unchanged existing worker; recreate a proven-absent settled worker only through reconciliation (`--ensure`); unknown or uncertain outcomes retain their owning attempt and never authorize duplicate creation.

A saved ID is not a startup receipt or live-provider health; never claim unattended coverage until the driver is acknowledged and healthy, and honor host consent. `set-mode --release <id> --mode interactive` pauses scheduled progression without deleting worker resources or receipts. Cleanup at release close uses `automation cleanup` → `claim-delete` → `m_delete_automation` (only with `permission_to_delete:true`) → `delete-result`. Generated workers use the canonical prompt from `automation plan`, prefix release CLI calls with global `--scheduled`, follow `work_dispatch`, and run `automation cleanup` in a `finally` block.

## No-progress boundary

`next` defaults to pending-only work. Inspect newly eligible host tasks after each result, but never repeat an unchanged failed/owned operation in a loop. Return a concrete wait, missing input, or failure with verified partial success; use `--retry` only after a real repair or explicit reconciliation request.

