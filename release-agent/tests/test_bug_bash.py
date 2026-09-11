"""Release-agent tests — bug_bash. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403
from tests._mrwp_evidence import current_rc, PROD
from tests._ui_results import broker_fill, auth_fill, publish


def _ui_state(plan_id="900"):
    st = _uts_state(plan_id=plan_id)
    st.pipeline_runs = {"rcs": [current_rc()]}
    return st



def test_find_auth_ui_test_build_matches_resource_link(monkeypatch):
    """The build->test join is the test run's authenticatorBuild pipeline-resource id."""
    from tools import pipelines as P
    monkeypatch.setattr(P, "_az_json",
                        lambda a, t: (True, [{"id": 700}, {"id": 701}], ""))
    # 701 tested a different build; 700 tested our build 500
    monkeypatch.setattr(P, "_auth_test_source_build_id",
                        lambda bid, timeout=60: {700: 500, 701: 499}.get(bid))
    ok, tid, _ = P.find_auth_ui_test_build(500)
    assert ok and tid == 700




def test_clone_plans_broker_builds_then_idempotent():
    """First run builds the plan (Broker flat suite + Native Auth folder) and stashes the new
    plan id; a re-run with that id stored reports done WITHOUT rebuilding."""
    st, out = _bb_build("clone_plans_broker", {"clone_id": "5551212"})
    assert out["kind"] == "done"
    assert "Broker test plan" in out["note"] and "5551212" in out["note"]
    assert "Native Auth" in out["note"] and "UI Automation" in out["note"]
    assert st.get_step("bug_bash", "clone_plans_broker").data["plan_id"] == "5551212"
    assert out["links"][0]["url"].endswith("planId=5551212")
    # idempotent: an injected existing plan id → already-built, no rebuild
    _, out2 = _bb_build("clone_plans_broker", {"plan_id": "5551212"})
    assert out2["kind"] == "done" and "already built" in out2["note"]




def test_clone_plans_broker_blocks_on_api_failure():
    _, out = _bb_build("clone_plans_broker", {"fail": "HTTP 403: forbidden"})
    assert out["kind"] == "blocked" and "403" in out["reason"]




def test_ui_test_status_fills_from_snapshot(monkeypatch):
    """With a cloned plan + reconciled snapshot, fill and persist the summary."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from tools import testplans as T
    st = _ui_state(plan_id="3737697")
    from orchestrator.state import StepState
    st.set_step("bug_bash", "clone_plans_auth", StepState(data={"suite_id": 902}))
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    captured = {}

    def fake_fill(plan_id, verdicts, timeout=120, *, suite_id):
        captured["plan_id"] = plan_id
        return broker_fill(plan_id, verdicts, suite_id=suite_id)

    o = T.fill_ui_automation_results
    T.fill_ui_automation_results = fake_fill
    try:
        with mockctx.active({}):
            out = as_dict(_steps.get_step("bug_bash", "ui_test_status").build(st))
    finally:
        T.fill_ui_automation_results = o
    assert out["kind"] == "done"
    assert "2 Passed" in out["note"] and "0 Failed" in out["note"] and "0 N/A" in out["note"]
    assert captured["plan_id"] == "3737697"
    data = st.get_step("bug_bash", "ui_test_status").data
    assert data["plan_id"] == "3737697" and data["summary"]["cases_touched"] == 1
    assert out["links"][0]["url"].endswith("planId=3737697")




def test_ui_test_status_fills_auth_and_assigns_failures_to_owner():
    """The expanded step also fills the Authenticator suite and reassigns every FAILED
    automated auth case to the release owner for triage."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import StepState
    from tools import testplans as T
    from tools import distribution as D
    st = _ui_state(plan_id="3737697")
    st.owner_email = "owner@microsoft.com"
    st.set_step("bug_bash", "clone_plans_auth", StepState(status="done", data={"suite_id": 714999}))
    from tests._auth_evidence import auth_snapshot
    from tools import pipelines as P
    rc = st.pipeline_runs["rcs"][-1]
    rc["auth"] = auth_snapshot(rc=rc["rc"], rows={P.AUTH_UI_SUITES[0]: [
        (f"test_{cid}_failed", "Failed") for cid in (2916347, 2916524, 3094649, 3261599, 3741283)]})

    assigned = []
    o_bfill, o_afill, o_assign = (T.fill_ui_automation_results, T.fill_auth_ui_results,
                                  D.set_assigned_to)
    T.fill_ui_automation_results = broker_fill
    T.fill_auth_ui_results = auth_fill
    D.set_assigned_to = lambda cid, upn, timeout=60: (assigned.append((cid, upn)), (True, ""))[1]
    try:
        with mockctx.active({}):
            out = as_dict(_steps.get_step("bug_bash", "ui_test_status").build(st))
    finally:
        T.fill_ui_automation_results, T.fill_auth_ui_results, D.set_assigned_to = (
            o_bfill, o_afill, o_assign)
    assert out["kind"] == "done"
    assert "Auth: 0 Passed, 5 Failed" in out["note"]
    assert "5 failed case(s) assigned to owner owner@microsoft.com" in out["note"]
    # every failed auth case was reassigned to the owner
    assert {c for c, _ in assigned} == {2916347, 2916524, 3094649, 3261599, 3741283}
    assert all(u == "owner@microsoft.com" for _, u in assigned)
    adata = st.get_step("bug_bash", "ui_test_status").data["auth"]
    assert adata["failed"] == 5 and adata["failed_assigned_to_owner"] == 5
    assert any("suite 714999" in l["name"] for l in out["links"])




def test_ui_test_status_surfaces_combined_failures_to_ui_failures():
    """ui_test_status forward-populates the `ui_failures` human-review reminder with BOTH the
    Broker MRWP UI failures AND the Authenticator ECS failed cases (+ run links)."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import StepState
    from tools import testplans as T
    from tools import distribution as D
    st = _ui_state(plan_id="3737697")
    st.owner_email = "owner@microsoft.com"
    st.set_step("bug_bash", "clone_plans_auth", StepState(status="done", data={"suite_id": 714999}))
    # seed a broker RC snapshot with a failing UI suite (individual tests) + an auth run
    rc = current_rc(ecs={PROD: [("test_831126_MDM_FirstPartyAppSignIn", "Failed"),
                               ("test_3321136_UpgradeFromRegularWpjToStrongKeyWpj", "Failed")]})
    from tests._auth_evidence import auth_snapshot
    rc["auth"] = auth_snapshot(rc=rc["rc"], apk=178685087, build=178777988, rows={
        "Firebase Test Lab - UIAutomator E2E": [
            ("test_2916347_passkeyInAppRegistration_fullWizard", "Failed"),
            ("test_2916524_passkeyDeregister_deleteFromAppAndVerifyMySecurityInfo", "Failed"),
            ("test_3094649_passkeyFromL2_createPasskeyFromAccountFullscreen", "Failed"),
            ("test_3261599_psiPushNotification_registerAfterEnablingNotifications", "Failed"),
            ("test_3741283_mfaDialogSurvivesProcessDeathRestore", "Failed")]})
    st.pipeline_runs = {"rcs": [rc]}
    o_b, o_a, o_as = T.fill_ui_automation_results, T.fill_auth_ui_results, D.set_assigned_to
    T.fill_ui_automation_results = broker_fill
    T.fill_auth_ui_results = auth_fill
    assigned_calls = []
    D.set_assigned_to = lambda cid, upn, timeout=60: (assigned_calls.append((str(cid), upn)), (True, ""))[1]
    try:
        with mockctx.active({}):
            as_dict(_steps.get_step("bug_bash", "ui_test_status").build(st))
    finally:
        T.fill_ui_automation_results, T.fill_auth_ui_results, D.set_assigned_to = o_b, o_a, o_as
    uf = st.get_step("bug_bash", "ui_failures")
    # step-8-style rich note: EVERY failing test listed individually, all 🔬 investigate for owner,
    # Broker grouped by provider (ECS/Local) then by bucket (suite name).
    assert uf.note.startswith("\U0001f9ea") and "UI failures to investigate" in uf.note
    assert "for owner@microsoft.com to investigate" in uf.note
    assert "**Broker (MRWP)** \u2014 2 failing test(s):" in uf.note
    assert "**ECS** \u2014 2 failing:" in uf.note                     # provider separation
    assert "_PROD MSAL - RC Broker (API 32)_ (2):" in uf.note         # bucket separation
    # each broker test is its own 🔬 line, linked by the case id parsed from the title
    assert "[831126](https://identitydivision.visualstudio.com/Engineering/_workitems/edit/831126)" in uf.note
    assert "test_3321136_UpgradeFromRegularWpjToStrongKeyWpj" in uf.note
    assert "**Authenticator (ECS)** \u2014 5 failing distinct test(s):" in uf.note
    assert "[2916347](https://identitydivision.visualstudio.com/Engineering/_workitems/edit/2916347)" in uf.note
    # auth lines show the automation test NAME, not a generic "Automated failure"
    assert "test_2916347_passkeyInAppRegistration_fullWizard" in uf.note
    assert "Automated failure" not in uf.note
    # user-facing action line — Scout speaking to the owner in first person, no raw engine command
    assert "let me know" in uf.note and "`done --step" not in uf.note
    assert uf.data["broker_failed_tests"] == 2 and len(uf.data["auth_failed_cases"]) == 5
    names = [l["name"] for l in uf.links]
    assert "MRWP ECS run" in names and "Authenticator ECS UI tests" in names
    # the reminder is NOT marked done — it stays a pending human review
    assert not st.is_done("bug_bash", "ui_failures")
    # BOTH apps' failures are physically reassigned to the release owner in ADO:
    #  - Broker: the 2 failing UI cases parsed from the ECS suite titles (831126, 3321136)
    #  - Auth: the 5 failed automated cases
    assigned_ids = {cid for cid, _ in assigned_calls}
    assert {"831126", "3321136"}.issubset(assigned_ids)      # broker cases reassigned
    assert {"2916347", "2916524", "3094649", "3261599", "3741283"}.issubset(assigned_ids)
    assert all(upn == "owner@microsoft.com" for _, upn in assigned_calls)
    bstep = st.get_step("bug_bash", "ui_test_status").data["broker"]
    assert bstep["failed_case_ids"] == [831126, 3321136] and bstep["failed_assigned_to_owner"] == 2




def test_ui_test_status_blocks_before_writes_when_no_auth_suite():
    """Missing target suite is not confused with Monthly's intentional lack of mapping."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from tools import testplans as T
    st = _ui_state(plan_id="900")
    o = T.fill_ui_automation_results
    T.fill_ui_automation_results = lambda p, v, timeout=120: (
        True, {"points_total": 5, "set_passed": 5, "set_failed": 0,
               "set_not_applicable": 0, "cases_touched": 3}, "")
    try:
        with mockctx.active({}):
            out = as_dict(_steps.get_step("bug_bash", "ui_test_status").build(st))
    finally:
        T.fill_ui_automation_results = o
    assert out["kind"] == "blocked" and "Authenticator release suite" in out["reason"]




def test_ui_test_status_blocks_without_broker_plan():
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = _uts_state(plan_id=None)
    with mockctx.active({}):
        out = as_dict(_steps.get_step("bug_bash", "ui_test_status").build(st))
    assert out["kind"] == "blocked" and "cloned" in out["reason"]




def test_ui_test_status_uses_current_recorded_evidence(monkeypatch):
    """Historical failure details cannot cause reassignment after current recovery."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from tools import pipelines as P, testplans as T
    st = _ui_state(plan_id="900")
    from orchestrator.state import StepState
    st.set_step("bug_bash", "clone_plans_auth", StepState(data={"suite_id": 902}))
    st.pipeline_runs = {"rcs": [
        current_rc(ecs={PROD: [("test_100_Default", "Failed")]}, rc=1),
        current_rc(ecs={PROD: [("test_100_Default", "Failed"), ("test_100_Default", "Passed")]}),
    ]}
    seen = {}

    def forbidden(*a, **k):
        raise AssertionError("No live MRWP reads or recovered-case assignments")

    def fake_fill(plan_id, verdicts, timeout=120, *, suite_id):
        seen["verdicts"] = verdicts
        return broker_fill(plan_id, verdicts, suite_id=suite_id)

    from tools import distribution as D
    st.owner_email = "owner@example.com"
    monkeypatch.setattr(P, "_ado_rest_get", forbidden)
    monkeypatch.setattr(P, "_run_results", forbidden)
    monkeypatch.setattr(D, "set_assigned_to", forbidden)
    monkeypatch.setattr(T, "fill_ui_automation_results", fake_fill)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    with mockctx.active({}):
        out = as_dict(_steps.get_step("bug_bash", "ui_test_status").build(st))
    assert out["kind"] == "done"
    assert seen["verdicts"] == {100: {("ECS", "prod_msal_rc_broker"): "Passed",
                                    ("Local", "prod_msal_rc_broker"): "Passed"}}
    assert not st.get_step("bug_bash", "ui_failures").note




def test_clone_plans_auth_creates_query_suite_then_idempotent():
    """First run creates the query-based suite (name + stash); a re-run with the suite id
    stored reports done without re-creating."""
    st, out = _bb_build("clone_plans_auth", {"existing": None, "create_id": "778899"})
    assert out["kind"] == "done"
    assert "Created the Authenticator bug-bash query-suite 'Android release/08/13/2026'" in out["note"]
    assert "assign testers" in out["note"].lower()          # stops before assigning testers
    assert st.get_step("bug_bash", "clone_plans_auth").data["suite_id"] == "778899"
    # idempotent via injected existing suite id
    _, out2 = _bb_build("clone_plans_auth", {"suite_id": "778899"})
    assert out2["kind"] == "done" and "already exists" in out2["note"]




def test_clone_plans_auth_reuses_existing_same_named_suite():
    """If a same-named suite already exists under the root, reuse it (no duplicate create)."""
    st, out = _bb_build("clone_plans_auth", {"existing": "424242"})
    assert out["kind"] == "done" and "already exists" in out["note"]
    assert st.get_step("bug_bash", "clone_plans_auth").data["suite_id"] == "424242"




def test_clone_plans_auth_blocks_without_ccd():
    """The suite name needs the CCD day ('Android release/MM/DD/YYYY') — no CCD → block."""
    st, out = _bb_build("clone_plans_auth", {"existing": None, "create_id": "1"}, ccd=None)
    assert out["kind"] == "blocked" and "Code Complete Date" in out["reason"]




def test_bug_bash_clone_steps_are_real_agents():
    """Both clone steps resolve to real agent modules (KIND=agent) — no longer stubs."""
    import steps as _steps
    for sid in ("clone_plans_broker", "clone_plans_auth"):
        mod = _steps.get_step("bug_bash", sid)
        assert mod is not None and getattr(mod, "KIND", None) == "agent" and hasattr(mod, "run")




def test_distribute_tests_step_previews_and_stores_plan():
    """The step computes the combined distribution offline and STASHES the plan on the step
    (read-only preview) — it does NOT write assignments."""
    mocks = {
        "roster": [{"name": "A", "upn": "a@microsoft.com"}, {"name": "B", "upn": "b@microsoft.com"},
                   {"name": "Owner", "upn": "owner@microsoft.com"}, {"name": "Oce", "upn": "oce@microsoft.com"}],
        "oce": "oce@microsoft.com",
        "broker_cases": [{"id": "1", "assignee": "a@microsoft.com"}, {"id": "2", "assignee": None},
                         {"id": "3", "assignee": "owner@microsoft.com"}, {"id": "4", "assignee": None}],
        "auth_cases": [{"id": "5", "assignee": "b@microsoft.com"}, {"id": "6", "assignee": None}],
    }
    st, out = _dist_build(mocks, oof=[])
    assert out["kind"] == "done" and "PREVIEW" in out["note"]
    plan = st.get_step("bug_bash", "distribute_tests").data["plan"]
    assert plan["applied"] is False
    assert plan["owner_excluded"] == "owner@microsoft.com" and plan["oce_excluded"] == "oce@microsoft.com"
    # 6 tests / 2 eligible (a,b; owner+oce excluded) -> 3 each
    assert sorted(plan["counts"].values()) == [3, 3]
    assert set(plan["assignments"].values()) == {"a@microsoft.com", "b@microsoft.com"}




def test_distribute_excludes_automated_auth_cases():
    """distribute_tests drops auth cases already automated by this release's auth ECS run
    (empirical, via the `auth_automated` set) so they don't go to manual testers."""
    mocks = {
        "oce": "oce@microsoft.com",
        "roster": [{"name": "A", "upn": "a@microsoft.com"}, {"name": "B", "upn": "b@microsoft.com"}],
        "broker_cases": [],
        "auth_cases": [{"id": "5", "assignee": None}, {"id": "6", "assignee": None},
                       {"id": "7", "assignee": None}, {"id": "8", "assignee": None}],
        "auth_automated": [6, 8],                       # 6 + 8 already automated -> excluded
    }
    st, out = _dist_build(mocks, oof=[])
    assert out["kind"] == "done"
    plan = st.get_step("bug_bash", "distribute_tests").data["plan"]
    assert plan["auth_total"] == 2 and plan["auth_excluded_automated"] == 2   # 4 -> 2
    assert "Excluded 2 already-automated auth case(s)" in out["note"]
    assigned_ids = {k.split(":")[1] for k in plan["assignments"]}
    assert assigned_ids == {"5", "7"}                   # only the non-automated cases distributed




def test_distribute_tests_blocks_without_broker_clone():
    """No cloned Broker plan → the step blocks (can't locate the manual tests)."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08", owner_email="o@x")
    with mockctx.active({"auth_cases": [], "roster": [{"name": "A", "upn": "a@x"}], "oce": "oce@x"}):
        out = as_dict(_steps.get_step("bug_bash", "distribute_tests").build(st, oof=[]))
    assert out["kind"] == "blocked" and "ui_test_status" in out["reason"]




# ---- Phase 3: send_invite ----

def test_bugbash_schedule_rule():
    """schedule_bugbash: after-3pm/weekend -> next business 9am; before-3pm weekday -> same
    day later; weekends roll to Monday."""
    from datetime import datetime
    from tools import invite as I
    # Fri 6pm -> Mon 9am (after 3pm + weekend skip)
    s, e, _ = I.schedule_bugbash(datetime(2026, 8, 21, 18, 0))
    assert (s.year, s.month, s.day, s.hour) == (2026, 8, 24, 9) and e.hour == 11
    # Fri 10am -> same day noon
    s2, _, _ = I.schedule_bugbash(datetime(2026, 8, 21, 10, 0))
    assert s2.day == 21 and s2.hour == 12
    # Sat -> Mon 9am
    s3, _, _ = I.schedule_bugbash(datetime(2026, 8, 22, 11, 0))
    assert s3.day == 24 and s3.hour == 9
    # Tue 3:30pm -> Wed 9am
    s4, _, _ = I.schedule_bugbash(datetime(2026, 8, 25, 15, 30))
    assert (s4.day, s4.hour) == (26, 9)




def test_send_invite_composes_create_event():
    """send_invite returns a NeedsSkill(workiq_create_event) with the meeting, real
    recipients, and a body carrying every release link + the injected flags."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = _invite_state()
    with mockctx.active({"now": "2026-08-21T18:00:00",
                         "flags": "{EnableBrowserSso:true,UseKdfVersion2:true}"}):
        out = as_dict(_steps.get_step("bug_bash", "send_invite").build(st))
    assert out["kind"] == "needs_skill" and out["tool"] == "workiq_create_event"
    p = out["payload"]
    assert p["subject"] == "September 2026 Release Bug Bash"
    assert p["attendees"] == ["androididentity@microsoft.com", "idnadevexciamdublin@microsoft.com"]
    assert p["start"] == "2026-08-24T09:00:00" and p["end"] == "2026-08-24T11:00:00"
    assert p["isOnlineMeeting"] is True and p["bodyContentType"] == "html"
    b = p["body"]
    for frag in ("planId=3730001", "buildId=1678863", "buildId=1678864",
                 "planId=714514&suiteId=3730002", "EnableBrowserSso",
                 "variableGroupId=40", "September 2026"):
        assert frag in b, frag
    assert "release-engineer-schedule" not in b     # Native Auth row removed
    assert out["outbound"] is True
    assert out["notification"]["expires_at"] == "2026-08-24T09:00:00-07:00"


def test_send_invite_uses_owner_timezone_and_blocks_missing_zone(monkeypatch):
    from datetime import datetime
    from orchestrator import schedule
    from steps.bug_bash import send_invite
    from steps.lib import mockctx
    st = _invite_state()
    st.timezone = "Asia/Tokyo"
    monkeypatch.setattr(schedule, "now_local", lambda zone:
                        datetime.fromisoformat("2026-08-24T22:00:00+00:00").astimezone(zone))
    with mockctx.active({"flags": "{}"}):
        out = send_invite.build(st)
    assert out.payload["start"] == "2026-08-25T09:00:00"
    assert out.payload["timeZone"] == "Asia/Tokyo"
    assert out.notification["expires_at"] == "2026-08-25T09:00:00+09:00"
    st.timezone = "Missing/Zone"
    assert send_invite.build(st).kind == "blocked"


def test_send_invite_expiry_refresh_and_ever_claimed_safety(tmp_path, monkeypatch, capsys):
    import json
    from datetime import datetime
    from orchestrator import cli, delivery as D, mocks, schedule
    from orchestrator.state import StepState
    current = datetime.fromisoformat("2026-08-24T10:00:00-07:00")
    monkeypatch.setattr(schedule, "now_local", lambda zone: current.astimezone(zone))
    monkeypatch.setattr(mocks, "load_mocks", lambda: {"bug_bash.send_invite": {"flags": "{}"}})
    st = _active_phase(_invite_state(), "bug_bash")
    for sid in ("ui_test_status", "distribute_tests"):
        st.set_step("bug_bash", sid, StepState(status="done"))
    base = ["--config", CONFIG, "--runs-root", str(tmp_path), "notification"]
    scope = ["--release", st.release_id]
    prepare = base + ["prepare"] + scope + ["--source", "step", "--phase", "bug_bash",
                                          "--step", "send_invite"]
    C.save_state(st, str(tmp_path), st.release_id)
    assert cli.main(prepare) == 0
    first = json.loads(capsys.readouterr().out)["notifications"][0]
    claim_args = base + ["claim"] + scope + ["--id", first["id"], "--executor", "test-worker", "--hash"]
    current = datetime.fromisoformat("2026-08-24T12:00:00-07:00")
    assert cli.main(claim_args + [first["hash"]]) == 1
    assert "deadline expired" in json.loads(capsys.readouterr().out)["error"]
    current = datetime.fromisoformat("2026-08-25T10:00:00-07:00")
    assert cli.main(prepare) == 0
    fresh = json.loads(capsys.readouterr().out)["notifications"][0]
    assert fresh["id"] == first["id"] and fresh["hash"] != first["hash"]
    assert fresh["payload"]["start"] == "2026-08-25T12:00:00"
    refreshed = C.load_state(str(tmp_path), st.release_id)
    assert refreshed.notification_deliveries[first["id"]]["superseded"]
    assert cli.main(claim_args + [first["hash"]]) == 1
    capsys.readouterr()
    assert cli.main(claim_args + [fresh["hash"]]) == 0
    claim = json.loads(capsys.readouterr().out)
    assert claim["permission_to_send"]
    assert cli.main(base + ["result"] + scope + ["--id", first["id"], "--execution-id",
        claim["execution_id"], "--outcome", "uncertain", "--evidence", "Simulated timeout"]) == 0
    capsys.readouterr()
    current = datetime.fromisoformat("2026-08-26T10:00:00-07:00")
    assert cli.main(prepare) == 0
    capsys.readouterr()
    saved = C.load_state(str(tmp_path), st.release_id).notification_deliveries[first["id"]]
    assert saved["descriptor"]["hash"] == fresh["hash"] and len(saved["attempts"]) == 1
    assert cli.main(claim_args + [fresh["hash"]]) == 1
    capsys.readouterr()
    # Even explicit positive not-sent recovery cannot silently issue a different meeting.
    assert cli.main(base + ["result"] + scope + ["--id", first["id"], "--execution-id",
        claim["execution_id"], "--outcome", "not_sent", "--evidence", "Owner verified no event",
        "--owner-review"]) == 0
    capsys.readouterr()
    assert cli.main(prepare) == 0
    capsys.readouterr()
    saved = C.load_state(str(tmp_path), st.release_id).notification_deliveries[first["id"]]
    assert saved["status"] == "not_sent" and saved["descriptor"]["hash"] == fresh["hash"]
    assert cli.main(claim_args + [fresh["hash"]]) == 1
    assert "deadline expired" in json.loads(capsys.readouterr().out)["error"]




def test_send_invite_blocks_without_plans():
    """No cloned plans → the invite step blocks."""
    import steps as _steps
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08", ccd="2026-08-13", owner_email="o@x")
    out = as_dict(_steps.get_step("bug_bash", "send_invite").build(st))
    assert out["kind"] == "blocked" and "cloned" in out["reason"]




def test_send_invite_is_scout_step():
    import steps as _steps
    mod = _steps.get_step("bug_bash", "send_invite")
    assert mod is not None and getattr(mod, "KIND", None) == "scout"




# ---- Phase 3: activate_chat + record-bugbash-chat ----

def test_activate_chat_composes_needs_skill():
    """activate_chat is a scout step whose NeedsSkill describes the search → Playwright
    activate → human fallback resolution, keyed to the meeting topic, with the recorder
    follow-up command."""
    import steps as _steps
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08", ccd="2026-08-13")
    out = as_dict(_steps.get_step("bug_bash", "activate_chat").build(st))
    assert out["kind"] == "needs_skill" and out["tool"] == "record-bugbash-chat"
    g = out["payload"]["_gather"]
    assert g["meeting_topic"] == "September 2026 Release Bug Bash"
    assert "Playwright" in g["instructions"] and "m_ask_user" in g["instructions"]
    assert "record-bugbash-chat --release 2026-08" in out["payload"]["followup_command"]
    assert _steps.get_step("bug_bash", "activate_chat").KIND == "scout"




def test_activate_chat_blocks_without_ccd():
    import steps as _steps
    from orchestrator.outcomes import as_dict
    out = as_dict(_steps.get_step("bug_bash", "activate_chat").build(ReleaseState(release_id="2026-08")))
    assert out["kind"] == "blocked" and "CCD" in out["reason"]




def test_record_bugbash_chat_stores_id_and_marks_done():
    """record-bugbash-chat with --chat-id stores it on the step (readable by the poller)
    and marks the step done; without --chat-id it holds the step for the owner."""
    import tempfile, argparse
    from orchestrator import cli_common as _C
    from orchestrator.commands import bugbash_chat as BC
    from steps.bug_bash.activate_chat import stored_chat_id
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-13", ccd_source="confirmed")
        _C.save_state(st, d, rid)
        cid = "19:meeting_ABC123@thread.v2"
        ns = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, as_of=None, chat_id=cid)
        assert BC.cmd_record_bugbash_chat(ns) == 0
        again = _C.load_state(d, rid)
        assert again.is_done("bug_bash", "activate_chat")
        assert stored_chat_id(again) == cid

        # no chat id -> attention hold (human fallback), step not done
        st2 = ReleaseState(release_id=rid, ccd="2026-08-13", ccd_source="confirmed")
        _C.save_state(st2, d, rid)
        ns2 = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, as_of=None, chat_id=None)
        assert BC.cmd_record_bugbash_chat(ns2) == 2
        after = _C.load_state(d, rid)
        assert not after.is_done("bug_bash", "activate_chat")
        assert after.get_step("bug_bash", "activate_chat").status == "blocked"




def test_notify_native_auth_composes_needs_skill():
    """notify_native_auth is a scout step: NeedsSkill with the RE-resolution instructions,
    the composed message (linking the Native Auth suite + asking for a confirmation), and the
    recorder follow-up. Seeds the Aug 2026 RE hint from the schedule."""
    import steps as _steps
    from orchestrator.outcomes import as_dict
    st = _na_state()
    out = as_dict(_steps.get_step("bug_bash", "notify_native_auth").build(st))
    assert out["kind"] == "needs_skill" and out["tool"] == "record-nativeauth-notify"
    assert out["payload"]["engineer_hint"] == "silviu.petrescu"      # Aug 2026 from schedule
    assert "planId=3730001" in out["payload"]["content"]            # links the Broker plan
    assert "confirmation" in out["payload"]["content"].lower()
    assert "notification prepare --release 2026-08" in out["payload"]["_gather"]["instructions"]
    assert "release-engineer-schedule" in out["payload"]["_gather"]["schedule_doc"]
    assert _steps.get_step("bug_bash", "notify_native_auth").KIND == "scout"




def test_notify_native_auth_blocks_without_broker_plan():
    import steps as _steps
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08", ccd="2026-08-13")
    out = as_dict(_steps.get_step("bug_bash", "notify_native_auth").build(st))
    assert out["kind"] == "blocked" and "Broker test plan" in out["reason"]




def test_notify_native_auth_idempotent_after_recorded():
    import steps as _steps
    from orchestrator.outcomes import as_dict
    from orchestrator.state import StepState
    st = _na_state()
    st.set_step("bug_bash", "notify_native_auth",
                StepState(status="done", data={"engineer": "silviu.petrescu"}))
    out = as_dict(_steps.get_step("bug_bash", "notify_native_auth").build(st))
    assert out["kind"] == "done" and "already notified" in out["note"]




# ---- Phase 3: native_auth_signoff ----

def test_native_auth_signoff_is_owner_attestation():
    """native_auth_signoff is a pure human ATTEST step (owner: human): build() returns a
    NeedsHuman confirmation prompt with no outbound action and no record command — the owner
    tells Scout once sign-off is received and Scout clears it. The prompt names the engineer
    captured in notify_native_auth when present (falls back to a generic reference otherwise)."""
    import steps as _steps
    from orchestrator.outcomes import as_dict
    from orchestrator.state import StepState
    mod = _steps.get_step("bug_bash", "native_auth_signoff")
    assert mod.KIND == "attest"
    # no notify yet → generic reference, still a valid attest
    st = _na_state()
    out = as_dict(mod.build(st))
    assert out["kind"] == "needs_human" and out.get("attest") is True
    assert "sign-off" in out["prompt"] and "let me know" in out["prompt"]
    assert "`done --step" not in out["prompt"]              # user-facing — not a raw engine command
    assert not out.get("outbound") and "tool" not in out       # nothing sent, no skill command
    assert "notify_native_auth" in out["prompt"]
    # engineer captured upstream → named in the prompt (reused, not re-collected)
    st.set_step("bug_bash", "notify_native_auth",
                StepState(status="done", data={"engineer": "silviu.petrescu"}))
    out2 = as_dict(mod.build(st))
    assert "silviu.petrescu" in out2["prompt"]




def test_native_auth_signoff_config_is_attest():
    """phases.yaml classifies it as a human attest step (not scout)."""
    import yaml as _yaml
    cfg = _yaml.safe_load(open(CONFIG, encoding="utf-8"))
    bb = next(p for p in cfg["phases"] if p["id"] == "bug_bash")
    s = next(x for x in bb["steps"] if x["id"] == "native_auth_signoff")
    assert s.get("owner") == "human" and s.get("attest") is True and s.get("source") != "scout"




# ---- Phase 3: bugbash_updates (periodic poster) ----

def test_bugbash_holidays_and_working_window():
    """US federal holidays are hardcoded (with observed shifts); is_working_time is a
    weekday, not-a-holiday, 09:00–18:00 gate."""
    from datetime import datetime
    from tools import bugbash as BB
    h = BB.us_holidays(2026)
    assert BB.date(2026, 7, 3) in h          # July 4 (Sat) observed Fri Jul 3
    assert BB.date(2026, 11, 26) in h        # Thanksgiving (4th Thu)
    assert BB.date(2026, 12, 25) in h        # Christmas
    assert BB.is_working_time(datetime(2026, 8, 21, 10))     # Fri 10am
    assert not BB.is_working_time(datetime(2026, 8, 21, 8))  # before 9
    assert not BB.is_working_time(datetime(2026, 8, 21, 18)) # 18:00 = closed
    assert not BB.is_working_time(datetime(2026, 8, 22, 10)) # Saturday
    assert not BB.is_working_time(datetime(2026, 7, 3, 10))  # observed holiday




def test_bugbash_render_mentions_finished_and_complete():
    """render_update @mentions (with <at id>) only owners with remaining tests; lists
    not-run + failed + blocked (failed first) each with a link; finished owners appear by
    name with an 'all completed' line; all_complete flips when 0 remain."""
    from tools import bugbash as BB
    prog = {"total": 4, "done": 2, "remaining": 2, "unassigned": 0, "owners": {
        "a@x": {"name": "Alice", "total": 2, "done": 0, "remaining": 2, "tests": [
            {"id": "101", "name": "Login", "url": "u101", "state": "notrun"},
            {"id": "102", "name": "MFA", "url": "u102", "state": "failed"}]},
        "b@x": {"name": "Bob", "total": 2, "done": 2, "remaining": 0, "tests": [
            {"id": "201", "name": "X", "url": "u", "state": "passed"}]}}}
    html, mentions = BB.render_update(prog, "August 2026",
                                      [{"name": "Broker plan", "url": "bp"}])
    assert mentions == [{"id": 0, "upn": "a@x", "name": "Alice"}]   # only Alice (remaining)
    assert '<at id="0">Alice</at>' in html                          # id matches mentions[0]
    # the FAILED test is surfaced (Option A) with a link, and ordered before the not-run one
    assert '<a href="u102">102</a>' in html and "(Failed)" in html
    assert html.index("102") < html.index("101")                   # failed listed first
    assert "all 2 tests completed" in html and "<at" not in html.split("Bob")[0][-40:]
    assert not BB.all_complete(prog)
    prog["done"], prog["remaining"] = 4, 0
    prog["owners"]["a@x"].update(done=2, remaining=0)
    assert BB.all_complete(prog)




def test_bugbash_updates_blocks_without_chat():
    """bugbash_updates blocks until the meeting chat is activated (no chat_id)."""
    import steps as _steps
    from orchestrator.outcomes import as_dict
    st = _bb_updates_state(chat_id=None)
    out = as_dict(_steps.get_step("bug_bash", "bugbash_updates").build(st))
    assert out["kind"] == "blocked" and "chat" in out["reason"].lower()




def test_bugbash_updates_composes_needs_skill():
    """With the chat activated + progress injected, the first update is a NeedsSkill send to
    the stored chat carrying content + the _mentions list, recorded as bugbash_updates."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = _bb_updates_state()
    _active_phase(st, "bug_bash")
    prog = {"total": 2, "done": 0, "remaining": 2, "unassigned": 0, "owners": {
        "a@x": {"name": "Alice", "total": 2, "done": 0, "remaining": 2, "tests": [
            {"id": "1", "name": "T1", "url": "u1", "state": "notrun"},
            {"id": "2", "name": "T2", "url": "u2", "state": "notrun"}]}}}
    with mockctx.active({"progress": prog}):
        out = as_dict(_steps.get_step("bug_bash", "bugbash_updates").build(st))
    assert out["kind"] == "needs_skill" and out["tool"] == "workiq_send_chat_message"
    assert out["payload"]["chatId"] == "19:meeting_X@thread.v2"
    assert out["payload"]["_mentions"] == [{"id": 0, "upn": "a@x", "name": "Alice"}]
    assert out["payload"]["_automation"] == {"on_demand": "bug-bash-update-poller"}
    assert "September 2026 Bug Bash" in out["payload"]["content"]
    assert out["record_as"] == "bugbash_updates" and out["outbound"] is True
    assert _steps.get_step("bug_bash", "bugbash_updates").KIND == "scout"




def test_bugbash_updates_done_when_all_complete():
    """If every test is already complete when the step is reached, it just completes (no
    poller needed) rather than posting an empty update."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = _bb_updates_state()
    prog = {"total": 2, "done": 2, "remaining": 0, "unassigned": 0, "owners": {
        "a@x": {"name": "Alice", "total": 2, "done": 2, "remaining": 0, "tests": []}}}
    with mockctx.active({"progress": prog}):
        out = as_dict(_steps.get_step("bug_bash", "bugbash_updates").build(st))
    assert out["kind"] == "done" and "complete" in out["note"].lower()




def test_post_bugbash_update_decisions():
    """post-bugbash-update: off_hours (weekend) sends nothing; a working-hour tick with
    progress posts content+mentions; all-complete yields a `complete` wrap-up decision."""
    import tempfile, argparse, json as _json, io
    from contextlib import redirect_stdout
    from orchestrator import cli_common as _C
    from orchestrator.commands import bugbash_update as BU
    from steps.lib import mockctx

    def run(now, mocks, force=False):
        buf = io.StringIO()
        ns = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, as_of=None,
                                now=now, force=force)
        with mockctx.active(mocks), redirect_stdout(buf):
            rc = BU.cmd_post_bugbash_update(ns)
        return rc, _json.loads(buf.getvalue().strip())

    with tempfile.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = _bb_updates_state()
        _active_phase(st, "bug_bash")
        step = st.get_step("bug_bash", "bugbash_updates")
        step.status = "done"
        st.set_step("bug_bash", "bugbash_updates", step)
        _C.save_state(st, d, rid)

        # weekend → off_hours, nothing gathered
        _, dec = run("2026-08-22T10:00:00", {})
        assert dec["decision"] == "off_hours"

        remaining = {"total": 2, "done": 1, "remaining": 1, "unassigned": 0, "owners": {
            "a@x": {"name": "Alice", "total": 2, "done": 1, "remaining": 1, "tests": [
                {"id": "1", "name": "T1", "url": "u1", "state": "notrun"}]}}}
        _, dec = run("2026-08-21T10:00:00", {"progress": remaining})   # Fri 10am
        assert dec["decision"] == "post" and dec["remaining"] == 1
        assert dec["chatId"] == "19:meeting_X@thread.v2" and dec["mentions"]

        allc = {"total": 2, "done": 2, "remaining": 0, "unassigned": 0, "owners": {
            "a@x": {"name": "Alice", "total": 2, "done": 2, "remaining": 0, "tests": []}}}
        _, dec = run("2026-08-21T10:00:00", {"progress": allc})
        assert dec["decision"] == "complete" and dec["total"] == 2
        assert not _C.load_state(d, rid).get_step("bug_bash", "bugbash_updates").data.get("poll_complete")
        _ack_notifications(d, rid, "2026-08-21T10:00:00-07:00", dec["notifications"])
        assert _C.load_state(d, rid).get_step(
            "bug_bash", "bugbash_updates").data["poll_complete"] is True

        # no chat activated → no_chat
        st2 = _bb_updates_state(chat_id=None)
        _active_phase(st2, "bug_bash")
        _C.save_state(st2, d, rid)
        _, dec = run("2026-08-21T10:00:00", {"progress": remaining})
        assert dec["decision"] == "no_chat"




def test_bugbash_render_marks_auto_failed_auth_as_triage():
    """render_update shows a pre-triaged automated-auth failure distinctly (🔬 'Automated
    failure — triage'), separate from manual not-run tests, and adds a header note. A plain
    failed/not-run test keeps its normal label."""
    from tools import bugbash as BB
    prog = {"total": 3, "done": 0, "remaining": 3, "auto_failed_remaining": 1,
            "unassigned": 0, "owners": {
        "o@x": {"name": "Owner", "total": 3, "done": 0, "remaining": 3, "tests": [
            {"id": "2916347", "name": "Passkey reg", "url": "uA", "state": "failed", "auto_failed": True},
            {"id": "50", "name": "Manual T", "url": "uM", "state": "notrun", "auto_failed": False},
            {"id": "51", "name": "Manual F", "url": "uF", "state": "failed", "auto_failed": False}]}}}
    html, mentions = BB.render_update(prog, "August 2026", [{"name": "Broker", "url": "b"}])
    assert "\U0001f52c 1 failed automated Authenticator case(s) need investigation" in html
    assert "Automated failure — triage" in html                        # the auto-failed row
    assert "2916347" in html and "50" in html and "51" in html         # all three listed
    assert "Not run" in html and "Failed" in html                      # manual labels intact
    # the auto-failed case is listed FIRST within the owner's pending block
    assert html.index("2916347") < html.index("Manual T")
    assert mentions == [{"id": 0, "upn": "o@x", "name": "Owner"}]




def test_bugbash_updates_passes_auto_failed_ids_to_gather(monkeypatch):
    """bugbash_updates.gather threads the completed fill's applied failed-auth case ids into
    gather_progress so they can be flagged in the update."""
    from steps.bug_bash import bugbash_updates as BU
    from orchestrator.state import StepState
    st = _bb_updates_state()
    from tests._auth_evidence import auth_snapshot
    rc = current_rc(rc=1)
    rc["auth"] = auth_snapshot(rows={
        "Firebase Test Lab - UIAutomator E2E": [("test_2916347_x", "Failed"),
                                               ("test_2916524_y", "Failed")]})
    st.pipeline_runs = {"rcs": [rc]}
    st.set_step("bug_bash", "clone_plans_broker",
                StepState(status="done", data={"plan_id": "900", "ui_suite_id": 901}))
    st.set_step("bug_bash", "clone_plans_auth", StepState(status="done", data={"suite_id": 714999}))
    publish(st)
    captured = {}
    from tools import bugbash as BB
    monkeypatch.setattr(BB, "gather_progress",
                        lambda bp, sn, ap, asid, timeout=90, auto_failed_ids=None:
                        (True, captured.setdefault("ids", auto_failed_ids) or {"ok": 1}, ""))
    ok, _prog, _ = BU.gather(st)
    assert ok and captured["ids"] == [2916347, 2916524]




def test_clone_plans_name_override_knob():
    """The `name` mock knob overrides the derived plan/suite name (safe 'TEST ...' runs)."""
    st, out = _bb_build("clone_plans_broker",
                        {"name": "TEST Android Monthly Release - Aug 2026", "clone_id": "1"})
    assert "TEST Android Monthly Release - Aug 2026" in out["note"]
    st2, out2 = _bb_build("clone_plans_auth",
                          {"name": "TEST Android/release/08/2026", "existing": None, "create_id": "2"})
    assert "TEST Android/release/08/2026" in out2["note"]
