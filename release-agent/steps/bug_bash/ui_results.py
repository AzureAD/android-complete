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
    resource = context.evidence.resources.get("broker_test_plan") or {}
    version = resource.get("source", {}).get("schema_version")
    if version == 4 or broker.get("source_version") == 4:
        from tools import broker_failure_triage as F
        broker_target = F.target(thaw(resource))
        if broker.get("automation") != resource.get("automation") or broker.get("ui_suite_id") is not None:
            raise ValueError(f"Failure-only clone hierarchy changed; {REFRESH}")
    else:
        broker_target = {"plan_id": _id(broker.get("plan_id")), "suite_id": _id(broker.get("ui_suite_id"))}
    suite = context.evidence.step("bug_bash", "clone_plans_auth").data or {}
    if (any((rc.get(slot) or {}).get("complete") is not True for slot in ("ecs", "local"))
            or build.get("complete") is not True or test.get("complete") is not True
            or build.get("result") not in ("succeeded", "partiallySucceeded")
            or _id(build.get("rc")) != _id(rc.get("rc"))):
        raise ValueError(f"UI result source is no longer current/completed; {REFRESH}")
    from tools import auth_partition as A
    auth_target = (A.current(context, thaw(suite)) if "monthly_amendment" in suite
                   else A.target(thaw(suite)))
    binding = {"release": context.release.release_id, "rc": _id(rc.get("rc")),
            "ecs_build": _id(rc["ecs"].get("run_id")), "local_build": _id(rc["local"].get("run_id")),
            "auth_apk_build": _id(build.get("run_id")), "auth_test_build": _id(test.get("run_id")),
            "broker": broker_target,
            "auth": auth_target}
    if "monthly" in auth_target:
        from tools import auth_monthly as M, broker_scenarios as S
        binding["monthly_source_sha256"] = S.digest(M.current(thaw(rc), auth_target["monthly"]))
    if broker.get("source_version") in (3, 4) or version in (3, 4):
        from tools import broker_scenarios as S
        if (broker.get("source_version") != version or resource.get("status") != "ready"
                or _id(broker.get("plan_id")) != binding["broker"]["plan_id"]
               or resource.get("plan_id") != binding["broker"]["plan_id"]
               or broker.get("scenario_source_sha256") != S.digest(thaw(resource.get("source")))):
            raise ValueError(f"Scenario source/resource binding changed; {REFRESH}")
        binding["broker_scenarios"] = {
            "source_sha256": broker["scenario_source_sha256"],
            "suite_ids": sorted({r["suite_id"] for r in resource["scenario_bindings"]})}
    return binding


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
        (["steps", "bug_bash.clone_plans_auth", "data", "suite_id"], auth["suite_id"]),
        (own + ["binding"], binding),
        (own + ["status"], "complete"), (own + ["stage"], "complete"),
    ]
    from tools import auth_partition as A
    if A.is_partition(auth):
        values.extend((["steps", "bug_bash.clone_plans_auth", "data", key], thaw(auth[key]))
                      for key in sorted(A.FIELDS | {"plan_id"}))
        if auth.get("layout") == A.MONTHLY_LAYOUT:
            values.extend((["steps", "bug_bash.clone_plans_auth", "data", key], thaw(auth[key]))
                          for key in ("monthly", "monthly_source"))
        if "monthly_amendment" in auth:
            values.append((["steps", "bug_bash.clone_plans_auth", "data", "monthly_amendment"],
                           thaw(auth["monthly_amendment"])))
    field = "automation" if broker.get("source_version") == 4 else "ui_suite_id"
    values.append((["steps", "bug_bash.clone_plans_broker", "data", field], thaw(broker[field])))
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
    if "monthly" in result["binding"]["auth"]:
        from tools import auth_monthly as M, broker_scenarios as S
        monthly = result["binding"]["auth"]["monthly"]
        projection = M.current(thaw(latest_rc(context)), monthly)
        if S.digest(projection) != result["binding"]["monthly_source_sha256"]:
            raise ValueError("Monthly fill source changed")
        M.validate_fill(result.get("monthly") or {}, monthly, projection)
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
        if product == "auth" and record["target"].get("layout"):
            from tools import auth_partition as A
            A.validate_target(record["target"])
            if record["automated_case_ids"] != record["target"]["classification"]["mapped_case_ids"]:
                raise ValueError(f"Published Authenticator classification changed; {REFRESH}")
        if product == "broker" and "broker_scenarios" in result["binding"]:
            if (record.get("mapping_version") != context.evidence.resources["broker_test_plan"]["source"]["schema_version"]
                    or record.get("suite_targets") != result["binding"]["broker_scenarios"]["suite_ids"]):
                raise ValueError(f"Scenario result suite binding changed; {REFRESH}")
            _scenario_points(record, complete=True,
                             expected_rows=thaw(context.evidence.resources["broker_test_plan"]["scenario_bindings"]))
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
            elif record["target"].get("layout"):
                _id(point.get("config_id"))
            if point["outcome"] == "Failed":
                failed.add(cid)
        if failed != set(record["failed_case_ids"]):
            raise ValueError(f"UI fill result failed cases were not applied; {REFRESH}")
    return deepcopy(result)


def applied_record(summary, automated_case_ids, product, target, *, complete=True):
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
        if product == "broker" or product == "auth" and target.get("layout"):
            _id(point.get("config_id"))
        if point["outcome"] == "Failed":
            failed.add(cid)
    result = {"target": deepcopy(target), "automated_case_ids": automated,
              "failed_case_ids": sorted(failed), "applied_points": deepcopy(summary["applied_points"])}
    if summary.get("mapping_version") in (3, 4):
        _scenario_points(summary, complete=complete)
        result.update({k: deepcopy(summary[k]) for k in ("mapping_version", "suite_targets", "scenario_bindings")})
    return result


def _scenario_points(record, *, complete=False, expected_rows=None):
    if record.get("mapping_version") == 4:
        from tools.broker_failure_triage import LAYOUT
        target = record.get("target")
        if (not isinstance(target, dict) or set(target) != {"plan_id", "layout", "root_suite_id"}
                or target.get("layout") != LAYOUT):
            raise ValueError(f"Invalid failure-only plan-root target; {REFRESH}")
        _id(target["root_suite_id"])
    rows = record.get("scenario_bindings")
    targets = record.get("suite_targets")
    if (not isinstance(rows, list) or (not rows and record.get("mapping_version") != 4)
            or any(not isinstance(r, dict) for r in rows)
            or not isinstance(targets, list)
            or targets != sorted({_id(r.get("suite_id")) for r in rows})):
        raise ValueError(f"Missing scenario target/provenance manifest; {REFRESH}")
    expected = {(r["key"], _id(r["suite_id"]), _id(r["case_id"]), _id(r["config_id"])) for r in rows}
    if (len(expected) != len(rows) or len({r["key"] for r in rows}) != len(rows)
            or len({(_id(r["suite_id"]), _id(r["case_id"]), _id(r["config_id"])) for r in rows}) != len(rows)):
        raise ValueError(f"Duplicate source scenario binding; {REFRESH}")
    observed = set()
    for point in record["applied_points"]:
        identity = (point.get("scenario_key"), _id(point.get("suite_id")), _id(point["case_id"]),
                    _id(point["config_id"]))
        if identity not in expected or identity in observed:
            raise ValueError(f"Applied point lost scenario/provider identity; {REFRESH}")
        observed.add(identity)
        if point["outcome"] != next(r["verdict"] for r in rows if r["key"] == point["scenario_key"]):
            raise ValueError(f"Applied scenario outcome lost its source proof; {REFRESH}")
    if complete and observed != expected:
        raise ValueError(f"Completed scenario result omitted source points; {REFRESH}")
    if expected_rows is not None:
        from tools.broker_scenarios import BINDING_FIELDS
        fields = (*BINDING_FIELDS, "build_url") if record.get("mapping_version") == 4 else BINDING_FIELDS
        by_key = {r["key"]: r for r in expected_rows}
        if (set(by_key) != {r["key"] for r in rows} or any(
                any(r.get(k) != by_key[r["key"]].get(k) for k in fields) for r in rows)):
            raise ValueError(f"Published scenario manifest changed its resource provenance; {REFRESH}")
