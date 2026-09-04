"""Release-agent tests — status_email. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




def test_status_email_composes_milestone_dashboard():
    from orchestrator import status_email as SE
    st = _status_state("build_verify")
    res = SE.compose(st, _PHASE_ORDER, ["authsdkrelease@microsoft.com"], changes=[
        {"level": "PATCH", "text": "Update common", "pr": 260}])
    assert res["skip"] is False
    assert res["subject"] == "Auth Client Android SDKs September 2026 Release — Daily Status"
    labels = [m["label"] for m in res["model"]["milestones"]]
    assert "Authenticator app built & tagged" in labels        # A
    assert "Bug Bash Test Plan" in labels                      # D (renamed)
    html = res["html"]
    assert "September 2026 Release" in html and "8.4.2" in html
    assert "UI automation 97.0%" in html                       # B (combined 194/200)
    assert "tree/release/24.6.0" in html                       # C (real release/<version> link)
    assert "Update common" in html                             # change list




def test_status_email_window_boundaries():
    from orchestrator import status_email as SE
    # before Phase 2 → skip
    r0 = SE.compose(_status_state("preflight"), _PHASE_ORDER, [])
    assert r0["skip"] and "before Phase 2" in r0["reason"]
    # Phase 4 → in window
    r4 = SE.compose(_status_state("finalize"), _PHASE_ORDER, [])
    assert r4["skip"] is False
    # Phase 5 → skip (done)
    r5 = SE.compose(_status_state("rollout_start"), _PHASE_ORDER, [])
    assert r5["skip"] and "after Phase 5" in r5["reason"]




def test_is_business_day_skips_weekends_and_holidays():
    from datetime import date
    from tools import bugbash as BB
    assert BB.is_business_day(date(2026, 8, 12)) is True        # Wed
    assert BB.is_business_day(date(2026, 8, 15)) is False       # Sat
    assert BB.is_business_day(date(2026, 12, 25)) is False      # Christmas (Fri)




def test_status_email_command_gates_and_stamp():
    import tempfile, argparse, json, io, contextlib
    from orchestrator import cli_common as _C
    from orchestrator.commands import status_email_cmd as SEC
    from tools import prs
    orig = prs.broker_change_list
    prs.broker_change_list = lambda *a, **k: (True, [], "")     # no network
    with tempfile.TemporaryDirectory() as d:
        st = _status_state("build_verify")
        _C.save_state(st, d, "2026-08")

        def run(as_of, force=False, send_to=None):
            A = argparse.Namespace(runs_root=d, release="2026-08", config=CONFIG,
                                   as_of=as_of, force=force, send_to=send_to)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                SEC.cmd_status_email(A)
            return json.loads(buf.getvalue())
        try:
            assert run("2026-08-15")["skip"] is True                    # Sat → skip
            sent = run("2026-08-12", send_to="me@x.com")                # Wed → send
            assert sent["skip"] is False and sent["to"] == ["me@x.com"]
            assert sent["followup_command"] == "record-status-email"
            # stamp, then idempotent skip
            rA = argparse.Namespace(runs_root=d, release="2026-08", config=CONFIG,
                                    as_of="2026-08-12", final=False)
            SEC.cmd_record_status_email(rA)
            assert _C.load_state(d, "2026-08").last_status_email_date == "2026-08-12"
            assert run("2026-08-12")["reason"] == "already sent today"  # idempotent
        finally:
            prs.broker_change_list = orig

