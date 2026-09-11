"""Progress counts human work, not automation passes owned by the same person."""
import pytest

from tools import bugbash as B
from steps.bug_bash import bugbash_updates as U
from steps.lib import mockctx
from tests._harness import _bb_updates_state

PEDRO = "pedro@example.test"
OWNER = "owner@example.test"
EMPTY_BROKER = {"target": {"plan_id": 1, "suite_id": 4}, "failed_case_ids": [], "applied_points": []}


def point(cid, outcome="Unspecified"):
    return {"case_id": str(cid), "name": f"Case {cid}", "outcome": outcome}


def stub_points(monkeypatch, broker, auth, owners):
    requested = []
    monkeypatch.setattr(B, "_broker_points", lambda *a: (True, broker, ""))
    monkeypatch.setattr(B, "_points_of_suite", lambda *a: (True, auth, ""))
    def assigned(ids, *args):
        requested.extend(ids)
        return True, {cid: owners.get(cid) for cid in ids}, ""
    monkeypatch.setattr(B.D, "_cases_assignedto", assigned)
    return requested


def test_seven_manual_plus_eight_automation_passes_is_zero_of_seven(monkeypatch):
    broker = [point(i) for i in range(1, 8)]
    auth = [point(i, "Passed") for i in range(100, 108)]
    requested = stub_points(monkeypatch, broker, auth, {p["case_id"]: PEDRO for p in broker + auth})
    ok, progress, detail = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=range(100, 108), broker_ui_result=EMPTY_BROKER)
    assert ok and not detail
    pedro = progress["owners"][PEDRO]
    assert (pedro["done"], pedro["total"], pedro["remaining"]) == (0, 7, 7)
    assert set(requested) == {str(i) for i in range(1, 8)}
    assert progress["auth_excluded_automated"] == 8
    html, _ = B.render_update(progress, "October 2026", [], {
        PEDRO: {"id": "11111111-1111-1111-1111-111111111111", "name": "Pedro"}})
    assert "0/7 done" in html and "8/15" not in html


@pytest.mark.parametrize("manual_outcome,done", [("Passed", 1), ("NotApplicable", 1), ("Failed", 0)])
@pytest.mark.parametrize("auto_outcome", ["Passed", "Failed", "Unspecified"])
def test_classification_not_outcome_selects_manual_work(monkeypatch, manual_outcome, done, auto_outcome):
    auth = [point(1, manual_outcome), point(100, auto_outcome)]
    stub_points(monkeypatch, [], auth, {"1": PEDRO, "100": PEDRO})
    ok, progress, _ = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[100], broker_ui_result=EMPTY_BROKER)
    assert ok and progress["total"] == 1 and progress["done"] == done
    assert [t["id"] for t in progress["owners"][PEDRO]["tests"]] == ["1"]


def test_applied_failure_triage_remains_visible_and_prevents_early_completion(monkeypatch):
    stub_points(monkeypatch, [point(1, "Passed")], [point(100, "Passed"), point(200, "Failed")],
                {"1": PEDRO, "100": PEDRO, "200": OWNER})
    ok, progress, _ = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[100, 200], auto_failed_ids=[200], broker_ui_result=EMPTY_BROKER)
    assert ok and (progress["done"], progress["total"], progress["remaining"]) == (1, 2, 1)
    assert progress["auto_failed_remaining"] == 1 and not B.all_complete(progress)
    assert progress["owners"][OWNER]["tests"][0]["auto_failed"]
    assert progress["owners"][PEDRO]["total"] == 1


def test_resolved_triage_counts_as_done_and_automated_only_owner_disappears(monkeypatch):
    stub_points(monkeypatch, [], [point(100, "Passed"), point(200, "Passed")],
                {"100": PEDRO, "200": OWNER})
    ok, progress, _ = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[100, 200], auto_failed_ids=[200], broker_ui_result=EMPTY_BROKER)
    assert ok and progress["done"] == progress["total"] == 1 and B.all_complete(progress)
    assert PEDRO not in progress["owners"] and progress["auto_failed_remaining"] == 0


def test_auth_classification_does_not_remove_broker_manual_case_or_change_case_count(monkeypatch):
    broker = [point(100, "Passed"), point(100, "Unspecified")]
    stub_points(monkeypatch, broker, [point(100, "Passed")], {"100": PEDRO})
    ok, progress, _ = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[100], broker_ui_result=EMPTY_BROKER)
    assert ok and progress["total"] == 1 and progress["done"] == 0
    assert not progress["owners"][PEDRO]["tests"][0]["auto_failed"]


@pytest.mark.parametrize("automated,failed", [(None, []), ([], [200])])
def test_missing_or_inconsistent_classification_fails_before_reads(monkeypatch, automated, failed):
    monkeypatch.setattr(B, "_broker_points", lambda *a: pytest.fail("No read without valid classification"))
    ok, progress, detail = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=automated, auto_failed_ids=failed, broker_ui_result=EMPTY_BROKER)
    assert not ok and progress is None and detail


def test_no_human_work_after_filtering_does_not_start_a_zero_of_zero_poller(monkeypatch):
    stub_points(monkeypatch, [], [point(100, "Passed"), point(100, "Passed")], {"100": PEDRO})
    ok, progress, _ = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[100], broker_ui_result=EMPTY_BROKER)
    assert ok and progress["total"] == 0 and progress["auth_excluded_automated"] == 1
    assert B.all_complete(progress)
    assert not B.all_complete({"total": 0, "remaining": 0})  # Empty reads alone still prove nothing.
    assert not B.all_complete({"total": 0, "auth_excluded_automated": 1})
    with mockctx.active({"progress": progress}):
        outcome = U.build(_bb_updates_state())
    assert outcome.kind == "done" and "No manual or triage" in outcome.note
