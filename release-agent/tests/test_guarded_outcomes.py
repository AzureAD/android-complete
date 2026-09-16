"""Invocation rights and receipt settlement share one guarded outcome boundary."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.projection import StateProjection
from orchestrator.state import ReleaseState, StepState
from orchestrator.transitions import OutcomePermit, TransitionIntent as I, TransitionKernel, TransitionResult
from orchestrator.workflow import WorkflowDefinition
from tests._context import bind_model_state


def _bound_notice(state, phase, sid, status="sent"):
    from orchestrator import delivery as D

    execution = state.get_step(phase, sid).execution
    item = D.descriptor(state, "notice", {"kind": "step", "phase": phase, "step": sid},
                        "workiq_send_email", {"to": ["owner@example.com"], "body": "notice"},
                        {"kind": "step", "record_as": sid})
    item["id"] = execution["notification_id"]
    item["hash"] = D.fingerprint({k: v for k, v in item.items() if k != "hash"})
    return {"descriptor": item, "status": status, "attempts": [{
        **execution, "status": status, "hash": item["hash"],
        "acknowledged_at": execution["started_at"], "evidence": "provider receipt",
    }]}


@pytest.fixture
def context():
    workflow = WorkflowDefinition.compile({"phases": [
        {"id": "p", "name": "P", "execution": "parallel", "anchor": "CCD", "steps": [
            {"id": "source", "name": "Source", "kind": "auto", "effect_mode": "read_only"},
            {"id": "auto", "name": "Auto", "kind": "auto", "effect_mode": "read_only", "depends_on": ["source"]},
            {"id": "effect", "name": "Effect", "kind": "auto", "effect_mode": "transactional", "effect_recovery": "frozen"},
            {"id": "external", "name": "External", "kind": "external", "repeatable": True, "pollable": True, "depends_on": ["source"]},
            {"id": "write", "name": "Write", "kind": "external", "write_command": "distribute-tests"},
            {"id": "human", "name": "Human", "kind": "human_action"},
            {"id": "gate", "name": "Gate", "kind": "approval_gate"},
        ]},
        {"id": "later", "name": "Later", "steps": [
            {"id": "auto", "name": "Later", "kind": "auto", "effect_mode": "read_only"},
        ]},
    ]})
    state = ReleaseState(release_id="r", ccd="2026-09-12")
    bind_model_state(state, workflow)
    state.set_step("p", "source", StepState(status="done"))
    settings = SimpleNamespace(
        now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        signed=True, blocked=False, fire=None, mocked=False,
    )

    def projection():
        return StateProjection(
            state, workflow, settings.now.date(), settings.now,
            readiness_signed=settings.signed, readiness_blocked=settings.blocked,
            fire_at=lambda _step: settings.fire, is_mocked=lambda _step: settings.mocked,
        )

    kernel = TransitionKernel(state, workflow, projection, lambda: settings.now.isoformat())
    return state, settings, kernel, projection


def _suspend(state, settings, reason):
    if reason == "halt":
        state.halt = {"reason": "incident", "at": settings.now.isoformat()}
    elif reason == "cancel":
        state.cancellation = {"reason": "cancelled", "at": settings.now.isoformat()}
    elif reason == "unsigned":
        settings.signed = False
    elif reason == "readiness":
        settings.blocked = True
    else:
        state.gate_decisions.append({"step": "p.gate", "decision": "denied"})


@pytest.mark.parametrize("intent,phase,step", [
    (I.EXECUTE, "missing", "auto"), (I.EXECUTE, "p", "missing"),
    (I.EXECUTE, "p", "human"), (I.EXECUTE, "p", "external"),
    (I.RECORD, "p", "gate"), (I.RECORD, "p", "auto"),
    (I.RECORD, "p", "human"), (I.RECORD, "p", "write"),
    (I.PREPARE, "p", "auto"), (I.POLL, "p", "external"),
    (I.REFRESH, "p", "external"), (I.MOCK, "p", "human"),
    (I.EXECUTE, "later", "auto"), (I.COMPLETE, "p", "human"),
])
def test_invalid_invocation_routes_are_mutation_free(context, intent, phase, step):
    state, _, kernel, _ = context
    before = deepcopy(state)
    result = kernel.authorize_outcome(intent, phase, step)
    assert isinstance(result, TransitionResult) and not result.changed and result.message
    assert state == before


@pytest.mark.parametrize("reason", ["halt", "cancel", "unsigned", "readiness", "denied"])
@pytest.mark.parametrize("intent,step", [(I.EXECUTE, "auto"), (I.PREPARE, "external"), (I.RECORD, "external")])
def test_new_work_cannot_bypass_any_suspension(context, reason, intent, step):
    state, settings, kernel, _ = context
    _suspend(state, settings, reason)
    before = deepcopy(state)
    assert isinstance(kernel.authorize_outcome(intent, "p", step), TransitionResult)
    assert state == before


@pytest.mark.parametrize("intent", [I.EXECUTE, I.PREPARE, I.POLL, I.REFRESH])
@pytest.mark.parametrize("constraint", ["prerequisite", "phase-date", "fire-time"])
def test_time_and_prerequisites_apply_to_every_new_call(context, intent, constraint):
    state, settings, kernel, _ = context
    sid = "auto" if intent == I.EXECUTE else "external"
    if intent in (I.POLL, I.REFRESH):
        state.set_step("p", sid, StepState(status="in_flight" if intent == I.POLL else "done"))
    if constraint == "prerequisite":
        state.steps.pop("p.source")
    elif constraint == "phase-date":
        state.ccd = "2026-09-13"
    else:
        settings.fire = "13:00"
    before = deepcopy(state)
    result = kernel.authorize_outcome(intent, "p", sid)
    assert isinstance(result, TransitionResult) and not result.changed
    assert state == before


@pytest.mark.parametrize("sid", ["human", "gate"])
def test_preparation_is_not_human_completion_even_with_forged_attribution(context, sid):
    state, _, kernel, _ = context
    permit = kernel.authorize_outcome(I.PREPARE, "p", sid)
    assert isinstance(permit, OutcomePermit)
    before = deepcopy(state)
    result = kernel.apply_outcome(permit, Done("bypass", by="human"), data={"changed": True})
    assert not result.changed
    assert state == before


def test_only_explicit_non_gate_mock_route_completes_human_work(context):
    state, settings, kernel, _ = context
    settings.mocked = True
    permit = kernel.authorize_outcome(I.MOCK, "p", "human")
    assert kernel.apply_outcome(permit, Done("mock", by="mock")).changed
    before = deepcopy(state)
    assert isinstance(kernel.authorize_outcome(I.MOCK, "p", "gate"), TransitionResult)
    assert state == before


@pytest.mark.parametrize("intent", [I.EXECUTE, I.PREPARE, I.POLL, I.REFRESH])
@pytest.mark.parametrize("reason", ["halt", "cancel", "unsigned", "readiness", "denied"])
def test_preissued_result_settles_without_authorizing_downstream_work(context, intent, reason):
    state, settings, kernel, projection = context
    sid = "auto" if intent == I.EXECUTE else "external"
    if intent in (I.POLL, I.REFRESH):
        state.set_step("p", sid, StepState(status="in_flight" if intent == I.POLL else "done"))
    permit = kernel.authorize_outcome(intent, "p", sid)
    assert isinstance(permit, OutcomePermit)
    _suspend(state, settings, reason)
    assert kernel.apply_outcome(permit, Done("provider evidence")).changed
    assert state.get_step("p", sid).status == "done"
    assert projection().scheduling().suspension
    assert not projection().scheduling().runnable
    before = deepcopy(state)
    assert not kernel.apply_outcome(permit, Done("duplicate")).changed
    assert state == before


@pytest.mark.parametrize("mutation", ["reopen", "new-owner", "terminal"])
def test_preissued_permit_cannot_overwrite_a_new_generation(context, mutation):
    state, _, kernel, _ = context
    permit = kernel.authorize_outcome(I.PREPARE, "p", "external")
    if mutation == "reopen":
        kernel.reopen("p", "source", "changed prerequisite")
    elif mutation == "new-owner":
        kernel.reserve("p", "external", "other")
    else:
        other = kernel.authorize_outcome(I.PREPARE, "p", "external")
        assert kernel.apply_outcome(other, Done("winner")).changed
    before = deepcopy(state)
    assert not kernel.apply_outcome(permit, Done("stale"), data={"unsafe": True}).changed
    assert state == before


def test_permits_are_issuer_bound_not_reconstructable_or_persisted(context):
    state, _, kernel, projection = context
    permit = kernel.authorize_outcome(I.EXECUTE, "p", "auto")
    other_kernel = TransitionKernel(state, kernel.workflow, projection, kernel.now)
    before = deepcopy(state)
    for rejected in (OutcomePermit("p", "auto", I.EXECUTE), None):
        assert not kernel.apply_outcome(rejected, Done()).changed
    assert not other_kernel.apply_outcome(permit, Done()).changed
    assert state == before


@pytest.mark.parametrize("reason", ["halt", "cancel", "unsigned", "readiness", "denied"])
def test_exact_reserved_receipt_settles_while_suspended_but_new_writes_cannot(context, reason):
    from orchestrator import write_review as W

    state, settings, kernel, projection = context
    plan = W.WritePlan("distribute-tests", {}, (
        W.WriteOperation("update", {"work_item": 1}, {"state": "Ready"}, {"revision": 2}),
    ))
    review = {"hash": W.review_hash(
        SimpleNamespace(state=state, workflow=kernel.workflow), "p", "write", plan),
        "approved_by": "test-reviewer"}
    assert kernel.reserve("p", "write", "worker", write_review=review).changed
    execution_id = state.get_step("p", "write").execution["id"]
    assert isinstance(kernel.begin_reviewed_write("p", "write", execution_id), OutcomePermit)
    _suspend(state, settings, reason)
    assert isinstance(kernel.authorize_outcome(I.WRITE, "p", "write", execution_id=execution_id), TransitionResult)
    assert kernel.settle_execution("p", "write", execution_id, Done("receipt")).changed
    assert not projection().scheduling().runnable
    before = deepcopy(state)
    assert not kernel.settle_execution("p", "write", execution_id, Done("replay")).changed
    assert state == before


@pytest.mark.parametrize("owner", [None, "", "wrong"])
def test_poll_and_receipt_require_exact_execution(context, owner):
    state, _, kernel, _ = context
    kernel.reserve("p", "external", "worker")
    execution_id = state.get_step("p", "external").execution["id"]
    assert kernel.settle_execution("p", "external", execution_id, InProgress("queued")).changed
    before = deepcopy(state)
    assert isinstance(kernel.authorize_outcome(I.POLL, "p", "external", execution_id=owner), TransitionResult)
    assert not kernel.settle_execution("p", "external", owner, Done("wrong")).changed
    assert state == before
    permit = kernel.authorize_outcome(I.POLL, "p", "external", execution_id=execution_id)
    assert kernel.apply_outcome(permit, Done("polled")).changed


def test_invalidated_owner_cannot_settle_or_clear_replacement(context):
    state, _, kernel, _ = context
    kernel.reserve("p", "external", "old")
    old_id = state.get_step("p", "external").execution["id"]
    kernel.reopen("p", "source", "new input")
    before = deepcopy(state)
    assert not kernel.settle_execution("p", "external", old_id, Done("late")).changed
    assert state == before
    kernel.reopen("p", "external", "old worker stopped")
    permit = kernel.authorize_outcome(I.EXECUTE, "p", "source")
    assert kernel.apply_outcome(permit, Done()).changed
    assert kernel.reserve("p", "external", "new").changed
    before = deepcopy(state)
    assert not kernel.settle_execution("p", "external", old_id, Done("late again")).changed
    assert state == before


def test_data_aliases_cannot_mutate_state_on_rejection(context):
    state, _, kernel, _ = context
    state.set_step("p", "external", StepState(
        status="running", execution={"id": "owner", "owner": "worker", "started_at": "now"},
        data={"nested": {"rows": [1]}}, links=[{"name": "original"}],
    ))
    before = deepcopy(state)
    candidate = state.get_step("p", "external")
    candidate.data["nested"]["rows"].append(2)
    candidate.links[0]["name"] = "changed"
    candidate.execution["id"] = "forged"
    assert not kernel.settle_execution("p", "external", "forged", Done(), data=candidate.data).changed
    assert state == before


def test_owned_notification_requires_receipt_and_defers_completion_during_halt(context):
    state, settings, kernel, _ = context
    kernel.reserve("p", "external", "worker")
    step = state.get_step("p", "external")
    execution_id = step.execution["id"]
    step.execution["notification_id"] = "notice"
    state.set_step("p", "external", step)
    before = deepcopy(state)
    assert not kernel.settle_execution("p", "external", execution_id, Done()).changed
    assert state == before
    state.notification_deliveries["notice"] = _bound_notice(state, "p", "external")
    _suspend(state, settings, "halt")
    before = deepcopy(state)
    assert not kernel.settle_execution("p", "external", execution_id, Done()).changed
    assert state == before
    kernel.resume()
    assert kernel.settle_execution("p", "external", execution_id, Done()).changed


@pytest.mark.parametrize("outcome", [
    None, {"kind": "done"}, Done(note=None), Done(links={}), Done(links=[None]),
    Done(kind="invalid"), InProgress(poll_in_min=0),
])
def test_malformed_outcomes_raise_without_consuming_owner_or_permit(context, outcome):
    state, _, kernel, _ = context
    permit = kernel.authorize_outcome(I.EXECUTE, "p", "auto")
    before = deepcopy(state)
    with pytest.raises((TypeError, ValueError)):
        kernel.apply_outcome(permit, outcome)
    assert state == before
    assert kernel.apply_outcome(permit, Done("valid")).changed


def test_no_public_unchecked_outcome_setter(context):
    _, _, kernel, _ = context
    assert not hasattr(kernel, "record_outcome")


def test_unowned_effect_cannot_complete_through_observation_permit(context):
    state, _, kernel, _ = context
    permit = kernel.authorize_outcome(I.EXECUTE, "p", "effect")
    before = deepcopy(state)
    for outcome in (Done("bypass checkpoint"), InProgress("bypass checkpoint")):
        assert not kernel.apply_outcome(permit, outcome).changed
        assert state == before
    assert kernel.apply_outcome(permit, Blocked("preparation failed")).changed


def test_notification_invocation_permit_cannot_bypass_receipt_or_resume(context):
    state, settings, kernel, _ = context
    kernel.reserve("p", "external", "worker")
    record = state.get_step("p", "external")
    execution_id = record.execution["id"]
    record.execution["notification_id"] = "notice"
    state.set_step("p", "external", record)
    state.notification_deliveries["notice"] = _bound_notice(state, "p", "external", "claimed")
    permit = kernel.authorize_outcome(I.WRITE, "p", "external", execution_id=execution_id)
    before = deepcopy(state)
    assert not kernel.apply_outcome(permit, Done("no receipt")).changed
    assert state == before
    state.notification_deliveries["notice"] = _bound_notice(state, "p", "external")
    _suspend(state, settings, "halt")
    before = deepcopy(state)
    assert not kernel.apply_outcome(permit, Done("suspended")).changed
    assert state == before
    kernel.resume()
    assert kernel.apply_outcome(permit, Done("acknowledged")).changed


def test_reserved_running_generation_invalidated_after_start_cannot_settle(context):
    state, _, kernel, _ = context
    kernel.reserve("p", "external", "worker")
    record = state.get_step("p", "external")
    record.invalidated_at = "2026-09-12T12:01:00+00:00"
    state.set_step("p", "external", record)
    before = deepcopy(state)
    assert not kernel.settle_execution("p", "external", record.execution["id"], Done()).changed
    assert state == before


@pytest.mark.parametrize("change", ["none", "halt", "invalidate", "replace"])
def test_automated_omission_requires_the_same_live_poll_owner(context, change):
    state, settings, kernel, _ = context
    kernel.reserve("p", "external", "worker")
    record = state.get_step("p", "external")
    record.status = "in_flight"
    state.set_step("p", "external", record)
    permit = kernel.authorize_outcome(I.POLL, "p", "external", execution_id=record.execution["id"])
    if change == "halt":
        _suspend(state, settings, "halt")
    elif change == "invalidate":
        kernel.reopen("p", "source", "new inputs")
    elif change == "replace":
        record.execution["id"] = "new-owner"
        state.set_step("p", "external", record)
    before = deepcopy(state)
    result = kernel.omit_execution(permit, "Configured cutoff reached", links=[])
    if change == "none":
        assert result.changed
        assert state.get_step("p", "external").status == "skipped"
        assert state.get_step("p", "external").execution is None
        before = deepcopy(state)
        assert not kernel.omit_execution(permit, "duplicate").changed
    else:
        assert not result.changed
    assert state == before
