"""Exact external-gate recovery using injected providers only; no engine or live IO.

approval/stage_state are build/preparation previews, never recovery receipts.
approval_state supplies the exact GET payload for reconciliation. Submission tests
inject the core-fenced writer: legacy submit='skip' blocks preparation rather than
fabricating a provider receipt. Production rejects gate mocks before all lifecycle hooks.
"""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orchestrator.approvals import ApprovalRequest
from orchestrator.outcomes import Blocked
from orchestrator.parameters import NoParameters
from orchestrator.step_context import ApprovalContext, Clock, EvidenceView, ReleaseView, StepContext
from steps.finalize import publish_notes_gate, remove_rc_tags_gate
from tools import pipelines as P


GATES = (remove_rc_tags_gate, publish_notes_gate)
ORG, PROJECT = "https://example.invalid/org", "project"
APPROVAL = "approval-12"


def _forbidden(*_args, **_kwargs):
    pytest.fail("Unexpected provider operation: tests must inject every read/write")


@pytest.fixture(autouse=True)
def _no_provider_io(monkeypatch):
    monkeypatch.setattr(P, "_ado_rest_get", _forbidden)
    monkeypatch.setattr(P, "_ado_rest_send", _forbidden)
    monkeypatch.setattr(P, "_az_json", _forbidden)


def _approval(approval_id=APPROVAL, *, status="approved", build=12):
    return {
        "id": approval_id, "status": status,
        "pipeline": {"owner": {"_links": {"web": {
            "href": f"{ORG}/{PROJECT}/_build/results?buildId={build}&view=results",
        }}}},
    }


def _request(module):
    return ApprovalRequest(
        org=ORG, project=PROJECT, build_id=12, stage=module.STAGE,
        approval_id=APPROVAL, comment="frozen human comment",
    )


def _context(module, *, inputs=None, request=None, writer=None, reader=None):
    approval = None if request is None else ApprovalContext(
        execution_id="owned-execution", request=request, submit=writer,
    )
    return StepContext(
        release=ReleaseView("2026-08", None, None, None, None, "UTC"),
        evidence=EvidenceView(),
        clock=Clock(datetime(2026, 9, 13, 17, tzinfo=timezone.utc)),
        services=SimpleNamespace(pipelines=SimpleNamespace(
            get_pipeline_approval=reader or _forbidden,
            find_orchestrator_pending_approval=_forbidden,
            orchestrator_stage_state=_forbidden,
            submit_pipeline_approval=_forbidden,
        )),
        parameters=NoParameters() if request else module.ApprovalParameters("reviewed"),
        inputs=inputs or {},
        approval=approval,
    )


@pytest.mark.parametrize("href,expected", [
    ("https://example.invalid/_build/results?buildId=12", 12),
    ("https://example.invalid/_build/results?buildId=123", 123),
    ("https://example.invalid/_build/results?buildId=012&view=results", 12),
    ("https://example.invalid/_build/results?buildId=12&buildId=123", None),
    ("https://example.invalid/_build/results?buildId=12&buildId=12", None),
    ("https://example.invalid/_build/results?buildId=12extra", None),
    ("https://example.invalid/_build/results?buildId=", None),
    ("https://example.invalid/_build/results?buildId=-12", None),
    ("https://example.invalid/_build/results?buildId=0", None),
    ("https://example.invalid/_build/results?buildId=12.0", None),
    ("https://example.invalid/_build/results#buildId=12", None),
    ("https://example.invalid/_build/results?redirect=buildId%3D12", None),
    ("https://example.invalid/_build/results?otherbuildId=12", None),
    (None, None), ({}, None), (12, None),
])
def test_approval_owner_uses_exact_numeric_query(href, expected):
    approval = _approval()
    approval["pipeline"]["owner"]["_links"]["web"]["href"] = href
    assert P.approval_owner_build_id(approval) == expected


@pytest.mark.parametrize("approval", [None, [], {}, {"pipeline": []}, {"pipeline": {"owner": "12"}}])
def test_malformed_owner_is_not_evidence(approval):
    assert P.approval_owner_build_id(approval) is None


def test_pending_build_prefix_collision_does_not_select_other_run(monkeypatch):
    rows = [_approval("other", status="pending", build=123),
            _approval(status="pending", build=12)]
    monkeypatch.setattr(P, "_ado_rest_get", lambda *_a: (True, {"value": rows}, ""))
    assert P._pending_approval_for_build(ORG, PROJECT, 12) == (True, APPROVAL, "")
    rows.pop()
    assert P._pending_approval_for_build(ORG, PROJECT, 12) == (True, None, "")


@pytest.mark.parametrize("status", ["approved", "completed", "rejected", "canceled", "unknown", None])
def test_only_pending_approvals_are_discovered(monkeypatch, status):
    monkeypatch.setattr(P, "_ado_rest_get", lambda *_a: (
        True, {"value": [_approval(status=status)]}, ""))
    assert P._pending_approval_for_build(ORG, PROJECT, 12) == (True, None, "")


@pytest.mark.parametrize("approval_id", [None, "", " ", " padded ", 12, True, [], {}])
def test_pending_approval_requires_real_identity(monkeypatch, approval_id):
    monkeypatch.setattr(P, "_ado_rest_get", lambda *_a: (
        True, {"value": [_approval(approval_id, status="pending")]}, ""))
    ok, identity, detail = P._pending_approval_for_build(ORG, PROJECT, 12)
    assert not ok and identity is None and "valid id" in detail


def test_multiple_build_approvals_fail_closed(monkeypatch):
    monkeypatch.setattr(P, "_ado_rest_get", lambda *_a: (
        True, {"value": [_approval(status="pending"), _approval("second", status="pending")]}, ""))
    ok, identity, detail = P._pending_approval_for_build(ORG, PROJECT, 12)
    assert not ok and identity is None and "ambiguous" in detail


def _timeline(stage="Remove RC Tags", checkpoint="checkpoint"):
    return [
        {"id": "stage", "type": "Stage", "name": stage, "state": "pending"},
        {"id": "phase", "type": "Phase", "parentId": "stage"},
        {"id": checkpoint, "type": "Checkpoint.Approval",
         "state": "inProgress", "parentId": "phase"},
    ]


def _discovery(monkeypatch, timeline):
    monkeypatch.setattr(P, "find_orchestrator_run", lambda *_a: (True, {"id": 12}, ""))
    monkeypatch.setattr(P, "get_timeline", lambda *_a: (True, timeline, ""))
    monkeypatch.setattr(P, "_ado_rest_get", lambda *_a: (
        True, {"value": [_approval(status="pending")]}, ""))


def test_unique_stage_and_build_approval_are_discovered(monkeypatch):
    _discovery(monkeypatch, _timeline())
    ok, info, detail = P.find_orchestrator_pending_approval(ORG, PROJECT, "2026-08")
    assert ok, detail
    assert info["approval_id"] == APPROVAL and info["build_id"] == 12
    assert info["stage"] == "Remove RC Tags"


@pytest.mark.parametrize("same_stage", [True, False])
def test_discovery_does_not_pair_ambiguous_checkpoints(monkeypatch, same_stage):
    timeline = _timeline()
    timeline.append({
        "id": "other-checkpoint", "type": "Checkpoint.Approval", "state": "inProgress",
        "parentId": "phase" if same_stage else "other-stage",
    })
    if not same_stage:
        timeline.append({"id": "other-stage", "type": "Stage", "name": "Unrelated Gate"})
    _discovery(monkeypatch, timeline)
    monkeypatch.setattr(P, "_ado_rest_get", _forbidden)
    ok, info, detail = P.find_orchestrator_pending_approval(ORG, PROJECT, "2026-08")
    assert not ok and info is None and "ambiguous" in detail


@pytest.mark.parametrize("parent", ["absent", "checkpoint", "phase", None, []])
def test_invalid_stage_ancestry_fails_closed(monkeypatch, parent):
    timeline = _timeline()
    timeline[1]["parentId"] = parent
    _discovery(monkeypatch, timeline)
    monkeypatch.setattr(P, "_ado_rest_get", _forbidden)
    ok, info, _ = P.find_orchestrator_pending_approval(ORG, PROJECT, "2026-08")
    assert not ok and info is None


def test_stage_advancement_during_discovery_is_not_misattributed(monkeypatch):
    _discovery(monkeypatch, _timeline())
    timelines = iter([_timeline(), _timeline("Publish GitHub Release Notes", "new-checkpoint")])
    monkeypatch.setattr(P, "get_timeline", lambda *_a: (True, next(timelines), ""))
    ok, info, detail = P.find_orchestrator_pending_approval(ORG, PROJECT, "2026-08")
    assert not ok and info is None and "changed" in detail


@pytest.mark.parametrize("payload", [
    None, [], {}, {"id": None}, {"id": ""}, {"id": 12},
    {"id": "different", "status": "approved"}, {"value": [_approval()]},
])
def test_get_exact_approval_rejects_missing_or_wrong_identity(monkeypatch, payload):
    monkeypatch.setattr(P, "_ado_rest_get", lambda *_a: (True, payload, ""))
    ok, approval, detail = P.get_pipeline_approval(ORG, PROJECT, APPROVAL)
    assert not ok and approval is None and detail


def test_get_exact_approval_uses_id_endpoint(monkeypatch):
    calls = []

    def read(url, timeout):
        calls.append((url, timeout))
        return True, _approval("opaque/id"), ""

    monkeypatch.setattr(P, "_ado_rest_get", read)
    ok, approval, _ = P.get_pipeline_approval(ORG, PROJECT, "opaque/id", timeout=7)
    assert ok and approval["id"] == "opaque/id"
    assert calls == [(f"{ORG}/{PROJECT}/_apis/pipelines/approvals/opaque%2Fid?api-version=7.2-preview.1", 7)]


@pytest.mark.parametrize("payload,expected", [
    ({"value": [{"id": APPROVAL, "status": "approved"}]}, True),
    ({"value": [{"id": "other", "status": "approved"}]}, False),
    ({"value": [{"id": None, "status": "approved"}]}, False),
    ({"value": [{"id": APPROVAL, "status": "pending"}]}, False),
    ({"value": [{"id": APPROVAL, "status": "rejected"}]}, False),
    ({"value": [{"id": APPROVAL, "status": "approved"},
                {"id": "other", "status": "approved"}]}, False),
])
def test_submit_requires_matching_response_identity_and_status(monkeypatch, payload, expected):
    writes = []

    def send(url, method, body, timeout):
        writes.append((method, body))
        return True, payload, ""

    monkeypatch.setattr(P, "_ado_rest_send", send)
    ok, _ = P.submit_pipeline_approval(ORG, PROJECT, APPROVAL, comment="frozen comment")
    assert ok is expected
    assert writes == [("PATCH", [{
        "approvalId": APPROVAL, "status": "approved", "comment": "frozen comment",
    }])]


@pytest.mark.parametrize("evidence,expected", [
    (_approval(), True),
    (_approval(status="pending"), False),
    (_approval(status="rejected"), False),
    (_approval("other"), False),
    (None, False),
    ({"status": "approved"}, False),
])
def test_submit_without_response_id_needs_exact_readback(monkeypatch, evidence, expected):
    writes, reads = [], []

    def send(*_args):
        writes.append("PATCH")
        return True, {"value": [{"status": "approved"}]}, ""

    def read(url, _timeout):
        reads.append(url)
        return True, evidence, ""

    monkeypatch.setattr(P, "_ado_rest_send", send)
    monkeypatch.setattr(P, "_ado_rest_get", read)
    ok, _ = P.submit_pipeline_approval(ORG, PROJECT, APPROVAL)
    assert ok is expected
    assert writes == ["PATCH"]
    assert reads == [f"{ORG}/{PROJECT}/_apis/pipelines/approvals/{APPROVAL}?api-version=7.2-preview.1"]


@pytest.mark.parametrize("module", GATES)
def test_prepare_freezes_identity_and_comment(module):
    info = {"approval_id": APPROVAL, "build_id": 12, "stage": module.STAGE}
    context = _context(module, inputs={"approval": info})
    request = module.prepare_approval(context)
    assert isinstance(request, ApprovalRequest)
    assert (request.org, request.project) == (module.CONFIG["org"], module.CONFIG["project"])
    assert (request.build_id, request.stage, request.approval_id, request.comment) == (
        12, module.STAGE, APPROVAL, "reviewed",
    )
    info["approval_id"] = "new-approval"
    assert request.approval_id == APPROVAL
    with pytest.raises(FrozenInstanceError):
        request.comment = "changed"
    assert module.PARAMETERS == {"prepare_approval": module.ApprovalParameters}


@pytest.mark.parametrize("module", GATES)
def test_prepare_freezes_default_comment(module):
    context = _context(module, inputs={"approval": {
        "approval_id": APPROVAL, "build_id": 12, "stage": module.STAGE,
    }})
    request = module.prepare_approval(replace(context, parameters=module.ApprovalParameters()))
    assert request.comment == f"Approved via Scout (release-agent {module.ID})."


@pytest.mark.parametrize("module", GATES)
@pytest.mark.parametrize("changed", [
    {"approval_id": None}, {"approval_id": ""}, {"approval_id": 12},
    {"approval_id": " padded "}, {"build_id": None}, {"build_id": 0},
    {"build_id": -12}, {"build_id": "12"}, {"build_id": True}, {"stage": None},
    {"stage": "Unrelated Gate"},
])
def test_prepare_rejects_invalid_or_different_stage_identity(module, changed):
    info = {"approval_id": APPROVAL, "build_id": 12, "stage": module.STAGE, **changed}
    result = module.prepare_approval(_context(module, inputs={"approval": info}))
    assert isinstance(result, Blocked)


@pytest.mark.parametrize("module", GATES)
def test_completed_stage_is_not_a_substitute_for_prepare_identity(module):
    result = module.prepare_approval(_context(module, inputs={
        "approval": None, "stage_state": {"state": "completed", "result": "succeeded"},
    }))
    assert isinstance(result, Blocked)
    assert "Stage completion alone" in result.reason


@pytest.mark.parametrize("module", GATES)
def test_legacy_submit_skip_blocks_prepare_without_discovery(module):
    result = module.prepare_approval(_context(module, inputs={"submit": "skip"}))
    assert isinstance(result, Blocked)
    assert "fake provider ports" in result.reason


@pytest.mark.parametrize("module", GATES)
def test_submit_only_calls_fenced_writer_and_does_not_fabricate_mock_success(module):
    writes = []
    request = _request(module)

    def writer():
        writes.append(request)
        return False, "injected provider failure"

    context = _context(module, request=request, writer=writer, inputs={
        "approval": {"approval_id": "new-run", "build_id": 123},
    })
    assert module.submit_approval(context) == (False, "injected provider failure")
    assert writes == [request]


@pytest.mark.parametrize("module", GATES)
@pytest.mark.parametrize("evidence,expected", [
    (_approval(), True),
    (_approval(status="pending"), False),
    (_approval(status="rejected"), False),
    (_approval(status="canceled"), False),
    (_approval(status="completed"), False),
    (_approval(status="unknown"), False),
    (_approval(status=None), False),
    (_approval("other"), False),
    (_approval(None), False),
    (_approval(build=123), False),
    ({"id": APPROVAL, "status": "approved"}, False),
    (None, False), ([], False), ({}, False),
])
def test_reconcile_requires_exact_identity_owner_and_approved_status(module, evidence, expected):
    calls = []

    def read(*args):
        calls.append(args)
        return True, evidence, ""

    context = _context(module, request=_request(module), writer=_forbidden, reader=read)
    ok, detail = module.reconcile_approval(context)
    assert ok is expected and detail
    assert calls == [(ORG, PROJECT, APPROVAL)]


@pytest.mark.parametrize("module", GATES)
def test_reconcile_provider_failure_never_writes(module):
    context = _context(module, request=_request(module), writer=_forbidden,
                       reader=lambda *_a: (False, _approval(), "unavailable"))
    ok, detail = module.reconcile_approval(context)
    assert not ok and "unavailable" in detail


@pytest.mark.parametrize("module", GATES)
def test_new_build_and_stage_completion_cannot_redirect_frozen_reconciliation(module):
    calls = []

    def read(*args):
        calls.append(args)
        return True, _approval(status="pending"), ""

    context = _context(module, request=_request(module), writer=_forbidden, reader=read, inputs={
        "approval": {"approval_id": "new-approval", "build_id": 123, "stage": module.STAGE},
        "stage_state": {"state": "completed", "result": "succeeded", "build_id": 123},
        "submit": "skip",
    })
    context = replace(context, release=replace(context.release, release_id="2026-09"))
    ok, _ = module.reconcile_approval(context)
    assert not ok and calls == [(ORG, PROJECT, APPROVAL)]


@pytest.mark.parametrize("module", GATES)
@pytest.mark.parametrize("evidence,expected", [(_approval(), True), (None, False), (_approval(build=123), False)])
def test_approval_state_mock_is_exact_read_evidence_only(module, evidence, expected):
    context = _context(module, request=_request(module), writer=_forbidden,
                       inputs={"approval_state": evidence})
    ok, _ = module.reconcile_approval(context)
    assert ok is expected


@pytest.mark.parametrize("module", GATES)
def test_reconcile_missing_or_wrong_stage_request_retains_hold(module):
    ok, _ = module.reconcile_approval(_context(module))
    assert not ok
    request = replace(_request(module), stage="Unrelated Gate")
    ok, _ = module.reconcile_approval(_context(module, request=request, writer=_forbidden))
    assert not ok
