"""Offline snapshots built through the production reconciliation, not hand-written verdicts."""
from unittest.mock import patch

from tools import pipelines as P


PROD = "PROD MSAL - RC Broker (API 32)"
RC = "RC MSAL - PROD Broker (API 32)"


def snapshot(build_id, suites=None):
    suites = suites if suites is not None else {PROD: [("test_100_Default", "Passed")]}
    runs, results = [], {}
    for rid, (name, rows) in enumerate(suites.items(), 1):
        runs.append({"id": rid, "name": name, "totalTests": len(rows)})
        results[rid] = [{"id": i, "testCaseTitle": title, "outcome": outcome}
                        for i, (title, outcome) in enumerate(rows, 1)]
    with patch.object(P, "_test_runs", return_value=(True, runs, "")), patch.object(
            P, "_run_results", side_effect=lambda org, project, rid, *a, **k:
            (True, results[rid], "")):
        ok, tests, detail = P.get_test_summary("ORG", "PROJECT", build_id)
    assert ok, detail
    return {"run_id": str(build_id), "complete": True, "ran": 1, "total": 1,
            "tests": tests, "failed_suites": tests["failed_suites"]}


def current_rc(ecs=None, local=None, rc=2):
    from tests._auth_evidence import auth_snapshot
    return {"rc": rc, "ecs": snapshot(201, ecs), "local": snapshot(202, local),
            "auth": auth_snapshot(rc=rc)}
