"""Release-agent tests — build_verify. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




def test_rc_retriggered_reopens_phase2_rc_steps():
    """`rc-retriggered` reopens the two MRWP verifies + rc_report so Scout re-evaluates the
    NEWEST RC, clears them from pending_human, and flips status back to running — while
    leaving checker_fired / orchestrator_health (which a re-triggered RC doesn't invalidate)
    untouched."""
    import tempfile, argparse
    from orchestrator.commands import release as R
    from orchestrator.state import StepState
    from orchestrator import cli_common as _C
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26", ccd_source="confirmed")
    for sid in ("checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local"):
        st.set_step("build_verify", sid, StepState(status="done"))
    st.set_step("build_verify", "rc_report", StepState(status="blocked", note="UI 88%"))
    st.pending_human = ["build_verify.rc_report"]
    st.status = "awaiting_action"
    with tempfile.TemporaryDirectory() as d:
        _C.save_state(st, d, "2026-08")
        ns = argparse.Namespace(runs_root=d, release="2026-08", config=CONFIG,
                                reason="flaky broker suite re-run")
        assert R.cmd_rc_retriggered(ns) == 0
        again = _C.load_state(d, "2026-08")
    assert not again.is_done("build_verify", "mrwp_ecs")
    assert not again.is_done("build_verify", "mrwp_local")
    assert again.get_step("build_verify", "rc_report").status == "pending"
    assert again.is_done("build_verify", "checker_fired")        # untouched
    assert again.is_done("build_verify", "orchestrator_health")  # untouched
    assert "build_verify.rc_report" not in again.pending_human
    assert again.status == "running"




def test_build_verify_mrwp_blocks_on_never_ran_stage():
    """An MRWP run with a skipped/pending stage blocks (aborted pipeline), with the
    recovery TSG + escalation in the reason."""
    st, orch = _bv_state({"build_verify.mrwp_ecs": {
        "mrwp_id": "555",
        "stages": [{"name": "Build", "state": "completed", "result": "succeeded"},
                   {"name": "UI Automation", "state": "pending", "result": None}]}})
    out = _bv_build(orch, st, "mrwp_ecs")
    assert out["kind"] == "blocked"
    assert "did NOT run to completion" in out["reason"] and "UI Automation" in out["reason"]
    assert "release-orchestrator-recovery" in out["reason"]        # TSG surfaced




def test_build_verify_checker_blocks_when_not_triggered():
    """checker_fired blocks when no run has a succeeded 'Trigger Monthly Release' job."""
    st, orch = _bv_state({"build_verify.checker_fired": {"triggering": None}})
    out = _bv_build(orch, st, "checker_fired")
    assert out["kind"] == "blocked" and "triggered the release" in out["reason"]




def test_find_auth_ecs_build_picks_highest_rc_ecs(monkeypatch):
    """Discovery keys off adAccountsVersion: newest ECS build of the HIGHEST RC iteration on
    the release working-branch; Local-flavor builds are ignored."""
    from tools import pipelines as P
    builds = [
        {"id": 10, "status": "completed", "result": "succeeded",
         "templateParameters": {"adAccountsVersion": "0.0.02468-rc-RC1-ecs"}},
        {"id": 11, "status": "completed", "result": "succeeded",
         "templateParameters": {"adAccountsVersion": "0.0.02468-rc-RC1-local-flights"}},
        {"id": 20, "status": "completed", "result": "partiallySucceeded",
         "templateParameters": {"adAccountsVersion": "0.0.02468-rc-RC2-ecs"}},
        {"id": 19, "status": "completed", "result": "succeeded",
         "templateParameters": {"adAccountsVersion": "0.0.02468-rc-RC2-ecs"}},
    ]
    seen = {}
    def fake_az(args, timeout):
        seen["args"] = args
        return (True, builds, "")
    monkeypatch.setattr(P, "_az_json", fake_az)
    ok, info, _ = P.find_auth_ecs_build("release/2026/08/28")
    assert ok and info["build_id"] == 20 and info["rc"] == 2       # highest RC, newest id
    assert info["result"] == "partiallySucceeded"
    # queried the WORKING branch ref for def 475778 in msazure/One
    assert "refs/heads/working-release/2026/08/28" in seen["args"]
    assert str(P.AUTH_BUILD_DEF) in seen["args"] and P.AUTH_PROJECT in seen["args"]




def test_verify_auth_ecs_done_when_both_suites_pass():
    st, orch = _bv_state({})                                        # uses the SAFE auth_ecs mock
    out = _bv_build(orch, st, "auth_ecs")
    assert out["kind"] == "done" and "captured" in out["note"] and "clears the 90% bar" in out["note"]
    # snapshot landed in the RC iteration under its own 'auth' section
    rc = st.pipeline_runs["rcs"][-1]
    assert rc["auth"]["verdict"] == "clean"
    assert rc["auth"]["build"]["run_id"] == "900010" and rc["auth"]["test"]["run_id"] == "900011"




def test_verify_auth_ecs_captures_and_does_NOT_block_when_below_threshold():
    """auth_ecs is a DATA-AVAILABILITY check: a sub-90% Firebase result is captured (verdict
    'attention' stashed) and the step is DONE — it never gates. rc_report makes the decision."""
    st, orch = _bv_state({"build_verify.auth_ecs": {
        "auth_build": {"build_id": 900010, "rc": 1, "version": "0.0.02468-rc-RC1-ecs",
                       "status": "completed", "result": "succeeded"},
        "test_build": 900011, "suites": _auth_suites(82.76, 100.0)}})
    out = _bv_build(orch, st, "auth_ecs")
    assert out["kind"] == "done" and "BELOW the 90% bar" in out["note"]
    assert st.pipeline_runs["rcs"][-1]["auth"]["verdict"] == "attention"




def test_verify_auth_ecs_records_failed_build_without_blocking():
    """A completed-but-failed auth build is DATA (recorded, verdict 'attention', no test) and
    the step is DONE — the RC report consolidates it and decides go/hold."""
    st, orch = _bv_state({"build_verify.auth_ecs": {
        "auth_build": {"build_id": 900010, "rc": 1, "version": "0.0.02468-rc-RC1-ecs",
                       "status": "completed", "result": "failed"}}})
    out = _bv_build(orch, st, "auth_ecs")
    assert out["kind"] == "done" and "did not succeed" in out["note"]
    auth = st.pipeline_runs["rcs"][-1]["auth"]
    assert auth["verdict"] == "attention" and auth["test"] is None




def test_verify_auth_ecs_holds_when_test_not_run_yet(monkeypatch):
    from tools import pipelines as P
    monkeypatch.setattr(P, "find_auth_ui_test_build",
                        lambda bid, **k: (True, None, "no test yet"))
    st, orch = _bv_state({"build_verify.auth_ecs": {
        "auth_build": {"build_id": 900010, "rc": 1, "version": "0.0.02468-rc-RC1-ecs",
                       "status": "completed", "result": "succeeded"}}})
    out = _bv_build(orch, st, "auth_ecs")
    assert out["kind"] == "in_progress" and "hasn't appeared yet" in out["note"]




def test_rc_email_includes_separate_auth_section():
    """The RC report renders an 'Authenticator ECS' section (its own gate), independent of
    the MRWP UI headline."""
    from steps.build_verify import _common as K
    st, _ = _bv_state({})
    _seed_rc_pipeline(st, {"total": 100, "passed": 100, "failed": 0},
                      {"total": 100, "passed": 100, "failed": 0})
    rc = st.pipeline_runs["rcs"][-1]
    K.stash_auth(st, rc["rc"], {
        "build": {"run_id": "900010", "rc": rc["rc"], "version": "0.0.02468-rc-RC1-ecs",
                  "result": "succeeded"},
        "test": {"run_id": "900011", "suites": _auth_suites(82.76, 100.0)},
        "verdict": "attention"})
    model = K.rc_report_model(st)
    assert (model.get("auth") or {}).get("verdict") == "attention"
    html = K._rc_email_html(model, {"owner": "pedro"})
    assert "Authenticator ECS" in html and "UIAutomator E2E" in html
    plain = K._rc_email_plain(model, {"owner": "pedro"})
    assert "AUTHENTICATOR ECS" in plain and "does NOT affect" in plain




def test_rc_report_contemplates_both_gates_at_a_glance():
    """The subject, HTML gates banner, and plain-text GATES block all reflect BOTH the MRWP
    UI gate and the SEPARATE Authenticator-ECS gate — without merging them."""
    from steps.build_verify import _common as K
    st, _ = _bv_state({})
    # MRWP warn (94%), auth ECS below (E2E 82.76%) — two independent verdicts.
    _seed_rc_pipeline(st, {"total": 100, "passed": 94, "failed": 6},
                      {"total": 100, "passed": 100, "failed": 0})
    rc = st.pipeline_runs["rcs"][-1]
    K.stash_auth(st, rc["rc"], {
        "build": {"run_id": "900010", "rc": rc["rc"], "version": "0.0.02468-rc-RC1-ecs",
                  "result": "succeeded"},
        "test": {"run_id": "900011", "suites": _auth_suites(82.76, 100.0)},
        "verdict": "attention"})
    model = K.rc_report_model(st)
    subj = K.rc_email_subject(model)
    assert "MRWP UI:" in subj and "Auth ECS: BELOW 90% gate" in subj
    html = K._rc_email_html(model, {"owner": "pedro"})
    assert "MRWP UI:" in html and "Auth ECS:" in html          # gates banner has both
    plain = K._rc_email_plain(model, {"owner": "pedro"})
    assert "GATES (evaluated independently)" in plain
    assert "MRWP UI:" in plain and "Authenticator ECS: BELOW gate" in plain
    # a clean auth leg flips only the auth surfaces, not the MRWP verdict
    K.stash_auth(st, rc["rc"], {
        "build": {"run_id": "900010", "rc": rc["rc"], "version": "0.0.02468-rc-RC1-ecs",
                  "result": "succeeded"},
        "test": {"run_id": "900011", "suites": _auth_suites(97.0, 100.0)},
        "verdict": "clean"})
    assert "Auth ECS: pass" in K.rc_email_subject(K.rc_report_model(st))




def test_build_verify_rc_report_emails_owner():
    """rc_report composes the RC report email to the release owner as a
    NeedsSkill(workiq_send_email) from the RECORD in state.pipeline_runs (no live call);
    blocks when no owner email is set."""
    from orchestrator.outcomes import as_dict
    from steps.build_verify import _common as K
    import steps as _steps

    st = ReleaseState(release_id="2026-08", ccd="2026-08-26",
                      owner_email="dev@microsoft.com", owner_name="Dev")
    # Seed the RC snapshot the verify steps would have stored (full categories + a suite).
    K.stash_checker(st, "1678599", "2026-08-13T06:00")
    K.stash_orchestrator(st, "1678611", parked=True)
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"})
    K.stash_mrwp(st, "ECS", {
        "run_id": "1678863", "complete": True, "ran": 23, "total": 23,
        "failed_stages": ["UI Automation"], "yellow_stages": [], "never_ran": [],
        "tests": {"total": 5871, "passed": 5767, "failed": 104, "categories": {
            "unit": {"total": 5248, "passed": 5248, "failed": 0},
            "instrumented": {"total": 442, "passed": 440, "failed": 2},
            "ui": {"total": 165, "passed": 63, "failed": 102}}},
        "failed_suites": [{"name": "PROD MSAL - RC Broker (API 32)", "failed": 18,
                           "total": 44, "category": "ui", "tests": ["test_1_Foo", "test_2_Bar"]}]})
    K.stash_mrwp(st, "Local", {
        "run_id": "1678864", "complete": True, "ran": 23, "total": 23,
        "failed_stages": [], "yellow_stages": [], "never_ran": [],
        "tests": {"total": 5856, "passed": 5756, "failed": 100, "categories": {}},
        "failed_suites": []})

    out = as_dict(_steps.get_step("build_verify", "rc_report").build(st))
    assert out["kind"] == "needs_skill" and out["tool"] == "workiq_send_email"
    assert out["payload"]["to"] == ["dev@microsoft.com"] and out["payload"]["isHtml"]
    assert out["payload"]["followup_command"] == "record-rc-report"
    body = out["payload"]["body"]
    assert "1678863" in body                          # run id present
    assert "UI-automation failure rate" in body       # per-category headline metric
    assert "61.8%" in body                            # 102/165 UI failures — the real UI rate
    assert "Unit" in body and "Instrumented" in body and "UI automation" in body
    assert "test_1_Foo" in body                       # failing test names still listed
    assert out["record_as"] == "rc_report" and out["outbound"] is True
    # no owner → blocked
    st2 = ReleaseState(release_id="2026-08", ccd="2026-08-26")
    out2 = as_dict(_steps.get_step("build_verify", "rc_report").build(st2))
    assert out2["kind"] == "blocked" and "owner" in out2["reason"]




def test_rc_ui_gate_and_run_links():
    """The Phase-2 UI gate is three-tier on the combined UI pass rate across BOTH MRWP
    providers: 100% → 'clean'; >=90% & <100% → 'warn' (non-blocking, investigate in
    parallel); <90% → 'attention' (blocking, with a failing-suite summary); no UI tests →
    'clean' with a warning. rc_run_links surfaces every evaluated run."""
    from steps.build_verify import _common as K

    def _model(ecs_ui, local_ui, ecs_suites=None):
        return {
            "release": "2026-08",
            "checker": {"fired": True, "run_id": 111},
            "orchestrator": {"found": True, "run_id": 222},
            "mrwp": {
                "ECS": {"run_id": 333, "failed_suites": ecs_suites or [],
                        "tests": {"categories": {"ui": ecs_ui}}},
                "Local": {"run_id": 444, "failed_suites": [],
                          "tests": {"categories": {"ui": local_ui}}}}}

    # 200/200 = 100% → clean (non-blocking)
    g0 = K.rc_ui_gate(_model({"total": 100, "passed": 100, "failed": 0},
                             {"total": 100, "passed": 100, "failed": 0}))
    assert g0["verdict"] == "clean" and g0["blocking"] is False and g0["pass_pct"] == 100.0

    # 180/200 = 90.0% → exactly at the bar, not clean → warn (non-blocking)
    g = K.rc_ui_gate(_model({"total": 100, "passed": 100, "failed": 0},
                            {"total": 100, "passed": 80, "failed": 20}))
    assert g["verdict"] == "warn" and g["blocking"] is False
    assert g["pass_pct"] == 90.0 and g["ui_total"] == 200
    assert "in parallel" in g["detail"]

    # 160/200 = 80% → below the bar → attention (blocking), with the failing suite listed
    fail_model = _model({"total": 100, "passed": 60, "failed": 40},
                        {"total": 100, "passed": 100, "failed": 0},
                        ecs_suites=[{"name": "PROD MSAL - RC Broker (API 32)",
                                     "failed": 40, "total": 100, "category": "ui"}])
    g2 = K.rc_ui_gate(fail_model)
    assert g2["verdict"] == "attention" and g2["blocking"] is True and g2["pass_pct"] == 80.0
    assert "BELOW" in g2["detail"] and "PROD MSAL - RC Broker (API 32)" in g2["detail"]
    # the three exits are spelled out: re-trigger (flaky) / cherry-pick (bug) / override
    assert "rc-retriggered" in g2["detail"]
    assert "cherry-pick-process-for-broker-libraries" in g2["detail"]
    assert "LAST RESORT" in g2["detail"] and "skip" in g2["detail"]

    # no UI tests anywhere → clean with a warning (absence of data is not a failure)
    g3 = K.rc_ui_gate({"mrwp": {"ECS": {"tests": {"categories": {}}},
                                "Local": {"tests": {"categories": {}}}}})
    assert g3["verdict"] == "clean" and g3["blocking"] is False
    assert g3["ui_total"] == 0 and "No UI-automation" in g3["detail"]

    # every evaluated run becomes a durable link
    links = K.rc_run_links(fail_model)
    names = [l["name"] for l in links]
    assert names == ["Code Complete Checker run", "Release Orchestrator run",
                     "MRWP ECS run", "MRWP Local run"]
    assert all("buildId=" in l["url"] for l in links)




def test_record_rc_report_applies_ui_gate_and_stashes_links():
    """`record-rc-report` (the follow-up the skill runs after emailing) reads the RC
    snapshot from state and applies the 90% UI gate: >=90% → step done; <90% → step
    BLOCKS (awaiting_action). Either way it stashes the evaluated run links on the step."""
    import tempfile as _tf
    from orchestrator.commands import rc_report as RR
    from orchestrator.state import StepState

    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-26", owner_email="dev@microsoft.com")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()

        class A:
            runs_root = d; release = rid; config = CONFIG; as_of = None

        # PASS: 190/200 = 95% ≥ 90 → step done, links stashed
        _seed_rc_pipeline(st, {"total": 100, "passed": 95, "failed": 5},
                          {"total": 100, "passed": 95, "failed": 5})
        C.save_state(st, d, rid)
        assert RR.cmd_record_rc_report(A) == 0
        s1 = C.load_state(d, rid)
        assert s1.is_done("build_verify", "rc_report")
        step1 = s1.get_step("build_verify", "rc_report")
        assert [l["name"] for l in step1.links] == [
            "Code Complete Checker run", "Release Orchestrator run",
            "MRWP ECS run", "MRWP Local run"]

        # reset the step + re-seed the SAME runs with a failing UI slice (60% < 90) →
        # blocked, links still stashed. Same run ids → updates the current rc in place.
        s1.set_step("build_verify", "rc_report", StepState())
        _seed_rc_pipeline(s1, {"total": 100, "passed": 60, "failed": 40},
                          {"total": 100, "passed": 60, "failed": 40})
        C.save_state(s1, d, rid)
        assert RR.cmd_record_rc_report(A) == 2
        s2 = C.load_state(d, rid)
        step2 = s2.get_step("build_verify", "rc_report")
        assert step2.status == "blocked" and not s2.is_done("build_verify", "rc_report")
        assert s2.status == "awaiting_action"
        assert "build_verify.rc_report" in s2.pending_human
        assert "BELOW" in step2.note and len(step2.links) == 4
        # the same rc was updated in place (not a spurious new RC iteration)
        assert len(s2.pipeline_runs["rcs"]) == 1




def test_record_rc_report_holds_when_auth_gate_fails_though_mrwp_clean():
    """The rc_report consolidation blocks (holds for attestation) when the Authenticator-ECS
    gate fails, EVEN IF the MRWP UI gate is clean — the two are separate evaluations but
    either one holding stops auto-advance. A clean auth leg lets it pass."""
    import tempfile as _tf
    from orchestrator.commands import rc_report as RR
    from orchestrator.state import StepState
    from steps.build_verify import _common as K

    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-26", owner_email="dev@microsoft.com")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()

        class A:
            runs_root = d; release = rid; config = CONFIG; as_of = None

        # MRWP clean (100% UI), but auth ECS BELOW (E2E 82.76%) -> hold for attestation.
        _seed_rc_pipeline(st, {"total": 100, "passed": 100, "failed": 0},
                          {"total": 100, "passed": 100, "failed": 0})
        rc = st.pipeline_runs["rcs"][-1]
        K.stash_auth(st, rc["rc"], {
            "build": {"run_id": "900010", "rc": rc["rc"], "version": "0.0.02468-rc-RC1-ecs",
                      "result": "succeeded"},
            "test": {"run_id": "900011", "suites": _auth_suites(82.76, 100.0)},
            "verdict": "attention"})
        C.save_state(st, d, rid)
        assert RR.cmd_record_rc_report(A) == 2                     # blocked by AUTH, not MRWP
        s1 = C.load_state(d, rid)
        assert s1.get_step("build_verify", "rc_report").status == "blocked"
        # links now include the auth build + test
        names = [l["name"] for l in s1.get_step("build_verify", "rc_report").links]
        assert "Authenticator ECS build" in names and "Authenticator ECS UI tests" in names

        # flip auth to clean -> now both gates clear -> pass (auto-advance)
        s1.set_step("build_verify", "rc_report", StepState())
        rc = s1.pipeline_runs["rcs"][-1]
        K.stash_auth(s1, rc["rc"], {
            "build": {"run_id": "900010", "rc": rc["rc"], "version": "0.0.02468-rc-RC1-ecs",
                      "result": "succeeded"},
            "test": {"run_id": "900011", "suites": _auth_suites(97.0, 100.0)},
            "verdict": "clean"})
        C.save_state(s1, d, rid)
        assert RR.cmd_record_rc_report(A) == 0
        assert C.load_state(d, rid).is_done("build_verify", "rc_report")




def test_rc_report_email_shows_retry_warning():
    """When a unit test recovered on retry, the RC report (plain + HTML) surfaces a retry
    warning that lists it (counted as passed but flagged)."""
    from steps.build_verify import _common as K
    model = {"release": "2026-08", "checker": {"run_id": 1},
             "orchestrator": {"run_id": 2, "versions": {}, "parked": True},
             "mrwp": {"ECS": {"run_id": 3, "ran": 23, "total": 23,
                              "tests": {"categories": {
                                  "unit": {"total": 100, "passed": 100, "failed": 0,
                                           "recovered": ["testNullDrsMetadata"]},
                                  "ui": {"total": 10, "passed": 10, "failed": 0}}}},
                      "Local": {"run_id": 4, "ran": 23, "total": 23, "tests": {"categories": {}}}},
             "problems": []}
    assert K.recovered_unit_tests(model) == ["testNullDrsMetadata"]
    plain = K._rc_email_plain(model, {})
    html = K._rc_email_html(model, {})
    assert "RETRY WARNING" in plain and "testNullDrsMetadata" in plain
    assert "Retry warning" in html and "testNullDrsMetadata" in html




def test_rc_model_shape_agrees_across_live_and_state_paths():
    """H1 guard: the live builder (pipelines.release_report → assemble_rc_model) and the
    state builder (steps._common.rc_report_model → assemble_rc_model) produce the SAME
    top-level model shape, so the gate/email/diagnostic never drift."""
    from tools import pipelines as P
    from steps.build_verify import _common as K
    # a canonical assembled model has exactly these top-level keys
    m = P.assemble_rc_model("2026-08", {"fired": True, "run_id": 1},
                            {"found": True, "healthy": True, "run_id": 2, "versions": {}},
                            {"ECS": {"run_id": 3, "complete": True}}, rc=1, id_source="tags")
    assert set(m) == {"release", "checker", "orchestrator", "mrwp", "problems", "rc", "mrwp_id_source"}
    # the state path yields the same core keys (no id_source — that's live-only)
    st = ReleaseState(release_id="2026-08")
    _seed_rc_pipeline(st, {"total": 10, "passed": 10, "failed": 0},
                      {"total": 10, "passed": 10, "failed": 0})
    sm = K.rc_report_model(st)
    assert {"release", "checker", "orchestrator", "mrwp", "problems", "rc"} <= set(sm)
    assert sm["rc"] == 1 and sm["problems"] == []
    # a never-ran MRWP snapshot yields the SAME problem string the live path derives
    st2 = ReleaseState(release_id="2026-08")
    from steps.build_verify import _common as K2
    K2.stash_mrwp(st2, "ECS", {"run_id": "9", "complete": False, "never_ran": ["UI Automation"]})
    pm = K2.rc_report_model(st2)
    assert any("did NOT run to completion" in p and "UI Automation" in p for p in pm["problems"])




def test_rc_report_aggregates_and_formats():
    """release_report composes the helpers into one model, and the rc-report formatter
    renders the chain + test breakdown. Helpers are monkeypatched so it's offline."""
    from tools import pipelines as P
    from orchestrator.commands import rc_report as RR
    orig = {n: getattr(P, n) for n in
            ("find_checker_runs", "get_timeline", "find_orchestrator_run", "get_stages",
             "mrwp_run_ids", "get_test_summary", "get_failed_tests")}
    try:
        P.get_failed_tests = lambda *a, **k: (True, [
            {"name": "PROD MSAL - RC Broker (API 32)", "failed": 2, "total": 44,
             "tests": ["test_1_Foo", "test_2_Bar"]}], "")
        P.find_checker_runs = lambda *a, **k: (True, [{"id": 10, "queueTime": "2026-08-13T06:00:00Z"}], "")
        P.get_timeline = lambda *a, **k: (True, [{"type": "Job", "name": "Trigger Monthly Release",
                                                  "result": "succeeded"}], "")
        P.find_orchestrator_run = lambda *a, **k: (True, {"id": 20, "tags": [
            "AuthenticatorBranch=release-2026-08-13", "NextCommonVersion=24.6.0",
            "NextMsalVersion=8.4.2", "NextBrokerVersion=16.5.0"]}, "")

        def _stages(org, project, bid, timeout=90):
            if bid == 20:   # orchestrator: pre-gate green, parked
                return (True, [
                    {"name": "Validate Branch and Versions availability", "state": "completed", "result": "succeeded"},
                    {"name": "Create Release Branches", "state": "completed", "result": "succeeded"},
                    {"name": "Trigger RC Testing", "state": "completed", "result": "succeeded"},
                    {"name": "Remove RC Tags", "state": "pending", "result": None}], "")
            return (True, [{"name": "Build", "state": "completed", "result": "succeeded"},
                           {"name": "UI Automation", "state": "completed", "result": "failed"}], "")
        P.get_stages = _stages
        P.mrwp_run_ids = lambda *a, **k: (True, {"ECS": 111, "Local": 222, "rc": 2}, "", "tags")
        P.get_test_summary = lambda org, project, bid, timeout=90: (
            True, {"total": 100, "passed": 96, "failed": 4,
                   "runs": [{"name": "UI", "total": 20, "passed": 16, "failed": 4}]}, "")

        m = P.release_report("O", "P", "2026-08")
        assert m["checker"]["fired"] and m["checker"]["run_id"] == 10
        assert m["orchestrator"]["healthy"] and m["orchestrator"]["parked"]
        assert m["orchestrator"]["versions"]["Common"] == "24.6.0"
        assert m["mrwp"]["ECS"]["complete"] and m["mrwp"]["Local"]["complete"]
        assert m["mrwp"]["ECS"]["failed_stages"] == ["UI Automation"]
        assert m["mrwp"]["ECS"]["failed_suites"][0]["tests"] == ["test_1_Foo", "test_2_Bar"]
        assert m["rc"] == 2                             # authoritative RC iteration flows into the model
        assert m["problems"] == []                      # red stages/tests don't add problems
        text = RR._format(m)
        assert "RC Pipeline Status" in text and "parked at 'Remove RC Tags'" in text
        assert "MRWP ECS" in text and "MRWP Local" in text
        assert "test_1_Foo" in text                     # individual failing tests listed
        assert "triaged in bug bash" not in text        # the dismissive line was removed
    finally:
        for n, f in orig.items():
            setattr(P, n, f)




def test_rc_report_flags_never_ran_stage_as_problem():
    """A never-ran MRWP stage becomes a reported problem (the report surfaces it even
    though the report itself never gates)."""
    from tools import pipelines as P
    orig = {n: getattr(P, n) for n in
            ("find_checker_runs", "get_timeline", "find_orchestrator_run", "get_stages",
             "mrwp_run_ids", "get_test_summary")}
    try:
        P.find_checker_runs = lambda *a, **k: (True, [{"id": 10, "queueTime": "t"}], "")
        P.get_timeline = lambda *a, **k: (True, [{"type": "Job", "name": "Trigger Monthly Release", "result": "succeeded"}], "")
        P.find_orchestrator_run = lambda *a, **k: (True, {"id": 20, "tags": []}, "")
        P.get_stages = lambda org, project, bid, timeout=90: (
            (True, [{"name": "Validate Branch and Versions availability", "state": "completed", "result": "succeeded"},
                    {"name": "Create Release Branches", "state": "completed", "result": "succeeded"},
                    {"name": "Trigger RC Testing", "state": "completed", "result": "succeeded"}], "")
            if bid == 20 else
            (True, [{"name": "Build", "state": "completed", "result": "succeeded"},
                    {"name": "UI Automation", "state": "pending", "result": None}], ""))
        P.mrwp_run_ids = lambda *a, **k: (True, {"ECS": 111, "Local": 222}, "", "logs")
        P.get_test_summary = lambda *a, **k: (False, None, "no tests")
        m = P.release_report("O", "P", "2026-08")
        assert not m["mrwp"]["ECS"]["complete"]
        assert any("did NOT run to completion" in p for p in m["problems"])
    finally:
        for n, f in orig.items():
            setattr(P, n, f)




def test_mrwp_run_ids_picks_newest_rc_iteration():
    """The orchestrator tags each RC iteration's MRWP runs as RC<N>-ECS / RC<N>-Local. A
    re-trigger adds a higher-numbered set; mrwp_run_ids must return the CURRENT (highest N)
    RC's ids + that rc number — regardless of build-id ordering."""
    from tools import pipelines as P
    run = {"id": 20, "tags": ["RC1-ECS=1678863", "RC1-Local=1678864",
                              "RC2-ECS=1679999", "RC2-Local=1679000"]}
    ok, ids, _, source = P.mrwp_run_ids("O", "P", run)
    assert ok and source == "tags"
    assert ids["ECS"] == "1679999" and ids["Local"] == "1679000" and ids["rc"] == 2
    # single RC iteration works
    ok2, ids2, _, _ = P.mrwp_run_ids("O", "P", {"id": 1, "tags": ["RC1-ECS=5", "RC1-Local=6"]})
    assert ok2 and ids2 == {"ECS": "5", "Local": "6", "rc": 1}
    # an incomplete RC (only one provider tagged yet) is ignored in favor of a complete lower RC
    run3 = {"id": 3, "tags": ["RC1-ECS=100", "RC1-Local=101", "RC2-ECS=200"]}
    ok3, ids3, _, _ = P.mrwp_run_ids("O", "P", run3)
    assert ok3 and ids3["rc"] == 1 and ids3["ECS"] == "100"




def test_stash_mrwp_uses_authoritative_rc_number():
    """stash_mrwp with an explicit rc merges ECS+Local into the entry with THAT number
    (mirroring the pipeline), and a higher rc appends a new entry — regardless of order."""
    from steps.build_verify import _common as K
    st = ReleaseState(release_id="2026-08")
    # RC1: ECS then Local -> one entry rc=1 with both providers
    K.stash_mrwp(st, "ECS", {"run_id": "100"}, rc=1)
    K.stash_mrwp(st, "Local", {"run_id": "101"}, rc=1)
    rcs = st.pipeline_runs["rcs"]
    assert len(rcs) == 1 and rcs[0]["rc"] == 1
    assert rcs[0]["ecs"]["run_id"] == "100" and rcs[0]["local"]["run_id"] == "101"
    # RC2 (re-trigger, authoritative) -> a second entry rc=2
    K.stash_mrwp(st, "ECS", {"run_id": "200"}, rc=2)
    rcs = st.pipeline_runs["rcs"]
    assert len(rcs) == 2 and rcs[-1]["rc"] == 2 and rcs[-1]["ecs"]["run_id"] == "200"
    # authoritative numbering is preserved even if the pipeline SKIPS a number (RC1 -> RC4)
    st2 = ReleaseState(release_id="2026-09")
    K.stash_mrwp(st2, "ECS", {"run_id": "1"}, rc=1)
    K.stash_mrwp(st2, "ECS", {"run_id": "4"}, rc=4)
    assert [e["rc"] for e in st2.pipeline_runs["rcs"]] == [1, 4]




def test_stash_mrwp_falls_back_to_local_counter_without_rc():
    """With rc=None (mock id / log fallback), a changed run_id rolls forward to the next
    local rc number (unchanged legacy behavior)."""
    from steps.build_verify import _common as K
    st = ReleaseState(release_id="2026-08")
    K.stash_mrwp(st, "ECS", {"run_id": "100"})
    K.stash_mrwp(st, "Local", {"run_id": "101"})           # same rc (no run_id change for local)
    assert [e["rc"] for e in st.pipeline_runs["rcs"]] == [1]
    K.stash_mrwp(st, "ECS", {"run_id": "200"})             # ECS run_id changed -> new rc
    assert [e["rc"] for e in st.pipeline_runs["rcs"]] == [1, 2]




def test_poll_rc_waits_then_nudges_once_at_6h():
    """The RC poller returns `waiting` while the run is in-flight, sends ONE courtesy
    nudge to the owner once it has been in-flight >= 6h, and does not repeat the nudge."""
    import tempfile
    from orchestrator.state import StepState
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-26", ccd_source="confirmed",
                          owner_email="dev@microsoft.com", owner_name="Dev")
        st.set_step("build_verify", "mrwp_ecs",
                    StepState(status="in_flight", note="RC running",
                              data={"in_flight_since": "2026-08-20T00:00:00+00:00",
                                    "poll_in_min": 30}))
        C.save_state(st, d, rid)
        # +2h → still waiting
        dec = _run_poll_rc(d, rid, "2026-08-20T02:00:00+00:00")
        assert dec["decision"] == "waiting" and dec["step"] == "mrwp_ecs"
        assert abs(dec["elapsed_hours"] - 2.0) < 0.01 and dec["poll_in_min"] == 30
        # +7h → nudge (once), addressed to the owner
        dec = _run_poll_rc(d, rid, "2026-08-20T07:00:00+00:00")
        assert dec["decision"] == "nudge"
        assert dec["nudge"]["email"]["to"] == ["dev@microsoft.com"]
        assert "polling" in dec["nudge"]["teams"]["text"]
        # nudged_at stamped → a later poll is waiting again (no repeat nudge)
        dec = _run_poll_rc(d, rid, "2026-08-20T09:00:00+00:00")
        assert dec["decision"] == "waiting"




def test_poll_rc_resolved_blocked_idle():
    """Once no verify step is in-flight, the poller reports the terminal verdict:
    resolved (rc_report done) / blocked (rc_report blocked) / idle (nothing running)."""
    import tempfile
    from orchestrator.state import StepState
    with tempfile.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-26", ccd_source="confirmed")
        C.save_state(st, d, rid)
        assert _run_poll_rc(d, rid, "2026-08-20T09:00:00+00:00")["decision"] == "idle"

        st.set_step("build_verify", "rc_report", StepState(status="done", note="UI CLEAN"))
        C.save_state(st, d, rid)
        assert _run_poll_rc(d, rid, "2026-08-20T09:00:00+00:00")["decision"] == "resolved"

        st.set_step("build_verify", "rc_report", StepState(status="blocked", note="UI 80%"))
        C.save_state(st, d, rid)
        r = _run_poll_rc(d, rid, "2026-08-20T09:00:00+00:00")
        assert r["decision"] == "blocked" and r["note"] == "UI 80%"




def test_rc_poller_automation_is_on_demand_interval():
    """The build-verify-rc-poller is planned as an on-demand 30-min interval automation
    driving rc_report, with a bespoke poll prompt (not the default step-action prompt)."""
    from orchestrator import automations as A
    assert A.validate(CONFIG) == []
    plan = A.plan(CONFIG, "2026-08", "2026-08-26")
    rc = next(a for a in plan["automations"] if a["slug"] == "build-verify-rc-poller")
    assert rc["on_demand"] and rc["interval"] == "30 minutes"
    assert rc["steps"] == ["build_verify.rc_report"]
    assert "poll-rc --release 2026-08" in rc["prompt"] and "6h" in rc["prompt"]




def test_stash_mrwp_appends_new_rc_on_id_change():
    """stash_mrwp merges ecs+local into ONE rc entry, and appends a NEW rc iteration only
    when a provider's run id changes (RC Testing re-triggered). Latest = rcs[-1]."""
    from steps.build_verify import _common as K
    st = ReleaseState(release_id="2026-08")
    ecs1 = {"run_id": "900001", "complete": True, "tests": {"categories": {"ui": {"total": 10, "passed": 9, "failed": 1}}}}
    K.stash_mrwp(st, "ECS", ecs1)
    K.stash_mrwp(st, "Local", {"run_id": "900002", "complete": True})
    assert len(st.pipeline_runs["rcs"]) == 1                      # both merged into rc 1
    assert K.latest_rc(st)["rc"] == 1
    # re-resolving the SAME ecs id updates in place — no new rc
    K.stash_mrwp(st, "ECS", ecs1)
    assert len(st.pipeline_runs["rcs"]) == 1
    # a NEW ecs id → RC re-triggered → append rc 2
    K.stash_mrwp(st, "ECS", {"run_id": "910001", "complete": True})
    K.stash_mrwp(st, "Local", {"run_id": "910002", "complete": True})
    rcs = st.pipeline_runs["rcs"]
    assert [r["rc"] for r in rcs] == [1, 2]
    assert K.latest_rc(st)["ecs"]["run_id"] == "910001"          # latest = last




def test_ui_test_status_blocks_without_rc_runs():
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = _uts_state(plan_id="900")     # plan present, but no build_ids + no pipeline_runs
    with mockctx.active({}):
        out = as_dict(_steps.get_step("bug_bash", "ui_test_status").build(st))
    assert out["kind"] == "blocked" and "RC pipeline runs" in out["reason"]




def test_digest_shows_rc_line_when_build_verify_active():
    """When a phase that opts in (show_pipeline_runs) is active and run ids are on state,
    the daily digest carries a one-line RC summary; phases that don't opt in omit it."""
    from orchestrator import render
    r = {"release_id": "2026-08", "readiness_signed": True,
         "active_phase": {"id": "build_verify", "name": "Build & RC", "num": 2,
                          "show_pipeline_runs": True,
                          "due": True, "started": True, "done": 2, "total": 5,
                          "outstanding": [], "completed": ["checker_fired", "orchestrator_health"]},
         "pipeline_runs": {
             "checker": {"run_id": "1678599"},
             "orchestrator": {"run_id": "1678611", "versions": {"Broker": "1.0.0"}},
             "rcs": [{"rc": 1, "ecs": {"run_id": "900001"}, "local": {"run_id": "900002"}}]}}
    text = render.notification(r)
    md = render.notification_markdown(r)
    assert "RC pipelines:" in text and "orchestrator 1678611" in text
    assert "MRWP ECS 900001 / Local 900002" in md
    # a phase that doesn't opt in omits the RC line
    r2 = dict(r, active_phase=dict(r["active_phase"], id="prep", name="Prep",
                                   show_pipeline_runs=False))
    assert "RC pipelines:" not in render.notification(r2)




def test_sim_fast_forwards_to_rc_gate_offline():
    """The at_rc_gate scenario (fine input mocks, no az) fast-forwards Phases 0-1, runs the
    4 build_verify steps for real on injected inputs, stashes the pipeline ids, auto-advances
    rc_report, and lands past Phase 2 at the bug-bash entry — all offline."""
    import tempfile
    from orchestrator import sim as SIM
    with tempfile.TemporaryDirectory() as tmp:
        res = SIM.run_scenario("at_rc_gate", runs_root=tmp)
    assert res.reached and res.stop_kind == "done"
    st = res.state
    # earlier phases complete
    assert all(st.is_done("preflight", s) for s in
               ("notice", "confirm_reminders", "vitals", "cron"))
    assert all(st.is_done("ccd", s) for s in ("final_reminder", "localization"))
    # the 4 verification steps ran (real build() on mocks) and rc_report auto-advanced
    for s in ("checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local", "rc_report"):
        assert st.is_done("build_verify", s), s
    from orchestrator.engine import Orchestrator as _O
    assert _O(CONFIG, st).current_phase_id() == "bug_bash"   # positioned past Phase 2
    # pipeline runs were stashed by the steps during the sim (nested RC schema)
    assert st.pipeline_runs["orchestrator"]["run_id"] == "1678611"
    assert st.pipeline_runs["rcs"][-1]["ecs"]["run_id"] == "1678863"
    assert st.readiness_signed




def test_orchestrator_health_populates_state_versions():
    """Phase 2 orchestrator_health fills state.versions from the run tags — common/msal/broker
    from Next*Version and authenticator as the release branch from AuthenticatorBranch."""
    from steps.lib import mockctx
    from steps.build_verify import orchestrator_health as OH
    st = ReleaseState(release_id="2026-08")
    run = {"id": 777, "tags": ["AuthenticatorBranch=release-2026-08-22",
                               "NextCommonVersion=24.6.0", "NextMsalVersion=8.4.2",
                               "NextBrokerVersion=16.5.0"]}
    stages = [{"name": n, "state": "completed", "result": "succeeded"}
              for n in OH.CONFIG["required_stages"]]
    stages.append({"name": OH.CONFIG["park_stage"], "state": "pending", "result": None})
    with mockctx.active({"run": run, "stages": stages}):
        OH.build(st)
    assert st.versions == {"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0",
                           "authenticator": "release/2026/08/22"}




# ======================= telemetry_verify + wiki_payload (new steps) =======================

def test_telemetry_verify_composes_kusto_needsskill():
    """build() resolves the bug-bash version and returns a NeedsSkill(kusto_query) with the
    checklist query + the record-telemetry follow-up."""
    from steps.lib import mockctx
    from steps.build_verify import telemetry_verify as TV
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"version": "6.2608.5658"}):
        out = TV.build(st)
    assert out.kind == "needs_skill" and out.tool == "kusto_query"
    p = out.payload
    assert p["cluster_uri"].startswith("https://") and p["database"]
    assert 'AppInfo_Version == "6.2608.5658"' in p["query"] and "loadaccountsoperations" in p["query"]
    assert p["query"].rstrip().endswith("| count")
    assert p["followup_command"] == "record-telemetry" and out.record_as == "telemetry_verify"




def test_telemetry_verify_blocks_without_version():
    """No Authenticator release branch on state → Blocked (nothing to query)."""
    from steps.build_verify import telemetry_verify as TV
    st = ReleaseState(release_id="2026-08")               # no versions.authenticator
    out = TV.build(st)
    assert out.kind == "blocked" and "release branch" in out.reason




def test_record_telemetry_pass_and_attention():
    """record-telemetry: rows>0 → step done; rows==0 → attention (blocked) with the
    Android Core Team heads-up in the detail."""
    import tempfile as _tf
    from orchestrator.commands import telemetry_cmd as TC
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-26", owner_email="dev@microsoft.com")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()
        C = __import__("orchestrator.cli_common", fromlist=["x"])
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG; as_of = None; version = "6.2608.5658"
            rows = "5"
        assert TC.cmd_record_telemetry(A) == 0
        assert C.load_state(d, rid).is_done("build_verify", "telemetry_verify")

        # zero rows → attention
        A.rows = "0"
        assert TC.cmd_record_telemetry(A) == 2
        s2 = C.load_state(d, rid)
        step = s2.get_step("build_verify", "telemetry_verify")
        assert step.status == "blocked" and "Android Core Team" in step.note

