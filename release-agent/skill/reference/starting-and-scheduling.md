# Starting a release, CCD scheduling & optional background accelerators

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
   - Release execution uses a single **state-driven hybrid model** — automations are purely optional accelerators and never block initialization or readiness.
   - If a custom date (other than the 2nd Wednesday of `<id>`) was elected, apply `set-ccd --release <id> --date <YYYY-MM-DD> --reason "Owner selected release date <YYYY-MM-DD> at release start"` (preview, then `--confirm` when pipeline 3155 is reachable).
   - **Initialization is NOT a stopping point.** All readiness items and phase steps are mandatory. Immediately run `verify --release <id> --pending-only` and `checklist --release <id> --attest-prompt --json` per [readiness-gate.md](readiness-gate.md), execute **every** runnable probe in `pending_auto_checks` without halting on individual failures (recording `--status fail --detail "<specific tool/connector error>"` only on the failed check), fetch `checklist --release <id> --attest-prompt --handoff --json`, paste `display_markdown` verbatim as live Markdown, and prompt with the readiness attestation/repair card.
4. **Provision the integrated `release-drainer` accelerator (7:00 AM–7:00 PM every 2h, Entry Gate -> Release Done) & continue automatic guided progression:**
   - Right at `init` (Entry Gate) or `checklist` / `sign`, if `drainer_setup` has not been recorded yet:
     1. Read `drainer_setup.provider_spec` (or run `python -m orchestrator.cli automation plan --release <id> --slug release-drainer --json`).
     2. Check `m_list_automations`: if `<id> · Release-wide — release drainer` already exists, record its ID; otherwise call `m_create_automation` once with `provider_spec`, then record via `python -m orchestrator.cli automation record-setup --release <id> --slug release-drainer --id <id> --json`. (If `m_create_automation` is unavailable or fails, record `--skipped "<reason>"` and continue immediately — never block the release!)
   - Right after Readiness clears (`signed: true`), immediately run `next --release <id> --deliver --json` and execute all eligible tasks in `work_dispatch` whose `depends_on` lists and `available` windows are satisfied without asking the owner to say `"next"`.
   - Also inspect `work_dispatch.held_work`: if an entry has `pollable: true` (`in_flight` async work such as `ccd.localization_pr`), execute one reconciliation pass via its `resume_command` (`task ... --retry`), then re-run `next --release <id> --deliver --json` if the step settled.
   - If a step fails due to a missing or misconfigured tool/connector, fail **only that step** via `fail-step --release <id> --phase <p> --step <s> --tool <tool> --reason "<specific error>"` and continue executing all remaining independent and satisfied-dependency steps! Future phase dates (`CCD`, `CCD+1`) defer scheduled phase execution — never move the clock (`--as-of`) or rewrite source state to force early sends.
   - Stop at a genuine human input/approval, scheduled/external wait, or completion. Exhaust independent siblings first, then fetch `status --release <id> --details --handoff --json` and paste its full `display_markdown` table before prompting. Status-only and explicitly one-task requests remain scoped.

## Code Complete Date (CCD) & pipeline conflicts

Phases are anchored to the CCD (`CCD-7`, `CCD`, `CCD+1`). By default, CCD is the **2nd Wednesday** of `<release_id>`.
`init` and `check-ccd` read ADO pipeline `3155` (`overrideCodeCompleteDate`) without silently adopting a differing date:
- If pipeline `3155` has a different in-month date (`status: "conflict"`), present an `m_ask_user` card with **`Use pipeline date <override>`**, **`Keep release default <ccd>`**, and **`Use a different date`**.
- Changing the pipeline CCD is a checked external write: run `set-ccd --release <id> [--date <YYYY-MM-DD> | --default] --reason "<why>"` (preview), then with `--confirm` (use `--help` for flags). Re-run `check-ccd` and record `ccd_confirmed`. No blanket write authority comes from startup or readiness: each owning write step checks its target, required review, access, host consent, and receipt.

## Unified Release Drainer (`release-drainer`)

The release pipeline uses a **unified state-driven hybrid model** with zero mode split:
- Every phase and step declares explicit `depends_on` prerequisites and an optional `available` window (`CCD-7`, `CCD`, `CCD+1`, `CCD+1 09:00`, `weekdays_only`, `forbid_weekdays`, `hours_local`).
- `config/automations.yaml` declares a single optional release-wide automation: **`release-drainer`**, scheduled every 2 hours from **7:00 AM to 7:00 PM** (`cron: 0 7-19/2 * * *`), scoped from the **Entry Gate** until **`release_done`**.
- Each drainer pass runs `drain --release <id> --trigger scheduled --deliver --json`, writes a machine-readable JSON line to `<runs-root>/<release>/drain-runs.jsonl` (capturing entry/exit snapshots, executed steps, waiting `blocked` vs `human_input` counts & reasons, and missed drain slots), and delivers anti-spam Calls-to-Action via **Scout Teams bot + HTML Email** using idle-business-hour & external-cutoff escalation.
- Disabling or deleting `release-drainer` in Scout's UI **never** blocks `status`, readiness, or manual progression, and background runs **never** resurrect disabled or deleted automations.
- Concurrent background drains (`--scheduled`) and chat sessions share `<runs-root>/<release>/.state.lock` plus durable claim-before-IO checkpoints (`D.claim` / `write_review.authorize`), preventing collisions or double-execution.

When the owner asks to provision or inspect the drainer or drain journal:

```powershell
python -m orchestrator.cli automation plan --release <id> --json
python -m orchestrator.cli automation obligations --release <id> --json
python -m orchestrator.cli drain-log --release <id> --json
```

For `release-drainer`:
1. Write the exact `provider_spec` from `automation plan --json` to a temporary JSON file outside `.release-runs`.
2. Read fresh exhaustive `m_list_automations` + `m_get_automation` details into `{observed_at:<UTC>, complete:true, automations:[{id, spec}]}`.
3. Run `automation reconcile-create --release <id> --slug <slug> --ensure --claim --executor <session> --spec-file <spec.json> --observed-file <obs.json> --json` (adding `--on-demand <slug>` for on-demand pollers).
4. Call `m_create_automation` **only** when `permission_to_create:true` (passing the exact returned `spec`), then immediately acknowledge with `automation create-result`. Unknown or uncertain outcomes retain their owning attempt and never authorize duplicate creation.

Cleanup at phase/release close uses `automation cleanup` → `claim-delete` → `m_delete_automation` (only with `permission_to_delete:true`) → `delete-result`. Generated workers prefix release CLI calls with global `--scheduled`, run `drain --deliver`, park cleanly at human gates, and run `automation cleanup` in a `finally` block.

## No-progress boundary

`next` defaults to pending-only work. Inspect newly eligible host tasks after each result, but never repeat an unchanged failed/owned operation in a loop. Return a concrete wait, missing input, or failure with verified partial success; use `--retry` only after a real repair or explicit reconciliation request.

