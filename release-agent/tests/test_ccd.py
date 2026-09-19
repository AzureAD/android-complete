"""Release-agent tests — ccd. Shared harness in tests/_harness.py."""
from tests._context import context as _context, invoke as _invoke
from tests._harness import *  # noqa: F401,F403
import pytest


def _localization_execution():
    return {
        "id": "localization-test-execution",
        "owner": "test",
        "started_at": "2026-09-09T19:00:00Z",
        "write_review": {"hash": "sha256:" + "a" * 64, "approved_by": "test-reviewer"},
    }




def test_ccd_confirmed_is_required_scout_item():
    """ccd_confirmed (source: scout) is a REQUIRED, non-opt_out auto item: Python
    verify() must not touch it, and the gate stays shut until the skill records it."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    orch.gate.verify()
    assert st.readiness_items.get("ccd_confirmed", {}).get("status", "pending") == "pending"
    cc = next(i for i in orch.gate.checklist()["items"] if i["id"] == "ccd_confirmed")
    assert cc["verify"] == "auto" and cc["source"] == "scout" and not cc.get("opt_out")
    # everything else satisfied but ccd_confirmed → gate still closed
    orch.gate.record_check("oncall_now", "pass", "not on-call")
    orch.gate.record_check("adx_access", "pass", "can query")
    orch.gate.record_check("silent_perms", "pass", "auto-approved")
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.sign()
    assert not orch.gate.signed
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled with pipeline")
    assert orch.gate.signed
    # 'degraded' is rejected — it's not an opt-out item (a wrong CCD must block)
    assert "error" in orch.gate.record_check("ccd_confirmed", "degraded", "nope")




def test_ccd_conflict_surfaced_in_status():
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08",
                      ccd_source="default", ccd_conflict="2026-07-09")
    orch = Orchestrator(CONFIG, st)
    rpt = orch.status_report()
    assert rpt["ccd_conflict"] == "2026-07-09"
    from orchestrator import render
    out = render.status_view(rpt)
    assert "Confirm the date" in out and "2026-07-09" in out




def test_ccd_viability_past_compressed_healthy():
    """ccd_viability is the single source of temporal truth: past (invalid),
    compressed (Phase 0 window squeezed — warn), and healthy (full runway)."""
    from orchestrator import schedule
    from datetime import date
    # PAST — a current-month release whose 2nd-Wed default already slipped by
    p = schedule.ccd_viability(date(2026, 8, 12), date(2026, 8, 18))
    assert p["past"] is True and p["days_to_ccd"] == -6 and p["runway_days"] == 0
    # COMPRESSED — CCD two days out: inside the CCD-7 window, only 2 prep days left
    c = schedule.ccd_viability(date(2026, 8, 20), date(2026, 8, 18))
    assert c["past"] is False and c["compressed"] is True and c["runway_days"] == 2
    # HEALTHY — CCD well in the future: full 7-day window, not compressed
    h = schedule.ccd_viability(date(2026, 9, 9), date(2026, 8, 18))
    assert h["past"] is False and h["compressed"] is False and h["runway_days"] == 7
    # BOUNDARY — as_of exactly at CCD-7 is still a full window (not yet compressed)
    b = schedule.ccd_viability(date(2026, 9, 9), date(2026, 9, 2))
    assert b["compressed"] is False and b["runway_days"] == 7
    # BOUNDARY — CCD today: not past, but zero prep days → compressed
    t = schedule.ccd_viability(date(2026, 8, 18), date(2026, 8, 18))
    assert t["past"] is False and t["compressed"] is True and t["runway_days"] == 0




def test_no_localization_strings_step():
    """The old #5 localization strings step was removed."""
    st, orch = _orch()
    preflight = next(p for p in orch.config["phases"] if p["id"] == "preflight")
    assert "strings" not in [s["id"] for s in preflight["steps"]]




def test_ccd_final_reminder_build_is_ccd_day_email():
    """final_reminder resolves to a real workiq_send_email with the CCD-day 'update'
    variant (subject says 'Today'), the real DL, and a rendered table."""
    from steps.ccd import final_reminder
    out = _invoke(final_reminder.build, _ccd_state())
    assert out.kind == "needs_skill" and out.tool == "workiq_send_email"
    assert out.record_as == "final_reminder"
    assert "androididentity@microsoft.com" in out.payload["to"]      # real DL
    assert "(Today)" in out.payload["subject"]                        # update variant
    assert not out.payload["subject"].startswith("[TEST")
    html = out.payload["body"]
    assert "<table" in html and "October" in html and "@pedroro" in html




def test_ccd_pr_reminder_build_targets_code_reviews_with_deadlines():
    """pr_reminder posts to the fixed 'Code reviews' chat and names the 11 PM branch
    cut, Moumita's approval, and the noon localization cutoff."""
    from steps.ccd import pr_reminder
    out = _invoke(pr_reminder.build, _ccd_state())
    assert out.kind == "needs_skill" and out.tool == "workiq_send_chat_message"
    assert out.payload["chatId"] == pr_reminder.CONFIG["live_chat_id"]
    assert out.payload["contentType"] == "html"
    body = out.payload["content"]
    assert "11:00 PM" in body                       # branch cut deadline
    assert "moghosh@microsoft.com" in body and "Moumita" in body  # approver
    assert "noon" in body                           # localization cutoff




def test_ccd_localization_build_triggers_pipeline_405133():
    """Localization routes through its checked auto-approved launch, never a raw provider trigger."""
    from steps.ccd import localization
    out = _invoke(localization.build, _ccd_state())
    assert out.kind == "needs_skill" and out.tool == "launch-localization"
    assert "launch-localization --release 2026-09" in out.payload["followup_command"]
    assert "--execute --auto-approve" in out.payload["followup_command"]
    assert localization.CONFIG["pipeline_id"] == 405133
    assert localization.CONFIG["variables"] == {"isCreatePrSelected": "true"}
    assert "_trigger" not in out.payload
    urls = [l["url"] for l in out.payload["links"]]
    assert any("pullrequests" in u for u in urls)   # where the OneLoc PR lands




def test_ccd_steps_blocked_without_ccd():
    """Every ccd comms/trigger step blocks cleanly if the release has no CCD."""
    from steps.ccd import final_reminder, pr_reminder, localization
    st = ReleaseState(release_id="x")               # no ccd
    for mod in (final_reminder, pr_reminder, localization):
        out = _invoke(mod.build, st)
        assert out.kind == "blocked"




def test_ccd_phase_shape_and_scout_kinds():
    """Phase 1 is three scout comms/trigger steps and NO gate — the branch cut is
    automatic (at 11 PM), so there's no manual cut step; the next hold is the Phase-3
    ui_failures reminder (Phase 2's rc_report gate is automatic)."""
    import yaml
    cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    ccd = next(p for p in cfg["phases"] if p["id"] == "ccd")
    ids = [s["id"] for s in ccd["steps"]]
    assert ids == ["final_reminder", "pr_reminder", "localization"]
    by = {s["id"]: s for s in ccd["steps"]}
    for sid in ("final_reminder", "pr_reminder", "localization"):
        assert by[sid].get("source") == "scout", f"{sid} should be a scout step"
    # Phase 1 has no gate anymore
    assert not any(s.get("gate") for s in ccd["steps"])




def test_localization_poll_helpers():
    from steps.ccd import localization as L
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    started = (now - timedelta(minutes=20)).isoformat()
    assert L.poll_status(False, started, now, 3) == "wait"
    assert L.poll_status(False, (now - timedelta(hours=4)).isoformat(), now, 3) == "timeout"
    assert L.poll_status(True, started, now, 3) == "complete"
    assert L.extract_pr_id(_PR_LOG) == "16790317"
    assert L.extract_pr_id("no pr line here") is None
    assert L.extract_create_pr_enabled(_NO_PR_CREATE_TRUE_LOG) is True
    assert L.extract_create_pr_enabled(_NO_PR_CREATE_FALSE_LOG) is False
    assert L.extract_create_pr_enabled("no create flag") is None
    assert L.pr_url("16790317").endswith("/pullrequest/16790317")
    # extract_pr: no URL in log → fall back to the template
    pid, url = L.extract_pr(_PR_LOG)
    assert pid == "16790317" and url == L.pr_url("16790317")
    # extract_pr: real log with the full URL → use exactly that URL
    pid2, url2 = L.extract_pr(_PR_LOG_WITH_URL)
    assert pid2 == "16790317"
    assert url2 == ("https://msazure.visualstudio.com/DefaultCollection/One/_git/"
                    "AD-MFA-phonefactor-phoneApp-android/pullrequest/16790317")




def test_localization_az_read_recipe_is_wired():
    """The step carries the exact az reads for msazure/One (MCP can't reach it)."""
    from steps.ccd import localization as L
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", owner_email="p@ms.com")
    assert _invoke(L.build, st).tool == "launch-localization"
    az = L._az_read(L.CONFIG)
    assert "az pipelines build show" in az["status"]
    assert "resource timeline" in az["log_id"] and "OneLocBuild@3" in az["log_id"]
    assert "resource logs" in az["log"] and "{build_id}" in az["log"] and "{log_id}" in az["log"]
    assert "az repos pr show" in az["pr_status"] and "{pr_id}" in az["pr_status"]




def test_localization_decide_branches(monkeypatch):
    import pytest
    from steps.ccd import localization as L
    from datetime import datetime, timezone, timedelta
    now = datetime.fromisoformat("2026-09-09T20:00:00+00:00")

    st = _loc_state(started_min_ago=20)
    step = st.get_step("ccd", "localization")
    step.data["started_at"] = (now - timedelta(minutes=20)).isoformat()
    st.set_step("ccd", "localization", step)
    assert L.decide(_context(st), False, None, now)["decision"] == "wait"

    st2 = _loc_state(started_min_ago=4 * 60 + 5)          # 4h+ → timeout
    step = st2.get_step("ccd", "localization")
    step.data["started_at"] = (now - timedelta(hours=4)).isoformat()
    st2.set_step("ccd", "localization", step)
    d = L.decide(_context(st2), False, None, now)
    assert d["decision"] == "timeout"
    assert d["email"]["to"] == ["pedroro@microsoft.com"]

    st3 = _loc_state(started_min_ago=60)
    proof = L.RunEvidence(result="succeeded", logs_complete=True)
    dpr = L.decide(_context(st3), True, _PR_LOG, now, evidence=proof)
    assert dpr["decision"] == "announce_pr" and dpr["pr_id"] == "16790317"
    assert dpr["chat"]["chatId"] == L.CONFIG["code_reviews_chat_id"]
    assert any("16790317" in l["url"] for l in dpr["links"])
    # proof: the PR case ALSO carries the pipeline run link (build id)
    assert any("buildId=177219192" in l["url"] for l in dpr["links"])
    # the Code reviews post @mentions the release engineer + names the 4 PM LA deadline
    assert '<at id="0">' in dpr["chat"]["content"]
    assert "4:00 PM Los Angeles time" in dpr["chat"]["content"]
    m = dpr["chat"]["mentions"][0]
    assert m["mentioned"]["user"]["id"] == "pedroro@microsoft.com"

    none = L.decide(_context(st3), True, _NO_PR_CREATE_TRUE_LOG, now,
                    evidence=L.RunEvidence("succeeded", True))
    assert none["decision"] == "announce_none"
    assert none["chat"]["chatId"] == L.CONFIG["code_reviews_chat_id"]
    assert "No strings to localize" in none["chat"]["content"]
    assert any("buildId=177219192" in l["url"] for l in none["links"])

    disabled = L.decide(_context(st3), True, _NO_PR_CREATE_FALSE_LOG, now,
                        evidence=L.RunEvidence("succeeded", True))
    assert disabled["decision"] == "failed"
    assert "PR creation was disabled" in disabled["note"]

    dn = L.decide(_context(st3), True, "no strings changed", now,
                  evidence=L.RunEvidence("succeeded", True, "Owner reviewed the complete task output: no changes"))
    assert dn["decision"] == "complete_none"
    # proof: even with NO PR, the Details box gets the pipeline run link as evidence
    assert dn["links"] and any("buildId=177219192" in l["url"] for l in dn["links"])

    step = st3.get_step("ccd", "localization")
    step.data.update({
        "pr_id": "16790317", "pr_url": L.pr_url("16790317"),
    })
    st3.set_step("ccd", "localization", step)
    # Confirmed merge wins; a delayed initial post cannot turn merged work into omitted work.
    assert L.decide(_context(st3), True, now=now, pr_status="completed", evidence=proof)["decision"] == "merged"
    step.data["pr_announced_at"] = "2026-09-09T20:05:00Z"
    st3.set_step("ccd", "localization", step)
    before = datetime.fromisoformat("2026-09-09T22:59:59+00:00")
    deadline = datetime.fromisoformat("2026-09-09T23:00:00+00:00")
    assert L.decide(_context(st3), True, now=before, pr_status="active", evidence=proof)["decision"] == "wait_for_merge"
    overdue = L.decide(_context(st3), True, now=deadline, pr_status="active", evidence=proof)
    assert overdue["decision"] == "warn_unmerged"
    assert "translated strings at risk" in overdue["chat"]["content"]
    assert "release remains on schedule" in overdue["chat"]["content"]
    step.data["merge_deadline_alert_at"] = deadline.isoformat()
    st3.set_step("ccd", "localization", step)
    assert L.decide(_context(st3), True, now=deadline, pr_status="active", evidence=proof)["decision"] == "wait_for_merge"
    assert L.decide(_context(st3), True, now=deadline, pr_status="completed", evidence=proof)["decision"] == "merged"
    omission = datetime.fromisoformat("2026-09-10T01:00:00+00:00")
    omitted = L.decide(_context(st3), True, now=omission, pr_status="active", evidence=proof)
    assert omitted["decision"] == "omit_unmerged"
    assert "release continues without these translated strings" in omitted["note"]
    assert L.decide(_context(st3), True, now=omission, pr_status="completed", evidence=proof)["decision"] == "merged"
    first_discovery = _loc_state(started_min_ago=60)
    assert L.decide(_context(first_discovery), True, _PR_LOG, omission,
                    pr_status="completed", evidence=proof)["decision"] == "merged"
    assert L.decide(_context(first_discovery), True, _PR_LOG, omission,
                    pr_status="active", evidence=proof)["decision"] == "omit_unmerged"

    monkeypatch.setattr(L, "get_tz", lambda _name: None)
    with pytest.raises(ValueError, match="timezone data unavailable"):
        L.decide(_context(st3), True, now=deadline, pr_status="active", evidence=proof)




def test_localization_review_post_no_owner_has_no_mention():
    """With no owner email, the post still goes out but without an @mention array."""
    from steps.ccd import localization as L
    st = _loc_state(started_min_ago=60)
    st.owner_email = ""
    st.owner_name = ""
    from datetime import datetime, timezone
    d = L.decide(_context(st), True, _PR_LOG, datetime.fromisoformat("2026-09-09T20:00:00+00:00"),
                 evidence=L.RunEvidence("succeeded", True))
    assert d["decision"] == "announce_pr"
    assert "mentions" not in d["chat"]
    assert "4:00 PM Los Angeles time" in d["chat"]["content"]




def test_localization_command_lifecycle_wait_announce_escalate_then_merge():
    """record-localization-run leaves the step in-flight; a wait poll keeps it
    in-flight; a completed run stores/announces its PR but remains in-flight; the 4 PM
    escalation is deduplicated; only a merged PR marks the step done."""
    from orchestrator.commands import localization as lc
    from steps.ccd import localization as L
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-09"
        st = ReleaseState(release_id=rid, ccd="2026-09-09",
                          owner_email="p@ms.com", owner_name="P")
        _active_step(st, "ccd", "localization")
        st.set_step("ccd", "localization", StepState(
            status="in_flight", execution=_localization_execution(),
            data={"build_id": "176407869", "started_at": "2026-09-09T19:00:00Z"}))
        execution_id = _localization_execution()["id"]
        C.save_state(st, d, rid)

        s1 = C.load_state(d, rid).get_step("ccd", "localization")
        assert s1.data["build_id"] == "176407869" and s1.status == "in_flight"

        target = L.poll_target(_context(C.load_state(d, rid)))
        assert target["decision"] == "poll_pipeline" and target["build_id"] == "176407869"
        assert "--id 176407869" in target["az"]["status"]

        class CKwait:
            runs_root = d; release = rid; config = CONFIG
            complete = "false"; logs = None; logs_file = None
            now = "2026-09-09T19:30:00Z"; as_of = None; pr_status = None
        CKwait.execution_id = execution_id
        lc.cmd_check_localization(CKwait)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "in_flight"

        class CKdone:
            runs_root = d; release = rid; config = CONFIG
            complete = "true"; logs = _PR_LOG; logs_file = None
            run_result = "succeeded"; logs_complete = True
            now = "2026-09-09T20:00:00Z"; as_of = None; pr_status = None
        CKdone.execution_id = execution_id
        lc.cmd_check_localization(CKdone)
        pending = C.load_state(d, rid).get_step("ccd", "localization")
        assert pending.status == "in_flight" and pending.data["pr_id"] == "16790317"
        assert any("16790317" in l["url"] for l in pending.links)
        target = L.poll_target(_context(C.load_state(d, rid)))
        assert target["decision"] == "poll_pr" and target["pr_id"] == "16790317"
        assert "--id 16790317" in target["az"]["status"]

        class ACKinitial:
            runs_root = d; release = rid; kind = "initial"; pr_id = "16790317"
        _ack_notifications(d, rid, "2026-09-09T20:00:00Z")
        assert lc.cmd_record_localization_post(ACKinitial) == 0
        assert lc.cmd_record_localization_post(ACKinitial) == 0
        announced = C.load_state(d, rid).get_step("ccd", "localization")
        assert announced.data["pr_announced_at"]

        class CKactive:
            runs_root = d; release = rid; config = CONFIG
            complete = "true"; logs = None; logs_file = None; run_result = "succeeded"
            now = "2026-09-09T22:59:00Z"; as_of = None; pr_status = "active"
        CKactive.execution_id = execution_id
        lc.cmd_check_localization(CKactive)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "in_flight"

        CKactive.now = "2026-09-09T23:00:00Z"
        lc.cmd_check_localization(CKactive)
        escalated = C.load_state(d, rid).get_step("ccd", "localization")
        assert escalated.status == "in_flight" and not escalated.data.get("merge_deadline_alert_at")

        class ACKdeadline:
            runs_root = d; release = rid; kind = "deadline"; pr_id = "16790317"
        _ack_notifications(d, rid, "2026-09-09T23:00:00Z")
        assert lc.cmd_record_localization_post(ACKdeadline) == 0
        assert C.load_state(d, rid).get_step("ccd", "localization").data["merge_deadline_alert_at"]

        CKactive.pr_status = "completed"
        CKactive.now = "2026-09-09T23:30:00Z"
        lc.cmd_check_localization(CKactive)
        done = C.load_state(d, rid).get_step("ccd", "localization")
        assert done.status == "done" and done.data["build_id"] == "176407869"




def test_localization_command_timeout_holds():
    """A poll past the 3h timeout blocks the step (awaiting the engineer)."""
    from orchestrator.commands import localization as lc
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-09"
        st = ReleaseState(release_id=rid, ccd="2026-09-09", owner_email="p@ms.com")
        _active_step(st, "ccd", "localization")
        st.set_step("ccd", "localization", StepState(
            status="in_flight", execution=_localization_execution(),
            data={"build_id": "1", "started_at": "2026-09-09T19:00:00Z"}))
        execution_id = _localization_execution()["id"]
        C.save_state(st, d, rid)


        class CK:
            runs_root = d; release = rid; config = CONFIG
            complete = "false"; logs = None; logs_file = None
            now = "2026-09-09T22:30:00Z"; as_of = None; pr_status = None
        CK.execution_id = execution_id
        lc.cmd_check_localization(CK)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "in_flight"
        _ack_notifications(d, rid, CK.now)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "blocked"


@pytest.mark.parametrize("delivery_status", ["prepared", "not_sent", "claimed"])
@pytest.mark.parametrize("recovery", ["pr", "complete_none", "stored_pr"])
@pytest.mark.parametrize("initial_complete", ["true", "false"])
def test_localization_recovery_invalidates_timeout(
        tmp_path, delivery_status, recovery, initial_complete, monkeypatch):
    from argparse import Namespace
    from datetime import datetime
    from orchestrator import delivery as D, mocks
    from orchestrator.commands import localization as lc
    from orchestrator.commands.delivery_cmd import finish
    from orchestrator.state import StepState
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", owner_email="owner@example.com")
    _active_step(st, "ccd", "localization")
    st.set_step("ccd", "localization", StepState(
        status="in_flight", execution=_localization_execution(), data={
        "build_id": "1", "started_at": "2026-09-09T19:00:00Z"}))
    if recovery == "stored_pr":
        step = st.get_step("ccd", "localization")
        step.data.update(pr_id="16790317", pr_url="https://example.test/pr/16790317",
                         pr_status="active", pipeline_complete=True,
                         pr_announced_at="2026-09-09T20:00:00Z")
        st.set_step("ccd", "localization", step)
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG, as_of=None,
                     now="2026-09-09T22:30:00Z", complete=initial_complete, logs=None, logs_file=None,
                     pr_status=None, execution_id="localization-test-execution")
    assert lc.cmd_check_localization(args) == 0
    st, orch = C.load_orch(str(tmp_path), st.release_id, CONFIG,
                          datetime.fromisoformat(args.now.replace("Z", "+00:00")))
    item = next(iter(st.notification_deliveries.values()))["descriptor"]
    if delivery_status != "prepared":
        claim = D.claim(orch, item["id"], item["hash"], "test-worker")
        if delivery_status == "not_sent":
            D.result(orch, item["id"], claim["execution_id"], "not_sent", "Simulated rejection")
        C.save_state(st, str(tmp_path), st.release_id)
    args.complete, args.now = "true", "2026-09-09T22:40:00Z"
    args.logs = _PR_LOG if recovery == "pr" else "no strings changed"
    args.run_result, args.logs_complete = "succeeded", True
    args.no_change_confirmation = "Owner reviewed complete task output: no changes" if recovery == "complete_none" else None
    if recovery == "stored_pr":
        args.logs, args.pr_status = None, "active"
    assert lc.cmd_check_localization(args) == 0
    st, orch = C.load_orch(str(tmp_path), st.release_id, CONFIG,
                          datetime.fromisoformat(args.now.replace("Z", "+00:00")))
    expected = "done" if recovery == "complete_none" else "in_flight"
    assert st.get_step("ccd", "localization").status == expected
    assert st.get_step("ccd", "localization").data["pipeline_complete"]
    if delivery_status == "claimed":
        D.result(orch, item["id"], claim["execution_id"], "sent", "Simulated accepted receipt")
        C.save_state(st, str(tmp_path), st.release_id)
        assert finish(orch, item["id"])
        assert st.notification_deliveries[item["id"]]["completion"]["status"] == "suppressed"
    else:
        with pytest.raises(
            ValueError,
            match="checkpoint changed|owning step complete|outside owning phase",
        ):
            D.claim(orch, item["id"], item["hash"], "test-worker")
    assert st.get_step("ccd", "localization").status == expected
    C.save_state(st, str(tmp_path), st.release_id)
    assert C.load_state(str(tmp_path), st.release_id).get_step("ccd", "localization").status == expected


@pytest.mark.parametrize("when", ["2026-09-09T22:00:00Z", "2026-09-10T01:00:00Z"])
@pytest.mark.parametrize("delivery_status", ["prepared", "not_sent", "claimed"])
def test_localization_confirmed_merge_wins_over_unacknowledged_initial_post(
        tmp_path, monkeypatch, when, delivery_status):
    from argparse import Namespace
    from datetime import datetime
    from orchestrator import delivery as D, mocks
    from orchestrator.commands import localization as lc
    from orchestrator.commands.delivery_cmd import finish
    from orchestrator.state import StepState
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", owner_email="owner@example.com")
    _active_step(st, "ccd", "localization")
    st.set_step("ccd", "localization", StepState(
        status="in_flight", execution=_localization_execution(), data={
        "build_id": "1", "started_at": "2026-09-09T19:00:00Z"}))
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG, as_of=None,
                     now="2026-09-09T20:00:00Z", complete="true", logs=_PR_LOG, logs_file=None,
                     run_result="succeeded", logs_complete=True,
                     pr_status=None, execution_id="localization-test-execution")
    assert lc.cmd_check_localization(args) == 0
    st, orch = C.load_orch(str(tmp_path), st.release_id, CONFIG,
                          datetime.fromisoformat(args.now.replace("Z", "+00:00")))
    item = next(iter(st.notification_deliveries.values()))["descriptor"]
    if delivery_status != "prepared":
        claim = D.claim(orch, item["id"], item["hash"], "test-worker")
        if delivery_status == "not_sent":
            D.result(orch, item["id"], claim["execution_id"], "not_sent", "Simulated rejection")
        C.save_state(st, str(tmp_path), st.release_id)
    args.now, args.pr_status, args.complete, args.logs = when, "completed", "true", None
    assert lc.cmd_check_localization(args) == 0
    st, orch = C.load_orch(str(tmp_path), st.release_id, CONFIG,
                          datetime.fromisoformat(when.replace("Z", "+00:00")))
    done = st.get_step("ccd", "localization")
    assert done.status == "done" and done.data["pr_status"] == "completed"
    assert not done.data.get("pr_announced_at") and "merged" in done.note
    if delivery_status == "claimed":
        D.result(orch, item["id"], claim["execution_id"], "sent", "Simulated delayed receipt")
        finish(orch, item["id"])
        assert st.notification_deliveries[item["id"]]["completion"]["status"] == "suppressed"
    else:
        with pytest.raises(ValueError, match="owning step complete|outside owning phase/window"):
            D.claim(orch, item["id"], item["hash"], "test-worker")
    assert st.get_step("ccd", "localization") == done


def test_localization_command_omits_unmerged_pr_at_6pm():
    from orchestrator.commands import localization as lc
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-09"
        st = ReleaseState(release_id=rid, ccd="2026-09-09")
        _active_phase(st, "ccd")
        st.set_step("ccd", "final_reminder", StepState(status="done"))
        st.set_step("ccd", "pr_reminder", StepState(status="done"))
        step = st.get_step("ccd", "localization")
        step.status = "in_flight"
        step.execution = _localization_execution()
        step.data = {
            "build_id": "176407869",
            "started_at": "2026-09-09T19:00:00Z",
            "pr_id": "16790317",
            "pr_url": "https://example.test/pr/16790317",
            "pr_announced_at": "2026-09-09T20:00:00Z",
            "merge_deadline_alert_at": "2026-09-09T23:00:00Z",
        }
        st.set_step("ccd", "localization", step)
        C.save_state(st, d, rid)

        class CK:
            runs_root = d; release = rid; config = CONFIG
            complete = "true"; logs = None; logs_file = None; run_result = "succeeded"
            now = "2026-09-10T01:00:00Z"; as_of = None; pr_status = "active"
            execution_id = "localization-test-execution"

        assert lc.cmd_check_localization(CK) == 0
        omitted = C.load_state(d, rid).get_step("ccd", "localization")
        assert omitted.status == "skipped" and omitted.by == "scout"
        assert "not merged by 6:00 PM Los Angeles time" in omitted.note
        assert omitted.links and omitted.data["pr_id"] == "16790317"


def test_localization_poll_target_is_available_from_cli(capsys):
    """The poller reads persisted identifiers through the command, not raw state files."""
    import json
    from orchestrator.commands import localization as lc
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-09"
        st = ReleaseState(release_id=rid, ccd="2026-09-09")
        _active_step(st, "ccd", "localization")
        step = st.get_step("ccd", "localization")
        step.status = "in_flight"
        step.execution = _localization_execution()
        step.data = {
            "build_id": "176407869",
            "started_at": "2026-09-09T19:00:00Z",
        }
        st.set_step("ccd", "localization", step)
        C.save_state(st, d, rid)

        class CK:
            runs_root = d; release = rid; config = CONFIG
            complete = None; logs = None; logs_file = None
            now = None; as_of = None; pr_status = None
            execution_id = "localization-test-execution"

        assert lc.cmd_check_localization(CK) == 0
        target = json.loads(capsys.readouterr().out)
        assert target["decision"] == "poll_pipeline"
        assert target["build_id"] == "176407869"
        assert "--id 176407869" in target["az"]["status"]
        assert "result:result" in target["az"]["status"]
        step.data.update(pr_id="16790317", pr_status="active")
        st.set_step("ccd", "localization", step)
        C.save_state(st, d, rid)
        assert lc.cmd_check_localization(CK) == 0
        target = json.loads(capsys.readouterr().out)
        assert target["decision"] == "poll_pr"
        assert "--id 16790317" in target["az"]["status"]
        assert "--id 176407869" in target["az"]["run_status"]
        assert "result:result" in target["az"]["run_status"]


def test_localization_inflight_is_not_retriggered_by_release_worker():
    """Once the run is recorded, localization drops out of scout_pending."""
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", ccd_source="confirmed")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 9, 9))
    _pass_scout_checks(orch)
    orch.gate.sign()
    _clear_phase0_scout(orch)
    orch.run_until_gate()
    orch.record_scout_step("ccd", "final_reminder", "pass", "sent")
    orch.record_scout_step("ccd", "pr_reminder", "pass", "sent")
    step = st.get_step("ccd", "localization")
    step.status = "in_flight"
    step.execution = _localization_execution()
    step.data = {"build_id": "176407869", "started_at": "2026-09-09T19:00:00Z"}
    st.set_step("ccd", "localization", step)

    assert orch.current_phase_id() == "ccd"
    assert "localization" not in orch.scout_pending_steps()




def test_automation_localization_poller_is_interval():
    """The poller is an hourly INTERVAL automation; it shares ccd.localization
    with the noon trigger, which is allowed. validate() stays clean."""
    from orchestrator import automations as A
    assert A.validate(CONFIG) == []
    by = {a["slug"]: a for a in A.plan(CONFIG, "2026-09", "2026-09-09")["automations"]}
    poller = by["ccd-localization-poller"]
    assert poller["interval"] == "1 hour"
    assert (poller["schedule"] == "every 1 hour" and poller["one_shot"] is False
            and poller["on_demand"] is True)
    assert poller["steps"] == ["ccd.localization"]
    # the noon trigger also drives localization (one-shot) — shared step is fine
    assert by["ccd-noon"]["steps"] == ["ccd.localization"] and by["ccd-noon"]["one_shot"] is True
    assert by["ccd-noon"]["cleanup_when"] == [
        "step_flag:ccd.localization:started_at", "steps_done", "phase_done:ccd"]




def test_ccd_phase_not_due_before_ccd_and_no_scout_pending():
    """REGRESSION (Phase 1 ran early): the ccd phase (Code Complete Day) is anchored to
    CCD, so before the CCD it holds as 'scheduled' AND exposes NO scout_pending — the
    autonomous automation drains scout steps off scout_pending, so a non-empty list here
    would fire the CCD-day comms (final_reminder / pr_reminder / localization) days early."""
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26", ccd_source="confirmed")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 19))   # CCD-7: Phase 0 open, Phase 1 NOT
    _pass_scout_checks(orch); orch.gate.sign()
    _clear_phase0_scout(orch)                                   # finish Phase 0
    orch.run_until_gate()
    r = orch.status_report()
    # Phase 1 holds scheduled (opens on the CCD), nothing drained
    assert r["status"] == "scheduled"
    assert r["scout_pending"] == [], r["scout_pending"]
    assert next(p["done"] for p in r["phases"] if p["id"] == "ccd") == 0
    # advance the clock to the CCD → Phase 1 opens and its scout steps become pending
    orch.as_of = date(2026, 8, 26)
    orch.run_until_gate()
    r2 = orch.status_report()
    assert r2["current_phase"] == "ccd"
    assert r2["scout_pending"] == ["final_reminder"]


@pytest.mark.parametrize("result,logs,complete_log,confirmation,expected", [
    ("succeeded", _PR_LOG_WITH_URL, True, None, "announce_pr"),
    ("succeeded", _NO_PR_CREATE_TRUE_LOG, True, None, "announce_none"),
    ("succeeded", _NO_PR_CREATE_FALSE_LOG, True, None, "failed"),
    ("succeeded", "reviewed task output", True, "Owner confirms no changes", "complete_none"),
    ("failed", "", True, "Owner confirms no changes", "failed"),
    ("canceled", "", True, None, "failed"),
    ("cancelled", _PR_LOG, True, None, "failed"),
    ("partiallySucceeded", _PR_LOG, True, None, "failed"),
    (None, _PR_LOG, True, None, "wait"),
    ("unknown", _PR_LOG, True, None, "wait"),
    ("", "", True, "Owner confirms no changes", "wait"),
    ("succeeded", None, True, "Owner confirms no changes", "wait"),
    ("succeeded", "", True, "Owner confirms no changes", "wait"),
    ("succeeded", _PR_LOG, False, None, "wait"),
    ("succeeded", "no strings changed", False, "Owner confirms no changes", "wait"),
    ("succeeded", "no strings changed", True, None, "wait"),
    ("succeeded", "unrecognized output", True, None, "wait"),
])
def test_localization_positive_evidence_matrix(result, logs, complete_log, confirmation, expected):
    from datetime import datetime
    from steps.ccd import localization as L
    st = _loc_state(started_min_ago=20)
    step = st.get_step("ccd", "localization")
    step.data["started_at"] = "2026-09-09T19:00:00Z"
    st.set_step("ccd", "localization", step)
    now = datetime.fromisoformat("2026-09-09T20:00:00+00:00")
    evidence = L.RunEvidence(result, complete_log, confirmation)
    decision = L.decide(_context(st, now=now), True, logs, evidence=evidence)
    assert decision["decision"] == expected
    assert any("buildId=177219192" in link["url"] for link in decision["links"])
    if expected == "wait":
        late = datetime.fromisoformat("2026-09-10T02:00:00+00:00")
        timed_out = L.decide(_context(st, now=late), True, logs, evidence=evidence)
        assert timed_out["decision"] == "timeout"  # Never silently omit unsupported missing evidence.
        assert "buildId=177219192" in timed_out["email"]["body"]


@pytest.mark.parametrize("result", ["failed", "canceled", "partiallySucceeded", "unknown", None])
def test_negative_localization_result_never_completes_even_with_a_merged_pr(result):
    from steps.ccd import localization as L
    st = _loc_state(started_min_ago=20)
    step = st.get_step("ccd", "localization")
    step.data.update(pr_id="16790317", pr_status="completed")
    st.set_step("ccd", "localization", step)
    expected = "wait" if result in ("unknown", None) else "failed"
    assert L.decide(_context(st), True, _PR_LOG, pr_status="completed",
                    evidence=L.RunEvidence(result, True))["decision"] == expected


@pytest.mark.parametrize("result", ["failed", "canceled", "succeeded", None])
def test_localization_block_requires_reopen_and_preserves_run_history(
        tmp_path, monkeypatch, capsys, result):
    from argparse import Namespace
    from datetime import datetime
    from orchestrator import mocks
    from orchestrator import write_review as W
    from orchestrator.commands import localization as lc
    from orchestrator.commands.step_action import prepare_step
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    rid = "2026-09"
    st = ReleaseState(release_id=rid, ccd="2026-09-09", owner_email="owner@example.com")
    _active_step(st, "ccd", "localization")
    st.set_step("ccd", "localization", StepState(
        status="in_flight", execution=_localization_execution(), data={
            "build_id": "1", "started_at": "2026-09-09T19:00:00Z",
            "run_url": "https://example.test/build/1"}))
    root = str(tmp_path)
    C.save_state(st, root, rid)
    args = Namespace(runs_root=root, release=rid, config=CONFIG, as_of=None,
                     now="2026-09-09T22:30:00Z", complete="true", logs=None, logs_file=None,
                     run_result=result, logs_complete=False, pr_status=None,
                     execution_id="localization-test-execution")
    assert lc.cmd_check_localization(args) == 0
    decision = __import__("json").loads(capsys.readouterr().out.splitlines()[-1])
    if result not in ("failed", "canceled"):
        assert decision["decision"] == "timeout"
        _ack_notifications(root, rid, args.now)
    st, orch = C.load_orch(root, rid, CONFIG, datetime.fromisoformat(args.now.replace("Z", "+00:00")))
    step = st.get_step("ccd", "localization")
    assert step.status == "blocked" and step.execution["id"] == args.execution_id
    assert step.execution["write_review"]["approved_by"] == "test-reviewer"
    old_data = dict(step.data)
    action = Namespace(phase="ccd", step="localization", release=rid, reserve=True, executor="worker")
    refused = prepare_step(action, st, orch)
    assert refused["kind"] == "blocked" and not refused.get("permission_to_execute")
    assert st.get_step("ccd", "localization").data == old_data
    assert orch.reopen("ccd", "localization", "Owner inspected old run; rerun approved").changed
    with pytest.raises(ValueError, match="launch-localization --execute --auto-approve"):
        prepare_step(action, st, orch)
    cfg = lc._launch_config()
    target = {"org": lc.provider.organization(cfg["org"]),
              "project": cfg["project"].lower(), "definition_id": cfg["pipeline_id"]}
    plan = lc.provider._launch_plan(
        orch, target, {"id": "localization-repository", "type": "TfsGit"},
        7, "refs/heads/main", "a" * 40, cfg["variables"], {})
    kernel = orch._transition_kernel()
    assert kernel.reserve(
        "ccd", "localization", "worker",
        write_review={"hash": W.review_hash(orch, "ccd", "localization", plan),
                      "approved_by": "test-reviewer"}).changed
    new_execution = st.get_step("ccd", "localization").execution["id"]
    kernel.begin_reviewed_write("ccd", "localization", new_execution)
    assert st.get_step("ccd", "localization").status == "in_flight"
    started_data = dict(st.get_step("ccd", "localization").data)
    build = dict(plan.as_dict()["operations"][0]["content"])
    build.update(id=2, project={"id": target["project"], "name": cfg["project"]},
                 url=f"{target['org']}/{target['project']}/_apis/build/builds/2",
                 queueTime=started_data["in_flight_since"])
    monkeypatch.setattr(lc.provider, "read_build", lambda *a: {
        **build, "id": int(a[-1]),
        "url": f"{target['org']}/{target['project']}/_apis/build/builds/{a[-1]}"})
    C.save_state(st, root, rid)
    receipt = Namespace(runs_root=root, release=rid, config=CONFIG, as_of="2026-09-09",
                        execution_id="stale-owner", build_id="2", started_at=None, run_url=None)
    assert lc.cmd_record_localization_run(receipt) == 1
    assert C.load_state(root, rid).get_step("ccd", "localization").data == started_data
    receipt.execution_id = new_execution
    assert lc.cmd_record_localization_run(receipt) == 0
    current = C.load_state(root, rid).get_step("ccd", "localization")
    assert current.data["build_id"] == "2"
    previous = current.data["previous_runs"][-1]
    assert {k: previous[k] for k in old_data if k != "in_flight_since"} == {
        k: v for k, v in old_data.items() if k != "in_flight_since"}
    assert not ({"run_result", "logs_complete", "no_change_confirmation"} & current.data.keys())
    assert lc.cmd_record_localization_run(receipt) == 0  # Idempotent receipt, not a second refresh.
    receipt.build_id = "3"
    assert lc.cmd_record_localization_run(receipt) == 1  # Same refresh cannot replace the owned run.
    saved, resumed = C.load_orch(root, rid, CONFIG,
                                datetime.fromisoformat("2026-09-09T23:00:00+00:00"))
    action.reserve, action.execution_id = False, new_execution
    poll = prepare_step(action, saved, resumed)
    assert poll["tool"] == "check-localization"
    assert poll["payload"]["execution_id"] == new_execution
    assert saved.get_step("ccd", "localization").data["build_id"] == "2"


def test_localization_stale_poll_cannot_mutate_evidence(tmp_path):
    from argparse import Namespace
    from orchestrator.commands import localization as lc
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09")
    _active_step(st, "ccd", "localization")
    st.set_step("ccd", "localization", StepState(
        status="in_flight", execution=_localization_execution(),
        data={"build_id": "1", "started_at": "2026-09-09T19:00:00Z"}))
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
                     now="2026-09-09T20:00:00Z", execution_id="stale-owner", as_of=None,
                     complete="true", run_result="succeeded", logs=_PR_LOG, logs_complete=True)
    assert lc.cmd_check_localization(args) == 1
    assert C.load_state(str(tmp_path), st.release_id).get_step("ccd", "localization") == st.get_step(
        "ccd", "localization")


@pytest.mark.parametrize("log_kind", ["missing", "invalid_utf8", "truncated", "unrecognized", "pr", "no_pr"])
def test_localization_cli_log_proof_and_timeout(tmp_path, monkeypatch, capsys, log_kind):
    import argparse
    import json
    from orchestrator import mocks
    from orchestrator.commands import localization as lc
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", owner_email="owner@example.com")
    _active_step(st, "ccd", "localization")
    st.set_step("ccd", "localization", StepState(
        status="in_flight", execution=_localization_execution(),
        data={"build_id": "1", "started_at": "2026-09-09T19:00:00Z"}))
    C.save_state(st, str(tmp_path), st.release_id)
    log_file = tmp_path / "oneloc.log"
    if log_kind == "invalid_utf8":
        log_file.write_bytes(b"\xff\xfe")
    elif log_kind != "missing":
        log_file.write_text(
            "unrecognized output" if log_kind == "unrecognized"
            else _NO_PR_CREATE_TRUE_LOG if log_kind == "no_pr"
            else _PR_LOG,
                            encoding="utf-8")
    parser = argparse.ArgumentParser()
    lc.register(parser.add_subparsers())
    flags = ["check-localization", "--release", st.release_id,
             "--execution-id", "localization-test-execution", "--complete", "true",
             "--run-result", "succeeded", "--logs-file", str(log_file),
             "--now", "2026-09-09T22:30:00Z"]
    if log_kind != "truncated":
        flags.append("--logs-complete")
    args = parser.parse_args(flags)
    args.runs_root, args.config = str(tmp_path), CONFIG
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    expected_decision = {"pr": "announce_pr", "no_pr": "announce_none"}.get(log_kind, "timeout")
    assert result["decision"] == expected_decision
    _ack_notifications(str(tmp_path), st.release_id, args.now)
    stored = C.load_state(str(tmp_path), st.release_id).get_step("ccd", "localization")
    expected_status = {"pr": "in_flight", "no_pr": "done"}.get(log_kind, "blocked")
    assert stored.status == expected_status
    assert not ({"run_result", "logs_complete", "no_change_confirmation"} & stored.data.keys())
