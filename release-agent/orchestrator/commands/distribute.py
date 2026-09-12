"""Read/validate live ADO distribution; apply only explicitly reviewed current corrections."""
from __future__ import annotations

import json

from orchestrator import cli_common as C, mocks as mocks_mod
from steps.bug_bash import distribute_tests as S
from steps.lib import mockctx
from tools import distribution as D


def _print_report(report, as_json):
    public = {key: value for key, value in report.items() if not key.startswith("_")}
    if as_json:
        print(json.dumps(public, indent=2))
        return
    if "error" in report:
        print(f"BLOCKED: {report['error']}")
        if "valid" not in report:
            for member in report.get("candidates", []):
                print(f"  {member['name']} <{member['upn']}>")
            return
    print(f"Live ADO distribution: {'VALID' if report['valid'] else 'CORRECTIONS NEED REVIEW'}")
    print(f"  Manual: Broker {report['broker_total']} + Auth {report['auth_total']}; "
          f"owner triage: {len(report['owner_triage'])}")
    print(f"  Excluded owner: {report['owner_excluded']} | OCE: {report['oce_excluded']}")
    print("  Owner-confirmed OOF: " + (", ".join(f"{m['name']} <{m['upn']}>" for m in report["oof_excluded"]) or "nobody"))
    for upn in report["eligible"]:
        print(f"  {upn}: {report['current_counts'][upn]} now -> {report['proposed_counts'][upn]} proposed")
    for change in report["case_changes"]:
        print(f"  {change['case']}: {change['from'] or '(unassigned)'} -> {change['to']} ({change['reason']})")
    for change in report["point_changes"]:
        print(f"  Plan {change['plan_id']} suite {change['suite_id']} point {change['point_id']}: "
              f"{change['from'] or '(unassigned)'} -> {change['to']}")
    if not report["valid"]:
        print(f"  After approval: --apply --review-hash {report['review_hash']}")


def _inspect(args, state):
    try:
        return S.inspect_distribution(state, oof=[] if args.no_oof else args.oof, oce=args.oce)[1]
    finally:
        C.save_state(state, args.runs_root, args.release)


def _finish(args, state):
    # Completion is based on fresh ADO validation, never on a saved applied flag.
    if state.is_done("bug_bash", S.ID):
        return
    _, orch = C.load_orch(args.runs_root, args.release, args.config)
    orch.record_scout_step("bug_bash", S.ID, "pass", state.get_step("bug_bash", S.ID).note)
    C.save_state(orch.state, args.runs_root, args.release)


def cmd_distribute_tests(args):
    if args.apply and (args.no_oof or args.oof is not None or args.oce is not None):
        print("Do not combine --apply with --oof/--no-oof/--oce. Review the live corrections first.")
        return 1
    state = C.load_state(args.runs_root, args.release)
    with mockctx.active(dict(mocks_mod.load_mocks().get("bug_bash.distribute_tests", {}))):
        report = _inspect(args, state)
        if "error" in report:
            _print_report(report, args.json)
            return 1
        if not args.apply:
            _print_report(report, args.json)
            return 1 if args.validate and not report["valid"] else 0
        if report["valid"]:
            _finish(args, state)
            _print_report(report, args.json)
            return 0
        if not args.review_hash or args.review_hash != report["review_hash"]:
            report["error"] = "Review hash missing/stale. Review the fresh live corrections; no assignments written."
            _print_report(report, args.json)
            return 1
        # Validate every revision before the first write, even when an earlier case is unchanged.
        if any(type(row.get("revision")) is not int or row["revision"] < 1 for row in report["_current"].values()):
            print("Missing ADO work-item revisions; no assignments written.")
            return 1
        failure = None
        for change in report["case_changes"]:
            row = report["_current"][change["case"]]
            ok, detail = D.set_assigned_to(
                change["case"][2:], change["to"], expected_revision=row["revision"])
            if not ok:
                failure = f"{change['case']}: {detail}"
                break
        if failure is None:
            for group in report["_point_sets"]:
                if not any(c["plan_id"] == group["plan_id"] and c["suite_id"] == group["suite_id"]
                           for c in report["point_changes"]):
                    continue
                targets = {p["case_id"]: report["_targets"][f"{group['prefix']}:{p['case_id']}"]
                           for p in group["points"]}
                ok, detail = D.sync_point_testers(
                    group["plan_id"], group["suite_id"], targets,
                    expected_testers={p["id"]: p["tester_id"] for p in group["points"]})
                if not ok:
                    failure = detail
                    break
        # Never replay a saved map: report what actually landed, including partial failures.
        after = _inspect(args, state)
        if failure:
            after["error"] = f"Apply stopped: {failure}. Earlier writes may have succeeded; inspect live ADO before retry."
        elif "error" not in after and not after["valid"]:
            after["error"] = "Live ADO validation still reports mismatches; review the remaining corrections."
        if "error" in after:
            _print_report(after, args.json)
            return 2
        _finish(args, state)
        _print_report(after, args.json)
        return 0


def register(sub):
    p = sub.add_parser("distribute-tests", help="Validate live ADO assignments and preview/apply reviewed corrections")
    p.add_argument("--release", required=True)
    p.add_argument("--oce", default=None, help="Verified primary on-call UPN")
    choice = p.add_mutually_exclusive_group()
    choice.add_argument("--oof", action="append", metavar="UPN", help="Owner-confirmed OOF tester; repeat per person")
    choice.add_argument("--no-oof", action="store_true", help="Confirm nobody is OOF")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply the reviewed live corrections, then validate ADO")
    mode.add_argument("--validate", action="store_true", help="Read only; return nonzero if ADO is not valid")
    p.add_argument("--review-hash", help="Digest from the explicitly approved preview; never a saved assignment list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_distribute_tests)
