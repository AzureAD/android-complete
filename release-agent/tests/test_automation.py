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
        reg.register("a2", "Phase-3 watcher", release="2026-08",
                     steps=["bug_bash.bugbash_complete"], cleanup_when="steps_done")
        reg.register("sh", "Release push reminders", shared=True, purpose="push",
                     cleanup_when="manual")
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
    assert by["ccd-localization-poller"]["schedule"] == "every 1 hour"
    # registration carries slug + schedule so sync can re-pin on a CCD move
    assert by["ccd-morning"]["registration"]["slug"] == "ccd-morning"
    assert by["ccd-morning"]["registration"]["schedule"] == "cron: 0 9 9 9 *"
    assert by["ccd-morning"]["registration"]["cleanup_when"] == "steps_done"
    assert by["build-verify-rc-poller"]["cleanup_when"] == "steps_settled"


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


def test_cli_plan_separates_startup_and_on_demand_automations(capsys):
    import json
    import tempfile as _tf
    from orchestrator import cli
    with _tf.TemporaryDirectory() as d:
        rid = "2026-09"
        C.save_state(ReleaseState(release_id=rid, ccd="2026-09-09"), d, rid)
        base = ["--runs-root", d, "automation", "plan", "--release", rid, "--json"]
        assert cli.main(base) == 0
        startup = json.loads(capsys.readouterr().out)["automations"]
        assert startup and all(not a["on_demand"] for a in startup)
        assert {a["slug"] for a in startup} == {"ccd-morning", "ccd-noon"}

        assert cli.main(base[:-1] + ["--on-demand", "build-verify-rc-poller", "--json"]) == 0
        on_demand = json.loads(capsys.readouterr().out)["automations"]
        assert [a["slug"] for a in on_demand] == ["build-verify-rc-poller"]


def test_cleanup_plan_applies_declared_lifecycle_rules():
    from orchestrator import automations as A
    from orchestrator.state import StepState
    import yaml
    st = ReleaseState(release_id="2026-09", status="running")
    for sid in ("final_reminder", "pr_reminder", "localization"):
        st.set_step("ccd", sid, StepState(status="done"))
    st.set_step("build_verify", "rc_report", StepState(status="blocked"))
    st.set_step("bug_bash", "bugbash_updates",
                StepState(status="done", data={"poll_complete": True}))
    entries = [
        {"id": "morning", "name": "Morning", "kind": "step-driving",
         "steps": ["ccd.final_reminder", "ccd.pr_reminder"], "cleanup_when": "steps_done"},
        {"id": "rc", "name": "RC poller", "kind": "step-driving",
         "steps": ["build_verify.rc_report"], "cleanup_when": "steps_settled"},
        {"id": "bug", "name": "Bug poller", "kind": "step-driving",
         "steps": ["bug_bash.bugbash_updates"],
         "cleanup_when": ["step_flag:bug_bash.bugbash_updates:poll_complete",
                          "phase_done:bug_bash"]},
        {"id": "loc", "name": "Localization poller", "kind": "step-driving",
         "steps": ["ccd.localization"], "cleanup_when": "steps_settled"},
        {"id": "push", "name": "Push", "kind": "release-level",
         "steps": [], "cleanup_when": "release_done"},
        {"id": "manual", "name": "Manual", "kind": "release-level",
         "steps": [], "cleanup_when": "manual"},
    ]
    first = A.cleanup_plan(st, entries, CONFIG)
    assert [r["id"] for r in first["removals"]] == ["bug", "loc", "morning", "rc"]
    assert first["problems"] == []
    st.status = "complete"
    second = A.cleanup_plan(st, entries, CONFIG)
    assert [r["id"] for r in second["removals"]][-1] == "push"
    assert "manual" not in [r["id"] for r in second["removals"]]

    # Owner sign-off also ends the Bug Bash poller when tests did not reach 100%.
    update_step = st.get_step("bug_bash", "bugbash_updates")
    update_step.data.pop("poll_complete")
    st.set_step("bug_bash", "bugbash_updates", update_step)
    phase = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    for step in next(p for p in phase["phases"] if p["id"] == "bug_bash")["steps"]:
        st.set_step("bug_bash", step["id"], StepState(status="done"))
    signed_off = A.cleanup_plan(st, entries, CONFIG)
    assert "bug" in [r["id"] for r in signed_off["removals"]]


def test_registry_requires_cleanup_rule():
    from orchestrator.registry import AutomationRegistry
    import tempfile as _tf
    import pytest
    with _tf.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="cleanup_when"):
            AutomationRegistry(d).register("x", "No lifecycle", release="2026-09")


def test_generated_prompts_run_central_cleanup():
    from orchestrator import automations as A
    plan = A.plan(CONFIG, "2026-09", "2026-09-09")
    for spec in plan["automations"]:
        assert "automation cleanup --release 2026-09 --json" in spec["prompt"]
        assert "m_delete_automation" in spec["prompt"]
        assert "only after" in spec["prompt"]


def test_cleanup_command_returns_registered_ids_without_mutating_registry(capsys):
    import json
    import tempfile as _tf
    from orchestrator import cli
    from orchestrator.registry import AutomationRegistry
    from orchestrator.state import StepState
    with _tf.TemporaryDirectory() as d:
        rid = "2026-09"
        st = ReleaseState(release_id=rid)
        st.set_step("ccd", "final_reminder", StepState(status="done"))
        C.save_state(st, d, rid)
        reg = AutomationRegistry(d, rid)
        reg.register("morning", "Morning", release=rid,
                     steps=["ccd.final_reminder"], cleanup_when="steps_done")
        assert cli.main(["--runs-root", d, "automation", "cleanup",
                         "--release", rid, "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert [r["id"] for r in result["removals"]] == ["morning"]
        assert reg.list(release=rid)[0]["id"] == "morning"  # skill deletes, then deregisters




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
                     steps=["ccd.final_reminder", "ccd.pr_reminder"],
                     schedule="cron: 0 9 26 8 *", cleanup_when="steps_done")
        reg.register("a-noon", "CCD noon", release=rid, slug="ccd-noon",
                     steps=["ccd.localization"], schedule="cron: 0 12 26 8 *",
                     cleanup_when="steps_done")
        reg.register("a-poll", "poller", release=rid, slug="ccd-localization-poller",
                     steps=["ccd.localization"], schedule="every 1 hour",
                     cleanup_when="steps_done")

        def sync():
            ns = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, json=True)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                A._cmd_sync(ns)
            return _json.loads(buf.getvalue())

        # in sync → nothing changed
        u0 = {u["slug"]: u for u in sync()["updates"]}
        assert all(not u["changed"] for u in u0.values())
        assert u0["ccd-morning"]["cleanup_when"] == "steps_done"
        # noon matched to the CRON, not the poller's hourly interval (slug disambiguates)
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


def test_preflight_escalation_checkpoint_timing_and_dedup():
    from datetime import date, datetime
    from orchestrator import notifications as N, render
    report = {
        "ccd": "2026-09-09",
        "active_phase": {
            "id": "preflight",
            "outstanding": [{"id": "cg"}],
            "steps": [
                {"id": "cg", "name": "Report critical CG alerts",
                 "status": "blocked", "note": "High alert remains"},
                {"id": "vitals", "name": "Review Play vitals",
                 "status": "confirm", "note": None},
            ],
        },
    }
    assert N.previous_business_day(date(2026, 9, 8)) == date(2026, 9, 4)  # Labor Day weekend
    assert N.preflight_escalation(report, datetime(2026, 9, 8, 8, 59), {}) is None
    pre = N.preflight_escalation(report, datetime(2026, 9, 8, 9, 0), {})
    assert pre["checkpoint"] == "pre_ccd" and pre["blocked"][0]["id"] == "cg"
    assert pre["confirmations"][0]["id"] == "vitals"
    assert N.preflight_escalation(report, datetime(2026, 9, 8, 12),
                                  {pre["key"]: {"emitted_at": "x"}}) is None
    # CCD checkpoint is independent; it also catches a machine that missed CCD-1.
    ccd = N.preflight_escalation(report, datetime(2026, 9, 9, 9), {})
    assert ccd["checkpoint"] == "ccd"
    assert N.preflight_escalation(report, datetime(2026, 9, 9, 9),
                                  {pre["key"]: {}, ccd["key"]: {}}) is None
    done = {**report, "active_phase": {"id": "ccd", "outstanding": [], "steps": []}}
    assert N.preflight_escalation(done, datetime(2026, 9, 9, 9), {}) is None
    holiday_report = {**report, "ccd": "2026-09-08",
                      "owner_name": "Owner", "owner_email": "owner@microsoft.com",
                      "target_month_label": "October 2026"}
    holiday_model = N.preflight_escalation(holiday_report, datetime(2026, 9, 4, 9), {})
    holiday_text = render.preflight_core_alert(holiday_report, holiday_model)
    assert "in 4 calendar days" in holiday_text and "tomorrow" not in holiday_text


def test_tick_core_alert_is_independent_of_owner_digest_and_checkpointed(monkeypatch):
    import argparse
    import tempfile as _tf
    import yaml
    from orchestrator.commands import notify as ncmd
    from orchestrator.engine import Orchestrator
    from orchestrator.state import StepState
    with _tf.TemporaryDirectory() as d:
        rid = "2026-09"
        st = ReleaseState(release_id=rid, ccd="2026-09-09", target_month="2026-10",
                          readiness_signed=True, owner_email="owner@microsoft.com",
                          owner_name="Release Owner")
        cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
        preflight = next(p for p in cfg["phases"] if p["id"] == "preflight")
        for step in preflight["steps"]:
            st.set_step("preflight", step["id"], StepState(status="done"))
        st.steps.pop("preflight.notice")
        # A pending Scout step suppresses the normal owner digest; blocked CG still needs
        # the independent Core Team deadline warning.
        st.set_step("preflight", "cg", StepState(
            status="blocked", note="High Bouncy Castle alert still unresolved",
            links=[{"name": "CG alert", "url": "https://example.com/cg"}]))
        C.save_state(st, d, rid)
        monkeypatch.setattr(Orchestrator, "run_until_gate", lambda self: [])

        class A:
            runs_root = d; release = rid; config = CONFIG
            as_of = "2026-09-08"; force = False; json = True

        # Read-only notify cannot consume or emit automation checkpoints.
        observation = ncmd._notify_payload(A, rid, advance=False)
        assert observation["core_alert"] is None
        assert C.load_state(d, rid).escalation_checkpoints == {}

        first = ncmd._notify_payload(A, rid, advance=True)
        assert first["message"] == "" and first["teams"] is None
        alert = first["core_alert"]
        assert alert["chatName"] == "Android Core Team"
        assert alert["mentions"][0]["mentioned"]["user"]["id"] == "owner@microsoft.com"
        assert "October 2026 release at risk" in alert["content"]
        assert "Failed / blocked checks" in alert["content"]
        assert 'href="https://example.com/cg"' in alert["content"]
        assert "manager approval" in alert["content"]
        assert "does not pause the ADO release schedule" in alert["content"]
        assert alert["checkpoint"] not in C.load_state(d, rid).escalation_checkpoints
        # Until successful delivery is acknowledged, retry remains available.
        second = ncmd._notify_payload(A, rid, advance=True)
        assert second["core_alert"]["checkpoint"] == alert["checkpoint"]
        checkpoint = alert["checkpoint"]
        ns = argparse.Namespace(runs_root=d, release=rid, config=CONFIG,
                                checkpoint=checkpoint)
        assert ncmd.cmd_record_core_alert(ns) == 0
        assert checkpoint in C.load_state(d, rid).escalation_checkpoints
        A.force = True
        assert ncmd._notify_payload(A, rid, advance=True)["core_alert"] is None

        # Still blocked on CCD morning => one separate checkpoint.
        A.as_of = "2026-09-09"; A.force = False
        ccd = ncmd._notify_payload(A, rid, advance=True)["core_alert"]
        assert ccd and ccd["checkpoint"].endswith(":ccd")
        assert "code complete today" in ccd["content"]


def test_non_json_tick_reports_digest_and_core_alert_independently(monkeypatch, capsys):
    from orchestrator.commands import notify as ncmd
    payload = {"message": "owner digest", "core_alert": {"checkpoint": "preflight:x:pre_ccd"}}
    monkeypatch.setattr(ncmd.C, "resolve_release_id", lambda *_: "x")
    monkeypatch.setattr(ncmd, "_notify_payload", lambda *_args, **_kwargs: payload)
    class A:
        runs_root = "unused"; release = "x"; config = CONFIG; json = False
    assert ncmd.cmd_tick(A) == 0
    out = capsys.readouterr().out
    assert "owner digest" in out and "Core Team deadline alert due" in out




def test_ui_projection_per_config():
    """Use recorded exact-title verdicts; preserve provider/config separation and diagnose skips."""
    from tools import pipelines as P
    from tests._mrwp_evidence import current_rc, PROD, RC
    rc = current_rc(
        ecs={PROD: [("test_100_X", "Failed"), ("test_100_X", "Passed")],
             RC: [("test_100_X", "Failed")]},
        local={PROD: [("test_100_X", "NotExecuted")],
               "Lab Api Tests": [("test_999_Unplaceable", "Passed")]})
    ok, projection, d = P.project_mrwp_ui_results(rc)
    assert ok, d
    v = projection["verdicts"]
    assert v == {100: {("ECS", "prod"): "Passed", ("ECS", "rc"): "Failed",
                       ("Local", "prod"): "NotApplicable"}}
    assert 999 not in v
    assert projection["provenance"]["providers"][1]["skipped_mapping"][0]["reason"] == "unknown_suite_variant"




def test_fill_ui_automation_results_maps_configs():
    """Each plan point (case, config) takes the outcome of its matching (flight, variant); a
    config with no verdict is untouched. Mirrors the user's test_3321136 example."""
    from tools import pipelines as P
    from tools import testplans as T
    verdicts = {3321136: {("ECS", "prod"): "Passed", ("ECS", "rc"): "Failed",
                          ("Local", "prod"): "NotApplicable"}}   # Local/rc omitted -> untouched
    points = [
        {"id": 1, "testCase": {"id": "3321136"}, "configuration": {"id": "292"}},  # ECS prod -> Passed
        {"id": 2, "testCase": {"id": "3321136"}, "configuration": {"id": "294"}},  # ECS rc  -> Failed
        {"id": 3, "testCase": {"id": "3321136"}, "configuration": {"id": "328"}},  # Local prod -> N/A
        {"id": 4, "testCase": {"id": "3321136"}, "configuration": {"id": "344"}},  # no verdict
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
    assert summ["set_passed"] == 1 and summ["set_failed"] == 1 and summ["set_not_applicable"] == 1
    assert summ["untouched_points"] == [{"point_id": 4, "case_id": 3321136, "config_id": 344,
                                         "reason": "no_source_verdict"}]
    assert summ["cases_touched"] == 1
    by_outcome = {}
    for u, b in sent:
        ids = set(u.split("/points/")[1].split("?")[0].split(","))
        by_outcome.setdefault(b["outcome"], set()).update(ids)
    assert by_outcome["Passed"] == {"1"}
    assert by_outcome["Failed"] == {"2"}
    assert by_outcome["NotApplicable"] == {"3"}




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
