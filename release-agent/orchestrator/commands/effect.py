"""Owner-reviewed recovery for a blocked transactional auto effect."""
from __future__ import annotations

from orchestrator import cli_common as C


def cmd_retry_effect(args):
    state, orch = C.load_orch(args.runs_root, args.release, args.config)
    transition = orch.retry_effect(
        args.phase,
        args.step,
        args.execution_id,
        args.reason,
        confirm_absent=args.confirm_absent,
    )
    if not transition.changed:
        print(transition.message)
        return 1
    C.save_state(state, args.runs_root, args.release)
    print(transition.message)
    return 0


def cmd_supersede_effect(args):
    state, orch = C.load_orch(args.runs_root, args.release, args.config)
    transition = orch.supersede_effect(
        args.phase,
        args.step,
        args.execution_id,
        args.reason,
        confirm_idempotent=args.confirm_idempotent,
    )
    if not transition.changed:
        print(transition.message)
        return 1
    C.save_state(state, args.runs_root, args.release)
    print(transition.message)
    return 0


def register(sub):
    parser = sub.add_parser(
        "retry-effect",
        help="Authorize retry only after a transactional handler proves absence",
    )
    parser.add_argument("--release", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--execution-id", required=True, dest="execution_id")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--confirm-absent", action="store_true")
    parser.set_defaults(func=cmd_retry_effect)

    supersede = sub.add_parser(
        "supersede-effect",
        help="Replace a blocked idempotent desired-state effect with current input",
    )
    supersede.add_argument("--release", required=True)
    supersede.add_argument("--phase", required=True)
    supersede.add_argument("--step", required=True)
    supersede.add_argument("--execution-id", required=True, dest="execution_id")
    supersede.add_argument("--reason", required=True)
    supersede.add_argument("--confirm-idempotent", action="store_true")
    supersede.set_defaults(func=cmd_supersede_effect)
