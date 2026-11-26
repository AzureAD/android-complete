# Copyright (c) Microsoft Corporation. All rights reserved.
"""Tests for phase/step `depends_on` + `available`, unified `release-drainer`,
`drain-runs.jsonl` missed-slot journaling, and Idle-Stall / Cutoff CTA pacing."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import yaml

from orchestrator import automations as A, cli, cli_common as C, drain as D, schedule
from orchestrator.outcomes import Blocked, Done
from orchestrator.state import ReleaseState, StepState
from orchestrator.workflow import WorkflowConfigError, WorkflowDefinition
from tests._context import fresh_orchestrator as Orchestrator
from tests._harness import CONFIG, _active_phase, _ready_state


LA = ZoneInfo("America/Los_Angeles")


# ---------------- Pillar 1: `available` rules & phase root `depends_on` ----------------


def test_available_rule_evaluates_ccd_time_weekdays_forbid_and_hours():
    ccd = date(2026, 9, 9)  # Wednesday

    # 1. `CCD+1 09:00` -> blocked on CCD, blocked at 08:30 on CCD+1, open at 09:00 on CCD+1
    rule_dt = schedule.parse_available_rule("CCD+1 09:00")
    assert rule_dt.anchor_spec == "CCD+1" and rule_dt.not_before_time == "09:00"
    early = schedule.evaluate_availability(
        rule_dt,
        ccd,
        as_of=date(2026, 9, 10),
        now_local=datetime(2026, 9, 10, 8, 30, tzinfo=LA),
    )
    assert not early.available and early.available_at == "2026-09-10T09:00"
    ready = schedule.evaluate_availability(
        rule_dt,
        ccd,
        as_of=date(2026, 9, 10),
        now_local=datetime(2026, 9, 10, 9, 0, tzinfo=LA),
    )
    assert ready.available and ready.available_at is None

    # 2. `forbid_weekdays: ["Friday"]` -> blocked on Friday 2026-09-11, next Monday with weekdays_only
    rule_no_fri = schedule.parse_available_rule({
        "weekdays_only": True,
        "forbid_weekdays": ["Friday"],
        "hours_local": "09:00-17:00",
    })
    fri = schedule.evaluate_availability(
        rule_no_fri,
        ccd,
        as_of=date(2026, 9, 11),
        now_local=datetime(2026, 9, 11, 10, 0, tzinfo=LA),
    )
    assert not fri.available and fri.available_at == "2026-09-14T09:00"
    assert "Friday" in (fri.reason or "")

    # 3. `hours_local: "09:00-17:00"` -> outside window at 18:00 rolls to next permitted business day
    thu_late = schedule.evaluate_availability(
        rule_no_fri,
        ccd,
        as_of=date(2026, 9, 10),
        now_local=datetime(2026, 9, 10, 18, 0, tzinfo=LA),
    )
    # Next day is Friday (forbidden) -> Saturday/Sunday (weekend) -> Monday 2026-09-14T09:00!
    assert not thu_late.available and thu_late.available_at == "2026-09-14T09:00"


def test_phase_root_depends_on_and_step_available_gate_execution(tmp_path):
    cfg = {
        "phases": [
            {
                "id": "p0",
                "name": "Phase Zero",
                "depends_on": [],
                "steps": [
                    {"id": "gate0", "name": "Gate 0", "kind": "approval_gate", "depends_on": []},
                    {"id": "extra", "name": "Extra human", "kind": "human_action", "depends_on": []},
                ],
            },
            {
                "id": "p1",
                "name": "Phase One",
                "depends_on": ["p0"],
                "available": "CCD",
                "steps": [
                    {
                        "id": "roll",
                        "name": "Timed rollout",
                        "kind": "auto",
                        "effect_mode": "read_only",
                        "depends_on": [],
                        "available": "CCD 11:00",
                    }
                ],
            },
        ]
    }
    path = tmp_path / "phases.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    ran: list[str] = []
    mod_roll = SimpleNamespace(
        ID="roll",
        KIND="agent",
        EFFECT_MODE="read_only",
        build=lambda _ctx: (ran.append("roll") or Done("rolled")),
    )
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", timezone="America/Los_Angeles")
    st._checkpoint = lambda: None

    # While p0 is incomplete, p1.roll has prerequisites_met == False
    orch0 = Orchestrator(
        str(path),
        st,
        step_defaults={},
        now=datetime(2026, 9, 9, 12, 0, tzinfo=LA),
        handler_resolver=lambda p, s: mod_roll if (p, s) == ("p1", "roll") else None,
    )
    sel0 = orch0.scheduling()
    assert not sel0.phase("p1").prerequisites_met
    assert not sel0.step("p1", "roll").prerequisites_met

    # Complete p0 -> at 09:00 on CCD, p1 is due, but p1.roll (`available: "CCD 11:00"`) holds as `scheduled`!
    st.set_step("p0", "gate0", StepState(status="done"))
    st.gate_decisions.append({
        "step": "p0.gate0", "decision": "approved", "at": "2026-09-09T15:00:00Z", "by": "owner", "comment": None,
    })
    st.set_step("p0", "extra", StepState(status="done"))

    orch_9am = Orchestrator(
        str(path),
        st,
        step_defaults={},
        now=datetime(2026, 9, 9, 9, 0, tzinfo=LA),
        handler_resolver=lambda p, s: mod_roll if (p, s) == ("p1", "roll") else None,
    )
    actions_9am = orch_9am.run_until_gate()
    assert actions_9am[0].kind == "scheduled"
    assert not ran
    assert orch_9am.scheduling().step("p1", "roll").available_at == "2026-09-09T11:00"

    # At 11:00 on CCD -> `available` evaluates to True and `p1.roll` executes!
    orch_11am = Orchestrator(
        str(path),
        st,
        step_defaults={},
        now=datetime(2026, 9, 9, 11, 0, tzinfo=LA),
        handler_resolver=lambda p, s: mod_roll if (p, s) == ("p1", "roll") else None,
    )
    actions_11am = orch_11am.run_until_gate()
    assert ran == ["roll"]
    assert st.get_step("p1", "roll").status == "done"
    assert actions_11am[-1].kind == "complete"


def test_workflow_rejects_forward_or_unknown_phase_depends_on():
    bad = {
        "phases": [
            {
                "id": "a",
                "name": "A",
                "depends_on": ["b"],
                "steps": [{"id": "s1", "name": "S1", "kind": "human_action"}],
            },
            {
                "id": "b",
                "name": "B",
                "depends_on": [],
                "steps": [{"id": "s2", "name": "S2", "kind": "human_action"}],
            },
        ]
    }
    with pytest.raises(WorkflowConfigError, match="cannot depend on later phase"):
        WorkflowDefinition.compile(bad, resolver=lambda _p, _s: None)


# ---------------- Pillar 2: `release-drainer` cron, `drain-runs.jsonl`, missed slots ----------------


def test_daily_window_cron_supports_same_and_cross_timezone():
    ref = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert A._daily_window_cron(
        "07:00-19:00/2h",
        owner_timezone="America/Los_Angeles",
        scheduler_timezone="America/Los_Angeles",
        now=ref,
    ) == "cron: 0 7-19/2 * * *"
    # PST (UTC-8) 07,09,11,13,15,17,19 -> UTC 15,17,19,21,23,01,03
    assert A._daily_window_cron(
        "07:00-19:00/2h",
        owner_timezone="America/Los_Angeles",
        scheduler_timezone="UTC",
        now=ref,
    ) == "cron: 0 1,3,15,17,19,21,23 * * *"


def test_drain_journal_records_progress_waiting_counts_and_missed_slots(tmp_path, capsys):
    cfg = {
        "phases": [
            {
                "id": "preflight",
                "name": "Pre-flight",
                "depends_on": [],
                "available": "CCD-7",
                "steps": [
                    {
                        "id": "auto_ok",
                        "name": "Auto check",
                        "kind": "auto",
                        "effect_mode": "read_only",
                        "depends_on": [],
                    },
                    {
                        "id": "auto_block",
                        "name": "Blocked check",
                        "kind": "auto",
                        "effect_mode": "read_only",
                        "depends_on": [],
                    },
                    {
                        "id": "human_todo",
                        "name": "Confirm dashboard",
                        "kind": "human_action",
                        "depends_on": ["auto_ok"],
                    },
                ],
            }
        ]
    }
    cfg_path = tmp_path / "phases.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (tmp_path / "readiness.yaml").write_text("version: 1\nitems: []\n", encoding="utf-8")

    mods = {
        "auto_ok": SimpleNamespace(
            ID="auto_ok", KIND="agent", EFFECT_MODE="read_only",
            build=lambda _ctx: Done("all clear"),
        ),
        "auto_block": SimpleNamespace(
            ID="auto_block", KIND="agent", EFFECT_MODE="read_only",
            build=lambda _ctx: Blocked("CG alert #42 active", links=[{"name": "CG", "url": "https://example.test/cg/42"}]),
        ),
    }
    st = ReleaseState(
        release_id="2026-09",
        ccd="2026-09-09",
        timezone="America/Los_Angeles",
        owner_email="owner@example.test",
        created_at=datetime(2026, 9, 3, 6, 50, tzinfo=LA).astimezone(timezone.utc).isoformat(),
    )
    orch = Orchestrator(
        str(cfg_path),
        st,
        readiness_path=str(tmp_path / "readiness.yaml"),
        step_defaults={},
        handler_resolver=lambda _p, sid: mods.get(sid),
    )
    orch.gate.sign()
    C.save_state(st, str(tmp_path), "2026-09")

    # Monkeypatch default resolver for CLI/drain_loop on custom synthetic steps
    import steps as steps_pkg
    orig_get_step = steps_pkg.get_step
    steps_pkg.get_step = lambda p, s: mods.get(s) if p == "preflight" and s in mods else orig_get_step(p, s)
    try:
        # Slot 1: 07:00 AM -> runs `auto_ok` (done) + `auto_block` (blocked), parks on `auto_block` + `human_todo`!
        r1 = D.run_drain(
            str(tmp_path),
            "2026-09",
            str(cfg_path),
            now=datetime(2026, 9, 3, 7, 0, tzinfo=LA),
        )
        assert r1["status"] == "advanced_then_parked"
        assert r1["missed_slot_count"] == 0
        assert r1["entered"]["done"] == 0 and r1["exited"]["done"] == 1
        assert {s["step"]: s["to_status"] for s in r1["executed_steps"]} == {
            "preflight.auto_ok": "done",
            "preflight.auto_block": "blocked",
        }
        assert r1["waiting"]["blocked_count"] == 1
        assert r1["waiting"]["human_input_count"] == 1

        # Simulate machine asleep across 09:00, 11:00, 13:00 -> next scheduled run at 15:00 detects 3 missed slots!
        r2 = D.run_drain(
            str(tmp_path),
            "2026-09",
            str(cfg_path),
            now=datetime(2026, 9, 3, 15, 2, tzinfo=LA),
        )
        assert r2["status"] == "parked"
        assert r2["missed_slot_count"] == 3
        assert len(r2["missed_slots"]) == 3

        # External visualization query via `drain-log --json` at 19:30 detects live missed 17:00 & 19:00 slots too!
        assert cli.main([
            "--config", str(cfg_path),
            "--runs-root", str(tmp_path),
            "drain-log",
            "--release", "2026-09",
            "--now", datetime(2026, 9, 3, 19, 30, tzinfo=LA).isoformat(),
            "--json",
        ]) == 0
        log_out = json.loads(capsys.readouterr().out)
        assert log_out["total_runs"] == 2
        assert log_out["live_missed_slot_count"] == 2
    finally:
        steps_pkg.get_step = orig_get_step


# ---------------- Pillar 3: Idle-Stall Pacing, Owner Check-In Reset & External Cutoffs ----------------


@pytest.mark.real_revision
def test_cta_pacing_immediate_then_cooldown_then_escalates_and_resets_on_ack_hold(tmp_path, capsys):
    rid = "2026-09"
    st = ReleaseState(
        release_id=rid,
        ccd="2026-09-16",  # Wednesday next week -> no external cutoff on 2026-09-09!
        timezone="America/Los_Angeles",
        owner_email="owner@example.test",
        owner_name="Pedro",
    )
    _active_phase(st, "preflight")
    for sid in ("notice", "flight_reminder", "lockdown", "breaking", "cg", "oneauth_access", "cron"):
        st.set_step("preflight", sid, StepState(status="done"))
    # Only `preflight.vitals` (`human_action`) remains -> 1 `human_input` hold!
    C.save_state(st, str(tmp_path), rid)

    sent_emails: list[dict] = []

    def fake_mcp(server: str, tool_name: str, arguments: dict) -> dict:
        sent_emails.append({"server": server, "tool": tool_name, "args": arguments})
        return {"id": f"mail-{len(sent_emails)}", "status": "ok"}

    # 1. Wed 09:00 AM — First time hold is seen -> immediate CTA (`immediate_new_hold`), email sent + Scout bot CTA staged!
    d1 = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 9, 9, 0, tzinfo=LA),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert d1["notification"]["sent"] is True
    assert d1["notification"]["reason"] == "immediate_new_hold"
    assert d1["risk"]["level"] == "normal"
    assert len(sent_emails) == 1
    bot1 = d1["scout_bot_cta"]
    assert bot1 is not None and bot1["action_required"] is True
    # Friendly step name + phase-scoped progress (`7 of 8 phase steps complete`), NEVER raw `preflight.vitals` ID!
    assert "Confirm Play Console vitals & policy status reviewed" in bot1["message"]
    assert "preflight.vitals" not in bot1["message"]
    assert "7 of 8 phase steps complete" in bot1["message"]
    assert "Pre-flight & Code Complete Checklist (7/8)" in bot1["message"]
    assert "/release-agent" in bot1["message"]

    # 2a. Wed 11:00 AM before Scout bot DM is acknowledged -> email NOT resent, unsent `scout_bot_cta` re-surfaced!
    d2_unacked = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 9, 11, 0, tzinfo=LA),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert len(sent_emails) == 1
    assert d2_unacked["notification"]["reason"] == "retry_unsent_scout_bot_cta"
    assert d2_unacked["scout_bot_cta"]["notification_id"] == bot1["notification_id"]

    # Acknowledge Scout bot DM as sent, then re-check at 11:05 AM -> suppressed (`paced_normal_cooldown`)!
    from orchestrator import delivery as deliv
    st_ack, orch_ack = C.load_orch(str(tmp_path), rid, CONFIG, datetime(2026, 9, 9, 11, 1, tzinfo=LA))
    claim = deliv.claim(orch_ack, bot1["notification_id"], bot1["hash"], "release-drainer")
    deliv.result(orch_ack, bot1["notification_id"], claim["execution_id"], "sent", "Scout bot DM sent")
    C.save_state(st_ack, str(tmp_path), rid)

    d2 = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 9, 11, 5, tzinfo=LA),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert d2["notification"]["sent"] is False
    assert d2["notification"]["reason"] == "paced_normal_cooldown"
    assert d2["scout_bot_cta"] is None
    assert len(sent_emails) == 1

    # 3. Thu 07:00 AM (+10h business time: Wed 09:00->19:00 = 10h) -> `attention` (`8-24h`), sends morning CTA!
    d3 = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 10, 7, 0, tzinfo=LA),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert d3["risk"]["level"] == "attention"
    assert d3["notification"]["sent"] is True
    assert d3["notification"]["reason"] == "attention_twice_daily"
    assert len(sent_emails) == 2

    # 4. Thu 03:00 PM (+18h business time, 8h since 7 AM) -> `attention` sends 2nd daily slot at 3 PM!
    d4 = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 10, 15, 0, tzinfo=LA),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert d4["risk"]["level"] == "attention"
    assert d4["notification"]["sent"] is True
    assert len(sent_emails) == 3

    # 5. Fri 11:00 AM (>24h business time: 10h Wed + 12h Thu + 4h Fri = 26h) -> `stalled` (`escalated_stalled_4h`)!
    d5 = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 11, 11, 0, tzinfo=LA),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert d5["risk"]["level"] == "stalled"
    assert d5["notification"]["sent"] is True
    assert d5["notification"]["reason"] == "escalated_stalled_4h"
    assert "Stalled" in sent_emails[-1]["args"]["subject"]

    # 6. Owner checks in via `ack-hold` at Fri 11:30 AM -> resets unattended_business_hours back to `normal`!
    loaded = C.load_state(str(tmp_path), rid)
    loaded.touch_owner_interaction(datetime(2026, 9, 11, 11, 30, tzinfo=LA).astimezone(timezone.utc).isoformat())
    C.save_state(loaded, str(tmp_path), rid)

    d6 = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 11, 15, 0, tzinfo=LA),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert d6["risk"]["level"] == "normal"
    assert d6["risk"]["unattended_business_hours"] == 3.5
    assert d6["notification"]["sent"] is False


def test_external_cutoffs_raise_cutoff_at_risk_only_for_real_deadlines(tmp_path):
    rid = "2026-09"
    st = ReleaseState(
        release_id=rid,
        ccd="2026-09-09",
        timezone="America/Los_Angeles",
        owner_email="owner@example.test",
    )
    _active_phase(st, "ccd")
    for sid in ("final_reminder", "pr_reminder", "localization"):
        st.set_step("ccd", sid, StepState(status="done"))
    st.set_step("ccd", "localization_pr", StepState(status="blocked", note="PR #99 unmerged"))
    C.save_state(st, str(tmp_path), rid)

    # At 11:00 AM on CCD -> normal (8+ hours left before 11 PM branch cut)
    d_am = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 9, 11, 0, tzinfo=LA),
    )
    assert d_am["risk"]["level"] == "normal"
    assert d_am["risk"]["external_cutoff"] is None

    # At 3:00 PM (15:00) on CCD -> `cutoff_at_risk` (`CCD 11:00 PM branch cut`)!
    d_pm = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 9, 15, 0, tzinfo=LA),
    )
    assert d_pm["risk"]["level"] == "cutoff_at_risk"
    assert "11:00 PM branch cut" in d_pm["risk"]["external_cutoff"]

    # Fluid phase (`bug_bash`) with NO upcoming CCOA freeze -> NEVER artificially marked `cutoff_at_risk`!
    _active_phase(st, "bug_bash")
    for sid in ("clone_plans_broker", "clone_plans_auth", "ui_test_status", "distribute_tests",
                "send_invite", "activate_chat", "bugbash_updates", "notify_native_auth"):
        st.set_step("bug_bash", sid, StepState(status="done"))
    C.save_state(st, str(tmp_path), rid)
    d_bb = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 15, 11, 0, tzinfo=LA),
    )
    assert d_bb["risk"]["external_cutoff"] is None
    assert d_bb["risk"]["level"] == "normal"

    # When `preflight.lockdown` recorded an upcoming CCOA freeze within <= 2 business days -> `cutoff_at_risk`!
    st.set_step("preflight", "lockdown", StepState(status="done", data={"next_freeze_start": "2026-09-17"}))
    C.save_state(st, str(tmp_path), rid)
    d_freeze = D.run_drain(
        str(tmp_path), rid, CONFIG,
        now=datetime(2026, 9, 15, 11, 0, tzinfo=LA),
    )
    assert d_freeze["risk"]["level"] == "cutoff_at_risk"
    assert "CCOA freeze" in d_freeze["risk"]["external_cutoff"]


@pytest.mark.real_revision
def test_scheduled_drain_on_unsigned_entry_gate_runs_python_verifiers_and_finalizes(
    tmp_path, monkeypatch, capsys
):
    """Regression test: `--scheduled drain` on a newly initialized release at the Entry Gate
    (`not orch.gate.signed`) must NOT fail on `ReadinessGate.checklist()`; it runs Python
    verifiers, surfaces Scout `pending_auto_checks` in `pending_host_work`, and finalizes
    cleanly into `drain-runs.jsonl` with accurate `blocked_count` + `human_input_count`."""
    from tests._harness import _m365_verifier_result, _stub_build_defs
    from tools import checks
    import phases.readiness_verifiers as rv

    monkeypatch.setattr(checks, "read_pipeline_variable", lambda *a, **k: (True, None, "offline"))
    _stub_build_defs("pass")
    rv.REGISTRY["m365_capabilities"] = _m365_verifier_result("pass")
    monkeypatch.setitem(
        rv.REGISTRY, "mcp_servers", lambda _it: rv.VerifyResult("pass", "stubbed mcp servers")
    )

    rid = "2026-11"
    assert cli.main([
        "--runs-root", str(tmp_path),
        "init",
        "--release", rid,
        "--ccd", "2026-11-25",
        "--owner-email", "owner@example.test",
        "--timezone", "America/Los_Angeles",
        "--json",
    ]) == 0
    capsys.readouterr()

    # Step 1: `--scheduled drain` while Entry Gate is unsigned -> runs Python verifiers & returns Scout host checks!
    assert cli.main([
        "--scheduled",
        "--runs-root", str(tmp_path),
        "drain",
        "--release", rid,
        "--trigger", "scheduled",
        "--now", datetime(2026, 11, 18, 9, 0, tzinfo=LA).isoformat(),
        "--json",
    ]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "awaiting_host_work"
    assert first["finalized"] is False
    host_ids = {item["id"] for item in first["pending_host_work"]}
    assert host_ids == {"ccd_confirmed", "teams_notify", "adx_access", "oncall_now"}
    assert all(item["probe"] and item["command"] for item in first["pending_host_work"])

    # Simulate Scout recording those 4 host checks (3 pass + 1 fail on adx_access)
    for iid, st_val, detail in (
        ("ccd_confirmed", "pass", "CCD reconciled"),
        ("teams_notify", "pass", "Scout bot reachable"),
        ("adx_access", "fail", "Kusto connector unavailable"),
        ("oncall_now", "pass", "Not primary on-call"),
    ):
        assert cli.main([
            "--scheduled",
            "--runs-root", str(tmp_path),
            "record-check",
            "--release", rid,
            "--item", iid,
            "--status", st_val,
            "--detail", detail,
        ]) == 0
    capsys.readouterr()

    # Step 2: `--finalize-run <run_id>` -> writes `drain-runs.jsonl` with 1 `blocked` (`adx_access`) + 4 `human_input` attestations!
    assert cli.main([
        "--scheduled",
        "--runs-root", str(tmp_path),
        "drain",
        "--release", rid,
        "--finalize-run", first["run_id"],
        "--now", datetime(2026, 11, 18, 9, 1, tzinfo=LA).isoformat(),
        "--json",
    ]) == 0
    finalized = json.loads(capsys.readouterr().out)
    assert finalized["finalized"] is True
    assert finalized["entered"]["phase"] == "readiness"
    assert finalized["waiting"]["blocked_count"] == 1
    assert finalized["waiting"]["human_input_count"] == 4
    blocked_item = next(i for i in finalized["waiting"]["items"] if i["category"] == "blocked")
    assert blocked_item["step"] == "readiness.adx_access"
    assert "Kusto connector unavailable" in blocked_item["reason"]
    assert len(D.read_drain_runs(str(tmp_path), rid)) == 1


def test_lockdown_overlap_renders_untruncated_details_and_ccoa_specific_owner_prompt(
    tmp_path, capsys
):
    """Regression test: blocked `preflight.lockdown` must NOT truncate `Details` mid-sentence
    (`Shift CCD past…`), must attach the clickable CCOA schedule link, and must present
    CCOA-specific date-review options in `owner_prompt` instead of generic `Override`."""
    from orchestrator import render
    from orchestrator.commands import lockdown as lk
    from orchestrator.dispatch import decorate_handoff_contract

    rid = "2026-11"
    st = ReleaseState(
        release_id=rid,
        ccd="2026-11-19",
        timezone="America/Los_Angeles",
        owner_email="owner@example.test",
    )
    _active_phase(st, "preflight")
    for sid in ("notice", "flight_reminder", "breaking", "cg", "oneauth_access", "cron"):
        st.set_step("preflight", sid, StepState(status="done", note="ok"))
    C.save_state(st, str(tmp_path), rid)

    args = SimpleNamespace(
        runs_root=str(tmp_path),
        release=rid,
        config=CONFIG,
        as_of="2026-11-18",
        periods_json=json.dumps([
            {
                "name": "[IDNA] Black Friday / Cyber Monday",
                "environment": "Production",
                "start": "2026-11-24",
                "end": "2026-12-01",
            }
        ]),
    )
    assert lk.cmd_check_lockdown(args) == 0
    capsys.readouterr()

    loaded = C.load_state(str(tmp_path), rid)
    orch = Orchestrator(CONFIG, loaded, step_defaults={}, as_of=date(2026, 11, 18))
    rep = decorate_handoff_contract(orch.status_report())
    md = render.status_view(rep)

    # 1. No mid-sentence ellipsis (`Shift CCD past…`); full message + clickable CCOA link present!
    current_phase_table = md.split("### ▶ Current phase")[1].split("\n---")[0]
    assert "…" not in current_phase_table
    assert "shift CCD outside the freeze (set-ccd) and re-check" in current_phase_table
    assert "https://prod.change-manager.msidentity.com/ccoa-periods" in current_phase_table

    # 2. `owner_prompt` explicitly instructs checking CCOA dates with domain-specific options!
    prompt = rep["owner_prompt"]
    assert prompt["kind"] == "blocked_step" and prompt["step"] == "lockdown"
    assert "https://prod.change-manager.msidentity.com/ccoa-periods" in prompt["question"]
    assert "[IDNA] Black Friday / Cyber Monday" in prompt["question"]
    titles = [a["title"] for a in prompt["answers"]]
    assert "Shift CCD outside lockdown (set new date)" in titles
    assert "Checked CCOA dates — rollout avoids freeze" in titles
    assert "Waive — approved CCOA lockdown exception" in titles
    assert "Keep blocked while checking CCOA dates" in titles
    assert [a["action"] for a in prompt["answers"]] == [
        "retry", "rerun", "complete-manually", "override", "hold",
    ]


def test_release_state_save_retries_transient_windows_permission_error(tmp_path, monkeypatch):
    """Regression test for Windows AV/file-watcher transient `PermissionError` on `os.replace`."""
    import os as _os
    real_replace = _os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied", src, 5, dst)
        return real_replace(src, dst)

    monkeypatch.setattr("orchestrator.state.os.replace", flaky_replace)
    st = ReleaseState(release_id="2026-11", ccd="2026-11-19")
    target = tmp_path / "release-state.json"
    st.save(str(target))
    assert calls["n"] == 3
    assert ReleaseState.load(str(target)).release_id == "2026-11"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_phase2_fixes_ccd_filtering_inprogress_orchestrator_telemetry_and_html_rc_email(monkeypatch):
    """Verify all Phase 2 fixes:
    1. `find_checker_runs` ignores earlier-in-month triggers before `ccd` and catches end-of-month
       11 PM PT runs that cross into next month in UTC.
    2. `find_orchestrator_run` prefers exact `AuthenticatorBranch=release-<ccd>` over older runs.
    3. `orchestrator_health` returns `in_progress` (not `blocked`) while pre-gate stages are running.
    4. `telemetry_verify` accepts `-rc<id>-ecs` and bare 4-digit APK versions while rejecting Broker versions.
    5. `rc_report_notify` includes `telemetry_verify` row counts AND attaches the full HTML email report.
    """
    from orchestrator.commands.step_action import prepare_step
    from steps.build_verify import _common as K, orchestrator_health as OH, rc_report_notify as RN, telemetry_verify as TV
    from tests import _fixtures as mockctx
    from tests._context import context as _ctx, invoke as _inv, pipeline as _pipe
    from tests._mrwp_evidence import PROD, snapshot
    from tools.pipelines import orchestrator as PO

    # 1. Checker runs: Nov 11 vs custom CCD Nov 19 + Nov 30 11:00 PM PT (`2026-12-01T07:00:00Z`)
    monkeypatch.setattr(PO._pp, "_az_json", lambda *a, **k: (True, [
        {"id": 10, "queueTime": "2026-11-11T07:00:00Z"},
        {"id": 20, "queueTime": "2026-11-20T07:00:00Z"},
        {"id": 30, "queueTime": "2026-12-01T07:00:00Z"},
    ], ""))
    ok_c1, runs_nov19, _ = PO.find_checker_runs("org", "proj", 3155, "2026-11", ccd="2026-11-19", timezone_name="America/Los_Angeles")
    assert ok_c1 and [r["id"] for r in runs_nov19] == [30, 20]  # 10 (Nov 11) excluded!
    ok_c2, runs_nov30, _ = PO.find_checker_runs("org", "proj", 3155, "2026-11", ccd="2026-11-30", timezone_name="America/Los_Angeles")
    assert ok_c2 and [r["id"] for r in runs_nov30] == [30]      # Dec 1 UTC = Nov 30 11 PM PT included!

    # 2. Orchestrator lookup: ignores older Nov 11 run when CCD is Nov 19
    monkeypatch.setattr(PO._pp, "_az_json", lambda *a, **k: (True, [
        {"id": 100, "queueTime": "2026-11-11T08:00:00Z", "tags": ["AuthenticatorBranch=release-2026-11-11"]},
        {"id": 200, "queueTime": "2026-11-19T08:00:00Z", "tags": ["AuthenticatorBranch=release-2026-11-19"]},
    ], ""))
    ok_o, orun, _ = PO.find_orchestrator_run("org", "proj", 2828, "2026-11", ccd="2026-11-19")
    assert ok_o and orun["id"] == 200

    # 3. `orchestrator_health` returns `in_progress` while `Trigger RC Testing` is running (`result=None`)
    st = ReleaseState(release_id="2026-11", ccd="2026-11-19", owner_email="owner@example.test")
    running_stages = [
        {"name": "Validate Branch and Versions availability", "state": "completed", "result": "succeeded"},
        {"name": "Create Release Branches", "state": "completed", "result": "succeeded"},
        {"name": "Trigger RC Testing", "state": "inProgress", "result": None},
        {"name": "Remove RC Tags", "state": "pending", "result": None},
    ]
    run_payload = {
        "id": 200,
        "tags": [
            "AuthenticatorBranch=release-2026-11-19",
            "NextCommonVersion=1.0.0",
            "NextMsalVersion=2.0.0",
            "NextBrokerVersion=3.0.0",
        ],
    }
    res_ip = OH.build(_ctx(st, inputs={"run": run_payload, "stages": running_stages}))
    assert res_ip.kind == "in_progress" and "Trigger RC Testing" in res_ip.note

    # 4. `telemetry_verify` version extraction handles `-rc<id>-ecs` and bare `6.2611.1234`
    from tests._auth_evidence import auth_test
    _pipe(K.stash_auth, st, 1, {
        "build": {"run_id": "555", "rc": 1, "build_number": "6.2611.1234-rc555-ecs", "complete": True, "result": "succeeded"},
        "test": auth_test({
            "Firebase Test Lab - UIAutomator E2E Tests": {"present": True, "passed": 98, "failed": 2, "total": 100, "pct": 98.0},
            "Firebase Test Lab - Monthly UI Tests": {"present": True, "passed": 100, "failed": 0, "total": 100, "pct": 100.0},
        }, rc=1),
        "verdict": "clean",
    })
    with mockctx.active({}):
        tv_out = _inv(TV.build, st)
    assert tv_out.kind == "needs_skill" and tv_out.payload["version"] == "6.2611.1234"

    # 5. `rc_report_notify` includes `Telemetry (Kusto)` line AND attaches companion HTML email!
    from tests._harness import _ready_for_rc_report, _seed_rc_pipeline
    st2 = ReleaseState(release_id="2026-11", ccd="2026-11-19", owner_email="owner@example.test")
    _ready_for_rc_report(st2)
    _seed_rc_pipeline(st2, {"total": 100, "passed": 98, "failed": 2}, {"total": 100, "passed": 100, "failed": 0})
    st2.set_step("build_verify", "telemetry_verify", StepState(
        status="done",
        note="telemetry flowing for 6.2611.1234 — 42 row(s)",
        data={"version": "6.2611.1234", "rows": 42},
    ))
    orch = Orchestrator(CONFIG, st2, step_defaults={}, as_of=date(2026, 11, 20))
    prep = prepare_step(SimpleNamespace(phase="build_verify", step="rc_report_notify", release="2026-11"), st2, orch)
    assert "Telemetry (Kusto): 42 row(s) for APK 6.2611.1234" in prep["mcp_arguments"]["message"]
    assert "_also_email" not in prep["mcp_arguments"]
    assert [n["tool"] for n in prep["notifications"]] == ["m_send_teams_message", "workiq_send_email"]
    assert prep["notifications"][1]["payload"]["isHtml"] is True
    assert "Telemetry (Kusto): 42 row(s)" in prep["notifications"][1]["payload"]["body"]




