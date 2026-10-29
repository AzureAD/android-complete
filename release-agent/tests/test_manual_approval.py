"""External/manual approval import has no provider writer or fake execution history."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import cli_common as C, manual_approvals as M
from orchestrator.cli import build_parser
from orchestrator.commands.gate_record import cmd_record_orchestrator_approval
from orchestrator.state import ReleaseState, StepState
from steps.finalize import publish_notes_gate, remove_rc_tags_gate
from tests._context import fresh_orchestrator


APPROVAL = "37c65c03-b6fc-44a5-b726-7500e8b9c6e9"
OTHER = "41c38feb-9333-411b-8144-9133e50235cc"
BUILD = 1705640


def setup_import(tmp_path, monkeypatch, module=remove_rc_tags_gate):
    state = ReleaseState(release_id="2026-10", timezone="UTC")
    state.pipeline_runs = {"orchestrator": {"run_id": str(BUILD), "parked": True}}
    state.set_step("finalize", module.ID, StepState(data={"preserved": "prior evidence"}))
    path = tmp_path / "2026-10" / "release-state.json"
    config = tmp_path / "phases.yaml"
    config.write_text(yaml.safe_dump({"phases": [{
        "id": "finalize", "name": "Finalize", "steps": [
            {"id": module.ID, "name": module.STAGE, "kind": "approval_gate",
             "approval_command": "approve-orchestrator-gate"},
            {"id": "after", "name": "After", "kind": "auto", "effect_mode": "read_only"},
        ],
    }]}), encoding="utf-8")
    control = {
        "approval": {
            "id": APPROVAL, "status": "approved",
            "pipeline": {"owner": {"id": BUILD, "_links": {"web": {
                "href": f"{module.CONFIG['org']}/{module.CONFIG['project']}/_build/results?buildId={BUILD}",
            }}}},
        },
        "records": [
            {"id": APPROVAL, "type": "Checkpoint.Approval", "name": "Approval",
             "parentId": "checkpoint", "state": "completed"},
            {"id": "checkpoint", "type": "Checkpoint", "parentId": "stage"},
            {"id": "stage", "type": "Stage", "name": module.STAGE,
             "state": "completed", "result": "succeeded"},
            {"id": OTHER, "type": "Checkpoint.Approval", "name": "Other pending",
             "parentId": "other-stage", "state": "inProgress"},
            {"id": "other-stage", "type": "Stage", "name": "Unrelated later gate"},
        ],
        "calls": [], "saves": 0, "during_read": None, "fail_save": False,
        "approval_ok": True, "timeline_ok": True,
    }

    def approval(org, project, approval_id):
        assert (org, project, approval_id) == (
            module.CONFIG["org"], module.CONFIG["project"], APPROVAL)
        control["calls"].append("read-approval")
        return control["approval_ok"], deepcopy(control["approval"]), "approval read"

    def timeline(org, project, build):
        assert (org, project, build) == (module.CONFIG["org"], module.CONFIG["project"], BUILD)
        control["calls"].append("read-timeline")
        if control["during_read"]:
            control["during_read"]()
        return control["timeline_ok"], deepcopy(control["records"]), "timeline read"

    def unexpected(*args, **kwargs):
        pytest.fail("Manual import must not create a provider writer, discover a run, or drain downstream")

    services = SimpleNamespace(pipelines=SimpleNamespace(
        get_pipeline_approval=approval, get_timeline=timeline,
        find_orchestrator_run=unexpected, submit_pipeline_approval=unexpected))
    after = SimpleNamespace(
        ID="after", KIND="agent", EFFECT_MODE="read_only", build=unexpected)
    orch = fresh_orchestrator(
        str(config), state, mocks={}, services=services, effect_services=unexpected,
        now=datetime(2026, 10, 20, 20, tzinfo=timezone.utc),
        handler_resolver=lambda _phase, step: module if step == module.ID else after)

    def save():
        control["saves"] += 1
        if control["fail_save"]:
            raise OSError("disk full")
        state.save(str(path))

    state._checkpoint = save
    state.save(str(path))
    monkeypatch.setattr(C, "load_orch", lambda *_: (state, orch))
    args = SimpleNamespace(
        runs_root=str(tmp_path), release=state.release_id, config=str(config),
        phase="finalize", step=module.ID, approval_id=APPROVAL, preview=True,
        review_hash=None, recorded_by="melissaahn@microsoft.com")
    return orch, args, path, control


def prepare(orch, args):
    return orch.preview_manual_gate_approval(args.phase, args.step, approval_id=args.approval_id)


def execute(orch, args, preview):
    return orch.record_manual_gate_approval(
        args.phase, args.step, approval_id=args.approval_id,
        review_hash=preview["review_hash"], recorded_by=args.recorded_by)


@pytest.mark.parametrize("module", [remove_rc_tags_gate, publish_notes_gate])
def test_preview_apply_reload_and_replay_are_read_only_at_provider(tmp_path, monkeypatch, module):
    orch, args, path, control = setup_import(tmp_path, monkeypatch, module)
    before = path.read_bytes()
    preview = prepare(orch, args)
    assert path.read_bytes() == before and control["saves"] == 0
    assert preview["target"]["build_id"] == BUILD
    assert preview["proof"]["checkpoint_path"][-1]["name"] == module.STAGE
    assert preview["impact"] == {
        "gate": f"finalize.{module.ID}", "provider_writes": False, "advance_downstream": False}
    assert execute(orch, args, preview)["status"] == "recorded"
    assert control["saves"] == 1
    saved = ReleaseState.load(str(path))
    gate = saved.get_step("finalize", module.ID)
    assert gate.status == "done" and gate.execution is None
    assert gate.data["preserved"] == "prior evidence"
    evidence = gate.data["last_approval"]
    assert evidence["provenance"] == "external_manual"
    assert evidence["recorded_by"] == args.recorded_by
    assert "execution_id" not in evidence and "submission_started_at" not in evidence
    assert "approved_by" not in evidence
    assert saved.gate_decisions[-1]["decision"] == "approved"
    assert saved.get_step("finalize", "after").status == "pending"
    assert not orch.invariant_violations()
    before = path.read_bytes()
    assert execute(orch, args, preview)["status"] == "already_recorded"
    assert path.read_bytes() == before and control["saves"] == 1
    assert control["calls"] == ["read-approval", "read-timeline"] * 3


def test_cli_preview_and_confirm_do_not_drain_or_write_journal(tmp_path, monkeypatch, capsys):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    assert cmd_record_orchestrator_approval(args) == 0
    preview = json.loads(capsys.readouterr().out)
    args.preview, args.review_hash = False, preview["review_hash"]
    assert cmd_record_orchestrator_approval(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "recorded"
    assert control["saves"] == 1
    assert sorted(p.name for p in path.parent.iterdir()) == ["release-state.json"]


def test_cli_requires_exact_selection_and_preview_or_hash():
    parser = build_parser()
    prefix = ["record-orchestrator-approval", "--release", "2026-10", "--phase", "finalize",
              "--step", "remove_rc_tags_gate", "--approval-id", APPROVAL]
    assert parser.parse_args(prefix + ["--preview"]).preview
    with pytest.raises(SystemExit):
        parser.parse_args(prefix)
    with pytest.raises(SystemExit):
        parser.parse_args(prefix + ["--preview", "--review-hash", "hash"])


@pytest.mark.parametrize("change", [
    lambda c: c["approval"].update(status="pending"),
    lambda c: c["approval"].update(status="rejected"),
    lambda c: c["approval"].update(status="canceled"),
    lambda c: c["approval"].update(id=OTHER),
    lambda c: c["approval"].pop("pipeline"),
    lambda c: c["approval"]["pipeline"]["owner"].update(id=BUILD + 1),
    lambda c: c["approval"]["pipeline"]["owner"].update(id=True),
    lambda c: c["approval"]["pipeline"]["owner"].pop("id"),
    lambda c: c["approval"]["pipeline"]["owner"]["_links"]["web"].update(href="?buildId=1"),
    lambda c: c.update(approval=None),
    lambda c: c.update(approval_ok=False),
    lambda c: c.update(timeline_ok=False),
    lambda c: c.update(records=None),
    lambda c: c.update(records=[]),
    lambda c: c["records"].append(deepcopy(c["records"][0])),
    lambda c: c["records"][0].update(id=OTHER),
    lambda c: c["records"][0].update(type="Task"),
    lambda c: c["records"][0].update(parentId="absent"),
    lambda c: c["records"][1].update(parentId=APPROVAL),
    lambda c: c["records"][2].update(name="Publish GitHub Release Notes"),
    lambda c: c["records"].append({"id": "duplicate-stage", "type": "Stage", "name": "Remove RC Tags"}),
    lambda c: c["records"].append(None),
])
def test_bad_provider_proof_never_changes_state(tmp_path, monkeypatch, change):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    preview = prepare(orch, args)
    before = path.read_bytes()
    change(control)
    with pytest.raises(ValueError):
        execute(orch, args, preview)
    assert path.read_bytes() == before and control["saves"] == 0
    assert orch.state.steps == ReleaseState.load(str(path)).steps


@pytest.mark.parametrize("approval_id", [
    "", "0", "00000000-0000-0000-0000-000000000000", " approval ", "not-a-uuid", None, 5,
])
def test_missing_or_invalid_explicit_identity_fails_before_reads(tmp_path, monkeypatch, approval_id):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    args.approval_id = approval_id
    with pytest.raises(ValueError):
        prepare(orch, args)
    assert not control["calls"] and control["saves"] == 0


@pytest.mark.parametrize("selection", [("finalize", "after"), ("wrong", "remove_rc_tags_gate")])
def test_wrong_gate_selection_is_not_an_import(tmp_path, monkeypatch, selection):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    args.phase, args.step = selection
    with pytest.raises(ValueError):
        prepare(orch, args)
    assert not control["calls"] and control["saves"] == 0


@pytest.mark.parametrize("mocks", [{"submit": "skip"}, {"approval_state": {"status": "approved"}}])
def test_nonempty_mocks_cannot_authorize_import(tmp_path, monkeypatch, mocks):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    orch.mocks = {f"{args.phase}.{args.step}": mocks}
    with pytest.raises(ValueError, match="mocks"):
        prepare(orch, args)
    assert not control["calls"] and control["saves"] == 0


@pytest.mark.parametrize("suspend", ["halt", "cancel"])
def test_suspended_release_cannot_import(tmp_path, monkeypatch, suspend):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    getattr(orch, suspend)("incident")
    with pytest.raises(ValueError):
        prepare(orch, args)
    assert not control["calls"] and control["saves"] == 0


@pytest.mark.parametrize("change", ["hash", "generation", "proof", "missing_build", "workflow", "runtime"])
def test_stale_review_binding_is_rejected_without_save(tmp_path, monkeypatch, change):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    preview = prepare(orch, args)
    if change == "hash":
        preview["review_hash"] = "sha256:" + "0" * 64
    elif change == "generation":
        record = orch.state.get_step(args.phase, args.step)
        record.invalidated_at = "2026-10-20T19:00:00+00:00"
        orch.state.set_step(args.phase, args.step, record)
    elif change == "proof":
        control["records"][1]["id"] = "changed"
        control["records"][0]["parentId"] = "changed"
    elif change == "missing_build":
        orch.state.pipeline_runs = {}
    elif change == "workflow":
        orch.state.workflow_revision["runtime_hash"] = "sha256:" + "0" * 64
    elif change == "runtime":
        orch._loaded_runtime_hash = "sha256:" + "0" * 64
    before = path.read_bytes()
    with pytest.raises(ValueError):
        execute(orch, args, preview)
    assert path.read_bytes() == before and control["saves"] == 0


@pytest.mark.parametrize("change", ["generation", "build", "halt", "runtime"])
def test_mutation_during_provider_read_cannot_apply(tmp_path, monkeypatch, change):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    preview = prepare(orch, args)

    def mutate():
        if change == "generation":
            record = orch.state.get_step(args.phase, args.step)
            record.invalidated_at = "2026-10-20T19:00:00+00:00"
            orch.state.set_step(args.phase, args.step, record)
        elif change == "build":
            orch.state.pipeline_runs["orchestrator"]["run_id"] = "2"
        elif change == "halt":
            orch.halt("incident")
        else:
            orch._loaded_runtime_hash = "sha256:" + "0" * 64

    control["during_read"] = mutate
    with pytest.raises(ValueError):
        execute(orch, args, preview)
    assert control["saves"] == 0 and not orch.state.gate_decisions


def test_owned_approval_is_not_overwritten(tmp_path, monkeypatch):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    # Reserve through the existing real kernel contract, without calling any writer.
    from orchestrator.approvals import ApprovalRequest, request_hash
    from orchestrator.revision import revision_id
    target = M.configured_target(orch, args.phase, args.step, APPROVAL)
    request = ApprovalRequest(**vars(target), comment="reviewed submission")
    kernel = orch._transition_kernel()
    assert kernel.reserve_approval(
        args.phase, args.step, request,
        request_hash(orch.state, f"{args.phase}.{args.step}", request,
                     revision_id(orch.state.workflow_revision)), "owner", "session").changed
    before = deepcopy(orch.state.steps)
    with pytest.raises(ValueError):
        prepare(orch, args)
    assert orch.state.steps == before and not control["calls"]


def test_prior_owned_submission_receipt_cannot_be_replaced(tmp_path, monkeypatch):
    from tests.test_approval_checkpoint import _setup
    command, owned_args, owned_orch, _, _, _ = _setup(tmp_path, monkeypatch)
    assert command(owned_args) == 0
    receipt = deepcopy(owned_orch.state.get_step("phase", "approve").data["last_approval"])
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    record = orch.state.get_step(args.phase, args.step)
    record.data["last_approval"] = receipt
    orch.state.set_step(args.phase, args.step, record)
    before = deepcopy(orch.state.steps)
    with pytest.raises(ValueError, match="owned approval evidence"):
        prepare(orch, args)
    assert orch.state.steps == before and not control["calls"] and control["saves"] == 0


@pytest.mark.parametrize("reviewer", [None, "", "   "])
def test_import_requires_truthful_recorder_before_provider_reads(tmp_path, monkeypatch, reviewer):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    args.recorded_by = reviewer
    with pytest.raises(ValueError, match="recorded-by"):
        execute(orch, args, {"review_hash": "hash"})
    assert not control["calls"] and control["saves"] == 0


def test_replay_cannot_rewrite_the_original_importer(tmp_path, monkeypatch):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    preview = prepare(orch, args)
    execute(orch, args, preview)
    before = path.read_bytes()
    args.recorded_by = "other-user"
    with pytest.raises(ValueError, match="not be overwritten"):
        execute(orch, args, preview)
    assert path.read_bytes() == before and control["saves"] == 1


def test_core_rejects_wrong_configured_stage_even_with_a_matching_proof_hash(tmp_path, monkeypatch):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    preview = prepare(orch, args)
    evidence = {key: deepcopy(preview[key]) for key in (
        "release", "step", "generation", "workflow_revision", "target", "proof", "review_hash")}
    evidence.update(provenance="external_manual", recorded_by="importer",
                    recorded_at="2026-10-20T20:00:00+00:00")
    evidence["target"]["stage"] = publish_notes_gate.STAGE
    evidence["proof"]["checkpoint_path"][-1]["name"] = publish_notes_gate.STAGE
    evidence["review_hash"] = M.evidence_hash(evidence)
    assert M.validate_evidence(evidence)
    before = deepcopy(orch.state.steps)
    result = orch._transition_kernel().record_manual_approval(args.phase, args.step, evidence)
    assert not result.changed and "binding" in result.message
    assert orch.state.steps == before and not orch.state.gate_decisions


def test_atomic_checkpoint_failure_rolls_back_all_local_changes(tmp_path, monkeypatch):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    preview = prepare(orch, args)
    before, steps = path.read_bytes(), deepcopy(orch.state.steps)
    control["fail_save"] = True
    with pytest.raises(OSError, match="disk full"):
        execute(orch, args, preview)
    assert path.read_bytes() == before and orch.state.steps == steps and not orch.state.gate_decisions
    control["fail_save"] = False
    assert execute(orch, args, preview)["status"] == "recorded"


def test_manual_receipt_survives_reopen_but_cannot_be_reimported_or_annotated(tmp_path, monkeypatch):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    preview = prepare(orch, args)
    execute(orch, args, preview)
    receipt = deepcopy(orch.state.get_step(args.phase, args.step).data["last_approval"])
    assert not orch.annotate_step(args.phase, args.step, data={"last_approval": {}}).changed
    assert orch.reopen_step(args.phase, args.step, "review needed").kind == "ran"
    assert orch.state.get_step(args.phase, args.step).data["last_approval"] == receipt
    with pytest.raises(ValueError, match="replay"):
        execute(orch, args, preview)


@pytest.mark.parametrize("field,value", [
    ("provenance", "owned"), ("review_hash", "sha256:" + "0" * 64),
    ("recorded_by", ""), ("recorded_at", "2026-10-20"), ("execution_id", "fabricated"),
    ("release", "other-release"), ("step", "finalize.publish_notes_gate"),
])
def test_corrupt_closed_manual_evidence_fails_invariants_and_load(tmp_path, monkeypatch, field, value):
    orch, args, path, control = setup_import(tmp_path, monkeypatch)
    execute(orch, args, prepare(orch, args))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["steps"][f"{args.phase}.{args.step}"]["data"]["last_approval"][field] = value
    if field in ("release", "step"):
        receipt = raw["steps"][f"{args.phase}.{args.step}"]["data"]["last_approval"]
        receipt["review_hash"] = M.evidence_hash(receipt)
    orch.state.steps = raw["steps"]
    assert any(v.code == "invalid_closed_approval" for v in orch.invariant_violations())
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        ReleaseState.load(str(path))
