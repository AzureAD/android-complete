"""Release-agent tests — sim. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




def test_sim_open_positions_at_target_entry():
    """`at: open` (data: mock) completes every phase before the target and stops at the
    target's entry with nothing in it run — the drop-in point for live validation."""
    import tempfile
    from orchestrator import sim as SIM
    scenario = {"name": "t_open", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD+1", "data": "mock",
                "target": {"phase": "build_verify", "at": "open"}}
    with tempfile.TemporaryDirectory() as tmp:
        res = SIM.run_scenario(scenario, runs_root=tmp)
    assert res.reached and res.stop_kind == "open"
    st = res.state
    assert st.is_done("preflight", "cron") and st.is_done("ccd", "localization")
    # nothing in the target phase has run
    assert not any(st.is_done("build_verify", s) for s in
                   ("checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local", "rc_report"))
    assert st.current_phase == "build_verify"




def test_sim_done_mode_completes_phase_and_advances():
    """`at: done` runs the whole target phase and lands at the next phase (Phase 2 has no
    gate now — rc_report auto-advances)."""
    import tempfile
    from orchestrator import sim as SIM
    scenario = {"name": "t_done", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD+2", "data": "mock",
                "target": {"phase": "build_verify", "at": "done"}}
    with tempfile.TemporaryDirectory() as tmp:
        res = SIM.run_scenario(scenario, runs_root=tmp)
    st = res.state
    assert all(st.is_done("build_verify", s) for s in
               ("checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local", "rc_report"))
    from orchestrator.engine import Orchestrator
    orch = Orchestrator(CONFIG, st)
    assert orch.current_phase_id() == "bug_bash"




def test_sim_surfaces_blocked_target_step():
    """A genuine block in the target phase (a never-ran MRWP stage) stops the sim and
    is reported as a problem — this is what live validation is meant to catch."""
    import tempfile
    from orchestrator import sim as SIM
    scenario = {"name": "t_block", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD+2", "data": "mock",
                "target": {"phase": "build_verify", "at": "gate"},
                "mocks": {"build_verify.mrwp_ecs": {
                    "mrwp_id": "555",
                    "stages": [{"name": "Build", "state": "completed", "result": "succeeded"},
                               {"name": "UI Automation", "state": "pending", "result": None}]}}}
    with tempfile.TemporaryDirectory() as tmp:
        res = SIM.run_scenario(scenario, runs_root=tmp)
    assert not res.reached and res.stop_kind == "blocked"
    assert any("mrwp_ecs" in p and "UI Automation" in p for p in res.problems)




def test_sim_freeze_writes_fixture(tmp_path=None):
    """--freeze snapshots the engine-produced state to a fixture that reloads cleanly."""
    import tempfile, os
    from orchestrator import sim as SIM
    from orchestrator.state import ReleaseState
    scenario = {"name": "t_freeze", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD+1", "data": "mock",
                "target": {"phase": "build_verify", "at": "open"}}
    fixture = os.path.join(SIM.FIXTURE_DIR, "t_freeze.json")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            res = SIM.run_scenario(scenario, runs_root=tmp, freeze=True)
        assert res.frozen_to == fixture and os.path.exists(fixture)
        reloaded = ReleaseState.load(fixture)     # engine-produced fixture round-trips
        assert reloaded.is_done("ccd", "localization")
    finally:
        if os.path.exists(fixture):
            os.remove(fixture)




def test_sim_surfaces_blocked_step_in_parallel_phase():
    """A blocked auto step in a PARALLEL target phase (preflight) is surfaced as
    'blocked', not spun to the iteration cap. Regression guard for the parallel
    block-detection in the fast-forward's 'ran' branch."""
    import tempfile
    from orchestrator import sim as SIM
    scenario = {"name": "t_par_block", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD-6", "data": "mock",
                "target": {"phase": "preflight", "at": "gate"},
                "mocks": {"preflight.cg": {"outcome": "blocked", "reason": "sim: CG alert"}}}
    with tempfile.TemporaryDirectory() as tmp:
        res = SIM.run_scenario(scenario, runs_root=tmp)
    assert res.stop_kind == "blocked" and res.steps_forwarded < 50
    assert any("preflight.cg" in p for p in res.problems)




def test_sim_backs_up_existing_state_before_seeding():
    """Seeding over an existing release backs the old state up first (a real release is
    never lost to a seed)."""
    import tempfile, os
    from orchestrator import sim as SIM
    from orchestrator import cli_common as _C
    from orchestrator.state import ReleaseState
    scenario = {"name": "t_seed", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD+1", "data": "mock",
                "target": {"phase": "build_verify", "at": "open"}}
    with tempfile.TemporaryDirectory() as tmp:
        # a pre-existing "real" state at this id
        _C.save_state(ReleaseState(release_id="2026-08", current_phase="ccd"), tmp, "2026-08")
        res = SIM.run_scenario(scenario, runs_root=tmp)
        assert res.backed_up_to and os.path.exists(res.backed_up_to)
        # backup preserved the OLD cursor; the live state now reflects the seed
        assert ReleaseState.load(res.backed_up_to).current_phase == "ccd"
        assert _C.load_state(tmp, "2026-08").current_phase == "build_verify"

