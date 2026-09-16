"""Test-run classification, retry reconciliation, summaries, UI verdicts, failed tests."""
from __future__ import annotations

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
MRWP_COUNT_BASIS = "distinct_tests_pass_any"
_RESULT_OUTCOMES = _NA_OUTCOMES | {
    "Passed", "Failed", "Error", "Timeout", "Aborted", "Blocked", "NotImpacted",
    "Paused", "InProgress", "Unspecified",
}


def reconcile_retries(results, *, include_tests=False):
    """Collapse ADO's per-attempt test results into ONE verdict per test (by title).

    Callers supply ONE normalized suite within ONE build/provider/RC. Order does not
    matter: Failed, Failed, Passed and Passed, Failed both yield one successful test.
    Exact titles (including parameterizations) are the identity, not case IDs. Rules:
      * PASSED    — at least one attempt Passed.
      * RECOVERED — Passed AND Failed on different attempts (a flaky pass — surfaced as a
                    warning, but counted as passed).
      * FAILED    — has a real (non-NA) attempt and NEVER passed.
    Not-executed / not-applicable attempts are ignored. Counts are DISTINCT tests. Returns
      {passed, failed, recovered:[titles], total, na}."""
    import collections
    by = collections.defaultdict(list)
    for r in results or []:
        title = r.get("testCaseTitle") or r.get("automatedTestName") or ""
        if not title.strip():
            continue
        by[title].append(r)
    passed = failed = na = 0
    recovered = []
    tests = []
    for title, attempts in sorted(by.items()):
        counts = collections.Counter(r.get("outcome") for r in attempts)
        outs = set(counts)
        eff = {o for o in outs if o not in _NA_OUTCOMES}
        if not eff:
            na += 1
            verdict = "NotApplicable"
        elif "Passed" in eff:
            passed += 1
            verdict = "Passed"
            if "Failed" in eff:
                recovered.append(title)
        else:
            failed += 1
            verdict = "Failed"
        if include_tests:
            ordered_attempts = sorted(attempts, key=lambda r: (
                r.get("run_id") or 0, r.get("id") or 0, r.get("outcome") or ""))
            tests.append({"title": title, "verdict": verdict,
                          "recovered": verdict == "Passed" and "Failed" in outs,
                          "outcome_counts": {"null" if k is None else k: v
                                             for k, v in sorted(counts.items(), key=lambda kv:
                                                                "null" if kv[0] is None else kv[0])},
                          "attempts": [{"run_id": r.get("run_id"), "result_id": r.get("id"),
                                        "outcome": r.get("outcome"),
                                        **{k: r[k] for k in ("errorMessage", "stackTrace",
                                                            "automatedTestName", "automatedTestStorage")
                                           if isinstance(r.get(k), str)}}
                                       for r in ordered_attempts]})
            tests[-1]["case_ids"] = sorted({_ui_case_id_from_result(r) for r in attempts
                                           if _ui_case_id_from_result(r)})
    summary = {"passed": passed, "failed": failed, "recovered": recovered,
               "total": passed + failed, "na": na}
    if include_tests:
        summary["test_results"] = tests
        summary["tests"] = [t["title"] for t in tests if t["verdict"] == "Failed"]
    return summary


def _positive_test_id(value):
    """Normalize Test API run/result IDs before attribution and duplicate detection."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    if not text.isascii() or not text.isdigit():
        return None
    number = int(text)
    return number if number > 0 else None


def _test_pages(url, timeout, page=1000, cap=None):
    """Read a Test API $top/$skip collection; never report a capped/partial read as complete."""
    out, seen = [], set()
    previous = None
    while True:
        top = page if cap is None else min(page, cap - len(out))
        if top <= 0:
            return (False, None, f"Test API result limit {cap} reached; evidence incomplete")
        ok, data, detail = _pp._ado_rest_get(f"{url}&$top={top}&$skip={len(out)}", timeout)
        if not ok:
            return (False, None, detail)
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            return (False, None, "Invalid Test API collection; evidence incomplete")
        batch = data["value"]
        if batch and batch == previous:
            return (False, None, "Repeated Test API page; evidence incomplete")
        previous = batch
        for item in batch:
            if not isinstance(item, dict):
                return (False, None, "Invalid Test API row; evidence incomplete")
            ident = _positive_test_id(item.get("id"))
            if ident is None:
                return (False, None, "Invalid/missing Test API id; evidence incomplete")
            if ident in seen:
                return (False, None, "Repeated Test API page/id; evidence incomplete")
            seen.add(ident)
            out.append({**item, "id": ident})
        if len(batch) < top:
            return (True, out, "")


def _test_runs(org, project, build_id, timeout=90):
    return _test_pages(
        f"{org.rstrip('/')}/{project}/_apis/test/runs"
        f"?buildUri=vstfs:///Build/Build/{build_id}&api-version=7.1", timeout)


def _run_results(org, project, run_id, timeout=90, page=1000, cap=10000, outcomes=None):
    """Paged results. cap=None requests complete evidence without a size limit."""
    url = (f"{org.rstrip('/')}/{project}/_apis/test/Runs/{run_id}/results"
           f"?api-version=7.1")
    if outcomes:
        url += f"&outcomes={outcomes}"
    return _test_pages(url, timeout, page=page, cap=cap)


def get_test_summary(org, project, build_id, timeout=60):
    """One complete MRWP evidence read, with pass-any counts and full suite/audit details.

    Every run is read, including successful runs: a pass there may recover a failure in
    another run of the same suite. No ADO aggregate fallback. NA-only tests are excluded
    from the denominator. Calls are scoped to one build (never mix providers or RCs).
    """
    ok, runs, detail = _pp._test_runs(org, project, build_id, timeout)
    if not ok:
        return (False, None, detail)
    return summarize_test_runs(org, project, build_id, runs, timeout)


def summarize_test_runs(org, project, build_id, runs, timeout=90):
    """Complete detailed read of an already acquired run list; shared by both providers."""
    groups, out_runs = {}, []
    for r in sorted(runs, key=lambda r: r["id"]):
        rid, name = r.get("id"), r.get("name")
        if rid is None or not isinstance(name, str) or not name.strip():
            return (False, None, "Test run id/name unavailable; evidence incomplete")
        ok2, results, d2 = _pp._run_results(org, project, rid, timeout, cap=None)
        if not ok2:
            return (False, None, f"Test run {rid}: {d2}")
        expected = r.get("totalTests")
        if type(expected) is not int or expected < 0 or len(results) != expected:
            return (False, None, f"Test run {rid}: expected {expected} result entries, "
                    f"received {len(results)}; refresh incomplete/changing evidence")
        for res in results:
            title = res.get("testCaseTitle") or res.get("automatedTestName")
            if not isinstance(title, str) or not title.strip():
                return (False, None, f"Test run {rid}: result title unavailable")
            outcome = res.get("outcome")
            if ("outcome" not in res or
                    (outcome is not None and not isinstance(outcome, str)) or
                    outcome not in _RESULT_OUTCOMES):
                return (False, None, f"Test run {rid}: invalid/missing result outcome")
        base = _pp._suite_base_name(name)
        group = groups.setdefault(base, {"run_ids": [], "results": []})
        group["run_ids"].append(rid)
        group["results"].extend({**res, "run_id": rid} for res in results)
        out_runs.append({"id": rid, "name": name, "result_entries": len(results)})
    suites = []
    cats = {c: {"total": 0, "passed": 0, "failed": 0, "na": 0, "recovered": []}
            for c in TEST_CATEGORIES}
    for name, group in sorted(groups.items()):
        rec = _pp.reconcile_retries(group["results"], include_tests=True)
        cat = _pp.classify_test_run(name)
        suite = {**rec, "name": name, "category": cat, "run_ids": group["run_ids"],
                 "count_basis": MRWP_COUNT_BASIS, "result_entries": len(group["results"])}
        suites.append(suite)
        for key in ("total", "passed", "failed", "na"):
            cats[cat][key] += suite[key]
        cats[cat]["recovered"].extend({"suite": name, "title": title} for title in rec["recovered"])
    totals = {key: sum(c[key] for c in cats.values()) for key in ("total", "passed", "failed", "na")}
    return (True, {**totals, "count_basis": MRWP_COUNT_BASIS, "build_id": build_id,
                   "result_entries": sum(s["result_entries"] for s in suites),
                   "runs": out_runs, "categories": cats, "suites": suites,
                   "failed_suites": sorted((s for s in suites if s["failed"]),
                                           key=lambda s: (-s["failed"], s["name"]))}, "")


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


def _suite_base_name(name):
    """The test API returns the same suite as several runs, each named
    '<suite> # <buildlabel>' — strip the ' # …' run suffix so same-suite runs merge."""
    return ((name or "").split(" # ")[0].strip()) or "(unnamed suite)"


def get_failed_tests(org, project, build_id, timeout=90):
    """Standalone failure query using the same complete pass-any evidence as the summary.
    Report collectors reuse summary['failed_suites'] instead of fetching a second time."""
    ok, summary, detail = _pp.get_test_summary(org, project, build_id, timeout)
    return (ok, summary["failed_suites"] if ok else None, detail)

__all__ = ['TEST_CATEGORIES', 'MRWP_COUNT_BASIS', '_CATEGORY_LABEL', '_NA_OUTCOMES', '_UI_API_RE', '_VERSION_KEYS', '_msal_variant', '_run_results', '_test_runs', '_suite_base_name', '_ui_case_id_from_result', 'classify_test_run', 'format_release_versions', 'format_versions', 'get_failed_tests', 'get_test_summary', 'summarize_test_runs', 'reconcile_retries']
