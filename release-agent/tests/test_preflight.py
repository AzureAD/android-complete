"""Release-agent tests — preflight. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




def test_next_json_emits_status_report_with_scout_pending(capsys):
    """`next --json` advances THEN prints the status report as JSON (same shape as
    `status --json`, carrying `scout_pending`) — so a caller advances + reads the pending
    scout steps in ONE call. Plain `next` still prints the human advance block, not JSON."""
    import json as _json, tempfile
    from orchestrator import cli as _cli
    with tempfile.TemporaryDirectory() as rr:
        R = "2099-05"
        _cli.main(["--runs-root", rr, "init", "--release", R,
                   "--owner-email", "t@example.com", "--owner-name", "T"])
        capsys.readouterr()                                   # drop init output
        rc = _cli.main(["--runs-root", rr, "next", "--release", R, "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        rep = _json.loads(out)                                # must be valid JSON
        assert rep["release_id"] == R
        assert "scout_pending" in rep                         # the field the push-loop reads
        # plain `next` (no --json) prints the advance block, NOT json
        _cli.main(["--runs-root", rr, "next", "--release", R])
        plain = capsys.readouterr().out
        try:
            _json.loads(plain)
            assert False, "plain `next` should not emit JSON"
        except _json.JSONDecodeError:
            pass


def test_unopened_phase_shows_all_steps_scheduled_uniformly():
    """REGRESSION: before a phase opens, its scout steps must NOT render 'Scout runs this —
    automatic' while its agent/human steps render 'Not open yet' — that mix confused the
    reader. An unopened phase shows EVERY not-yet-run step uniformly as 'Not open yet'
    (state 'scheduled'). Preflight opens CCD-7; as-of CCD-14 it's the current phase but not due."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-06-24")           # CCD-14 → preflight is current but NOT yet open
    r = orch.status_report()
    assert r["active_phase"]["id"] == "preflight"
    assert r["active_phase"]["due"] is False
    states = {s["state"] for s in r["current_steps"]}
    # uniform — no 'scout'/'auto'/'pending' mix, everything is 'scheduled'
    assert states == {"scheduled"}, states
    view = render.status_view(r)
    assert "Not open yet" in view
    assert "Scout runs this" not in view          # the confusing early label is gone


def test_open_phase_still_differentiates_step_states():
    """The uniform 'Not open yet' only applies BEFORE a phase opens. Once open, states still
    differentiate: scout steps → 'scout' (Scout runs this), so the fix didn't flatten a live phase."""
    st, orch = _ccd_orch("2026-07-02")            # Phase 0 open
    r = orch.status_report()
    assert r["active_phase"]["id"] == "preflight"
    assert r["active_phase"]["due"] is True
    states = {s["state"] for s in r["current_steps"]}
    assert states != {"scheduled"}                # no longer uniform once open
    assert "scout" in states                      # scout steps now labelled as scout


def test_ccd_cron_pins_to_exact_date():
    """_ccd_cron builds a cron 'M H D Mo *' targeting the CCD's day+month+time, so a
    one-shot fires ON the CCD — never the next matching weekday (the early-fire bug)."""
    from orchestrator import automations as A
    from datetime import date
    assert A._ccd_cron(date(2026, 8, 26), "09:00") == "cron: 0 9 26 8 *"
    assert A._ccd_cron(date(2026, 8, 26), "12:00") == "cron: 0 12 26 8 *"
    assert A._ccd_cron(date(2026, 12, 9), "13:30") == "cron: 30 13 9 12 *"
    # missing/invalid inputs → None (caller falls back / skips)
    assert A._ccd_cron(None, "09:00") is None
    assert A._ccd_cron(date(2026, 8, 26), "") is None
    assert A._ccd_cron(date(2026, 8, 26), "nonsense") is None




def test_status_surfaces_agent_result_notes_and_wiki_link():
    """Agent results are stored as the step note and surfaced in status: the
    Details column shows each step's outcome; multi-line reports expand below."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-07-02")          # Phase 0 open
    orch.run_until_gate()                        # runs breaking/cg/cron (set notes)
    r = orch.status_report()
    steps = {s["id"]: s for s in r["current_steps"]}
    assert steps["cg"].get("note")
    view = render.status_view(r)
    assert "| Details |" in view                 # the third column exists
    assert "Component Governance" in view        # cg's real report note surfaces




def test_parse_breaking_only_scans_vnext_section():
    from steps.preflight.breaking import parse_breaking
    hits = parse_breaking(_SAMPLE_CHANGELOG, section="vNext", tag="[MAJOR]")
    assert len(hits) == 1
    assert "(#2)" in hits[0] and "(#0)" not in hits[0]
    assert hits[0].startswith("[MAJOR]")          # leading "- " bullet stripped (no doubling)




def test_parse_breaking_none_when_no_major():
    from steps.preflight.breaking import parse_breaking
    txt = "vNext\n----------\n- [MINOR] x (#1)\nVersion 1.0.0\n----------\n"
    assert parse_breaking(txt) == []




def test_breaking_agent_detects_and_drafts():
    from steps.preflight import breaking, cg, cron
    from steps.preflight import breaking as _bk
    orig = _bk._fetch_text
    _bk._fetch_text = lambda *a, **k: _SAMPLE_CHANGELOG
    try:
        st = ReleaseState(release_id="2026-08")
        r = breaking.run("preflight", {"id": "breaking"}, st)
        assert r.ok
        assert "Detected 1 breaking" in r.action
        assert "(#2)" in r.action and "DRAFT COMMS" in r.action
    finally:
        _bk._fetch_text = orig




def test_breaking_agent_none_found_passes():
    from steps.preflight import breaking, cg, cron
    from steps.preflight import breaking as _bk
    orig = _bk._fetch_text
    _bk._fetch_text = lambda *a, **k: "vNext\n----------\n- [MINOR] x (#1)\nVersion 1.0.0\n"
    try:
        r = breaking.run("preflight", {"id": "breaking"},
                            ReleaseState(release_id="2026-08"))
        assert r.ok and "No breaking" in r.action
    finally:
        _bk._fetch_text = orig




def test_breaking_agent_fetch_error_holds():
    from steps.preflight import breaking, cg, cron
    from steps.preflight import breaking as _bk
    orig = _bk._fetch_text

    def _boom(*a, **k):
        raise RuntimeError("network down")
    _bk._fetch_text = _boom
    try:
        r = breaking.run("preflight", {"id": "breaking"},
                            ReleaseState(release_id="2026-08"))
        assert not r.ok and "could not fetch" in r.action
    finally:
        _bk._fetch_text = orig




def test_build_verify_mrwp_in_flight_when_run_still_executing():
    """An MRWP run whose OVERALL status is still inProgress is NOT a failure — verify
    returns in_progress (Scout polls + re-evaluates on completion) instead of blocking it
    as an aborted pipeline. A pending stage on an in-flight run is 'not run YET'."""
    st, orch = _bv_state({"build_verify.mrwp_ecs": {
        "mrwp_id": "999", "build_status": "inProgress"}})
    out = _bv_build(orch, st, "mrwp_ecs")
    assert out["kind"] == "in_progress"
    assert "still running" in out["note"] and "poll" in out["note"].lower()
    assert out["poll_in_min"] == 30
    # a completed run still runs the normal rule (control): injected stages, no in-flight
    st2, orch2 = _bv_state({})
    assert _bv_build(orch2, st2, "mrwp_ecs")["kind"] == "done"




def test_engine_in_flight_step_holds_without_flagging_owner():
    """The engine records an in-flight agent step as status 'in_flight' (not blocked):
    it is NOT added to pending_human, the release stays 'running' (no user action), the
    drain returns a 'waiting' action, and first-seen time is stamped for the 6h nudge."""
    st, orch = _bv_state({"build_verify.mrwp_ecs": {
        "mrwp_id": "999", "build_status": "inProgress"}})
    phase = next(p for p in orch.config["phases"] if p["id"] == "build_verify")
    step = next(s for s in phase["steps"] if s["id"] == "mrwp_ecs")
    act = orch._run_auto_step(phase, step, block_holds=True)
    assert act.kind == "waiting"
    rec = st.get_step("build_verify", "mrwp_ecs")
    assert rec.status == "in_flight"
    assert "build_verify.mrwp_ecs" not in st.pending_human
    assert st.status == "running"
    assert rec.data.get("in_flight_since") and rec.data.get("poll_in_min") == 30
    assert not st.is_done("build_verify", "mrwp_ecs")     # not done → re-runs on next poll




def test_verify_auth_ecs_in_flight_build_holds():
    st, orch = _bv_state({"build_verify.auth_ecs": {
        "auth_build": {"build_id": 900010, "rc": 1, "version": "0.0.02468-rc-RC1-ecs",
                       "status": "inProgress", "result": None}}})
    out = _bv_build(orch, st, "auth_ecs")
    assert out["kind"] == "in_progress" and "still running" in out["note"]




def test_msal_variant_and_flight_markers():
    from tools import pipelines as P
    assert P._msal_variant("PROD MSAL - RC Broker (API 32)") == "prod"
    assert P._msal_variant("PROD MSAL - RC BrokerHost (API 32)") == "prod"
    assert P._msal_variant("RC MSAL - PROD Broker (API 32)") == "rc"
    assert P._msal_variant("LTW, RC MSAL - RC Broker (API 32)") == "rc"
    assert P._msal_variant("Stress Tests - RC MSAL with RC Broker") == "rc"
    assert P._msal_variant("Lab Api Tests") is None




def test_lockdown_overlap_only_production():
    """Overlap rule: only Production-env periods that intersect the window count;
    Banner-only advisories are ignored even if they overlap."""
    from orchestrator.commands.lockdown import overlapping_periods
    from datetime import date
    ws, we = date(2026, 8, 5), date(2026, 8, 26)
    periods = [
        {"name": "FIFA", "environment": "Banner", "start": date(2026, 8, 1), "end": date(2026, 8, 10)},
        {"name": "YearEnd", "environment": "Production", "start": date(2026, 8, 20), "end": date(2026, 9, 5)},
        {"name": "Old", "environment": "Production", "start": date(2026, 6, 1), "end": date(2026, 7, 1)},
    ]
    hits = overlapping_periods(ws, we, periods, "Production")
    assert [h["name"] for h in hits] == ["YearEnd"]




def test_engine_holds_scout_assisted_lockdown():
    """Scout steps are the SKILL's work: they surface in `scout_pending` (for the
    skill to run via step-action), NOT as the user's `current_step`/`action`. Clearing
    them drains the list; an attest step (confirm_reminders) becomes a real user hold."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()                 # NOTE: no scout steps cleared yet
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    rep = orch.status_report()
    # the three scout steps are pending FOR THE SKILL — not surfaced as a user action
    assert set(rep["scout_pending"]) == {"notice", "flight_reminder", "lockdown"}
    assert (rep["action"] or {}).get("step") not in ("notice", "flight_reminder", "lockdown")
    assert "preflight.notice" in st.pending_human
    # the skill runs each scout step (record_scout_step = its step-action + record-step)
    _clear_notice(orch)
    orch.run_until_gate()
    assert "notice" not in orch.status_report()["scout_pending"]
    orch.record_scout_step("preflight", "flight_reminder", "pass", "posted")
    orch.run_until_gate()
    assert "flight_reminder" not in orch.status_report()["scout_pending"]
    # confirm_reminders (attest, dep on flight_reminder now done) is a genuine user hold
    assert "preflight.confirm_reminders" in st.pending_human
    orch.complete_step("preflight", "confirm_reminders", "owner confirmed")
    orch.record_scout_step("preflight", "lockdown", "pass", "no overlap")
    orch.run_until_gate()
    assert orch.status_report()["scout_pending"] == []   # all scout work drained




def test_check_lockdown_pass_and_attention():
    """check-lockdown records pass when nothing overlaps and holds (attention)
    when a Production CCOA overlaps the release window."""
    import tempfile as _tf, json as _json
    from orchestrator.commands import lockdown as lk
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-12", ccd_source="default")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG; as_of = None
            periods_json = _json.dumps([
                {"name": "Banner Only Thing", "environment": "Banner",
                 "start": "2026-08-10", "end": "2026-08-14"}])
        assert lk.cmd_check_lockdown(A) == 0
        assert C.load_state(d, rid).is_done("preflight", "lockdown")   # banner ignored → pass

        # now a Production overlap → attention (held, not done)
        from orchestrator.state import StepState
        st2 = C.load_state(d, rid)
        st2.set_step("preflight", "lockdown", StepState())   # reset to pending
        C.save_state(st2, d, rid)

        class B(A):
            periods_json = _json.dumps([
                {"name": "IDNA Year-End", "environment": "Production",
                 "start": "2026-08-11", "end": "2026-12-31"}])
        assert lk.cmd_check_lockdown(B) == 0
        st3 = C.load_state(d, rid)
        assert not st3.is_done("preflight", "lockdown")
        assert st3.status == "awaiting_action"




def test_notice_build_real_recipients_and_html():
    """Real send: notice.build targets the REAL DL and renders a proper <table> +
    clean hotfix anchor. (Redirect to yourself is the send_to mock knob, not automatic.)"""
    from steps.preflight import notice
    from steps.lib import templating as T
    st = ReleaseState(release_id="2026-08", ccd="2026-08-12", ccd_source="default",
                      owner_email="pedroro@microsoft.com", owner_name="Pedro")
    out = notice.build(st)
    html = out.payload["body"]
    assert "<table" in html and "</table>" in html
    assert ">the hotfix cherry-pick guide</a>" in html          # clean anchor, no raw URL
    assert "August" in html and "08/12/2026" in html and "@pedroro" in html
    assert "androididentity@microsoft.com" in out.payload["to"]  # real DL by default
    assert not out.payload["subject"].startswith("[TEST")
    assert T.ordinal(12) == "12th" and T.ordinal(21) == "21st"




def test_confirm_reminders_is_attestation_hold():
    """In the parallel Phase 0, confirm_reminders is a human attestation that becomes
    ready only after flight_reminder is sent; it surfaces as a hold with a confirm
    pill and clears via `done`."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()
    _clear_notice(orch)
    orch.record_scout_step("preflight", "flight_reminder", "pass", "sent")
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    # confirm_reminders (dep on flight_reminder, now done) is among the pending holds
    assert "preflight.confirm_reminders" in st.pending_human
    ap = orch.status_report()["active_phase"]
    step = next(s for s in ap["steps"] if s["id"] == "confirm_reminders")
    assert step["status"] == "confirm" and step["needs_owner"]
    # owner attests → advances
    orch.complete_step("preflight", "confirm_reminders", "verified with feature owners")
    assert st.is_done("preflight", "confirm_reminders")




def test_confirm_reminders_gated_by_flight_send():
    """confirm_reminders must NOT be offered until flight_reminder is sent (dependency)."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()
    _clear_notice(orch)
    # flight_reminder NOT yet recorded → confirm_reminders is not ready
    orch.run_until_gate()
    assert "preflight.confirm_reminders" not in st.pending_human
    assert "preflight.flight_reminder" in st.pending_human   # the send is what's pending




def test_cg_report_summarizes_and_flags_high():
    """The CG report groups active alerts by severity and lists High/Critical."""
    from steps.preflight.cg import _cg_summary, _cg_report
    alerts = [
        {"alertState": "active", "severity": "high", "title": "CVE-1",
         "component": {"displayName": "io.netty:x", "displayVersion": "4.2.15"},
         "actionItems": "Upgrade to 4.2.16"},
        {"alertState": "active", "severity": "medium", "title": "CVE-2"},
        {"alertState": "autoDismissed", "severity": "high", "title": "CVE-OLD"},
        {"alertState": "fixed", "severity": "critical", "title": "CVE-FIXED"},
    ]
    active, high = _cg_summary(alerts, ["critical", "high"])
    assert len(active) == 2 and len(high) == 1
    rep = _cg_report(active, high)
    assert "2 active alert(s)" in rep and "1 high" in rep
    assert "CVE-1" in rep and "io.netty:x" in rep and "Upgrade to 4.2.16" in rep
    assert "CVE-OLD" not in rep and "CVE-FIXED" not in rep   # non-active excluded




def test_cg_agent_blocks_on_high():
    """High/Critical active alerts BLOCK the step (ok=False) with a fix-and-rerun message."""
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (True, [
        {"alertState": "active", "severity": "high", "title": "CVE-9",
         "component": {"displayName": "pkg", "displayVersion": "1.0"}},
    ], "ok")
    try:
        r = cg.run("preflight", {"id": "cg"}, None)
        assert not r.ok                       # High → blocks
        assert "CVE-9" in r.action and "RERUN" in r.action
    finally:
        checks.fetch_cg_alerts = orig




def test_cg_agent_passes_when_no_high():
    """Only Medium/Low active alerts → the step passes (report captured)."""
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (True, [
        {"alertState": "active", "severity": "medium", "title": "CVE-M"},
    ], "ok")
    try:
        r = cg.run("preflight", {"id": "cg"}, None)
        assert r.ok and "1 active" in r.action
    finally:
        checks.fetch_cg_alerts = orig




def test_oneauth_access_granted():
    """Injected access='granted' → the step passes and links the repo + access package."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import ReleaseState
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"alias": "pedroro", "access": "granted"}):
        out = as_dict(_steps.get_step("preflight", "oneauth_access").build(st))
    assert out["kind"] == "done"
    assert "pedroro" in out["note"] and "write access confirmed" in out["note"]
    assert any("access-packages" in l["url"] for l in out["links"])




def test_oneauth_access_denied_blocks():
    """Injected access='denied' → BLOCKED, pointing at the access package + a rerun instruction."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import ReleaseState
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"alias": "pedroro", "access": "denied"}):
        out = as_dict(_steps.get_step("preflight", "oneauth_access").build(st))
    assert out["kind"] == "blocked"
    assert "no write access" in out["reason"] and "RERUN" in out["reason"]
    assert any("access-packages" in l["url"] for l in out["links"])




def test_oneauth_access_blocks_without_alias():
    """No resolvable alias → BLOCKED (can't form the user/<alias>/… probe branch)."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import ReleaseState
    from tools import checks
    st = ReleaseState(release_id="2026-08")
    o = checks.current_az_user
    checks.current_az_user = lambda *a, **k: None
    try:
        with mockctx.active({}):
            out = as_dict(_steps.get_step("preflight", "oneauth_access").build(st))
    finally:
        checks.current_az_user = o
    assert out["kind"] == "blocked" and "alias" in out["reason"]




def test_cg_blocked_step_reruns_and_clears_when_fixed():
    """A CG block holds the step; fixing (alerts now clean) + rerunning `next`
    re-checks and lets the flow continue."""
    st, orch = _mock_orch({"preflight.cg": {"outcome": "blocked",
                          "reason": "CG: 1 critical active\n→ Fix and RERUN or skip."}})
    _clear_phase0_scout(orch)          # clear the earlier scout/human holds
    orch.run_until_gate()
    assert st.status == "awaiting_action" and st.current_step == "cg"
    assert st.get_step("preflight", "cg").status == "blocked"
    # the digest shows it blocked / needs owner
    step = next(s for s in orch.status_report()["active_phase"]["steps"] if s["id"] == "cg")
    assert step["status"] == "blocked" and step["needs_owner"]
    # FIX: alerts now clean → RERUN (next) re-checks and passes
    orch.mocks["preflight.cg"] = {"outcome": "done", "note": "CG: 0 active alerts."}
    orch.run_until_gate()
    assert st.is_done("preflight", "cg")




def test_cg_blocked_step_skip_override():
    """The owner can override a CG block by skipping the step (with a reason)."""
    st, orch = _mock_orch({"preflight.cg": {"outcome": "blocked", "reason": "CG: 1 high active"}})
    _clear_phase0_scout(orch)
    orch.run_until_gate()
    assert st.current_step == "cg" and st.get_step("preflight", "cg").status == "blocked"
    orch.skip_step("preflight", "cg", "accepted risk; tracked separately")
    assert st.is_done("preflight", "cg")   # skipped counts as done
    assert st.get_step("preflight", "cg").status == "skipped"




def test_cg_agent_fetch_error_holds():
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (False, [], "403 forbidden")
    try:
        r = cg.run("preflight", {"id": "cg"}, None)
        assert not r.ok and "could not read alerts" in r.action
    finally:
        checks.fetch_cg_alerts = orig




def test_cron_check_passes_on_recent_scheduled_run():
    from steps.preflight import breaking, cg, cron
    from tools import checks
    from datetime import datetime, timezone
    orig = checks.latest_scheduled_build
    now_iso = datetime.now(timezone.utc).isoformat()
    checks.latest_scheduled_build = lambda *a, **k: (True, {
        "queueTime": now_iso, "result": "succeeded", "status": "completed"}, "ok")
    try:
        r = cron.run("preflight", {"id": "cron"}, None)
        assert r.ok and "scheduled and firing" in r.action
    finally:
        checks.latest_scheduled_build = orig




def test_cron_check_blocks_when_stale():
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.latest_scheduled_build
    checks.latest_scheduled_build = lambda *a, **k: (True, {
        "queueTime": "2026-01-01T06:00:00Z", "result": "succeeded", "status": "completed"}, "ok")
    try:
        r = cron.run("preflight", {"id": "cron"}, None)
        assert not r.ok and "stale" in r.action
    finally:
        checks.latest_scheduled_build = orig




def test_cron_check_blocks_when_no_scheduled_run():
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.latest_scheduled_build
    checks.latest_scheduled_build = lambda *a, **k: (True, None, "no scheduled runs in recent history")
    try:
        r = cron.run("preflight", {"id": "cron"}, None)
        assert not r.ok and "no scheduled run" in r.action
    finally:
        checks.latest_scheduled_build = orig




def test_vitals_is_attestation_hold():
    """Play Console vitals/policy is an attest step (no API for policy): it HOLDS
    for the owner to confirm they reviewed it, and clears via `done`."""
    st, orch = _orch()   # clears the earlier holds (incl. vitals via _clear_phase0_scout)
    # re-open vitals to observe its natural hold
    orch.reopen_step("preflight", "vitals")
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    assert st.current_step == "vitals"
    step = next(s for s in orch.status_report()["active_phase"]["steps"] if s["id"] == "vitals")
    assert step["status"] == "confirm" and step["needs_owner"]
    orch.complete_step("preflight", "vitals", "reviewed vitals + policy status in Play Console")
    assert st.is_done("preflight", "vitals")




def test_create_payload_wiki_dry_run_and_execute(capsys):
    """create-payload-wiki --dry-run previews (no write); --execute creates the page and
    records the step done with the page link."""
    import tempfile as _tf
    from orchestrator.commands import payload_wiki_cmd as PW
    from tools import checks
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-13", owner_email="dev@microsoft.com")
        st.versions = {"authenticator": "release/2026/08/13", "broker": "16.5.0",
                       "common": "24.6.0", "msal": "8.4.2"}
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()
        C = __import__("orchestrator.cli_common", fromlist=["x"])
        C.save_state(st, d, rid)
        # the step's version/prs mocks so compose_payload runs offline (no ADO)
        orch_mocks = {"finalize.wiki_payload": {
            "version": {"version": "6.2608.5658", "build_url": "https://x/y"},
            "prs": [{"id": 1, "title": "Feature"}]}}

        # patch the checks helpers so no network/az
        oe, oc = checks.wiki_page_exists, checks.create_wiki_page
        created = {}
        checks.wiki_page_exists = lambda *a, **k: False        # absent → create
        def _create(org, project, wiki, path, content, timeout=60):
            created["path"] = path
            return checks.CheckResult(True, True, "created")
        checks.create_wiki_page = _create

        class A:
            runs_root = d; release = rid; config = CONFIG; as_of = None
            execute = False; dry_run = True

        # surface the step mocks on the orch the command loads (so compose runs offline)
        import orchestrator.cli_common as _CC
        real_load = _CC.load_orch
        def fake_load(runs_root, release, config, as_of=None):
            s, o = real_load(runs_root, release, config, as_of)
            o.mocks = orch_mocks
            return s, o
        _CC.load_orch = fake_load
        try:
            assert PW.cmd_create_payload_wiki(A) == 0        # dry-run
            out = capsys.readouterr().out
            assert "PAGE CONTENT (preview)" in out and "#App Version" in out
            assert not created                                # nothing written on dry-run

            A.execute = True; A.dry_run = False
            assert PW.cmd_create_payload_wiki(A) == 0
            assert created["path"].endswith("September 2026 Release")
            s2 = C.load_state(d, rid)
            assert s2.is_done("finalize", "wiki_payload")
            step = s2.get_step("finalize", "wiki_payload")
            assert step.links and "pagePath=" in step.links[0]["url"]
        finally:
            _CC.load_orch = real_load
            checks.wiki_page_exists, checks.create_wiki_page = oe, oc

