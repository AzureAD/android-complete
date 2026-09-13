"""Table-driven tests for the generic workflow transition kernel."""
from datetime import date, datetime, timezone

import pytest

from orchestrator.projection import StateProjection
from orchestrator.invariants import validate_snapshot
from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.state import ReleaseState, StepState
from orchestrator.transitions import (
    EligibilityEvaluator,
    TransitionIntent,
    TransitionKernel,
)
from orchestrator.workflow import WorkflowDefinition
from tests._context import bind_model_state


def _workflow():
    return WorkflowDefinition.compile(
        {
            "version": 1,
            "phases": [
                {
                    "id": "phase",
                    "name": "Phase",
                    "execution": "parallel",
                    "steps": [
                        {"id": "auto", "name": "Auto", "kind": "auto"},
                        {
                            "id": "external",
                            "name": "External",
                            "kind": "external",
                        },
                        {
                            "id": "human",
                            "name": "Human",
                            "kind": "human_action",
                        },
                        {
                            "id": "attest",
                            "name": "Attest",
                            "kind": "attestation",
                        },
                        {
                            "id": "gate",
                            "name": "Gate",
                            "kind": "approval_gate",
                        },
                    ],
                },
                {
                    "id": "optional",
                    "name": "Optional",
                    "conditional": True,
                    "steps": [
                        {"id": "work", "name": "Work", "kind": "auto"},
                    ],
                },
            ],
        }
    )


def _kernel(state=None):
    state = state or ReleaseState(release_id="r")
    workflow = _workflow()
    bind_model_state(state, workflow)

    def projection():
        return StateProjection(
            state,
            workflow,
            date(2026, 9, 11),
            datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
        )

    return state, workflow, projection, TransitionKernel(
        state, workflow, projection, lambda: "2026-09-11T12:00:00+00:00"
    )


@pytest.mark.parametrize(
    "intent,step,allowed",
    [
        (TransitionIntent.EXECUTE, "auto", True),
        (TransitionIntent.EXECUTE, "external", False),
        (TransitionIntent.PREPARE, "external", True),
        (TransitionIntent.PREPARE, "human", True),
        (TransitionIntent.PREPARE, "gate", True),
        (TransitionIntent.PREPARE, "auto", False),
        (TransitionIntent.RESERVE, "external", True),
        (TransitionIntent.RESERVE, "auto", False),
        (TransitionIntent.RESERVE, "gate", False),
        (TransitionIntent.COMPLETE, "human", True),
        (TransitionIntent.COMPLETE, "attest", True),
        (TransitionIntent.COMPLETE, "auto", False),
        (TransitionIntent.COMPLETE, "gate", False),
        (TransitionIntent.SKIP, "auto", True),
        (TransitionIntent.SKIP, "external", True),
        (TransitionIntent.SKIP, "gate", False),
    ],
)
def test_step_kind_event_matrix(intent, step, allowed):
    state, workflow, projection, _ = _kernel()
    result = EligibilityEvaluator(state, workflow, projection()).evaluate(
        intent, "phase", step
    )
    assert result.allowed is allowed


def test_skip_complete_and_reserve_share_projection_eligibility():
    state, _workflow, _projection, kernel = _kernel()
    reserved = kernel.reserve("phase", "external", "worker")
    assert reserved.changed
    execution = state.get_step("phase", "external").execution
    assert execution["owner"] == "worker"
    assert not kernel.skip("phase", "external", "override").changed
    assert not kernel.complete("phase", "external", "").changed
    assert kernel.complete("phase", "external", "owner reviewed receipt").changed
    assert state.get_step("phase", "external").execution is None

    assert kernel.complete("phase", "human", "finished").changed
    assert state.get_step("phase", "human").status == "done"
    assert kernel.skip("phase", "auto", "not applicable").changed
    assert state.get_step("phase", "auto").status == "skipped"


def test_gate_decision_and_reconsideration_are_atomic():
    state, _workflow, projection, kernel = _kernel()
    for step in ("auto", "external", "human", "attest"):
        state.set_step("phase", step, StepState(status="done"))
    assert projection().current_hold().step_id == "gate"

    denied = kernel.deny_gate("evidence incomplete")
    assert denied.changed
    assert projection().release_status() == "blocked"
    assert not kernel.approve_gate("unsafe").changed

    reopened = kernel.reopen("phase", "gate", "new evidence")
    assert reopened.changed
    assert state.gate_decisions == []
    assert projection().release_status() == "holding_gate"

    approved = kernel.approve_gate("reviewed")
    assert approved.changed
    assert state.get_step("phase", "gate").status == "done"
    assert projection().step_complete(_workflow.step("phase", "gate"))


def test_halt_resume_and_conditional_activation_recompute_frontier():
    state, workflow, projection, kernel = _kernel()
    for step in workflow.phase("phase").steps:
        state.set_step("phase", step.id, StepState(status="done"))
        if step.is_gate:
            state.gate_decisions.append(
                {"step": step.key, "decision": "approved"}
            )
    assert projection().release_status() == "complete"

    assert kernel.activate("optional").changed
    assert projection().frontier_phase().id == "optional"
    assert kernel.halt("incident").changed
    assert projection().release_status() == "halted"
    assert kernel.resume("mitigated").changed
    assert projection().release_status() == "running"


def test_record_outcomes_clear_and_restore_pending_actions():
    state, _workflow, _projection, kernel = _kernel()
    done = kernel.apply_outcome(
        kernel.authorize_outcome(TransitionIntent.EXECUTE, "phase", "auto"), Done("passed")
    )
    assert done.changed
    assert "phase.auto" not in _projection().pending_human()

    kernel.reopen("phase", "auto", "retry")
    blocked = kernel.apply_outcome(
        kernel.authorize_outcome(TransitionIntent.EXECUTE, "phase", "auto"), Blocked("failed"),
        block_holds=True,
    )
    assert blocked.kind == "reminder"
    assert "phase.auto" in _projection().pending_human()

    inflight = kernel.apply_outcome(
        kernel.authorize_outcome(TransitionIntent.EXECUTE, "phase", "auto"),
        InProgress("running", poll_in_min=15),
    )
    assert inflight.kind == "waiting"
    record = state.get_step("phase", "auto")
    assert record.data["poll_in_min"] == 15
    assert "phase.auto" not in _projection().pending_human()

    external_workflow = WorkflowDefinition.compile({
        "phases": [{
            "id": "poll",
            "name": "Poll",
            "steps": [{
                "id": "external",
                "name": "External",
                "kind": "external",
                "repeatable": True,
            }],
        }],
    })
    external_state = ReleaseState(release_id="r")
    bind_model_state(external_state, external_workflow)
    external_state.set_step("poll", "external", StepState(status="in_flight"))
    external_projection = StateProjection(
        external_state,
        external_workflow,
        date(2026, 9, 11),
        datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
    )
    assert external_projection.release_status() == "running"


def test_in_flight_external_step_cannot_be_prepared_again():
    state, workflow, projection, _kernel_instance = _kernel()
    state.set_step("phase", "external", StepState(status="in_flight"))
    result = EligibilityEvaluator(state, workflow, projection()).evaluate(
        TransitionIntent.PREPARE, "phase", "external"
    )
    assert not result.allowed
    assert "already in_flight" in result.reason.lower()


def test_reopen_invalidates_transitive_and_later_workflow_results():
    config = {
        "phases": [
            {
                "id": "first",
                "name": "First",
                "steps": [
                    {"id": "source", "name": "Source", "kind": "auto"},
                    {"id": "derived", "name": "Derived", "kind": "auto"},
                    {
                        "id": "gate",
                        "name": "Gate",
                        "kind": "approval_gate",
                    },
                ],
            },
            {
                "id": "later",
                "name": "Later",
                "steps": [{"id": "publish", "name": "Publish", "kind": "auto"}],
            },
        ]
    }
    workflow = WorkflowDefinition.compile(config)
    state = ReleaseState(release_id="r")
    bind_model_state(state, workflow)
    for step in workflow.step_by_key.values():
        state.set_step(step.phase_id, step.id, StepState(status="done"))
    state.gate_decisions = [{"step": "first.gate", "decision": "approved"}]

    def projection():
        return StateProjection(
            state,
            workflow,
            date(2026, 9, 11),
            datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
        )

    kernel = TransitionKernel(
        state, workflow, projection, lambda: "2026-09-11T12:00:00+00:00"
    )
    result = kernel.reopen("first", "source", "new input")
    assert result.changed
    assert result.affected == (
        "first.source",
        "first.derived",
        "first.gate",
        "later.publish",
    )
    assert state.gate_decisions == []
    assert all(
        state.get_step(step.phase_id, step.id).status == "pending"
        for step in workflow.step_by_key.values()
    )


def test_snapshot_invariants_detect_invalid_status_and_legacy_gate():
    state, workflow, projection, _kernel_instance = _kernel()
    state.set_step("phase", "auto", StepState(status="mystery"))
    state.set_step("phase", "gate", StepState(status="skipped"))
    violations = validate_snapshot(state, workflow, projection())
    by_code = {violation.code: violation for violation in violations}
    assert by_code["invalid_step_status"].severity == "error"
    assert by_code["unapproved_terminal_gate"].severity == "warning"


def test_terminal_notification_replaces_identity_and_archives_previous():
    state, _workflow, _projection, kernel = _kernel()
    state.set_step(
        "phase",
        "external",
        StepState(
            status="running",
            execution={
                "id": "new-exec",
                "owner": "worker",
                "started_at": "2026-09-12T00:00:00Z",
                "notification_id": "new-notification",
            },
            data={
                "notification_id": "old-notification",
                "notification_execution_id": "old-exec",
            },
        ),
    )
    from tests.test_guarded_outcomes import _bound_notice
    state.notification_deliveries["new-notification"] = _bound_notice(state, "phase", "external")
    assert kernel.settle_execution("phase", "external", "new-exec", Done("sent")).changed
    data = state.get_step("phase", "external").data
    assert data["notification_id"] == "new-notification"
    assert data["notification_execution_id"] == "new-exec"
    assert data["previous_notifications"] == [{
        "notification_id": "old-notification",
        "notification_execution_id": "old-exec",
    }]


def test_semantically_unchanged_refresh_preserves_downstream_results():
    workflow = WorkflowDefinition.compile({
        "phases": [{
            "id": "phase",
            "name": "Phase",
            "steps": [
                {
                    "id": "source",
                    "name": "Source",
                    "kind": "external",
                    "repeatable": True,
                },
                {"id": "dependent", "name": "Dependent", "kind": "auto"},
                {"id": "tail", "name": "Tail", "kind": "auto"},
            ],
        }],
    })
    state = ReleaseState(release_id="r")
    bind_model_state(state, workflow)
    state.set_step("phase", "source", StepState(status="done", note="5 rows"))
    state.set_step("phase", "dependent", StepState(status="done"))

    def projection():
        return StateProjection(
            state,
            workflow,
            date(2026, 9, 11),
            datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
        )

    kernel = TransitionKernel(
        state, workflow, projection, lambda: "2026-09-12T00:00:00Z")
    assert kernel.reserve("phase", "source", "worker").changed
    execution = state.get_step("phase", "source").execution
    assert kernel.settle_execution("phase", "source", execution["id"], Done("6 rows")).changed
    assert state.get_step("phase", "dependent").status == "done"


@pytest.mark.parametrize("complete_release", [False, True])
def test_refresh_cannot_reopen_an_advanced_or_complete_release(complete_release):
    workflow = WorkflowDefinition.compile({
        "phases": [
            {
                "id": "first",
                "name": "First",
                "steps": [{
                    "id": "source",
                    "name": "Source",
                    "kind": "external",
                    "repeatable": True,
                }],
            },
            {
                "id": "later",
                "name": "Later",
                "steps": [{"id": "publish", "name": "Publish", "kind": "auto"}],
            },
        ],
    })
    state = ReleaseState(release_id="r")
    bind_model_state(state, workflow)
    state.set_step("first", "source", StepState(status="done", note="old"))
    if complete_release:
        state.set_step("later", "publish", StepState(status="done"))

    def projection():
        return StateProjection(
            state,
            workflow,
            date(2026, 9, 11),
            datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
        )

    kernel = TransitionKernel(
        state, workflow, projection, lambda: "2026-09-12T00:00:00Z")
    check = kernel.eligibility().evaluate(
        TransitionIntent.REFRESH, "first", "source")
    assert not check.allowed
    assert "current frontier" in check.reason
    assert not kernel.reserve("first", "source", "worker").changed
    result = kernel.authorize_outcome(TransitionIntent.REFRESH, "first", "source")
    assert not result.changed
    assert state.get_step("first", "source").status == "done"
    assert state.get_step("later", "publish").status == (
        "done" if complete_release else "pending")


def test_cancelled_release_is_not_eligible_and_cleans_up_workers():
    from orchestrator import automations
    from orchestrator import cli_common as C
    from tests._context import fresh_orchestrator

    state = ReleaseState(
        release_id="r",
        cancellation={"reason": "test", "at": "2026-09-12T00:00:00Z"},
    )
    workflow = _workflow()
    bind_model_state(state, workflow)
    projection = StateProjection(
        state,
        workflow,
        date(2026, 9, 11),
        datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
    )
    eligibility = EligibilityEvaluator(state, workflow, projection).evaluate(
        TransitionIntent.PREPARE, "phase", "external"
    )
    assert not eligibility.allowed and "cancelled" in eligibility.reason

    cleanup_state = ReleaseState(release_id="r")
    fresh_orchestrator(C.DEFAULT_CONFIG, cleanup_state, mocks={})
    cleanup_state.cancellation = dict(state.cancellation)
    entry = {
        "id": "worker",
        "name": "Worker",
        "scope": "release",
        "release": "r",
        "steps": ["preflight.notice"],
        "cleanup_when": "steps_done",
    }
    assert automations.cleanup_plan(
        cleanup_state, [entry], C.DEFAULT_CONFIG
    )["removals"] == [
        {
            "id": "worker",
            "name": "Worker",
            "slug": None,
            "cleanup_when": "steps_done",
            "reason": "release cancelled (universal backstop)",
        }
    ]
