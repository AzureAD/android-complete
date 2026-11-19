"""Release-agent tests — status_email. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403


def _compose(state, recipients, changes=None):
    from orchestrator import status_email as SE

    selection = Orchestrator(CONFIG, state).scheduling()
    return SE.compose(
        state,
        _PHASE_ORDER,
        recipients,
        changes=changes,
        selection=selection,
    )




def test_status_email_composes_milestone_dashboard():
    from orchestrator import status_email as SE
    st = _status_state("build_verify")
    res = _compose(st, ["authsdkrelease@microsoft.com"], changes=[
        {"level": "PATCH", "text": "Update common", "pr": 260}])
    assert res["skip"] is False
    assert res["subject"] == "Auth Client Android SDKs September 2026 Release — Daily Status"
    labels = [m["label"] for m in res["model"]["milestones"]]
    assert "Authenticator app built & tagged" in labels        # A
    assert "Bug Bash Test Plan" in labels                      # D (renamed)
    html = res["html"]
    assert "September 2026 Release" in html and "8.4.2" in html
    assert "UI+Stress 97.0%" in html                            # B (combined 194/200)
    assert "tree/release/24.6.0" in html                       # C (real release/<version> link)
    assert "Update common" in html                             # change list


def test_status_email_uses_calendar_icon_for_scheduled_milestones():
    from orchestrator import status_email as SE

    assert SE._PILL["scheduled"][0] == "📅 Scheduled"




def test_status_email_window_boundaries():
    from orchestrator import status_email as SE
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
    orch = Orchestrator(CONFIG, st, mocks={})
    result = SE.compose(
        st,
        _PHASE_ORDER,
        ["authsdkrelease@microsoft.com"],
        selection=orch.scheduling(),
    )
    milestone = next(
        item
        for item in result["model"]["milestones"]
        if item["label"] == "Manual Test Pass Complete"
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
    monkeypatch.setattr(prs, "broker_change_list", lambda *a, **k: (True, [], ""))
    st = _status_state("finalize")
    _active_phase(st, "finalize")
    orch = Orchestrator(CONFIG, st, mocks={"finalize.final_status_email": {"send_to": "me@x.com"}})
    outcome = final_status_email.build(orch.context("finalize", "final_status_email"))
    assert outcome.tool == "workiq_send_email"
    assert outcome.payload["to"] == ["me@x.com"]
    assert "Final Status" in outcome.payload["subject"]
