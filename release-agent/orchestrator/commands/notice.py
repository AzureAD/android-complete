"""`record-step` — generic recorder for a scout-assisted phase step.

The old `prepare-notice` / `prepare-flight-reminder` commands are gone: scout
steps are now resolved by the generic `step-action` command (which returns the
step's uniform NeedsSkill payload) and the skill records the result here.
"""
from __future__ import annotations

from orchestrator import cli_common as C


def cmd_record_step(args):
    """Record non-notification work, revalidate an explicit no-delivery outcome,
    or fail a specific step before any external write/send starts."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    if args.status == "fail":
        try:
            act = orch.fail_step(args.phase, args.step, args.detail or "",
                                 tool=getattr(args, "tool", None))
        except ValueError as exc:
            print(str(exc))
            return 1
        C.save_state(orch.state, args.runs_root, args.release)
        C.elog(args.runs_root, args.release).log(
            "step_failed", phase=args.phase, step=args.step,
            tool=getattr(args, "tool", None), detail=args.detail or "")
        C.emit(args.runs_root, args.release, f"[fail] {args.step}: {act.message}", kind="step")
        return 0
    try:
        handler = orch.handler(args.phase, args.step)
    except ValueError as exc:
        print(str(exc))
        return 1
    if handler.definition.write_command:
        print(
            f"Use the checked {handler.definition.write_command} adapter and its receipt recovery. "
            "For explicit owner resolution, use done/reopen with reviewed provider evidence; "
            "record-step cannot certify provider writes.")
        return 1
    if handler.notification and not orch.state.is_done(args.phase, args.step):
        from orchestrator.commands.step_action import prepare_step
        try:
            outcome = prepare_step(args, orch.state, orch)
        except ValueError as exc:
            print(str(exc))
            return 1
        if outcome.get("no_delivery_required"):
            if outcome.get("state_changed"):
                C.save_state(orch.state, args.runs_root, args.release)
                C.emit(
                    args.runs_root,
                    args.release,
                    outcome.get("note", "No delivery required; step completed."),
                    kind="step",
                )
                return 0
        elif (args.detail or "").strip().lower() == "no external work required":
            print("Outbound notification still requires delivery before marking pass; send the payload or use skip/complete-manually.")
            return 1
        elif outcome.get("kind") == "blocked":
            print(outcome.get("reason") or "Notification step is currently blocked.")
            return 1
        else:
            notif_list = outcome.get("notifications") or []
            completion = (notif_list[0].get("completion") if notif_list else None) or {}
            comp_status = completion.get("status") if completion.get("status") in ("pass", "attention") else args.status
            comp_links = completion.get("links")
            comp_data = completion.get("data")
            note_text = (args.detail or "").strip() or completion.get("note") or outcome.get("note") or f"Sent {args.step}"
            try:
                act = orch.record_scout_step(
                    args.phase, args.step, comp_status, note_text,
                    execution_id=getattr(args, "execution_id", None),
                    links=comp_links, data=comp_data,
                )
            except ValueError as exc:
                print(str(exc))
                return 1
            C.save_state(orch.state, args.runs_root, args.release)
            C.emit(
                args.runs_root, args.release,
                f"[{'ok' if comp_status == 'pass' else 'attention'}] {args.step}: {act.message}",
                kind="step",
            )
            return 0
    try:
        act = orch.record_scout_step(args.phase, args.step, args.status, args.detail or "",
                                     execution_id=getattr(args, "execution_id", None))
    except ValueError as e:
        print(str(e))
        return 1
    if act.kind == "idle":
        C.emit(args.runs_root, args.release, act.message, kind="step")
        return 0
    C.save_state(orch.state, args.runs_root, args.release)
    if getattr(args, "execution_id", None):
        C.elog(args.runs_root, args.release).log(
            "execution_recorded", phase=args.phase, step=args.step,
            execution_id=args.execution_id, status=args.status, detail=args.detail or "")
    C.emit(args.runs_root, args.release,
           f"[{'ok' if args.status == 'pass' else 'attention'}] {args.step}: {act.message}",
           kind="step")
    return 0


def cmd_fail_step(args):
    """Fail ONLY the specified step with an explicit reason (e.g. missing/misconfigured tool)
    without halting independent or downstream steps whose dependencies are met."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    try:
        act = orch.fail_step(args.phase, args.step, args.reason or "", tool=args.tool)
    except ValueError as exc:
        print(str(exc))
        return 1
    C.save_state(orch.state, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "step_failed", phase=args.phase, step=args.step, tool=args.tool, detail=args.reason or "")
    C.emit(args.runs_root, args.release, f"[fail] {args.phase}.{args.step}: {act.message}", kind="step")
    return 0


def register(sub):
    rs = sub.add_parser("record-step",
                        help="Record a scout-assisted phase step result (pass|attention|fail)")
    rs.add_argument("--release", required=True)
    rs.add_argument("--phase", default="preflight")
    rs.add_argument("--step", required=True)
    rs.add_argument("--status", required=True, choices=["pass", "attention", "fail"])
    rs.add_argument("--detail", default="")
    rs.add_argument("--tool", default=None, help="Missing or misconfigured tool/connector name when status=fail")
    rs.add_argument("--as-of", default=None)
    rs.add_argument("--execution-id", help="Owning execution ID returned by step-action --reserve")
    rs.set_defaults(func=cmd_record_step)

    fs = sub.add_parser("fail-step",
                        help="Fail only this specific step (e.g. missing/misconfigured tool) without halting siblings")
    fs.add_argument("--release", required=True)
    fs.add_argument("--phase", required=True)
    fs.add_argument("--step", required=True)
    fs.add_argument("--tool", default=None, help="Missing or misconfigured tool/connector identifier")
    fs.add_argument("--reason", required=True, help="Explicit failure reason shown to the user")
    fs.add_argument("--as-of", default=None)
    fs.set_defaults(func=cmd_fail_step)
