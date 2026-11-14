"""Machine-scoped structured WorkIQ reads; no release discovery or mutations."""
import json
import subprocess

from orchestrator import infra, structured_fetch


def cmd_workiq_read(args):
    try:
        if getattr(args, "preset", None) == "roster":
            from orchestrator import m365_capabilities as M
            bundle = M.fetch_live_roster_bundle(
                args.account, scout_root=args.scout_root, workiq_path=args.workiq_path)
            payload = json.dumps(bundle, indent=2)
            if getattr(args, "output_file", None):
                with open(args.output_file, "w", encoding="utf-8") as out:
                    out.write(payload)
                print(json.dumps({"ok": True, "output_file": args.output_file,
                                  "pages": len(bundle["observations"])}))
            else:
                print(payload)
            return 0
        if not getattr(args, "entity_url", None):
            raise ValueError("Provide --entity-url <Graph-relative URL> or --preset roster")
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
    payload = json.dumps(result, indent=2)
    if getattr(args, "output_file", None):
        with open(args.output_file, "w", encoding="utf-8") as out:
            out.write(payload)
        print(json.dumps({"ok": True, "output_file": args.output_file}))
        return 0
    print(payload)
    return 0


def register(sub):
    parser = sub.add_parser("workiq-read", help="Read an exact entity or preset bundle through native WorkIQ fetch")
    parser.add_argument("--scout-root", help="Actual Scout profile used for launcher discovery")
    parser.add_argument("--workiq-path", help="Actual existing WorkIQ launcher if discovery is unresolved")
    parser.add_argument("--account", required=True, help="Verified signed-in release-owner address")
    parser.add_argument("--entity-url", default=None, help="Exact Graph-relative read URL")
    parser.add_argument("--preset", choices=["roster"], default=None,
                        help="Fetch a complete paginated bundle in Python without PowerShell $-query quoting")
    parser.add_argument("--output-file", default=None, help="Write JSON output directly to this file")
    parser.add_argument("--route-file", help="Reuse a fresh verified discovery descriptor across pagination calls")
    parser.set_defaults(func=cmd_workiq_read)
