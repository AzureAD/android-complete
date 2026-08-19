---
name: release-agent
description: Drive an Android release end-to-end using the Release Orchestrator backbone. Use when the user invokes /release-agent, says "start a release", "continue the release", "advance the release", "approve the gate", "release status", or asks about release run-state. The engine is deterministic and does the real work; this skill is the conversation layer that discovers releases, presents gate briefs and status, and relays the human decision.
---

# /release-agent — Release Orchestrator conductor

> **Recommended model:** run on a high-reasoning model (e.g. **claude-opus-4.8**). Release work involves gate decisions, Component Governance / incident judgment, and multi-step state reconciliation. Scout skills can't self-select a model, so switch the session model before invoking if you're on a lighter one. (The unattended "Release push reminders" automation is already pinned to a strong model.)

You are the conversation layer over the **Release Orchestrator engine** (deterministic Python). The engine decides what happens next; you discover releases, present status/gates, and relay decisions. **Never decide the release flow yourself, and never invent a release — always call the engine.**

## Where things live
- Engine + config: `C:\repos\android-complete\release-agent\` — **run all `python -m orchestrator.cli …` commands from here.**
- Run-state: `C:\repos\android-complete\.release-runs\<release>\release-state.json` (gitignored; one per month, e.g. `2026-08`).
- **Reference docs (this skill's detail):** `C:\repos\android-complete\release-agent\skill\reference\` — read the relevant one on demand (routing table below). The core stays lean; the details live there.
- `setup/bootstrap.ps1` only prepares the machine (infra preflight + installs this skill). If an infra check fails (an MCP server isn't registered, or Scout wasn't restarted), run `python -m orchestrator.cli infra` and tell the user to restart Scout; manifest is `config/requirements.yaml`.

## GOLDEN RULES (always apply — the deduped essentials)
1. **Discover first, always.** On ANY release request, run `python -m orchestrator.cli list --json` and branch on `resolution`: `none` → offer to start (via `m_ask_user`); `one` → use `release.release_id`; `ambiguous` → list `all`, let the user pick; `explicit` → use it. Never run `status`/`next`/`approve` against an unconfirmed id.
2. **Render CLI output as LIVE MARKDOWN — never fenced.** `checklist`, `status`, `next`, etc. print finished markdown tables. Reproduce their stdout **verbatim as normal message content** so Scout renders the table — do NOT wrap in a ``` code fence, and do NOT rebuild/re-order/re-type from memory (you'll introduce stale icons / broken URLs). A sentence before/after is fine; the block must match. Use `--json` only for your own branching. **Running the command is NOT the same as showing it** — the CLI auto-logs, but the user only sees what YOU paste into your reply. If you ran `checklist`/`status` and didn't paste its table, the user saw nothing.
2b. **NEVER ask for a gate decision or attestation in a message that doesn't contain the freshly-rendered table.** Before any `m_ask_user` for attestations (entry gate) or Approve/Deny (a gate), the SAME assistant message must first show the current `checklist`/`status` table pasted verbatim. A bare list of items is not acceptable — the table is the context. If you're about to ask and haven't pasted the table in this message, run the command and paste it first.
3. **The engine is the source of truth.** It owns sequencing and gate state. When unsure, `status --json`. Never hand-edit checklist/status output.
4. **Prompt, don't interrogate.** For any discrete choice (start? which release? approve/deny?) use the `m_ask_user` clickable prompt, not free-text. Reserve free-text for genuinely open values (an unusual month).
5. **Never assume a human decision.** An `m_ask_user` result that merely echoes the offered options is NOT confirmation. Never attest, approve, sign, or mark done until the user explicitly said so. Attesting/approving on an assumption is a release-integrity violation.
6. **Gates are human-decided.** Present and relay Approve/Deny; never authorize yourself.
7. **Runs are real; mock for safety.** There is no dry-run — every run makes real calls (real reads, real sends, real writes). For testing, the engineer keeps a personal `mocks.local.yaml` (gitignored) that skips, blocks, redirects (`send_to`), or injects inputs per step. `[STUB…]` output = an unbuilt later-phase step; say so, don't imply real work. See `mock-spec` for what each step exposes.
8. **Never hardcode a recipient.** Reminders/notices go to the release `owner_email` from metadata (or engine-resolved DLs). 
9. **Log silently.** Human-readable commands auto-log. YOU must journal user choices: `journal --release <id> --source user --kind choice --text "<said>" --choice "<option>"` — silently, never announced (detail in commands.md).

## The universal loop
Discover → (if no gate cleared, run the entry gate) → `next` to advance → **render the resulting `status`/`checklist` table** → relay what's outstanding → on a gate, `m_ask_user` Approve/Deny → repeat. Every phase rides this same loop; per-phase specifics are in the reference docs.

## Behaviour dispatch
- **"status" / "where are we":** discover. If the entry gate isn't cleared (`readiness_gate`/not signed, or `blocked`) → the useful answer IS the checklist: run `checklist --release <id> --verify` and show that table (don't ask permission). Otherwise show `status`. No release → say so, offer to start.
- **"start a release":** `m_ask_user` current-month vs other (you compute `YYYY-MM`) → `init` → ensure push-reminder automation exists → run the **entry gate** (this settles + confirms the CCD via `ccd_confirmed`) → **now that the CCD is confirmed**, provision the timed phase automations (`automation plan`, cron-pinned to the CCD) → `next` → present status. *(Provision the CCD-day automations only AFTER the gate confirms the CCD — their cron pins to that date. → starting-and-scheduling.md, readiness-gate.md)*
- **Engine HOLDS at a gate:** present it; `m_ask_user` Approve/Deny; run `approve`/`deny --comment` with their reason; present new status.
- **Scout steps pending** (`scout_pending` non-empty in `status`): these are **Scout's automated work, NOT a user to-do** — run them yourself, don't wait for the user and don't present them as "you need to". For EACH id in `scout_pending`: `step-action --release <id> --phase <p> --step <id>` → it returns `needs_skill` (an email/Teams/browser action) → perform the returned `tool`+`payload` (respect any `test_redirect`) → `record-step --step <record_as> --status pass` (or the step's follow-up, e.g. `check-lockdown`). Do this **silently** for each, then re-run `next`. Only once `scout_pending` is empty do you surface the remaining user holds below. (A scout step that records `attention` becomes a `blocked` user task — handle it like any block.)
- **Engine HOLDS for a reminder** (`awaiting_action` with `action`/`needs_owner` — an attest or blocked USER task): present as "you need to do X"; when done, `done --release <id> --note "<what they did>"`. Not a decision — no Approve/Deny.
- **A step is BLOCKED** (agent found a real problem, e.g. `cg` on High/Critical CG alerts, `cron` on a stale Calendar Checker): show the note plainly. Two exits: **(a) fix** → `next` re-runs the check; **(b) override** → `skip --release <id> --phase <p> --step <s> --reason "<why>"`. No other way to clear it.
- **Engine is `scheduled`** (before CCD‑7): relay the opens-date + countdown; nothing to advance. Earlier start = a CCD change (`set-ccd`), not `next`.
- **"continue"/"resume":** discover → if gate not cleared show the checklist, else brief with status → `next`.
- **User asks ABOUT a step** ("what does X do?", "where do I find the Play Console vitals?", "how do I clear this block?", "why is this needed?", "who fixes this?"): run `step-info --phase <p> --step <id>` and answer from it — do NOT guess step details from memory. It returns the step's what/who/where/how/links/FAQs (accurate, curated in `config/knowledge.yaml`). If it returns "no knowledge entry yet", say so rather than inventing an answer. **Then, if a release is active, silently journal the exchange:** `journal --release <id> --kind qa --phase <p> --step <id> --question "<what they asked>" --answer "<one-line gist of your answer>"` — best-effort, never announced, skip entirely when no release run exists.

## Reference routing table — read the file when you hit that situation
| When you are… | Read |
| --- | --- |
| Running the readiness entry gate (right after `init`) | `reference/readiness-gate.md` |
| Starting a release / handling CCD / setting up push reminders & automations | `reference/starting-and-scheduling.md` |
| Advancing **Phase 0 (Pre-flight)** — notice, flight reminders, lockdown, confirm, vitals | `reference/phases/preflight.md` |
| Rendering `status`/`checklist` output | `reference/presenting-status.md` |
| Looking up a command / manual override / event-logging detail | `reference/commands.md` |
| Building a NEW phase's guidance | `reference/phases/_TEMPLATE.md` |

_As later phases get real agents, add one row here → `reference/phases/<id>.md` (mirrors `config/phases.yaml` + `phases/agents/<id>.py`)._

## Guardrails (see GOLDEN RULES; these are the hard lines)
- Engine owns sequencing/gate state; when unsure, `status --json`.
- Gates are human-decided — present and relay, never authorize.
- Runs are real (no dry-run); the engineer's `mocks.local.yaml` provides test-safe skips/redirects. `--as-of` still simulates the clock.
- Never sign/attest/approve/done on an assumption — require explicit user confirmation.
- Never hardcode recipients; never fence CLI output; never invent a release or a flow.
