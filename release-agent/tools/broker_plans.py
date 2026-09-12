"""Broker plan identity, discovery and fail-closed recovery (no engine policy).

The caller supplies a release-owned record and a checkpoint bound to the existing
CLI state lock. ADO has no create idempotency key: uncertain creates stay reserved,
even when a later successful search returns nothing. Partial plans are not deleted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
import re

from tools import pipelines as P, testplans as T

RESOURCE = "broker_test_plan"
MISSING_ID = object()
_MARKER = "release-agent:broker-plan:"
_COMPLETE = "release-agent:build-complete"


def positive_id(value):
    if not re.fullmatch(r"[0-9]+", str(value)) or int(value) <= 0:
        raise ValueError(f"Invalid ADO resource ID: {value!r}")
    return int(value)


def identity(release_id, name):
    return {"release_id": release_id, "name": name, "org": T.ORG, "project": T.PROJECT,
            "area": T.BROKER_AREA_PATH, "iteration": T.BROKER_ITERATION,
            "marker": _MARKER + release_id}


def find_candidates(expected, timeout=120):
    """Include inactive plans and all pages; same-name collisions must not be hidden."""
    ok, plans, detail = P._ado_rest_get_all(
        f"{T.ORG}/{T.PROJECT}/_apis/testplan/plans?{T._API}"
        "&filterActivePlans=false&includePlanDetails=true", timeout)
    if not ok:
        return False, None, detail
    found = {}
    for plan in plans:
        if (not isinstance(plan, dict) or not isinstance(plan.get("name"), str)
                or not plan["name"].strip()
                or (plan.get("description") is not None and not isinstance(plan["description"], str))):
            return False, None, "Malformed plan entry; discovery cannot establish absence"
        pid = positive_id(plan.get("id"))
        if (str(plan.get("name", "")).strip().casefold() == expected["name"].strip().casefold()
                or expected["marker"] in (plan.get("description") or "").splitlines()):
            if pid in found:
                return False, None, "Duplicate plan ID in discovery; search is inconsistent"
            found[pid] = {"id": pid, "name": plan.get("name"), "area_path": plan.get("areaPath"),
                          "iteration": plan.get("iteration"), "url": T.plan_web_url(pid)}
    return True, [found[pid] for pid in sorted(found)], ""


def _validate_source(source):
    if not isinstance(source, dict) or not isinstance(source.get("native_query"), str) or not source["native_query"].strip():
        raise ValueError("Invalid saved Broker source snapshot; owner recovery required")
    for key in ("root_configs", "broker_configs", "ui_configs", "broker_cases", "ui_cases"):
        values = source.get(key)
        if (not isinstance(values, list) or not values
                or any(type(v) is not int or positive_id(v) != v for v in values)
                or len(values) != len(set(values))):
            raise ValueError(f"Invalid saved Broker source snapshot field: {key}")
    matrix = source.get("ui_case_configs")
    if matrix is not None:
        if not isinstance(matrix, dict) or set(matrix) != {str(c) for c in source["ui_cases"]}:
            raise ValueError("Invalid frozen UI case/configuration matrix")
        for configs in matrix.values():
            if (not isinstance(configs, list) or not configs or
                    any(type(c) is not int or c not in T.UI_CONFIG_FLIGHT_VARIANT for c in configs)
                    or len(configs) != len(set(configs))):
                raise ValueError("Invalid frozen UI point configurations")


def _snapshot(timeout, rc=None):
    """Freeze the source BEFORE the first write; recovery never re-snapshots the master."""
    from tools import distribution as D

    ok, root, detail = T._suite_full(T.BROKER_MASTER_ROOT_SUITE, timeout)
    if not ok:
        return False, None, detail
    root_configs = sorted({positive_id(c.get("id")) for c in root.get("defaultConfigurations", [])})
    if root.get("inheritDefaultConfigurations") or not root_configs:
        return False, None, "Master root needs explicit configurations before building the monthly plan"
    ok, query, detail = T._native_auth_query(timeout)
    if not ok:
        return False, None, detail
    source = {"root_configs": root_configs, "native_query": query,
              "broker_configs": list(T.BROKER_CONFIGS), "ui_configs": list(T.BROKER_UI_CONFIGS)}
    for key, root_id in (("broker_cases", T.BROKER_MANUAL_ROOT_SUITE),
                         ("ui_cases", T.BROKER_UI_ROOT_SUITE)):
        ok, cases, detail = D.broker_manual_cases(T.BROKER_MASTER_PLAN, root_id, timeout)
        if not ok:
            return False, None, detail
        source[key] = sorted({positive_id(c["id"]) for c in cases})
        if not source[key]:
            return False, None, f"Master {key} resolved to zero cases"
    ok, projection, detail = P.project_mrwp_ui_results(rc)
    if not ok:
        return False, None, detail
    ok, _, detail = verify_ui_configurations(timeout)
    if not ok:
        return False, None, detail
    matrix = {str(cid): set(source["ui_configs"]) for cid in source["ui_cases"]}
    for cid, values in projection["verdicts"].items():
        if str(cid) not in matrix:
            return False, None, f"Source UI case {cid} is absent from master; owner must review membership"
        for config, pair in T.UI_CONFIG_FLIGHT_VARIANT.items():
            if pair in values:
                matrix[str(cid)].add(config)
    source["ui_case_configs"] = {cid: sorted(configs) for cid, configs in matrix.items()}
    source["ui_projection_provenance"] = projection["provenance"]
    _validate_source(source)
    return True, source, ""


def validate_plan(pid, expected, source=None, timeout=120):
    """Read-only identity/flat-suite/config/point verification; never overwrite results."""
    pid = positive_id(pid)
    if source is not None:
        _validate_source(source)
    ok, info, detail = T.get_plan(pid, timeout)
    if not ok:
        return False, None, f"Recorded/candidate plan {pid} cannot be read: {detail}"
    if (positive_id(info["id"]) != pid or info.get("name") != expected["name"]
            or info.get("areaPath") != expected["area"]
            or info.get("iteration") != expected["iteration"]):
        return False, None, f"Plan {pid} does not match this release's name/area/iteration"
    markers = [line for line in (info.get("description") or "").splitlines()
               if line.startswith(_MARKER)]
    if markers and markers != [expected["marker"]]:
        return False, None, f"Plan {pid} belongs to a different resource identity"
    if markers and source is None and _COMPLETE not in info["description"].splitlines():
        return False, None, f"Plan {pid} is an interrupted build with no saved source snapshot; owner recovery required"
    root = positive_id(info["rootSuiteId"])
    ok, root_info, detail = P._ado_rest_get(
        f"{T.ORG}/{T.PROJECT}/_apis/test/Plans/{pid}/suites/{root}?api-version=5.0", timeout)
    if not ok:
        return False, None, detail
    root_configs = {positive_id(c.get("id")) for c in root_info.get("defaultConfigurations", [])}
    if (root_info.get("inheritDefaultConfigurations") or not root_configs
            or (source and root_configs != set(source["root_configs"]))):
        return False, None, f"Plan {pid}: root configurations are missing or differ from the saved source"
    ok, suites, detail = P._ado_rest_get_all(
        f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{pid}/suites?{T._API}", timeout)
    if not ok:
        return False, None, detail
    children = [s for s in suites if positive_id(s.get("id")) != root]
    specs = ((T.BROKER_MANUAL_SUITE_NAME, "staticTestSuite", "broker"),
             (T.BROKER_NATIVE_AUTH_SUITE_NAME, "dynamicTestSuite", "native"),
             (T.BROKER_UI_SUITE_NAME, "staticTestSuite", "ui"))
    if (len(children) != 3 or {s.get("name") for s in children} != {s[0] for s in specs}
            or any(str((s.get("parentSuite") or {}).get("id")) != str(root) for s in children)):
        return False, None, f"Plan {pid} must contain exactly the three flat release suites; repair this plan, do not recreate it"
    by_name = {s["name"]: s for s in children}
    for name, suite_type, key in specs:
        suite = by_name[name]
        if suite.get("suiteType") != suite_type:
            return False, None, f"Plan {pid}: wrong suite type for {name}"
        sid = positive_id(suite["id"])
        if key == "native":
            ok, full, detail = P._ado_rest_get(
                f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{pid}/suites/{sid}?{T._API}", timeout)
            if not ok:
                return False, None, detail
            if (not full.get("inheritDefaultConfigurations") or not full.get("queryString")
                    or (source and full["queryString"] != source["native_query"])):
                return False, None, f"Plan {pid}: Native Auth query is missing or differs from the saved source"
            continue
        ok, points, detail = P._ado_rest_get_all(
            f"{T.ORG}/{T.PROJECT}/_apis/test/Plans/{pid}/Suites/{sid}/points?api-version=5.0", timeout)
        if not ok:
            return False, None, detail
        actual = {(positive_id((p.get("testCase") or {}).get("id")),
                   positive_id((p.get("configuration") or {}).get("id"))) for p in points}
        cases = source[key + "_cases"] if source else sorted({cid for cid, _ in actual})
        configs = (source[key + "_configs"] if source else
                   T.BROKER_CONFIGS if key == "broker" else T.BROKER_UI_CONFIGS)
        matrix = source.get("ui_case_configs") if source and key == "ui" else None
        expected_points = {(cid, cfg) for cid in cases
                           for cfg in (matrix[str(cid)] if matrix else configs)}
        if not source and key == "ui":
            # An unbound historical plan may carry selective full-combination points.
            # No source means we cannot infer which RC/RC points should exist.
            allowed = {(cid, cfg) for cid in cases for cfg in T.UI_CONFIG_FLIGHT_VARIANT}
            expected_points |= actual & allowed
        if not cases or actual != expected_points or len(points) != len(actual):
            return False, None, f"Plan {pid}: {name} has incomplete or unexpected case/configuration points"
    info["ui_suite_id"] = positive_id(by_name[T.BROKER_UI_SUITE_NAME]["id"])
    return True, info, ""


def ensure_plan(release_id, name, record, checkpoint, *, stored_id=MISSING_ID,
                selected_id=None, reason="", area_path=None, allow_create=True, timeout=120,
                rc=None):
    """Reuse a bound plan or discover exactly one; only a fresh intent can POST a plan."""
    expected = identity(release_id, name)
    if record and record.get("identity") != expected:
        return False, None, "Saved resource identity changed; owner recovery required"
    if record and record.get("status") not in ("creating", "created", "ready", "retry_authorized"):
        return False, None, "Invalid resource lifecycle state; owner recovery required"
    if "source" in record or record.get("status") in ("creating", "created", "retry_authorized"):
        _validate_source(record.get("source"))
    validation_identity = dict(expected, area=record.get("area_path", expected["area"]))
    if area_path is not None:
        if selected_id is None or not (area_path == T.PROJECT or area_path.startswith(T.PROJECT + "\\")):
            return False, None, "An area override requires explicit selection and an area within this project"
        if record.get("area_path") and record["area_path"] != area_path:
            return False, None, "Cannot change an already-bound plan's area identity"
        validation_identity["area"] = area_path
    known = positive_id(record["plan_id"]) if "plan_id" in record else None
    stored = positive_id(stored_id) if stored_id is not MISSING_ID else None
    if record.get("status") in ("created", "ready") and known is None:
        return False, None, "Saved resource lifecycle requires a plan ID; owner recovery required"
    if known is not None and stored is not None and known != stored:
        return False, None, "Resource registry and step reference different plans; owner recovery required"
    pid = known if known is not None else stored
    if selected_id is not None:
        selected_id = positive_id(selected_id)
        if not reason.strip():
            return False, None, "Explicit plan selection requires an owner-confirmed reason"
        if pid and positive_id(pid) != selected_id:
            return False, None, "Cannot replace a bound plan; downstream evidence may already reference it"
    if not pid:
        ok, candidates, detail = find_candidates(expected, timeout)
        if not ok:
            return False, None, f"Could not complete plan discovery: {detail}"
        if selected_id is not None:
            if selected_id not in {p["id"] for p in candidates}:
                return False, None, "Selected plan is not a candidate for this release"
            pid = selected_id
        elif len(candidates) > 1:
            return False, None, ("Multiple Broker plans match this release: "
                                 + ", ".join(str(p["id"]) for p in candidates)
                                 + ". Tell Scout which existing plan to retain; nothing was created.")
        elif candidates:
            pid = candidates[0]["id"]
        elif record and record.get("status") != "retry_authorized":
            return False, None, "Previous plan creation is unresolved. No matching plan is visible; do not retry creation. Owner recovery required."
        elif not allow_create:
            return False, None, "No existing plan selected; recovery never creates a plan"
    if pid:
        pid = positive_id(pid)
        ok, info, detail = validate_plan(pid, validation_identity, record.get("source"), timeout)
        if not ok:
            return False, pid, detail
    else:
        ok, source, detail = _snapshot(timeout, rc)
        if not ok:
            return False, None, f"Cannot snapshot master: {detail}"
        _validate_source(source)
        record.update(identity=expected, status="creating", source=source,
                      started_at=datetime.now(timezone.utc).isoformat())
        checkpoint()  # Save permission-to-create BEFORE calling the non-idempotent POST.

        def created(new_id):
            record.update(plan_id=positive_id(new_id), status="created")
            checkpoint()  # Survives failure/crash during suite creation.

        ok, pid, detail = T.build_broker_plan(
            name, timeout, source=source, description=expected["marker"], on_created=created)
        if not ok:
            return False, pid, detail
        ok, info, detail = validate_plan(pid, expected, source, timeout)
        if not ok:
            return False, pid, detail
    # After lost acknowledgement, publish only the completion marker, never re-create suites.
    if allow_create and record.get("source") and _COMPLETE not in (info.get("description") or "").splitlines():
        ok, _, detail = P._ado_rest_send(
            T._plan_url(pid), "PATCH",
            {"description": (info.get("description") or expected["marker"]) + "\n" + _COMPLETE}, timeout)
        if not ok:
            return False, pid, f"Plan {pid} is verified but its completion marker could not be saved: {detail}"
    record.update(identity=expected, plan_id=pid, ui_suite_id=info["ui_suite_id"],
                  status="ready", area_path=info["areaPath"],
                  verified_at=datetime.now(timezone.utc).isoformat())
    if selected_id is not None:
        record["selection"] = {"plan_id": selected_id, "reason": reason.strip(),
                               "source": "release-owner", "at": record["verified_at"]}
    checkpoint()
    return True, pid, ""


def confirm_not_created(release_id, name, record, checkpoint, reason, timeout=120):
    """Owner-reviewed retry permission, not a create; retain the interrupted attempt."""
    if not reason.strip():
        return False, "Retry authorization requires owner-reviewed evidence in --reason"
    expected = identity(release_id, name)
    if record.get("identity") != expected or record.get("status") != "creating" or "plan_id" in record:
        return False, "Only an unresolved creation with no recorded ID can be reviewed as not created"
    _validate_source(record.get("source"))
    ok, candidates, detail = find_candidates(expected, timeout)
    if not ok:
        return False, f"Cannot verify absence: {detail}"
    if candidates:
        return False, "Matching plans exist; select/repair an existing plan instead of authorizing another create"
    history = list(record.get("attempt_history", []))
    history.append({k: deepcopy(v) for k, v in record.items() if k != "attempt_history"})
    record.update(status="retry_authorized", attempt_history=history,
                  retry_review={"reason": reason.strip(), "source": "release-owner",
                                "at": datetime.now(timezone.utc).isoformat()})
    checkpoint()
    return True, ""


def verify_ui_configurations(timeout=120):
    """Fail before creation if the verified configuration identities have changed."""
    from tools.ui_mapping import CONFIG_NAMES
    ok, configs, detail = P._ado_rest_get_all(
        f"{T.ORG}/{T.PROJECT}/_apis/testplan/configurations?api-version=7.1-preview.1", timeout)
    if not ok:
        return False, None, detail
    found = {}
    if not isinstance(configs, list):
        return False, None, "Malformed UI configuration collection; owner review required"
    for config in configs:
        if not isinstance(config, dict):
            return False, None, "Malformed UI configuration row; owner review required"
        try:
            cid = positive_id(config.get("id"))
        except ValueError:
            return False, None, "Invalid UI configuration ID; owner review required"
        if cid in found:
            return False, None, f"Duplicate UI configuration {cid}; owner review required"
        found[cid] = config
    for cid, name in CONFIG_NAMES.items():
        config = found.get(cid, {})
        raw_values = config.get("values", [])
        if not isinstance(raw_values, list) or any(
                not isinstance(v, dict) or not isinstance(v.get("name"), str)
                or not isinstance(v.get("value"), str) for v in raw_values):
            return False, None, f"Malformed UI configuration {cid} metadata; owner review required"
        values = {v["name"]: v["value"] for v in raw_values}
        if len(values) != len(raw_values):
            return False, None, f"Duplicate UI configuration {cid} metadata; owner review required"
        flight = T.UI_CONFIG_FLIGHT_VARIANT[cid][0]
        if (config.get("name") != name or config.get("state") != "active"
                or values.get("AndroidBrokerFlightProvider", flight) != flight):
            return False, None, f"UI configuration {cid} identity/flight changed; owner review required"
    return True, {cid: found[cid] for cid in CONFIG_NAMES}, ""


def preview_ui_repair(pid, rc, timeout=120):
    """Read-only, deterministic repair proposal. Never infers ownership from outcome equality."""
    from tools.ui_mapping import config_for
    pid = positive_id(pid)
    ok, projection, detail = P.project_mrwp_ui_results(rc)
    if not ok:
        return False, None, detail
    ok, configs, detail = verify_ui_configurations(timeout)
    if not ok:
        return False, None, detail
    ok, sid, detail = T._find_suite_by_name(pid, T.BROKER_UI_SUITE_NAME, timeout)
    if not ok or not sid:
        return False, None, detail or "Broker UI suite missing"
    ok, points, detail = P._ado_rest_get_all(
        f"{T.ORG}/{T.PROJECT}/_apis/test/Plans/{pid}/Suites/{sid}/points?api-version=5.0", timeout)
    if not ok:
        return False, None, detail
    by_pair = {}
    error = T._point_validation_error(points, require_config=True)
    if error:
        return False, None, error
    for point in points:
        pair = (positive_id((point.get("testCase") or {}).get("id")),
                positive_id((point.get("configuration") or {}).get("id")))
        if pair in by_pair:
            return False, None, "Duplicate case/config point; owner review required"
        positive_id(point.get("id"))
        by_pair[pair] = point
    changes = []
    for provider in projection["provenance"]["providers"]:
        for source in provider["sources"]:
            cid, new_config = source["case_id"], source.get("config_id")
            if not cid or not new_config:
                continue
            variant = T.UI_CONFIG_FLIGHT_VARIANT[new_config][1]
            old_config = (config_for(provider["flight"], "rc_msal_prod_broker")
                          if variant == "rc_msal_rc_broker" else new_config)
            old, new = by_pair.get((cid, old_config)), by_pair.get((cid, new_config))
            if old_config != new_config or new is None:
                changes.append({"source": source, "flight": provider["flight"],
                                "build_id": provider["build_id"], "old_config_id": old_config,
                                "new_config_id": new_config, "old_point": old, "new_point": new,
                                "add_point": new is None,
                                "old_result_action": "preserve_owner_review_required"
                                if old_config != new_config and old else "preserve"})
    missing = sorted({(row["source"]["case_id"], row["new_config_id"])
                      for row in changes if row["add_point"]})
    return True, {"plan_id": pid, "suite_id": sid, "rc": rc["rc"], "read_only": True,
                  "configurations": configs, "changes": changes,
                  "unmapped_sources": [{"flight": provider["flight"], **source}
                                       for provider in projection["provenance"]["providers"]
                                       for source in provider["sources"] if source["status"] != "mapped"],
                  "add_points": [{"case_id": cid, "config_id": cfg} for cid, cfg in missing],
                  "historical_cleanup": "NOT authorized: no ownership receipts in historical snapshots. "
                  "Equal outcomes do not establish ownership. Preserve all old/manual results; "
                  "owner review and later exact approval plus provenance and match-before-write "
                  "are required for any cleanup. No apply operation is implemented."}, ""
