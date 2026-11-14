"""Machine-scoped structured WorkIQ reads; no release discovery or mutations."""
import json
import subprocess

from orchestrator import infra, structured_fetch


def cmd_workiq_read(args):
    try:
        discovered = None
        if getattr(args, "route_file", None):
            with open(args.route_file, encoding="utf-8-sig") as stream:
                discovered = json.load(stream)
            if not isinstance(discovered, dict) or discovered.get("kind") != "cli":
                raise ValueError("Route file must contain the actual verified CLI discovery descriptor")
        result = structured_fetch.read(
            infra.scout_profile_root(args.scout_root), args.account, args.entity_url,
            preferred=args.workiq_path, discovered=discovered)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc), "permission_to_write": False}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


def register(sub):
    parser = sub.add_parser("workiq-read", help="Read an exact entity through this user's native WorkIQ fetch")
    parser.add_argument("--scout-root", help="Actual Scout profile used for launcher discovery")
    parser.add_argument("--workiq-path", help="Actual existing WorkIQ launcher if discovery is unresolved")
    parser.add_argument("--account", required=True, help="Verified signed-in release-owner address")
    parser.add_argument("--entity-url", required=True, help="Exact Graph-relative read URL")
    parser.add_argument("--route-file", help="Reuse a fresh verified discovery descriptor across pagination calls")
    parser.set_defaults(func=cmd_workiq_read)
