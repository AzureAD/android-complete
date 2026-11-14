# Starting a release and optional unattended operation

## Automatic guided start

1. Resolve an unambiguous existing clone and the actual release/owner context.
   `paths --json` and `list --json` provide local discovery. Do not reconfirm a
   valid known path on every interaction.
2. Use `preview-release --json` when the work/ship month or CCD is unclear.
   Gather missing choices together; show the release label and actual CCD, not
   ambiguous month shorthand.
3. Run `init --release <id> --json` with the verified owner/timezone.
   Guided foreground operation is the default; no worker is created. Inspect its `checklist_command`
   for required readiness. Initialization alone is not a claim that work executed.
4. Perform required automatic checks and record actual results. Ask only for the
   specific remaining human confirmations/repairs. Capabilities marked "when used"
   do not block unrelated work. Future phase dates do not authorize early sends.
5. Use `next --json` and `work_dispatch` to execute all currently available
   agent-owned work, including host-assisted steps. Recheck after each result and
   continue across phases without asking the owner to say "next".
6. Stop at a genuine human input, scheduled/external wait or completion. Exhaust
   independent siblings first, then print the full status table before prompting.
   A status-only or explicitly one-task request remains scoped.

Changing the pipeline CCD is a checked external write, not just changing a label.
Use `set-ccd`'s actual preview/confirmation policy. `set-target-month` changes only
the release display month. Never move the clock or rewrite source state to make work eligible.

## Setup on request

`onboarding --json` diagnoses the selected installation and its supported
integrations. It does not initialize a release, sign readiness, send messages, upload
files, or create workers. Use `--help` for actual profile/launcher overrides.
Resolve missing paths once; preserve existing connections, authentication, and permissions.
Obtain required host/user consent before installation or configuration changes.
Do not assume source edits have synchronized an already-installed skill.

Discover the actual loaded tools; missing MCP fetch does not prove native CLI fetch
is absent. Keep verified structured-fetch routes separate from tool inventories.
Use exact owner-bound observations and complete pagination. A route or registration
is not authenticated access proof. After one real reconnect/reload and one retry,
an unchanged failure needs a precise support handoff, not another loop or alternate account.

No blanket write authority comes from successful setup. Each owning write step
still checks its target, required review, access, host consent, and receipt.

## Opt in to unattended progression

Use `init --unattended`, or explicitly:

```powershell
python -m orchestrator.cli set-mode --release <id> --mode unattended
python -m orchestrator.cli automation obligations --release <id> --json
```

Use the returned canonical plan/spec and fresh complete actual-provider
observations. `automation reconcile-create --ensure --claim` may authorize one
create only when it returns `permission_to_create:true`; acknowledge the owning
create-result. Reuse an exact existing worker. Recreate a proven-absent settled
worker only through reconciliation; unknown outcomes never authorize duplicate creation.

A saved ID is not a startup receipt or live-provider health. Never claim unattended
coverage until the driver is acknowledged and healthy. Do not ask the owner to
reset registry fields or supply internal execution IDs.

`set-mode --mode interactive` stops scheduled progression without deleting worker
resources or their receipts. Cleanup remains explicit, checked, and owner-authorized.

**prompt:** use the canonical generated worker prompt, pinned to its release.
Its `status --release <YYYY-MM>`, `next --release <YYYY-MM>`, and
`tick --release <YYYY-MM>` operate only within the configured worker scope.
Run `automation cleanup --release <YYYY-MM>` in a finally boundary through its
claim/result protocol. Do not add courtesy copies, extra invitations, or raw sends.
Preserve real gates, scheduling, failed checks, and unknown outcomes.
Use `work_dispatch` for the default guided flow as well as opted-in workers.
A plain status request remains read-only and does not authorize progression.

## Ensure the daily partner status task only when requested

Use the engine's declared automation plan and durable trigger. Do not create every
possible phase worker at initialization. Respect owner-local business hours, CCD
confirmation, exact source identities, and declared cleanup objectives.
Unknown create/delete outcomes retain their owning attempts; no automatic replacement,
universal unlock, or silent in-place update is permitted.

## No-progress boundary

`next` defaults to pending-only work. The agent may inspect newly eligible host tasks
after a result, but must not repeat an unchanged failed/owned operation in a loop.
Return a concrete wait, missing input, or failure with verified partial success.
Use an explicit `--retry` only after a real repair or supported reconciliation request.

Before a guided-flow question/wait handoff, use `status --details --handoff --json`
and display its full status table. This is not a requirement for merely inspecting
status or honoring an explicit "only this step" request.
Generated workers pass global `--scheduled` before release subcommands. This
pauses new worker effects in interactive mode without refusing owning result receipts.
