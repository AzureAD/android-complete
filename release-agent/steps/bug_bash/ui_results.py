"""ui_test_status's published result contract for downstream Bug Bash consumers.

Only the fill owns publication. Consumers validate compact authoritative identities,
not Phase-2 evidence internals, projection rules, or the mutable live outcomes.
"""
from copy import deepcopy

from steps.build_verify._common import latest_rc
from tools import testplans as T

STEP = "ui_test_status"
REFRESH = "refresh the owning clone/verification steps as needed, then re-run ui_test_status"


def _id(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
        raise ValueError(f"UI result binding missing/invalid; {REFRESH}")
    return int(value)


def current_binding(state):
    """The minimal current RC/build/target binding; no evidence-schema dependency."""
    rc = latest_rc(state)
    auth = rc.get("auth") or {}
    build, test = auth.get("build") or {}, auth.get("test") or {}
    broker = state.get_step("bug_bash", "clone_plans_broker").data or {}
    suite = state.get_step("bug_bash", "clone_plans_auth").data or {}
    if (any((rc.get(slot) or {}).get("complete") is not True for slot in ("ecs", "local"))
            or build.get("complete") is not True or test.get("complete") is not True
            or build.get("result") not in ("succeeded", "partiallySucceeded")
            or _id(build.get("rc")) != _id(rc.get("rc"))):
        raise ValueError(f"UI result source is no longer current/completed; {REFRESH}")
    return {"release": state.release_id, "rc": _id(rc.get("rc")),
            "ecs_build": _id(rc["ecs"].get("run_id")), "local_build": _id(rc["local"].get("run_id")),
            "auth_apk_build": _id(build.get("run_id")), "auth_test_build": _id(test.get("run_id")),
            "broker": {"plan_id": _id(broker.get("plan_id")), "suite_id": _id(broker.get("ui_suite_id"))},
            "auth": {"plan_id": _id(T.AUTH_PLAN), "suite_id": _id(suite.get("suite_id"))}}


def completed_result(state):
    """Read a completed, still-current fill receipt or fail closed. Never infer empty work."""
    result = (state.get_step("bug_bash", STEP).data or {}).get("result")
    if (not isinstance(result, dict) or result.get("status") != "complete"
            or result.get("stage") != "complete" or not isinstance(result.get("id"), str)
            or not result["id"]):
        raise ValueError(f"Completed UI fill result unavailable (missing/partial/invalidated); {REFRESH}")
    if result.get("binding") != current_binding(state):
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
