"""Pure projection of recorded MRWP evidence onto Broker test-plan configurations.

Retry reconciliation belongs to get_test_summary, never to this projection. Several
distinct titles/API suites can map to one plan point: any Failed verdict wins there;
otherwise any Passed wins over NA, and NA-only maps to NotApplicable.
"""
from __future__ import annotations

from collections import Counter

from .tests_results import (
    MRWP_COUNT_BASIS, TEST_CATEGORIES, _RESULT_OUTCOMES, _positive_test_id,
    _msal_variant, _suite_base_name, _ui_case_id_from_result,
)


def _require(condition, detail):
    if not condition:
        raise ValueError(detail)


def _counts_match(record, expected):
    return isinstance(record, dict) and all(
        type(record.get(k)) is int and record[k] == v for k, v in expected.items())


def _snapshot_tests(snapshot):
    """Validate attribution/completeness and counts, without recomputing retry verdicts."""
    _require(isinstance(snapshot, dict), "missing snapshot")
    bid = _positive_test_id(snapshot.get("run_id"))
    _require(bid and snapshot.get("complete") is True
             and type(snapshot.get("total")) is int and snapshot["total"] > 0
             and type(snapshot.get("ran")) is int and snapshot["ran"] == snapshot["total"]
             and not any(snapshot.get(k) for k in
                         ("never_ran", "error", "tests_error", "failed_suites_error")),
             "missing completed stage/test snapshot")
    summary = snapshot.get("tests")
    _require(isinstance(summary, dict), "missing test summary")
    _require(summary.get("count_basis") == MRWP_COUNT_BASIS,
             "stale/unreconciled count_basis")
    _require(_positive_test_id(summary.get("build_id")) == bid,
             "summary build_id does not match MRWP run_id")
    runs, suites = summary.get("runs"), summary.get("suites")
    _require(isinstance(runs, list) and isinstance(suites, list) and suites,
             "missing runs/suites evidence")
    run_counts, run_names = {}, {}
    for run in runs:
        _require(isinstance(run, dict), "invalid test run")
        rid = _positive_test_id(run.get("id"))
        _require(rid and rid not in run_counts and isinstance(run.get("name"), str)
                 and run["name"].strip()
                 and type(run.get("result_entries")) is int and run["result_entries"] >= 0,
                 "invalid/duplicate test run id or result count")
        run_counts[rid] = run["result_entries"]
        run_names[rid] = _suite_base_name(run["name"])
    seen_suites, seen_runs, seen_results = set(), set(), set()
    categories = {cat: Counter(total=0, passed=0, failed=0, na=0) for cat in TEST_CATEGORIES}
    ui_tests = []
    for suite in suites:
        _require(isinstance(suite, dict), "invalid suite")
        name, cat = suite.get("name"), suite.get("category")
        _require(isinstance(name, str) and name.strip() and name not in seen_suites
                 and isinstance(cat, str) and cat in categories
                 and suite.get("count_basis") == MRWP_COUNT_BASIS,
                 "invalid/duplicate suite or count_basis")
        seen_suites.add(name)
        ids, tests = suite.get("run_ids"), suite.get("test_results")
        _require(isinstance(ids, list) and ids and isinstance(tests, list),
                 f"{name}: missing run_ids/test_results")
        rids = [_positive_test_id(i) for i in ids]
        _require(all(i in run_counts for i in rids) and len(set(rids)) == len(rids)
                 and not seen_runs.intersection(rids), f"{name}: invalid/duplicate run_ids")
        _require(all(run_names[rid] == name for rid in rids), f"{name}: run/suite attribution mismatch")
        seen_runs.update(rids)
        titles, entries, counts = set(), Counter(), Counter(total=0, passed=0, failed=0, na=0)
        for test in tests:
            _require(isinstance(test, dict), f"{name}: invalid test")
            title, verdict = test.get("title"), test.get("verdict")
            _require(isinstance(title, str) and title.strip() and title not in titles,
                     f"{name}: missing/duplicate exact title")
            _require(isinstance(verdict, str) and verdict in ("Passed", "Failed", "NotApplicable")
                     and type(test.get("recovered")) is bool,
                     f"{name}: invalid reconciled verdict/recovered flag")
            titles.add(title)
            attempts = test.get("attempts")
            _require(isinstance(attempts, list) and attempts, f"{name}: missing attempts")
            outcomes = Counter()
            for attempt in attempts:
                _require(isinstance(attempt, dict), f"{name}: invalid attempt")
                rid = _positive_test_id(attempt.get("run_id"))
                result_id = _positive_test_id(attempt.get("result_id"))
                outcome = attempt.get("outcome")
                _require(rid in rids and result_id and (rid, result_id) not in seen_results,
                         f"{name}: invalid/duplicate attempt ids")
                _require("outcome" in attempt and (outcome is None or isinstance(outcome, str))
                         and outcome in _RESULT_OUTCOMES, f"{name}: invalid attempt outcome")
                seen_results.add((rid, result_id))
                entries[rid] += 1
                outcomes["null" if outcome is None else outcome] += 1
            _require(_counts_match(test.get("outcome_counts"), outcomes)
                     and set(test["outcome_counts"]) == set(outcomes),
                     f"{name}: incomplete outcome_counts")
            counts["na" if verdict == "NotApplicable" else verdict.lower()] += 1
            counts["total"] += verdict != "NotApplicable"
            if cat == "ui":
                ui_tests.append((name, test))
        _require(all(entries[rid] == run_counts[rid] for rid in rids)
                 and _counts_match(suite, {**counts, "result_entries": sum(entries.values())}),
                 f"{name}: incomplete/inconsistent suite counts")
        _require(suite.get("tests") == sorted(
            test["title"] for test in tests if test["verdict"] == "Failed"),
            f"{name}: failure titles do not match reconciled tests")
        categories[cat].update(counts)
    _require(seen_runs == set(run_counts), "incomplete suite/run coverage")
    recorded_categories = summary.get("categories")
    _require(isinstance(recorded_categories, dict) and all(
        _counts_match(recorded_categories.get(cat), counts) for cat, counts in categories.items()),
        "category counts do not match reconciled tests")
    totals = {k: sum(c[k] for c in categories.values()) for k in ("total", "passed", "failed", "na")}
    _require(_counts_match(summary, {**totals, "result_entries": sum(run_counts.values())}),
             "summary counts do not match reconciled tests")
    failures = sorted((suite for suite in suites if suite["failed"]),
                      key=lambda suite: (-suite["failed"], suite["name"]))
    _require(summary.get("failed_suites") == failures and snapshot.get("failed_suites") == failures,
             "failure details do not match reconciled summary")
    _require(ui_tests, "missing UI test evidence")
    return bid, sorted(ui_tests, key=lambda pair: (pair[0], pair[1]["title"]))


def project_mrwp_ui_results(rc):
    """Return (ok, projection, detail) for ONE current RC, with no I/O.

    verdicts is the existing testplans case/(flight,variant) input; failures retains
    distinct source titles, including unmapped ones. Only compact JSON provenance is
    persisted by the caller; all attempt evidence remains in the Phase-2 snapshot.
    """
    verdicts, failures, providers = {}, [], []
    try:
        _require(isinstance(rc, dict) and _positive_test_id(rc.get("rc")),
                 "current RC not identified")
        for slot, flight in (("ecs", "ECS"), ("local", "Local")):
            try:
                bid, tests = _snapshot_tests(rc.get(slot))
            except ValueError as exc:
                raise ValueError(f"MRWP {flight}: {exc}") from exc
            skipped = []
            for suite, test in tests:
                cid = _ui_case_id_from_result({"testCaseTitle": test["title"]})
                cid = _positive_test_id(cid)
                variant = _msal_variant(suite)
                source = {"suite": suite, "title": test["title"], "case_id": cid}
                if test["verdict"] == "Failed":
                    failures.append({**source, "flight": flight, "build_id": bid})
                if not cid or not variant:
                    skipped.append({**source, "status": "skipped_mapping",
                                    "reason": "missing_case_id" if not cid else "unknown_suite_variant"})
                    continue
                fv = (flight, variant)
                previous = verdicts.setdefault(cid, {}).get(fv, "NotApplicable")
                # This is a projection of DISTINCT tests, not retry pass-any.
                represented = (previous, test["verdict"])
                verdicts[cid][fv] = ("Failed" if "Failed" in represented else
                                     "Passed" if "Passed" in represented else "NotApplicable")
            providers.append({"flight": flight, "build_id": bid, "ui_tests": len(tests),
                              "mapped_tests": len(tests) - len(skipped), "skipped_mapping": skipped})
        _require(providers[0]["build_id"] != providers[1]["build_id"],
                 "ECS and Local cannot reference the same MRWP build")
    except ValueError as exc:
        return False, None, f"{exc}; refresh Phase-2 MRWP verification before filling results"
    verdicts = {cid: dict(sorted(values.items())) for cid, values in sorted(verdicts.items())}
    return True, {"verdicts": verdicts, "failures": failures,
                  "provenance": {"rc": int(rc["rc"]), "count_basis": MRWP_COUNT_BASIS,
                                 "projection_rule": "distinct_tests_failed_wins",
                                 "providers": providers}}, ""


__all__ = ["project_mrwp_ui_results"]
