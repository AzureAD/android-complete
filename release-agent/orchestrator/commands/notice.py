"""`record-step` — generic recorder for a scout-assisted phase step.

The old `prepare-notice` / `prepare-flight-reminder` commands are gone: scout
steps are now resolved by the generic `step-action` command (which returns the
step's uniform NeedsSkill payload) and the skill records the result here.
"""
from __future__ import annotations

from orchestrator import cli_common as C


def cmd_record_step(args):
    """Record non-notification work, or revalidate an explicit no-delivery outcome."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
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
        if args.status != "pass" or not outcome.get("no_delivery_required"):
            print("Use notification claim/result for notification steps; record-step is not delivery evidence.")
            return 1
        if outcome.get("state_changed"):
            C.save_state(orch.state, args.runs_root, args.release)
            C.emit(
                args.runs_root,
                args.release,
                outcome.get("note", "No delivery required; step completed."),
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


def register(sub):
    rs = sub.add_parser("record-step",
                        help="Record a scout-assisted phase step result (pass|attention)")
    rs.add_argument("--release", required=True)
    rs.add_argument("--phase", default="preflight")
    rs.add_argument("--step", required=True)
    rs.add_argument("--status", required=True, choices=["pass", "attention"])
    rs.add_argument("--detail", default="")
    rs.add_argument("--as-of", default=None)
    rs.add_argument("--execution-id", help="Owning execution ID returned by step-action --reserve")
    rs.set_defaults(func=cmd_record_step)
