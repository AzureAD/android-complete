"""ui_test_status's published result contract for downstream Bug Bash consumers.

Only the fill owns publication. Consumers validate compact authoritative identities,
not Phase-2 evidence internals, projection rules, or the mutable live outcomes.
"""
from copy import deepcopy

from steps.build_verify._common import latest_rc
from tools import testplans as T
from orchestrator.step_context import thaw

STEP = "ui_test_status"
REFRESH = "refresh the owning clone/verification steps as needed, then re-run ui_test_status"


def _id(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
        raise ValueError(f"UI result binding missing/invalid; {REFRESH}")
    return int(value)


def current_binding(context):
    """The minimal current RC/build/target binding; no evidence-schema dependency."""
    rc = latest_rc(context)
    auth = rc.get("auth") or {}
    build, test = auth.get("build") or {}, auth.get("test") or {}
    broker = context.evidence.step("bug_bash", "clone_plans_broker").data or {}
    suite = context.evidence.step("bug_bash", "clone_plans_auth").data or {}
    if (any((rc.get(slot) or {}).get("complete") is not True for slot in ("ecs", "local"))
            or build.get("complete") is not True or test.get("complete") is not True
            or build.get("result") not in ("succeeded", "partiallySucceeded")
            or _id(build.get("rc")) != _id(rc.get("rc"))):
        raise ValueError(f"UI result source is no longer current/completed; {REFRESH}")
    return {"release": context.release.release_id, "rc": _id(rc.get("rc")),
            "ecs_build": _id(rc["ecs"].get("run_id")), "local_build": _id(rc["local"].get("run_id")),
            "auth_apk_build": _id(build.get("run_id")), "auth_test_build": _id(test.get("run_id")),
            "broker": {"plan_id": _id(broker.get("plan_id")), "suite_id": _id(broker.get("ui_suite_id"))},
            "auth": {"plan_id": _id(T.AUTH_PLAN), "suite_id": _id(suite.get("suite_id"))}}


def reminder_preconditions(context):
    """Stable source/represented-failure checks for the downstream human confirmation."""
    binding = current_binding(context)
    result = context.recovery().step("bug_bash", STEP).data.get("result") or {}
    if result.get("binding") != binding or not isinstance(result.get("investigations"), dict):
        raise ValueError(f"UI reminder has no current producer binding; {REFRESH}")
    source = ["pipeline_runs", "rcs", -1]
    own = ["steps", "bug_bash." + STEP, "data", "result"]
    rc = latest_rc(context)
    broker = context.evidence.step("bug_bash", "clone_plans_broker").data
    auth = context.evidence.step("bug_bash", "clone_plans_auth").data
    values = [
        (["release_id"], binding["release"]), (["ccd"], context.release.ccd),
        (["owner_email"], context.release.owner_email),
        (source + ["rc"], rc["rc"]),
        (source + ["ecs", "run_id"], rc["ecs"]["run_id"]),
        (source + ["local", "run_id"], rc["local"]["run_id"]),
        (source + ["auth", "build", "run_id"], rc["auth"]["build"]["run_id"]),
        (source + ["auth", "test", "run_id"], rc["auth"]["test"]["run_id"]),
        (source + ["auth", "build", "rc"], rc["auth"]["build"]["rc"]),
        (["steps", "bug_bash.clone_plans_broker", "data", "plan_id"], broker["plan_id"]),
        (["steps", "bug_bash.clone_plans_broker", "data", "ui_suite_id"], broker["ui_suite_id"]),
        (["steps", "bug_bash.clone_plans_auth", "data", "suite_id"], auth["suite_id"]),
        (own + ["binding"], binding),
        (own + ["status"], "complete"), (own + ["stage"], "complete"),
    ]
    for path in (["ecs"], ["local"], ["auth", "build"], ["auth", "test"]):
        values.append((source + path + ["complete"], True))
    for product in ("broker", "auth"):
        failures = result["investigations"].get(product)
        if not isinstance(failures, (list, tuple)):
            raise ValueError(f"UI reminder failure evidence missing; {REFRESH}")
        values.append((own + ["investigations", product], thaw(failures)))
    return tuple({"path": path, "value": value} for path, value in values)


def completed_result(context):
    """Read a completed, still-current fill receipt or fail closed. Never infer empty work."""
    result = (context.evidence.step("bug_bash", STEP).data or {}).get("result")
    if (not isinstance(result, dict) or result.get("status") != "complete"
            or result.get("stage") != "complete" or not isinstance(result.get("id"), str)
            or not result["id"]):
        raise ValueError(f"Completed UI fill result unavailable (missing/partial/invalidated); {REFRESH}")
    if result.get("binding") != current_binding(context):
        raise ValueError(f"UI fill result RC/build/plan/suite binding changed; {REFRESH}")
    investigations = result.get("investigations")
    if not isinstance(investigations, dict) or any(not isinstance(investigations.get(key), list)
            for key in ("broker", "auth", "unmapped_broker", "report_only_or_unmapped_auth")):
        raise ValueError(f"UI fill result investigation evidence missing; {REFRESH}")
    for product in ("broker", "auth"):
        record = result.get(product)
        if not isinstance(record, dict) or record.get("target") != result["binding"][product]:
            raise ValueError(f"UI fill result has no applied {product} target; {REFRESH}")
        for key in ("automated_case_ids", "failed_case_ids", "applied_points"):
            if not isinstance(record.get(key), list):
                raise ValueError(f"UI fill result missing {product}.{key}; {REFRESH}")
        for key in ("automated_case_ids", "failed_case_ids"):
            ids = record[key]
            if ids != sorted({_id(i) for i in ids}):
                raise ValueError(f"Invalid recorded UI case identities; {REFRESH}")
        seen, failed = set(), set()
        for point in record["applied_points"]:
            if not isinstance(point, dict):
                raise ValueError(f"Malformed applied UI point; {REFRESH}")
            pid, cid = _id(point.get("point_id")), _id(point.get("case_id"))
            outcomes = ("Passed", "Failed", "NotApplicable") if product == "broker" else ("Passed", "Failed")
            if pid in seen or point.get("outcome") not in outcomes or cid not in record["automated_case_ids"]:
                raise ValueError(f"Invalid/duplicate applied UI point; {REFRESH}")
            seen.add(pid)
            if product == "broker":
                _id(point.get("config_id"))
            if point["outcome"] == "Failed":
                failed.add(cid)
        if failed != set(record["failed_case_ids"]):
            raise ValueError(f"UI fill result failed cases were not applied; {REFRESH}")
    return deepcopy(result)


def applied_record(summary, automated_case_ids, product, target):
    """Validate acknowledged provider points before any follow-on owner triage."""
    if (not isinstance(summary, dict) or summary.get("target") != target
            or not isinstance(summary.get("applied_points"), list)):
        raise ValueError(f"Missing actual applied {product} target/points; {REFRESH}")
    automated = sorted({_id(cid) for cid in automated_case_ids})
    seen, failed = set(), set()
    for point in summary["applied_points"]:
        if not isinstance(point, dict):
            raise ValueError(f"Malformed applied UI point; {REFRESH}")
        pid, cid = _id(point.get("point_id")), _id(point.get("case_id"))
        outcomes = ("Passed", "Failed", "NotApplicable") if product == "broker" else ("Passed", "Failed")
        if pid in seen or cid not in automated or point.get("outcome") not in outcomes:
            raise ValueError(f"Invalid/duplicate applied UI point; {REFRESH}")
        seen.add(pid)
        if product == "broker":
            _id(point.get("config_id"))
        if point["outcome"] == "Failed":
            failed.add(cid)
    return {"target": deepcopy(target), "automated_case_ids": automated,
            "failed_case_ids": sorted(failed), "applied_points": deepcopy(summary["applied_points"])}
