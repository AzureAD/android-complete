"""Test-run classification, retry reconciliation, summaries, UI verdicts, failed tests."""
from __future__ import annotations

import json as _json
import shutil
import subprocess

from tools.coordinates import coords
from tools import pipelines as _pp
import re as _re_mod


_UI_API_RE = _re_mod.compile(r"\(API\s*\d+\)", _re_mod.IGNORECASE)
TEST_CATEGORIES = ("unit", "instrumented", "ui")
_CATEGORY_LABEL = {"unit": "Unit", "instrumented": "Instrumented", "ui": "UI automation"}

_VERSION_KEYS = ("Common", "Msal", "Broker")


def format_versions(versions, fallback: str = "") -> str:
    """'Common X, Msal Y, Broker Z' from a {Common,Msal,Broker} dict — fixed order,
    blanks omitted. Returns `fallback` when nothing is set. One place so every report /
    status render formats RC versions identically."""
    v = versions or {}
    return ", ".join(f"{k} {v[k]}" for k in _VERSION_KEYS if v.get(k)) or fallback


def format_release_versions(versions, fallback: str = "") -> str:
    """Format the canonical state.versions (LOWERCASE keys) as 'Common X, Msal Y, Broker Z'
    (SDK-only — authenticator omitted). Mirror of format_versions for the source-of-truth store."""
    v = versions or {}
    return ", ".join(f"{lbl} {v[k]}"
                     for k, lbl in (("common", "Common"), ("msal", "Msal"), ("broker", "Broker"))
                     if v.get(k)) or fallback


def classify_test_run(name):
    """Bucket a test-run/suite name into one of THREE categories:
      * '*_UnitTests'          → unit
      * '*_InstrumentedTests'  → instrumented
      * everything else        → ui   (the device UI-automation suites, which carry an
                                       '(API NN)' tag, plus any other run such as
                                       'Lab Api Tests' — 'the rest are UI').
    """
    low = (name or "").lower()
    if "unittest" in low:
        return "unit"
    if "instrumentedtest" in low:
        return "instrumented"
    return "ui"


# Outcomes that are neither a pass nor a real failure (skipped / not run / inconclusive).
_NA_OUTCOMES = {"NotExecuted", "NotApplicable", "None", "Inconclusive", "Warning", None}


def reconcile_retries(results):
    """Collapse ADO's per-attempt test results into ONE verdict per test (by title).

    UNIT tests run under a RETRY rule: a flaky test can appear several times in the same
    run — e.g. Passed, Failed, Passed. ADO's run aggregate still counts that as a failure,
    but the test ultimately PASSED. This groups results by testCaseTitle and rules:
      * PASSED    — at least one attempt Passed.
      * RECOVERED — Passed AND Failed on different attempts (a flaky pass — surfaced as a
                    warning, but counted as passed).
      * FAILED    — has a real (non-NA) attempt and NEVER passed.
    Not-executed / not-applicable attempts are ignored. Counts are DISTINCT tests. Returns
      {passed, failed, recovered:[titles], total, na}."""
    import collections
    by = collections.defaultdict(set)
    for r in results or []:
        title = (r.get("testCaseTitle") or r.get("automatedTestName") or "").strip()
        if not title:
            continue
        by[title].add(r.get("outcome"))
    passed = failed = na = 0
    recovered = []
    for title, outs in by.items():
        eff = {o for o in outs if o not in _NA_OUTCOMES}
        if not eff:
            na += 1
            continue
        if "Passed" in eff:
            passed += 1
            if "Failed" in eff:
                recovered.append(title)
        else:
            failed += 1
    return {"passed": passed, "failed": failed, "recovered": sorted(recovered),
            "total": passed + failed, "na": na}


def _run_results(org, project, run_id, timeout=90, page=1000, cap=10000):
    """All test results for a run (paged). Returns (ok, [results], detail)."""
    base = org.rstrip("/")
    out, skip = [], 0
    while len(out) < cap:
        url = (f"{base}/{project}/_apis/test/Runs/{run_id}/results"
               f"?api-version=7.1&$top={page}&$skip={skip}")
        ok, data, detail = _pp._ado_rest_get(url, timeout)
        if not ok:
            return (False, out, detail)
        batch = (data or {}).get("value", []) or []
        out.extend(batch)
        if len(batch) < page:
            break
        skip += page
    return (True, out, "")


def get_test_summary(org, project, build_id, timeout=60):
    """Return (ok, summary, detail) for a build's Test-tab results. summary =
    {total, passed, failed, runs:[{name,total,passed,failed,category,recovered}],
     categories:{unit|instrumented|ui: {total,passed,failed,recovered:[titles]}}}
    aggregated across all test runs, classified into unit / instrumented / UI-automation.

    UNIT runs apply the retry rule (reconcile_retries): a run WITH failures is re-read at
    the per-result level so a flaky test that failed-then-passed counts as passed (and is
    reported as `recovered`). UI/instrumented runs use ADO's run aggregate unchanged.

    Uses the Test Runs REST API directly (az devops invoke mis-routes this one)."""
    base = org.rstrip("/")
    url = (f"{base}/{project}/_apis/test/runs"
           f"?buildUri=vstfs:///Build/Build/{build_id}&api-version=7.1")
    ok, data, detail = _pp._ado_rest_get(url, timeout)
    if not ok:
        return (False, None, detail)
    runs = (data or {}).get("value", []) or []
    out_runs, tot, passed = [], 0, 0
    cats = {c: {"total": 0, "passed": 0, "failed": 0, "recovered": []} for c in TEST_CATEGORIES}
    for r in runs:
        t = r.get("totalTests") or 0
        p = r.get("passedTests") or 0
        na = r.get("notApplicableTests") or 0
        f = max(t - p - na, 0)
        cat = _pp.classify_test_run(r.get("name"))
        recovered = []
        # UNIT retry rule: re-read a failing unit run per-result and reconcile flaky passes.
        if cat == "unit" and f > 0:
            ok2, results, _ = _pp._run_results(org, project, r.get("id"), timeout)
            if ok2 and results:
                rec = _pp.reconcile_retries(results)
                t, p, f, recovered = rec["total"], rec["passed"], rec["failed"], rec["recovered"]
        tot += t
        passed += p
        cats[cat]["total"] += t
        cats[cat]["passed"] += p
        cats[cat]["failed"] += f
        if recovered:
            cats[cat]["recovered"].extend(recovered)
        out_runs.append({"name": r.get("name"), "total": t, "passed": p,
                         "failed": f, "category": cat, "recovered": recovered})
    failed_total = sum(c["failed"] for c in cats.values())
    return (True, {"total": tot, "passed": passed, "failed": failed_total,
                   "runs": out_runs, "categories": cats}, "")


def _ui_case_id_from_result(res):
    """Extract the ADO test-CASE work-item id embedded in a UI-automation result's name.
    The automated tests are named `test_<caseId>_...` (with storage `...TestCase<caseId>`),
    e.g. 'test_3522687_WpjWithHardwareKeyByDefault' -> 3522687. Returns int or None."""
    for f in (res.get("automatedTestName"), res.get("testCaseTitle")):
        if f:
            m = _re_mod.search(r"test_(\d+)", f, _re_mod.I)
            if m:
                return int(m.group(1))
    st = res.get("automatedTestStorage")
    if st:
        m = _re_mod.search(r"TestCase(\d+)", st)
        if m:
            return int(m.group(1))
    return None


def _flight_provider(org, project, build_id, timeout=60):
    """The MRWP build's flight provider — 'ECS' or 'Local' — read from its templateParameters.
    This is what splits the ECS vs LocalFlight test configurations. Returns the string or None."""
    ok, b, _d = _pp._ado_rest_get(
        f"{org.rstrip('/')}/{project}/_apis/build/builds/{build_id}?api-version=7.1", timeout)
    if not ok:
        return None
    fp = ((b or {}).get("templateParameters") or {}).get("flightProvider")
    return fp or None


def _msal_variant(run_name):
    """The MSAL variant of a UI run — 'prod' (a 'PROD MSAL …' run) or 'rc' (an 'RC MSAL …' run),
    which selects the PROD-MSAL vs RC-MSAL test configuration. None when the run carries neither
    marker (e.g. 'Lab Api Tests') and so can't be placed on a specific config."""
    low = (run_name or "").lower()
    if "prod msal" in low:
        return "prod"
    if "rc msal" in low:
        return "rc"
    return None


def ui_automation_verdicts(org, project, build_ids, timeout=90):
    """Aggregate UI-automation outcomes across the given RC/flight builds, PER (test case,
    flight, MSAL-variant) — which maps 1:1 to the plan's four flight configurations.

    Each MRWP build is one flight (ECS or Local, from templateParameters.flightProvider), and its
    UI runs split into a PROD-MSAL and an RC-MSAL variant (from the run name). A test can run many
    times for a given (flight, variant) — across RC iterations and retries — with different
    outcomes. The rule per (case, flight, variant):
      * 'Passed'        — passed in AT LEAST ONE run;
      * 'Failed'        — has a real (non-NA) result but NEVER passed;
      * 'NotApplicable' — only skipped / not-executed results (no real run).
    Runs that can't be placed (no flight, or a run without a PROD/RC-MSAL marker) are ignored.
    The join from a result to a case is the id in the automated test name.

    Returns (ok, {case_id: {(flight, variant): 'Passed'|'Failed'|'NotApplicable'}}, detail),
    where flight is 'ECS'|'Local' and variant is 'prod'|'rc'."""
    import collections
    outs = collections.defaultdict(lambda: collections.defaultdict(set))
    base = org.rstrip("/")
    for bid in build_ids:
        flight = _pp._flight_provider(org, project, bid, timeout)
        if not flight:
            continue                       # unknown flight -> can't place its results
        url = (f"{base}/{project}/_apis/test/runs"
               f"?buildUri=vstfs:///Build/Build/{bid}&api-version=7.1")
        ok, data, detail = _pp._ado_rest_get(url, timeout)
        if not ok:
            return (False, None, detail)
        for r in (data or {}).get("value", []) or []:
            if _pp.classify_test_run(r.get("name")) != "ui":
                continue
            variant = _pp._msal_variant(r.get("name"))
            if not variant:
                continue                   # e.g. 'Lab Api Tests' -> no config to place on
            ok2, results, d2 = _pp._run_results(org, project, r.get("id"), timeout)
            if not ok2:
                return (False, None, d2)
            for res in results or []:
                cid = _pp._ui_case_id_from_result(res)
                if cid is not None:
                    outs[cid][(flight, variant)].add(res.get("outcome"))
    verdicts = {}
    for cid, fv in outs.items():
        verdicts[cid] = {}
        for key, o in fv.items():
            eff = {x for x in o if x not in _NA_OUTCOMES}
            verdicts[cid][key] = ("Passed" if "Passed" in eff
                                  else ("Failed" if eff else "NotApplicable"))
    return (True, verdicts, "")


def _suite_base_name(name):
    """The test API returns the same suite as several runs, each named
    '<suite> # <buildlabel>' — strip the ' # …' run suffix so same-suite runs merge."""
    return ((name or "").split(" # ")[0].strip()) or "(unnamed suite)"


def get_failed_tests(org, project, build_id, max_result_calls=20, per_suite_cap=40, timeout=90):
    """Return (ok, suites, detail) — the individual FAILING tests for a build, aggregated
    by suite name (the same suite appears as multiple runs; merged). suites is a list of
    {name, failed, total, category, tests:[titles], recovered:[titles]}, sorted by failure
    count desc. Test titles are fetched for the worst runs first, bounded by
    max_result_calls; per suite capped at per_suite_cap names.

    UNIT suites apply the retry rule: a failing unit run is re-read per-result and
    reconciled (reconcile_retries), so a flaky test that failed-then-passed is NOT listed
    as a failure — it's collected under `recovered` and a suite that fully recovers is
    dropped."""
    base = org.rstrip("/")
    url = (f"{base}/{project}/_apis/test/runs"
           f"?buildUri=vstfs:///Build/Build/{build_id}&api-version=7.1")
    ok, data, detail = _pp._ado_rest_get(url, timeout)
    if not ok:
        return (False, None, detail)

    def fcount(r):
        return max((r.get("totalTests") or 0) - (r.get("passedTests") or 0)
                   - (r.get("notApplicableTests") or 0), 0)

    failing = sorted((r for r in (data or {}).get("value", []) if fcount(r) > 0),
                     key=lambda r: -fcount(r))
    suites, calls = {}, 0
    for r in failing:
        name = _pp._suite_base_name(r.get("name"))
        cat = _pp.classify_test_run(name)
        s = suites.setdefault(name, {"name": name, "failed": 0, "total": 0,
                                     "category": cat, "tests": [], "recovered": []})
        # UNIT retry rule: reconcile per-result so flaky-recovered tests aren't failures.
        if cat == "unit":
            ok2, results, _ = _pp._run_results(org, project, r.get("id"), timeout)
            if ok2:
                rec = _pp.reconcile_retries(results)
                s["failed"] += rec["failed"]
                s["total"] += rec["total"]
                for t in rec["recovered"]:
                    if t not in s["recovered"]:
                        s["recovered"].append(t)
                # only the tests that truly failed (never passed) — reconcile again for names
                import collections
                by = collections.defaultdict(set)
                for res in results:
                    title = (res.get("testCaseTitle") or res.get("automatedTestName") or "").strip()
                    if title:
                        by[title].add(res.get("outcome"))
                for title, outs in by.items():
                    eff = {o for o in outs if o not in _NA_OUTCOMES}
                    if eff and "Passed" not in eff and title not in s["tests"] \
                            and len(s["tests"]) < per_suite_cap:
                        s["tests"].append(title)
            continue
        # NON-unit (UI / instrumented) — ADO aggregate + the failed titles (unchanged).
        s["failed"] += fcount(r)
        s["total"] += r.get("totalTests") or 0
        if calls < max_result_calls:
            calls += 1
            rurl = (f"{base}/{project}/_apis/test/Runs/{r.get('id')}/results"
                    f"?outcomes=Failed&$top=100&api-version=7.1")
            ok2, rdata, _ = _pp._ado_rest_get(rurl, timeout)
            if ok2:
                for res in (rdata or {}).get("value", []):
                    title = (res.get("testCaseTitle") or res.get("automatedTestName") or "").strip()
                    if title and title not in s["tests"] and len(s["tests"]) < per_suite_cap:
                        s["tests"].append(title)
    # Drop suites whose failures all recovered on retry (unit); keep real failures.
    real = [s for s in suites.values() if s["failed"] > 0]
    return (True, sorted(real, key=lambda x: -x["failed"]), "")

__all__ = ['TEST_CATEGORIES', '_CATEGORY_LABEL', '_NA_OUTCOMES', '_UI_API_RE', '_VERSION_KEYS', '_flight_provider', '_msal_variant', '_run_results', '_suite_base_name', '_ui_case_id_from_result', 'classify_test_run', 'format_release_versions', 'format_versions', 'get_failed_tests', 'get_test_summary', 'reconcile_retries', 'ui_automation_verdicts']
