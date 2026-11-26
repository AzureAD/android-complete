"""Release-agent tests — unified 4-milestone partner status email & 24h monitor."""
from datetime import datetime, timedelta, timezone

from tests._harness import *  # noqa: F401,F403


def _compose(state, recipients, changes=None, **kwargs):
    from orchestrator import status_email as SE

    selection = Orchestrator(CONFIG, state).scheduling()
    return SE.compose(
        state,
        _PHASE_ORDER,
        recipients,
        changes=changes,
        selection=selection,
        **kwargs,
    )


def test_status_email_composes_4_milestone_dashboard():
    st = _status_state("build_verify")
    res = _compose(st, ["authsdkrelease@microsoft.com", "androididentity@microsoft.com"])
    assert res["skip"] is False
    assert res["subject"] == "Auth Client Android SDKs September 2026 Release — Daily Status"
    labels = [m["label"] for m in res["model"]["milestones"]]
    assert labels == [
        "Code Complete",
        "Automated Test Pass",
        "Manual Test Pass",
        "Final Release builds published",
    ]
    statuses = [m["status"] for m in res["model"]["milestones"]]
    assert statuses[0] == "complete"
    assert statuses[1] == "in_progress"
    html = res["html"]
    assert "September 2026 Release" in html and "8.4.2" in html
    assert "UI+Stress 97.0%" in html
    assert "tree/release/24.6.0" in html
    assert "Authenticator app built & tagged" not in html


def test_unified_template_across_p0_p1_and_p4_final():
    """P0 and P1 omit versions/changelogs and drop the dead cherry-pick link; P4 Final marks
    all 4 milestones complete and renders Common, MSAL, and Broker changelogs."""
    from steps.preflight import notice as P0
    from steps.ccd import final_reminder as P1
    from steps.finalize import final_status_email as P4
    from tests._context import context as _ctx, fresh_orchestrator

    st = _status_state("preflight")
    p0_out = P0.build(_ctx(st, model=P0.BuildParameters, now=datetime.fromisoformat("2026-08-04T09:00:00-07:00")))
    assert p0_out.payload["to"] == ["authsdkrelease@microsoft.com", "androididentity@microsoft.com"]
    assert "SDK Versions" not in p0_out.payload["body"]
    assert "Changelogs" not in p0_out.payload["body"]
    assert "cherry-pick-to-hotfix-guidelines" not in p0_out.payload["body"]
    assert "hotfix cherry-pick process and EM approval" in p0_out.payload["body"]
    assert "📅 Scheduled" in p0_out.payload["body"]
    assert "ETA 08/11/2026" in p0_out.payload["body"]

    p1_out = P1.build(_ctx(st, model=P1.BuildParameters, now=datetime.fromisoformat("2026-08-11T09:00:00-07:00")))
    assert p1_out.payload["to"] == ["authsdkrelease@microsoft.com", "androididentity@microsoft.com"]
    assert "(Today)" in p1_out.payload["subject"]
    assert "<strong>Today</strong>" in p1_out.payload["body"]
    assert "🟡 In Progress" in p1_out.payload["body"]
    assert "SDK Versions" not in p1_out.payload["body"]

    st_fin = _status_state("finalize")
    _active_phase(st_fin, "finalize")
    changelogs = {
        "common": [{"level": "MINOR", "text": "Common telemetry update", "pr": 3301}],
        "msal": [{"level": "PATCH", "text": "Fix MsalArgumentException", "pr": 2621}],
        "broker": [{"level": "MAJOR", "text": "Broker IPC hardening", "pr": 363}],
    }
    orch = fresh_orchestrator(
        CONFIG, st_fin,
        step_defaults={"finalize.final_status_email": {"send_to": "me@x.com", "changelogs": changelogs}},
    )
    p4_out = P4.build(orch.context(
        "finalize", "final_status_email",
        inputs={"send_to": "me@x.com", "changelogs": changelogs},
    ))
    assert p4_out.payload["to"] == ["me@x.com"]
    assert p4_out.payload["subject"].startswith("[TEST → me] Auth Client Android SDKs")
    assert "Final Status" in p4_out.payload["subject"]
    body = p4_out.payload["body"]
    assert body.count("✅ Complete") == 4
    assert "Common (24.6.0)" in body and "Common telemetry update" in body
    assert "MSAL (8.4.2)" in body and "Fix MsalArgumentException" in body
    assert "Broker (16.5.0)" in body and "Broker IPC hardening" in body


def test_status_email_uses_calendar_icon_for_scheduled_milestones():
    from orchestrator import status_email as SE

    assert SE._PILL["scheduled"][0] == "📅 Scheduled"


def test_status_email_window_boundaries():
    # before Phase 2 → skip
    r0 = _compose(_status_state("preflight"), [])
    assert r0["skip"] and "before Phase 2" in r0["reason"]
    # Phase 4 → in window
    r4 = _compose(_status_state("finalize"), [])
    assert r4["skip"] is False
    # Phase 5 → skip (done)
    r5 = _compose(_status_state("rollout_start"), [])
    assert r5["skip"] and "after Phase 5" in r5["reason"]


def test_status_email_projection_does_not_complete_legacy_skipped_gate():
    from orchestrator import status_email as SE
    from orchestrator.state import StepState

    st = _status_state("bug_bash")
    _active_phase(st, "bug_bash")
    st.set_step(
        "bug_bash",
        "bugbash_complete",
        StepState(status="skipped", note="legacy override"),
    )
    orch = Orchestrator(CONFIG, st, step_defaults={})
    result = SE.compose(
        st,
        _PHASE_ORDER,
        ["authsdkrelease@microsoft.com"],
        selection=orch.scheduling(),
    )
    milestone = next(
        item
        for item in result["model"]["milestones"]
        if item["label"] == "Manual Test Pass"
    )
    assert result["skip"] is False
    assert milestone["status"] != "complete"


def test_is_business_day_skips_weekends_and_holidays():
    from datetime import date
    from tools import bugbash as BB
    assert BB.is_business_day(date(2026, 8, 12)) is True        # Wed
    assert BB.is_business_day(date(2026, 8, 15)) is False       # Sat
    assert BB.is_business_day(date(2026, 12, 25)) is False      # Christmas (Fri)


def test_final_status_email_step_composes_closing_snapshot(monkeypatch):
    from steps.finalize import final_status_email
    from tests._context import fresh_orchestrator as Orchestrator
    from tools import prs
    monkeypatch.setattr(prs, "repo_change_list", lambda *a, **k: (True, [], ""))
    st = _status_state("finalize")
    _active_phase(st, "finalize")
    orch = Orchestrator(CONFIG, st, step_defaults={"finalize.final_status_email": {"send_to": "me@x.com"}})
    outcome = final_status_email.build(orch.context(
        "finalize", "final_status_email", inputs={"send_to": "me@x.com"}))
    assert outcome.tool == "workiq_send_email"
    assert outcome.payload["to"] == ["me@x.com"]
    assert "Final Status" in outcome.payload["subject"]


def test_partner_24h_monitor_eod_blocked_and_offline_catchup(tmp_path, monkeypatch):
    """Verify the P2-P4 24h partner status monitor:
      1) Global `status_email.send_to` mock redirects delivery away from real DLs.
      2) Sends at EOD (`17:30` local) with `Automated Test Pass = In Progress`.
      3) Next morning (`09:30` local) after >=8 business hours with zero progress, flips
         `Automated Test Pass` to `🔴 Blocked` and sends on `milestone_state_changed`.
      4) If offline for >20h across a missed afternoon, recovers automatically at `09:00` (`offline_catchup_24h`).
    """
    from orchestrator import cli_common as C, environment as env_mod, status_email as SE
    from tests._context import fresh_orchestrator

    sent_emails = []

    def fake_mcp(server, tool_name, arguments):
        sent_emails.append((server, tool_name, arguments))
        return {"status": "sent"}

    monkeypatch.setenv("RELEASE_AGENT_PROFILE", "test")
    monkeypatch.setattr(env_mod, "load_step_defaults", lambda *_a, **_kw: {})

    runs_root = str(tmp_path)
    st = _status_state("build_verify")
    st.owner_email = "pedroro@microsoft.com"
    _active_phase(st, "build_verify")
    st.timezone = "America/Los_Angeles"

    # 1) Day 1 at 17:30 PT (Wed Aug 12, 2026) -> EOD heartbeat sends `🟡 In Progress` to `@owner` in test profile!
    t1 = datetime.fromisoformat("2026-08-12T17:30:00-07:00")
    orch1 = fresh_orchestrator(CONFIG, st, step_defaults={}, now=t1)
    C.save_state(st, runs_root, st.release_id)
    r1 = SE.evaluate_and_deliver_partner_status(
        orch1,
        waiting={"blocked_count": 0},
        risk={"level": "normal", "unattended_business_hours": 2.0},
        runs_root=runs_root,
        config_path=CONFIG,
        now_utc=t1.astimezone(timezone.utc),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert r1["sent"] is True and r1["reason"] == "eod_daily_heartbeat"
    assert len(sent_emails) == 1
    assert sent_emails[-1][2]["to"] == ["pedroro@microsoft.com"]
    assert sent_emails[-1][2]["subject"].startswith("[TEST] ")
    assert "🟡 In Progress" in sent_emails[-1][2]["body"]

    # 2) Same evening at 19:00 PT -> already sent today with unchanged milestone state -> skipped!
    t1b = datetime.fromisoformat("2026-08-12T19:00:00-07:00")
    orch1b = fresh_orchestrator(CONFIG, st, step_defaults={}, now=t1b)
    r1b = SE.evaluate_and_deliver_partner_status(
        orch1b,
        waiting={"blocked_count": 0},
        risk={"level": "normal", "unattended_business_hours": 3.5},
        runs_root=runs_root,
        config_path=CONFIG,
        now_utc=t1b.astimezone(timezone.utc),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert r1b["sent"] is False
    assert len(sent_emails) == 1

    # 3) Next day at 16:00 PT (Thu Aug 13, >8 business hours with ZERO step progress) -> flips to `🔴 Blocked`!
    t2 = datetime.fromisoformat("2026-08-13T16:00:00-07:00")
    orch2 = fresh_orchestrator(CONFIG, st, step_defaults={}, now=t2)
    r2 = SE.evaluate_and_deliver_partner_status(
        orch2,
        waiting={"blocked_count": 0},
        risk={"level": "attention", "unattended_business_hours": 9.0},
        runs_root=runs_root,
        config_path=CONFIG,
        now_utc=t2.astimezone(timezone.utc),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert r2["sent"] is True and r2["reason"] == "milestone_state_changed"
    assert len(sent_emails) == 2
    assert "🔴 Blocked" in sent_emails[-1][2]["body"]

    # 4) Offline all Friday afternoon -> wakes up Monday morning 09:00 PT (>20h elapsed) -> `offline_catchup_24h`!
    t3 = datetime.fromisoformat("2026-08-17T09:00:00-07:00")
    orch3 = fresh_orchestrator(CONFIG, st, step_defaults={}, now=t3)
    r3 = SE.evaluate_and_deliver_partner_status(
        orch3,
        waiting={"blocked_count": 0},
        risk={"level": "stalled", "unattended_business_hours": 24.0},
        runs_root=runs_root,
        config_path=CONFIG,
        now_utc=t3.astimezone(timezone.utc),
        deliver=True,
        mcp_caller=fake_mcp,
    )
    assert r3["sent"] is True and r3["reason"] == "offline_catchup_24h"
    assert len(sent_emails) == 3


def test_partner_24h_monitor_routes_via_test_vs_prod_environment_profile(tmp_path, monkeypatch):
    """Under `active_profile: test`, partner-status automatically resolves `@owner` and `[TEST] `;
    under `active_profile: prod`, it uses the configured partner DLs."""
    from orchestrator import cli_common as C, environment as env_mod, status_email as SE
    from tests._context import fresh_orchestrator

    monkeypatch.setattr(env_mod, "load_step_defaults", lambda *_a, **_kw: {})
    for profile, expected_to, expected_prefix in (
        ("test", ["pedroro@microsoft.com"], "[TEST] "),
        ("prod", ["authsdkrelease@microsoft.com", "androididentity@microsoft.com"], "Auth Client Android SDKs"),
    ):
        monkeypatch.setenv("RELEASE_AGENT_PROFILE", profile)
        sent_emails = []
        runs_root = str(tmp_path / profile)
        st = _status_state("build_verify")
        st.owner_email = "pedroro@microsoft.com"
        _active_phase(st, "build_verify")
        st.timezone = "America/Los_Angeles"
        t = datetime.fromisoformat("2026-11-25T09:19:00-08:00")
        orch = fresh_orchestrator(CONFIG, st, step_defaults={}, now=t)
        C.save_state(st, runs_root, st.release_id)

        res = SE.evaluate_and_deliver_partner_status(
            orch,
            waiting={"blocked_count": 2},
            risk={"level": "stalled", "unattended_business_hours": 10.0},
            runs_root=runs_root,
            config_path=CONFIG,
            now_utc=t.astimezone(timezone.utc),
            deliver=True,
            mcp_caller=lambda s, tool, args: (sent_emails.append((s, tool, args)) or {"status": "sent"}),
        )
        assert res["sent"] is True
        assert len(sent_emails) == 1
        assert sent_emails[0][2]["to"] == expected_to
        assert sent_emails[0][2]["subject"].startswith(expected_prefix)
