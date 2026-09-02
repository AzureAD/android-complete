"""Release-agent tests — schedule_state. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




def test_check_ccd_classifies_pipeline_reconciliation():
    """check-ccd validates the CCD end-to-end — temporal (past/compressed) layered
    on pipeline reconciliation — so the skill can decide how to satisfy the gate item.
    `as_of` pins the clock so the classification is deterministic."""
    import tempfile as _tf, json as _json, io, contextlib, argparse
    from orchestrator.commands import pipeline as pcmd
    from orchestrator import cli_common as _C
    src = {"org": "o", "project": "p", "pipeline_id": "3038",
           "override_variable": "CodeCompleteDate"}
    orig_src, orig_read = _C.ccd_source, pcmd.checks.read_pipeline_variable

    def _run(rid, ccd, read_ret, as_of="2026-08-18"):
        with _tf.TemporaryDirectory() as d:
            st = ReleaseState(release_id=rid, ccd=ccd, ccd_source="pipeline-default")
            _C.save_state(st, d, rid)
            _C.ccd_source = lambda: dict(src)
            pcmd.checks.read_pipeline_variable = lambda *a, **k: read_ret
            ns = argparse.Namespace(runs_root=d, release=rid, json=True, as_of=as_of)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pcmd.cmd_check_ccd(ns)
            return _json.loads(buf.getvalue())
    try:
        # future-dated, no override on the pipeline → match (not compressed: 22 days out)
        m = _run("2026-09", "2026-09-09", (True, "", ""))
        assert m["status"] == "match" and m["compressed"] is False and m["days_to_ccd"] == 22
        # override differs, in-month → conflict (+ ccd_conflict surfaced)
        conf = _run("2026-09", "2026-09-09", (True, "2026-09-16", ""))
        assert conf["status"] == "conflict" and conf["ccd_conflict"] == "2026-09-16"
        # pipeline unreadable → attest-fallback territory
        assert _run("2026-09", "2026-09-09", (False, None, "auth"))["status"] == "unreadable"
        # no CCD at all → unset
        assert _run("t", None, (True, "", ""))["status"] == "unset"
        # CCD in the PAST (current-month release whose 2nd-Wed default already passed) →
        # `past` wins over reconciliation; a future override is still surfaced to adopt
        past = _run("2026-08", "2026-08-12", (True, "2026-08-26", ""))
        assert past["status"] == "past" and past["days_to_ccd"] == -6
        assert past["override"] == "2026-08-26"    # resolver can offer to adopt it
        # CCD two days out → not past, but Phase 0's 7-day window is compressed (WARN)
        comp = _run("2026-08", "2026-08-20", (True, "", ""))
        assert comp["status"] == "match" and comp["compressed"] is True
        assert comp["runway_days"] == 2 and comp["days_to_ccd"] == 2
    finally:
        _C.ccd_source, pcmd.checks.read_pipeline_variable = orig_src, orig_read




def test_phase0_scheduled_before_ccd_minus_7():
    st, orch = _ccd_orch("2026-06-28")     # before the window opens (CCD-7 = 07-01)
    actions = orch.run_until_gate()
    assert actions[-1].kind == "scheduled"
    assert st.status == "scheduled"
    # nothing ran — the window isn't open
    assert not st.is_done("preflight", "notice")
    rpt = orch.status_report()
    assert rpt["scheduled"]["opens"] == "2026-07-01"
    assert rpt["scheduled"]["opens_in_days"] == 3




def test_phase_map_marks_scheduled():
    st, orch = _ccd_orch("2026-06-28")
    orch.run_until_gate()
    rpt = orch.status_report()
    p0 = next(p for p in rpt["phases"] if p["id"] == "preflight")
    assert p0["state"] == "scheduled"
    assert p0["opens"] == "2026-07-01"




def test_automations_cover_every_scheduled_step():
    """SELF-ENFORCING TRACEABILITY GUARDRAIL — every step that declares a
    fire_at_local must be owned by EXACTLY ONE automation in config/automations.yaml,
    and each automation's steps must exist and share one fire time. Fails LOUDLY on
    drift so a timed step can't be added without wiring its automation."""
    from orchestrator import automations as A
    problems = A.validate(CONFIG)
    assert problems == [], "automation/step mapping drift:\n  " + "\n  ".join(problems)




def test_sim_as_of_before_window_reports_scheduled():
    """If as_of is before an EARLIER phase's anchor, the fast-forward can't complete it
    (the engine holds 'scheduled') and the sim surfaces that as a problem instead of
    silently pretending. Target ccd at CCD-30: preflight (opens CCD-7) isn't due yet."""
    import tempfile
    from orchestrator import sim as SIM
    scenario = {"name": "t_sched", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD-30", "data": "mock",
                "target": {"phase": "ccd", "at": "open"}}
    with tempfile.TemporaryDirectory() as tmp:
        res = SIM.run_scenario(scenario, runs_root=tmp)
    assert res.stop_kind == "scheduled" and not res.reached
    assert any("scheduled" in p for p in res.problems)




def test_schedule_today_uses_owner_timezone():
    """schedule.today() is the date in the owner's zone (PT), not the host's."""
    from datetime import datetime
    from orchestrator import schedule
    assert schedule.today() == datetime.now(schedule.get_tz()).date()




def test_status_render_shows_versions_from_state():
    """The pipeline-runs status line reads SDK versions from state.versions (not the removed
    pipeline_runs.orchestrator.versions), and omits the authenticator branch."""
    from dataclasses import asdict
    from orchestrator import render
    from steps.build_verify import _common as K
    st = ReleaseState(release_id="2026-08")
    K.stash_orchestrator(st, "1678611", parked=True)
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0",
                        "authenticator": "release/2026/08/22"})
    line = render._pipelines_line(asdict(st))
    assert "orchestrator 1678611" in line
    assert "Common 24.6.0" in line and "Msal 8.4.2" in line and "Broker 16.5.0" in line
    assert "2026/08/22" not in line          # SDK-only display




# ======================= target month (release ship-month naming) =======================

def test_month_add_rolls_year():
    """month_add shifts a YYYY-MM by n months, rolling the year both directions."""
    from orchestrator import schedule as S
    assert S.month_add("2026-09", 1) == "2026-10"
    assert S.month_add("2026-12", 1) == "2027-01"
    assert S.month_add("2026-01", -1) == "2025-12"
    assert S.month_add("2026-09", 4) == "2027-01"
    assert S.month_add("bad", 1) is None


def test_preview_release_derives_name_and_ccd():
    """preview_release derives the full pre-init identity: id = CCD/work month, ship_label =
    CCD-month+1 (the display name), ccd = 2nd Wednesday. This is what the single start prompt
    shows so the user never picks a bare month then gets told it's a different one."""
    from orchestrator import schedule as S
    p = S.preview_release("2026-09")                     # CCD Sep -> ships October
    assert p["release_id"] == "2026-09"
    assert p["ship_month"] == "2026-10"
    assert p["ship_label"] == "October 2026"
    assert p["ccd"] == "2026-09-09"                      # 2nd Wednesday of Sep 2026
    assert p["ccd_weekday"] == "Wednesday"
    assert "Sep 9, 2026" in p["ccd_pretty"] and p["ccd_pretty"].startswith("Wednesday")
    assert S.preview_release("nope") is None


def test_preview_releases_lists_consecutive_candidates():
    """preview_releases returns `count` consecutive months; the first is flagged default.
    With no start month it begins at the current calendar month."""
    from orchestrator import schedule as S
    cands = S.preview_releases("2026-09", 3)
    assert [c["release_id"] for c in cands] == ["2026-09", "2026-10", "2026-11"]
    assert [c["ship_label"] for c in cands] == ["October 2026", "November 2026", "December 2026"]
    assert cands[0]["is_default"] is True and cands[1]["is_default"] is False
    # default start = current month (whatever "now" is, it yields a valid first candidate)
    auto = S.preview_releases(None, 1)
    assert len(auto) == 1 and auto[0]["is_default"] is True


def test_preview_release_command_json(capsys):
    """`preview-release --json` emits the candidate list the start prompt consumes."""
    import json as _json
    from orchestrator import cli as _cli
    rc = _cli.main(["preview-release", "--month", "2026-09", "--count", "2", "--json"])
    assert rc == 0
    rows = _json.loads(capsys.readouterr().out)
    assert [r["ship_label"] for r in rows] == ["October 2026", "November 2026"]
    assert rows[0]["ccd"] == "2026-09-09"


def test_default_target_month_rolls_year():
    """default_target_month = CCD/work month + 1, rolling the year at Dec->Jan."""
    from orchestrator import schedule as S
    assert S.default_target_month("2026-08") == "2026-09"
    assert S.default_target_month("2026-12") == "2027-01"
    assert S.default_target_month("2026-01") == "2026-02"
    assert S.default_target_month("not-a-month") is None




def test_target_month_label_uses_stored_then_default():
    """target_month_label reads the stored target_month, else falls back to CCD-month+1."""
    from orchestrator import schedule as S
    st = ReleaseState(release_id="2026-08")                 # unset -> default +1
    assert S.target_month_id(st) == "2026-09"
    assert S.target_month_label(st) == "September 2026"
    assert S.target_month_label(st, with_year=False) == "September"
    st.target_month = "2026-10"                             # explicit override wins
    assert S.target_month_label(st) == "October 2026" and S.target_month_id(st) == "2026-10"




def test_init_stores_default_target_month():
    """`init` persists the ship month (CCD month + 1) so docs/comms don't misname the release."""
    import tempfile, argparse
    from orchestrator.commands import release as R
    from orchestrator import cli_common as _C
    _stub_build_defs("pass")
    with tempfile.TemporaryDirectory() as tmp:
        ns = argparse.Namespace(runs_root=tmp, release="2026-08", force=False,
                                owner_email="dev@x.com", owner_name=None,
                                timezone="America/Chicago", config=CONFIG)
        R.cmd_init(ns)
        st = _C.load_state(tmp, "2026-08")
        assert st.target_month == "2026-09"                 # August work month -> September release




def test_set_target_month_command_overrides_and_resets():
    """set-target-month sets an explicit ship month (display-only) and resets to the default."""
    import tempfile, argparse
    from orchestrator.commands import release as R
    from orchestrator import cli_common as _C
    with tempfile.TemporaryDirectory() as tmp:
        st0 = ReleaseState(release_id="2026-08", target_month="2026-09")
        _C.save_state(st0, tmp, "2026-08")
        # explicit override
        R.cmd_set_target_month(argparse.Namespace(runs_root=tmp, release="2026-08", month="2026-11"))
        assert _C.load_state(tmp, "2026-08").target_month == "2026-11"
        # reset to default (no --month)
        R.cmd_set_target_month(argparse.Namespace(runs_root=tmp, release="2026-08", month=None))
        assert _C.load_state(tmp, "2026-08").target_month == "2026-09"
        # bad input is rejected, state unchanged
        rc = R.cmd_set_target_month(argparse.Namespace(runs_root=tmp, release="2026-08", month="2026/13"))
        assert rc == 1 and _C.load_state(tmp, "2026-08").target_month == "2026-09"

