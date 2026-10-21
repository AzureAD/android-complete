"""Guided onboarding planner. No host writes, release discovery or saved evidence."""
import json
import os
import tempfile

from orchestrator import cli_common as C, infra, onboarding


def cmd_onboarding(args):
    try:
        evidence = None
        if args.observation_file:
            with open(args.observation_file, encoding="utf-8-sig") as stream:
                evidence = json.load(stream)
            if not isinstance(evidence, dict):
                raise ValueError("Onboarding observation must be an object")
        report = infra.run(
            C.REQUIREMENTS_CONFIG, register=False, tool_inventory=evidence,
            scout_root=args.scout_root, agency_path=args.agency_path,
            workiq_path=getattr(args, "workiq_path", None))
        readiness = infra.load_requirements(os.path.join(os.path.dirname(args.config), "readiness.yaml"))
        result = onboarding.plan(report, evidence, readiness)
        result["structured_fetch"] = (report.get("m365_connectors") or {}).get("structured_fetch")
        if result["status"] == "ready" and getattr(args, "record_routing", False):
            profile = report["scout_root"]
            path = os.path.join(profile, "release-agent-routing.json")
            routing = {**result["routing"], "scout_root": profile,
                       "verified_at": evidence["observed_at"]}
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=profile, prefix=".release-agent-routing-",
                    suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(routing, stream, indent=2)
            try:
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
            result["routing_file"] = path
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {
            "status": "blocked", "title": "Setup evidence needs attention",
            "message": str(exc), "action": {"kind": "recollect"},
            "permission_to_start_release": False, "permission_to_write": False,
        }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["title"])
        print(result["message"])
        for step in result["action"].get("steps", []):
            print(f"  {step}")
        if result.get("next_action"):
            print(result["next_action"])
    return 0 if result["status"] == "ready" else 1


def register(sub):
    parser = sub.add_parser("onboarding", help="Plan guided setup only; never start or modify a release")
    parser.add_argument("--observation-file", help="Temporary Scout-owned inventory and read observations")
    parser.add_argument("--scout-root", help="Actual existing Scout profile")
    parser.add_argument("--agency-path", help="Actual Agency executable")
    parser.add_argument("--workiq-path", help="Actual existing native WorkIQ launcher")
    parser.add_argument("--record-routing", action="store_true",
                        help="After verified setup, save only its compact invocation recipe in the selected Scout profile")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd_onboarding)
