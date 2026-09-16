"""Public lifecycle ownership, corruption diagnostics and adapter boundaries."""
import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import delivery as D, effects, render
from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.outcomes import Done
from orchestrator.evidence import RetryDecision
from orchestrator.state import ReleaseState, StepState
from orchestrator.transitions import TransitionIntent


@dataclass(frozen=True)
class RetryParameters:
    reason: str


def _public(tmp_path, *, mode=None):
    control = {"verify": lambda _: RetryDecision(True, "provider confirmed absence"), "calls": []}

    def verify(context):
        control["calls"].append("verify")
        return control["verify"](context)

    step = {"id": "job", "name": "Job", "kind": "external"}
    module = SimpleNamespace(ID="job", KIND="scout", build=lambda _: Done())
    if mode:
        recovery = "frozen" if mode == "transactional" else "match_current"
        step.update(kind="auto", effect_mode=mode, effect_recovery=recovery)
        if mode == "transactional":
            step["effect_retry"] = True
        module = SimpleNamespace(
            ID="job", KIND="agent", EFFECT_MODE=mode, EFFECT_RECOVERY=recovery,
            build=lambda _: Done(), prepare_effect=lambda _: {"target": "resource"},
            execute=lambda *_: Done(), reconcile=lambda *_: Done(), authorize_retry=verify,
            PARAMETERS={"authorize_retry": RetryParameters} if mode == "transactional" else {},
        )
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump({"phases": [{
        "id": "p", "name": "P", "execution": "parallel", "steps": [
            step, {"id": "human", "name": "Human", "kind": "human_action"},
            {"id": "gate", "name": "Gate", "kind": "approval_gate"},
        ],
    }]}), encoding="utf-8")
    state = ReleaseState(release_id="r", timezone="UTC")
    orch = Orchestrator(str(path), state, mocks={},
                        now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
                        handler_resolver=lambda _p, sid: module if sid == "job" else None)
    if mode:
        value = {"target": "resource"}
        state.set_step("p", "job", StepState(status="blocked", execution={
            "id": "owned", "owner": "engine", "started_at": "2026-09-12T00:00:00Z",
            "effect_mode": mode, "effect_recovery": recovery,
            "effect_input": value, "operation_key": effects.input_key("job", value),
        }))
    return orch, control


def _notice(orch, logical="notice"):
    item = D.descriptor(
        orch.state, logical, {"kind": "step", "phase": "p", "step": "job",
                             "generation": orch.state.get_step("p", "job").invalidated_at or "initial"},
        "workiq_send_email", {"to": ["owner@example.com"], "body": "notice"},
        {"kind": "step", "record_as": "job"})
    D.offer(orch, item)
    return item


@pytest.mark.parametrize("raw", [
    None, [], {"unknown": True}, {"status": []}, {"data": []}, {"links": {}}, {"links": [None]},
    {"status": "running", "execution": []}, {"status": "running", "execution": "owner"},
    {"status": "running", "execution": {}},
    {"status": "done", "execution": {"id": "x", "owner": "w", "started_at": "2026-09-12T00:00:00Z"}},
    {"status": "running", "execution": {"id": [], "owner": "w", "started_at": "now"}},
])
def test_malformed_snapshot_diagnostics_and_mutations_are_safe(tmp_path, raw):
    orch, _ = _public(tmp_path)
    orch.state.steps["p.job"] = raw
    before = deepcopy(orch.state)
    report = orch.status_report()
    assert report["status"] == "blocked" and report["invariant_violations"]
    assert "State integrity requires owner review" in render.status_view(report)
    assert not orch.reserve_execution("p", "job", "worker").changed
    assert not orch.annotate_step("p", "job", data={"new": True}).changed
    assert not orch.reopen("p", "job", "reviewed").changed
    assert orch.step_once().kind == "blocked"
    assert orch.state == before


@pytest.mark.parametrize("decisions", [None, {}, [None], [[]], [{"step": []}],
                                     [{"step": "p.gate", "decision": []}],
                                     [{"step": "p.job", "decision": "approved"}]])
def test_malformed_or_misclassified_gates_do_not_crash_status_or_mutate(tmp_path, decisions):
    orch, _ = _public(tmp_path)
    orch.state.gate_decisions = decisions
    before = deepcopy(orch.state)
    assert orch.status_report()["status"] == "blocked"
    assert not orch.reserve_execution("p", "job", "worker").changed
    assert orch.approve_gate().kind == "idle"
    assert orch.state == before


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("owner", "outsider"), ("started_at", "invalid"),
    ("effect_mode", None), ("effect_recovery", "match_current"),
    ("operation_key", "wrong"), ("effect_input", []), ("refresh", "true"),
])
def test_invalid_effect_metadata_rejects_retry_before_provider_read(tmp_path, field, value):
    orch, control = _public(tmp_path, mode="transactional")
    record = orch.state.get_step("p", "job")
    record.execution[field] = value
    orch.state.set_step("p", "job", record)
    before = deepcopy(orch.state)
    result = orch.retry_effect("p", "job", "owned", "reviewed", confirm_absent=True)
    assert not result.changed and result.message
    assert not control["calls"]
    assert orch.state == before
    assert orch.status_report()["invariant_violations"]


@pytest.mark.parametrize("change", ["id", "invalidated_at", "status"])
def test_retry_verification_cannot_clear_a_changed_generation(tmp_path, change):
    orch, control = _public(tmp_path, mode="transactional")

    def verify(context):
        state = orch.state
        with pytest.raises(TypeError):
            context.evidence.step("p", "job").execution["effect_input"]["target"] = "detached"
        record = state.get_step("p", "job")
        assert record.execution["effect_input"]["target"] == "resource"
        if change == "id":
            record.execution["id"] = "new-owner"
        elif change == "invalidated_at":
            record.invalidated_at = "2099-09-12T00:00:00Z"
        else:
            record.status = "in_flight"
        state.set_step("p", "job", record)
        return RetryDecision(True, "old resource absent")

    control["verify"] = verify
    result = orch.retry_effect("p", "job", "owned", "reviewed", confirm_absent=True)
    assert not result.changed
    assert orch.state.get_step("p", "job").execution


@pytest.mark.parametrize("result", [False, ("yes", "bad"), (True, None), (True,), None])
def test_malformed_verification_preserves_owner_and_propagates(tmp_path, result):
    orch, control = _public(tmp_path, mode="transactional")
    control["verify"] = lambda *_: result
    before = deepcopy(orch.state)
    with pytest.raises(TypeError, match="authorize_retry: invalid return"):
        orch.retry_effect("p", "job", "owned", "reviewed", confirm_absent=True)
    assert orch.state == before


def test_provider_retry_errors_propagate_and_unrelated_corruption_does_not_block_recovery(tmp_path):
    orch, control = _public(tmp_path, mode="transactional")

    def fail(*_):
        raise RuntimeError("provider unavailable")

    control["verify"] = fail
    before = deepcopy(orch.state)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        orch.retry_effect("p", "job", "owned", "reviewed", confirm_absent=True)
    assert orch.state == before
    control["verify"] = lambda _: RetryDecision(False, "could still exist")
    assert not orch.retry_effect("p", "job", "owned", "reviewed", confirm_absent=True).changed
    assert orch.state == before
    control["verify"] = lambda _: RetryDecision(True, "absent")
    orch.state.gate_decisions = [None]
    assert orch.retry_effect("p", "job", "owned", "reviewed", confirm_absent=True).changed
    assert orch.status_report()["status"] == "blocked"


@pytest.mark.parametrize("mode", ["transactional", "idempotent"])
def test_recovery_is_exact_and_can_release_owned_work_during_suspension(tmp_path, mode):
    orch, control = _public(tmp_path, mode=mode)
    orch.halt("incident")
    operation = orch.retry_effect if mode == "transactional" else orch.supersede_effect
    confirmation = {"confirm_absent": True} if mode == "transactional" else {"confirm_idempotent": True}
    before = deepcopy(orch.state)
    assert not operation("p", "job", "wrong", "reviewed", **confirmation).changed
    assert not operation("p", "job", "owned", "reviewed").changed
    assert orch.state == before and not control["calls"]
    assert operation("p", "job", "owned", "reviewed", **confirmation).changed
    assert orch.state.get_step("p", "job").execution is None
    assert orch.scheduling().suspension and not orch.scheduling().runnable
    before = deepcopy(orch.state)
    assert not operation("p", "job", "owned", "duplicate", **confirmation).changed
    assert orch.state == before


def test_annotation_is_detached_and_cannot_rewrite_ownership(tmp_path):
    orch, _ = _public(tmp_path)
    assert orch.reserve_execution("p", "job", "worker").changed
    owner = orch.step_execution("p", "job")
    data, links = {"nested": []}, [{"name": "evidence", "url": "https://example.invalid"}]
    assert orch.annotate_step("p", "job", data=data, links=links, note="receipt").changed
    data["nested"].append("changed")
    links[0]["url"] = "changed"
    record = orch.state.get_step("p", "job")
    assert record.status == "running" and record.execution == owner
    assert record.data["nested"] == [] and record.links[0]["url"] != "changed"
    before = deepcopy(orch.state)
    assert not orch.annotate_step("p", "job", data={"bad": 1}, links={}).changed
    assert not orch.annotate_step("p", "missing", data=data).changed
    with pytest.raises(TypeError):
        orch.annotate_step("p", "job", execution={"id": "stolen"})
    assert orch.state == before


@pytest.mark.parametrize("status", ["claimed", "sent", "uncertain"])
def test_notification_release_never_infers_not_sent(tmp_path, status):
    orch, _ = _public(tmp_path)
    item = _notice(orch)
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    if status != "claimed":
        D.result(orch, item["id"], claim["execution_id"], status, "transport evidence")
    before = deepcopy(orch.state)
    assert not orch.release_notification_step(item["id"], claim["execution_id"]).changed
    assert orch.state == before


def test_late_not_sent_does_not_release_a_new_owner(tmp_path):
    orch, _ = _public(tmp_path)
    old = _notice(orch)
    claim = D.claim(orch, old["id"], old["hash"], "old-worker")
    assert orch.reopen("p", "job", "old runner stopped").changed
    new = _notice(orch, "new-generation")
    D.claim(orch, new["id"], new["hash"], "new-worker")
    owner = orch.state.get_step("p", "job")
    assert D.result(orch, old["id"], claim["execution_id"], "not_sent", "verified absent")
    assert orch.state.get_step("p", "job") == owner
    assert orch.state.notification_deliveries[old["id"]]["status"] == "not_sent"


def test_proven_not_sent_releases_only_its_owner_while_halted(tmp_path):
    orch, _ = _public(tmp_path)
    item = _notice(orch)
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    orch.halt("incident")
    assert D.result(orch, item["id"], claim["execution_id"], "not_sent", "verified no send")
    assert orch.state.get_step("p", "job").execution is None
    assert orch.state.get_step("p", "job").status == "pending"
    before = deepcopy(orch.state)
    assert not orch.claim_notification_step(item["id"], item["hash"], "other").changed
    assert orch.state == before and not orch.scheduling().runnable


@pytest.mark.parametrize("mutation", ["hash", "scope", "completion", "attempt"])
def test_rejected_notification_claim_is_atomic(tmp_path, mutation):
    orch, _ = _public(tmp_path)
    item = _notice(orch)
    ledger = orch.state.notification_deliveries[item["id"]]
    if mutation == "attempt":
        ledger["attempts"] = [None]
    elif mutation == "hash":
        ledger["descriptor"]["hash"] = "wrong"
    else:
        ledger["descriptor"][mutation] = []
    before = deepcopy(orch.state)
    assert not orch.claim_notification_step(item["id"], item["hash"], "worker").changed
    assert orch.state == before


@pytest.mark.parametrize("kind", ["claim", "retry"])
def test_failed_persistence_never_emits_send_permission_or_durably_clears_an_owner(
        tmp_path, monkeypatch, capsys, kind):
    import json
    from orchestrator import cli_common as C
    from orchestrator.commands import delivery_cmd, effect

    orch, _ = _public(tmp_path, mode="transactional" if kind == "retry" else None)
    item = _notice(orch) if kind == "claim" else None
    path = tmp_path / "release-state.json"
    orch.state.save(str(path))
    disk_before = path.read_bytes()
    monkeypatch.setattr(C, "load_orch", lambda *_: (orch.state, orch))

    def fail_save(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr(C, "save_state", fail_save)
    args = SimpleNamespace(runs_root=str(tmp_path), release="r", config="unused", as_of=None)
    if kind == "claim":
        args.operation, args.id, args.hash, args.executor = "claim", item["id"], item["hash"], "worker"
        assert delivery_cmd.cmd_notification(args) == 1
        assert json.loads(capsys.readouterr().out)["permission_to_send"] is False
    else:
        args.phase, args.step, args.execution_id = "p", "job", "owned"
        args.reason, args.confirm_absent = "verified absence", True
        with pytest.raises(OSError, match="disk unavailable"):
            effect.cmd_retry_effect(args)
        assert not capsys.readouterr().out
    assert path.read_bytes() == disk_before
    persisted = ReleaseState.load(str(path))
    assert (persisted.get_step("p", "job").execution or {}).get("id") == ("owned" if kind == "retry" else None)


def test_notification_completion_cannot_name_a_lifecycle_field(tmp_path):
    orch, _ = _public(tmp_path)
    item = _notice(orch)
    ledger = orch.state.notification_deliveries[item["id"]]
    ledger["descriptor"]["completion"] = {"release_field": "cancellation", "date": "unsafe"}
    ledger["descriptor"]["hash"] = D.fingerprint({
        k: v for k, v in ledger["descriptor"].items() if k != "hash"})
    before = deepcopy(orch.state)
    assert not orch.claim_notification_step(item["id"], ledger["descriptor"]["hash"], "worker").changed
    assert orch.state == before


def test_last_step_notification_can_finalize_its_confirmed_checkpoint(tmp_path):
    from orchestrator.commands.delivery_cmd import finish

    orch, _ = _public(tmp_path)
    assert orch.complete_step("p", "human").kind == "ran"
    assert orch.approve_gate().kind == "ran"
    item = _notice(orch)
    item["completion"]["checkpoint"] = "last-notice"
    item["hash"] = D.fingerprint({k: v for k, v in item.items() if k != "hash"})
    D.offer(orch, item)
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    D.result(orch, item["id"], claim["execution_id"], "sent", "provider receipt")
    assert finish(orch, item["id"])
    assert orch.scheduling().status == "complete"
    assert "last-notice" in orch.state.escalation_checkpoints
    before = deepcopy(orch.state)
    assert not orch.record_notification_evidence(item["id"]).changed
    assert orch.state == before


def test_duplicate_execution_identity_cannot_be_recovered(tmp_path):
    orch, control = _public(tmp_path, mode="transactional")
    orch.state.steps["removed.old_job"] = deepcopy(orch.state.steps["p.job"])
    before = deepcopy(orch.state)
    assert not orch.retry_effect("p", "job", "owned", "reviewed", confirm_absent=True).changed
    assert not control["calls"] and orch.state == before
    assert any(v.code == "duplicate_execution" for v in orch.invariant_violations())


@pytest.mark.parametrize("operation", ["claim", "release", "evidence"])
def test_public_notification_operations_reject_a_malformed_ledger(tmp_path, operation):
    orch, _ = _public(tmp_path)
    orch.state.notification_deliveries = []
    before = deepcopy(orch.state)
    result = {
        "claim": lambda: orch.claim_notification_step("notice", "hash", "worker"),
        "release": lambda: orch.release_notification_step("notice", "execution"),
        "evidence": lambda: orch.record_notification_evidence("notice"),
    }[operation]()
    assert not result.changed and result.message
    assert orch.state == before


def test_notification_evidence_rejects_corruption_without_finalizing_or_crashing(tmp_path):
    from orchestrator.commands.delivery_cmd import finish

    orch, _ = _public(tmp_path)
    item = _notice(orch)
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    D.result(orch, item["id"], claim["execution_id"], "sent", "provider receipt")
    orch.state.steps["p.job"]["data"] = []
    before = deepcopy(orch.state)
    assert not orch.record_notification_evidence(item["id"]).changed
    with pytest.raises(ValueError, match="invalid release state"):
        finish(orch, item["id"])
    assert orch.state == before


@pytest.mark.parametrize("field,value", [
    ("last_notified_date", []), ("escalation_checkpoints", []),
])
def test_notification_evidence_validates_all_destinations_before_writing(tmp_path, field, value):
    orch, _ = _public(tmp_path)
    item = D.descriptor(
        orch.state, "evidence", {"kind": "release"}, "workiq_send_email",
        {"to": ["owner@example.com"], "body": "notice"},
        {"release_field": "last_notified_date", "date": "2026-09-12", "checkpoint": "notice"})
    D.offer(orch, item)
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    D.result(orch, item["id"], claim["execution_id"], "sent", "provider receipt")
    setattr(orch.state, field, value)
    before = deepcopy(orch.state)
    assert not orch.record_notification_evidence(item["id"]).changed
    assert orch.state == before


def test_release_lifecycle_noops_and_reopen_affected_steps(tmp_path):
    orch, _ = _public(tmp_path)
    assert not orch.cancel("").changed
    assert orch.cancel("reviewed").changed
    before = deepcopy(orch.state)
    assert not orch.cancel("again").changed and not orch.reactivate("").changed
    assert orch.state == before
    assert orch.reactivate("approved").changed
    assert not orch.reactivate("again").changed
    permit = orch.authorize_outcome(TransitionIntent.RECORD, "p", "job")
    orch.apply_outcome(permit, Done())
    result = orch.reopen("p", "job", "new inputs")
    assert result.changed and "p.job" in result.affected


def _assert_lifecycle_adapter_source(source, path):
    from orchestrator.engine import Orchestrator as Engine

    fields = {"status", "execution", "halt", "cancellation", "gate_decisions", "steps"}
    private_engine_methods = {
        name for cls in Engine.__mro__ for name, value in vars(cls).items()
        if name.startswith("_") and not name.startswith("__") and callable(value)
    }
    for node in ast.walk(ast.parse(source)):
        location = (path, getattr(node, "lineno", 0))
        if isinstance(node, ast.ImportFrom):
            assert all(alias.name != "TransitionKernel" for alias in node.names), location
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in private_engine_methods, location
            assert node.func.attr not in {"_transition_kernel", "TransitionKernel", "set_step", "record_outcome"}, location
            if node.func.attr in {"append", "extend", "insert", "remove", "pop", "clear", "update", "setdefault"}:
                assert not any(isinstance(value, ast.Attribute) and value.attr in fields
                               for value in ast.walk(node.func.value)), location
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr":
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                assert node.args[1].value not in fields, location
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            assert node.attr not in fields, location
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            assert not any(isinstance(value, ast.Attribute) and value.attr in fields
                           for value in ast.walk(node.value)), location


@pytest.mark.parametrize("source", [
    "orch._transition_kernel().cancel('reason')",
    "orch._projection().current_hold()",
    "_orch._workflow_definition().step('phase', 'step')",
    "Orchestrator(config, state)._projection().release_status()",
    "orch._step_complete('phase', 'step')",
    "from orchestrator.transitions import TransitionKernel as Kernel",
    "state.set_step('phase', 'step', record)",
    "record.status = 'done'",
    "setattr(state, 'cancellation', None)",
    "state.steps['phase.step']['status'] = 'done'",
    "record.execution['id'] = 'replacement'",
    "state.gate_decisions.append({'decision': 'approved'})",
    "state.steps.clear()",
])
def test_lifecycle_structural_guard_detects_direct_mutation(source):
    with pytest.raises(AssertionError):
        _assert_lifecycle_adapter_source(source, "adapter")


@pytest.mark.parametrize("source", [
    "orch.scheduling().focus_hold",
    "orch.handler('phase', 'step').definition",
    "super().__init__('provider rejected the operation')",
])
def test_lifecycle_structural_guard_allows_public_queries_and_standard_protocols(source):
    _assert_lifecycle_adapter_source(source, "adapter")


def test_adapters_cannot_bypass_the_lifecycle_facade():
    root = Path(__file__).resolve().parents[1] / "orchestrator"
    paths = [root / "delivery.py", root / "automations.py", *(root / "commands").glob("*.py")]
    for path in paths:
        _assert_lifecycle_adapter_source(path.read_text(encoding="utf-8"), path)
