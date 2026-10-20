"""Explicit preview/claim/execute for the compatible existing-parent Monthly amendment."""
import json
from orchestrator import cli_common as C
from orchestrator import auth_monthly_amendment as M


def run(args):
    if args.reason and not args.cancel or args.cancel and args.reserve:
        raise ValueError("--reason is cancellation-only; cancellation cannot reserve a new claim")
    _, orch = C.load_orch(args.runs_root, args.release, args.config)
    orch._monthly_runs_root = args.runs_root
    if args.cancel:
        value = M.cancel(orch, review_hash=args.review_hash, approved_by=args.approved_by,
                         executor=args.executor, reason=args.reason)
    elif args.execute:
        value = M.execute(orch, review_hash=args.review_hash, approved_by=args.approved_by,
                          executor=args.executor, reserve=args.reserve)
    else:
        if args.reserve or args.reason or args.review_hash or args.approved_by or args.executor:
            raise ValueError("Preview is read-only; claim/review arguments require --execute or --cancel")
        value = M.preview(orch)
    return value


def command(args):
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc), "permission_to_execute": False}))
        return 1


def register(sub):
    parser = sub.add_parser(M.COMMAND, help="Review and attach one Monthly leaf to a completed v1 Authenticator parent")
    parser.add_argument("--release", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true")
    action.add_argument("--cancel", action="store_true")
    parser.add_argument("--review-hash")
    parser.add_argument("--approved-by")
    parser.add_argument("--executor")
    parser.add_argument("--reserve", action="store_true")
    parser.add_argument("--reason")
    parser.set_defaults(func=command)
