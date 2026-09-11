"""Explicit offline fill writers and checkpoint recording for result-owner tests."""
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import patch

from tools import testplans as T, distribution as D
from tools.ui_mapping import config_for


def checkpoint_memory(state):
    snapshots = []
    state._checkpoint = lambda: snapshots.append(deepcopy(asdict(state)))
    return snapshots


def broker_fill(plan, verdicts, timeout=120, *, suite_id=901):
    points = [{"point_id": i, "case_id": cid, "config_id": config_for(*pair), "outcome": outcome}
              for i, (cid, pair, outcome) in enumerate(
                  ((cid, pair, outcome) for cid, variants in sorted(verdicts.items())
                   for pair, outcome in sorted(variants.items())), 1)]
    return True, {"target": {"plan_id": int(plan), "suite_id": int(suite_id)},
                  "applied_points": points, "points_total": len(points),
                  "set_passed": sum(p["outcome"] == "Passed" for p in points),
                  "set_failed": sum(p["outcome"] == "Failed" for p in points),
                  "set_not_applicable": sum(p["outcome"] == "NotApplicable" for p in points),
                  "cases_touched": len(verdicts)}, ""


def auth_fill(plan, suite, outcomes, timeout=120):
    points = [{"point_id": i, "case_id": cid, "outcome": outcome}
              for i, (cid, outcome) in enumerate(sorted(outcomes.items()), 1)]
    return True, {"target": {"plan_id": int(plan), "suite_id": int(suite)}, "applied_points": points,
                  "points_total": len(points), "set_passed": sum(v == "Passed" for v in outcomes.values()),
                  "set_failed": sum(v == "Failed" for v in outcomes.values()),
                  "failed_case_ids": sorted(cid for cid, v in outcomes.items() if v == "Failed")}, ""


def publish(state):
    from steps.bug_bash import ui_test_status as U
    checkpoint_memory(state)
    with patch.object(T, "fill_ui_automation_results", broker_fill), patch.object(
            T, "fill_auth_ui_results", auth_fill), patch.object(D, "set_assigned_to", return_value=(True, "")):
        outcome = U.build(state)
    assert outcome.kind == "done", outcome
