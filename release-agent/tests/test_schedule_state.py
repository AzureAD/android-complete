"""Release-agent tests — schedule_state. Shared harness in tests/_harness.py."""
from tests._context import pipeline as _pipeline
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

    def _run(rid, ccd, read_ret, as_of="2026-08-18", ccd_source="pipeline-default"):
        with _tf.TemporaryDirectory() as d:
            st = ReleaseState(release_id=rid, ccd=ccd, ccd_source=ccd_source)
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
        # Owner-elected custom day within the current month (`manual`) is accepted
        manual_cur = _run("2026-08", "2026-08-14", (True, "2026-08-14", ""), ccd_source="manual")
        assert manual_cur["status"] == "match"
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
    assert orch.status_report()["status"] == "scheduled"
    # nothing ran — the window isn't open
    assert not st.is_done("preflight", "notice")
    rpt = orch.status_report()
    assert rpt["scheduled"]["opens"] == "2026-07-01"
    assert rpt["scheduled"]["opens_in_days"] == 3




def test_phase_map_marks_scheduled():
    from orchestrator import render

    st, orch = _ccd_orch("2026-06-28")
    orch.run_until_gate()
    rpt = orch.status_report()
    p0 = next(p for p in rpt["phases"] if p["id"] == "preflight")
    assert p0["state"] == "scheduled"
    assert p0["opens"] == "2026-07-01"
    view = render.status_view(rpt)
    assert "📅 **Scheduled**" in view
    assert "📅 Pre-flight & Code Complete" in view
    assert "🗓" not in view




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
    _pipeline(K.stash_orchestrator, st, "1678611", parked=True)
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
    """preview_release derives the pre-init identity: label matches the calendar month of the
    release date directly (no +1 month offset), and default ccd = 2nd Wednesday."""
    from orchestrator import schedule as S
    p = S.preview_release("2026-09")                     # Sep 2026 -> labeled September 2026
    assert p["release_id"] == "2026-09"
    assert p["label_month"] == "2026-09"
    assert p["label"] == "September"
    assert p["ship_month"] == "2026-09"
    assert p["ship_label"] == "September 2026"
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
    assert [c["label"] for c in cands] == ["September", "October", "November"]
    assert [c["ship_label"] for c in cands] == ["September 2026", "October 2026", "November 2026"]
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
    assert [r["ship_label"] for r in rows] == ["September 2026", "October 2026"]
    assert [r["label"] for r in rows] == ["September", "October"]
    assert rows[0]["ccd"] == "2026-09-09"


def test_default_target_month_matches_calendar_month_without_offset():
    """default_target_month directly matches the calendar month of the elected release date."""
    from orchestrator import schedule as S
    assert S.default_target_month("2026-11") == "2026-11"
    assert S.default_target_month("2026-12") == "2026-12"
    assert S.default_target_month("2026-01") == "2026-01"
    assert S.default_target_month("2026-11", "2026-11-18") == "2026-11"
    assert S.default_target_month("", "2027-02-17") == "2027-02"
    assert S.default_target_month("not-a-month") is None
    assert S.default_target_month("2026-13") is None


def test_target_month_label_uses_stored_then_calendar_month():
    """target_month_label reads stored target_month, else falls back to the release date's calendar month."""
    from orchestrator import schedule as S
    st = ReleaseState(release_id="2026-11", ccd="2026-11-18")
    assert S.target_month_id(st) == "2026-11"
    assert S.target_month_label(st) == "November 2026"
    assert S.target_month_label(st, with_year=False) == "November"
    st.target_month = "2026-12"                             # explicit override wins
    assert S.target_month_label(st) == "December 2026" and S.target_month_id(st) == "2026-12"


def test_init_stores_calendar_month_and_supports_custom_dates(monkeypatch):
    """`init` labels a November release date as November (no +1 offset) and accepts custom
    days in the current month or any future date via --ccd."""
    import tempfile, argparse
    from orchestrator.commands import release as R
    from orchestrator import cli_common as _C
    from tools import checks
    monkeypatch.setattr(checks, "read_pipeline_variable", lambda *a, **k: (False, None, "offline"))
    _stub_build_defs("pass")
    with tempfile.TemporaryDirectory() as tmp:
        # Default 2nd Wednesday of 2026-11 -> labeled 2026-11 (November 2026)
        ns = argparse.Namespace(runs_root=tmp, release="2026-11", ccd=None, as_of="2026-11-05",
                                force=False, owner_email="dev@x.com", owner_name=None,
                                timezone="America/Chicago", config=CONFIG)
        assert R.cmd_init(ns) == 0
        st = _C.load_state(tmp, "2026-11")
        assert st.ccd == "2026-11-11" and st.target_month == "2026-11"

        # Custom day in current month (even after 2nd Wed passed) -> accepted & labeled 2026-11
        ns_cur = argparse.Namespace(runs_root=tmp + "_cur", release=None, ccd="2026-11-18",
                                    as_of="2026-11-13", force=False, owner_email="dev@x.com",
                                    owner_name=None, timezone="America/Chicago", config=CONFIG)
        assert R.cmd_init(ns_cur) == 0
        st_cur = _C.load_state(tmp + "_cur", "2026-11")
        assert st_cur.ccd == "2026-11-18" and st_cur.ccd_source == "manual"
        assert st_cur.target_month == "2026-11"

        # Custom date in a future month -> accepted & labeled for that future month
        ns_fut = argparse.Namespace(runs_root=tmp, release=None, ccd="2027-01-20",
                                    as_of="2026-11-13", force=False, owner_email="dev@x.com",
                                    owner_name=None, timezone="America/Chicago", config=CONFIG)
        assert R.cmd_init(ns_fut) == 0
        st_fut = _C.load_state(tmp, "2027-01")
        assert st_fut.ccd == "2027-01-20" and st_fut.target_month == "2027-01"

        # Date in a past month -> rejected
        ns_past = argparse.Namespace(runs_root=tmp, release=None, ccd="2026-10-14",
                                     as_of="2026-11-13", force=False, owner_email="dev@x.com",
                                     owner_name=None, timezone="America/Chicago", config=CONFIG)
        assert R.cmd_init(ns_past) == 1


def test_set_target_month_command_overrides_and_resets():
    """set-target-month sets an explicit display month and resets to the calendar month default."""
    import tempfile, argparse
    from orchestrator.commands import release as R
    from orchestrator import cli_common as _C
    with tempfile.TemporaryDirectory() as tmp:
        st0 = ReleaseState(release_id="2026-08", ccd="2026-08-12", target_month="2026-08")
        _C.save_state(st0, tmp, "2026-08")
        # explicit override
        R.cmd_set_target_month(argparse.Namespace(runs_root=tmp, release="2026-08", month="2026-11"))
        assert _C.load_state(tmp, "2026-08").target_month == "2026-11"
        # reset to default (no --month) -> 2026-08 (calendar month of release date)
        R.cmd_set_target_month(argparse.Namespace(runs_root=tmp, release="2026-08", month=None))
        assert _C.load_state(tmp, "2026-08").target_month == "2026-08"
        # bad input is rejected, state unchanged
        rc = R.cmd_set_target_month(argparse.Namespace(runs_root=tmp, release="2026-08", month="2026/13"))
        assert rc == 1 and _C.load_state(tmp, "2026-08").target_month == "2026-08"


def test_preview_releases_offers_current_or_next_second_wednesday():
    from datetime import date
    from orchestrator import schedule as S

    # Before 2nd Wednesday of Nov 2026 (2026-11-05): offers Nov 11, 2026 labeled November.
    before = S.preview_releases(None, 3, as_of=date(2026, 11, 5))
    assert before[0]["release_id"] == "2026-11" and before[0]["ccd"] == "2026-11-11"
    assert before[0]["label"] == "November" and before[0]["recommended"] is True

    # After 2nd Wednesday of Nov 2026 (2026-11-13): offers Dec 9, 2026 labeled December.
    after = S.preview_releases(None, 3, as_of=date(2026, 11, 13))
    assert after[0]["release_id"] == "2026-11" and after[0]["ccd_past"] is True
    assert after[0]["is_default"] is True and after[0]["recommended"] is False
    assert after[1]["release_id"] == "2026-12" and after[1]["ccd"] == "2026-12-09"
    assert after[1]["label"] == "December" and after[1]["recommended"] is True


