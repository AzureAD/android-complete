"""Version-four MRWP failure inventory; never an execution/pass-rate denominator."""
from collections import Counter
from copy import deepcopy

from tools import broker_scenarios as S, testplans as T, pipelines as P
from tools.coordinates import coords
from tools.pipelines.test_evidence import require, result_links, validate_snapshot_tests
from tools.ui_mapping import config_for, route_suite


VERSION = 4
LAYOUT = "mrwp_failure_triage_by_flight"
MARKER = "release-agent:broker-source:v4"
LEGACY_CATEGORIES = ("UI Automation",) + S.CATEGORIES
CATEGORIES = ("UI Automation", "Stress Tests") + S.CATEGORIES
CLASSIFICATION_POLICY = 2


def projection_policy(projection):
    policy = projection.get("classification_policy", 1)
    require(type(policy) is int and policy in (1, CLASSIFICATION_POLICY),
            "Unknown failure classification policy")
    return policy


def _category(name, policy):
    category = P.classify_test_run(name, legacy=policy == 1)
    if category == "stress":
        return "Stress Tests", category
    variant, _ = route_suite(name)
    return ("UI Automation" if category == "ui" and variant is not None else
            "Instrumented Tests" if category == "instrumented" else
            "Unit Tests" if category == "unit" else
            "Lab API" if name == "Lab Api Tests" else
            "Native Auth E2E" if name == "Native Auth E2E Tests" else
            "Other UI Automation"), category


def category_role(flight, category):
    return f"category:{flight}:{category}"


def build_url(build_id):
    return (f"{coords.org_url('engineering')}/{coords.project('engineering')}"
            f"/_build/results?buildId={build_id}")


def project(rc, *, tracked=(), classification_policy=CLASSIFICATION_POLICY):
    try:
        require(isinstance(rc, dict), "Missing current RC evidence")
        policy = projection_policy({"classification_policy": classification_policy})
        number = rc.get("rc")
        require(type(number) is int and number > 0, "Missing explicit current RC")
        rows, warnings, providers = [], [], []
        tracked = {r["key"]: r for r in tracked}
        for flight in ("ECS", "Local"):
            snapshot = rc.get(flight.lower()) or {}
            bid, suites = validate_snapshot_tests(snapshot, allow_empty=True, include_all=True)
            providers.append({"flight": flight, "build_id": bid, "skipped_mapping": []})
            for name, test in suites:
                title = test["title"]
                key = S.scenario_key(number, flight, name, title)
                if test.get("recovered"):
                    warnings.append({"key": key, "title": title, "suite": name, "flight": flight,
                                     "reason": "failed_attempt_recovered", "attempts": deepcopy(test["attempts"])})
                if test["verdict"] != "Failed" and key not in tracked:
                    continue
                category, source_category = _category(name, policy)
                variant, routing = route_suite(name)
                variant = variant or "rc_msal_rc_broker"
                config = config_for(flight, variant)
                require(config, f"Unsupported configuration for {name}")
                parsed = (S._positive_test_id(S._ui_case_id_from_result({"testCaseTitle": title}))
                          if source_category in ("ui", "stress") else None)
                ids = [parsed] if parsed else []
                original = ids[0] if len(ids) == 1 else None
                rows.append({"key": key, "kind": "failure", "category": category, "rc": number,
                             "flight": flight, "build_id": bid, "suite": name, "title": title,
                             "build_url": build_url(bid),
                             "config_id": config, "original_case_id": original, "case_ids": ids,
                             "verdict": test["verdict"], "recovered": bool(test.get("recovered")),
                             "links": result_links(test["attempts"], "engineering"),
                             "routing": routing, "attempts": deepcopy(test["attempts"])})
        rows.sort(key=lambda r: (r["flight"], r["category"], r["suite"], r["title"]))
        require(set(tracked).issubset({r["key"] for r in rows}),
                "Previously tracked failure lacks current exact source observation; history retained")
        projection = {"schema_version": VERSION, "layout": LAYOUT, "rc": number,
                      "count_basis": S.MRWP_COUNT_BASIS, "inventory": [], "triage": rows,
                      "warnings": warnings, "providers": providers,
                      "counts": dict(Counter(r["verdict"] for r in rows))}
        if policy != 1:
            projection["classification_policy"] = policy
        validate_projection(projection, initial=not tracked)
        return True, projection, ""
    except (ValueError, KeyError, TypeError) as exc:
        return False, None, f"Cannot prepare failure-only MRWP source: {exc}"


def validate_projection(projection, *, initial=False):
    require(isinstance(projection, dict) and projection.get("schema_version") == VERSION
            and projection.get("layout") == LAYOUT and projection.get("inventory") == []
            and projection.get("count_basis") == S.MRWP_COUNT_BASIS
            and type(projection.get("rc")) is int and projection["rc"] > 0,
            "Unsupported failure-only source contract")
    require(isinstance(projection.get("triage"), list), "Missing failure scenario inventory")
    policy = projection_policy(projection)
    providers = projection.get("providers")
    require(isinstance(providers, list) and len(providers) == 2
            and all(isinstance(p, dict) for p in providers)
            and [p.get("flight") for p in providers] == ["ECS", "Local"]
            and all(type(p.get("build_id")) is int and p["build_id"] > 0 for p in providers)
            and providers[0]["build_id"] != providers[1]["build_id"],
            "Missing pinned flight build identities")
    seen = set()
    for row in projection["triage"]:
        S._validate_scenario_row(row, projection)
        require(row["key"] not in seen and row["kind"] == "failure"
                and row["category"] == _category(row["suite"], policy)[0]
                and row.get("build_url") == build_url(row["build_id"])
                and (not initial or (row["verdict"] == "Failed" and not row["recovered"])),
                "Invalid/duplicate/nonfailed initial investigation")
        require(row["build_id"] == next(p["build_id"] for p in providers
                                       if p["flight"] == row["flight"]),
                "Failure/provider build identity changed")
        seen.add(row["key"])
    require(projection.get("counts") == dict(Counter(r["verdict"] for r in projection["triage"])),
            "Failure inventory counts changed")


def representations(source):
    validate_projection(source["scenario_projection"])
    rows = deepcopy(source["scenario_projection"]["triage"])
    catalog = set(source["ui_cases"])
    collisions = Counter((r["original_case_id"], r["config_id"]) for r in rows)
    for row in rows:
        original = row["original_case_id"]
        if original not in catalog:
            original = row["original_case_id"] = None
        row["reuse_case_id"] = (original if row["category"] == "UI Automation"
                                and original in catalog
                                and collisions[original, row["config_id"]] == 1 else None)
    return rows


def specs(source, root):
    rows = representations(source)
    result = [("broker", T.BROKER_MANUAL_SUITE_NAME, root, source["broker_configs"], False),
              ("native", T.BROKER_NATIVE_AUTH_SUITE_NAME, root, [], True)]
    if rows:
        result.append(("triage", S.TRIAGE, root, source["root_configs"], False))
    for flight in ("ECS", "Local"):
        selected = [r for r in rows if r["flight"] == flight]
        if not selected:
            continue
        result.append((f"flight:{flight}", flight, "triage", source["root_configs"], False))
        for category in CATEGORIES:
            configs = sorted({r["config_id"] for r in selected if r["category"] == category})
            if configs:
                result.append((category_role(flight, category), category, f"flight:{flight}", configs, False))
    return result


def record_specs(record):
    result = specs(record["source"], record["plan_root_id"])
    amendment = record.get("classification_amendment")
    if amendment:
        intent = amendment["intent"]
        result = [(intent["new_role"], "Stress Tests", parent, configs, native)
                  if role == intent["old_role"] else (role, name, parent, configs, native)
                  for role, name, parent, configs, native in result]
    return result


def automation_contract(record):
    from tools.broker_plans import positive_id
    source, suites = record["source"], record["suites"]
    root = positive_id(record.get("plan_root_id"))
    expected = {role for role, *_ in record_specs(record)}
    require(set(suites) == expected, "Failure hierarchy role IDs changed")
    ids = [positive_id(sid) for sid in suites.values()]
    require(len(ids) == len(set(ids)) and root not in ids, "Duplicate failure hierarchy suite IDs")
    flights = {}
    for flight in ("ECS", "Local"):
        if f"flight:{flight}" in suites:
            flights[flight] = {"suite_id": suites[f"flight:{flight}"],
                              "categories": {category: suites[category_role(flight, category)]
                                             for category in CATEGORIES
                                             if category_role(flight, category) in suites}}
    return {"layout": LAYOUT, "anchor": {"kind": "plan_root", "suite_id": root},
            "root_suite_id": suites.get("triage"), "flights": flights}


def target(record):
    from tools.broker_plans import positive_id
    checked_record(record)
    contract = automation_contract(record)
    require(record.get("automation") == contract and "ui_suite_id" not in record,
            "Failure-only typed destination changed")
    return {"plan_id": positive_id(record["plan_id"]), "layout": LAYOUT,
            "root_suite_id": contract["anchor"]["suite_id"]}


def bind_projection(record, projection):
    checked_record(record)
    validate_projection(projection)
    frozen = record["source"]["scenario_projection"]
    require(projection["rc"] == frozen["rc"]
            and projection["providers"] == frozen["providers"]
            and projection_policy(projection) == policy_for_record(record),
            "Failure-only RC/build identity changed; reviewed new source required before writes")
    current = {r["key"]: r for r in representations(
        {"scenario_projection": projection, "ui_cases": record["source"]["ui_cases"]})}
    saved = record["scenario_bindings"]
    require(set(current) == {r["key"] for r in saved},
            "Failure set changed; new failures require explicit reviewed source, tracked history retained")
    bound = []
    for old in saved:
        row = current[old["key"]]
        for field in ("key", "kind", "category", "rc", "flight", "build_id", "suite",
                      "title", "config_id", "original_case_id", "case_ids", "build_url"):
            require(row.get(field) == old.get(field), "Failure source provenance changed before writes")
        bound.append({**old, **{k: deepcopy(row[k]) for k in ("verdict", "links", "attempts", "recovered")}})
    result = S._bound_projection(projection, bound, VERSION)
    result["provenance"]["tracker_basis"] = "retained_failure_investigations_not_execution_coverage"
    return result


_HISTORY_FIELDS = ("suites", "automation", "scenario_bindings", "fill")


def policy_for_record(record):
    checked_record(record)
    return CLASSIFICATION_POLICY if record.get("classification_amendment") else projection_policy(
        record["source"]["scenario_projection"])


def _stress_intent(record, history, key):
    """One source-bound Local leaf rename, not an arbitrary taxonomy migration."""
    from tools import broker_plans as B
    source = record["source"]
    B._validate_source(source)
    require(source.get("schema_version") == VERSION
            and projection_policy(source["scenario_projection"]) == 1,
            "Stress correction requires the original policy-1 source-v4")
    require(B._runtime_matches(record.get("identity"))
            and record["identity"]["name"].startswith("TEST ONLY - ")
            and record.get("status") == "ready" and record.get("fill_status") == "complete",
            "Stress correction requires this exact completed owned TEST ONLY plan")
    require(isinstance(history, dict) and set(history) == set(_HISTORY_FIELDS),
            "Classification history missing/changed")
    rows = history["scenario_bindings"]
    expected = representations(source)
    require(len(rows) == len(expected)
            and all({k: row.get(k) for k in original} == original
                    for original, row in zip(expected, rows)),
            "Original classification bindings differ from frozen evidence")
    changed = [row for row in rows if row["category"] != _category(row["suite"], CLASSIFICATION_POLICY)[0]]
    require(len(changed) == 1 and changed[0]["key"] == key,
            "Correction requires exactly the selected source-bound Stress classification change")
    row = changed[0]
    require(row["category"] == "UI Automation" and row["flight"] == "Local"
            and row["verdict"] == "Failed" and not row["recovered"]
            and row["routing"] == "stress_exact_combination"
            and _category(row["suite"], CLASSIFICATION_POLICY)[0] == "Stress Tests"
            and not row["reuse_case_id"],
            "Only an owned Failed Local Stress investigation can be corrected")
    old_role, new_role = category_role("Local", "UI Automation"), category_role("Local", "Stress Tests")
    sid = B.positive_id(history["suites"].get(old_role))
    require(new_role not in history["suites"] and row["suite_id"] == sid
            and [r["key"] for r in rows if r["suite_id"] == sid] == [key],
            "Stress correction cannot split an empty/mixed/shared category leaf")
    legacy = {**record, **history}
    legacy.pop("classification_amendment", None)
    require(history["automation"] == automation_contract(legacy),
            "Original typed category hierarchy changed")
    from steps.bug_bash.ui_results import applied_record, _scenario_points
    fill = history["fill"]
    require(fill.get("mapping_version") == VERSION
            and fill.get("target") == target(legacy)
            and fill.get("set_failed") == len(rows) and fill.get("set_passed") == 0
            and fill.get("set_not_applicable") == 0 and fill.get("points_total") == len(rows)
            and not any(fill.get(k) for k in ("uncertain_points", "incomplete_outcome",
                                            "untouched_points", "unmatched_verdicts")),
            "Original complete failure-fill receipt missing/changed")
    applied_record(fill, [r["case_id"] for r in rows], "broker", fill["target"])
    _scenario_points(fill, complete=True, expected_rows=rows)
    points = [p for p in fill["applied_points"] if p["suite_id"] == sid]
    require(len(points) == 1 and points[0]["scenario_key"] == key
            and points[0]["case_id"] == row["case_id"] and points[0]["config_id"] == row["config_id"]
            and points[0]["outcome"] == "Failed",
            "Stress correction requires the exact single acknowledged Failed point")
    suite_receipt = record["operations"].get("suite:" + old_role, {})
    payload = deepcopy(suite_receipt.get("payload"))
    require(isinstance(payload, dict) and suite_receipt.get("status") == "created"
            and suite_receipt.get("id") == sid and suite_receipt.get("sha256") == S.digest(payload)
            and payload == {"name": "UI Automation", "suiteType": "staticTestSuite",
                            "parentSuite": {"id": history["suites"]["flight:Local"]},
                            "inheritDefaultConfigurations": False,
                            "defaultConfigurations": [{"id": row["config_id"]}]},
            "Original category creation receipt changed")
    payload["name"] = "Stress Tests"
    return {"kind": "v4-single-local-stress-leaf-v1", "plan_id": B.positive_id(record["plan_id"]),
            "source_sha256": S.digest(source), "from_policy": 1, "to_policy": CLASSIFICATION_POLICY,
            "identity_sha256": S.digest(record["identity"]),
            "creation_operations_sha256": S.digest(record["operations"]),
            "history_sha256": S.digest(history), "scenario_key": key,
            "case_id": row["case_id"], "point_id": points[0]["point_id"],
            "config_id": row["config_id"], "build_id": row["build_id"], "suite_id": sid,
            "old_role": old_role, "new_role": new_role,
            "external_request": {
                "method": "PATCH",
                "url": f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{record['plan_id']}/suites/{sid}?{T._API}",
                "payload": payload},
            "case_provenance_writes": [], "creates": False, "outcome_writes": False}


def _effective_history(record, history, intent):
    values = deepcopy(history)
    values["suites"][intent["new_role"]] = values["suites"].pop(intent["old_role"])
    for rows in (values["scenario_bindings"], values["fill"]["scenario_bindings"]):
        for row in rows:
            if row["key"] == intent["scenario_key"]:
                row["category"] = "Stress Tests"
    view = {**record, **values}
    values["automation"] = automation_contract(view)
    return values


def checked_record(record, *, recovery=False):
    """Validate the explicit bridge; frozen source and create payloads stay historical."""
    amendment = record.get("classification_amendment")
    if not amendment:
        return record
    require(isinstance(amendment, dict) and amendment.get("status") in ("applying", "applied"),
            "Unknown classification amendment state")
    require(recovery or amendment["status"] == "applied",
            "Classification correction is unsettled; explicit result-only recovery required")
    history, intent = amendment["history"], amendment["intent"]
    require(intent == _stress_intent(record, history, intent["scenario_key"]),
            "Source-bound classification amendment intent changed")
    effective = _effective_history(record, history, intent)
    expected = effective if amendment["status"] == "applied" else history
    require(all(record.get(k) == expected[k] for k in _HISTORY_FIELDS if k != "fill")
            and (amendment["status"] == "applied" or record.get("fill") == history["fill"]),
            "Classification amendment changed historical/current provider facts")
    if amendment["status"] == "applied":
        from steps.bug_bash.ui_results import applied_record, _scenario_points
        current = record.get("fill")
        applied_record(current, [r["case_id"] for r in effective["scenario_bindings"]],
                       "broker", effective["fill"]["target"], complete=False)
        _scenario_points(current, expected_rows=effective["scenario_bindings"])
        original_points = {p["scenario_key"]: p for p in history["fill"]["applied_points"]}
        require(current.get("mapping_version") == VERSION and all(
            all(point.get(k) == original_points[point["scenario_key"]].get(k)
                for k in ("suite_id", "point_id", "case_id", "config_id"))
            for point in current["applied_points"]), "Amended fill point identities changed")
    require(amendment.get("approval_sha256") == S.digest(_correction_body(
               intent, history, amendment["before"], recovery=False))
            and amendment.get("expected_after_sha256") == S.digest(_renamed_snapshot(
                amendment["before"], intent)),
            "Classification correction checkpoint/readback intent changed")
    if amendment["status"] == "applied":
        require(amendment.get("readback_sha256") == amendment["expected_after_sha256"],
                "Classification correction lacks acknowledged exact readback")
    return {**record, **effective} if recovery else record


def expected_rows(record):
    rows = representations(record["source"])
    amendment = record.get("classification_amendment")
    if amendment:
        for row in rows:
            if row["key"] == amendment["intent"]["scenario_key"]:
                row["category"] = "Stress Tests"
    return rows


def _renamed_snapshot(before, intent):
    after = deepcopy(before)
    after["suites"][str(intent["suite_id"])]["name"] = "Stress Tests"
    for point in after["points"][str(intent["suite_id"])]:
        if isinstance(point.get("suite"), dict) and "name" in point["suite"]:
            point["suite"]["name"] = "Stress Tests"
    return after


def _live_classification_snapshot(record, timeout):
    from tools import broker_plans as B
    pid = record["plan_id"]
    ok, plan, detail = T.get_plan(pid, timeout)
    require(ok, detail)
    require(B._COMPLETE in (plan.get("description") or "").splitlines()
            and record["identity"]["marker"] in (plan.get("description") or "").splitlines(),
            "Correction requires exact owned completed plan markers")
    suites, points = {}, {}
    acknowledged = {(p["suite_id"], p["point_id"]): p for p in record["fill"]["applied_points"]}
    for sid in sorted({record["plan_root_id"], *record["suites"].values()}):
        ok, suite, detail = P._ado_rest_get(
            f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{pid}/suites/{sid}?{T._API}", timeout)
        require(ok and isinstance(suite, dict), f"Cannot verify correction suite {sid}: {detail}")
        require(B.positive_id(suite.get("id")) == sid, f"Correction suite {sid} returned another identity")
        suites[str(sid)] = {k: deepcopy(suite.get(k)) for k in
                            ("id", "name", "suiteType", "inheritDefaultConfigurations", "queryString",
                             "defaultTesters")}
        suites[str(sid)]["parentSuite"] = {"id": str((suite.get("parentSuite") or {}).get("id"))}
        suites[str(sid)]["defaultConfigurations"] = sorted(
            B.positive_id(c["id"]) for c in suite.get("defaultConfigurations", []))
        observed = sorted(S._suite_points(pid, sid, timeout), key=lambda p: int(p["id"]))
        for point in observed:
            if point.get("suite") is not None:
                require(isinstance(point["suite"], dict)
                        and B.positive_id(point["suite"].get("id")) == sid
                        and point["suite"].get("name", suite["name"]) == suite["name"],
                        f"Suite {sid} point reports another suite identity")
            receipt = acknowledged.get((sid, int(point["id"])))
            require(point.get("outcome") == (receipt["outcome"] if receipt else "Unspecified")
                    and point.get("state") in (None, "Ready", "NotReady")
                    and not point.get("comment"),
                    f"Suite {sid} has changed human/result/outcome evidence")
            result_ids = []
            for field in ("lastResult", "lastTestRun"):
                value = point.get(field)
                require(value is None or isinstance(value, dict),
                        f"Suite {sid} point {point['id']} has malformed {field}")
                resource_id = value.get("id") if value is not None else None
                require(resource_id is None or type(resource_id) in (int, str),
                        f"Suite {sid} point {point['id']} has malformed {field} ID")
                result_ids.append(None if resource_id in (None, 0, "0") else B.positive_id(resource_id))
            if receipt and any(result_ids):
                # Receipted outcomes can have published provider history; freeze it, never infer its author.
                require(all(result_ids) and point.get("lastResultState") == "Completed"
                        and isinstance(point.get("lastResultDetails"), dict),
                        f"Suite {sid} point {point['id']} has inconsistent published result metadata")
            else:
                require(not any(result_ids) and not point.get("lastResultDetails")
                        and point.get("lastResultState") in (None, "Unspecified"),
                        f"Suite {sid} point {point['id']} has unreceipted/inconsistent result history")
            if receipt:
                require(int(point["testCase"]["id"]) == receipt["case_id"]
                        and int(point["configuration"]["id"]) == receipt["config_id"],
                        "Acknowledged correction point case/configuration changed")
        points[str(sid)] = observed
    require({(int(sid), int(p["id"])) for sid, rows in points.items() for p in rows
             if (int(sid), int(p["id"])) in acknowledged} == set(acknowledged),
            "Correction lost an acknowledged provider point")
    return {"plan": plan, "suites": suites, "points": points}


def _correction_body(intent, history, before, *, recovery):
    return {"intent": intent, "before": before, "recovery_only": recovery,
            "effective_counts": dict(Counter(
                f"{row['flight']}/{_category(row['suite'], CLASSIFICATION_POLICY)[0]}"
                for row in history["scenario_bindings"])),
            "historical_source_retained": True, "workflow_adoption": False}


def preview_stress_correction(record, key, timeout=120, *, recovery=False):
    from tools import broker_plans as B
    amendment = record.get("classification_amendment")
    require(bool(amendment) == recovery,
            "Existing correction requires --recover-stress-category; new correction requires --correct-stress-category")
    if amendment:
        view = checked_record(record, recovery=True)
        require(amendment["intent"]["scenario_key"] == key, "Correction recovery scenario identity changed")
        history, intent = amendment["history"], amendment["intent"]
    else:
        history = {k: deepcopy(record[k]) for k in _HISTORY_FIELDS}
        intent = _stress_intent(record, history, key)
        view = record
    ok, _, detail = B.validate_plan(record["plan_id"], record["identity"], record["source"],
                                    timeout, resource=record, classification_recovery=recovery)
    require(ok, detail)
    observed = _live_classification_snapshot(view, timeout)
    if amendment:
        require(observed == _renamed_snapshot(amendment["before"], intent),
                "Uncertain correction has not read back exactly; never retry suite PATCH")
    plan = _correction_body(intent, history, observed, recovery=recovery)
    return {**plan, "correction_sha256": S.digest(plan)}


def correct_stress_category(record, key, confirmation, checkpoint, timeout=120, *, recovery=False):
    from tools import broker_plans as B
    require(callable(checkpoint), "Durable classification correction checkpoint required")
    preview = preview_stress_correction(record, key, timeout, recovery=recovery)
    require(confirmation == preview["correction_sha256"],
            "Classification correction requires exact reviewed --confirm-correction-sha256")
    intent = preview["intent"]
    if not recovery:
        amendment = {"status": "applying", "intent": deepcopy(intent),
                     "history": {k: deepcopy(record[k]) for k in _HISTORY_FIELDS},
                     "before": deepcopy(preview["before"]), "approval_sha256": confirmation,
                     "expected_after_sha256": S.digest(_renamed_snapshot(preview["before"], intent))}
        record["classification_amendment"] = amendment
        checkpoint()
        original = {**record, **amendment["history"]}
        original.pop("classification_amendment")
        ok, _, detail = B.validate_plan(record["plan_id"], record["identity"], record["source"],
                                        timeout, resource=original)
        require(ok, detail)
        require(_live_classification_snapshot(original, timeout) == preview["before"],
                "Classification evidence changed after reservation; no suite PATCH")
        request = intent["external_request"]
        ok, _, detail = P._ado_rest_send(
            request["url"], request["method"], deepcopy(request["payload"]), timeout)
        require(ok, f"Uncertain Stress suite rename: {detail}; result-only readback recovery, never retry PATCH")
    amendment = record["classification_amendment"]
    view = checked_record(record, recovery=True)
    ok, _, detail = B.validate_plan(record["plan_id"], record["identity"], record["source"],
                                    timeout, resource=record, classification_recovery=True)
    require(ok, f"Classification readback failed: {detail}; never retry PATCH")
    observed = _live_classification_snapshot(view, timeout)
    require(observed == _renamed_snapshot(amendment["before"], intent),
            "Classification rename changed other provider facts; never retry PATCH")
    if amendment["status"] != "applied":
        record.update(_effective_history(record, amendment["history"], intent))
        amendment.update(status="applied", readback_sha256=S.digest(observed))
        checkpoint()
    return {"plan_id": record["plan_id"], "suite_id": intent["suite_id"],
            "classification_policy": CLASSIFICATION_POLICY,
            "effective_counts": preview["effective_counts"], "creates": False,
            "outcome_writes": False, "workflow_adoption": False, "recovery_only": recovery}


def preview(source):
    rows = representations(source)
    return {"schema_version": VERSION, "layout": LAYOUT, "source_sha256": S.digest(source),
            "rc": source["scenario_projection"]["rc"], "providers": source["scenario_projection"]["providers"],
            "manual_broker_cases": len(source["broker_cases"]),
            "manual_broker_points": len(source["broker_cases"]) * len(source["broker_configs"]),
            "failure_points": len(rows), "new_cases": sum(not r["reuse_case_id"] for r in rows),
            "original_case_references": sum(bool(r["reuse_case_id"]) for r in rows),
            "suites": [{"role": role, "name": name, "parent_role": parent,
                        "configurations": configs, "query": source["native_query"] if native else None}
                       for role, name, parent, configs, native in specs(source, "plan-root")],
            "counts": source["scenario_projection"]["counts"], "warnings": source["scenario_projection"]["warnings"],
            "tracker_basis": "failures_only_not_execution_coverage"}


def approval_manifest(source, release, name):
    from tools.broker_plans import identity
    rows = representations(source)
    hierarchy = specs(source, "plan-root")
    membership = {"broker": S._membership_body(
        [(cid, cfg) for cid in source["broker_cases"] for cfg in source["broker_configs"]])}
    grouped = {}
    for row in rows:
        role = category_role(row["flight"], row["category"])
        cid = row["reuse_case_id"] or f"new-case:{row['key']}"
        grouped.setdefault(role, {}).setdefault(cid, set()).add(row["config_id"])
    membership.update({role: [{"workItem": {"id": cid},
                              "pointAssignments": [{"configurationId": cfg} for cfg in sorted(configs)]}
                             for cid, configs in sorted(cases.items(), key=lambda item: str(item[0]))]
                       for role, cases in grouped.items()})
    return {"preview": preview(source), "frozen_source": deepcopy(source), "identity": {
                "release_instance": release, "name": name, "org": T.ORG, "project": T.PROJECT,
                "areaPath": T.BROKER_AREA_PATH, "iteration": T.BROKER_ITERATION},
            "plan_create": {"method": "POST", "url": f"{T.ORG}/{T.PROJECT}/_apis/testplan/plans?{T._API}",
                            "payload": {"name": name, "areaPath": T.BROKER_AREA_PATH,
                                        "iteration": T.BROKER_ITERATION,
                                        "description": identity(release, name)["marker"] + "\n" + MARKER}},
            "suite_payloads": [{"role": role, "method": "POST", "payload": {
                "name": title, "parentSuite": {"id": "new-plan-root" if parent == "plan-root" else f"new-suite:{parent}"},
                "suiteType": "dynamicTestSuite" if native else "staticTestSuite",
                "inheritDefaultConfigurations": native,
                **({"queryString": source["native_query"]} if native else
                   {"defaultConfigurations": [{"id": cfg} for cfg in configs]})}}
                for role, title, parent, configs, native in hierarchy],
            "membership_payloads": [{"suite_role": role, "method": "POST", "payload": payload}
                                    for role, payload in membership.items()],
            "case_payloads": [{"scenario": row, "method": "POST",
                               "url": f"{T.ORG}/{T.PROJECT}/_apis/wit/workitems/$Test%20Case?api-version=7.1",
                               "payload": S._case_payload(row, release)}
                              for row in rows if not row["reuse_case_id"]],
            "outcome_writes": [{"scenario_key": row["key"], "flight": row["flight"],
                                "category": row["category"],
                                "suite_role": category_role(row["flight"], row["category"]),
                                "original_case_id": row["original_case_id"],
                                "case_id": row["reuse_case_id"] or f"new-case:{row['key']}",
                                "config_id": row["config_id"], "outcome": row["verdict"],
                                "source_links": row["links"], "source_build_id": row["build_id"],
                                "source_build_url": row["build_url"]}
                               for row in rows],
            "provider_writes_executed": False, "workflow_adoption": False}
