"""Cross-phase handoff contracts, isolated provider ports and persisted CLI."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import cli, cli_common as C, render
from orchestrator.dispatch import handoff_plan, decorate_handoff_contract
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
    orch = Orchestrator(str(path), st, step_defaults={}, handler_resolver=lambda _p, sid: modules.get(sid))
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
    orch = Orchestrator(CONFIG, st, step_defaults={}, as_of=date(2027, 1, 30))
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


def test_held_work_includes_recovery_command_and_execution_id(tmp_path):
    orch, _ = engine(tmp_path, "phase")
    orch.state.set_step("phase", "gate", StepState(status="done", note="approved"))
    orch.state.set_step("phase", "gather", StepState(
        status="in_flight",
        execution={"id": "exec-123", "owner": "owner@example.com", "started_at": "2026-10-21T00:00:00Z"},
        note="awaiting external completion",
    ))
    held = orch.status_report()["work_dispatch"]["held_work"]
    item = next(entry for entry in held if entry["step"] == "gather")
    assert item["execution_id"] == "exec-123"
    assert "exec-123" in item["resume_command"]
    assert item["resume_command"].startswith("step-action --release ")


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
    orch = Orchestrator(CONFIG, st, step_defaults={}, as_of=date(2026, 10, 21))
    report = orch.status_report()
    plan = report["work_dispatch"]
    assert plan["next_action"] == "scheduled" and plan["pending_work"] == []
    assert plan["may_return_to_user"]
    assert report["display_before_prompt"]
    assert report["display_markdown"] == render.status_view(report)
    assert "### Phases" in report["display_markdown"]
    assert "Scheduled" in report["display_markdown"] and "owner_prompt" not in report


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
    else:
        args.append("--details")
    assert cli.main(args) == 2
    output = capsys.readouterr().out
    if as_json:
        report = json.loads(output)
        assert report["action"]["step"] == "cg"
        plan = report["work_dispatch"]
        assert {item["step"] for item in plan["pending_work"]} == {"notice", "flight_reminder", "lockdown"}
        assert not plan["may_return_to_user"]
    else:
        assert "Scout work remains" in output and "Next actions before owner handoff:" in output
        assert "Mark it done when complete" not in output
    assert path.read_bytes() == before


def test_step_action_exposes_cross_phase_queue(tmp_path, monkeypatch, capsys):
    from orchestrator import schedule
    st = seed_test6(tmp_path)
    monkeypatch.setattr(schedule, "now_local", lambda _tz=None:
                        datetime(2026, 12, 2, 12, tzinfo=timezone.utc))
    assert cli.main(["--runs-root", str(tmp_path), "step-action", "--release", st.release_id,
                     "--phase", "preflight", "--step", "vitals"]) == 0
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
    Orchestrator(CONFIG, st, step_defaults={})
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


def test_owner_prompt_payload_surfaces_gate_decision_when_queue_is_empty(tmp_path):
    orch, _ = engine(tmp_path, "phase", execution="sequential")
    report = orch.status_report()
    prompt = report.get("owner_prompt")
    assert prompt and prompt["ready"] and prompt["kind"] == "gate_decision"
    assert [item["action"] for item in prompt["answers"]] == ["approve", "deny", "hold"]


def test_handoff_contract_exposes_continue_signal_and_gate_prompt(tmp_path):
    # Active queue => must_continue_automatic_work=True, no owner prompt yet.
    orch, _ = engine(tmp_path, "phase", execution="parallel")
    queued = decorate_handoff_contract(orch.status_report())
    assert queued["must_continue_automatic_work"] and not queued["handoff_ready"]
    assert queued["progression_policy"] == "automatic_until_input_or_wait"
    assert not queued["display_before_prompt"] and "display_markdown" not in queued
    assert "owner_prompt" not in queued

    # Empty queue + gate hold => owner_prompt is present.
    gate_report = decorate_handoff_contract(
        engine(tmp_path, "phase2", execution="sequential")[0].status_report()
    )
    assert gate_report["handoff_ready"] and not gate_report["must_continue_automatic_work"]
    assert gate_report["owner_prompt"]["kind"] == "gate_decision"
    assert gate_report["owner_prompt"]["display_before_prompt"]
    assert gate_report["display_markdown"] == render.status_view(gate_report)
    assert "| Step | State | Details |" in gate_report["display_markdown"]
    assert "you are here" in gate_report["display_markdown"]


def test_default_guided_followup_is_read_only_and_keeps_executable_work_automatic(tmp_path):
    orch, calls = engine(tmp_path, "phase", execution="parallel")
    before = deepcopy(orch.state)
    followup = C.guided_followup(orch)
    assert followup["progression_policy"] == "automatic_until_input_or_wait"
    assert followup["continue_automatically"] and followup["must_continue_automatic_work"]
    assert {item["step"] for item in followup["work_dispatch"]["pending_work"]} == {"scan", "gather"}
    assert not followup["handoff_ready"] and "owner_prompt" not in followup
    assert not followup["work_dispatch"]["permission_to_execute"]
    assert not followup["work_dispatch"]["permission_to_send"]
    assert orch.state == before and not calls


def test_guided_progression_continues_across_phases_until_the_actual_human_gate(tmp_path):
    calls = []
    config = {"phases": [
        {"id": "first", "name": "First phase", "steps": [
            {"id": "scan", "name": "Initial scan", "kind": "auto", "effect_mode": "read_only"},
            {"id": "host", "name": "Agent-hosted read", "kind": "external"}]},
        {"id": "second", "name": "Second phase", "steps": [
            {"id": "scan", "name": "Later scan", "kind": "auto", "effect_mode": "read_only",
             "depends_on": ["first.host"]},
            {"id": "gate", "name": "Real owner decision", "kind": "approval_gate"}]},
    ]}
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    def resolver(phase, step):
        if step == "scan":
            def scan(context):
                calls.append(phase)
                return Done("Read completed")
            return SimpleNamespace(ID=step, KIND="agent", EFFECT_MODE="read_only", build=scan)
        if step == "host":
            return SimpleNamespace(ID=step, KIND="scout", build=lambda context:
                                   NeedsSkill("fixture_read", record_as="host", outbound=False))
        return None

    state = ReleaseState(release_id="r", timezone="UTC")
    orch = Orchestrator(str(path), state, step_defaults={}, handler_resolver=resolver)
    orch.run_until_gate()
    followup = C.guided_followup(orch)
    assert calls == ["first"] and followup["continue_automatically"]
    assert [item["step"] for item in followup["work_dispatch"]["pending_work"]] == ["host"]
    assert "owner_prompt" not in followup
    orch.record_scout_step("first", "host", "pass", "Actual host read")
    assert C.guided_followup(orch)["must_continue_automatic_work"]
    orch.run_until_gate()
    report = orch.status_report()
    assert calls == ["first", "second"]
    assert report["current_phase"] == "second" and report["handoff_ready"]
    assert report["owner_prompt"]["kind"] == "gate_decision"
    assert report["display_before_prompt"]
    assert report["display_markdown"] == render.status_view(report)
    assert not state.gate_decisions


def test_owner_prompt_suppressed_when_non_owner_held_work_exists(tmp_path):
    orch, _ = engine(tmp_path, "phase", execution="parallel")
    orch.state.set_step("phase", "scan", StepState(status="done", note="observed"))
    orch.state.set_step("phase", "gather", StepState(status="done", note="observed"))
    orch.state.set_step("phase", "dependent", StepState(status="skipped", note="not required"))
    orch.state.set_step("phase", "gather", StepState(
        status="in_flight",
        execution={"id": "gather-exec-1", "owner": "owner@example.com", "started_at": "2026-10-21T00:00:00Z"},
        note="awaiting completion",
    ))
    report = decorate_handoff_contract(orch.status_report())
    assert report["work_dispatch"]["next_action"] == "resolve_phase_hold"
    assert report["handoff_ready"]
    assert "owner_prompt" not in report


def test_non_blocking_exhaustive_execution_isolates_tool_failures_and_reports_all_reasons(tmp_path):
    """Step-level tool failures fail ONLY the affected step, prevent its direct dependents,
    continue executing all independent and satisfied-dependency branches, and surface every
    specific failure reason in the rendered status view."""
    calls = []
    config = {"phases": [{
        "id": "phase", "name": "Validation Phase", "execution": "parallel",
        "steps": [
            {"id": "missing_cli", "name": "Missing CLI step", "kind": "auto",
             "effect_mode": "read_only", "depends_on": [], "required_tools": ["gh"]},
            {"id": "blocked_downstream", "name": "Blocked downstream", "kind": "auto",
             "effect_mode": "read_only", "depends_on": ["missing_cli"], "required_tools": []},
            {"id": "healthy_root", "name": "Healthy root", "kind": "auto",
             "effect_mode": "read_only", "depends_on": [], "required_tools": ["az"]},
            {"id": "healthy_child", "name": "Healthy child", "kind": "auto",
             "effect_mode": "read_only", "depends_on": ["healthy_root"], "required_tools": []},
            {"id": "mcp_notify", "name": "Teams notification", "kind": "external",
             "depends_on": ["healthy_root"], "required_tools": ["microsoft_teams"]},
            {"id": "notify_child", "name": "After Teams notify", "kind": "auto",
             "effect_mode": "read_only", "depends_on": ["mcp_notify"], "required_tools": []},
        ],
    }]}
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    def resolver(_phase, sid):
        if sid == "missing_cli":
            def fail_cli(_ctx):
                calls.append(sid)
                raise FileNotFoundError("gh binary not found on PATH")
            return SimpleNamespace(ID=sid, KIND="agent", EFFECT_MODE="read_only", build=fail_cli)
        if sid in ("blocked_downstream", "healthy_root", "healthy_child", "notify_child"):
            def ok(_ctx, sid=sid):
                calls.append(sid)
                return Done(f"{sid} succeeded")
            return SimpleNamespace(ID=sid, KIND="agent", EFFECT_MODE="read_only", build=ok)
        if sid == "mcp_notify":
            return SimpleNamespace(
                ID=sid, KIND="scout", NOTIFICATION=True,
                build=lambda _ctx: NeedsSkill("microsoft_teams-SendMessageToChannel", record_as=sid),
            )
        return None

    st = ReleaseState(release_id="2026-11", timezone="UTC")
    orch = Orchestrator(str(path), st, step_defaults={}, handler_resolver=resolver)

    # First drain: missing_cli fails with FileNotFoundError, NOT halting healthy_root or healthy_child!
    orch.run_until_gate()
    assert calls == ["missing_cli", "healthy_root", "healthy_child"]
    assert st.get_step("phase", "missing_cli").status == "blocked"
    assert "gh binary not found" in st.get_step("phase", "missing_cli").note
    assert st.is_done("phase", "healthy_root") and st.is_done("phase", "healthy_child")
    assert st.get_step("phase", "blocked_downstream").status == "pending"

    # External notification step fails due to missing/misconfigured MCP connector via fail_step:
    orch.fail_step("phase", "mcp_notify", "Session not found (-32001)", tool="microsoft_teams")
    orch.run_until_gate()
    assert st.get_step("phase", "mcp_notify").status == "blocked"
    assert st.get_step("phase", "notify_child").status == "pending"

    report = orch.status_report()
    assert report["work_dispatch"]["pending_work"] == []
    assert report["work_dispatch"]["may_return_to_user"]
    markdown = render.status_view(report)
    assert "Healthy root" in markdown and "Done" in markdown
    assert "Healthy child" in markdown
    assert "Missing CLI step" in markdown and "gh binary not found" in markdown
    assert "Teams notification" in markdown and "microsoft_teams" in markdown and "Session not found" in markdown


def test_status_view_renders_contextual_quick_commands_footer_by_state(tmp_path):
    from datetime import date
    st = seed_test6(tmp_path)
    orch = Orchestrator(CONFIG, st, step_defaults={}, as_of=date(2026, 12, 2))
    # Mark pending scout steps done so queue is exhausted and handoff is active
    for sid in ("notice", "flight_reminder", "lockdown"):
        st.set_step("preflight", sid, StepState(status="done", note="ok"))
    active_report = orch.status_report()
    active_md = render.status_view(active_report)
    assert "💡 **Quick commands:**" in active_md
    assert '`"status"`' in active_md and '`"continue"`' in active_md and '`"help"`' in active_md
    assert '`"explain <step>"`' in active_md and '`"rerun <step>"`' in active_md
    assert '`"skip <step>"`' in active_md and '`"completed <step> manually"`' in active_md

    # Phase 2 surfaces "rc report"
    p2_report = deepcopy(active_report)
    p2_report["current_phase"] = "build_verify"
    assert '`"rc report"`' in render.status_view(p2_report)

    # Scheduled phase wait surfaces window open date without suggesting CCD changes
    sched_report = deepcopy(active_report)
    sched_report["scheduled"] = {"phase": "ccd", "phase_name": "Code Complete Day", "opens": "2026-12-09", "opens_in_days": 7}
    sched_md = render.status_view(sched_report)
    assert "once 2026-12-09 opens" in sched_md and "set CCD to YYYY-MM-DD" not in sched_md

    # Uncertain execution never suggests blind rerun/continue
    uncertain_report = deepcopy(active_report)
    uncertain_report["action"] = {
        "phase": "preflight", "step": "notice", "step_name": "Send early release notice",
        "execution": {"notification_id": "step:preflight.notice:once:1"},
    }
    uncertain_md = render.status_view(uncertain_report)
    assert '`"task history"`' in uncertain_md
    assert '`"rerun ' not in uncertain_md


def test_commands_cli_renders_starter_and_live_active_release_guides(tmp_path, capsys):
    # 1. No active release -> starter guide + launcher_no_release
    assert cli.main(["--runs-root", str(tmp_path), "commands", "--json"]) == 0
    empty_payload = json.loads(capsys.readouterr().out)
    assert not empty_payload["has_active_release"]
    assert "No active release is initialized yet" in empty_payload["guide_markdown"]
    assert empty_payload["launcher_prompt"]["kind"] == "launcher_no_release"
    assert [a["action"] for a in empty_payload["launcher_prompt"]["answers"]] == [
        "start_release", "environment", "list_releases", "show_commands",
    ]

    # 2. Active release -> auto-resolves release, fills concrete step names, builds active launcher
    st = seed_test6(tmp_path)
    for sid in ("notice", "flight_reminder", "lockdown"):
        st.set_step("preflight", sid, StepState(status="done", note="ok"))
    C.save_state(st, str(tmp_path), st.release_id)

    assert cli.main(["--runs-root", str(tmp_path), "commands", "--as-of", "2026-12-02", "--json"]) == 0
    active_payload = json.loads(capsys.readouterr().out)
    assert active_payload["has_active_release"]
    assert active_payload["release_id"] == st.release_id
    assert "Report critical CG alerts" in active_payload["guide_markdown"]
    assert "Send early release notice" in active_payload["guide_markdown"]
    assert active_payload["status_markdown"] and "## Release" in active_payload["status_markdown"]
    assert active_payload["launcher_prompt"]["kind"] == "launcher_active_release"
    assert [a["action"] for a in active_payload["launcher_prompt"]["answers"]] == [
        "continue", "resolve_step", "rerun_step", "show_commands",
    ]


def test_checkpoint_owner_prompts_preserve_seamless_auto_resume(tmp_path):
    from datetime import date
    # 1. Blocked step surfaces blocked_step prompt with resume_on_resolve=True
    st = seed_test6(tmp_path)
    for sid in ("notice", "flight_reminder", "lockdown"):
        st.set_step("preflight", sid, StepState(status="done", note="ok"))
    orch = Orchestrator(CONFIG, st, step_defaults={}, as_of=date(2026, 12, 2))
    blocked_report = decorate_handoff_contract(orch.status_report())
    assert blocked_report["handoff_ready"]
    bp = blocked_report["owner_prompt"]
    assert bp["kind"] == "blocked_step" and bp["step"] == "cg" and bp["resume_on_resolve"] is True
    assert [a["action"] for a in bp["answers"]] == [
        "retry", "rerun", "complete-manually", "override", "hold",
    ]

    # 2. Human confirm step surfaces owner_action prompt with [done, hold, skip, help] & resume_on_resolve=True
    st.set_step("preflight", "cg", StepState(status="done", note="cleared"))
    human_report = decorate_handoff_contract(orch.status_report())
    hp = human_report["owner_prompt"]
    assert hp["kind"] == "owner_action" and hp["step"] == "vitals" and hp["resume_on_resolve"] is True
    assert [a["action"] for a in hp["answers"]] == ["done", "hold", "skip", "help"]
    assert "continue remaining release steps automatically" in hp["answers"][0]["description"]


