"""Exact gate ownership survives every provider/local persistence boundary."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import cli_common as C
from orchestrator.approvals import ApprovalRequest
from orchestrator.authority import WriteOperation
from orchestrator.commands import gate_approve, release
from orchestrator.outcomes import Done, NeedsHuman
from orchestrator.services import EffectServices
from orchestrator.state import ReleaseState, StepState
from tests._context import fresh_orchestrator as Orchestrator


@dataclass(frozen=True)
class ApprovalParameters:
    comment: str = ""


def _setup(tmp_path, monkeypatch, external=True, fail_after=False, predecessor=False):
    calls = []
    control = {"approval_id": "approval-1", "confirmed": False, "fail_save": None,
               "saves": 0, "submit_error": None, "suspend": None, "skip_provider": False,
               "submit_twice": False, "during_submit": None}
    state = ReleaseState(release_id="r", timezone="UTC")
    path = tmp_path / "r" / "release-state.json"
    gate = {"id": "approve", "name": "Approval", "kind": "approval_gate"}
    modules = {}
    if external:
        gate["approval_command"] = "approve-orchestrator-gate"

        def prepare(context):
            return ApprovalRequest("https://dev.azure.com/example", "project", 12,
                                   "Publish", control["approval_id"], context.parameters.comment)

        def reconcile(context):
            calls.append("reconcile")
            assert context.approval.submit is None
            assert context.approval.request.approval_id == "approval-1"
            return control["confirmed"], "exact approval observed"

        def submit_hook(context):
            if control["skip_provider"]:
                return True, "pretend"
            result = context.approval.submit()
            return context.approval.submit() if control["submit_twice"] else result

        modules["approve"] = SimpleNamespace(
            ID="approve", KIND="gate", APPROVAL_COMMAND="approve-orchestrator-gate",
            WRITES=(WriteOperation.SUBMIT_PIPELINE_APPROVAL,),
            PARAMETERS={"prepare_approval": ApprovalParameters},
            build=lambda _: NeedsHuman("Review"), prepare_approval=prepare,
            submit_approval=submit_hook,
            reconcile_approval=reconcile,
        )

    def after(_context):
        calls.append("after")
        saved = ReleaseState.load(str(path))
        assert saved.get_step("phase", "approve").status == "done"
        assert saved.gate_decisions[-1]["decision"] == "approved"
        if fail_after:
            raise RuntimeError("downstream failure")
        return Done("after complete")

    modules["after"] = SimpleNamespace(
        ID="after", KIND="agent", EFFECT_MODE="read_only", build=after)
    previous = []
    if predecessor:
        previous = [{"id": "before", "name": "Before", "kind": "auto", "effect_mode": "read_only"}]
        modules["before"] = SimpleNamespace(
            ID="before", KIND="agent", EFFECT_MODE="read_only", build=lambda _: Done())
        state.set_step("phase", "before", StepState(status="done"))
    config = tmp_path / "phases.yaml"
    config.write_text(yaml.safe_dump({"phases": [{
        "id": "phase", "name": "Phase", "steps": previous + [
            gate, {"id": "after", "name": "After", "kind": "auto", "effect_mode": "read_only"}],
    }]}), encoding="utf-8")

    def submit(org, project, approval_id, comment):
        saved = ReleaseState.load(str(path)).get_step("phase", "approve")
        assert saved.status == "in_flight"
        assert saved.execution["approval"]["submission_started_at"]
        assert saved.execution["approval"]["request"]["approval_id"] == approval_id == "approval-1"
        assert comment == "reviewed"
        calls.append("submit")
        if control["during_submit"]:
            control["during_submit"]()
        if control["suspend"]:
            getattr(control["orch"], control["suspend"])("incident")
        if control["submit_error"]:
            raise RuntimeError(control["submit_error"])
        control["confirmed"] = True
        return True, "exact provider confirmed"

    def make_orch(st):
        orch = Orchestrator(
            str(config), st, mocks={}, services=SimpleNamespace(),
            effect_services=lambda *_: EffectServices(submit_pipeline_approval=submit),
            now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
            handler_resolver=lambda _phase, step: modules.get(step))

        def checkpoint():
            control["saves"] += 1
            if control["saves"] == control["fail_save"]:
                raise OSError("disk full")
            st.save(str(path))

        st._checkpoint = checkpoint
        control["orch"] = orch
        return orch

    orch = make_orch(state)
    state.save(str(path))
    monkeypatch.setattr(C, "load_orch", lambda *_: (control["orch"].state, control["orch"]))
    args = SimpleNamespace(
        release="r", runs_root=str(tmp_path), config=str(config), as_of=None,
        comment="reviewed", approved_by="reviewer", executor="session",
        review_hash=orch.preview_gate_approval("phase", "approve", comment="reviewed")["review_hash"] if external else None,
        execution_id=None, reserve=False, preview=False, phase=None, step=None)
    control["reload"] = lambda: make_orch(ReleaseState.load(str(path)))
    command = gate_approve.cmd_approve_orchestrator_gate if external else release.cmd_approve
    return command, args, orch, calls, path, control


@pytest.mark.parametrize("external", [False, True])
def test_approval_is_saved_before_following_work(tmp_path, monkeypatch, external):
    command, args, _, calls, path, _ = _setup(tmp_path, monkeypatch, external)
    assert command(args) == 0
    assert calls == (["submit", "after"] if external else ["after"])
    saved = ReleaseState.load(str(path))
    assert saved.get_step("phase", "after").status == "done"
    if external:
        record = saved.get_step("phase", "approve")
        assert record.execution is None
        assert record.data["last_approval"]["receipt"]["approval_id"] == "approval-1"


@pytest.mark.parametrize("external", [False, True])
def test_following_failure_does_not_lose_approved_gate(tmp_path, monkeypatch, external):
    command, args, _, _, path, _ = _setup(tmp_path, monkeypatch, external, fail_after=True)
    with pytest.raises(RuntimeError, match="downstream failure"):
        command(args)
    saved = ReleaseState.load(str(path))
    assert saved.get_step("phase", "approve").status == "done"
    assert saved.gate_decisions[-1]["decision"] == "approved"
    assert saved.get_step("phase", "after").status == "pending"


@pytest.mark.parametrize("boundary", [1, 2, 3, 4])
def test_checkpoint_failure_retains_last_durable_boundary(tmp_path, monkeypatch, boundary):
    command, args, orch, calls, path, control = _setup(tmp_path, monkeypatch)
    control["fail_save"] = boundary
    with pytest.raises(OSError, match="disk full"):
        command(args)
    saved = ReleaseState.load(str(path))
    record = saved.get_step("phase", "approve")
    assert not saved.gate_decisions
    assert calls == ([] if boundary <= 2 else ["submit"])
    assert orch.state.steps == saved.steps
    if boundary == 1:
        assert record.execution is None
    else:
        approval = record.execution["approval"]
        assert (approval["submission_started_at"] is None) == (boundary == 2)
        assert (approval["receipt"] is not None) == (boundary == 4)
        args.execution_id = record.execution["id"]
        control["fail_save"] = None
        control["reload"]()
        assert command(args) == 0
        assert calls.count("submit") == 1
        assert calls.count("reconcile") == (1 if boundary == 3 else 0)


def test_interrupted_attempt_never_resubmits_even_if_discovery_changes(tmp_path, monkeypatch):
    command, args, _, calls, path, control = _setup(tmp_path, monkeypatch)
    control["submit_error"] = "connection lost"
    with pytest.raises(RuntimeError, match="connection lost"):
        command(args)
    saved = ReleaseState.load(str(path))
    args.execution_id = saved.get_step("phase", "approve").execution["id"]
    control.update(approval_id="replacement-approval", submit_error=None)
    control["reload"]()
    assert command(args) == 1
    assert calls == ["submit", "reconcile"]
    assert not ReleaseState.load(str(path)).gate_decisions
    control["confirmed"] = True
    assert command(args) == 0
    assert calls == ["submit", "reconcile", "reconcile", "after"]


@pytest.mark.parametrize("suspend", ["halt", "cancel"])
def test_late_receipt_survives_suspension_without_completion(tmp_path, monkeypatch, suspend):
    command, args, orch, calls, path, control = _setup(tmp_path, monkeypatch)
    control["suspend"] = suspend
    assert command(args) == 0
    saved = ReleaseState.load(str(path))
    record = saved.get_step("phase", "approve")
    assert record.execution["approval"]["receipt"]["status"] == "approved"
    assert not saved.gate_decisions and calls == ["submit"]
    args.execution_id = record.execution["id"]
    assert command(args) == 0
    assert calls == ["submit"]
    getattr(orch, "resume" if suspend == "halt" else "reactivate")("reviewed")
    assert command(args) == 0
    assert calls == ["submit", "after"]


def test_stale_hash_and_wrong_execution_cannot_mutate_or_submit(tmp_path, monkeypatch):
    command, args, orch, calls, path, control = _setup(tmp_path, monkeypatch)
    before = path.read_bytes()
    control["approval_id"] = "replacement"
    assert command(args) == 1
    assert path.read_bytes() == before and not calls
    control["approval_id"] = "approval-1"
    args.reserve = True
    assert command(args) == 0
    before = path.read_bytes()
    args.reserve, args.execution_id = False, "wrong-owner"
    assert command(args) == 1
    assert path.read_bytes() == before and not calls
    assert orch.state.get_step("phase", "approve").execution


def test_owned_gate_blocks_reopen_direct_completion_and_adoption(tmp_path, monkeypatch):
    from orchestrator.revision import adoption_preview

    command, args, orch, calls, _, _ = _setup(tmp_path, monkeypatch)
    assert orch.approve_gate().kind == "idle"
    args.reserve = True
    assert command(args) == 0
    before = deepcopy(orch.state.steps)
    assert not orch.reopen("phase", "approve", "try again").changed
    assert orch.complete_step("phase", "approve").kind == "idle"
    assert orch.skip_step("phase", "approve", "try again").kind == "idle"
    assert any("owned execution" in item for item in adoption_preview(orch)["blockers"])
    assert orch.state.steps == before and not calls


def test_success_without_provider_call_cannot_create_receipt(tmp_path, monkeypatch):
    command, args, _, calls, path, control = _setup(tmp_path, monkeypatch)
    control["skip_provider"] = True
    assert command(args) == 1
    record = ReleaseState.load(str(path)).get_step("phase", "approve")
    assert record.execution["approval"]["receipt"] is None
    assert record.execution["approval"]["submission_started_at"]
    assert not calls


def test_suspended_start_and_gate_input_mocks_cannot_authorize_work(tmp_path, monkeypatch):
    command, args, orch, calls, path, _ = _setup(tmp_path, monkeypatch)
    before = path.read_bytes()
    orch.halt("incident")
    assert command(args) == 1 and not calls
    orch.resume()
    orch.mocks = {"phase.approve": {"submit": "skip"}}
    assert command(args) == 1 and not calls
    assert path.read_bytes() == before


def test_single_use_submission_and_stale_results_fail_closed(tmp_path, monkeypatch):
    command, args, orch, calls, path, control = _setup(tmp_path, monkeypatch)
    control["submit_twice"] = True
    assert command(args) == 1
    assert calls == ["submit"]
    saved = ReleaseState.load(str(path)).get_step("phase", "approve")
    assert saved.execution["approval"]["receipt"] is None
    assert saved.execution["approval"]["submission_started_at"]
    args.execution_id = saved.execution["id"]
    assert command(args) == 0
    assert calls == ["submit", "reconcile", "after"]


def test_result_cannot_settle_replaced_execution(tmp_path, monkeypatch):
    command, args, orch, calls, path, control = _setup(tmp_path, monkeypatch)

    def replace_owner():
        record = orch.state.get_step("phase", "approve")
        record.execution["id"] = "replacement-owner"
        orch.state.set_step("phase", "approve", record)

    control["during_submit"] = replace_owner
    assert command(args) == 1
    assert calls == ["submit"] and not orch.state.gate_decisions
    saved = ReleaseState.load(str(path)).get_step("phase", "approve")
    assert saved.execution["approval"]["receipt"] is None
    assert orch.state.get_step("phase", "approve").execution["id"] == "replacement-owner"


def test_upstream_reopen_cannot_invalidate_owned_gate(tmp_path, monkeypatch):
    command, args, orch, _, _, _ = _setup(tmp_path, monkeypatch, predecessor=True)
    args.reserve = True
    assert command(args) == 0
    before = deepcopy(orch.state.steps)
    assert not orch.reopen("phase", "before", "upstream correction").changed
    assert orch.state.steps == before


def test_closed_receipt_survives_reopen_and_cannot_be_annotated(tmp_path, monkeypatch):
    command, args, orch, _, _, _ = _setup(tmp_path, monkeypatch)
    assert command(args) == 0
    receipt = deepcopy(orch.state.get_step("phase", "approve").data["last_approval"])
    assert orch.reopen("phase", "approve", "review again").changed
    assert orch.state.get_step("phase", "approve").data["last_approval"] == receipt
    assert not orch.annotate_step("phase", "approve", data={"last_approval": {}}).changed


def test_malformed_approval_is_inspectable_but_cannot_execute(tmp_path, monkeypatch):
    command, args, orch, _, _, _ = _setup(tmp_path, monkeypatch)
    args.reserve = True
    assert command(args) == 0
    before = deepcopy(orch.state.steps)
    for field, value in [
        ("request_hash", "bad"), ("approved_by", ""), ("receipt", {"status": "approved"}),
        ("request", {"approval_id": "invented"}), ("submission_started_at", "not-a-date"),
    ]:
        orch.state.steps = deepcopy(before)
        orch.state.steps["phase.approve"]["execution"]["approval"][field] = value
        report = orch.status_report()
        assert report["status"] == "blocked" and report["invariant_violations"]
        assert orch.step_once().kind == "blocked"
    orch.state.steps = before


def test_production_checkpoint_requires_current_release_lock(tmp_path, monkeypatch):
    _, args, original, calls, path, _ = _setup(tmp_path, monkeypatch)

    def loaded():
        return Orchestrator(
            args.config, C.load_state(args.runs_root, args.release), mocks={},
            services=original._services, effect_services=original._effect_services,
            handler_resolver=original._handler_resolver, now=original.now_local)

    before = path.read_bytes()
    orch = loaded()
    with pytest.raises(RuntimeError, match="current release state lock"):
        orch.execute_gate_approval("phase", "approve", comment=args.comment,
                                  review_hash=args.review_hash, approved_by=args.approved_by)
    assert not calls and path.read_bytes() == before
    with C.state_lock(args.runs_root, args.release):
        result = loaded().execute_gate_approval(
            "phase", "approve", comment=args.comment, review_hash=args.review_hash, approved_by=args.approved_by)
    assert result["status"] == "approved" and calls == ["submit"]
