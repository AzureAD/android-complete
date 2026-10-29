"""Final publish-attempt selection must not assume the first job attempt."""
import pytest

from tools import pipelines as P
from tools.pipelines.orchestrator import _latest_final_mrwp


@pytest.mark.parametrize("tags,expected", [
    (["Final1=101"], "101"),
    (["Final2=1707747"], "1707747"),
    (["Final1=101", "Final2=202"], "202"),
    (["Final2=202", "Final1=101"], "202"),
    (["Final9=909", "Final10=1010", "Final2=202"], "1010"),
    (["Final2=202", "Final2=202"], "202"),
    (["RC8", "RC8-ECS=808", "Final1=101"], "101"),
])
def test_latest_final_tag_uses_numeric_publish_attempt(tags, expected):
    assert _latest_final_mrwp(tags) == (True, expected, "")


@pytest.mark.parametrize("tags", [
    ["Final1=101", "Final1=102"],
    ["Final2=202", "Final1=101", "Final2=203"],
    ["Final0=101"],
    ["Final01=101"],
    ["Final2=0"],
    ["Final2=0202"],
    ["Final2="],
    ["Final2=not-a-build"],
    ["Final2=202suffix"],
    ["Final2"],
    ["Final2=202=203"],
    ["Final1=101", "Final2=bad"],
    ["Final2=202", "Final1=bad"],
    ["Final2=202", None],
    "Final2=202",
])
def test_invalid_or_ambiguous_final_tags_fail_closed(tags):
    ok, build, detail = _latest_final_mrwp(tags)
    assert ok is False and build is None and detail


def test_absent_final_tag_is_a_wait_not_an_error():
    assert _latest_final_mrwp(["RC2", "Unrelated=123"]) == (
        True, None, "no valid Final<N> tag yet")


@pytest.fixture
def finalization(monkeypatch):
    run = {"id": 1705640, "status": "inProgress", "tags": ["Final2=1707747"]}
    timeline = [
        {"id": "stage", "type": "Stage", "identifier": "PublishGitHubReleaseNotes",
         "name": "Publish GitHub Release Notes", "state": "pending"},
        {"id": "approval", "parentId": "stage", "type": "Checkpoint.Approval",
         "state": "inProgress"},
    ]
    final = {"id": 1707747, "status": "completed", "result": "succeeded", "tags": []}
    calls = []
    monkeypatch.setattr(P, "find_orchestrator_run", lambda *a: (True, run, ""))
    monkeypatch.setattr(P, "get_timeline", lambda *a: (True, timeline, ""))

    def read(args, timeout):
        calls.append(args)
        return True, final, ""

    monkeypatch.setattr(P, "_az_json", read)
    return run, timeline, final, calls


def observe():
    return P.orchestrator_finalization_status(
        "https://identitydivision.visualstudio.com", "Engineering",
        "2026-10", "PublishGitHubReleaseNotes")


def test_final2_only_resolves_the_actual_successful_final_build(finalization):
    _, _, _, calls = finalization
    ok, info, detail = observe()
    assert ok and detail == "" and info["status"] == "ready"
    assert info["mrwp_run_id"] == "1707747"
    assert info["orchestrator_run_id"] == "1705640"
    assert calls[0][calls[0].index("--id") + 1] == "1707747"


@pytest.mark.parametrize("status,result,expected", [
    ("inProgress", None, "waiting"),
    ("notStarted", None, "waiting"),
    ("completed", "failed", "failed"),
    ("completed", "canceled", "failed"),
    ("completed", "partiallySucceeded", "ready"),
])
def test_newest_attempt_outcome_never_falls_back_to_final1(finalization, status, result, expected):
    run, _, final, calls = finalization
    run["tags"] = ["Final1=1690000", "Final2=1707747"]
    final.update(status=status, result=result)
    ok, info, _ = observe()
    assert ok and info["status"] == expected
    assert len(calls) == 1
    assert calls[0][calls[0].index("--id") + 1] == "1707747"


def test_wrong_returned_build_identity_blocks_finalization(finalization):
    finalization[2]["id"] = 1690000
    ok, info, detail = observe()
    assert not ok and info is None and "mismatched build identity" in detail


def test_ambiguous_tags_never_query_a_build(finalization):
    run, _, _, calls = finalization
    run["tags"] = ["Final2=1707747", "Final2=9999999"]
    ok, info, detail = observe()
    assert not ok and info is None and "conflicting Final2" in detail
    assert calls == []


def test_missing_tag_waits_without_querying_another_build(finalization):
    run, _, _, calls = finalization
    run["tags"] = ["RC2-ECS=1705757"]
    ok, info, detail = observe()
    assert ok and info["status"] == "waiting" and "Final<N>" in detail
    assert calls == []


def test_final_tag_does_not_bypass_reaching_the_publication_gate(finalization):
    _, timeline, _, calls = finalization
    timeline.pop()
    ok, info, detail = observe()
    assert ok and info["status"] == "waiting" and "not reached" in detail
    assert calls == []
