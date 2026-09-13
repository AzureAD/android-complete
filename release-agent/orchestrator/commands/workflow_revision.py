"""Explicit, reviewed adoption; never dispatches handlers or provider work."""
import json

from orchestrator import cli_common as C
from orchestrator.locking import file_lock
from orchestrator.registry import AutomationRegistry
from orchestrator.revision import adoption_preview, adopt


def cmd_workflow_adopt(args):
    try:
        state, orch = C.load_orch(args.runs_root, args.release, args.config)
        registry = AutomationRegistry(args.runs_root, args.release)
        # CLI main owns the release lock; all registry operations take this lock
        # second. Hold it through the state replace so ownership cannot race.
        with file_lock(registry.lock_path, 30.0):
            entries = [entry for _, entry in registry._all_unlocked()]
            if args.approve_hash:
                result = adopt(
                    orch, args.approve_hash, by=args.by, reason=args.reason,
                    registry_entries=entries,
                )
            else:
                result = adoption_preview(orch, registry_entries=entries)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "permission_to_adopt": False}))
        return 1


def register(subparsers):
    parser = subparsers.add_parser(
        "workflow-adopt", help="Preview or hash-confirm a workflow revision adoption (no execution)")
    parser.add_argument("--release", required=True)
    parser.add_argument("--approve-hash")
    parser.add_argument("--by")
    parser.add_argument("--reason")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd_workflow_adopt)
