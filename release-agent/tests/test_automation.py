"""Release-agent tests — automation. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




def test_teams_notify_is_scout_optout_item():
    """teams_notify (source: scout, opt_out) is a required auto item verified by the
    skill; Python verify() must not touch it, and 'degraded' (email-only fallback)
    satisfies the gate — a Teams hiccup never blocks a release."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    # it's a scout item → verify() leaves it pending for the skill
    orch.gate.verify()
    assert st.readiness_items.get("teams_notify", {}).get("status", "pending") == "pending"
    tn = next(i for i in orch.gate.checklist()["items"] if i["id"] == "teams_notify")
    assert tn["verify"] == "auto" and tn["source"] == "scout" and tn["opt_out"] is True
    # gate stays closed until it's recorded
    orch.gate.record_check("oncall_now", "pass", "not on-call")
    orch.gate.record_check("adx_access", "pass", "can query")
    orch.gate.record_check("silent_perms", "pass", "auto-approved")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    orch.gate.sign()
    assert not st.readiness_signed
    # degraded (Teams unreachable → email only) satisfies the opt-out item
    res = orch.gate.record_check("teams_notify", "degraded", "Teams unreachable — email only")
    assert "error" not in res
    tn2 = next(i for i in orch.gate.checklist()["items"] if i["id"] == "teams_notify")
    assert tn2["status"] == "degraded" and tn2["satisfied"]
    assert st.readiness_signed




# ---- push notifications: DAILY PHASE DIGEST model ----
# Setup (readiness + CCD) is interactive → no push. First push is a phase opening
# (Phase 0 at CCD-7); then a daily status digest while the phase has outstanding work.

def test_notify_silent_before_phase_opens():
    from orchestrator import render
    st, orch = _ccd_orch("2026-06-20")   # opens 2026-07-01, ~11 days out
    assert render.notification(orch.status_report()) == ""




def test_notify_silent_two_days_before_open():
    """No pre-open heads-up anymore — the first push is when the phase opens."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-06-29")   # opens in 2 days
    assert render.notification(orch.status_report()) == ""




def test_notify_phase0_digest_when_open():
    """Phase 0 open but Scout's own steps not yet run → SILENT (premature — the digest
    reports the settled 'needs YOU' picture, so it holds until Scout drains its steps).
    Once notice/reminders/lockdown are done, the daily phase digest fires."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-07-01")   # Phase 0 opens today, signed by _ccd_orch
    # premature: Scout still owes notice/flight_reminder/lockdown → no push yet
    assert render.notification(orch.status_report()) == ""
    _drain_phase0_scout_only(orch)       # Scout runs its 3 steps; human holds remain
    msg = render.notification(orch.status_report())
    assert "Phase 0" in msg and "Pre-flight" in msg
    assert render.notification_subject(orch.status_report()) == "Release 2026-07 — Phase 0 status"




def test_digest_silent_while_scout_pending():
    """CORE OF THIS FIX: the daily digest reports the SETTLED 'needs YOU' picture, so
    while the open phase still has un-run Scout steps (scout_pending) every renderer
    stays silent — no premature email that lists Scout's own undone work. Draining the
    scout steps flips all three renderers on together."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-07-01")          # signed, Phase 0 open
    r = orch.status_report()
    assert r["scout_pending"] == ["notice", "flight_reminder", "lockdown"]
    assert render._digest_model(r) is None       # premature → silent
    assert (render.notification(r) == "" and render.notification_markdown(r) == ""
            and render.notification_html(r) == "")
    _drain_phase0_scout_only(orch)               # Scout finishes its own steps
    r2 = orch.status_report()
    assert r2["scout_pending"] == []
    assert render._digest_model(r2) is not None   # now the settled digest is due
    assert render.notification(r2) and render.notification_html(r2)




def test_notify_json_carries_owner_and_subject():
    from orchestrator import render
    import json as _json
    st, orch = _orch()                   # signed, phase due
    orch.state.owner_email = "owner@example.com"
    orch.run_until_gate()
    r = orch.status_report()
    payload = {"message": render.notification(r), "subject": render.notification_subject(r),
               "owner_email": r["owner_email"], "release": r["release_id"]}
    assert payload["owner_email"] == "owner@example.com"
    assert payload["message"] and "Phase" in payload["subject"]
    assert _json.loads(_json.dumps(payload))["owner_email"] == "owner@example.com"




def test_notify_silent_when_halted_or_complete():
    from orchestrator import render
    st, orch = _orch()
    r = orch.status_report(); r["halted"] = True
    assert render.notification(r) == ""
    r2 = orch.status_report(); r2["status"] = "complete"
    assert render.notification(r2) == ""




def test_registry_relocates_release_automations_into_release_folder():
    """Release-scoped automations live in <runs_root>/<release>/_automations.json (owned
    by the release); shared ones stay machine-wide."""
    from orchestrator.registry import AutomationRegistry
    import os as _os, json as _json
    with tempfile.TemporaryDirectory() as tmp:
        reg = AutomationRegistry(tmp, release="2026-08")
        reg.register("a2", "Phase-3 watcher", release="2026-08", steps=["bug_bash.bugbash_complete"])
        reg.register("sh", "Release push reminders", shared=True, purpose="push")
        rel_file = _os.path.join(tmp, "2026-08", "_automations.json")
        shared_file = _os.path.join(tmp, "_automations.json")
        # the release automation is co-located with the release; shared stays machine-wide
        assert [e["id"] for e in _json.load(open(rel_file))] == ["a2"]
        assert [e["id"] for e in _json.load(open(shared_file))] == ["sh"]
        # release listing reads the release file + shared; deregister finds it in-folder
        assert {e["id"] for e in reg.list(release="2026-08")} == {"a2"}
        assert reg.deregister("a2") is True
        assert reg.list(release="2026-08") == []




def test_automation_plan_derives_specs_from_ccd():
    """`plan` turns automations.yaml + the release CCD into concrete specs: a one-shot
    pinned to the EXACT CCD date (cron on the CCD's day+month, NOT 'every <weekday>'
    which would fire the next matching weekday a week early), the steps it drives, and
    the registration args (so linkage is captured when it's created)."""
    from orchestrator import automations as A
    result = A.plan(CONFIG, "2026-09", "2026-09-09")   # CCD Sept 9 (a Wednesday)
    assert result["problems"] == []
    by = {a["slug"]: a for a in result["automations"]}
    assert by["ccd-morning"]["steps"] == ["ccd.final_reminder", "ccd.pr_reminder"]
    assert by["ccd-morning"]["fire_at"] == "09:00"
    # cron: minute hour day month * → 0 9 9 9 * = 09:00 on Sept 9 exactly
    assert by["ccd-morning"]["schedule"] == "cron: 0 9 9 9 *"
    assert by["ccd-noon"]["schedule"] == "cron: 0 12 9 9 *"
    assert by["ccd-noon"]["registration"]["steps"] == ["ccd.localization"]
    # the poller stays an interval automation (not date-pinned)
    assert by["ccd-localization-poller"]["schedule"] == "every 10 minutes"
    # registration carries slug + schedule so sync can re-pin on a CCD move
    assert by["ccd-morning"]["registration"]["slug"] == "ccd-morning"
    assert by["ccd-morning"]["registration"]["schedule"] == "cron: 0 9 9 9 *"


def test_automation_names_follow_standard_format():
    """Every provisioned automation title is `<release-id> · <scope> — <label>`, where <scope>
    is the phase's DISPLAY name (from phases.yaml) and <label> is the yaml `label`. This keeps
    titles consistent + scannable (release first, then phase, then purpose)."""
    from orchestrator import automations as A
    result = A.plan(CONFIG, "2026-09", "2026-09-09")
    by = {a["slug"]: a for a in result["automations"]}
    assert by["ccd-morning"]["name"] == "2026-09 · Code Complete Day — morning reminders"
    assert by["ccd-noon"]["name"] == "2026-09 · Code Complete Day — noon localization"
    assert by["build-verify-rc-poller"]["name"] == "2026-09 · Build & Lib Verification — RC verification poller"
    assert by["bug-bash-update-poller"]["name"] == "2026-09 · Test / Bug Bash — bug-bash update poller"
    # the registration name matches the display name (so the registry row is the standard title)
    assert by["ccd-morning"]["registration"]["name"] == by["ccd-morning"]["name"]
    # every name has exactly the three standard segments
    for a in result["automations"]:
        assert a["name"].startswith("2026-09 · ")
        assert " — " in a["name"]


def test_automation_name_helper_and_phase_label():
    """The name-builder + phase-label helpers are the single source of the standard title."""
    from orchestrator import automations as A
    assert A.automation_name("2026-08", "Release-wide", "push reminders") == \
        "2026-08 · Release-wide — push reminders"
    # scope falls back to 'Release-wide' when empty (non-phase automations)
    assert A.automation_name("2026-08", "", "x") == "2026-08 · Release-wide — x"
    assert A.phase_label(CONFIG, "ccd") == "Code Complete Day"
    assert A.phase_label(CONFIG, None) == "Release-wide"




def test_automation_sync_repins_on_ccd_change():
    """When the CCD moves, `automation sync` reports which registered CCD automations
    have a stale cron and the new schedule to apply — matching by slug so the noon
    trigger and the poller (which share the ccd.localization step) aren't confused."""
    import tempfile as _tf, json as _json, io, contextlib, argparse
    from orchestrator import cli_common as C
    from orchestrator.registry import AutomationRegistry
    from orchestrator.commands import automation as A
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        st = ReleaseState(release_id=rid, ccd="2026-08-26", ccd_source="confirmed")
        C.save_state(st, d, rid)
        reg = AutomationRegistry(d)
        reg.register("a-morn", "CCD morning", release=rid, slug="ccd-morning",
                     steps=["ccd.final_reminder", "ccd.pr_reminder"], schedule="cron: 0 9 26 8 *")
        reg.register("a-noon", "CCD noon", release=rid, slug="ccd-noon",
                     steps=["ccd.localization"], schedule="cron: 0 12 26 8 *")
        reg.register("a-poll", "poller", release=rid, slug="ccd-localization-poller",
                     steps=["ccd.localization"], schedule="every 10 minutes")

        def sync():
            ns = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, json=True)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                A._cmd_sync(ns)
            return _json.loads(buf.getvalue())

        # in sync → nothing changed
        u0 = {u["slug"]: u for u in sync()["updates"]}
        assert all(not u["changed"] for u in u0.values())
        # noon matched to the CRON, not the poller's 'every 10 minutes' (slug disambiguates)
        assert u0["ccd-noon"]["desired_schedule"] == "cron: 0 12 26 8 *"
        # move the CCD within the month → the two cron automations go stale, poller unchanged
        st.ccd = "2026-08-27"; C.save_state(st, d, rid)
        u1 = {u["slug"]: u for u in sync()["updates"]}
        assert u1["ccd-morning"]["changed"] and u1["ccd-morning"]["desired_schedule"] == "cron: 0 9 27 8 *"
        assert u1["ccd-noon"]["changed"] and u1["ccd-noon"]["desired_schedule"] == "cron: 0 12 27 8 *"
        assert not u1["ccd-localization-poller"]["changed"]




def test_tick_advances_and_reports(tmp=None):
    """A headless `tick` advances AGENT steps but can't run Scout steps, so while the
    open phase still has scout_pending the digest STAYS SILENT (no premature email).
    Once Scout's steps are drained (the skill's job — simulated here), the next tick
    returns a digest listing completed steps + what needs the user."""
    import tempfile as _tf
    from orchestrator.commands import notify as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "2026-07"
        # signed release, CCD reached so Phase 0 is open
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-07-08",
                          ccd_source="default", owner_email="o@x.com")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch)
        orch.gate.sign()
        C.save_state(st, d, rid)

        class A:
            runs_root = d
            release = rid
            config = CONFIG
            as_of = "2026-07-08"
            force = False
            json = True
        # Scout steps still pending → tick advances agent steps but the digest is SILENT
        premature = ncmd._notify_payload(A, rid, advance=True)
        assert premature["message"] == ""
        # skill runs the Phase-0 scout steps; now the digest is genuinely due
        st_now = C.load_state(d, rid)
        orch2 = Orchestrator(CONFIG, st_now)
        _drain_phase0_scout_only(orch2)
        C.save_state(st_now, d, rid)
        payload = ncmd._notify_payload(A, rid, advance=True)
        # advanced: state file now shows Phase-0 progress + holding at a gate
        st2 = C.load_state(d, rid)
        assert st2.status in ("holding_gate", "awaiting_action")
        assert Orchestrator(CONFIG, st2).status_report()["done"] > 0
        # digest reflects the state machine
        assert payload["message"] and "Phase 0" in payload["message"]
        assert "Completed" in payload["message"]
        assert payload["owner_email"] == "o@x.com"




def test_tick_dedup_same_day():
    """A second tick on the same day sends nothing (once-per-day digest)."""
    import tempfile as _tf
    from orchestrator.commands import notify as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "2026-07"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-07-08",
                          ccd_source="default", owner_email="o@x.com")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch)
        orch.gate.sign()
        _drain_phase0_scout_only(orch)        # skill ran Scout's steps → digest now due
        C.save_state(st, d, rid)

        class A:
            runs_root = d
            release = rid
            config = CONFIG
            as_of = "2026-07-08"
            force = False
            json = True
        first = ncmd._notify_payload(A, rid, advance=True)
        second = ncmd._notify_payload(A, rid, advance=True)
        assert first["message"] and second["message"] == ""




def test_tick_payload_carries_teams_block_when_enabled():
    """When a digest is due and Teams is enabled, the tick payload includes a Teams
    delivery descriptor. The repo config targets the Scout bot, so it's a scout_bot
    delivery carrying the plain-text digest. Deduped second tick carries none."""
    import tempfile as _tf
    from orchestrator.commands import notify as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "2026-07"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-07-08",
                          ccd_source="default", owner_email="o@x.com")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch)
        orch.gate.sign()
        _drain_phase0_scout_only(orch)        # skill ran Scout's steps → digest now due
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG
            as_of = "2026-07-08"; force = False; json = True
        p = ncmd._notify_payload(A, rid, advance=True)
        assert p["channels"]["teams"] is True
        assert p["message"] and p["teams"] is not None
        assert p["teams"]["via"] == "scout_bot"
        # scout bot gets the MARKDOWN digest (blank-line paragraphs survive collapse)
        from orchestrator import render
        st_now = C.load_state(d, rid)
        expected_md = render.notification_markdown(Orchestrator(CONFIG, st_now).status_report())
        assert p["teams"]["text"] == expected_md
        assert "\n\n" in p["teams"]["text"] and "**Release" in p["teams"]["text"]
        # deduped second tick → message empty AND no teams delivery
        p2 = ncmd._notify_payload(A, rid, advance=True)
        assert p2["message"] == "" and p2["teams"] is None




def test_ui_automation_verdicts_per_config():
    """Per (case, flight, variant): pass if any run passed; fail if a real result but never
    passed; NotApplicable if only skipped. Flight comes from the build; variant from the run
    name. Runs with no PROD/RC-MSAL marker (e.g. 'Lab Api Tests') are ignored."""
    from tools import pipelines as P
    flight = {"b1": "ECS", "b2": "Local"}
    runs_by_build = {
        "b1": {"value": [{"id": "r1", "name": "PROD MSAL - RC Broker (API 32)"},
                         {"id": "r2", "name": "RC MSAL - PROD Broker (API 32)"}]},
        "b2": {"value": [{"id": "r3", "name": "PROD MSAL - RC Broker (API 32)"},
                         {"id": "r4", "name": "Lab Api Tests"}]},
    }
    results_by_run = {
        "r1": [{"automatedTestName": "test_100_X", "outcome": "Failed"},
               {"automatedTestName": "test_100_X", "outcome": "Passed"}],       # retry -> Passed
        "r2": [{"automatedTestName": "test_100_X", "outcome": "Failed"}],        # ECS/rc -> Failed
        "r3": [{"automatedTestName": "test_100_X", "outcome": "NotExecuted"}],   # Local/prod -> N/A
        "r4": [{"automatedTestName": "test_999_Unplaceable", "outcome": "Passed"}],  # no variant -> ignored
    }

    def fake_get(url, timeout):
        for b, data in runs_by_build.items():
            if f"Build/Build/{b}" in url:
                return (True, data, "")
        return (True, {"value": []}, "")

    def fake_flight(org, project, bid, timeout=60):
        return flight.get(bid)

    def fake_run_results(org, project, run_id, timeout=90, **k):
        return (True, results_by_run.get(run_id, []), "")

    o = (P._ado_rest_get, P._flight_provider, P._run_results)
    P._ado_rest_get, P._flight_provider, P._run_results = fake_get, fake_flight, fake_run_results
    try:
        ok, v, d = P.ui_automation_verdicts("ORG", "PROJ", ["b1", "b2"])
    finally:
        P._ado_rest_get, P._flight_provider, P._run_results = o
    assert ok, d
    assert v == {100: {("ECS", "prod"): "Passed", ("ECS", "rc"): "Failed",
                       ("Local", "prod"): "NotApplicable"}}
    assert 999 not in v                          # unplaceable run never recorded




def test_fill_ui_automation_results_maps_configs():
    """Each plan point (case, config) takes the outcome of its matching (flight, variant); a
    config with no verdict is NotApplicable. Mirrors the user's test_3321136 example."""
    from tools import pipelines as P
    from tools import testplans as T
    verdicts = {3321136: {("ECS", "prod"): "Passed", ("ECS", "rc"): "Failed",
                          ("Local", "prod"): "NotApplicable"}}   # Local/rc omitted -> N/A
    points = [
        {"id": 1, "testCase": {"id": "3321136"}, "configuration": {"id": "292"}},  # ECS prod -> Passed
        {"id": 2, "testCase": {"id": "3321136"}, "configuration": {"id": "294"}},  # ECS rc  -> Failed
        {"id": 3, "testCase": {"id": "3321136"}, "configuration": {"id": "328"}},  # Local prod -> N/A
        {"id": 4, "testCase": {"id": "3321136"}, "configuration": {"id": "344"}},  # Local rc (no verdict) -> N/A
    ]
    sent = []

    def fake_get_all(url, timeout, **k):
        if "/suites?" in url:
            return (True, [{"id": 555, "name": T.BROKER_UI_SUITE_NAME}], "")
        if "/points?" in url:
            return (True, points, "")
        return (True, [], "")

    def fake_send(url, method, body, timeout):
        sent.append((url, body))
        return (True, {}, "")

    og, os_ = P._ado_rest_get_all, P._ado_rest_send
    P._ado_rest_get_all, P._ado_rest_send = fake_get_all, fake_send
    try:
        ok, summ, d = T.fill_ui_automation_results(900, verdicts)
    finally:
        P._ado_rest_get_all, P._ado_rest_send = og, os_
    assert ok, d
    assert summ["set_passed"] == 1 and summ["set_failed"] == 1 and summ["set_not_applicable"] == 2
    assert summ["cases_touched"] == 1
    by_outcome = {}
    for u, b in sent:
        ids = set(u.split("/points/")[1].split("?")[0].split(","))
        by_outcome.setdefault(b["outcome"], set()).update(ids)
    assert by_outcome["Passed"] == {"1"}
    assert by_outcome["Failed"] == {"2"}
    assert by_outcome["NotApplicable"] == {"3", "4"}




def test_record_nativeauth_notify_stores_or_holds():
    """record-nativeauth-notify with --engineer stores it + marks done; without --engineer it
    holds the step for the owner."""
    import tempfile, argparse
    from orchestrator import cli_common as _C
    from orchestrator.commands import bugbash_chat as BC
    from steps.bug_bash.notify_native_auth import notified_engineer
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        _C.save_state(_na_state(), d, rid)
        ns = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, as_of=None,
                                engineer="silviu.petrescu")
        assert BC.cmd_record_nativeauth_notify(ns) == 0
        again = _C.load_state(d, rid)
        assert again.is_done("bug_bash", "notify_native_auth")
        assert notified_engineer(again) == "silviu.petrescu"

        # no engineer -> attention hold
        _C.save_state(_na_state(), d, rid)
        ns2 = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, as_of=None, engineer=None)
        assert BC.cmd_record_nativeauth_notify(ns2) == 2
        after = _C.load_state(d, rid)
        assert not after.is_done("bug_bash", "notify_native_auth")
        assert after.get_step("bug_bash", "notify_native_auth").status == "blocked"

