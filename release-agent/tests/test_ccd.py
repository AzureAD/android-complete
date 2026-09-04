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




def test_localization_decide_branches():
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
    assert dpr["decision"] == "complete_pr" and dpr["pr_id"] == "16790317"
    assert dpr["chat"]["chatId"] == L.CONFIG["code_reviews_chat_id"]
    assert any("16790317" in l["url"] for l in dpr["links"])
    # proof: the PR case ALSO carries the pipeline run link (build id)
    assert any("buildId=177219192" in l["url"] for l in dpr["links"])
    # the Code reviews post @mentions the release engineer + asks for an EOD merge
    assert '<at id="0">' in dpr["chat"]["content"] and "merged before EOD" in dpr["chat"]["content"]
    m = dpr["chat"]["mentions"][0]
    assert m["mentioned"]["user"]["id"] == "pedroro@microsoft.com"

    dn = L.decide(st3, True, "no strings changed", now)
    assert dn["decision"] == "complete_none"
    # proof: even with NO PR, the Details box gets the pipeline run link as evidence
    assert dn["links"] and any("buildId=177219192" in l["url"] for l in dn["links"])




def test_localization_review_post_no_owner_has_no_mention():
    """With no owner email, the post still goes out but without an @mention array."""
    from steps.ccd import localization as L
    st = _loc_state(started_min_ago=60)
    st.owner_email = ""
    st.owner_name = ""
    from datetime import datetime, timezone
    d = L.decide(st, True, _PR_LOG, datetime.now(timezone.utc))
    assert d["decision"] == "complete_pr"
    assert "mentions" not in d["chat"]
    assert "merged before EOD" in d["chat"]["content"]




def test_localization_command_lifecycle_wait_then_complete():
    """record-localization-run leaves the step in-flight; a wait poll keeps it
    pending; a complete poll with a PR log marks it done with the PR link."""
    from orchestrator.commands import localization as lc
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
        assert s1.data["build_id"] == "176407869" and s1.status == "pending"

        class CKwait:
            runs_root = d; release = rid; config = CONFIG
            complete = "false"; logs = None; logs_file = None
            now = "2026-09-09T19:30:00Z"; as_of = None
        lc.cmd_check_localization(CKwait)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "pending"

        class CKdone:
            runs_root = d; release = rid; config = CONFIG
            complete = "true"; logs = _PR_LOG; logs_file = None
            now = "2026-09-09T20:00:00Z"; as_of = None
        lc.cmd_check_localization(CKdone)
        done = C.load_state(d, rid).get_step("ccd", "localization")
        assert done.status == "done"
        assert any("16790317" in l["url"] for l in done.links)
        assert done.data["build_id"] == "176407869"     # data preserved through completion




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
            now = "2026-09-09T15:30:00Z"; as_of = None      # 3.5h later
        lc.cmd_check_localization(CK)
        assert C.load_state(d, rid).get_step("ccd", "localization").status == "blocked"




def test_automation_localization_poller_is_interval():
    """The poller is an INTERVAL automation (every 10 min); it shares ccd.localization
    with the noon trigger, which is allowed. validate() stays clean."""
    from orchestrator import automations as A
    assert A.validate(CONFIG) == []
    by = {a["slug"]: a for a in A.plan(CONFIG, "2026-09", "2026-09-09")["automations"]}
    poller = by["ccd-localization-poller"]
    assert poller["interval"] == "10 minutes"
    assert poller["schedule"] == "every 10 minutes" and poller["one_shot"] is False
    assert poller["steps"] == ["ccd.localization"]
    # the noon trigger also drives localization (one-shot) — shared step is fine
    assert by["ccd-noon"]["steps"] == ["ccd.localization"] and by["ccd-noon"]["one_shot"] is True




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

