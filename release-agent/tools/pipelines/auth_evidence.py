"""Authenticator evidence acquisition and source-only current-RC validation.

Gate counts remain ADO execution aggregates. Target mapping belongs to Phase 3.
"""
from collections import Counter
from tools import pipelines as P
from .tests_results import _positive_test_id
from .test_evidence import require as _require, validate_snapshot_tests, result_links

AUTH_EVIDENCE_VERSION = 1
MONTHLY_REPORT_ONLY = "Firebase Test Lab - Monthly UI Tests"
AUTH_MAPPING_NOTE = (
    "Monthly UI Tests intentionally has no test-plan case map (report-only). "
    "Do not create cases or block for that absence. All failures require release-owner "
    "investigation. Auth gate denominators are source executions (passed + failed), "
    "not distinct tests or case points; Monthly never enters the Broker pass rate."
)


def _rates(runs):
    """Retain the aggregate policy: latest run per exact gated suite name."""
    out = {}
    for name in P.AUTH_UI_SUITES:
        selected = [r for r in runs if r["name"] == name]
        if not selected:
            out[name] = dict(present=False, passed=0, failed=0, total=0, pct=None)
            continue
        r = max(selected, key=lambda r: r["id"])
        passed, total = r["passedTests"], r["totalTests"]
        failed = total - passed - r["notApplicableTests"]
        denom = passed + failed
        out[name] = dict(present=True, passed=passed, failed=failed, total=total,
                         pct=round(passed * 100 / denom, 1) if denom else None)
    return out


def collect_auth_ui_evidence(test_build_id, apk_build_id, rc, timeout=120):
    """Single complete capture: linked APK, paged runs/results, exact source identities."""
    bid, apk, rc = map(_positive_test_id, (test_build_id, apk_build_id, rc))
    if not all((bid, apk, rc)):
        return False, None, "Authenticator: invalid test/APK build or current RC"
    if _positive_test_id(P._auth_test_source_build_id(bid, timeout)) != apk:
        return False, None, "Authenticator: UI-test build does not prove the current APK source"
    ok, listed, detail = P._test_runs(P.AUTH_ORG, P.AUTH_PROJECT, bid, timeout)
    if not ok:
        return False, None, detail
    if not isinstance(listed, list) or any(
            not isinstance(row, dict) or not _positive_test_id(row.get("id"))
            or not isinstance(row.get("name"), str) or not row["name"].strip() for row in listed):
        return False, None, "Authenticator: malformed run list"
    runs = []
    for row in sorted(listed, key=lambda r: int(r["id"])):
        rid = int(row["id"])
        ok, run, detail = P._ado_rest_get(
            f"{P.AUTH_ORG}/{P.AUTH_PROJECT}/_apis/test/runs/{rid}?api-version=7.1", timeout)
        if not ok:
            return False, None, detail
        if (not isinstance(run, dict) or _positive_test_id(run.get("id")) != rid
                or run.get("name") != row.get("name")
                or not isinstance(run.get("build"), dict)
                or _positive_test_id(run["build"].get("id")) != bid
                or run.get("state") != "Completed" or run.get("incompleteTests") != 0):
            return False, None, f"Authenticator test run {rid}: incomplete/mismatched build attribution"
        runs.append({"id": rid, "name": run["name"], "build_id": bid,
                     **{k: run.get(k) for k in ("totalTests", "passedTests", "notApplicableTests")}})
    ok, summary, detail = P.summarize_test_runs(P.AUTH_ORG, P.AUTH_PROJECT, bid, runs, timeout)
    if not ok:
        return False, None, detail
    evidence = {"version": AUTH_EVIDENCE_VERSION, "rc": rc, "build_id": bid,
                "apk_build_id": apk, "runs": runs, "summary": summary}
    try:
        _inspect(evidence, rc, apk, bid)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return False, None, str(exc)
    return True, {"evidence": evidence, "suites": _rates(runs)}, ""


def _inspect(evidence, rc, apk, bid):
    _require(isinstance(evidence, dict) and evidence.get("version") == AUTH_EVIDENCE_VERSION,
             "missing/stale detailed Authenticator evidence")
    for key, expected in (("rc", rc), ("apk_build_id", apk), ("build_id", bid)):
        _require(_positive_test_id(expected) and
                 _positive_test_id(evidence.get(key)) == _positive_test_id(expected),
                 f"Authenticator evidence {key} does not match current RC/APK/test build")
    summary = evidence.get("summary")
    _require(isinstance(summary, dict), "missing Authenticator summary")
    _, tests = validate_snapshot_tests(
        {"run_id": bid, "complete": True, "total": 1, "ran": 1, "tests": summary,
         "failed_suites": summary.get("failed_suites")}, allow_empty=True)
    runs = evidence.get("runs")
    _require(isinstance(runs, list), "missing Authenticator aggregate run evidence")
    seen, raw_counts = set(), {}
    for run in runs:
        _require(isinstance(run, dict), "invalid Authenticator run")
        rid = _positive_test_id(run.get("id"))
        _require(rid and rid not in seen and isinstance(run.get("name"), str)
                 and run["name"].strip() and _positive_test_id(run.get("build_id")) == int(bid),
                 "invalid/duplicate Authenticator run attribution")
        seen.add(rid)
        _require(all(type(run.get(k)) is int and run[k] >= 0
                     for k in ("totalTests", "passedTests", "notApplicableTests"))
                 and run["passedTests"] + run["notApplicableTests"] <= run["totalTests"],
                 f"invalid aggregate counts for Authenticator run {rid}")
        raw_counts[rid] = Counter()
    _require(seen == {r["id"] for r in summary["runs"]}, "incomplete Authenticator run coverage")
    for run in runs:
        recorded = next(r for r in summary["runs"] if r["id"] == run["id"])
        _require(recorded["name"] == run["name"]
                 and recorded["result_entries"] == run["totalTests"],
                 "Authenticator aggregate/detail attribution mismatch")
    sources = []
    for suite, test in tests:
        ids = test.get("case_ids")
        _require(isinstance(ids, list) and len(ids) <= 1 and
                 all(_positive_test_id(cid) == cid and type(cid) is int for cid in ids),
                 f"{suite}: missing/ambiguous test-case identity; refresh evidence")
        for attempt in test["attempts"]:
            raw_counts[int(attempt["run_id"])][attempt["outcome"]] += 1
        cid = ids[0] if ids else None
        source = {"suite": suite, "title": test["title"], "case_id": cid,
                  "verdict": test["verdict"],
                  "links": result_links(test["attempts"], "auth")}
        sources.append(source)
    for run in runs:
        counts = raw_counts[run["id"]]
        _require(sum(counts.values()) == run["totalTests"]
                 and counts["Passed"] == run["passedTests"]
                 and counts["NotApplicable"] == run["notApplicableTests"],
                 f"Authenticator run {run['id']}: detailed results disagree with gate aggregates")
    return {"sources": sources, "failures": [s for s in sources if s["verdict"] == "Failed"],
            "provenance": {"rc": int(rc), "apk_build_id": int(apk), "build_id": int(bid),
                           "source_executions": summary["result_entries"],
                           "distinct_tests": len(tests)}}


def inspect_auth_ui_evidence(rc):
    """Validate source identity/coverage and expose source facts, never case-point outcomes."""
    try:
        _require(isinstance(rc, dict), "current RC missing")
        auth = rc.get("auth") or {}
        build, test = auth.get("build") or {}, auth.get("test") or {}
        _require(build.get("complete") is True and test.get("complete") is True
                 and build.get("result") in ("succeeded", "partiallySucceeded")
                 and _positive_test_id(build.get("rc")) == _positive_test_id(rc.get("rc")),
                 "Authenticator completed current-RC build/test unavailable")
        evidence = test.get("evidence")
        facts = _inspect(evidence, rc.get("rc"), build.get("run_id"), test.get("run_id"))
        _require(test.get("suites") == _rates(evidence["runs"]),
                 "Authenticator captured gate rates disagree with source evidence")
        return True, facts, ""
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return False, None, f"{exc}; refresh Phase-2 Authenticator verification"


__all__ = ["AUTH_MAPPING_NOTE", "MONTHLY_REPORT_ONLY", "collect_auth_ui_evidence",
           "inspect_auth_ui_evidence"]
