"""Product-neutral validation of complete, attributed Test API evidence. No plan mapping."""
from __future__ import annotations

from collections import Counter

from .tests_results import (
    MRWP_COUNT_BASIS, TEST_CATEGORIES, _RESULT_OUTCOMES, _positive_test_id,
    _suite_base_name, reconcile_retries,
)
from tools.coordinates import coords


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def _counts_match(record, expected):
    return isinstance(record, dict) and all(
        type(record.get(k)) is int and record[k] == v for k, v in expected.items())


def validate_snapshot_tests(snapshot, *, allow_empty=False):
    """Validate attribution, coverage and recorded verdicts against exact-title retries."""
    require(isinstance(snapshot, dict), "missing snapshot")
    bid = _positive_test_id(snapshot.get("run_id"))
    require(bid and snapshot.get("complete") is True
             and type(snapshot.get("total")) is int and snapshot["total"] > 0
             and type(snapshot.get("ran")) is int and snapshot["ran"] == snapshot["total"]
             and not any(snapshot.get(k) for k in
                         ("never_ran", "error", "tests_error", "failed_suites_error")),
             "missing completed stage/test snapshot")
    summary = snapshot.get("tests")
    require(isinstance(summary, dict), "missing test summary")
    require(summary.get("count_basis") == MRWP_COUNT_BASIS,
             "stale/unreconciled count_basis")
    require(_positive_test_id(summary.get("build_id")) == bid,
             "summary build_id does not match snapshot run_id")
    runs, suites = summary.get("runs"), summary.get("suites")
    require(isinstance(runs, list) and isinstance(suites, list) and (suites or allow_empty),
             "missing runs/suites evidence")
    run_counts, run_names = {}, {}
    for run in runs:
        require(isinstance(run, dict), "invalid test run")
        rid = _positive_test_id(run.get("id"))
        require(rid and rid not in run_counts and isinstance(run.get("name"), str)
                 and run["name"].strip()
                 and type(run.get("result_entries")) is int and run["result_entries"] >= 0,
                 "invalid/duplicate test run id or result count")
        run_counts[rid] = run["result_entries"]
        run_names[rid] = _suite_base_name(run["name"])
    seen_suites, seen_runs, seen_results = set(), set(), set()
    categories = {cat: Counter(total=0, passed=0, failed=0, na=0) for cat in TEST_CATEGORIES}
    ui_tests = []
    for suite in suites:
        require(isinstance(suite, dict), "invalid suite")
        name, cat = suite.get("name"), suite.get("category")
        require(isinstance(name, str) and name.strip() and name not in seen_suites
                 and isinstance(cat, str) and cat in categories
                 and suite.get("count_basis") == MRWP_COUNT_BASIS,
                 "invalid/duplicate suite or count_basis")
        seen_suites.add(name)
        ids, tests = suite.get("run_ids"), suite.get("test_results")
        require(isinstance(ids, list) and ids and isinstance(tests, list),
                 f"{name}: missing run_ids/test_results")
        rids = [_positive_test_id(i) for i in ids]
        require(all(i in run_counts for i in rids) and len(set(rids)) == len(rids)
                 and not seen_runs.intersection(rids), f"{name}: invalid/duplicate run_ids")
        require(all(run_names[rid] == name for rid in rids), f"{name}: run/suite attribution mismatch")
        seen_runs.update(rids)
        titles, entries, counts = set(), Counter(), Counter(total=0, passed=0, failed=0, na=0)
        for test in tests:
            require(isinstance(test, dict), f"{name}: invalid test")
            title, verdict = test.get("title"), test.get("verdict")
            require(isinstance(title, str) and title.strip() and title not in titles,
                     f"{name}: missing/duplicate exact title")
            require(isinstance(verdict, str) and verdict in ("Passed", "Failed", "NotApplicable")
                     and type(test.get("recovered")) is bool,
                     f"{name}: invalid reconciled verdict/recovered flag")
            titles.add(title)
            attempts = test.get("attempts")
            require(isinstance(attempts, list) and attempts, f"{name}: missing attempts")
            outcomes = Counter()
            for attempt in attempts:
                require(isinstance(attempt, dict), f"{name}: invalid attempt")
                rid = _positive_test_id(attempt.get("run_id"))
                result_id = _positive_test_id(attempt.get("result_id"))
                outcome = attempt.get("outcome")
                require(rid in rids and result_id and (rid, result_id) not in seen_results,
                         f"{name}: invalid/duplicate attempt ids")
                require("outcome" in attempt and (outcome is None or isinstance(outcome, str))
                         and outcome in _RESULT_OUTCOMES, f"{name}: invalid attempt outcome")
                seen_results.add((rid, result_id))
                entries[rid] += 1
                outcomes["null" if outcome is None else outcome] += 1
            require(_counts_match(test.get("outcome_counts"), outcomes)
                     and set(test["outcome_counts"]) == set(outcomes),
                     f"{name}: incomplete outcome_counts")
            verified = reconcile_retries([
                {"testCaseTitle": title, "outcome": a["outcome"]} for a in attempts])
            require(verdict == ("Passed" if verified["passed"] else
                                "Failed" if verified["failed"] else "NotApplicable")
                     and test["recovered"] == bool(verified["recovered"]),
                     f"{name}: verdict does not match exact-title retry evidence")
            counts["na" if verdict == "NotApplicable" else verdict.lower()] += 1
            counts["total"] += verdict != "NotApplicable"
            if cat == "ui":
                ui_tests.append((name, test))
        require(all(entries[rid] == run_counts[rid] for rid in rids)
                 and _counts_match(suite, {**counts, "result_entries": sum(entries.values())}),
                 f"{name}: incomplete/inconsistent suite counts")
        require(suite.get("tests") == sorted(
            test["title"] for test in tests if test["verdict"] == "Failed"),
            f"{name}: failure titles do not match reconciled tests")
        categories[cat].update(counts)
    require(seen_runs == set(run_counts), "incomplete suite/run coverage")
    recorded_categories = summary.get("categories")
    require(isinstance(recorded_categories, dict) and all(
        _counts_match(recorded_categories.get(cat), counts) for cat, counts in categories.items()),
        "category counts do not match reconciled tests")
    totals = {k: sum(c[k] for c in categories.values()) for k in ("total", "passed", "failed", "na")}
    require(_counts_match(summary, {**totals, "result_entries": sum(run_counts.values())}),
             "summary counts do not match reconciled tests")
    failures = sorted((suite for suite in suites if suite["failed"]),
                      key=lambda suite: (-suite["failed"], suite["name"]))
    require(summary.get("failed_suites") == failures and snapshot.get("failed_suites") == failures,
             "failure details do not match reconciled summary")
    require(ui_tests or allow_empty, "missing UI test evidence")
    return bid, sorted(ui_tests, key=lambda pair: (pair[0], pair[1]["title"]))


def result_links(attempts, project):
    org = coords.org_url("one") if project == "auth" else coords.org_url("engineering")
    proj = coords.project("one") if project == "auth" else coords.project("engineering")
    return [{"run_id": a["run_id"], "result_id": a["result_id"],
             "url": f"{org}/{proj}/_TestManagement/Runs?runId={a['run_id']}"
                    f"&resultId={a['result_id']}&_a=resultSummary"}
            for a in sorted(attempts, key=lambda a: (int(a["run_id"]), int(a["result_id"])))]
