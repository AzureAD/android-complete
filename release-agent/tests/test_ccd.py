"""Release-agent tests — ccd. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




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
    assert not st.readiness_signed
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled with pipeline")
    assert st.readiness_signed
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
    out = final_reminder.build(_ccd_state())
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
    out = pr_reminder.build(_ccd_state())
    assert out.kind == "needs_skill" and out.tool == "workiq_send_chat_message"
    assert out.payload["chatId"] == pr_reminder.CONFIG["live_chat_id"]
    assert out.payload["contentType"] == "html"
    body = out.payload["content"]
    assert "11:00 PM" in body                       # branch cut deadline
    assert "moghosh@microsoft.com" in body and "Moumita" in body  # approver
    assert "noon" in body                           # localization cutoff




def test_ccd_localization_build_triggers_pipeline_405133():
    """localization resolves to a pipeline-run action for 405133 with
    isCreatePrSelected=true, an az fallback, and the repo-PR link."""
    from steps.ccd import localization
    out = localization.build(_ccd_state())
    assert out.kind == "needs_skill" and out.tool == "azure_devops-pipelines_run_pipeline"
    assert out.payload["pipelineId"] == 405133
    assert out.payload["variables"] == {"isCreatePrSelected": {"value": "true"}}
    trig = out.payload["_trigger"]
    assert "az pipelines run --id 405133" in trig["az_fallback"]
    urls = [l["url"] for l in out.payload["links"]]
    assert any("pullrequests" in u for u in urls)   # where the OneLoc PR lands




def test_ccd_steps_blocked_without_ccd():
    """Every ccd comms/trigger step blocks cleanly if the release has no CCD."""
    from steps.ccd import final_reminder, pr_reminder, localization
    st = ReleaseState(release_id="x")               # no ccd
    for mod in (final_reminder, pr_reminder, localization):
        out = mod.build(st)
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
    trig = L.build(st).payload["_trigger"]
    az = trig["az_read"]
    assert "az pipelines build show" in az["status"]
    assert "resource timeline" in az["log_id"] and "OneLocBuild@3" in az["log_id"]
    assert "resource logs" in az["log"] and "{build_id}" in az["log"] and "{log_id}" in az["log"]
    assert "az repos pr show" in az["pr_status"] and "{pr_id}" in az["pr_status"]




def test_localization_decide_branches(monkeypatch):
    import pytest
    from steps.ccd import localization as L
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)

    st = _loc_state(started_min_ago=20)
    assert L.decide(st, False, None, now)["decision"] == "wait"

    st2 = _loc_state(started_min_ago=4 * 60 + 5)          # 4h+ → timeout
    d = L.decide(st2, False, None, now)
    assert d["decision"] == "timeout"
    assert d["email"]["to"] == ["pedroro@microsoft.com"]

    st3 = _loc_state(started_min_ago=60)
    dpr = L.decide(st3, True, _PR_LOG, now)
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

    dn = L.decide(st3, True, "no strings changed", now)
    assert dn["decision"] == "complete_none"
    # proof: even with NO PR, the Details box gets the pipeline run link as evidence
    assert dn["links"] and any("buildId=177219192" in l["url"] for l in dn["links"])

    step = st3.get_step("ccd", "localization")
    step.data.update({
        "pr_id": "16790317", "pr_url": L.pr_url("16790317"),
    })
    st3.set_step("ccd", "localization", step)
    # A fast merge cannot bypass the required initial delivery acknowledgement.
    assert L.decide(st3, False, now=now, pr_status="completed")["decision"] == "announce_pr"
    step.data["pr_announced_at"] = "2026-09-09T20:05:00Z"
    st3.set_step("ccd", "localization", step)
    before = datetime.fromisoformat("2026-09-09T22:59:59+00:00")
    deadline = datetime.fromisoformat("2026-09-09T23:00:00+00:00")
    assert L.decide(st3, False, now=before, pr_status="active")["decision"] == "wait_for_merge"
    overdue = L.decide(st3, False, now=deadline, pr_status="active")
    assert overdue["decision"] == "warn_unmerged"
    assert "translated strings at risk" in overdue["chat"]["content"]
    assert "release remains on schedule" in overdue["chat"]["content"]
    step.data["merge_deadline_alert_at"] = deadline.isoformat()
    st3.set_step("ccd", "localization", step)
    assert L.decide(st3, False, now=deadline, pr_status="active")["decision"] == "wait_for_merge"
    assert L.decide(st3, False, now=deadline, pr_status="completed")["decision"] == "merged"
    omission = datetime.fromisoformat("2026-09-10T01:00:00+00:00")
    omitted = L.decide(st3, False, now=omission, pr_status="active")
    assert omitted["decision"] == "omit_unmerged"
    assert "release continues without these translated strings" in omitted["note"]
    assert L.decide(st3, False, now=omission, pr_status="completed")["decision"] == "merged"

    monkeypatch.setattr(L, "get_tz", lambda _name: None)
    with pytest.raises(ValueError, match="timezone data unavailable"):
        L.decide(st3, False, now=deadline, pr_status="active")




def test_localization_review_post_no_owner_has_no_mention():
    """With no owner email, the post still goes out but without an @mention array."""
    from steps.ccd import localization as L
    st = _loc_state(started_min_ago=60)
    st.owner_email = ""
    st.owner_name = ""
    from datetime import datetime, timezone
    d = L.decide(st, True, _PR_LOG, datetime.now(timezone.utc))
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
        C.save_state(st, d, rid)

        class RR:
            runs_root = d; release = rid
            build_id = "176407869"; run_url = None; started_at = "2026-09-09T19:00:00Z"
        lc.cmd_record_localization_run(RR)
        s1 = C.load_state(d, rid).get_step("ccd", "localization")
        assert s1.data["build_id"] == "176407869" and s1.status == "in_flight"

        target = L.poll_target(C.load_state(d, rid))
        assert target["decision"] == "poll_pipeline" and target["build_id"] == "176407869"
        assert "--id 176407869" in target["az"]["status"]

        class CKwait:
            runs_root = d; release = rid; config = CONFIG
            complete = "false"; logs = None; logs_file = None
            now = "2026-09-09T19:30:00Z"; as_of = None; pr_status = None
        lc.cmd_check_localization(CKwait)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "in_flight"

        class CKdone:
            runs_root = d; release = rid; config = CONFIG
            complete = "true"; logs = _PR_LOG; logs_file = None
            now = "2026-09-09T20:00:00Z"; as_of = None; pr_status = None
        lc.cmd_check_localization(CKdone)
        pending = C.load_state(d, rid).get_step("ccd", "localization")
        assert pending.status == "in_flight" and pending.data["pr_id"] == "16790317"
        assert any("16790317" in l["url"] for l in pending.links)
        target = L.poll_target(C.load_state(d, rid))
        assert target["decision"] == "poll_pr" and target["pr_id"] == "16790317"
        assert "--id 16790317" in target["az"]["status"]

        class ACKinitial:
            runs_root = d; release = rid; kind = "initial"; pr_id = "16790317"
        assert lc.cmd_record_localization_post(ACKinitial) == 0
        assert lc.cmd_record_localization_post(ACKinitial) == 0
        announced = C.load_state(d, rid).get_step("ccd", "localization")
        assert announced.data["pr_announced_at"]

        class CKactive:
            runs_root = d; release = rid; config = CONFIG
            complete = "false"; logs = None; logs_file = None
            now = "2026-09-09T22:59:00Z"; as_of = None; pr_status = "active"
        lc.cmd_check_localization(CKactive)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "in_flight"

        CKactive.now = "2026-09-09T23:00:00Z"
        lc.cmd_check_localization(CKactive)
        escalated = C.load_state(d, rid).get_step("ccd", "localization")
        assert escalated.status == "in_flight" and not escalated.data.get("merge_deadline_alert_at")

        class ACKdeadline:
            runs_root = d; release = rid; kind = "deadline"; pr_id = "16790317"
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
        C.save_state(st, d, rid)

        class RR:
            runs_root = d; release = rid
            build_id = "1"; run_url = None; started_at = "2026-09-09T12:00:00Z"
        lc.cmd_record_localization_run(RR)

        class CK:
            runs_root = d; release = rid; config = CONFIG
            complete = "false"; logs = None; logs_file = None
            now = "2026-09-09T15:30:00Z"; as_of = None; pr_status = None
        lc.cmd_check_localization(CK)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "blocked"


def test_localization_command_omits_unmerged_pr_at_6pm():
    from orchestrator.commands import localization as lc
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-09"
        st = ReleaseState(release_id=rid, ccd="2026-09-09")
        step = st.get_step("ccd", "localization")
        step.status = "in_flight"
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
            complete = None; logs = None; logs_file = None
            now = "2026-09-10T01:00:00Z"; as_of = None; pr_status = "active"

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
        step = st.get_step("ccd", "localization")
        step.status = "in_flight"
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

        assert lc.cmd_check_localization(CK) == 0
        target = json.loads(capsys.readouterr().out)
        assert target["decision"] == "poll_pipeline"
        assert target["build_id"] == "176407869"
        assert "--id 176407869" in target["az"]["status"]


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
    assert by["ccd-noon"]["cleanup_when"] == "step_flag:ccd.localization:started_at"




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
    assert set(r2["scout_pending"]) == {"final_reminder", "pr_reminder", "localization"}
