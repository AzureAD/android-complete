"""Both-plan failure triage counts cases, preserving the original failing configurations."""
from copy import deepcopy

import pytest

from tools import bugbash as B

OWNER = "owner@example.test"
OTHER = "other@example.test"


def row(pid, cid, config=292, outcome="Failed"):
    return {"point_id": str(pid), "case_id": str(cid), "config_id": str(config),
            "name": f"Case {cid}", "outcome": outcome}


def receipt(points):
    return {"target": {"plan_id": 1, "suite_id": 4},
            "failed_case_ids": sorted({int(p["case_id"]) for p in points if p["outcome"] == "Failed"}),
            "applied_points": [
                {"point_id": int(p["point_id"]), "case_id": int(p["case_id"]),
                 "config_id": int(p["config_id"]), "outcome": p["outcome"]} for p in points]}


def stub(monkeypatch, broker_ui, auth=(), manual=(), owners=None):
    reads = []
    def get(plan, suite, timeout):
        reads.append((plan, suite))
        return True, deepcopy(broker_ui if suite == 4 else list(auth)), ""
    monkeypatch.setattr(B, "_points_of_suite", get)
    monkeypatch.setattr(B, "_broker_points", lambda *a: (True, list(manual), ""))
    monkeypatch.setattr(B.D, "_cases_assignedto",
                        lambda ids, *a: (True, {cid: (owners or {}).get(cid, OWNER) for cid in ids}, ""))
    return reads


def gather(evidence, automated=(), failed=()):
    return B.gather_progress(1, "Manual", 2, 3, auth_automated_ids=automated,
                             auto_failed_ids=failed, broker_ui_result=evidence)


def test_owner_has_seventeen_cases_from_both_plans_not_ten(monkeypatch):
    failures = [row(i, 100 + i) for i in range(1, 8)]
    failures += [row(8, 101, 328), row(9, 102, 328)]
    auth = [row(i, i, outcome="Passed" if i <= 6 else "Blocked") for i in range(1, 11)]
    reads = stub(monkeypatch, failures + [row(10, 999, outcome="Passed")], auth)
    ok, progress, detail = gather(receipt(failures), range(1, 7), range(1, 7))
    assert ok and not detail and set(reads) == {(1, 4), (2, 3)}
    owner = progress["owners"][OWNER]
    assert (owner["done"], owner["total"], owner["remaining"]) == (6, 17, 11)
    assert progress["auto_failed_remaining"] == 7
    assert progress["auto_failed_remaining_by_product"] == {"Broker": 7, "Authenticator": 0}
    assert {t["id"] for t in owner["tests"]} == {str(i) for i in range(1, 11)} | {str(i) for i in range(101, 108)}
    content, mentions = B.render_update(progress, "October 2026", [], {
        OWNER: {"name": "Owner", "id": "11111111-1111-1111-1111-111111111111"}})
    assert "6/17 done" in content and "7 failed automated Broker case(s)" in content
    assert "(Automation triage)" in content and "[Broker]" in content and len(mentions) == 1
    assert "Case 999" not in content


@pytest.mark.parametrize("remaining_outcome,done", [("Failed", False), ("Passed", True),
                                                   ("NotApplicable", True), ("Unspecified", False)])
def test_each_originally_failing_configuration_must_resolve(monkeypatch, remaining_outcome, done):
    failures = [row(1, 100), row(2, 100, 328)]
    live = [row(1, 100, outcome="Passed"), row(2, 100, 328, remaining_outcome),
            row(3, 100, 344, "Unspecified")]  # Not executed by this fill: not triage.
    stub(monkeypatch, live)
    ok, progress, _ = gather(receipt(failures))
    assert ok and progress["total"] == 1
    assert progress["done"] == int(done) and B.all_complete(progress) == done


def test_broker_failure_uses_actual_assignee_not_assumed_owner(monkeypatch):
    points = [row(1, 100)]
    stub(monkeypatch, points, owners={"100": OTHER})
    ok, progress, _ = gather(receipt(points))
    assert ok and set(progress["owners"]) == {OTHER}


@pytest.mark.parametrize("damage", ["missing", "case", "config", "duplicate"])
def test_missing_or_changed_live_triage_points_block_instead_of_disappearing(monkeypatch, damage):
    evidence = receipt([row(1, 100)])
    live = {"missing": [], "case": [row(1, 200)], "config": [row(1, 100, 328)],
            "duplicate": [row(1, 100), row(1, 100)]}[damage]
    stub(monkeypatch, live)
    ok, progress, detail = gather(evidence)
    assert not ok and progress is None and "broker UI triage" in detail


@pytest.mark.parametrize("damage", ["missing", "wrong_plan", "missing_failure", "duplicate", "bad_config"])
def test_invalid_failure_evidence_cannot_turn_into_empty_triage(monkeypatch, damage):
    evidence = receipt([row(1, 100)])
    if damage == "missing":
        evidence = None
    elif damage == "wrong_plan":
        evidence["target"]["plan_id"] = 999
    elif damage == "missing_failure":
        evidence["applied_points"] = []
    elif damage == "duplicate":
        evidence["applied_points"] *= 2
    else:
        evidence["applied_points"][0]["config_id"] = None
    monkeypatch.setattr(B, "_points_of_suite", lambda *a: pytest.fail("No reads with invalid evidence"))
    assert not gather(evidence)[0]


def test_failure_read_error_blocks_instead_of_returning_partial_progress(monkeypatch):
    stub(monkeypatch, [])
    monkeypatch.setattr(B, "_points_of_suite", lambda *a: (False, None, "HTTP 403"))
    assert gather(receipt([row(1, 100)])) == (False, None, "broker UI triage: HTTP 403")


def test_empty_valid_broker_failure_set_does_not_read_the_ui_suite(monkeypatch):
    reads = stub(monkeypatch, [], manual=[row(1, 42, outcome="Passed")])
    ok, progress, _ = gather(receipt([]))
    assert ok and progress["done"] == progress["total"] == 1
    assert reads == [(2, 3)]


def test_same_case_in_manual_and_ui_is_not_counted_twice(monkeypatch):
    failed = [row(1, 42)]
    stub(monkeypatch, failed, manual=[row(2, 42, outcome="Passed")])
    ok, progress, _ = gather(receipt(failed))
    assert ok and progress["total"] == 1 and progress["remaining"] == 1


def test_initial_and_periodic_producers_both_include_live_broker_triage(monkeypatch, tmp_path, capsys):
    import json
    from argparse import Namespace
    from orchestrator import cli_common as C
    from orchestrator.commands import bugbash_update
    from steps.bug_bash import bugbash_updates as U
    from steps.lib import mockctx
    from tests.test_bugbash_mentions import state

    st = state()
    broker_plan = st.get_step("bug_bash", "clone_plans_broker").data["plan_id"]
    evidence = receipt([row(1, 100)])
    evidence["target"]["plan_id"] = broker_plan
    monkeypatch.setattr(U, "completed_result", lambda _: {
        "auth": {"failed_case_ids": [], "automated_case_ids": []}, "broker": evidence})
    stub(monkeypatch, [row(1, 100)], auth=[row(2, 200, outcome="Passed")])
    with mockctx.active({"people": {OWNER: {
            "name": "Owner", "id": "11111111-1111-1111-1111-111111111111"}}}):
        initial = U.build(st)
        assert initial.kind == "needs_skill"
        assert "1/2 tests done" in initial.payload["content"]
        assert "(Automation triage)" in initial.payload["content"] and "[Broker]" in initial.payload["content"]
        C.save_state(st, str(tmp_path), st.release_id)
        args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=C.DEFAULT_CONFIG,
                         now="2026-09-11T11:00:00-07:00", force=True)
        assert bugbash_update.cmd_post_bugbash_update(args) == 0
    periodic = json.loads(capsys.readouterr().out)
    assert periodic["decision"] == "post" and periodic["total"] == 2
    assert periodic["content"] == initial.payload["content"]
    assert periodic["mentions"] == initial.payload["mentions"]
