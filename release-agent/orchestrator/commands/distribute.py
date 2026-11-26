"""Read live ADO distribution and execute only a checked, transient correction plan."""
from __future__ import annotations

import json
from copy import deepcopy

from orchestrator import cli_common as C, environment as env_mod, write_review as W
from orchestrator import write_review as revision
from orchestrator.outcomes import Blocked, Done
from orchestrator.step_context import thaw
from steps.bug_bash import distribute_tests as S
from tools import distribution as D, testplans as T


class DistributionReviewError(ValueError):
    def __init__(self, report):
        super().__init__(report["error"])
        self.report = {key: value for key, value in report.items() if not key.startswith("_")}


def _inspect(args, orch, *, parameters=None, permit=None):
    roster_file = getattr(args, "roster_file", None)
    temp_roster = None
    if not roster_file and getattr(args, "auto_roster", False) and orch.state.owner_email:
        import os
        import tempfile
        from orchestrator import m365_capabilities as M
        bundle = M.fetch_live_roster_bundle(orch.state.owner_email)
        fd, temp_roster = tempfile.mkstemp(prefix="release-roster-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh)
        roster_file = temp_roster
    try:
        parameters = parameters or {
            "oof": [] if args.no_oof else args.oof, "oce": args.oce,
            "roster_file": roster_file,
        }
        active_defaults = orch.step_defaults if getattr(orch, "step_defaults", None) else env_mod.load_step_defaults(orch.config_path)
        context = orch.context(
            "bug_bash", S.ID, parameters=parameters,
            permit=permit,
            inputs=env_mod.step_config(active_defaults, "bug_bash", S.ID))
        return S.inspect_distribution(
            context, oof=(list(context.parameters.oof) if context.parameters.oof is not None else None),
            oce=context.parameters.oce, roster_file=context.parameters.roster_file)
    finally:
        if temp_roster:
            try:
                import os
                os.remove(temp_roster)
            except OSError:
                pass


def plan_distribution(args, orch):
    """No evidence application: preview cannot save availability or assignments."""
    execution = orch.step_execution("bug_bash", S.ID)
    if execution.get("write_progress"):
        progress = W.validate_progress(execution["write_progress"])
        original = (progress.get("context") or {}).get("plan")
        if not isinstance(original, dict) or revision.digest(original) != progress["scope"]:
            raise ValueError("Distribution continuation requires its original approved operation scope")
        return _remaining_plan(args, orch, original)
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
        assignments = {str(p["case_id"]): report["_targets"][S.point_work_key(group, p)]
                       for p in group["points"]}
        case_identities, updates = {}, {}
        for point in group["points"]:
            key = str(point["case_id"])
            upn = assignments[key]
            identity = identities.get(upn) or report["_current"][S.point_work_key(group, point)]["identity_id"]
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
             "points": {str(p["id"]): {
                 "case_id": str(p["case_id"]), "config_id": str(p["config_id"]),
                 **({"outcome": p["outcome"]} if "outcome" in p else {})}
                        for p in group["points"]}}))
    return W.WritePlan(
        S.WRITE_COMMAND,
        {"oof": [m["upn"] for m in report["oof_excluded"]], "oce": report["oce_excluded"],
         "roster_file": getattr(args, "roster_file", None)},
        tuple(operations), report["_review"])


def _input_contract(review):
    result = {key: deepcopy(thaw(value)) for key, value in review.items()
              if key not in ("current", "points", "targets")}
    source = result.get("inputs", {}).get("roster_source")
    if isinstance(source, dict):
        result["inputs"]["roster_source"] = {
            key: source[key] for key in ("group_mail", "members") if key in source}
    return result


def _remaining_plan(args, orch, original):
    parameters = deepcopy(original["parameters"])
    if getattr(args, "roster_file", None):
        parameters["roster_file"] = args.roster_file
    requested = [] if args.no_oof else args.oof
    if requested is not None and sorted(D._identity(upn) for upn in requested) != sorted(parameters["oof"]):
        raise ValueError("Availability changed; keep the original attempt and review a new allocation explicitly")
    if args.oce and D._identity(args.oce) != parameters["oce"]:
        raise ValueError("Primary on-call selection changed from the approved distribution")
    _, report = _inspect(args, orch, parameters=parameters)
    if "error" in report:
        raise DistributionReviewError(report)
    if _input_contract(report["_review"]) != _input_contract(original["preconditions"]):
        raise ValueError("Distribution business inputs changed; original operation remains owned")
    groups = {(group["plan_id"], group["suite_id"]): group for group in report["_point_sets"]}
    operations = []
    for saved in original["operations"]:
        target, content = saved["target"], deepcopy(saved["content"])
        group = groups.get((target["plan_id"], target["suite_id"]))
        if group is None:
            raise ValueError("An approved distribution target is missing")
        points = {str(point["id"]): point for point in group["points"]}
        current, updates = {}, {}
        for pid, identity in saved["preconditions"]["points"].items():
            point = points.get(pid)
            if point is None or any(
                    str(point[key]) != str(identity[field])
                    for field, key in (("case_id", "case_id"), ("config_id", "config_id"))
            ) or "outcome" in identity and point.get("outcome") != identity["outcome"]:
                raise ValueError("Approved point identity/configuration/outcome changed")
            tester = point["tester_id"]
            desired = content["identities"][str(point["case_id"])]
            current[pid] = tester
            if tester == desired:
                continue
            if tester != saved["preconditions"]["testers"][pid]:
                raise ValueError("A point Tester changed outside the approved operation; owner review required")
            updates.setdefault(desired, []).append(int(pid))
        if updates:
            content["updates"] = [{"tester_id": key, "point_ids": sorted(ids)}
                                  for key, ids in sorted(updates.items())]
            operations.append(W.WriteOperation(saved["kind"], target, content, {
                "testers": current, "points": saved["preconditions"]["points"]}))
    return W.WritePlan(S.WRITE_COMMAND, parameters, tuple(operations), report["_review"])


def _reconcile_distribution(operation):
    descriptor = operation["descriptor"]
    target = descriptor["target"]
    expected = target["points"]
    url = (f"{target['org']}/{target['project']}/_apis/test/Plans/"
           f"{target['plan_id']}/Suites/{target['suite_id']}/points?api-version=5.0")
    ok, points, detail = D.P._ado_rest_get_all(url, 90)
    if not ok:
        raise ValueError(f"Distribution readback unavailable: {detail}")
    if error := T._point_validation_error(points, require_config=True):
        raise ValueError(error)
    actual = {}
    for point in points:
        pid = str(point["id"])
        if pid not in expected:
            continue
        actual[pid] = {
            "case_id": str(point["testCase"]["id"]),
            "config_id": str(point["configuration"]["id"]),
            "tester_id": D._point_tester_id(point),
            **({"outcome": point.get("outcome") or "Unspecified"}
               if "outcome" in expected[pid] else {}),
        }
    if actual != expected or revision.digest(actual) != descriptor["expected"]:
        raise ValueError("Distribution result is unknown or changed; no attempted update will be repeated")
    return {"target": {key: target[key] for key in ("org", "project", "plan_id", "suite_id")},
            "points_sha256": revision.digest(actual)}


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
            progress = getattr(authorization, "progress", None)
            if progress is not None:
                expected = {
                    pid: {**thaw(identity),
                          "tester_id": content["identities"][str(identity["case_id"])]}
                    for pid, identity in before["points"].items()}
                descriptor = {"kind": "distribution.align_points",
                              "target": {**thaw(target), "points": expected},
                              "expected": revision.digest(expected),
                              "before": revision.digest(before["testers"])}
                key = progress.attempt(revision.digest(descriptor), descriptor)
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
        if progress is not None:
            progress.acknowledge(key)


def cmd_distribute_tests(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config)
    try:
        if getattr(args, "manual_completion", None):
            if args.apply or args.validate:
                raise ValueError("Manual completion is result-only; do not combine it with apply/validate")
            if args.no_oof or args.oof is not None or args.oce or args.roster_file:
                raise ValueError("Manual completion covers the entire original distribution; omit new availability/path selectors")
            progress = W.VerifiedProgress(orch, "bug_bash", S.ID, None, _reconcile_distribution)
            result = W.record_recovery(args, progress)
            C.save_state(orch.state, args.runs_root, args.release)
            print(json.dumps({**result, "status": "done", "verification": "owner_attested",
                              "permission_to_execute": False, "permission_to_send": False}))
            return 0
        if not args.apply:
            plan = plan_distribution(args, orch)
            print(json.dumps(W.preview(orch, "bug_bash", S.ID, plan), indent=2))
            return int(args.validate and bool(plan.operations))
        execution = orch.step_execution("bug_bash", S.ID)
        original = ((execution.get("write_progress") or {}).get("context") or {}).get("plan")
        if original is None:
            original = plan_distribution(args, orch).as_dict()
        progress = W.VerifiedProgress(
            orch, "bug_bash", S.ID, original, _reconcile_distribution,
            context={"plan": original})
        if progress.existing:
            if not args.review_hash:
                args.review_hash = progress.value["reviews"][-1]["hash"]
            if not args.approved_by:
                args.approved_by = progress.value["policy"]["reviewer"]
        authorization = W.authorize(
            args, orch, "bug_bash", S.ID, lambda: plan_distribution(args, orch),
            progress=progress, resume_approved=True)
    except DistributionReviewError as exc:
        print(json.dumps({**exc.report, "permission_to_execute": False}))
        return 1
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "permission_to_execute": False}))
        return 1

    try:
        selection, _ = _inspect(args, orch, parameters=thaw(authorization.plan.parameters),
                                permit=authorization._permit)
        orch.apply_evidence(authorization._permit, selection)
        orch.state.checkpoint()
        _apply(authorization)
        progress.finish()
        authorization.validate()
        _validate_roster(authorization.plan)
        outcome, report = _inspect(args, orch, parameters=thaw(authorization.plan.parameters))
        current_review = report.get("_review", {})
        before = thaw(authorization.plan.preconditions)
        if any(current_review.get(key) != before[key] for key in (
                "provider", "inputs", "configuration", "broker_plan", "auth_plan",
                "auth_suite", "fill", "ccd", "destination_names")):
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
    except (ValueError, TypeError, KeyError, OSError) as exc:
        note = (f"Distribution stopped: {exc}. Earlier writes may have succeeded; inspect live "
                "ADO and resolve the owned execution before another attempt.")
        orch.settle_execution("bug_bash", S.ID, authorization.execution_id, Blocked(note))
        C.save_state(orch.state, args.runs_root, args.release)
        print(json.dumps({"error": note}))
        return 2


def register(sub):
    p = sub.add_parser("distribute-tests", help="Preview/apply checked live ADO Current Tester corrections")
    p.add_argument("--release", required=True)
    p.add_argument("--oce", default=None, help="Verified primary on-call UPN")
    p.add_argument("--roster-file", help="Fresh owner-bound exact group/member observations from verified structured WorkIQ fetch")
    p.add_argument("--auto-roster", action="store_true",
                   help="Fetch the configured distribution roster directly in Python via native WorkIQ CLI without shell $-query quoting")
    choice = p.add_mutually_exclusive_group()
    choice.add_argument("--oof", action="append", metavar="UPN", help="Owner-confirmed OOF tester")
    choice.add_argument("--no-oof", action="store_true", help="Confirm nobody is OOF")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", "--execute", dest="apply", action="store_true")
    mode.add_argument("--validate", action="store_true", help="Read only; nonzero if corrections remain")
    p.add_argument("--json", action="store_true")
    p.add_argument("--manual-completion", help="Owner attests all remaining original distribution work is complete; evidence URL")
    p.add_argument("--confirmed-by", help="Configured release owner explicitly confirming completion and stopped writer")
    p.add_argument("--recovery-reason", help="Owner explanation; manual completion never claims provider verification")
    W.add_arguments(p)
    p.set_defaults(func=cmd_distribute_tests)
