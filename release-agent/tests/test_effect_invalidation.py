"""Upstream invalidation must not strand a currently owned auto effect."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import effects
from orchestrator.authority import OwnStepData, VersionEvidence
from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.evidence import ReleaseVersions, StepData
from orchestrator.handler_contracts import HookRole
from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.state import ReleaseState, StepState
from orchestrator.transitions import TransitionIntent as I


POLICIES = [("transactional", "frozen"), ("idempotent", "frozen"), ("idempotent", "match_current")]
NOW = datetime(2026, 9, 13, 9, tzinfo=timezone.utc)


def _orch(tmp_path, *, mode="transactional", recovery="frozen", source_kind="human_action",
          policy="status", status="running", dependent=True, later=False, conditional=False,
          execution="parallel", write_command=None):
    calls = []
    control = {"during": lambda context: None}

    def build(context):
        calls.append("build")
        return Done("observed", updates=(StepData({"observed": True}),))

    def prepare(context):
        calls.append("prepare")
        return {"target": "resource"}

    def execute(context):
        calls.append(("execute", context.effect.execution["id"]))
        control["during"](context)
        return Done("confirmed")

    def reconcile(context):
        calls.append(("reconcile", context.effect.execution["id"]))
        control["during"](context)
        return Done("reconciled")

    source = {"id": "source", "name": "Source", "kind": source_kind}
    if source_kind == "external":
        source.update(repeatable=True, pollable=True, refresh_invalidation=policy)
    if write_command:
        source["write_command"] = write_command
    effect = {"id": "effect", "name": "Effect", "kind": "auto",
              "effect_mode": mode, "effect_recovery": recovery}
    if dependent and not later:
        effect["depends_on"] = ["source"]
    tail = {"id": "tail", "name": "Tail", "kind": "human_action", "depends_on": ["effect"]}
    gate = {"id": "gate", "name": "Gate", "kind": "approval_gate", "depends_on": ["source"]}
    other = {"id": "other", "name": "Independent", "kind": "human_action"}
    phases = [{"id": "p", "name": "P", "execution": execution,
               "steps": [source, gate, other] if later else [source, effect, tail, gate, other]}]
    if conditional:
        phases.append({"id": "optional", "name": "Optional", "conditional": True,
                       "steps": [{"id": "repair", "name": "Repair", "kind": "human_action"}]})
    effect_phase = "q" if later else "p"
    if later:
        phases.append({"id": "q", "name": "Q", "execution": "parallel", "steps": [effect, tail]})
    handler = SimpleNamespace(
        ID="effect", KIND="agent", EFFECT_MODE=mode, EFFECT_RECOVERY=recovery,
        EVIDENCE=(OwnStepData(),), build=build, prepare_effect=prepare, execute=execute, reconcile=reconcile)
    observer = SimpleNamespace(
        ID="source", KIND="scout", WRITE_COMMAND=write_command,
        EVIDENCE=(OwnStepData(), VersionEvidence()), build=build)
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump({"phases": phases}), encoding="utf-8")
    state = ReleaseState(release_id="r", timezone="UTC", versions={"broker": "1"})
    state.set_step("p", "source", StepState(status="done", note="original", data={"original": [1]}))
    if later or execution == "parallel":
        state.set_step("p", "gate", StepState(status="done"))
        state.gate_decisions = [{"step": "p.gate", "decision": "approved"}]
    if later:
        state.set_step("p", "other", StepState(status="done"))
    orch = Orchestrator(str(path), state, mocks={}, now=NOW, handler_resolver=lambda pid, sid: (
        handler if (pid, sid) == (effect_phase, "effect") else (
            observer if sid == "source" and source_kind == "external" else None)))
    control.update(calls=calls, phase=effect_phase, mode=mode, recovery=recovery)
    if status:
        _own(orch, control, status)
    return orch, control


def _own(orch, control, status="running"):
    value = {"target": "resource"}
    orch.state.set_step(control["phase"], "effect", StepState(status=status, execution={
        "id": "owned-effect", "owner": "engine", "started_at": NOW.isoformat(),
        "effect_mode": control["mode"], "effect_recovery": control["recovery"],
        "effect_input": value, "operation_key": effects.input_key("effect", value),
    }))


def _permits(orch):
    return dict(orch._transition_kernel()._outcome_permits)


@pytest.mark.parametrize("mode,recovery", POLICIES)
@pytest.mark.parametrize("status", ["running", "in_flight", "blocked"])
@pytest.mark.parametrize("execution", ["parallel", "sequential"])
def test_reopen_preserves_active_effect_and_existing_permit_then_succeeds_after_settlement(
        tmp_path, mode, recovery, status, execution):
    orch, control = _orch(tmp_path, mode=mode, recovery=recovery, status=status, execution=execution)
    permit = orch.authorize_outcome(I.EFFECT, "p", "effect", execution_id="owned-effect")
    before, permits = deepcopy(orch.state), _permits(orch)
    result = orch.reopen("p", "source", "new input")
    assert not result.changed and "p.effect" in result.message and "owned-effect" in result.message
    assert "Settle" in result.message
    assert orch.state == before and _permits(orch) == permits
    orch.validate_outcome_permit(permit)
    assert orch.apply_outcome(permit, Done("confirmed")).kind == "ran"
    result = orch.reopen("p", "source", "new input")
    assert result.changed and {"p.source", "p.effect", "p.tail", "p.gate"} <= set(result.affected)
    assert not orch.state.gate_decisions
    assert orch.state.get_step("p", "effect").status == "pending"
    assert not control["calls"]


@pytest.mark.parametrize("mode,recovery", POLICIES)
def test_reconciliation_and_durable_evidence_survive_rejected_reopen(tmp_path, mode, recovery):
    orch, control = _orch(tmp_path, mode=mode, recovery=recovery)
    checkpoints = []
    orch.state._checkpoint = lambda: checkpoints.append(deepcopy(orch.state.steps))

    def during(context):
        context.effect.commit.commit(StepData({"provider_id": 42}))
        before, permits = deepcopy(orch.state), _permits(orch)
        assert not orch.reopen("p", "source", "new inputs").changed
        assert orch.state == before and _permits(orch) == permits

    control["during"] = during
    assert orch.step_once().kind == "ran"
    assert orch.state.get_step("p", "effect").data == {"provider_id": 42}
    assert checkpoints[-1]["p.effect"]["execution"]["id"] == "owned-effect"
    expected = "reconcile" if mode == "transactional" else "execute"
    assert (expected, "owned-effect") in control["calls"]
    assert orch.reopen("p", "source", "now settled").changed


def test_only_actual_parallel_closure_is_protected(tmp_path):
    orch, _ = _orch(tmp_path, dependent=False)
    permit = orch.authorize_outcome(I.EFFECT, "p", "effect", execution_id="owned-effect")
    effect = orch.state.get_step("p", "effect")
    assert orch.reopen("p", "source", "independent").changed
    assert orch.state.get_step("p", "effect") == effect
    assert orch.apply_outcome(permit, Done()).kind == "ran"


@pytest.mark.parametrize("operation", ["reopen", "activate", "internal", "force-clear"])
def test_later_phase_effect_blocks_all_invalidation_entry_points(tmp_path, operation):
    orch, control = _orch(tmp_path, later=True, conditional=True)
    permit = orch.authorize_outcome(I.EFFECT, "q", "effect", execution_id="owned-effect")
    kernel = orch._transition_kernel()
    before, permits = deepcopy(orch.state), _permits(orch)
    if operation == "reopen":
        result = orch.reopen("p", "source", "new input")
    elif operation == "activate":
        result = orch.activate_conditional("optional")
    elif operation == "internal":
        result = kernel.invalidate_dependents("p", "source", "new input")
    else:
        result = kernel._invalidate_steps(
            orch.workflow.steps_from_phase("q"), reason="unsafe", force_clear={"q.effect"})
    assert result.kind == "idle" and "q.effect" in result.message
    assert orch.state == before and _permits(orch) == permits
    assert orch.apply_outcome(permit, Done()).kind == "ran"
    assert orch.activate_conditional("optional").kind == "ran"
    assert "optional" in orch.state.active_conditionals and not control["calls"]


@pytest.mark.parametrize("policy", ["status", "always", "never"])
@pytest.mark.parametrize("outcome", [Done("same"), Blocked("changed"), InProgress("waiting")])
def test_refresh_preflights_before_typed_evidence_and_preserves_rejected_permit(tmp_path, policy, outcome):
    from dataclasses import replace

    orch, control = _orch(tmp_path, source_kind="external", policy=policy, status=None)
    permit = orch.authorize_outcome(I.REFRESH, "p", "source")
    orch.context("p", "source", permit=permit)
    _own(orch, control)
    effect_permit = orch.authorize_outcome(I.EFFECT, "p", "effect", execution_id="owned-effect")
    outcome = replace(outcome, updates=(StepData({"new": [2]}), ReleaseVersions({"broker": "2"})))
    before, permits = deepcopy(orch.state), _permits(orch)
    invalidates = policy == "always" or policy == "status" and not isinstance(outcome, Done)
    if invalidates:
        with pytest.raises(ValueError, match="Cannot invalidate active engine-owned"):
            orch.validate_outcome_application(permit, outcome)
        with pytest.raises(ValueError, match="Cannot invalidate active engine-owned"):
            orch.apply_outcome(permit, outcome)
        assert orch.state == before and _permits(orch) == permits
        assert orch.apply_outcome(effect_permit, Done()).kind == "ran"
        orch.apply_outcome(permit, outcome)
        assert orch.state.get_step("p", "effect").status == "pending"
    else:
        orch.apply_outcome(permit, outcome)
        assert orch.state.get_step("p", "effect") == before.get_step("p", "effect")
    assert orch.state.get_step("p", "source").data["new"] == [2]
    assert orch.state.versions["broker"] == "2"


@pytest.mark.parametrize("operation", ["observe", "reserve"])
@pytest.mark.parametrize("policy", ["status", "always", "never"])
def test_known_always_invalidation_is_denied_before_invocation(tmp_path, operation, policy, monkeypatch):
    from orchestrator.commands import step_action

    orch, control = _orch(tmp_path, source_kind="external", policy=policy)
    monkeypatch.setattr(step_action.mocks_mod, "load_mocks", lambda: {})
    before, permits = deepcopy(orch.state), _permits(orch)
    if operation == "reserve":
        result = orch.reserve_execution("p", "source", "worker")
        assert result.changed is (policy != "always")
        if policy == "always":
            assert "Cannot invalidate active engine-owned" in result.message
    else:
        args = SimpleNamespace(phase="p", step="source", release="r", param=[], execution_id=None)
        if policy == "always":
            with pytest.raises(ValueError, match="Cannot invalidate active engine-owned"):
                step_action.prepare_step(args, orch.state, orch)
        else:
            assert step_action.prepare_step(args, orch.state, orch)["kind"] == "done"
            assert control["calls"] == ["build"]
    if policy == "always":
        assert orch.state == before and _permits(orch) == permits and not control["calls"]


@pytest.mark.parametrize("policy", ["status", "always", "never"])
@pytest.mark.parametrize("operation", ["skip", "receipt", "complete", "write"])
def test_reserved_refresh_checks_policy_before_clearing_owner(tmp_path, policy, operation):
    orch, _ = _orch(tmp_path, source_kind="external", policy=policy, execution="sequential")
    source = orch.state.get_step("p", "source")
    source.status = "running"
    source.execution = {"id": "refresh", "owner": "worker", "started_at": NOW.isoformat(),
                        "refresh": True, "previous_status": "done"}
    orch.state.set_step("p", "source", source)
    kernel = orch._transition_kernel()
    before, permits = deepcopy(orch.state), _permits(orch)
    if operation == "skip":
        result = kernel.skip("p", "source", "reviewed", execution_id="refresh")
        rejected = policy != "never"
    elif operation == "receipt":
        result = kernel.settle_execution("p", "source", "refresh", Blocked("changed"))
        rejected = policy != "never"
    elif operation == "complete":
        result = kernel.complete("p", "source", "owner confirmed")
        rejected = policy == "always"
    else:
        result = kernel.authorize_outcome(I.WRITE, "p", "source", execution_id="refresh")
        rejected = policy == "always"
    if rejected:
        assert result.kind == "idle" and "Cannot invalidate active engine-owned" in result.message
        assert orch.state == before and _permits(orch) == permits
    elif operation != "write":
        assert result.changed
        assert orch.state.get_step("p", "effect") == before.get_step("p", "effect")


@pytest.mark.parametrize("policy", ["status", "always", "never"])
def test_refreshed_poll_omission_cannot_invalidate_active_effect_or_consume_permit(tmp_path, policy):
    orch, control = _orch(tmp_path, source_kind="external", policy=policy, status=None)
    source = orch.state.get_step("p", "source")
    source.status = "in_flight"
    source.execution = {"id": "refresh", "owner": "worker", "started_at": NOW.isoformat(),
                        "refresh": True, "previous_status": "done"}
    orch.state.set_step("p", "source", source)
    permit = orch.authorize_outcome(I.POLL, "p", "source", execution_id="refresh")
    _own(orch, control)
    before, permits = deepcopy(orch.state), _permits(orch)
    if policy != "never":
        with pytest.raises(ValueError, match="Cannot invalidate active engine-owned"):
            orch.omit_execution(permit, "not applicable", links=[{"url": "https://example.invalid"}])
        assert orch.state == before and _permits(orch) == permits
    else:
        assert orch.omit_execution(permit, "not applicable").kind == "ran"
        assert orch.state.get_step("p", "effect") == before.get_step("p", "effect")


@pytest.mark.parametrize("status", ["pending", "blocked", "done"])
def test_unowned_effects_still_invalidate_normally(tmp_path, status):
    orch, _ = _orch(tmp_path, status=None)
    orch.state.set_step("p", "effect", StepState(status=status, data={"retained": 1}))
    result = orch.reopen("p", "source", "new input")
    assert result.changed
    effect = orch.state.get_step("p", "effect")
    assert effect.status == "pending" and effect.invalidated_at and not effect.execution
    assert effect.data == {"retained": 1}


@pytest.mark.parametrize("invalidated_at", ["2026-09-13T09:01:00+00:00", NOW.isoformat()])
def test_old_invalidated_owner_is_diagnosed_not_reset(tmp_path, invalidated_at):
    orch, _ = _orch(tmp_path, status="blocked")
    record = orch.state.get_step("p", "effect")
    record.invalidated_at = invalidated_at
    orch.state.set_step("p", "effect", record)
    before = deepcopy(orch.state)
    issues = orch.invariant_violations()
    assert any("already-invalidated engine effect" in issue.message for issue in issues)
    assert not orch.reopen("p", "source", "repair").changed
    with pytest.raises(ValueError, match="invalidated"):
        orch.settle_execution("p", "effect", "owned-effect", Done())
    assert orch.state == before


@pytest.mark.parametrize("operation", ["reopen", "activate", "retrigger"])
def test_commands_report_protected_effect_without_saving_success(tmp_path, operation, monkeypatch, capsys):
    from orchestrator.commands import release

    orch, _ = _orch(tmp_path, later=True, conditional=True)
    monkeypatch.setattr(release.C, "load_orch", lambda *args: (orch.state, orch))
    monkeypatch.setattr(release.C, "save_state", lambda *args: pytest.fail("rejection must not save"))
    args = SimpleNamespace(runs_root=str(tmp_path), release="r", config="unused",
                           phase="optional" if operation == "activate" else "p", step="source", reason="new input")
    if operation == "retrigger":
        original = orch.reopen
        monkeypatch.setattr(orch, "reopen", lambda *args: original("p", "source", "new input"))
    command = {"reopen": release.cmd_reopen, "activate": release.cmd_activate,
               "retrigger": release.cmd_rc_retriggered}[operation]
    before = deepcopy(orch.state)
    assert command(args) == 1
    assert "Cannot invalidate active engine-owned" in capsys.readouterr().out
    assert orch.state == before


def test_reviewed_write_finalization_preflights_before_evidence(tmp_path):
    from orchestrator import write_review as W

    orch, _ = _orch(tmp_path, source_kind="external", execution="sequential",
                    write_command="distribute-tests")
    plan = W.WritePlan("distribute-tests", {}, (
        W.WriteOperation("assign_test_case", {"case_id": "1"},
                         {"assignee": "alice@example.com"}, {"revision": 2}),
    ))
    review = {"hash": W.review_hash(orch, "p", "source", plan), "approved_by": "test-reviewer"}
    kernel = orch._transition_kernel()
    assert kernel.reserve("p", "source", "worker", write_review=review).changed
    execution_id = orch.step_execution("p", "source")["id"]
    kernel.begin_reviewed_write("p", "source", execution_id)
    assert orch.state.get_step("p", "source").status == "in_flight"
    before = deepcopy(orch.state)
    with pytest.raises(ValueError, match="Cannot invalidate active engine-owned"):
        orch.settle_execution(
            "p", "source", execution_id,
            Blocked("changed", updates=(StepData({"unsafe": True}),)))
    assert orch.state == before
