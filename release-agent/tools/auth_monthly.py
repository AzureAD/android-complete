"""Release-owned Monthly investigations, not canonical automation cases or gate counts."""
from collections import Counter
from copy import deepcopy
from html import escape
import json

from tools import pipelines as P, testplans as T, broker_scenarios as S
from tools.pipelines.test_evidence import require, result_links

LAYOUT = "auth-manual-automated-monthly-v2"
VERSION = 1
NAME = "Monthly UI Failure Triage"
MARKER = "release-agent:auth-monthly:v1"
TAG = "ReleaseMonthlyInvestigation"
SOURCE_FIELDS = {"version", "release", "rc", "apk_build_id", "test_build_id",
                 "evidence_sha256", "rows"}


def source(rc, release, *, tracked=None):
    ok, facts, detail = P.inspect_auth_ui_evidence(rc, include_attempts=True)
    require(ok, detail)
    require(isinstance(release, str) and bool(release.strip()), "Monthly release identity missing")
    provenance = facts["provenance"]
    identity = {"version": VERSION, "release": release, "rc": provenance["rc"],
                "apk_build_id": provenance["apk_build_id"], "test_build_id": provenance["build_id"]}
    monthly = [s for s in facts["sources"] if s["suite"] == P.MONTHLY_REPORT_ONLY]
    rows = []
    for item in monthly:
        key = S.digest({**identity, "suite": item["suite"], "title": item["title"]})
        if item["verdict"] != "Failed" and (tracked is None or key not in tracked):
            continue
        rows.append({"key": key, "suite": item["suite"], "title": item["title"],
                     "verdict": item["verdict"], "attempts": [
                         {field: attempt[field] for field in ("run_id", "result_id", "outcome")}
                         for attempt in item["attempts"]],
                     "links": deepcopy(item["links"])})
    rows.sort(key=lambda r: r["key"])
    if tracked is not None:
        require({r["key"] for r in rows} == set(tracked),
                "Monthly source scenario coverage changed; refresh clone in a new reviewed layout")
    return {**identity, "evidence_sha256": S.digest(rc["auth"]["test"]["evidence"]), "rows": rows}


def validate_source(value, *, initial=True):
    from tools.auth_partition import positive
    require(isinstance(value, dict) and set(value) == SOURCE_FIELDS
            and type(value["version"]) is int and value["version"] == VERSION
            and isinstance(value["release"], str) and value["release"].strip(),
            "Unknown Monthly source contract")
    for field in ("rc", "apk_build_id", "test_build_id"):
        positive(value[field])
    require(isinstance(value["evidence_sha256"], str) and len(value["evidence_sha256"]) == 64
            and all(c in "0123456789abcdef" for c in value["evidence_sha256"]),
            "Missing Monthly source fingerprint")
    require(isinstance(value["rows"], list), "Missing Monthly source rows")
    keys, attempts = set(), set()
    for row in value["rows"]:
        require(isinstance(row, dict) and set(row) == {"key", "suite", "title", "verdict", "attempts", "links"},
                "Malformed Monthly scenario")
        require(row["suite"] == P.MONTHLY_REPORT_ONLY and isinstance(row["title"], str)
                and row["title"].strip() and row["verdict"] in ("Failed", "Passed", "NotApplicable")
                and (not initial or row["verdict"] == "Failed"),
                "Initial Monthly inventory contains a nonfailure")
        identity = {k: value[k] for k in ("version", "release", "rc", "apk_build_id", "test_build_id")}
        require(row["key"] == S.digest({**identity, "suite": row["suite"], "title": row["title"]})
                and row["key"] not in keys, "Duplicate/changed Monthly scenario ownership")
        keys.add(row["key"])
        require(isinstance(row["attempts"], list) and row["attempts"], "Missing Monthly attempt provenance")
        for attempt in row["attempts"]:
            require(isinstance(attempt, dict) and set(attempt) == {"run_id", "result_id", "outcome"},
                    "Malformed Monthly attempt")
            rid, result = positive(attempt.get("run_id")), positive(attempt.get("result_id"))
            require((rid, result) not in attempts, "Duplicate Monthly source result")
            attempts.add((rid, result))
        from tools.pipelines.tests_results import reconcile_retries
        reconciled = reconcile_retries([{"testCaseTitle": row["title"], "outcome": a["outcome"]}
                                       for a in row["attempts"]])
        verdict = "Passed" if reconciled["passed"] else "Failed" if reconciled["failed"] else "NotApplicable"
        require(row["verdict"] == verdict and row["links"] == result_links(row["attempts"], "auth"),
                "Monthly exact-title retry/link provenance changed")
    require(value["rows"] == sorted(value["rows"], key=lambda r: r["key"]), "Unordered Monthly source")
    return value


def case_payload(row, frozen):
    provenance = {"contract": MARKER, "ownership": MARKER + ":" + row["key"],
                  **{k: frozen[k] for k in SOURCE_FIELDS - {"rows"}}, "scenario": row}
    fields = {"System.Title": row["title"], "System.Tags": TAG, "System.AreaPath": T.AUTH_AREA_PATH,
              "System.Description": "<pre>" + escape(json.dumps(provenance, sort_keys=True)) + "</pre>"}
    payload = [{"op": "add", "path": "/fields/" + k, "value": v} for k, v in fields.items()]
    for url in [link["url"] for link in row["links"]] + [
            f"{P.AUTH_ORG}/{P.AUTH_PROJECT}/_build/results?buildId={frozen[k]}"
            for k in ("apk_build_id", "test_build_id")]:
        payload.append({"op": "add", "path": "/relations/-", "value": {
            "rel": "Hyperlink", "url": url, "attributes": {"comment": "Exact Monthly source provenance"}}})
    return payload


def suite_payload(parent):
    return {"name": NAME, "suiteType": "staticTestSuite", "parentSuite": {"id": parent},
            "inheritDefaultConfigurations": True}


def _base(record):
    return f"{T.ORG}/{T.PROJECT}/_apis/testplan/Plans/{record['plan_id']}"


def _receipt(record, key, body, resource_id):
    receipt = record["operations"].get(key, {})
    require(receipt.get("status") == "created" and receipt.get("id") == resource_id
            and receipt.get("sha256") == S.digest(body) and receipt.get("payload") == body,
            "Monthly create acknowledgement/provenance changed")


def checked(record, *, ready=True):
    from tools.auth_partition import positive
    require(isinstance(record, dict) and set(record) <= {
        "version", "source", "source_sha256", "plan_id", "parent_suite_id", "operations",
        "suite_id", "bindings", "status"}, "Malformed Monthly owned record")
    require(type(record.get("version")) is int and record.get("version") == VERSION
            and record.get("plan_id") == T.AUTH_PLAN
            and positive(record.get("parent_suite_id")) != T.AUTH_ROOT_SUITE,
            "Monthly parent/plan ownership changed")
    frozen = validate_source(record["source"])
    require(record.get("source_sha256") == S.digest(frozen) and isinstance(record.get("operations"), dict),
            "Monthly frozen source/ledger changed")
    if ready:
        require(record.get("status") == "ready", "Monthly clone incomplete")
        rows = frozen["rows"]
        bindings = record.get("bindings")
        require(isinstance(bindings, list), "Monthly point manifest missing")
        if not rows:
            require(record.get("suite_id") is None and not bindings and not record["operations"],
                    "Zero Monthly failures must not create an empty folder")
            return record
        sid = positive(record.get("suite_id"))
        require(sid not in (T.AUTH_ROOT_SUITE, record["parent_suite_id"]), "Monthly suite identity conflicts")
        _receipt(record, "suite", suite_payload(record["parent_suite_id"]), sid)
        case_ids, point_ids, pairs = set(), set(), set()
        require({b.get("key") for b in bindings} == {r["key"] for r in rows}, "Monthly bindings incomplete")
        for b in bindings:
            require(set(b) == {"key", "case_id", "config_id", "point_id"}, "Malformed Monthly point binding")
            cid, cfg, pid = (positive(b[k]) for k in ("case_id", "config_id", "point_id"))
            require(pid not in point_ids and (cid, cfg) not in pairs, "Duplicate Monthly point/configuration")
            point_ids.add(pid)
            pairs.add((cid, cfg))
            case_ids.add(cid)
            row = next(r for r in rows if r["key"] == b["key"])
            _receipt(record, "case:" + b["key"], case_payload(row, frozen), cid)
        require(len(case_ids) == len(rows), "Monthly scenarios collapsed to shared case IDs")
        membership = record["operations"].get("membership", {})
        require(membership.get("status") == "applied"
                and membership.get("sha256") == S.digest(S._membership_body(pairs)),
                "Monthly membership acknowledgement missing/changed")
        require(set(record["operations"]) == {"suite", "membership"} | {"case:" + r["key"] for r in rows},
                "Unexpected Monthly provider operations")
    return record


def _configs(record, timeout):
    from tools.auth_partition import positive
    sid, seen = record["parent_suite_id"], set()
    while sid not in seen:
        seen.add(sid)
        ok, parent, detail = T.get_suite(record["plan_id"], sid, timeout)
        require(ok and isinstance(parent, dict) and str(parent.get("id")) == str(sid)
                and parent.get("suiteType") == "staticTestSuite", "Monthly configuration parent unavailable: " + detail)
        configs = parent.get("defaultConfigurations")
        require(configs is None or isinstance(configs, list), "Monthly inherited configurations unavailable")
        if configs:
            require(all(isinstance(c, dict) and type(c.get("id")) in (int, str) for c in configs),
                    "Malformed Monthly inherited configuration")
            require(all(str(c["id"]).isascii() and str(c["id"]).isdigit() for c in configs),
                    "Malformed Monthly inherited configuration id")
            ids = [positive(int(c["id"])) for c in configs]
            require(len(set(ids)) == len(ids), "Duplicate Monthly inherited configurations")
            return sorted(ids)
        require(parent.get("inheritDefaultConfigurations") is True,
                "Monthly inherited configurations unavailable")
        require(isinstance(parent.get("parentSuite"), dict), "Invalid Monthly configuration parent identity")
        parent_id = parent["parentSuite"].get("id", 0)
        require(type(parent_id) in (int, str) and str(parent_id).isascii()
                and str(parent_id).isdigit(), "Invalid Monthly configuration parent identity")
        sid = positive(int(parent_id))
    raise ValueError("Monthly configuration inheritance cycle")


def validate_live(record, timeout=120):
    checked(record)
    if not record["source"]["rows"]:
        return []
    sid, parent = record["suite_id"], record["parent_suite_id"]
    options = {"plan_id": record["plan_id"], "parent_suite_id": parent, "suite_type": "staticTestSuite"}
    ok, info, detail = T.validate_auth_suite(sid, NAME, None, timeout, **options)
    require(ok, "Monthly leaf identity changed: " + detail)
    ok, found, detail = T.find_auth_suite(NAME, None, timeout, **options)
    require(ok and isinstance(found, dict) and int(found["id"]) == sid, "Monthly leaf no longer unique: " + detail)
    ok, children, detail = P._ado_rest_get_all(_base(record) + "/suites?" + T._API, timeout)
    require(ok and not any(str((c.get("parentSuite") or {}).get("id")) == str(sid) for c in children),
            "Monthly target is not a static leaf")
    configs = _configs(record, timeout)
    points = S._suite_points(record["plan_id"], sid, timeout)
    expected = {(b["point_id"], b["case_id"], b["config_id"]) for b in record["bindings"]}
    require({(int(p["id"]), int(p["testCase"]["id"]), int(p["configuration"]["id"])) for p in points} == expected,
            "Monthly live point/case/configuration identities changed")
    rows = record["source"]["rows"]
    for row in rows:
        cid = next(b["case_id"] for b in record["bindings"] if b["key"] == row["key"])
        require({b["config_id"] for b in record["bindings"] if b["key"] == row["key"]} == set(configs),
                "Monthly inherited configuration set changed")
        _validate_case(cid, case_payload(row, record["source"]), timeout)
    return points


def _validate_case(cid, payload, timeout):
    ok, case, detail = P._ado_rest_get(
        f"{T.ORG}/{T.PROJECT}/_apis/wit/workitems/{cid}?$expand=relations&api-version=7.1", timeout)
    require(ok and isinstance(case, dict) and int(case.get("id", 0)) == cid, "Monthly owned case unavailable: " + detail)
    fields = case.get("fields", {})
    require(fields.get("System.WorkItemType") == "Test Case" and fields.get("System.TeamProject") == T.PROJECT,
            "Monthly owned case provider identity changed")
    require(all(fields.get(p["path"].split("/")[-1]) == p["value"] for p in payload
                if p["path"].startswith("/fields/")), "Monthly case title/tag/ownership provenance changed")
    expected = Counter((p["value"]["rel"], p["value"]["url"]) for p in payload if p["path"] == "/relations/-")
    observed = Counter((r.get("rel"), r.get("url")) for r in case.get("relations", []) if r.get("rel") == "Hyperlink")
    require(all(observed[k] >= n for k, n in expected.items()), "Monthly source links disappeared")


def ensure(frozen, parent, record, checkpoint, timeout=120):
    """Own suite, cases and membership. Reuse Broker's intent-before-POST primitive."""
    try:
        validate_source(frozen)
        if record:
            checked(record, ready=False)
            require(record["source"] == frozen and record["parent_suite_id"] == parent,
                    "Monthly frozen creation source/parent changed")
        else:
            record.update(version=VERSION, source=deepcopy(frozen), source_sha256=S.digest(frozen),
                          plan_id=T.AUTH_PLAN, parent_suite_id=parent, operations={}, bindings=[], status="creating")
            checkpoint(record)
        if not frozen["rows"]:
            record.update(status="ready", suite_id=None)
            checkpoint(record)
            checked(record)
            return True, record, ""
        if record.get("status") == "ready":
            validate_live(record, timeout)
            return True, record, ""
        ledger = record["operations"]
        persist = lambda: checkpoint(record)
        if "suite" not in ledger:
            ok, found, detail = T.find_auth_suite(NAME, None, timeout, plan_id=T.AUTH_PLAN,
                                                parent_suite_id=parent, suite_type="staticTestSuite")
            require(ok and found is None, "Foreign same-named Monthly leaf; no adoption: " + detail)
        configs = _configs(record, timeout)
        body = suite_payload(parent)
        sid = S._create(ledger, "suite", body, lambda: P._ado_rest_send(
            _base(record) + "/suites?" + T._API, "POST", body, timeout), persist)
        record["suite_id"] = sid
        persist()
        ok, _, detail = T.validate_auth_suite(sid, NAME, None, timeout, plan_id=T.AUTH_PLAN,
                                             parent_suite_id=parent, suite_type="staticTestSuite")
        require(ok, "Acknowledged Monthly leaf failed readback: " + detail)
        cases = {}
        for row in frozen["rows"]:
            body = case_payload(row, frozen)
            cid = S._create(ledger, "case:" + row["key"], body, lambda body=body: P._ado_rest_send(
                f"{T.ORG}/{T.PROJECT}/_apis/wit/workitems/$Test%20Case?api-version=7.1", "POST", body, timeout), persist)
            _validate_case(cid, body, timeout)
            require(cid not in cases.values(), "Monthly create returned shared scenario case ID")
            cases[row["key"]] = cid
        pairs = {(cid, cfg) for cid in cases.values() for cfg in configs}
        body = S._membership_body(pairs)
        saved = ledger.get("membership")
        if saved:
            require(saved.get("sha256") == S.digest(body) and saved.get("status") in ("applying", "applied"),
                    "Monthly membership source changed")
        else:
            require(not S._suite_points(T.AUTH_PLAN, sid, timeout), "Monthly new leaf has foreign points")
            ledger["membership"] = {"status": "applying", "sha256": S.digest(body)}
            persist()
            ok, _, detail = P._ado_rest_send(
                _base(record) + f"/Suites/{sid}/TestCase?api-version=7.1-preview.3", "POST", body, timeout)
            require(ok, "Uncertain Monthly membership write; never replay: " + detail)
        points = S._suite_points(T.AUTH_PLAN, sid, timeout)
        require(S._point_pairs(points) == pairs, "Monthly membership readback incomplete; never replay")
        require(len(points) == len(pairs), "Duplicate Monthly points")
        record["bindings"] = sorted([
            {"key": next(k for k, cid in cases.items() if cid == int(p["testCase"]["id"])),
             "point_id": int(p["id"]), "case_id": int(p["testCase"]["id"]),
             "config_id": int(p["configuration"]["id"])} for p in points],
            key=lambda b: (b["key"], b["config_id"]))
        ledger["membership"]["status"] = "applied"
        record["status"] = "ready"
        persist()
        validate_live(record, timeout)
        return True, record, ""
    except (ValueError, KeyError, TypeError) as exc:
        return False, record, str(exc)


def current(rc, record):
    checked(record)
    projection = source(rc, record["source"]["release"], tracked={r["key"] for r in record["source"]["rows"]})
    for field in ("release", "rc", "apk_build_id", "test_build_id"):
        require(projection[field] == record["source"][field], "Monthly current RC/APK/test-build drift")
    validate_source(projection, initial=False)
    return projection


def fill(record, projection, previous, checkpoint, timeout=120):
    """Publish source-backed points while retaining human resolution and acknowledged history."""
    summary = {"target": deepcopy(record), "source": deepcopy(projection),
               "applied_points": deepcopy((previous or {}).get("applied_points", [])),
               "preserved_points": deepcopy((previous or {}).get("preserved_points", [])),
               "history": deepcopy((previous or {}).get("history", [])), "status": "incomplete"}
    if (previous or {}).get("uncertain_points"):
        summary["uncertain_points"] = deepcopy(previous["uncertain_points"])
    try:
        checked(record)
        validate_source(projection, initial=False)
        require({r["key"] for r in projection["rows"]} == {r["key"] for r in record["source"]["rows"]},
                "Monthly fill source membership changed")
        require(all(projection[k] == record["source"][k] for k in
                    ("release", "rc", "apk_build_id", "test_build_id")), "Monthly fill source identity drift")
        points = validate_live(record, timeout)
        prior = previous or {}
        require(not prior or prior.get("target") == record, "Monthly prior receipt target changed")
        for field in ("applied_points", "preserved_points", "uncertain_points", "history"):
            require(isinstance(prior.get(field, []), list)
                    and all(isinstance(p, dict) for p in prior.get(field, [])),
                    "Malformed Monthly prior receipt/history")
        old = {p["point_id"]: p for p in prior.get("applied_points", [])}
        uncertain = {p["point_id"]: p for p in prior.get("uncertain_points", [])}
        manual = {p["point_id"]: p for p in prior.get("preserved_points", [])}
        for field in ("applied_points", "preserved_points", "uncertain_points"):
            seen = set()
            for p in prior.get(field, []):
                b = next((b for b in record["bindings"] if b["point_id"] == p.get("point_id")), None)
                require(b and p["point_id"] not in seen
                        and all(p.get(k) == b[k] for k in b)
                        and p.get("suite_id") == record["suite_id"], "Monthly prior point receipt changed")
                seen.add(p["point_id"])
        verdicts = {r["key"]: r["verdict"] for r in projection["rows"]}
        def remember(receipt, origin):
            if not any(all(h.get(k) == v for k, v in receipt.items())
                       and (h.get("receipt") == origin or origin == "prior_receipt")
                       for h in summary["history"]):
                summary["history"].append({**deepcopy(receipt), "receipt": origin,
                                           "source_sha256": S.digest(projection)})
        for point in points:
            pid = int(point["id"])
            b = next(b for b in record["bindings"] if b["point_id"] == pid)
            row = {**b, "suite_id": record["suite_id"], "outcome": verdicts[b["key"]]}
            live = point.get("outcome")
            require(isinstance(live, str) and live.casefold() in (
                "passed", "failed", "notapplicable", "blocked", "unspecified", "notrun", "active"),
                "Unreadable Monthly point result")
            normalized = live.casefold()
            receipt = old.get(pid) or uncertain.get(pid)
            pending_unchanged = (pid in uncertain
                                 and normalized == uncertain[pid].get("before_outcome", "").casefold())
            for field in ("applied_points", "preserved_points", "uncertain_points"):
                summary[field] = [p for p in summary.get(field, []) if p["point_id"] != pid]
            if not summary["uncertain_points"]:
                summary.pop("uncertain_points")
            if pid in manual or receipt and normalized != receipt["outcome"].casefold() and not pending_unchanged or (
                    not receipt and normalized not in ("unspecified", "notrun", "active")):
                summary["preserved_points"].append({**b, "suite_id": record["suite_id"], "outcome": live})
                if receipt:
                    remember(receipt, "prior_intent" if pid in uncertain else "prior_receipt")
            elif receipt and receipt["outcome"] == row["outcome"] and normalized == row["outcome"].casefold():
                summary["applied_points"].append(row)
                remember(row, "uncertain_readback" if pid in uncertain else "prior_receipt")
            else:
                if receipt:
                    remember(receipt, "prior_intent" if pid in uncertain else "prior_receipt")
                summary.setdefault("uncertain_points", []).append({**row, "before_outcome": live})
                checkpoint(summary)
                ok, detail = T._set_points_outcome(T.AUTH_PLAN, record["suite_id"], [pid], row["outcome"],
                                                  timeout, org=T.ORG, project=T.PROJECT, expected_points=points)
                require(ok, "Monthly point fill uncertain: " + detail)
                remember(row, "provider_ack")
                checkpoint(summary)
                observed = S._suite_points(record["plan_id"], record["suite_id"], timeout)
                require({(int(p["id"]), int(p["testCase"]["id"]), int(p["configuration"]["id"]))
                         for p in observed} == {(b["point_id"], b["case_id"], b["config_id"])
                                                for b in record["bindings"]},
                        "Monthly outcome readback identity changed")
                actual = next(p for p in observed if int(p["id"]) == pid)
                require(isinstance(actual.get("outcome"), str)
                        and actual["outcome"].casefold() == row["outcome"].casefold(),
                        "Monthly acknowledged outcome readback incomplete")
                summary["applied_points"].append(row)
                summary["uncertain_points"] = [p for p in summary["uncertain_points"] if p["point_id"] != pid]
                if not summary["uncertain_points"]:
                    summary.pop("uncertain_points")
            checkpoint(summary)
        summary["status"] = "complete"
        validate_fill(summary, record, projection)
        checkpoint(summary)
        return True, summary, ""
    except (ValueError, KeyError, TypeError) as exc:
        return False, summary, str(exc)


def validate_fill(summary, record, projection):
    checked(record)
    validate_source(projection, initial=False)
    require(summary.get("status") == "complete" and summary.get("target") == record
            and summary.get("source") == projection and not summary.get("uncertain_points"),
            "Monthly fill missing/partial/stale")
    expected = {(b["key"], b["point_id"], b["case_id"], b["config_id"]) for b in record["bindings"]}
    observed = set()
    verdicts = {r["key"]: r["verdict"] for r in projection["rows"]}
    for field in ("applied_points", "preserved_points"):
        require(isinstance(summary.get(field), list), "Monthly fill point receipt missing")
        for p in summary[field]:
            identity = (p.get("key"), p.get("point_id"), p.get("case_id"), p.get("config_id"))
            require(identity in expected and identity not in observed and p.get("suite_id") == record["suite_id"],
                    "Monthly fill point identity changed")
            if field == "applied_points":
                require(p.get("outcome") == verdicts[p["key"]], "Monthly applied outcome lost source proof")
            observed.add(identity)
    require(expected == observed, "Monthly complete fill omitted points")
    require(isinstance(summary.get("history"), list), "Monthly acknowledged history missing")


def links(record):
    checked(record)
    return ([{"name": NAME, "url": T.plan_web_url(record["plan_id"], record["suite_id"])}]
            if record["suite_id"] else [])


def preview(rc, release, parent_name):
    from tools import auth_partition as A
    frozen = source(rc, release)
    validate_source(frozen)
    ok, projection, detail = P.project_auth_ui_results(rc)
    require(ok, detail)
    classification = A.classification(rc, projection)
    queries = A.queries(classification)
    return {"layout": LAYOUT, "scope": "fresh layout only; existing v1 parent cannot be amended by replay",
            "plan_id": T.AUTH_PLAN, "parent_suite_id": T.AUTH_ROOT_SUITE, "parent_name": parent_name,
            "source": frozen, "source_sha256": S.digest(frozen), "monthly_folder": NAME if frozen["rows"] else None,
            "new_cases": [case_payload(row, frozen) for row in frozen["rows"]],
            "suite_creates": [
                {"name": parent_name, "suiteType": "staticTestSuite",
                 "parentSuite": {"id": T.AUTH_ROOT_SUITE}, "inheritDefaultConfigurations": True},
                *[{"name": name, "suiteType": "dynamicTestSuite", "parentSuite": {"id": "<new-parent-ack>"},
                   "queryString": queries[key], "inheritDefaultConfigurations": True}
                  for key, name in (("manual", "Manual Tests"), ("automated", "Automated Tests"))],
                *([suite_payload("<new-parent-ack>")] if frozen["rows"] else [])],
            "classification": classification,
            "results": [], "assignments": [], "canonical_mapping_changes": [],
            "quality_gates": deepcopy(rc["auth"]["test"]["suites"])}
