"""Versioned, exact-source Broker inventory and release-owned creation receipts.

Historical master-matrix resources never enter this contract. No source work item
is edited; outcomes and Testers belong to the release's points.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from html import escape
import json
import re
from urllib.parse import urlsplit
from uuid import UUID

from tools import pipelines as P, testplans as T
from tools.pipelines.test_evidence import require, validate_snapshot_tests, result_links
from tools.pipelines.tests_results import _positive_test_id, _ui_case_id_from_result, MRWP_COUNT_BASIS
from tools.ui_mapping import config_for, route_suite

VERSION = 3
MARKER = "release-agent:broker-source:v3"
TRIAGE = "Automation Failure Triage"
CATEGORIES = ("Instrumented Tests", "Unit Tests", "Lab API", "Native Auth E2E", "Other UI Automation")
BINDING_FIELDS = ("key", "suite_id", "case_id", "config_id", "original_case_id",
                  "kind", "category", "rc", "build_id", "title", "suite", "flight", "reuse_case_id")


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def scenario_key(rc, flight, suite, title):
    return digest([int(rc), flight, suite, title])


def project(rc, *, tracked=(), version=VERSION, classification_policy=None):
    """Validate the SAME complete Phase-2 snapshots, retaining exact titles and APIs."""
    if version == 4:
        from tools import broker_failure_triage as F
        return F.project(rc, tracked=tracked, **(
            {"classification_policy": classification_policy} if classification_policy is not None else {}))
    require(version == VERSION, "Unknown scenario projection version")
    try:
        require(isinstance(rc, dict) and _positive_test_id(rc.get("rc")), "current RC not identified")
        inventory, triage, warnings, providers, retained = [], [], [], [], []
        tracked_keys = {r["key"] for r in tracked if r["kind"] == "triage"}
        for slot, flight in (("ecs", "ECS"), ("local", "Local")):
            bid, tests = validate_snapshot_tests(rc.get(slot), include_all=True)
            categories = {s["name"]: s["category"] for s in rc[slot]["tests"]["suites"]}
            totals = Counter()
            for suite, test in tests:
                cat = categories[suite]
                if cat == "stress":
                    cat = P.classify_test_run(suite, legacy=True)
                variant, routing = route_suite(suite)
                device = cat == "ui" and variant is not None
                category = ("Instrumented Tests" if cat == "instrumented" else
                            "Unit Tests" if cat == "unit" else
                            "Lab API" if suite == "Lab Api Tests" else
                            "Native Auth E2E" if suite == "Native Auth E2E Tests" else
                            "Other UI Automation")
                original = (_positive_test_id(_ui_case_id_from_result(
                    {"testCaseTitle": test["title"]})) if cat == "ui" else None)
                row = {"key": scenario_key(rc["rc"], flight, suite, test["title"]),
                       "rc": int(rc["rc"]), "flight": flight, "build_id": bid,
                       "suite": suite, "title": test["title"], "original_case_id": original,
                       "verdict": test["verdict"], "recovered": test["recovered"],
                       "links": result_links(test["attempts"], "engineering"),
                       "category": "Broker/device" if device else category,
                       "config_id": config_for(flight, variant or "rc_msal_rc_broker"),
                       "routing": routing}
                totals[f"{cat}:{test['verdict']}"] += 1
                if test["recovered"]:
                    warnings.append(row)
                if device:
                    inventory.append(row)
                elif test["verdict"] == "Failed":
                    triage.append(row)
                if not device and row["key"] in tracked_keys:
                    retained.append(row)
            providers.append({"flight": flight, "build_id": bid, "counts": dict(sorted(totals.items()))})
        require(providers[0]["build_id"] != providers[1]["build_id"], "MRWP builds must differ")
        require(inventory, "missing Broker/device source scenarios")
        return True, {"schema_version": VERSION, "rc": int(rc["rc"]),
                      "count_basis": MRWP_COUNT_BASIS, "inventory": inventory,
                      "triage": triage, "warnings": warnings, "providers": providers,
                      "retained_triage": retained}, ""
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return False, None, f"{exc}; refresh complete Phase-2 MRWP evidence"


def validate_projection(projection):
    if isinstance(projection, dict) and projection.get("schema_version") == 4:
        from tools import broker_failure_triage as F
        F.validate_projection(projection)
        return
    require(isinstance(projection, dict) and projection.get("schema_version") == VERSION,
            "Invalid Broker scenario contract version")
    require(projection.get("count_basis") == MRWP_COUNT_BASIS, "Invalid Broker scenario count basis")
    require(_positive_test_id(projection.get("rc")), "Invalid Broker scenario RC")
    providers = projection.get("providers")
    require(isinstance(providers, list) and len(providers) == 2
            and [p.get("flight") for p in providers] == ["ECS", "Local"]
            and all(_positive_test_id(p.get("build_id")) for p in providers)
            and providers[0]["build_id"] != providers[1]["build_id"], "Invalid scenario provider binding")
    seen = set()
    for kind in ("inventory", "triage", "warnings"):
        rows = projection.get(kind)
        require(isinstance(rows, list) and (rows or kind != "inventory"), "Missing scenario inventory")
        for row in rows:
            _validate_scenario_row(row, projection)
            variant, _ = route_suite(row["suite"])
            if kind != "warnings":
                require(row["key"] not in seen, "Duplicate source scenario")
                seen.add(row["key"])
            if kind == "inventory":
                require(variant and row.get("category") == "Broker/device", "Nondevice scenario in UI inventory")
            elif kind == "triage":
                require(row.get("category") in CATEGORIES and row["verdict"] == "Failed"
                        and not row["recovered"], "Invalid additional failure investigation")
            else:
                require(row["recovered"] and row["verdict"] == "Passed", "Invalid recovered warning")


def _validate_scenario_row(row, projection):
    require(isinstance(row, dict) and row.get("rc") == projection.get("rc")
            and row.get("flight") in ("ECS", "Local")
            and isinstance(row.get("suite"), str) and row["suite"].strip()
            and isinstance(row.get("title"), str) and row["title"].strip()
            and row.get("key") == scenario_key(row["rc"], row["flight"], row["suite"], row["title"])
            and row.get("verdict") in ("Passed", "Failed", "NotApplicable")
            and type(row.get("recovered")) is bool, "Invalid exact scenario identity")
    provider = next(p for p in projection["providers"] if p["flight"] == row["flight"])
    require(row.get("build_id") == provider["build_id"], "Scenario provider build changed")
    links = row.get("links")
    require(isinstance(links, list) and links
            and all(isinstance(link, dict) and _positive_test_id(link.get("run_id"))
                    and _positive_test_id(link.get("result_id")) for link in links),
            "Scenario provider result links missing")
    require(links == result_links(links, "engineering"), "Scenario provider links changed")
    variant, _ = route_suite(row["suite"])
    require(row.get("config_id") == config_for(row["flight"], variant or "rc_msal_rc_broker"),
            "Scenario configuration changed")
    require(row.get("original_case_id") is None
            or _positive_test_id(row["original_case_id"]), "Invalid original case identity")


def representations(projection, verified_cases):
    """Collisions ALL get separate owned cases; only verified singleton pairs reuse."""
    validate_projection(projection)
    if projection["schema_version"] == 4:
        from tools import broker_failure_triage as F
        return F.representations({"scenario_projection": projection, "ui_cases": verified_cases})
    verified = set(verified_cases)
    counts = Counter((r["original_case_id"], r["config_id"]) for r in projection["inventory"])
    result = []
    for kind in ("inventory", "triage"):
        for row in projection[kind]:
            original = row["original_case_id"]
            if original and original not in verified:
                # A title alone never proves a shared provider case exists.
                original = None
            reuse = kind == "inventory" and original and counts[(original, row["config_id"])] == 1
            result.append({**deepcopy(row), "original_case_id": original,
                           "kind": kind, "reuse_case_id": original if reuse else None})
    return result


def preview(source):
    if source.get("schema_version") == 4:
        from tools import broker_failure_triage as F
        return F.preview(source)
    rows = representations(source["scenario_projection"], source["ui_cases"])
    categories = Counter(r["category"] for r in rows if r["kind"] == "triage")
    return {"schema_version": VERSION, "source_sha256": digest(source),
            "rc": source["scenario_projection"]["rc"],
            "device_points": sum(r["kind"] == "inventory" for r in rows),
            "device_outcomes": dict(Counter(r["verdict"] for r in rows if r["kind"] == "inventory")),
            "new_cases": sum(not r["reuse_case_id"] for r in rows),
            "referenced_points": sum(bool(r["reuse_case_id"]) for r in rows),
            "triage_categories": dict(categories), "additional_failure_points": sum(categories.values()),
            "manual_broker_cases": len(source["broker_cases"]),
            "manual_broker_configs": source["broker_configs"], "native_query": source["native_query"],
            "warnings": len(source["scenario_projection"]["warnings"]),
            "suites": [T.BROKER_MANUAL_SUITE_NAME, T.BROKER_NATIVE_AUTH_SUITE_NAME, T.BROKER_UI_SUITE_NAME]
                      + ([TRIAGE, *[c for c in CATEGORIES if c in categories]] if categories else [])}


def _create(ledger, key, payload, send, checkpoint):
    """Reserve before POST. Unknown creates never retry or discover guessed matches."""
    from tools.broker_plans import positive_id
    saved = ledger.get(key)
    if saved is not None:
        require(saved.get("sha256") == digest(payload), "Creation payload changed; owner recovery required")
        require(saved.get("status") == "created" and _positive_test_id(saved.get("id")),
                f"Uncertain create {key}; retain intent and recover by exact provider receipt, never retry")
        return saved["id"]
    ledger[key] = {"status": "creating", "sha256": digest(payload), "payload": deepcopy(payload)}
    checkpoint()
    ok, receipt, detail = send()
    require(ok and isinstance(receipt, dict) and _positive_test_id(receipt.get("id")),
            f"Uncertain create {key}: {detail}; exact provider receipt required")
    ledger[key].update(status="created", id=positive_id(receipt["id"]))
    checkpoint()
    return ledger[key]["id"]


def _case_payload(row, release):
    from tools import broker_failure_triage as F
    provenance = {**row, "release": release, "contract": F.MARKER if row.get("kind") == "failure" else MARKER}
    fields = {"System.Title": row["title"], "System.AreaPath": T.BROKER_AREA_PATH,
              "System.IterationPath": T.BROKER_ITERATION,
              "System.Description": "<pre>" + escape(json.dumps(provenance, sort_keys=True)) + "</pre>"}
    payload = [{"op": "add", "path": "/fields/" + key, "value": value} for key, value in fields.items()]
    for link in row["links"]:
        payload.append({"op": "add", "path": "/relations/-", "value": {
            "rel": "Hyperlink", "url": link["url"], "attributes": {"comment": "Exact Phase-2 source result"}}})
    if row.get("kind") == "failure":
        payload.append({"op": "add", "path": "/relations/-", "value": {
            "rel": "Hyperlink", "url": row["build_url"],
            "attributes": {"comment": "Exact Phase-2 source build"}}})
    if row["original_case_id"]:
        payload.append({"op": "add", "path": "/relations/-", "value": {
            "rel": "System.LinkTypes.Related",
            "url": f"{T.ORG}/{T.PROJECT}/_apis/wit/workItems/{row['original_case_id']}",
            "attributes": {"comment": "Original verified case; release-owned scenario representation"}}})
    return payload


def _work_item_identity(value):
    from tools.localization import organization
    if (not isinstance(value, str) or any(c.isspace() for c in value)
            or any(c in value for c in ("?", "#", "\\", "%"))):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.port is not None
                or url.netloc.casefold() != (url.hostname or "").casefold()):
            return None
        path = url.path
        if url.hostname == "dev.azure.com":
            _, org_name, path = path.split("/", 2)
            org = organization(f"https://dev.azure.com/{org_name}")
            path = "/" + path
        else:
            org = organization(f"https://{url.netloc}")
            path = path.removeprefix("/DefaultCollection")
        if org != organization(T.ORG):
            return None
        match = re.fullmatch(r"/([^/]+)/_apis/wit/work[Ii]tems/([1-9][0-9]*)", path)
        return (match[1], int(match[2])) if match else None
    except (ValueError, TypeError):
        return None


def _original_case_identity(case_id, proofs, timeout):
    if case_id not in proofs:
        proofs[case_id] = None
        ok, original, _ = P._ado_rest_get(
            f"{T.ORG}/_apis/wit/workitems/{case_id}?api-version=7.1", timeout)
        if not ok or not isinstance(original, dict) or type(original.get("id")) is not int:
            return None
        fields = original.get("fields") or {}
        if not isinstance(fields, dict):
            return None
        project = fields.get("System.TeamProject")
        if (original["id"] != case_id or fields.get("System.WorkItemType") != "Test Case"
                or not isinstance(project, str) or not project.strip()):
            return None
        links = original.get("_links") or {}
        if not isinstance(links, dict):
            return None
        self_link = links.get("self")
        if self_link is not None and not isinstance(self_link, dict):
            return None
        canonical = _work_item_identity(
            self_link.get("href") if self_link is not None else original.get("url"))
        if not canonical or canonical[1] != case_id:
            return None
        try:
            project_id = str(UUID(canonical[0]))
        except ValueError:
            return None
        if canonical[0].lower() == project_id:
            proofs[case_id] = (project_id, case_id, project)
    return proofs[case_id]


def _case_relation_matches(expected, observed, case, case_id, original_proofs, timeout):
    if not isinstance(observed, dict) or observed.get("rel") != expected["rel"]:
        return False
    if observed.get("url") == expected["url"]:
        return True
    if expected["rel"] != "System.LinkTypes.Related":
        return False
    if (case.get("id") != case_id
            or (case.get("fields") or {}).get("System.TeamProject") != T.PROJECT):
        return False
    wanted = _work_item_identity(expected["url"])
    actual = _work_item_identity(observed.get("url"))
    owned = _work_item_identity(case.get("url"))
    if (not wanted or not actual or not owned or wanted[0] != T.PROJECT
            or owned[1] != case_id or actual[1] != wanted[1]):
        return False
    try:
        project_id = str(UUID(owned[0]))
        original_project_id = str(UUID(actual[0]))
    except ValueError:
        return False
    if owned[0].lower() != project_id or actual[0].lower() != original_project_id:
        return False
    # Original work items may belong to another project; IDs are organization-wide.
    original = _original_case_identity(wanted[1], original_proofs, timeout)
    return (original is not None and original[:2] == (original_project_id, wanted[1])
            and (original[2].casefold() != T.PROJECT.casefold() or original_project_id == project_id))


def _suite_points(pid, sid, timeout):
    ok, points, detail = P._ado_rest_get_all(
        f"{T.ORG}/{T.PROJECT}/_apis/test/Plans/{pid}/Suites/{sid}/points?api-version=5.0", timeout)
    require(ok, f"Cannot read suite {sid} points: {detail}")
    require(not T._point_validation_error(points, require_config=True), f"Malformed suite {sid} points")
    return points


def _point_pairs(points):
    return {(int(p["testCase"]["id"]), int(p["configuration"]["id"])) for p in points}


def _membership_body(pairs):
    cases = {}
    for cid, cfg in sorted(pairs):
        cases.setdefault(cid, []).append(cfg)
    return [{"workItem": {"id": cid}, "pointAssignments": [{"configurationId": cfg} for cfg in configs]}
            for cid, configs in cases.items()]


def _pin_suite_configs(pid, sid, configs, identity, record, checkpoint, timeout):
    from tools.broker_plans import positive_id
    body = {"inheritDefaultConfigurations": False,
            "defaultConfigurations": [{"id": c} for c in configs]}
    key = "config-pin:" + str(sid)
    saved = record["operations"].get(key)
    if saved:
        require(saved.get("sha256") == digest(body) and saved.get("status") in ("applying", "applied"),
                f"Suite {sid} configuration-pin receipt changed")

    def read():
        ok, full, detail = P._ado_rest_get(
            f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{pid}/suites/{sid}?{T._API}", timeout)
        require(ok and isinstance(full, dict) and positive_id(full.get("id")) == sid,
                f"Cannot verify suite {sid} configuration identity: {detail}")
        require(all((str((full.get(k) or {}).get("id")) == str(value["id"])
                     if k == "parentSuite" else full.get(k) == value)
                    for k, value in identity.items()),
                f"Suite {sid} configuration identity changed")
        actual = full.get("defaultConfigurations") or []
        require(isinstance(actual, list) and all(isinstance(c, dict) for c in actual),
                f"Suite {sid} configurations malformed")
        ids = [positive_id(c.get("id")) for c in actual]
        return (full.get("inheritDefaultConfigurations") is False
                and len(ids) == len(set(ids)) and set(ids) == set(configs))

    correct = read()
    if not correct:
        require(not saved, f"Suite {sid} configurations changed/uncertain; never reapply a saved pin")
        require(not any(k.startswith("membership:") for k in record["operations"])
                and not _suite_points(pid, sid, timeout),
                f"Suite {sid} configurations changed after membership; preserve points/results")
        record["operations"][key] = {"status": "applying", "sha256": digest(body)}
        checkpoint()
        ok, detail = T._set_suite_configs(pid, sid, configs, timeout)
        require(ok, f"Suite {sid} configuration-pin acknowledgement uncertain: {detail}")
        require(read(), f"Suite {sid} configuration-pin readback differs; never retry blindly")
    if not saved or saved["status"] != "applied":
        record["operations"][key] = {"status": "applied", "sha256": digest(body)}
        checkpoint()


def build(dest_name, source, description, record, checkpoint, timeout=120):
    """Create/resume only the resources of this frozen versioned source."""
    from tools.broker_plans import positive_id
    from tools import broker_failure_triage as F
    v4 = source.get("schema_version") == F.VERSION
    ledger = record.setdefault("operations", {})
    pid = record.get("plan_id")
    try:
        require(callable(checkpoint), "Durable creation checkpoint required")
        require(not record.get("classification_amendment"),
                "Completed classification-amended plans must be validated, never rebuilt")
        rows = representations(source["scenario_projection"], source["ui_cases"])
        payload = {"name": dest_name, "areaPath": T.BROKER_AREA_PATH, "iteration": T.BROKER_ITERATION,
                   "description": description + "\n" + (F.MARKER if v4 else MARKER)}
        created = _create(ledger, "plan", payload, lambda: P._ado_rest_send(
            f"{T.ORG}/{T.PROJECT}/_apis/testplan/plans?{T._API}", "POST", payload, timeout), checkpoint)
        if pid is not None:
            require(positive_id(pid) == created, "Plan receipt disagrees with resource")
        pid = created
        record.update(plan_id=pid, status="created")
        checkpoint()
        ok, plan, detail = T.get_plan(pid, timeout)
        require(ok and isinstance(plan, dict), f"Cannot read created plan: {detail}")
        root = positive_id(plan.get("rootSuiteId"))
        if v4:
            require(record.get("plan_root_id", root) == root, "Plan-root anchor changed")
            record["plan_root_id"] = root
            checkpoint()
        _pin_suite_configs(pid, root, source["root_configs"],
                           {"name": dest_name, "suiteType": "staticTestSuite"},
                           record, checkpoint, timeout)
        specs = [("broker", T.BROKER_MANUAL_SUITE_NAME, root, source["broker_configs"], False),
                 ("native", T.BROKER_NATIVE_AUTH_SUITE_NAME, root, [], True),
                 ("ui", T.BROKER_UI_SUITE_NAME, root, sorted({r["config_id"] for r in rows
                                                           if r["kind"] == "inventory"}), False)]
        if v4:
            specs = F.specs(source, root)
        categories = {r["category"] for r in rows if r["kind"] == "triage"}
        if categories:
            specs.append(("triage", TRIAGE, root, source["root_configs"], False))
        suites = record.setdefault("suites", {})
        for category in CATEGORIES:
            if category in categories:
                specs.append((category, category, "triage",
                              sorted({r["config_id"] for r in rows if r["category"] == category}), False))
        for key, name, parent, configs, native in specs:
            parent = suites[parent] if isinstance(parent, str) else parent
            body = {"name": name, "suiteType": "dynamicTestSuite" if native else "staticTestSuite",
                    "parentSuite": {"id": parent}, "inheritDefaultConfigurations": native}
            if native:
                body["queryString"] = source["native_query"]
            else:
                body["defaultConfigurations"] = [{"id": c} for c in configs]
            sid = _create(ledger, "suite:" + key, body, lambda body=body: P._ado_rest_send(
                f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{pid}/suites?{T._API}",
                "POST", body, timeout), checkpoint)
            suites[key] = sid
            checkpoint()
            if not native:
                _pin_suite_configs(pid, sid, configs,
                                   {k: body[k] for k in ("name", "suiteType", "parentSuite")},
                                   record, checkpoint, timeout)
        represented = []
        if v4:
            record["automation"] = F.automation_contract(record)
            record.setdefault("scenario_bindings", [])
            checkpoint()
        for row in rows:
            cid = row["reuse_case_id"]
            if not cid:
                body = _case_payload(row, record["identity"]["release_id"])
                cid = _create(ledger, "case:" + row["key"], body, lambda body=body: P._ado_rest_send(
                    f"{T.ORG}/{T.PROJECT}/_apis/wit/workitems/$Test%20Case?api-version=7.1",
                    "POST", body, timeout), checkpoint)
            role = (F.category_role(row["flight"], row["category"]) if v4 else
                    "ui" if row["kind"] == "inventory" else row["category"])
            represented.append({**row, "case_id": cid, "suite_id": suites[role]})
            record["scenario_bindings"] = deepcopy(represented)
            checkpoint()
        groups = {suites["broker"]: [(cid, cfg) for cid in source["broker_cases"]
                                     for cfg in source["broker_configs"]]}
        for row in represented:
            groups.setdefault(row["suite_id"], []).append((row["case_id"], row["config_id"]))
        for sid, pairs in groups.items():
            require(len(pairs) == len(set(pairs)), "Distinct source scenarios collapsed to one point")
            body = _membership_body(pairs)
            key = "membership:" + str(sid)
            if key in ledger:
                require(ledger[key].get("sha256") == digest(body), "Membership source changed")
                require(ledger[key].get("status") in ("applying", "applied"), "Invalid membership receipt")
            else:
                ledger[key] = {"status": "applying", "sha256": digest(body)}
                checkpoint()
                ok, _, detail = P._ado_rest_send(
                    f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{pid}/Suites/{sid}/TestCase"
                    "?api-version=7.1-preview.3", "POST", body, timeout)
                require(ok, f"Uncertain membership write: {detail}")
            points = _suite_points(pid, sid, timeout)
            require(_point_pairs(points) == set(pairs),
                    f"Uncertain membership write; exact complete point readback required for suite {sid}, never retry")
            if ledger[key]["status"] == "applied":
                continue
            ledger[key]["status"] = "applied"
            checkpoint()
        return True, pid, ""
    except ValueError as exc:
        return False, pid, str(exc)


def validate_target(pid, root, children, source, resource, timeout=120, *,
                    membership_repair=None, classification_recovery=False):
    """Read every destination identity before binding or filling; no guessed adoption."""
    from tools.broker_plans import positive_id
    from tools import broker_failure_triage as F
    v4 = source.get("schema_version") == F.VERSION
    if v4:
        resource = F.checked_record(resource, recovery=classification_recovery)
    require(not v4 or membership_repair is None, "Legacy matrix repair cannot amend failure-only resources")
    require(isinstance(resource, dict) and resource.get("source") == source
            and resource.get("plan_id") == pid and isinstance(resource.get("operations"), dict),
            "Scenario plan requires original release resource receipts")
    projection = source["scenario_projection"]
    expected_rows = representations(projection, source["ui_cases"])
    frozen_rows = {row["key"]: row for row in expected_rows}
    if v4:
        expected_rows = F.expected_rows(resource)
    rows = resource.get("scenario_bindings")
    require(isinstance(rows, list) and len(rows) == len(expected_rows), "Incomplete scenario creation bindings")
    suites = resource.get("suites") or {}
    categories = {r["category"] for r in expected_rows if r["kind"] == "triage"}
    expected = {"broker": (T.BROKER_MANUAL_SUITE_NAME, root, False),
                "native": (T.BROKER_NATIVE_AUTH_SUITE_NAME, root, True),
                "ui": (T.BROKER_UI_SUITE_NAME, root, False)}
    if categories:
        expected["triage"] = (TRIAGE, root, False)
        expected.update({c: (c, suites.get("triage"), False) for c in categories})
    if v4:
        expected = {key: (name, suites.get(parent) if isinstance(parent, str) else parent, native)
                    for key, name, parent, _, native in F.record_specs(resource)}
        require(resource.get("plan_root_id") == root and resource.get("automation") == F.automation_contract(resource)
                and "ui_suite_id" not in resource, "Failure-only typed hierarchy changed")
    require(set(suites) == set(expected) and len(children) == len(expected)
            and len(set(suites.values())) == len(suites), "Unexpected/missing scenario suites")
    plan_receipt = resource["operations"].get("plan", {})
    plan_payload = {"name": resource["identity"]["name"], "areaPath": T.BROKER_AREA_PATH,
                    "iteration": T.BROKER_ITERATION,
                    "description": resource["identity"]["marker"] + "\n" + (F.MARKER if v4 else MARKER)}
    require(plan_receipt.get("status") == "created" and plan_receipt.get("id") == pid
            and plan_receipt.get("sha256") == digest(plan_payload), "Plan creation receipt changed")
    by_id = {positive_id(s["id"]): s for s in children}
    require(set(by_id) == set(suites.values()), "Scenario suite receipt identity changed")
    suite_configs = {}
    for key, (name, parent, native) in expected.items():
        suite = by_id[suites[key]]
        require(suite.get("name") == name and isinstance(suite.get("parentSuite"), dict)
                and str(suite["parentSuite"].get("id")) == str(parent)
                and suite.get("suiteType") == ("dynamicTestSuite" if native else "staticTestSuite"),
                f"Scenario suite identity changed: {key}")
        ok, full, detail = P._ado_rest_get(
            f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{pid}/suites/{suites[key]}?{T._API}", timeout)
        require(ok and isinstance(full, dict), f"Cannot read scenario suite {key}: {detail}")
        if native:
            require(full.get("inheritDefaultConfigurations") is True
                    and full.get("queryString") == source["native_query"], "Manual Native Auth query changed")
            configs = []
        else:
            configs = (source["broker_configs"] if key == "broker" else source["root_configs"]
                       if key == "triage" else sorted({r["config_id"] for r in expected_rows
                            if (key == "ui" and r["kind"] == "inventory") or r["category"] == key}))
            if v4:
                configs = next(configs for role, _, _, configs, _ in F.record_specs(resource) if role == key)
            require(full.get("inheritDefaultConfigurations") is False
                    and {positive_id(c["id"]) for c in full.get("defaultConfigurations", [])} == set(configs),
                    f"Scenario suite configurations changed: {key}")
            suite_configs[suites[key]] = set(configs)
        payload = {"name": name, "suiteType": "dynamicTestSuite" if native else "staticTestSuite",
                   "parentSuite": {"id": parent}, "inheritDefaultConfigurations": native}
        if native:
            payload["queryString"] = source["native_query"]
        else:
            payload["defaultConfigurations"] = [{"id": c} for c in configs]
        creation_key = key
        amendment = resource.get("classification_amendment")
        if v4 and amendment and key == amendment["intent"]["new_role"]:
            creation_key = amendment["intent"]["old_role"]
            payload["name"] = "UI Automation"
        receipt = resource["operations"].get("suite:" + creation_key, {})
        require(receipt.get("status") == "created" and receipt.get("id") == suites[key]
                and receipt.get("sha256") == digest(payload), "Suite creation receipt changed")
    pairs = {sid: set() for role, sid in suites.items() if role != "native"}
    pairs[suites["broker"]] = {(cid, cfg) for cid in source["broker_cases"] for cfg in source["broker_configs"]}
    if v4:
        pairs[root] = set()
    if categories:
        pairs[suites["triage"]] = set()
    original_proofs = {}
    for expected_row, row in zip(expected_rows, rows):
        require(isinstance(row, dict) and {k: row.get(k) for k in expected_row} == expected_row,
                "Frozen scenario binding changed")
        sid = suites[F.category_role(row["flight"], row["category"]) if v4 else
                     "ui" if row["kind"] == "inventory" else row["category"]]
        cid = positive_id(row.get("case_id"))
        require(row.get("suite_id") == sid, "Scenario target suite changed")
        if row["reuse_case_id"]:
            require(cid == row["reuse_case_id"], "Original case reference changed")
        else:
            receipt = resource["operations"].get("case:" + row["key"], {})
            case_row = frozen_rows[row["key"]]
            require(receipt.get("status") == "created" and receipt.get("id") == cid
                    and receipt.get("sha256") == digest(_case_payload(case_row, resource["identity"]["release_id"])),
                    f"Missing/changed release-owned case receipt (owned case {cid}, scenario {row['key']})")
            ok, case, detail = P._ado_rest_get(
                f"{T.ORG}/{T.PROJECT}/_apis/wit/workitems/{cid}?$expand=relations&api-version=7.1", timeout)
            require(ok and isinstance(case, dict), f"Cannot verify owned case {cid}: {detail}")
            fields = case.get("fields") or {}
            require(positive_id(case.get("id")) == cid and fields.get("System.WorkItemType") == "Test Case",
                    f"Owned scenario case provider identity changed (owned case {cid})")
            payload = _case_payload(case_row, resource["identity"]["release_id"])
            require(all(fields.get(p["path"].split("/")[-1]) == p["value"] for p in payload
                        if p["path"].startswith("/fields/")),
                    f"Owned case provenance/title changed (owned case {cid}, scenario {row['key']})")
            relations = case.get("relations") or []
            require(all(any(_case_relation_matches(p["value"], rel, case, cid, original_proofs, timeout)
                            for rel in relations) for p in payload if p["path"] == "/relations/-"),
                    f"Owned case source/original links missing (owned case {cid}, "
                    f"original case {expected_row.get('original_case_id')}, scenario {row['key']})")
        pair = cid, row["config_id"]
        require(pair not in pairs.setdefault(sid, set()), "Duplicate source scenario point")
        pairs[sid].add(pair)
    for sid, wanted in pairs.items():
        ok, points, detail = P._ado_rest_get_all(
            f"{T.ORG}/{T.PROJECT}/_apis/test/Plans/{pid}/Suites/{sid}/points?api-version=5.0", timeout)
        require(ok, detail)
        require(isinstance(points, list) and all(isinstance(p, dict)
                and isinstance(p.get("testCase"), dict) and isinstance(p.get("configuration"), dict)
                for p in points), "Malformed scenario point membership")
        actual = {(positive_id(p["testCase"].get("id")), positive_id(p["configuration"].get("id")))
                  for p in points}
        require(not T._point_validation_error(points, require_config=True),
                f"Malformed/duplicate scenario membership in suite {sid}")
        receipt = resource["operations"].get("membership:" + str(sid), {})
        body = _membership_body(wanted)
        if wanted:
            require(receipt.get("sha256") == digest(body)
                    and receipt.get("status") in (("applying", "applied")
                                                  if membership_repair is not None else ("applied",)),
                    f"Scenario membership receipt changed for suite {sid}")
        repair_key = "assignment-repair:" + str(sid)
        previous = resource["operations"].get(repair_key)
        if membership_repair is not None:
            _require_unpublished_points(points, sid)
            if previous:
                require(previous.get("sha256") == digest(body)
                        and previous.get("status") in ("applying", "applied"),
                        f"Suite {sid} assignment-repair receipt changed")
                require(actual == wanted, f"Uncertain/applied assignment repair for suite {sid}; readback only, never re-PATCH")
            if actual != wanted:
                expanded = {(cid, cfg) for cid, _ in wanted for cfg in suite_configs[sid]}
                require(wanted <= actual and actual == expanded,
                        f"Suite {sid} is not a proven complete default-configuration expansion")
            if actual != wanted or (previous and previous["status"] == "applying"):
                membership_repair.append({
                    "suite_id": sid, "payload": body, "sha256": digest(body),
                    "before": sorted(points, key=lambda p: int(p["id"])),
                    "remove_pairs": [list(pair) for pair in sorted(actual - wanted)],
                    "recovery_only": bool(previous)})
        else:
            if previous:
                require(previous.get("sha256") == digest(body) and previous.get("status") == "applied",
                        f"Suite {sid} assignment repair is unsettled; explicit readback recovery required")
            require(len(points) == len(actual) and actual == wanted,
                    f"Scenario case/config membership changed for suite {sid}")


def _require_unpublished_points(points, sid):
    for point in points:
        require(isinstance(point.get("outcome"), str) and point["outcome"].casefold() == "unspecified"
                and point.get("state") in (None, "Ready", "NotReady")
                and not point.get("comment") and not point.get("lastResultDetails"),
                f"Suite {sid} has human/completed/unknown point results; repair prohibited")
        for key in ("lastResult", "lastTestRun"):
            result = point.get(key)
            require(result is None or (isinstance(result, dict) and result.get("id") in (None, 0, "0")),
                    f"Suite {sid} has result/run evidence; repair prohibited")


def preview_membership_repair(record, timeout=120):
    from tools import broker_plans as B
    require(record.get("status") == "created" and "fill" not in record and "fill_status" not in record,
            "Assignment repair requires an unpublished created resource without any fill receipt")
    source = record.get("source")
    B._validate_source(source)
    require(source.get("schema_version") == VERSION, "Assignment repair requires frozen source-v3")
    repairs = []
    ok, info, detail = B.validate_plan(record.get("plan_id"), record.get("identity"), source,
                                     timeout, resource=record, membership_repair=repairs)
    require(ok, detail)
    require(B._COMPLETE not in (info.get("description") or "").splitlines(),
            "Published/completed plans cannot receive assignment repair")
    plan = {"kind": "isolated-suite-assignment-repair-v1", "plan_id": record["plan_id"],
            "source_sha256": digest(source), "operations": repairs,
            "scope": "Remove only proven unrun default-expansion points; no creates or outcomes"}
    return {**plan, "repair_sha256": digest(plan)}


def repair_membership(record, confirmation, checkpoint, timeout=120):
    require(callable(checkpoint), "Durable assignment-repair checkpoint required")
    plan = preview_membership_repair(record, timeout)
    require(confirmation == plan["repair_sha256"], "Assignment repair requires exact reviewed --confirm-repair-sha256")
    pid = record["plan_id"]
    for operation in plan["operations"]:
        sid = operation["suite_id"]
        key = "assignment-repair:" + str(sid)
        points = _suite_points(pid, sid, timeout)
        require(sorted(points, key=lambda p: int(p["id"])) == operation["before"],
                f"Suite {sid} changed after repair preview; no assignment write")
        if not operation["recovery_only"]:
            record["operations"][key] = {"status": "applying", "sha256": operation["sha256"],
                                         "approval_sha256": confirmation}
            checkpoint()
            ok, _, detail = P._ado_rest_send(
                f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{pid}/Suites/{sid}/TestCase"
                "?api-version=7.1-preview.3", "PATCH", deepcopy(operation["payload"]), timeout)
            require(ok, f"Uncertain assignment repair for suite {sid}: {detail}; readback recovery only")
        points = _suite_points(pid, sid, timeout)
        _require_unpublished_points(points, sid)
        wanted = {(row["workItem"]["id"], cfg["configurationId"])
                  for row in operation["payload"] for cfg in row["pointAssignments"]}
        require(_point_pairs(points) == wanted,
                f"Assignment repair readback differs for suite {sid}; never re-PATCH")
        record["operations"][key]["status"] = "applied"
        checkpoint()
    return {"plan_id": pid, "repaired_suites": [op["suite_id"] for op in plan["operations"]],
            "workflow_adoption": False, "outcome_writes": False, "creates": False}


def bind_projection(resource, projection):
    """Bind current outcomes to provider IDs without replacing original case identity."""
    if resource["source"].get("schema_version") == 4:
        from tools import broker_failure_triage as F
        return F.bind_projection(resource, projection)
    validate_projection(projection)
    source = resource["source"]["scenario_projection"]
    require(source["rc"] == projection["rc"], "Scenario plan RC changed; explicit new source review required")
    require([(p["flight"], p["build_id"]) for p in source["providers"]] ==
            [(p["flight"], p["build_id"]) for p in projection["providers"]],
            "Scenario MRWP build changed; explicit new source review required")
    rows = resource.get("scenario_bindings") or []
    current = {r["key"]: r for r in [*projection["inventory"], *projection.get("retained_triage", [])]}
    saved = {r["key"]: r for r in rows}
    require({r["key"] for r in projection["inventory"]} ==
            {r["key"] for r in rows if r["kind"] == "inventory"}, "Broker source inventory changed")
    require({r["key"] for r in projection["triage"]} <= set(saved),
            "Additional automation failures require an explicitly reviewed source revision")
    require(set(current) == set(saved), "Historical triage source missing; preserve its case and receipt")
    bound = []
    for key, row in saved.items():
        observed = current[key]
        require(all(observed[k] == row[k] for k in ("flight", "suite", "title", "config_id", "category")),
                "Scenario identity changed")
        bound.append({**deepcopy(row), "verdict": observed["verdict"], "links": observed["links"],
                      "build_id": observed["build_id"], "recovered": observed["recovered"]})
    return _bound_projection(projection, bound, VERSION)


def _bound_projection(projection, bound, version):
    ui = [r for r in bound if r["kind"] == "inventory" or version == 4]
    verdicts = {}
    for row in ui:
        verdicts.setdefault(row["case_id"], {})[T.UI_CONFIG_FLIGHT_VARIANT[row["config_id"]]] = row["verdict"]
    return {"verdicts": verdicts, "scenario_bindings": bound,
            "failures": [r for r in bound if r["verdict"] == "Failed"],
            "provenance": {"mapping_version": version, "rc": projection["rc"],
                           "count_basis": MRWP_COUNT_BASIS, "projection_rule": "exact_source_scenarios",
                           "providers": [{**p, "skipped_mapping": [], "sources": [
                               r for r in bound if r["flight"] == p["flight"]]} for p in projection["providers"]],
                           "warnings": projection["warnings"]}}


def fill(pid, resource, rows, *, suite_id=None, automation_target=None, org, project,
         prior_points=None, checkpoint=None, timeout=120):
    """Fill exact release points, including retained additional failure investigations."""
    from tools import broker_plans as B
    from tools import broker_failure_triage as F
    v4 = resource["source"].get("schema_version") == F.VERSION
    summary = None
    try:
        require(org == T.ORG and project == T.PROJECT and resource.get("plan_id") == int(pid),
                "Scenario fill destination changed")
        if v4:
            require(suite_id is None and automation_target == F.target(resource),
                   "Failure-only fill requires exact typed plan-root destination")
        else:
            require(resource.get("ui_suite_id") == int(suite_id), "Scenario fill destination changed")
        require(callable(checkpoint), "Scenario fill requires durable per-batch receipts")
        ok, _, detail = B.validate_plan(pid, resource["identity"], resource["source"], timeout,
                                         resource=resource)
        require(ok, detail)
        saved = {r["key"]: r for r in resource["scenario_bindings"]}
        require(isinstance(rows, list) and len(rows) == len(saved)
                and {r["key"] for r in rows} == set(saved), "Scenario fill manifest incomplete")
        for row in rows:
            require(all(row[k] == saved[row["key"]][k] for k in BINDING_FIELDS),
                    "Scenario fill provider identity changed")
            if v4:
                require(row.get("build_url") == saved[row["key"]].get("build_url"),
                        "Failure source build link changed")
        groups = {}
        for row in rows:
            groups.setdefault(row["suite_id"], {}).setdefault(row["case_id"], {})[
                T.UI_CONFIG_FLIGHT_VARIANT[row["config_id"]]] = row["verdict"]
        summary = {"target": F.target(resource) if v4 else {"plan_id": int(pid), "suite_id": int(suite_id)},
                   "applied_points": [], "mapping_version": F.VERSION if v4 else VERSION, "suite_targets": sorted(groups),
                   "scenario_bindings": deepcopy(rows),
                   "set_passed": 0, "set_failed": 0, "set_not_applicable": 0,
                   "cases_touched": len({r["case_id"] for r in rows}), "points_total": len(rows),
                   "untouched_points": [], "unmatched_verdicts": []}
        names = {sid: T.BROKER_UI_SUITE_NAME if key == "ui" else key
                 for key, sid in resource["suites"].items()}
        if v4:
            names = {resource["suites"][role]: name for role, name, *_ in F.record_specs(resource)}
        # Preservation is checked for ALL groups before even the first outcome write.
        for sid, verdicts in groups.items():
            ok, points, detail = P._ado_rest_get_all(
                f"{org}/{project}/_apis/test/Plans/{pid}/Suites/{sid}/points?api-version=5.0", timeout)
            require(ok, detail)
            require(not T._point_validation_error(points, require_config=True), "Invalid scenario points")
            desired = [{"point_id": int(p["id"]), "case_id": int(p["testCase"]["id"]),
                        "config_id": int(p["configuration"]["id"]),
                        "outcome": verdicts[int(p["testCase"]["id"])][
                            T.UI_CONFIG_FLIGHT_VARIANT[int(p["configuration"]["id"])]]} for p in points]
            _, error = T._preserved_result_check(points, desired, [
                p for p in prior_points or [] if p.get("suite_id") == sid])
            require(not error, error)
        for sid, verdicts in groups.items():
            before = deepcopy(summary)

            def progress(partial):
                summary["applied_points"] = before["applied_points"] + [
                    {**p, "suite_id": sid, "scenario_key": next(r["key"] for r in rows
                     if r["suite_id"] == sid and r["case_id"] == p["case_id"]
                     and r["config_id"] == p["config_id"])} for p in partial["applied_points"]]
                for key in ("set_passed", "set_failed", "set_not_applicable"):
                    summary[key] = before[key] + partial[key]
                if partial.get("uncertain_points"):
                    summary["uncertain_points"] = [{**p, "suite_id": sid} for p in partial["uncertain_points"]]
                else:
                    summary.pop("uncertain_points", None)
                checkpoint(deepcopy(summary))

            ok, part, detail = T._fill_ui_suite(
                pid, verdicts, timeout, suite_id=sid, suite_name=names[sid], org=org, project=project,
                prior_points=[p for p in prior_points or [] if p.get("suite_id") == sid],
                require_exact=True, checkpoint=progress, **({"exact_suite": True} if v4 else {}))
            if isinstance(part, dict):
                progress(part)
            if not ok:
                return False, summary, detail
        if not groups:
            checkpoint(deepcopy(summary))
        return True, summary, ""
    except (ValueError, KeyError, TypeError) as exc:
        return False, summary, str(exc)


def recover_create(record, key, resource_id, reason, checkpoint, timeout=120):
    """Record an explicitly supplied exact provider receipt; never search by fuzzy name."""
    from tools.broker_plans import positive_id
    require(isinstance(reason, str) and reason.strip(), "Exact receipt recovery requires an owner reason")
    operation = record.get("operations", {}).get(key)
    require(isinstance(operation, dict) and operation.get("status") == "creating",
            "Only an uncertain create can receive an exact provider receipt")
    body = operation.get("payload")
    require(body is not None and operation.get("sha256") == digest(body), "Frozen create payload missing/changed")
    rid = positive_id(resource_id)
    if key == "plan":
        ok, observed, detail = T.get_plan(rid, timeout)
        require(ok and isinstance(observed, dict), detail)
        require(all(observed.get(k) == v for k, v in body.items()), "Exact plan receipt identity mismatch")
    elif key.startswith("suite:"):
        ok, observed, detail = P._ado_rest_get(
            f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{positive_id(record.get('plan_id'))}"
            f"/suites/{rid}?{T._API}", timeout)
        require(ok and isinstance(observed, dict), detail)
        require(str((observed.get("parentSuite") or {}).get("id")) == str(body["parentSuite"]["id"])
                and all(observed.get(k) == v for k, v in body.items()
                        if k not in ("defaultConfigurations", "parentSuite")),
                "Exact suite receipt identity mismatch")
    elif key.startswith("case:"):
        ok, observed, detail = P._ado_rest_get(
            f"{T.ORG}/{T.PROJECT}/_apis/wit/workitems/{rid}?$expand=relations&api-version=7.1", timeout)
        require(ok and isinstance(observed, dict) and observed.get("id") == rid, detail)
        require(all(observed.get("fields", {}).get(p["path"].split("/")[-1]) == p["value"]
                    for p in body if p["path"].startswith("/fields/")), "Exact case receipt provenance mismatch")
        original_proofs = {}
        require(all(any(_case_relation_matches(p["value"], r, observed, rid, original_proofs, timeout)
                        for r in observed.get("relations", [])) for p in body if p["path"] == "/relations/-"),
                f"Exact case receipt source links mismatch (owned case {rid}, operation {key})")
    else:
        raise ValueError("Not a supported create receipt")
    operation.update(status="created", id=rid, recovery={"reason": reason.strip(), "source": "release-owner"})
    checkpoint()
