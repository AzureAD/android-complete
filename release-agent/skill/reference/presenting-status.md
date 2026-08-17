# Reference — Presenting status & the checklist

_Loaded on demand. How to render the CLI's `status` / `checklist` output._

> **Render as markdown, never as a code block.** Both `checklist` and `status` outputs are already markdown (headings + tables). Paste them into your reply as **normal message content** so Scout renders the table/stepper — do **NOT** wrap them in a ``` code fence. Fencing shows raw text instead of a rendered table. Reproduce faithfully, as live markdown. (This is the core "render CLI output" golden rule — detailed here.)

**First decide what to show.** If the readiness entry gate isn't cleared (not signed, or blocked), the useful "status" **is the checklist** — run `checklist --release <id> --verify` and show that table (don't show the terse status line, don't ask permission to pull it). Only when the gate is cleared / mid-flight do you show the `status` block.

**Mid-release status:** run `status --release <id>` (no `--json`) and show its output as rendered markdown — a next-action headline, a **phase map** (✅ done · ⏸ in progress · 🗓 scheduled · ⬜ not started), and the **current phase's steps** table. It auto-logs what was shown. Add a sentence before/after, but reproduce the block faithfully; don't re-render from `--json` (that skips the auto-log) and don't invent a layout. Never surface raw engine state names (`holding_gate`, `awaiting_action`, `scheduled`) — the view already translates them ("Waiting for your approval", "Action needed from you", "Scheduled").

Alongside the table, tell the user exactly what each outstanding step needs (run the scout steps yourself; for `attest` steps spell out what to confirm). Do **not** replace the table with your own summarized list.

**Render once per advance pass — after the work, not before.** When a turn both advances the release (`next` + executing scout/agent steps) and shows status, do the work first and paste the `status` table **once**, at the end, reflecting the settled state. Don't paste a table before running the scout steps and then a second one after — the pre-work render is immediately stale and just duplicates the final one. One pass → one table.

**Structured fields for branching** (`status --json`):
`release_id, status, ccd, ccd_source, as_of, done, total, percent, current_phase_name, current_step_name, gate, action, scheduled, pending_human, readiness_signed, blocked`.
