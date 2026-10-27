"""Cross-phase handoff contracts, isolated provider ports and persisted CLI."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import cli, cli_common as C, render
from orchestrator.dispatch import handoff_plan
from orchestrator.outcomes import Done, NeedsSkill
from orchestrator.state import ReleaseState, StepState
from tests._context import fresh_orchestrator as Orchestrator
from tests._harness import CONFIG, _active_phase


with open(CONFIG, encoding="utf-8") as stream:
    PHASES = [item["id"] for item in yaml.safe_load(stream)["phases"]]


def engine(tmp_path, phase, *, execution="parallel"):
    calls = []
    config = {"phases": [{
        "id": phase, "name": phase, "execution": execution,
        "steps": [
            {"id": "gate", "name": "Owner gate", "kind": "approval_gate"},
            {"id": "scan", "name": "Independent scan", "kind": "auto", "effect_mode": "read_only"},
            {"id": "gather", "name": "Independent host read", "kind": "external"},
            {"id": "dependent", "name": "Dependent scan", "kind": "auto",
             "effect_mode": "read_only", "depends_on": ["gather"]},
        ],
    }]}
    path = tmp_path / "phases.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    def scan(_context):
        calls.append("scan")
        return Done("observed")

    def dependent(_context):
        calls.append("dependent")
        return Done("observed dependent")

    modules = {
        "scan": SimpleNamespace(ID="scan", KIND="agent", EFFECT_MODE="read_only", build=scan),
        "dependent": SimpleNamespace(ID="dependent", KIND="agent", EFFECT_MODE="read_only", build=dependent),
        "gather": SimpleNamespace(ID="gather", KIND="scout", build=lambda _context:
            NeedsSkill(tool="fixture_read", payload={}, record_as="gather")),
    }
    st = ReleaseState(release_id="r", timezone="UTC")
    orch = Orchestrator(str(path), st, mocks={}, handler_resolver=lambda _p, sid: modules.get(sid))
    return orch, calls


@pytest.mark.parametrize("phase", PHASES + ["future_phase"])
@pytest.mark.parametrize("execution", ["parallel", "sequential"])
def test_all_phase_names_share_dispatch_without_crossing_real_gates(tmp_path, phase, execution):
    orch, calls = engine(tmp_path, phase, execution=execution)
    before = deepcopy(orch.state)
    report = orch.status_report()
    assert report["active_phase"] is not None
    plan = report["work_dispatch"]
    assert report["gate"]["step"] == "gate"
    assert {item["step"] for item in plan["pending_work"]} == (
        {"scan", "gather"} if execution == "parallel" else set())
    assert plan["may_return_to_user"] == (execution != "parallel")
    assert not plan["permission_to_execute"] and not plan["permission_to_send"]
    assert orch.state == before and not calls
    if execution == "parallel":
        assert "Scout work remains" in render.status_view(report)
        assert "Next: your decision" not in render.status_view(report)
        orch.run_until_gate(pending_only=True)
        assert calls == ["scan"]
        assert {item["step"] for item in orch.status_report()["work_dispatch"]["pending_work"]} == {"gather"}
        orch.record_scout_step(phase, "gather", "pass", "actual fixture read")
        assert {item["step"] for item in orch.status_report()["work_dispatch"]["pending_work"]} == {"dependent"}
        orch.run_until_gate(pending_only=True)
        assert calls == ["scan", "dependent"]
        assert orch.status_report()["work_dispatch"]["may_return_to_user"]
        assert not orch.state.gate_decisions


@pytest.mark.parametrize("phase", PHASES)
def test_actual_phase_config_queue_matches_scheduler_and_frontier(phase):
    st = ReleaseState(release_id="2026-12", ccd="2026-12-09",
                      owner_email="owner@example.test", timezone="UTC")
    _active_phase(st, phase)
    if phase == "hotfix":
        st.active_conditionals.append("hotfix")
    orch = Orchestrator(CONFIG, st, mocks={}, as_of=date(2027, 1, 30))
    selection = orch.scheduling()
    report = orch.status_report()
    assert selection.frontier.id == phase
    expected = {
        candidate.step.key for candidate in selection.runnable
        if selection.step(phase, candidate.step.id).status == "pending"
        and not selection.step(phase, candidate.step.id).reserved and not candidate.recovery
    } | {f"{phase}.{sid}" for sid in selection.scout_pending
         if selection.step(phase, sid).status == "pending" and not selection.step(phase, sid).reserved}
    assert {item["key"] for item in report["work_dispatch"]["pending_work"]} == expected
    assert report["work_dispatch"]["may_return_to_user"] == (not expected)


@pytest.mark.parametrize("status", ["blocked", "running", "in_flight"])
def test_local_failures_and_waits_do_not_replay_or_hide_siblings(tmp_path, status):
    orch, calls = engine(tmp_path, "phase")
    orch.state.set_step("phase", "scan", StepState(status=status, note="actual wait/failure"))
    before = deepcopy(orch.state.get_step("phase", "scan"))
    orch.run_until_gate(pending_only=True)
    assert not calls
    plan = orch.status_report()["work_dispatch"]
    assert {item["step"] for item in plan["pending_work"]} == {"gather"}
    assert {item["step"] for item in plan["held_work"]} == {"scan"}
    assert orch.state.get_step("phase", "scan") == before


@pytest.mark.parametrize("hold", ["halt", "cancellation", "denied"])
def test_global_holds_precede_independent_phase_dispatch(tmp_path, hold):
    orch, calls = engine(tmp_path, "phase")
    if hold == "denied":
        orch.state.gate_decisions.append({
            "step": "phase.gate", "decision": "denied", "at": "2026-10-21T00:00:00Z",
            "by": "owner", "comment": "hold"})
    else:
        setattr(orch.state, hold, {"reason": "owner stopped", "at": "2026-10-21T00:00:00Z"})
    report = orch.status_report()
    assert not report["work_dispatch"]["pending_work"]
    assert report["work_dispatch"]["may_return_to_user"]
    assert report["work_dispatch"]["next_action"] == "resolve_release_hold"
    assert "Scout work remains" not in render.status_view(report)
    orch.run_until_gate(pending_only=True)
    assert not calls


def test_transient_scout_input_holds_cannot_hide_unattempted_engine_work(tmp_path):
    orch, _ = engine(tmp_path, "phase")
    plan = orch.status_report()["work_dispatch"]
    before = deepcopy(orch.state)
    result = handoff_plan(plan, ["phase.gather=Need actual browser sign-in"])
    assert not result["may_return_to_user"]
    assert [item["step"] for item in result["pending_work"]] == ["scan"]
    assert result["deferred_work"][0]["verification"] == "host_reported_not_provider_verified"
    assert orch.state == before
    with pytest.raises(ValueError, match="queued Scout"):
        handoff_plan(plan, ["phase.scan=Skip this engine work"])
    with pytest.raises(ValueError, match="queued Scout"):
        handoff_plan(plan, ["phase.dependent=Not yet eligible"])
    orch.run_until_gate(pending_only=True)
    result = handoff_plan(orch.status_report()["work_dispatch"], ["phase.gather=Need actual browser sign-in"])
    assert result["may_return_to_user"] and not result["permission_to_execute"]
    assert not orch.state.is_done("phase", "gather")
    assert not orch.status_report()["work_dispatch"]["may_return_to_user"]


def test_scheduled_real_phase_never_becomes_executable_to_satisfy_handoff():
    st = ReleaseState(release_id="2026-12", ccd="2026-12-09",
                      owner_email="owner@example.test", timezone="UTC")
    _active_phase(st, "preflight")
    orch = Orchestrator(CONFIG, st, mocks={}, as_of=date(2026, 10, 21))
    plan = orch.status_report()["work_dispatch"]
    assert plan["next_action"] == "scheduled" and plan["pending_work"] == []
    assert plan["may_return_to_user"]


def seed_test6(root):
    st = ReleaseState(release_id="2026-12", ccd="2026-12-09",
                      owner_email="owner@example.test", timezone="UTC")
    _active_phase(st, "preflight")
    for sid in ("breaking", "oneauth_access", "cron"):
        st.set_step("preflight", sid, StepState(status="done", note="already observed"))
    st.set_step("preflight", "cg", StepState(status="blocked", note="actual CG alerts"))
    C.save_state(st, str(root), st.release_id)
    from tests._automation import seed_driver
    seed_driver(root, st)
    return st


@pytest.mark.parametrize("as_json", [False, True])
def test_persisted_test6_guard_refuses_cg_handoff_before_independent_tasks(tmp_path, capsys, as_json):
    st = seed_test6(tmp_path)
    path = tmp_path / st.release_id / "release-state.json"
    before = path.read_bytes()
    args = ["--runs-root", str(tmp_path), "status", "--release", st.release_id,
            "--as-of", "2026-12-02", "--handoff", "--no-pipeline-check"]
    if as_json:
        args.append("--json")
    assert cli.main(args) == 2
    output = capsys.readouterr().out
    if as_json:
        report = json.loads(output)
        assert report["action"]["step"] == "cg"
        plan = report["work_dispatch"]
        assert {item["step"] for item in plan["pending_work"]} == {"notice", "flight_reminder", "lockdown"}
        assert not plan["may_return_to_user"]
    else:
        assert "Scout work remains" in output and "Scout queue" in output
        assert "Mark it done when complete" not in output
    assert path.read_bytes() == before


def test_step_action_and_tick_expose_same_cross_phase_queue(tmp_path, monkeypatch, capsys):
    from orchestrator import schedule
    st = seed_test6(tmp_path)
    monkeypatch.setattr(schedule, "now_local", lambda _tz=None:
                        datetime(2026, 12, 2, 12, tzinfo=timezone.utc))
    for verb, extra in (
        ("step-action", ["--phase", "preflight", "--step", "vitals"]),
        ("tick", ["--json"]),
    ):
        assert cli.main(["--runs-root", str(tmp_path), verb, "--release", st.release_id, *extra]) == 0
        result = json.loads(capsys.readouterr().out)
        assert {item["step"] for item in result["work_dispatch"]["pending_work"]} == {
            "notice", "flight_reminder", "lockdown"}
        assert not result["work_dispatch"]["may_return_to_user"]


@pytest.mark.parametrize("holds,code", [
    ([], 2),
    (["preflight.notice=Need actual send approval"], 2),
    (["preflight.notice=Need actual send approval", "preflight.flight_reminder=Need actual post approval",
      "preflight.lockdown=Need actual browser sign-in"], 0),
    (["preflight.cg=Do not run"], 1),
    (["preflight.notice="], 1),
    (["preflight.notice=first", "preflight.notice=duplicate"], 1),
])
def test_guarded_cli_input_boundaries_never_mutate_or_hide_independent_work(
        tmp_path, capsys, holds, code):
    st = seed_test6(tmp_path)
    path = tmp_path / st.release_id / "release-state.json"
    before = path.read_bytes()
    args = ["--runs-root", str(tmp_path), "status", "--release", st.release_id,
            "--as-of", "2026-12-02", "--handoff", "--json", "--no-pipeline-check"]
    for hold in holds:
        args.extend(["--hold-step", hold])
    assert cli.main(args) == code
    result = json.loads(capsys.readouterr().out)
    assert path.read_bytes() == before
    if code != 1:
        plan = result["work_dispatch"]
        assert plan["may_return_to_user"] == (code == 0)
        assert not plan["permission_to_send"] and not plan["permission_to_execute"]
        assert len(plan["pending_work"]) + len(plan["deferred_work"]) == 3
        assert not C.load_state(str(tmp_path), st.release_id).notification_deliveries
        if code == 0:
            view = render.status_view(result)
            assert "Blocked check" in view and "Mark it done when complete" not in view


def test_guard_without_release_readiness_reuses_entry_exhaustion(tmp_path, capsys):
    st = ReleaseState(release_id="2026-12", owner_email="owner@example.test")
    # Binding through the normal test orchestrator does not sign readiness.
    Orchestrator(CONFIG, st, mocks={})
    C.save_state(st, str(tmp_path), st.release_id)
    from tests._automation import seed_driver
    seed_driver(tmp_path, st)
    assert cli.main(["--runs-root", str(tmp_path), "status", "--release", st.release_id,
                     "--handoff", "--json", "--no-pipeline-check"]) == 2
    plan = json.loads(capsys.readouterr().out)["work_dispatch"]
    assert plan["next_action"] == "readiness" and not plan["pending_work"]
    assert not plan["may_return_to_user"]
    assert plan["readiness_dispatch"]["pending_auto_checks"]


def test_guarded_integrity_hold_has_no_dispatch_or_provider_permission(tmp_path):
    orch, calls = engine(tmp_path, "phase")
    orch.state.set_step("phase", "gather", StepState(status="running"))
    report = orch.status_report()
    assert any(item["severity"] == "error" for item in report["invariant_violations"])
    assert report["work_dispatch"]["may_return_to_user"]
    assert not report["work_dispatch"]["pending_work"]
    assert not report["work_dispatch"]["permission_to_execute"] and not calls
