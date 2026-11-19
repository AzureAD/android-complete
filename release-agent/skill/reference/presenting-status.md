# Presenting status and operation results

> **Render as live Markdown — NEVER inside a code fence, NEVER condensed.**
> Both `status --release <id> --details` and `display_markdown` emit complete Markdown headings and tables. Paste them **verbatim as normal message content** so Scout renders the table — NEVER wrap them in ```` ```markdown ```` or any code block, and NEVER replace them with a custom/condensed summary matrix (e.g. `Field | Value` or bullet summaries). Tool output alone is not visible to the user unless you paste it into your message.

## Status-only, launcher, help, and release-advancing turns

- **Bare `/release-agent` / `"menu"` / `"options"` (Orientation + Launcher Hub)**: run `commands --json`, paste `status_markdown` (if an active release exists) or `guide_markdown` (if none exists) verbatim, and present `launcher_prompt` via `m_ask_user`. Never substitute this generic launcher menu when an active progression turn pauses at a human checkpoint — checkpoint pauses always show the step's own decision card!
- **Command guide requests** (`"help"`, `"commands"`, `"what can I do?"`): run `commands [--release <id>]` (no `--json`) and paste the live-filled Quick Guide Markdown block verbatim.
- **Status-only requests** (`/release-agent show status`, `"where are we?"`): run `status --release <id> --details` (no `--json`; omit `--details` only if the user explicitly asked for a one-line brief) and paste the emitted Markdown block verbatim (which includes the contextual Quick commands footer). Status-only and explicit `"only this step"` requests do not drain the broader queue, probe providers, advance phases, or attach unsolicited approval cards.
- **Release-advancing turns** (default automatic guided progression): use `next --release <id> --deliver --json` and `work_dispatch` to exhaust all executable agent-owned work across phases, rechecking after each result. Human holds do not veto independent siblings. Stop only for genuine input/consent/repair, a scheduling/external wait, or completion — never ask `"continue?"` between successful steps. When the user answers a checkpoint card with an advancing choice (**Completed**, **Approve**, **Fixed — retry**, **New attempt / inputs**, **Completed manually**, **Skip**), record the answer and **immediately resume automatic guided progression in the same turn** without requiring the user to say `"continue"` or `"run next"`.

## Guarded handoff & mandatory full-table context

At every owner-input or wait boundary, **always print the existing full status table before the question/wait explanation**:
1. Run `status --release <id> --details --handoff --json` (or `checklist --release <id> --attest-prompt --handoff --json` while entry readiness is unsigned). Identify genuine host input/consent/recovery boundaries on queued Scout steps with `--hold-step "<phase.step>=<actual reason>"` after independent work is exhausted.
2. `ready:false` alone is not a user-input boundary: consult dispatch (`must_continue_automatic_work`) and finish executable checks before showing an actual input/repair/wait. Preserve actual host consent and ownership; do not replay an unchanged failed operation.
3. Paste `display_markdown` verbatim as normal Markdown **first**, then state what happened (with links and provenance: `verified`, `owner_attested`, `waived`, or `manual-only` — note that `%` measures checklist completion, not rollout ring percentage) and present the focused `m_ask_user` card or wait explanation.

## Actionable `m_ask_user` cards for focused owner decisions

Never end an actionable hold with prose-only `"please review/confirm"` — always follow the pasted table with an `m_ask_user` option card (using `owner_prompt` when provided, or the exact options below):

- **Attestation / human action (`confirm` / `action`):** run `step-action --release <id> --phase <p> --step <s>` for the exact `prompt`, paste fresh `display_markdown`, and call `m_ask_user` with **Completed**, **Not yet**, **Skip (waive)**, **Need help** (`step-info`).
- **Blocked step (`blocked`):** paste fresh `display_markdown` + the full block note, and pass `owner_prompt.question` & `owner_prompt.answers` **verbatim** to `m_ask_user` when present (preserving domain-specific guidance such as `preflight.lockdown` CCOA date review — if the owner chooses **Shift CCD outside lockdown**, collect the new `YYYY-MM-DD` date, apply `set-ccd`, and re-run `lockdown`). Fallback options when `owner_prompt` is absent:
  - **Fixed — retry** (`task --retry` to recheck/reconcile the same attempt after a real fix)
  - **New attempt / inputs** (`rerun` with `step-info --json` public parameters, keeping prior history)
  - **Completed manually** (`complete-manually`, owner-attested without claiming provider verification)
  - **Override (skip with reason)** (verify the underlying finding first, collect free-text reason, then `skip`)
  - **Keep blocked** (leave unchanged)
  *(An uncertain/owned prior runner requires confirming the prior runner stopped before `--prior-writer-stopped`.)*
- **Approval gate (`gate`):** approval gates remain genuine decisions, not manual-completion or skip toggles. Local gate → paste `display_markdown` + `m_ask_user` **Approve gate / Deny gate / Keep on hold**. External gate (`remove_rc_tags_gate`, `publish_notes_gate`) → run `approve-orchestrator-gate --release <id> --preview --comment "<comment>"`, display the exact coordinates/build/stage/approval ID/hash alongside `display_markdown`, then `m_ask_user` **Approve / Deny / Keep on hold**.

Ask only for genuinely missing information, never a confirmation the owner already clearly supplied. After recording the owner's answer, **resume automatic guided progression** immediately unless the owner explicitly limited the scope. The background `release-drainer` is optional and non-blocking; never block status or auto-recreate automations the owner disabled or deleted.

