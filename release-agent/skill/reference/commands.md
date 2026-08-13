# Reference — Commands & manual overrides

_Loaded on demand. Run all from `C:\repos\android-complete\release-agent`._

| Intent | Command |
| --- | --- |
| Discover releases | `python -m orchestrator.cli list --json` |
| Start a new release (dry-run) | `python -m orchestrator.cli init --release <YYYY-MM>` |
| Start for real | `python -m orchestrator.cli init --release <YYYY-MM> --live` |
| Show readiness entry checklist | `python -m orchestrator.cli checklist --release <YYYY-MM> [--verify] [--json]` |
| Run auto readiness verifiers | `python -m orchestrator.cli verify --release <YYYY-MM>` |
| Attest human items (+auto verify) | `python -m orchestrator.cli sign --release <YYYY-MM> --item <id> [--item <id> …] --note "<what they confirmed>"` |
| Record a scout-assisted check (e.g. ICM on-call) | `python -m orchestrator.cli record-check --release <YYYY-MM> --item <id> --status pass\|fail\|degraded --detail "..."` |
| Decide CCOA lockdown overlap | `python -m orchestrator.cli check-lockdown --release <YYYY-MM> --periods-json '[{"name","environment","start","end"}]'` |
| Prepare early code-complete notice (JSON) | `python -m orchestrator.cli prepare-notice --release <YYYY-MM> [--variant initial\|update]` |
| Prepare flight & string reminders (JSON) | `python -m orchestrator.cli prepare-flight-reminder --release <YYYY-MM>` |
| Record a scout-assisted phase step | `python -m orchestrator.cli record-step --release <YYYY-MM> --step <id> --status pass\|attention --detail "..."` |
| Declare you CANNOT satisfy an item | `python -m orchestrator.cli decline --release <YYYY-MM> --item <id>` |
| Status (structured) | `python -m orchestrator.cli status --release <YYYY-MM> --json` |
| Advance to next gate | `python -m orchestrator.cli next --release <YYYY-MM>` |
| Approve the holding gate | `python -m orchestrator.cli approve --release <YYYY-MM> --comment "<why>"` |
| Deny the holding gate | `python -m orchestrator.cli deny --release <YYYY-MM> --comment "<why>"` |
| **Done** — mark a reminder (human to-do) complete | `python -m orchestrator.cli done --release <YYYY-MM> [--phase <p> --step <s>] --note "<what you did>"` |
| **Set/change CCD** (writes pipeline; preview→confirm) | `python -m orchestrator.cli set-ccd --release <YYYY-MM> --date <YYYY-MM-DD> --reason "<why>" [--confirm]` |
| **Revert CCD to default** (2nd Wednesday) | `python -m orchestrator.cli set-ccd --release <YYYY-MM> --default --reason "<why>" [--confirm]` |
| **Skip/cancel the release** (writes pipeline) | `python -m orchestrator.cli skip-release --release <YYYY-MM> --reason "<why>" [--confirm]` (add `--clear` to un-skip) |
| **Skip** a step (reason REQUIRED) | `python -m orchestrator.cli skip --release <YYYY-MM> --phase <p> --step <s> --reason "<why>"` |
| **Reopen** a done/skipped step | `python -m orchestrator.cli reopen --release <YYYY-MM> --phase <p> --step <s> [--reason "..."]` |
| **Halt** (emergency, reason REQUIRED) | `python -m orchestrator.cli halt --release <YYYY-MM> --reason "<why>"` |
| **Resume** after a halt | `python -m orchestrator.cli resume --release <YYYY-MM> [--reason "..."]` |
| Show / analyze this release's log | `python -m orchestrator.cli log --release <YYYY-MM> [--analyze] [--json]` |
| Journal interaction (silent) | `python -m orchestrator.cli journal --release <YYYY-MM> --source scout\|user --text "..."` |
| Activate conditional hotfix phase | `python -m orchestrator.cli activate --release <YYYY-MM> --phase hotfix` |
| **Notify** — push line if something needs me | `python -m orchestrator.cli notify [--release <YYYY-MM>] [--as-of <date>] [--force]` |
| **Track automations** | `automation register --id <id> --name "<n>" [--shared\|--release <YYYY-MM>] [--purpose "..."]` · `automation list [--release <YYYY-MM>] [--json]` · `automation deregister --id <id>` |

## Manual overrides (steer when reality diverges from the plan)
- **skip** — a step doesn't apply, or was done manually outside the tool. **Reason required** (audited). Confirm the reason, then run.
- **reopen** — a step (incl. an approved gate) needs to run again; reopening a gate makes it re-hold for a fresh decision.
- **halt** — emergency freeze (e.g. production incident). **Reason required.** While halted, `next` refuses; status shows a HALTED banner.
- **resume** — clear a halt and continue.

Map natural language to these ("skip the CG report, doesn't apply" → `skip … --reason`; "halt, we have an incident" → `halt --reason`; "resume" → `resume`). Never skip or halt without capturing the user's reason.

The human-readable commands (`checklist`, `status`, `next`, `approve`, `deny`, `decline` without `--json`) emit a **canonical block AND auto-log it**. Prefer these and show their output; use `--json` only for your own logic.

## Event logging (silent — never changes the interaction)
Each release keeps an append-only log at `.release-runs/<id>/events.jsonl` (per-release only; no machine-wide aggregate). For debugging only. **Invisible to the user** — don't announce it, don't add questions to populate it.
- **Scout output is logged automatically** by the CLI for every human-readable command — you don't journal what was shown.
- **User input is your responsibility** — the engine can't see what the user typed/clicked. Every time the user makes a choice, immediately (and silently) journal it: `journal --release <id> --source user --kind choice --text "<what they said>" --choice "<option>"`.
- Capture the **decision driver** passively: a reason given while approving/denying/declining → pass as `--comment "<their words>"` (or `--reason` for decline). No reason → empty comment. Never prompt just for the log.
- User asks "what happened" / "show the log" → `log --release <id>` (add `--analyze` for a rollup).
