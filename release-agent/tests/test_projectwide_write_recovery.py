"""Every checked writer uses one plan, one durable attempt and no reserve-only mode."""
from argparse import Namespace
from dataclasses import asdict
from importlib import import_module
from pathlib import Path

import pytest
import yaml

from orchestrator import cli_common as C, revision, write_review as W
from orchestrator.cli import build_parser
from orchestrator.command_catalog import COMMAND_MODULES
from orchestrator.engine import Orchestrator
from orchestrator.outcomes import Blocked
from orchestrator.render import step_detail
from orchestrator.state import ReleaseState
from orchestrator.step_context import thaw
from orchestrator.workflow import WorkflowDefinition


WORKFLOW = WorkflowDefinition.compile(yaml.safe_load(Path(C.DEFAULT_CONFIG).read_text(encoding="utf-8")))
WRITERS = tuple(step for step in WORKFLOW.step_by_key.values() if step.write_command)


@pytest.fixture(params=WRITERS, ids=lambda step: step.write_command)
def writer(request, tmp_path):
    definition = request.param
    raw = thaw(definition.raw)
    raw.pop("depends_on", None)
    config = tmp_path / "phases.yaml"
    config.write_text(yaml.safe_dump({"phases": [{
        "id": definition.phase_id, "name": "Writer", "steps": [raw],
    }]}), encoding="utf-8")
    state = ReleaseState(release_id="new-version", timezone="UTC", owner_email="owner@example.test")
    orch = Orchestrator(str(config), state, mocks={})
    revision.bind_initial(orch)
    path = Path(C.state_path(str(tmp_path), state.release_id))
    state.save(str(path))
    args = Namespace(release=state.release_id, runs_root=str(tmp_path), config=str(config),
                     phase=definition.phase_id, step=definition.id, execution_id=None,
                     review_hash=None, approved_by=state.owner_email, executor="runner")
    plan = W.WritePlan(definition.write_command, {}, (
        W.WriteOperation("offline.operation", {"target": "exact"}, {"body": "reviewed"}),))
    args.review_hash = W.review_hash(orch, args.phase, args.step, plan)
    return args, orch, plan, path


def test_every_real_writer_adapter_reaches_shared_authority(writer, monkeypatch, capsys):
    args, orch, plan, path = writer
    owner = next(item for item in COMMAND_MODULES if plan.command in item.external_writes)
    module = import_module("orchestrator.commands." + owner.name)
    planners = {
        "localization": "plan_localization", "rc_report_publish": "plan_rc_report_publish",
        "distribute": "plan_distribution", "integ_prs_cmd": "integration_plan",
        "oneauth_pr_cmd": "oneauth_plan", "payload_wiki_cmd": "plan_payload_wiki",
        "signoff_cmd": "_plan_stage_start",
    }
    assert owner.name in planners, "A new adapter must exercise the shared lifecycle"
    monkeypatch.setattr(module, planners[owner.name], lambda *a, **kw: plan)
    if owner.name in ("integ_prs_cmd", "oneauth_pr_cmd"):
        monkeypatch.setattr(module, "integration_progress" if owner.name == "integ_prs_cmd"
                            else "oneauth_progress", lambda *a: object())
    if owner.name == "signoff_cmd":
        monkeypatch.setattr(module.checks, "current_az_user", lambda: args.approved_by)
        monkeypatch.setattr(module, "_validate_owner_execution_policy", lambda *a: None)
    monkeypatch.setattr(C, "load_orch", lambda *a: (orch.state, orch))
    calls = []

    def authority(cli_args, owning_orch, phase, step, planner, **kwargs):
        calls.append((owning_orch, phase, step, planner()))
        raise ValueError("Stopped at shared write authority")

    monkeypatch.setattr(W, "authorize", authority)
    parsed = build_parser().parse_args([
        "--config", args.config, "--runs-root", args.runs_root, plan.command,
        "--release", args.release, "--execute", "--review-hash", args.review_hash,
        "--approved-by", args.approved_by])
    before = path.read_bytes()
    assert parsed.func(parsed) == 1
    capsys.readouterr()
    assert calls == [(orch, args.phase, args.step, plan)]
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["missing_input", "changed_plan", "policy"])
def test_preflight_failure_leaves_every_writer_unowned(writer, failure):
    args, _, plan, path = writer
    calls = []

    def planner():
        calls.append(True)
        if failure == "missing_input":
            raise FileNotFoundError("Input unavailable")
        if failure == "policy":
            raise ValueError("Execution policy not satisfied")
        return W.WritePlan(plan.command, {"changed": True}, plan.operations)

    before = path.read_bytes()
    with C.state_lock(args.runs_root, args.release):
        state, orch = C.load_orch(args.runs_root, args.release, args.config)
        with pytest.raises((ValueError, FileNotFoundError)):
            W.authorize(args, orch, args.phase, args.step, planner)
        assert not state.get_step(args.phase, args.step).execution
        assert state.get_step(args.phase, args.step).status == "pending"
    assert calls == [True] and path.read_bytes() == before


@pytest.mark.parametrize("automatic", [False, True])
def test_every_writer_plans_once_and_persists_only_the_attempt(writer, automatic):
    args, _, plan, path = writer
    if automatic:
        args.auto_approve, args.execute = True, True
        args.review_hash, args.approved_by = None, None
    snapshots, plans = [], []
    with C.state_lock(args.runs_root, args.release):
        state, orch = C.load_orch(args.runs_root, args.release, args.config)
        actual = state._checkpoint

        def checkpoint():
            snapshots.append(asdict(state))
            actual()

        def planner():
            plans.append(True)
            return plan

        state._checkpoint = checkpoint
        live = W.authorize(args, orch, args.phase, args.step, planner,
                           auto_approved_by="configured-automation" if automatic else None)
        assert plans == [True] and len(snapshots) == 1
        assert snapshots[0]["steps"][args.phase + "." + args.step]["status"] == "in_flight"
        assert state.get_step(args.phase, args.step).execution["id"] == live.execution_id
        live.validate()
        orch.settle_execution(args.phase, args.step, live.execution_id, Blocked("Provider result unknown"))
        state.checkpoint()
        before = path.read_bytes()
        with pytest.raises(ValueError, match="existing execution"):
            W.authorize(args, orch, args.phase, args.step, planner)
        args.execution_id = None
        with pytest.raises(ValueError):
            W.authorize(args, orch, args.phase, args.step, planner)
        assert path.read_bytes() == before


def test_every_writer_rejects_reserve_only_cli(writer):
    args, _, plan, path = writer
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        build_parser().parse_args([plan.command, "--release", args.release, "--reserve"])
    assert path.read_bytes() == before


@pytest.mark.parametrize("ownership,expected", [
    ({"effect_mode": "transactional"}, "reconcile the previous automatic action"),
    ({"effect_mode": "idempotent"}, "reconcile the previous automatic action"),
    ({"notification_id": "delivery"}, "check whether the previous notification was delivered"),
    ({"approval": {}}, "check the existing pipeline approval"),
    ({"write_review": {"hash": "review"}}, "checking the previous write's outcome"),
])
def test_owner_sees_plain_recovery_not_protocol_commands(ownership, expected):
    execution = {"id": "owned", "owner": "runner", **ownership}
    if "approval" in ownership:
        execution["approval"] = {"submission_started_at": None}
    text = step_detail({"state": "in_flight", "execution": execution}, limit=500)
    assert expected in text
    assert "--" not in text and "mark done or reopen" not in text
