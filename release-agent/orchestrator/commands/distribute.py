"""Read live ADO distribution and execute only a checked, transient correction plan."""
from __future__ import annotations

import json

from orchestrator import cli_common as C, mocks as mocks_mod, write_review as W
from orchestrator.outcomes import Blocked, Done
from orchestrator.step_context import thaw
from steps.bug_bash import distribute_tests as S
from tools import distribution as D


class DistributionReviewError(ValueError):
    def __init__(self, report):
        super().__init__(report["error"])
        self.report = {key: value for key, value in report.items() if not key.startswith("_")}


def _inspect(args, orch, *, parameters=None):
    parameters = parameters or {
        "oof": [] if args.no_oof else args.oof, "oce": args.oce,
        "roster_file": getattr(args, "roster_file", None),
    }
    context = orch.context(
        "bug_bash", S.ID, parameters=parameters,
        inputs=mocks_mod.load_mocks().get("bug_bash.distribute_tests", {}))
    return S.inspect_distribution(
        context, oof=(list(context.parameters.oof) if context.parameters.oof is not None else None),
        oce=context.parameters.oce, roster_file=context.parameters.roster_file)


def plan_distribution(args, orch):
    """No evidence application: preview cannot save availability or assignments."""
    _, report = _inspect(args, orch)
    if "error" in report:
        raise DistributionReviewError(report)
    provider = report["_review"]["provider"]
    identities = {}
    for change in report["point_changes"]:
        upn = change["to"]
        if upn in identities:
            continue
        observed = {row["identity_id"] for row in report["_current"].values()
                    if D._identity(row["assignee"]) == upn and row.get("identity_id")}
        if len(observed) > 1:
            raise ValueError(f"Conflicting ADO identities for {upn}")
        identities[upn] = (next(iter(observed)) if observed else
                           D.resolve_tester_identity(upn, org=provider["org"]))
    operations = []
    for group in report["_point_sets"]:
        if not any(c["plan_id"] == group["plan_id"] and c["suite_id"] == group["suite_id"]
                   for c in report["point_changes"]):
            continue
        assignments = {str(p["case_id"]): report["_targets"][f"{group['prefix']}:{p['case_id']}"]
                       for p in group["points"]}
        case_identities, updates = {}, {}
        for point in group["points"]:
            key = str(point["case_id"])
            upn = assignments[key]
            identity = identities.get(upn) or report["_current"][f"{group['prefix']}:{key}"]["identity_id"]
            if not identity:
                raise ValueError(f"Missing reviewed tester identity for {upn}")
            case_identities[key] = identity
            if point["tester_id"] != identity:
                updates.setdefault(identity, []).append(int(point["id"]))
        operations.append(W.WriteOperation(
            "align_point_testers", {**provider, "plan_id": group["plan_id"], "suite_id": group["suite_id"]},
            {"assignments": assignments, "identities": case_identities,
             "updates": [{"tester_id": key, "point_ids": sorted(value)}
                         for key, value in sorted(updates.items())]},
            {"testers": {str(p["id"]): p["tester_id"] for p in group["points"]},
             "points": {str(p["id"]): {"case_id": str(p["case_id"]), "config_id": str(p["config_id"])}
                        for p in group["points"]}}))
    return W.WritePlan(
        S.WRITE_COMMAND,
        {"oof": [m["upn"] for m in report["oof_excluded"]], "oce": report["oce_excluded"],
         "roster_file": getattr(args, "roster_file", None)},
        tuple(operations), report["_review"])


def _validate_roster(plan):
    path = plan.parameters.get("roster_file")
    if path:
        try:
            with open(path, encoding="utf-8-sig") as stream:
                observation = json.load(stream)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot revalidate roster observation: {exc}") from None
        expected = thaw(plan.preconditions["inputs"]["roster_source"])
        members, _ = D.roster_observed(expected["group_mail"], observation,
                                       plan.preconditions["inputs"]["owner"])
        current = {"group_mail": expected["group_mail"],
                   "observations": observation["observations"], "members": members}
        if current != expected:
            raise ValueError("Roster source/membership changed after review; no further writes")


def _apply(authorization):
    def validate():
        authorization.validate()
        _validate_roster(authorization.plan)
        review = authorization.plan.preconditions
        if review["fill"].get("binding"):
            D.validate_release_targets(
                review["fill"]["binding"]["release"], review["ccd"], thaw(review["fill"]["binding"]),
                org=review["provider"]["org"], project=review["provider"]["project"],
                expected_names=thaw(review.get("destination_names")))
    for operation in authorization.plan.operations:
        validate()
        target, content, before = (operation.target, operation.content, operation.preconditions)
        if operation.kind == "align_point_testers":
            ok, detail = D.sync_point_testers(
                target["plan_id"], target["suite_id"], thaw(content["assignments"]),
                expected_testers={int(k): v for k, v in before["testers"].items()},
                org=target["org"], project=target["project"], validate=validate,
                expected_identities=thaw(content["identities"]),
                reviewed_updates=thaw(content["updates"]), expected_points=thaw(before["points"]))
        else:
            raise ValueError(f"Unknown distribution operation: {operation.kind}")
        if not ok:
            raise ValueError(detail or "ADO write result unknown")


def cmd_distribute_tests(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config)
    try:
        if args.validate and args.reserve:
            raise ValueError("--validate is read-only and cannot reserve a write.")
        if not (args.apply or args.reserve):
            plan = plan_distribution(args, orch)
            print(json.dumps(W.preview(orch, "bug_bash", S.ID, plan), indent=2))
            return int(args.validate and bool(plan.operations))
        authorization = W.authorize(args, orch, "bug_bash", S.ID,
                                    lambda: plan_distribution(args, orch))
    except DistributionReviewError as exc:
        print(json.dumps({**exc.report, "permission_to_execute": False}))
        return 1
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "permission_to_execute": False}))
        return 1
    if authorization.reserved_only:
        W.print_reservation(authorization)
        return 0

    try:
        _apply(authorization)
        authorization.validate()
        _validate_roster(authorization.plan)
        outcome, report = _inspect(args, orch, parameters=thaw(authorization.plan.parameters))
        current_review = report.get("_review", {})
        before = thaw(authorization.plan.preconditions)
        if any(current_review.get(key) != before[key] for key in (
                "provider", "inputs", "configuration", "broker_plan", "auth_plan",
                "auth_suite", "fill", "targets", "ccd", "destination_names")):
            raise ValueError("Distribution inputs/targets changed before final validation.")
        if not isinstance(outcome, Done):
            raise ValueError(outcome.reason)
        # Availability is the only saved selection; the final read owns completion.
        orch.settle_execution(
            "bug_bash", S.ID, authorization.execution_id, outcome,
            data=thaw(outcome.updates[0].values))
        C.save_state(orch.state, args.runs_root, args.release)
        print(json.dumps({"valid": True, "note": outcome.note}))
        return 0
    except Exception as exc:
        note = (f"Distribution stopped: {exc}. Earlier writes may have succeeded; inspect live "
                "ADO and resolve the owned execution before another attempt.")
        orch.settle_execution("bug_bash", S.ID, authorization.execution_id, Blocked(note))
        C.save_state(orch.state, args.runs_root, args.release)
        print(json.dumps({"error": note}))
        return 2


def register(sub):
    p = sub.add_parser("distribute-tests", help="Preview/apply checked live ADO assignment corrections")
    p.add_argument("--release", required=True)
    p.add_argument("--oce", default=None, help="Verified primary on-call UPN")
    p.add_argument("--roster-file", help="Fresh owner-bound workiq.fetch exact group/member observation bundle")
    choice = p.add_mutually_exclusive_group()
    choice.add_argument("--oof", action="append", metavar="UPN", help="Owner-confirmed OOF tester")
    choice.add_argument("--no-oof", action="store_true", help="Confirm nobody is OOF")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", "--execute", dest="apply", action="store_true")
    mode.add_argument("--validate", action="store_true", help="Read only; nonzero if corrections remain")
    p.add_argument("--execution-id", help="Active reviewed execution id")
    p.add_argument("--json", action="store_true")
    W.add_arguments(p)
    p.set_defaults(func=cmd_distribute_tests)
