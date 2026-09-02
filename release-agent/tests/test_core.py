"""Release-agent tests — core. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




def test_auto_two_types_only():
    """Every readiness item is exactly one of two user-visible types: auto or attest."""
    st, orch = _orch(signed=False)
    chk = orch.gate.checklist()
    for it in chk["items"]:
        assert it["verify"] in ("auto", "attest")
    types = {it["id"]: it["verify"] for it in chk["items"]}
    # build_access = python-auto; oncall_now + adx_access = scout-assisted auto; the rest attest
    assert types["build_access"] == "auto"
    assert types["oncall_now"] == "auto"
    assert types["adx_access"] == "auto"
    for iid in ("play_console_access", "saw_ame", "yubikey", "oncall_window"):
        assert types[iid] == "attest"
    # scout-sourced items; build_access is python (no source)
    src = {it["id"]: it.get("source") for it in chk["items"]}
    assert src["oncall_now"] == "scout"
    assert src["adx_access"] == "scout"
    assert src["build_access"] is None
    # adx_access exposes the cluster coords for the skill to query
    adx = next(i for i in chk["items"] if i["id"] == "adx_access")
    assert adx["cluster_uri"] and adx["database"]




def test_attest_prompt_is_separate_render_never_in_table():
    """The table (render #1) NEVER contains the confirmation block — that keeps the
    table showing exactly once. The '✋ Your confirmation needed' block is a SEPARATE
    render (render.attest_prompt), which only lists items once all auto items pass."""
    from orchestrator import render
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", ccd="2026-07-08", ccd_source="default")
    orch = Orchestrator(CONFIG, st)
    # The full table never carries the confirmation block, before OR after auto checks.
    early = render.readiness_table(orch.gate.checklist(), "t")
    assert "Your confirmation needed" not in early
    # attest_prompt before auto done → not the confirmation block (auto still pending)
    ap_early = render.attest_prompt(orch.gate.checklist())
    assert "Your confirmation needed" not in ap_early
    assert "still pending" in ap_early
    # resolve all auto items
    orch.gate.verify()
    orch.gate.record_check("silent_perms", "pass", "auto-approved")
    orch.gate.record_check("oncall_now", "pass", "not on roster")
    orch.gate.record_check("adx_access", "pass", "print 1 ok")
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    # table STILL has no confirmation block (shows once)
    assert "Your confirmation needed" not in render.readiness_table(orch.gate.checklist(), "t")
    # attest_prompt now lists each outstanding attest item
    ap = render.attest_prompt(orch.gate.checklist())
    assert "Your confirmation needed" in ap
    for label in ("Play Console access", "Free during release window", "SAW + AME", "YubiKey in hand"):
        assert label in ap
    # once signed, attest_prompt reports cleared
    orch.gate.sign(["play_console_access", "oncall_window", "saw_ame", "yubikey"], note="ok")
    assert "entry gate cleared" in render.attest_prompt(orch.gate.checklist())




def test_attest_prompt_payload_is_deterministic_card():
    """render.attest_prompt_payload builds the exact m_ask_user card the engine owns:
    ready:false until auto checks pass, then a confirm_all + one decline-per-item card
    set with confirm_items — so the always-rendered Scout card is the source of truth."""
    from orchestrator import render
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default")
    orch = Orchestrator(CONFIG, st)
    # Not ready before auto checks
    p0 = render.attest_prompt_payload(orch.gate.checklist(), "2026-07")
    assert p0["ready"] is False and "pending" in p0["reason"]
    # Resolve auto items
    orch.gate.verify()
    orch.gate.record_check("silent_perms", "pass", "auto-approved")
    orch.gate.record_check("oncall_now", "pass", "not on roster")
    orch.gate.record_check("adx_access", "pass", "print 1 ok")
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    p = render.attest_prompt_payload(orch.gate.checklist(), "2026-07")
    assert p["ready"] is True
    assert p["recommendedIndex"] == 0
    # first card is confirm_all; the rest are one decline per outstanding attest item
    assert p["answers"][0]["action"] == "confirm_all"
    decline_items = {a["item"] for a in p["answers"] if a["action"] == "decline"}
    assert decline_items == {"play_console_access", "oncall_window", "saw_ame", "yubikey"}
    assert set(p["confirm_items"]) == {"play_console_access", "oncall_window", "saw_ame", "yubikey"}
    # the on-call window dates are surfaced in the confirm_all description
    assert "2026-07-01" in p["answers"][0]["description"]
    # 2-5 cards (valid m_ask_user answer count)
    assert 2 <= len(p["answers"]) <= 5
    # once signed, not ready (nothing outstanding)
    orch.gate.sign(["play_console_access", "oncall_window", "saw_ame", "yubikey"], note="ok")
    assert render.attest_prompt_payload(orch.gate.checklist(), "2026-07")["ready"] is False




def test_no_blocking_attribute():
    """The 'blocking' per-item distinction was removed — all items equally required."""
    st, orch = _orch(signed=False)
    chk = orch.gate.checklist()
    for it in chk["items"]:
        assert "blocking" not in it




def test_degraded_rejected_for_non_opt_out_item():
    """'degraded' is only valid for opt_out items — a normal scout item rejects it."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    res = orch.gate.record_check("oncall_now", "degraded", "nope")
    assert "error" in res





def test_render_is_consistent_and_has_links():
    """The canonical render must include links and both type labels (no lock marker)."""
    from orchestrator import render
    st, orch = _orch(signed=False)
    orch.gate.verify()   # populates build-def check results (with URLs)
    out = render.readiness_table(orch.gate.checklist(), "t")
    assert "[auto" in out and "[attest" in out
    assert "🔒" not in out  # lock marker removed (was confusing)
    assert "aka.ms/saw" in out and "play.google.com" in out  # attest links included
    assert "definitionId=2828" in out  # build-def check rendered as a link to the endpoint




# ---- phase flow (readiness pre-signed) ----

def test_holds_at_first_hold():
    st, orch = _orch()
    actions = orch.run_until_gate()
    assert actions[-1].kind == "reminder"
    assert actions[-1].step == "ui_failures"   # Phases 0-2 gateless (rc_report auto); first hold is Phase-3 ui_failures
    # auto steps that RUN before the first hold: Phase-0 breaking/cg/oneauth_access/cron (4) +
    # Phase-2 checker_fired/orchestrator_health/mrwp_ecs/mrwp_local/auth_ecs/telemetry_verify (6) +
    # rc_report (scout email, mocked done here) (1) + Phase-3 clone_plans_broker/clone_plans_auth/
    # ui_test_status/distribute_tests (4) + send_invite + activate_chat (scout, mocked done) (2) +
    # notify_native_auth (1) + bugbash_updates (1). native_auth_signoff now follows ui_failures, so
    # it does NOT run before the first hold.
    assert sum(1 for a in actions if a.kind == "ran") == 19




def test_approve_advances():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    orch.approve_gate("ok")
    assert st.is_done("bug_bash", "bugbash_complete")
    orch.run_until_gate()
    assert st.current_step == "gate_watch"       # next stop after bugbash_complete: the Phase-4 finalize gate
    assert st.status == "holding_gate"




def test_deny_blocks():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    orch.deny_gate("flag not approved")
    assert st.status == "blocked"
    assert any("denied" in p for p in st.pending_human)




def test_full_flow_replay_completes():
    st, orch = _orch()
    guard = 0
    while st.status != "complete" and guard < 100:
        orch.run_until_gate()
        if st.status == "holding_gate":
            orch.approve_gate("auto-approve (replay)")
        elif st.status == "awaiting_action":
            orch.complete_step(note="done (replay)")
        guard += 1
    assert st.status == "complete"
    total = sum(len(p["steps"]) for p in orch.config["phases"] if not p.get("conditional"))
    done = sum(1 for p in orch.config["phases"] if not p.get("conditional")
               for s in p["steps"] if st.is_done(p["id"], s["id"]))
    assert done == total




def test_persistence_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "s.json")
        st = ReleaseState(release_id="rt")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch)
        orch.gate.sign()
        _clear_phase0_scout(orch)
        _clear_ccd_scout(orch)
        _advance_to_first_gate(orch)
        st.save(path)
        # reload — simulates resuming next day
        st2 = ReleaseState.load(path)
        assert st2.status == "holding_gate"
        assert st2.current_step == "bugbash_complete"
        assert st2.readiness_signed  # readiness survives the roundtrip
        orch2 = Orchestrator(CONFIG, st2)
        orch2.approve_gate("resumed")
        assert st2.is_done("bug_bash", "bugbash_complete")




def test_conditional_hotfix_excluded_by_default():
    st, orch = _orch()
    guard = 0
    while st.status != "complete" and guard < 100:
        orch.run_until_gate()
        if st.status == "holding_gate":
            orch.approve_gate("ok")
        elif st.status == "awaiting_action":
            orch.complete_step(note="done")
        guard += 1
    assert not st.is_done("hotfix", "cherry")




# ---- manual overrides ----

def test_skip_requires_reason():
    st, orch = _orch()
    _advance_to_first_gate(orch)   # holds at bugbash_complete
    act = orch.skip_step("bug_bash", "bugbash_complete", "")   # no reason
    assert act.kind == "idle"
    assert not st.is_done("bug_bash", "bugbash_complete")       # unchanged




def test_reopen_step():
    st, orch = _orch()
    _advance_to_first_gate(orch); orch.approve_gate("ok")
    assert st.is_done("bug_bash", "bugbash_complete")
    orch.reopen_step("bug_bash", "bugbash_complete")
    assert not st.is_done("bug_bash", "bugbash_complete")        # back to pending
    orch.run_until_gate()
    assert st.current_step == "bugbash_complete"                 # gate re-holds




def test_halt_blocks_then_resume():
    st, orch = _orch()
    orch.halt("prod incident")
    assert st.halted and st.status == "halted"
    actions = orch.run_until_gate()
    assert actions[-1].kind == "halted"
    # nothing ran while halted (breaking is an auto step the fixture doesn't pre-clear)
    assert not st.is_done("preflight", "breaking")
    orch.resume("resolved")
    assert not st.halted
    orch.run_until_gate()
    assert st.is_done("preflight", "notice")                # advances again




def test_halt_requires_reason():
    st, orch = _orch()
    act = orch.halt("")
    assert act.kind == "idle"
    assert not st.halted




# ---- event log ----

def test_eventlog_per_release_and_captures_interaction():
    from orchestrator.eventlog import EventLog, summarize
    with tempfile.TemporaryDirectory() as tmp:
        el = EventLog(tmp, "2026-10")
        el.log("release_started", mode="real")
        el.scout_said("Readiness gate — confirm items?", kind="prompt",
                      options=["All confirmed", "I AM on-call"])
        el.user_said("All confirmed", kind="choice", choice="A")
        el.log("gate_approved", phase="preflight", step="flag_freeze",
               driver="flags reviewed with lead")
        events = el.read()
        assert len(events) == 4
        # interaction captured with sources
        srcs = {e["source"] for e in events}
        assert {"engine", "scout", "user"} <= srcs
        # per-release file only (no aggregate attribute)
        assert el.path.endswith(os.path.join("2026-10", "events.jsonl"))
        s = summarize(events)
        assert s["interactions_logged"] == 2
        assert s["gate_decisions"][0]["driver"] == "flags reviewed with lead"




def test_log_actions_records_step_outcome_and_blocks():
    """log_actions(state=...) enriches phase/step events with the recorded OUTCOME
    (status + note), and a ran step that recorded a block is logged as step_blocked —
    so the log is self-contained (queryable finding/reason), not just 'it ran'."""
    from orchestrator import cli_common as C
    from orchestrator.eventlog import EventLog
    from datetime import date
    _stub_build_defs("pass")
    with tempfile.TemporaryDirectory() as tmp:
        st = ReleaseState(release_id="2026-08", ccd="2026-08-26", ccd_source="confirmed")
        # cg blocks with a real-shaped reason; breaking/cron run clean
        mocks = _safe({"preflight.cg": {"outcome": "blocked",
                                        "reason": "High CG alert: CVE-2026-54399 httpcore5 5.3"}})
        orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 19), mocks=mocks)
        _pass_scout_checks(orch); orch.gate.sign()
        actions = orch.run_until_gate()
        el = EventLog(tmp, "2026-08")
        C.log_actions(el, actions, state=st)
        events = el.read()
        by_step = {(e.get("step")): e for e in events if e.get("event") in ("step_ran", "step_blocked")}
        # a clean agent step → step_ran WITH its finding note + done status
        assert by_step["breaking"]["event"] == "step_ran"
        assert by_step["breaking"]["status"] == "done"
        assert by_step["breaking"].get("note")            # the actual result is present
        # the blocked step → step_blocked WITH the block reason
        assert by_step["cg"]["event"] == "step_blocked"
        assert by_step["cg"]["status"] == "blocked"
        assert "CVE-2026-54399" in by_step["cg"]["note"]




def test_advance_log_summary_is_compact_no_status_table():
    """The advance journal form carries the per-action outcome lines but NOT the full
    rendered status table (that's presentation + already captured structurally), so
    events.jsonl stays lean."""
    from orchestrator import cli_common as C
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26", ccd_source="confirmed")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 19))
    _pass_scout_checks(orch); orch.gate.sign()
    actions = orch.run_until_gate()
    full = C.advance_block(actions, orch)
    compact = C.advance_log_summary(actions)
    # the full block embeds the status table; the compact log must not
    assert "### ▶ Current phase" in full
    assert "### ▶ Current phase" not in compact and "| Step | State |" not in compact
    assert len(compact) < len(full)
    # but the compact form still names what happened (the action tags)
    assert "[" in compact and "]" in compact




def test_eventlog_never_raises_on_bad_path():
    from orchestrator.eventlog import EventLog
    el = EventLog("\x00::invalid::", "x")
    el.log("release_started")   # must silently no-op, not raise
    el.scout_said("x"); el.user_said("y")




def test_eventlog_step_qa_is_captured_and_counted():
    from orchestrator.eventlog import EventLog, summarize
    with tempfile.TemporaryDirectory() as tmp:
        el = EventLog(tmp, "2026-10")
        el.qa("who fixes this alert?", "the release owner creates the fix PR",
              phase="preflight", step="cg")
        events = el.read()
        assert len(events) == 1
        e = events[0]
        assert e["event"] == "step_qa"
        assert e["question"] == "who fixes this alert?"
        assert e["answer"].startswith("the release owner")
        assert e["phase"] == "preflight" and e["step"] == "cg"
        s = summarize(events)
        assert s["questions_answered"] == 1
        assert s["interactions_logged"] == 1   # scout-sourced → counts as interaction




# ---- CCD schedule math (pure) ----

def test_second_wednesday():
    from orchestrator import schedule
    from datetime import date
    # July 2026: Wednesdays land on 1, 8, 15, 22, 29 → 2nd is the 8th.
    assert schedule.second_wednesday(2026, 7) == date(2026, 7, 8)
    # Feb 2026 starts on a Sunday → Wednesdays 4, 11 → 2nd is the 11th.
    assert schedule.second_wednesday(2026, 2) == date(2026, 2, 11)




def test_resolve_ccd_default_and_conflict():
    from orchestrator import schedule
    from datetime import date
    # canonical CCD is always the 2nd Wednesday
    assert schedule.default_ccd("2026-07") == date(2026, 7, 8)
    # no override, or override == default → no conflict
    assert schedule.pipeline_conflict("2026-07", None, "2026-07-08") is None
    assert schedule.pipeline_conflict("2026-07", "2026-07-08", "2026-07-08") is None
    # in-month override that DIFFERS from stored CCD → conflict (surface it)
    assert schedule.pipeline_conflict("2026-07", "2026-07-09", "2026-07-08") == date(2026, 7, 9)
    # cross-month override → ignored (month-scoped)
    assert schedule.pipeline_conflict("2026-07", "2026-06-30", "2026-07-08") is None
    # against the default when nothing stored yet (init path)
    assert schedule.pipeline_conflict("2026-07", "2026-07-09", None) == date(2026, 7, 9)




def test_anchor_offset_and_date():
    from orchestrator import schedule
    from datetime import date
    assert schedule.anchor_offset("CCD-7") == -7
    assert schedule.anchor_offset("CCD+1") == 1
    assert schedule.anchor_offset("CCD") == 0
    assert schedule.anchor_date(date(2026, 7, 8), "CCD-7") == date(2026, 7, 1)




def test_phase0_opens_on_ccd_minus_7():
    st, orch = _ccd_orch("2026-07-01")     # window opens exactly today
    _clear_early_phase0_scout(orch)        # clear notice + flight_reminder, hold at confirm_reminders
    actions = orch.run_until_gate()
    # Phase 0 is open and running: holding at the confirm-reminders attestation step
    assert st.status == "awaiting_action"
    assert st.current_step == "confirm_reminders"
    assert st.is_done("preflight", "notice")




def test_only_frontier_phase_shows_current_despite_stale_downstream_progress():
    """Issue-B regression: exactly ONE phase (the frontier = first incomplete) renders as
    'current'. A LATER phase left with stale partial progress (e.g. after reopening an
    upstream phase) must NOT also show as 'current' — it's 'pending'."""
    from orchestrator.state import StepState
    st, orch = _mock_orch({}, as_of="2026-07-09")
    # Phases 0-1 done; build_verify (Phase 2) incomplete = the frontier; bug_bash (Phase 3)
    # carries stale progress (2 steps done) as if an upstream reopen rolled Phase 2 back.
    for pid in ("preflight", "ccd"):
        for s in next(p for p in orch.config["phases"] if p["id"] == pid)["steps"]:
            orch.state.set_step(pid, s["id"], StepState(status="done", by="test"))
    for sid in ("clone_plans_broker", "bugbash_updates"):
        orch.state.set_step("bug_bash", sid, StepState(status="done", by="test"))
    phases = {p["id"]: p for p in orch.status_report()["phases"]}
    assert phases["build_verify"]["state"] == "current" and phases["build_verify"]["current"]
    assert phases["bug_bash"]["state"] == "pending"        # stale progress ≠ a 2nd current
    assert phases["bug_bash"]["done"] == 2                 # the leftover count still shows
    assert not phases["bug_bash"]["current"]
    # exactly one phase is current
    assert sum(1 for p in phases.values() if p["state"] == "current") == 1




def test_no_anchor_when_ccd_unknown_runs_immediately():
    """Backward-compatible: with no CCD stored, the anchor is inert and Phase 0 runs."""
    st, orch = _orch()   # ccd is None
    orch.run_until_gate()
    assert st.status == "awaiting_action"   # reached the first hold (Phase-3 ui_failures), not 'scheduled'




# ---- reminder steps (human, non-gate → hold until done) ----

def test_reminder_holds_until_done():
    """The bug-bash phase has 'ui_failures' (human, non-gate) → the engine must
    HOLD for the person, not silently auto-complete it."""
    st, orch = _orch()
    # drive through the gates until we hit the first reminder hold
    guard = 0
    while st.status not in ("awaiting_action", "complete") and guard < 100:
        orch.run_until_gate()
        if st.status == "holding_gate":
            orch.approve_gate("ok")
        guard += 1
    assert st.status == "awaiting_action"
    assert st.current_step == "ui_failures"
    assert not st.is_done("bug_bash", "ui_failures")
    assert "bug_bash.ui_failures" in st.pending_human
    # re-running does not advance past a reminder
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    # marking it done clears the hold and advances
    orch.complete_step(note="triaged")
    assert st.is_done("bug_bash", "ui_failures")
    assert "bug_bash.ui_failures" not in st.pending_human




def test_owner_stored_and_in_status():
    st = ReleaseState(release_id="2026-08", ccd="2026-08-12",
                      ccd_source="default", owner_email="pedroro@microsoft.com",
                      owner_name="Pedro Romero Vargas")
    orch = Orchestrator(CONFIG, st)
    rpt = orch.status_report()
    assert rpt["owner_email"] == "pedroro@microsoft.com"
    assert rpt["owner_name"] == "Pedro Romero Vargas"
    from orchestrator import render
    assert "pedroro@microsoft.com" in render.status_view(rpt)




# ---- infra preflight (CLIs + MCP registration into Scout config) ----

def test_infra_kusto_known_services_multicluster():
    """A kusto-style entry with known_services_from builds --known-services from
    the data list (so adding clusters is data-only, no code change)."""
    from orchestrator import infra
    import json, sys as _sys
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "m-mcp-servers.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"servers": {}}, fh)
        orig = infra.scout_mcp_config_path
        infra.scout_mcp_config_path = lambda: cfg
        try:
            req = {
                "mcp_servers": [{
                    "id": "kusto", "name": "Kusto MCP", "scout_key": "kusto",
                    "provider": f'"{_sys.executable}" --version',
                    "command": _sys.executable, "args": ["mcp", "kusto"],
                    "known_services_from": "kusto_clusters",
                }],
                "kusto_clusters": [
                    {"service_uri": "https://a.kusto.windows.net", "default_database": "db1", "description": "A"},
                    {"service_uri": "https://b.kusto.windows.net", "default_database": "db2", "description": "B"},
                ],
            }
            infra.ensure_mcp_servers(req, register=True)
            args = json.load(open(cfg, encoding="utf-8"))["servers"]["kusto"]["config"]["args"]
            assert "--known-services" in args
            known = json.loads(args[args.index("--known-services") + 1])
            assert [k["service_uri"] for k in known] == [
                "https://a.kusto.windows.net", "https://b.kusto.windows.net"]
        finally:
            infra.scout_mcp_config_path = orig




def test_infra_scout_missing_blocks_registration(monkeypatch=None):
    """When Scout isn't installed, infra.run reports scout_missing and does NOT
    register MCP servers (there's no config to write to)."""
    from orchestrator import infra
    import os as _os
    orig = infra.os.path.isdir
    # force ~/.scout to look absent
    infra.os.path.isdir = lambda p: False if p.endswith(".scout") else orig(p)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            reqp = os.path.join(tmp, "requirements.yaml")
            with open(reqp, "w", encoding="utf-8") as fh:
                fh.write("requirements: []\n"
                         "mcp_servers:\n"
                         "  - id: icm\n    name: ICM\n    scout_key: icm\n"
                         "    provider: 'python --version'\n    command: python\n    args: []\n")
            rep = infra.run(reqp, register=True)
            assert rep["scout_present"] is False
            assert rep["ok"] is False
            assert rep["mcp_servers"][0]["status"] == "scout_missing"
    finally:
        infra.os.path.isdir = orig




def test_infra_registers_missing_mcp():
    """infra.ensure_mcp_servers adds a missing server (whose launcher exists) into
    a Scout config, backing it up, reports 'registered'; a second pass is 'present'."""
    from orchestrator import infra
    import json, sys as _sys
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "m-mcp-servers.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"servers": {}}, fh)
        orig = infra.scout_mcp_config_path
        infra.scout_mcp_config_path = lambda: cfg
        try:
            req = {"mcp_servers": [{
                "id": "demo", "name": "Demo MCP", "scout_key": "demo",
                "provider": f'"{_sys.executable}" --version',
                "command": _sys.executable, "args": ["-m", "demo"],
            }]}
            assert infra.ensure_mcp_servers(req, register=False)[0]["status"] == "would_register"
            r1 = infra.ensure_mcp_servers(req, register=True)
            assert r1[0]["status"] == "registered"
            d = json.load(open(cfg, encoding="utf-8"))
            assert "demo" in d["servers"]
            assert d["servers"]["demo"]["config"]["command"] == _sys.executable
            assert any(f.startswith("m-mcp-servers.json.bak-") for f in os.listdir(tmp))
            assert infra.ensure_mcp_servers(req, register=True)[0]["status"] == "present"
        finally:
            infra.scout_mcp_config_path = orig




def test_infra_registers_declared_tools_allowlist():
    """A server declaring a `tools` list is registered WITH it (Scout drops
    command-based servers whose allowlist is empty); no `tools` → []."""
    from orchestrator import infra
    import json, sys as _sys
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "m-mcp-servers.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"servers": {}}, fh)
        orig = infra.scout_mcp_config_path
        infra.scout_mcp_config_path = lambda: cfg
        try:
            req = {"mcp_servers": [
                {"id": "teams", "name": "Teams MCP", "scout_key": "teams",
                 "provider": f'"{_sys.executable}" --version',
                 "command": _sys.executable, "args": ["-m", "teams"],
                 "tools": ["CreateChat", "AddChatMember", "SendMessageToChat"]},
                {"id": "bare", "name": "Bare MCP", "scout_key": "bare",
                 "provider": f'"{_sys.executable}" --version',
                 "command": _sys.executable, "args": ["-m", "bare"]},
            ]}
            infra.ensure_mcp_servers(req, register=True)
            d = json.load(open(cfg, encoding="utf-8"))
            assert d["servers"]["teams"]["tools"] == ["CreateChat", "AddChatMember", "SendMessageToChat"]
            assert d["servers"]["bare"]["tools"] == []      # no declaration → empty
        finally:
            infra.scout_mcp_config_path = orig




def test_requirements_declares_teams_mcp_with_tools():
    """The repo requirements.yaml ships the Teams MCP with a populated tools
    allowlist, so the entry-gate mcp_servers check includes it and infra registers
    it correctly (empty allowlist would make Scout drop it)."""
    from orchestrator import infra
    root = os.path.dirname(os.path.abspath(__file__))
    req = infra.load_requirements(os.path.join(os.path.dirname(root), "config", "requirements.yaml"))
    teams = next((m for m in req.get("mcp_servers", []) if m.get("scout_key") == "teams"), None)
    assert teams is not None, "Teams MCP missing from requirements.yaml"
    assert "CreateChat" in teams.get("tools", []) and len(teams["tools"]) >= 30




def test_all_command_mcps_ship_nonempty_tool_allowlists():
    """GUARDRAIL: every command-based MCP in requirements.yaml must ship a NON-EMPTY
    `tools` allowlist — an empty list risks Scout silently dropping the server on a
    first-load hiccup. (Builtins auto-discover and are exempt, but requirements.yaml
    only lists command servers.)"""
    from orchestrator import infra
    root = os.path.dirname(os.path.abspath(__file__))
    req = infra.load_requirements(os.path.join(os.path.dirname(root), "config", "requirements.yaml"))
    for m in req.get("mcp_servers", []):
        tools = m.get("tools") or []
        assert len(tools) >= 1, f"MCP '{m.get('scout_key')}' has an empty tools allowlist"
    # spot-check the ones we care about
    by = {m["scout_key"]: m for m in req["mcp_servers"]}
    assert "get_on_call_schedule_by_team_id" in by["icm"]["tools"]   # readiness.oncall_now
    assert "kusto_query" in by["kusto"]["tools"]                     # readiness.adx_access




def test_infra_provider_missing_not_registered():
    """If the launcher/provider is absent, infra must NOT register a broken server."""
    from orchestrator import infra
    import json
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "m-mcp-servers.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"servers": {}}, fh)
        orig = infra.scout_mcp_config_path
        infra.scout_mcp_config_path = lambda: cfg
        try:
            req = {"mcp_servers": [{
                "id": "ghost", "name": "Ghost MCP", "scout_key": "ghost",
                "provider": "nonexistent-launcher-xyz --version",
                "command": os.path.join(tmp, "nope.exe"), "args": [],
            }]}
            r = infra.ensure_mcp_servers(req, register=True)
            assert r[0]["status"] in ("provider_missing", "launcher_missing")
            assert "ghost" not in json.load(open(cfg, encoding="utf-8"))["servers"]
        finally:
            infra.scout_mcp_config_path = orig




# ---- automation registry (track provisioned automations for teardown) ----

def test_registry_register_list_deregister():
    from orchestrator.registry import AutomationRegistry
    with tempfile.TemporaryDirectory() as tmp:
        reg = AutomationRegistry(tmp)
        reg.register("a1", "Release push reminders", shared=True, purpose="push")
        reg.register("a2", "Phase-3 watcher", release="2026-08", purpose="bug bash")
        # shared entry stores release=None
        shared = reg.list(scope="shared")
        assert len(shared) == 1 and shared[0]["release"] is None
        # release-scoped listing excludes the shared one
        rel = reg.list(release="2026-08")
        assert [e["id"] for e in rel] == ["a2"]
        # upsert by id (no duplicates)
        reg.register("a2", "Phase-3 watcher v2", release="2026-08")
        rel = reg.list(release="2026-08")
        assert len(rel) == 1 and rel[0]["name"] == "Phase-3 watcher v2"
        # deregister
        assert reg.deregister("a2") is True
        assert reg.list(release="2026-08") == []
        assert reg.deregister("nope") is False
        # shared one still there
        assert len(reg.list()) == 1




def test_registry_records_step_linkage_and_reverse_lookup():
    """An automation entry records the steps it drives + its kind; list(step=...) is
    the reverse lookup (which automation owns a step) — the traceability link."""
    from orchestrator.registry import AutomationRegistry, kind_of
    with tempfile.TemporaryDirectory() as tmp:
        reg = AutomationRegistry(tmp)
        reg.register("m", "CCD morning", release="2026-09", purpose="reminders",
                     steps=["ccd.final_reminder", "ccd.pr_reminder"])
        reg.register("n", "CCD noon", release="2026-09", purpose="loc",
                     steps=["ccd.localization"])
        # forward: automation -> steps
        m = reg.list(release="2026-09", step="ccd.final_reminder")[0]
        assert m["steps"] == ["ccd.final_reminder", "ccd.pr_reminder"]
        # reverse: step -> automation
        owners = reg.list(step="ccd.localization")
        assert [e["id"] for e in owners] == ["n"]
        assert reg.list(step="ccd.pr_reminder")[0]["id"] == "m"
        # a step nobody drives → empty
        assert reg.list(step="build_verify.go_test") == []
        # steps present → kind auto-derives to step-driving
        assert kind_of(m) == "step-driving"




def test_registry_kind_taxonomy_and_guard():
    """kind is 'step-driving' when steps are present, 'release-level' when not; the
    two can't contradict. Old entries without the field derive their kind."""
    from orchestrator.registry import AutomationRegistry, kind_of
    with tempfile.TemporaryDirectory() as tmp:
        reg = AutomationRegistry(tmp)
        # no steps → release-level (e.g. the hourly push-reminder / tick automation)
        pr = reg.register("pr", "Release push reminders", release="2026-09",
                          purpose="hourly advance + digest")
        assert pr["kind"] == "release-level" and pr["steps"] == []
        assert reg.list(kind="release-level")[0]["id"] == "pr"
        # steps → step-driving
        m = reg.register("m", "CCD morning", release="2026-09", steps=["ccd.final_reminder"])
        assert m["kind"] == "step-driving"
        assert reg.list(kind="step-driving")[0]["id"] == "m"
        # contradictions are rejected
        try:
            reg.register("bad", "Bad", release="2026-09", kind="step-driving")
            assert False, "expected ValueError for step-driving with no steps"
        except ValueError:
            pass
        try:
            reg.register("bad2", "Bad2", release="2026-09", steps=["ccd.x"], kind="release-level")
            assert False, "expected ValueError for release-level with steps"
        except ValueError:
            pass
        # derivation for a legacy entry that predates the kind field
        assert kind_of({"steps": ["a.b"]}) == "step-driving"
        assert kind_of({"steps": []}) == "release-level"
        assert kind_of({}) == "release-level"




# ---- inter-process state lock (parallel CLI mutation safety) ----

def test_state_lock_is_exclusive_then_releases():
    """While a release's state lock is held, a second acquisition blocks (times
    out); once released it can be acquired again."""
    import threading
    with tempfile.TemporaryDirectory() as rr:
        R = "2099-02"
        os.makedirs(os.path.join(rr, R))
        acquired, timed_out = [], []
        with C.state_lock(rr, R):
            orig = C._LOCK_TIMEOUT
            C._LOCK_TIMEOUT = 0.3
            def try_acquire():
                try:
                    with C.state_lock(rr, R):
                        acquired.append(True)
                except TimeoutError:
                    timed_out.append(True)
            t = threading.Thread(target=try_acquire)
            t.start(); t.join()
            C._LOCK_TIMEOUT = orig
            assert timed_out and not acquired      # blocked while held
        with C.state_lock(rr, R):                  # released -> acquirable
            acquired.append("after")
        assert "after" in acquired




def test_failing_agent_holds_as_action_needed():
    """A pre-flight agent that returns ok=False must HOLD the release as
    awaiting_action (not silently mark the step done)."""
    st, orch = _mock_orch({"preflight.breaking": {"outcome": "blocked", "reason": "boom"}},
                          as_of="2026-07-08")
    _clear_phase0_scout(orch)
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    assert "preflight.breaking" in st.pending_human
    # human resolves + marks done -> flow resumes
    orch.complete_step("preflight", "breaking", "handled")
    assert st.status == "running"




# ---- notification channels (email + Teams) ----

def test_notifications_config_defaults_and_target():
    """Missing file → email only, Teams off. Default target is the Scout bot; an
    explicit chat id passes through."""
    from orchestrator import notifications as N
    cfg = N.load_config("no/such/phases.yaml")     # file absent → defaults
    assert cfg["channels"] == {"email": True, "teams": False}
    # Scout-bot delivery (default + aliases)
    for t in ("scout", "self", "me", "owner", "bot"):
        d = N.teams_delivery({"channels": {"teams": True}, "teams": {"target": t}}, "<b>x</b>", "x")
        assert d["via"] == "scout_bot" and d["text"] == "x"
    # explicit chat id → workiq html chat block
    d2 = N.teams_delivery({"channels": {"teams": True}, "teams": {"target": "19:abc@thread.v2"}},
                          "<b>hi</b>", "hi")
    assert d2["via"] == "chat" and d2["chatId"] == "19:abc@thread.v2"
    assert d2["contentType"] == "html" and "hi" in d2["content"]
    # teams off → no delivery
    assert N.teams_delivery({"channels": {"teams": False}, "teams": {"target": "scout"}}, "h", "m") is None




def test_notifications_repo_config_targets_scout_bot():
    """The repo's config/notifications.yaml enables Teams and targets the Scout bot."""
    from orchestrator import notifications as N
    cfg = N.load_config(CONFIG)                     # real config/ dir
    assert cfg["channels"]["email"] is True
    assert cfg["channels"]["teams"] is True
    assert N.teams_target(cfg) == "scout"




def test_notification_html_lists_all_tasks_and_flags_attention():
    """The HTML digest shows every step with a status pill and flags the hold."""
    from orchestrator import render
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", ccd="2026-08-12",
                      ccd_source="default", owner_email="o@x.com")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 5))
    _pass_scout_checks(orch)
    orch.gate.sign()
    _drain_phase0_scout_only(orch)        # Scout's own steps done; hold at confirm_reminders
    orch.run_until_gate()
    html = render.notification_html(orch.status_report())
    assert html.startswith("<div") and "Release 2026-08" in html
    # every Phase-0 step name appears in the table
    for name in ("Send early release notice", "Detect lockdown/holiday overlap",
                 "Confirm Play Console vitals"):
        assert name in html
    # the hold is flagged for attention + a done pill exists
    assert "Needs your attention" in html and "Needs you now" in html
    assert "✓ Done" in html




def test_notification_html_silent_when_plain_is_silent():
    from orchestrator import render
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", ccd="2026-08-12",
                      ccd_source="default")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 6))  # unsigned → silent
    assert render.notification_html(orch.status_report()) == ""




# ---- Phase 2 (build_verify) — RC pipeline verification ----

def test_stage_completion_rule():
    """The core Phase-2 rule: a run 'ran to completion' iff every stage executed
    (state completed AND result succeeded/succeededWithIssues/failed). Red/yellow are
    OK; skipped/canceled/pending = never-ran = block."""
    from tools import pipelines as P
    healthy = [
        {"name": "A", "state": "completed", "result": "succeeded"},
        {"name": "B", "state": "completed", "result": "succeededWithIssues"},
        {"name": "C", "state": "completed", "result": "failed"},           # red is fine
    ]
    c = P.stage_completion(healthy)
    assert c["complete"] and c["ran"] == 3 and c["never_ran"] == []
    assert c["failed"] == ["C"] and c["yellow"] == ["B"]
    # skipped / canceled / pending → never-ran → NOT complete
    for bad in ("skipped", "canceled"):
        s = healthy + [{"name": "X", "state": "completed", "result": bad}]
        cc = P.stage_completion(s)
        assert not cc["complete"] and "X" in cc["never_ran"]
    pend = healthy + [{"name": "Y", "state": "pending", "result": None}]
    assert not P.stage_completion(pend)["complete"]
    # empty timeline is not "complete" (nothing ran)
    assert not P.stage_completion([])["complete"]




def test_active_phase_report_steps_carry_links():
    """The digest's active-phase step model exposes each step's durable `links` so the
    links to items a step evaluated aren't dropped from the phase report."""
    st, orch = _mock_orch({})
    ap = orch.status_report()["active_phase"]
    assert ap and all("links" in s for s in ap["steps"])




def test_agent_steps_render_as_automatic_not_pending():
    """UX contract: engine-run agent steps ('auto') read IDENTICALLY to skill-run scout
    steps — 'Scout runs this — automatic', 🤖 — so a human never sees a confusing
    'Pending' next to 'automatic' for work that needs no action. 'Pending' stays distinct
    (a human step still to come)."""
    from orchestrator import render
    assert render._STEP_STATE_WORD["auto"] == render._STEP_STATE_WORD["scout"] == \
        "Scout runs this — automatic"
    assert render._STEP_ICON["auto"] == render._STEP_ICON["scout"] == "🤖"
    assert render._STEP_STATE_WORD["pending"] != render._STEP_STATE_WORD["auto"]

    # And the classifier tags the (pending, engine-run) build_verify agent steps as 'auto',
    # the scout email as 'scout', never 'pending'. Force Phases 0-1 done so build_verify is
    # the current phase with its steps still pending.
    from orchestrator.state import StepState
    st, orch = _mock_orch({}, as_of="2026-07-09")     # build_verify anchor CCD+1 → due
    for pid in ("preflight", "ccd"):
        ph = next(p for p in orch.config["phases"] if p["id"] == pid)
        for s in ph["steps"]:
            orch.state.set_step(pid, s["id"], StepState(status="done", by="test"))
    orch.state.current_phase = "build_verify"          # the engine sets this during `next`
    steps = {s["id"]: s for s in orch.status_report()["current_steps"]}
    for sid in ("checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local"):
        assert steps[sid]["state"] == "auto", (sid, steps[sid]["state"])
    assert steps["rc_report"]["state"] == "scout"




def test_get_failed_tests_drops_fully_recovered_unit_suite():
    """A unit suite whose only failure recovered on retry produces NO failing suite."""
    from tools import pipelines as P
    runs = {"value": [{"id": 701, "name": "broker4j_UnitTests # 700_build.1",
                       "totalTests": 5, "passedTests": 4, "notApplicableTests": 0}]}
    results = {"value": [
        {"testCaseTitle": "flaky", "outcome": "Failed"},
        {"testCaseTitle": "flaky", "outcome": "Passed"},
    ]}
    orig = P._ado_rest_get
    P._ado_rest_get = lambda url, timeout: (True, runs if "buildUri" in url else results, "")
    try:
        ok, suites, _ = P.get_failed_tests("O", "P", 701)
        assert ok and suites == []          # the only failure recovered → no failing suite
    finally:
        P._ado_rest_get = orig




def test_ui_case_id_from_result_extraction():
    from tools import pipelines as P
    assert P._ui_case_id_from_result({"automatedTestName": "test_3522687_WpjWithHardwareKey"}) == 3522687
    assert P._ui_case_id_from_result({"testCaseTitle": "test_833561_WPJ_Install"}) == 833561
    assert P._ui_case_id_from_result({"automatedTestStorage": "com.x.TestCase1592465"}) == 1592465
    assert P._ui_case_id_from_result({"automatedTestName": "no_case_here"}) is None




def test_eligible_testers_removes_owner_oce_and_excluded():
    from tools import distribution as D
    roster = ["a@x", "jialh@microsoft.com", "b@x", "owner@x", "oce@x", "c@x"]
    elig = D.eligible_testers(roster, ["jialh@microsoft.com"], owner="OWNER@x", oce="oce@X")
    assert elig == ["a@x", "b@x", "c@x"]        # case-insensitive owner/oce/excluded removal




def test_build_broker_plan_makes_three_flat_suites_by_reference():
    """build_broker_plan: create plan → pin root configs → THREE FLAT suites, all referenced:
    Manual Broker (static + cases), Native Auth (single dynamic tag-query suite), UI Automation
    (static + all distinct cases). CRITICAL: the classic duplicating Test Suite Clone
    (`/cloneoperation`) is NEVER called; configs are pinned via the classic /test/ PATCH."""
    from tools import pipelines as P
    from tools import testplans as T
    from tools import distribution as D

    NA_ROOT = T.BROKER_NATIVE_AUTH_ROOT_SUITE
    NA_DYN = 30351340                                  # dynamic descendant (tag query)
    UI_ROOT = T.BROKER_UI_ROOT_SUITE                   # static, explicit [292,294]
    MROOT = T.BROKER_MASTER_ROOT_SUITE

    SRC = {
        MROOT: {"id": MROOT, "name": "master", "suiteType": "staticTestSuite",
                "inheritDefaultConfigurations": False, "defaultConfigurations": [{"id": 293}]},
        NA_ROOT: {"id": NA_ROOT, "name": T.BROKER_NATIVE_AUTH_SUITE_NAME,
                  "suiteType": "staticTestSuite", "inheritDefaultConfigurations": True,
                  "defaultConfigurations": []},
        NA_DYN: {"id": NA_DYN, "name": "Native Auth Test Manual", "suiteType": "dynamicTestSuite",
                 "inheritDefaultConfigurations": True, "defaultConfigurations": [],
                 "queryString": "SELECT x WHERE tag='native'"},
        UI_ROOT: {"id": UI_ROOT, "name": T.BROKER_UI_SUITE_NAME, "suiteType": "staticTestSuite",
                  "inheritDefaultConfigurations": False,
                  "defaultConfigurations": [{"id": 292}, {"id": 294}]},
    }
    FLAT = [
        {"id": NA_ROOT, "name": SRC[NA_ROOT]["name"], "parentSuite": {"id": MROOT}},
        {"id": NA_DYN, "name": SRC[NA_DYN]["name"], "parentSuite": {"id": NA_ROOT}},
        {"id": UI_ROOT, "name": SRC[UI_ROOT]["name"], "parentSuite": {"id": MROOT}},
    ]
    sends, new_id = [], {"n": 9100}

    def fake_send(url, method, body, timeout):
        sends.append((url, method, body))
        if url.endswith(f"plans?{T._API}"):
            return (True, {"id": 9000, "rootSuite": {"id": 9001}}, "")
        if "/suites?" in url and method == "POST":                # any suite create
            new_id["n"] += 1
            return (True, {"id": new_id["n"]}, "")
        if "/TestCase?" in url and method == "POST":
            return (True, {"value": body}, "")
        if "/test/Plans/" in url and method == "PATCH":           # classic config pin
            return (True, {}, "")
        return (True, {}, "")

    def fake_get(url, timeout):                                   # _suite_full
        import re
        m = re.search(r"/suites/(\d+)\?", url)
        if m:
            return (True, SRC.get(int(m.group(1)), {}), "")
        return (True, {}, "")

    def fake_get_all(url, timeout, **k):
        if f"/Plans/{T.BROKER_MASTER_PLAN}/suites?" in url:       # _fetch_source_suites
            return (True, list(FLAT), "")
        return (True, [], "")

    o = (P._ado_rest_send, P._ado_rest_get, P._ado_rest_get_all, D.broker_manual_cases)
    P._ado_rest_send, P._ado_rest_get, P._ado_rest_get_all = fake_send, fake_get, fake_get_all
    D.broker_manual_cases = lambda *a, **k: (True, [{"id": "111", "assignee": "a@x"}], "")
    try:
        ok, pid, d = T.build_broker_plan("TEST plan")
    finally:
        (P._ado_rest_send, P._ado_rest_get, P._ado_rest_get_all, D.broker_manual_cases) = o
    assert ok and pid == 9000 and d == "", d

    # SAFETY: the duplicating classic Test Suite Clone is NEVER used
    assert not any("/cloneoperation" in u for (u, m, b) in sends)
    # root configs pinned to master root's [293] via the classic /test/ PATCH
    assert any("/test/Plans/9000/suites/9001?" in u and m == "PATCH"
               and [c["id"] for c in b["defaultConfigurations"]] == [293]
               for (u, m, b) in sends)
    created = [b for (u, m, b) in sends if "/suites?" in u and m == "POST"]
    names = [b.get("name") for b in created]
    # flat Manual Broker suite (configs pinned separately, not in the create body)
    broker = next(b for b in created if b.get("name") == T.BROKER_MANUAL_SUITE_NAME)
    assert "defaultConfigurations" not in broker
    assert any("/test/Plans/9000/suites/" in u and m == "PATCH"
               and [c["id"] for c in b["defaultConfigurations"]] == T.BROKER_CONFIGS
               for (u, m, b) in sends)
    # Native Auth = a single FLAT dynamic (tag-query) suite; UI Automation = a FLAT static suite.
    na = next(b for b in created if b.get("name") == T.BROKER_NATIVE_AUTH_SUITE_NAME)
    assert na.get("suiteType") == "dynamicTestSuite" and na.get("queryString")
    ui = next(b for b in created if b.get("name") == T.BROKER_UI_SUITE_NAME)
    assert ui.get("suiteType", "staticTestSuite") == "staticTestSuite"
    # UI Automation pinned to the full ECS + LocalFlight matrix (must include LocalFlight configs)
    assert any("/test/Plans/9000/suites/" in u and m == "PATCH"
               and [c["id"] for c in b["defaultConfigurations"]] == T.BROKER_UI_CONFIGS
               for (u, m, b) in sends)
    # exactly one dynamic suite (Native Auth) — no nested UI subtree replication
    assert [b.get("name") for b in created if b.get("suiteType") == "dynamicTestSuite"] \
           == [T.BROKER_NATIVE_AUTH_SUITE_NAME]




def test_build_broker_plan_cleans_up_on_case_failure():
    """If adding the Broker cases fails after the plan is created, the half-built plan is
    DELETEd so a re-run starts clean, and the function returns (False, ...)."""
    from tools import pipelines as P
    from tools import testplans as T
    from tools import distribution as D
    calls = []

    def fake_send(url, method, body, timeout):
        calls.append((method, url))
        if url.endswith(f"plans?{T._API}"):
            return (True, {"id": 9200, "rootSuite": {"id": 9201}}, "")
        if "/suites?" in url:
            return (True, {"id": 9202}, "")
        if "/TestCase?" in url:
            return (False, None, "HTTP 400: bad point assignment")
        return (True, {}, "")

    # root-config lookup: return an inheriting master root so no config-pin is attempted
    def fake_get(url, timeout):
        return (True, {"inheritDefaultConfigurations": True}, "")

    o = (P._ado_rest_send, P._ado_rest_get, D.broker_manual_cases)
    P._ado_rest_send, P._ado_rest_get = fake_send, fake_get
    D.broker_manual_cases = lambda *a, **k: (True, [{"id": "111", "assignee": "a@x"}], "")
    try:
        ok, pid, d = T.build_broker_plan("TEST plan")
    finally:
        P._ado_rest_send, P._ado_rest_get, D.broker_manual_cases = o
    assert not ok and "400" in d
    assert any(m == "DELETE" and "/plans/9200" in u for (m, u) in calls)   # cleaned up




def test_ado_rest_get_all_follows_header_continuation_token():
    """The pager concatenates every page, following the ADO `x-ms-continuationtoken`
    RESPONSE HEADER (not a body field) — the bug the live clone_plans_auth test caught,
    where only the first page was scanned so a same-named suite was missed → duplicate."""
    from tools import pipelines as P
    pages = [
        (True, {"value": [{"id": 1}, {"id": 2}]}, {"x-ms-continuationtoken": "p2"}, ""),
        (True, {"value": [{"id": 3}]}, {}, ""),   # no token → last page
    ]
    calls = []

    def fake_get_h(url, timeout):
        calls.append(url)
        return pages[len(calls) - 1]

    orig = P._ado_rest_get_h
    P._ado_rest_get_h = fake_get_h
    try:
        ok, items, _ = P._ado_rest_get_all("https://x/_apis/y?api-version=7.1", 30)
    finally:
        P._ado_rest_get_h = orig
    assert ok and [i["id"] for i in items] == [1, 2, 3]
    assert "continuationToken=p2" in calls[1]     # 2nd request carried the header token




def test_due_ness_uses_owner_timezone_not_host():
    """The bug: a UTC host rolled the date at UTC-midnight, opening phases the evening
    before (PT). An instant that is Aug 20 in UTC but still Aug 19 in PT must resolve to
    as_of=Aug 19, so a CCD=Aug 20 phase is NOT yet due."""
    from datetime import datetime, timezone as _utc
    from orchestrator import schedule
    utc_instant = datetime(2026, 8, 20, 3, 0, tzinfo=_utc.utc)   # = 2026-08-19 20:00 PDT
    now_pt = utc_instant.astimezone(schedule.get_tz())
    st = ReleaseState(release_id="2026-08", ccd="2026-08-20", ccd_source="manual")
    orch = Orchestrator(CONFIG, st, now=now_pt, mocks={})
    assert orch.as_of.isoformat() == "2026-08-19"               # PT date, not UTC Aug 20
    ccd_phase = next(p for p in orch.config["phases"] if p["id"] == "ccd")
    assert orch._phase_due(ccd_phase) is False                  # not due on PT Aug 19




def test_state_timezone_overrides_config_in_engine():
    """The engine prefers the timezone captured on the release (state.timezone) over the
    config default — so a headless run in a UTC process still uses the owner's zone."""
    from datetime import datetime, timezone as _utc
    st = ReleaseState(release_id="2026-08", ccd="2026-08-20", timezone="America/New_York")
    # 2026-08-20T02:00Z = 2026-08-19 22:00 EDT (still Aug 19 in New York too)
    now_pt = datetime(2026, 8, 20, 2, 0, tzinfo=_utc.utc)
    orch = Orchestrator(CONFIG, st, now=now_pt, mocks={})
    # tz resolved from state → New York; as_of derived from the passed now stays Aug 20 UTC.date? 
    # (now is explicit here; the point is self.tz came from state.timezone)
    assert str(orch.tz) == "America/New_York"




def test_init_captures_owner_timezone():
    """`init` auto-detects and persists the owner's IANA timezone (and --timezone overrides)."""
    import tempfile, argparse
    from orchestrator.commands import release as R
    from orchestrator import cli_common as _C
    _stub_build_defs("pass")
    with tempfile.TemporaryDirectory() as tmp:
        ns = argparse.Namespace(runs_root=tmp, release="2026-09", force=False,
                                owner_email="dev@x.com", owner_name=None,
                                timezone="America/Chicago", config=CONFIG)
        R.cmd_init(ns)
        st = _C.load_state(tmp, "2026-09")
        assert st.timezone == "America/Chicago"
        # auto-detect path (no --timezone): stores whatever the machine reports (non-None here)
        ns2 = argparse.Namespace(runs_root=tmp, release="2026-10", force=False,
                                 owner_email="dev@x.com", owner_name=None,
                                 timezone=None, config=CONFIG)
        R.cmd_init(ns2)
        st2 = _C.load_state(tmp, "2026-10")
        assert st2.timezone is not None                # detected from this machine




def test_scout_pending_exposed_and_action_is_not_scout():
    """REGRESSION GUARD: pending scout steps are surfaced in `scout_pending` for the
    skill to run, and are NEVER the `action` (user-hold) cue nor announced as an
    'Action needed now' in the digest — that's what made scout steps silently stall."""
    from orchestrator import render
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch); orch.gate.sign()
    orch.run_until_gate()
    rep = orch.status_report()
    assert set(rep["scout_pending"]) == {"notice", "flight_reminder", "lockdown"}
    # action (if any) is a genuine user hold, never a scout step
    if rep["action"]:
        assert rep["action"]["step"] not in ("notice", "flight_reminder", "lockdown")
    # the email digest must NOT tell the user to do a scout step
    msg = render.notification(rep)
    assert "Send early release notice" not in msg
    assert "Action needed now: Send feature-owner" not in msg




def test_current_steps_label_scout_not_user_action():
    """REGRESSION: a scout step that is the current cursor under awaiting_action must
    render as 'Scout runs this' — NOT 'Do this — then mark done' (a human reminder).
    All three Phase-1 (ccd) steps are scout, so none should look like a user to-do."""
    from datetime import date
    from orchestrator import render
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26", ccd_source="default")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 26))   # CCD day → Phase 1 due
    _pass_scout_checks(orch); orch.gate.sign()
    _clear_phase0_scout(orch)                                   # finish Phase 0 → into ccd
    orch.run_until_gate()
    r = orch.status_report()
    assert r["current_phase"] == "ccd"
    # every current step is a scout step → state 'scout', not 'reminder'
    for s in r["current_steps"]:
        assert s["state"] == "scout", f"{s['id']} state={s['state']}"
        assert s["reminder"] is False
    # and it's never surfaced as a user action
    assert r["action"] is None
    assert set(r["scout_pending"]) == {"final_reminder", "pr_reminder", "localization"}
    # the rendered current-phase table says 'Scout runs this', never 'Do this'
    view = render.status_view(r)
    assert "Scout runs this" in view
    assert "Do this — then mark done" not in view




def test_scout_pending_empty_when_all_scout_done():
    """Once the skill has run every scout step, scout_pending is empty."""
    st, orch = _orch()   # _orch clears phase-0 scout + ccd scout via helpers
    orch.run_until_gate()
    assert orch.status_report()["scout_pending"] == []




def test_scout_steps_declare_outbound_effect():
    """Every scout step that sends something EXTERNAL (email / Teams post / pipeline
    trigger) carries outbound=True, so the autonomous automation posts a one-line Scout-DM
    copy of what went out. A local follow-up like lockdown's check-lockdown is outbound=False
    (nothing left the box) and stays quiet."""
    from orchestrator.outcomes import as_dict
    import steps as _steps
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", ccd_source="default")
    expect = {
        ("preflight", "notice"): True,           # workiq_send_email
        ("preflight", "flight_reminder"): True,  # workiq_send_chat_message
        ("preflight", "lockdown"): False,        # check-lockdown (local follow-up)
        ("ccd", "final_reminder"): True,         # workiq_send_email
        ("ccd", "pr_reminder"): True,            # workiq_send_chat_message
        ("ccd", "localization"): True,           # azure_devops-pipelines_run_pipeline
    }
    for (phase, sid), want in expect.items():
        out = as_dict(_steps.get_step(phase, sid).build(st))
        assert out["kind"] == "needs_skill", f"{phase}.{sid} not needs_skill"
        assert out.get("outbound") is want, f"{phase}.{sid} outbound={out.get('outbound')} want {want}"




def test_record_step_generic_pass():
    """record-step marks a scout-assisted step done (skill's post-send call)."""
    import tempfile as _tf
    from orchestrator.commands import notice as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "t"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid)
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG
            phase = "preflight"; step = "notice"; status = "pass"; detail = "sent"; as_of = None
        ncmd.cmd_record_step(A)
        assert C.load_state(d, rid).is_done("preflight", "notice")




def test_stepstate_data_persists():
    """StepState.data (localization build id / start time) survives save/load."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "s.json")
        st = ReleaseState(release_id="d")
        step = st.get_step("ccd", "localization")
        step.data = {"build_id": "42", "started_at": "2026-09-09T12:00:00Z"}
        st.set_step("ccd", "localization", step)
        st.save(p)
        st2 = ReleaseState.load(p)
        assert st2.get_step("ccd", "localization").data["build_id"] == "42"




def test_parallel_autos_run_despite_pending_holds():
    """Independent auto steps (breaking/cg/cron) complete even while scout/attest
    steps are still holding — a hold no longer blocks its siblings."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()
    # nothing cleared: notice/flight/lockdown (scout) + vitals (attest) all hold
    orch.run_until_gate()
    # yet the independent auto agents ran to completion
    for sid in ("breaking", "cg", "cron"):
        assert st.is_done("preflight", sid), sid
    # and the holds are all surfaced together
    for sid in ("notice", "flight_reminder", "lockdown", "vitals"):
        assert f"preflight.{sid}" in st.pending_human, sid




def test_oneauth_write_access_probe():
    """checks.oneauth_write_access: a successful branch create (then cleanup delete) => granted;
    a rejected create (e.g. 403) => denied. Exercises the Git refs update API via pipelines."""
    from tools import checks
    from tools import pipelines as P
    tip = "abc123"

    def fake_get(url, timeout):
        if "filter=heads/master" in url:
            return (True, {"value": [{"objectId": tip}]}, "")
        return (True, {"value": []}, "")           # no stale probe branch

    og, os_ = P._ado_rest_get, P._ado_rest_send

    calls = []

    def fake_send_ok(url, method, body, timeout):
        calls.append(body[0])
        return (True, {"value": [{"success": True, "updateStatus": "succeeded"}]}, "")

    P._ado_rest_get, P._ado_rest_send = fake_get, fake_send_ok
    try:
        granted, d = checks.oneauth_write_access("pedroro")
    finally:
        P._ado_rest_get, P._ado_rest_send = og, os_
    assert granted, d
    assert "user/pedroro/scout-oneauth-access-check" in calls[0]["name"]
    assert calls[0]["newObjectId"] == tip                 # create -> tip
    assert calls[-1]["newObjectId"] == "0" * 40           # cleanup -> delete

    def fake_send_denied(url, method, body, timeout):
        return (False, None, "AUTH: HTTP 403 (run `az login` / check access)")

    P._ado_rest_get, P._ado_rest_send = fake_get, fake_send_denied
    try:
        granted2, d2 = checks.oneauth_write_access("pedroro")
    finally:
        P._ado_rest_get, P._ado_rest_send = og, os_
    assert not granted2 and "403" in d2




def test_local_mock_blocks_agent_step():
    """A mock `outcome: blocked` replaces a real agent step and holds it; other
    (unmocked) steps still run for real."""
    st, orch = _mock_orch({"preflight.cg": {"outcome": "blocked", "reason": "mocked: boom"}})
    orch.run_until_gate()
    assert st.get_step("preflight", "cg").status == "blocked"
    assert "preflight.cg" in st.pending_human
    assert st.get_step("preflight", "cg").note == "mocked: boom"
    assert st.is_done("preflight", "cron")            # another agent ran for real




def test_local_mock_completes_scout_step():
    """A mock `outcome: done` on a scout step auto-resolves it during `next` — the
    skill is never asked to send — while other scout steps still hold."""
    st, orch = _mock_orch({"preflight.notice": {"outcome": "done", "note": "mocked send"}})
    orch.run_until_gate()
    assert st.is_done("preflight", "notice")
    assert "preflight.notice" not in st.pending_human
    assert st.get_step("preflight", "notice").note == "mocked send"
    assert "preflight.flight_reminder" in st.pending_human   # unmocked scout still holds




def test_local_mock_applies_to_later_phases():
    """Engine-level mocks work for ANY phase's steps, not just Phase 0 — here a
    Phase-1 step is forced to block. Phase 1 (Code Complete Day) is anchored to CCD,
    so the clock must be at/after the CCD for it to be due."""
    st, orch = _mock_orch({"ccd.final_reminder": {"outcome": "blocked", "reason": "mocked P1"}},
                          as_of="2026-07-08")   # CCD day → Phase 1 open
    _clear_phase0_scout(orch)          # advance out of Phase 0
    orch.run_until_gate()
    assert st.get_step("ccd", "final_reminder").status == "blocked"
    assert "ccd.final_reminder" in st.pending_human
    assert st.get_step("ccd", "final_reminder").note == "mocked P1"




def test_local_mock_input_feeds_real_logic():
    """An `input` knob (cg `alerts`) injects data and the step's REAL report/block
    logic runs on it — a Critical alert blocks (no az call)."""
    st, orch = _mock_orch({"preflight.cg": {"alerts": [
        {"severity": "critical", "alertState": "active", "title": "CVE-2026-1"}]}})
    orch.run_until_gate()
    cg = st.get_step("preflight", "cg")
    assert cg.status == "blocked"                       # real _cg_summary/_cg_report decided
    assert "critical" in cg.note.lower()
    assert "preflight.cg" in st.pending_human




def test_local_mock_input_variant_on_scout_step():
    """An `input` knob on a scout step is visible to build() via mock_input — here
    notice's `variant` flips the rendered body to the CCD-day 'update' wording."""
    from steps.preflight import notice
    from steps.lib import mockctx
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08",
                      owner_email="me@x.com")
    with mockctx.active({"variant": "update"}):
        out = notice.build(st)
    assert "Today" in out.payload["body"]              # 'update' variant renders "Today"




def test_step_detail_summarizes_note_for_column():
    """The Details column summary: one line, pipe-safe, first URL as a compact link,
    and an em dash for outstanding (no-note) steps."""
    from orchestrator.render import step_detail
    # multi-line note → lead line only; embedded URL → [link](…)
    d = step_detail({"state": "done", "note":
        "Payload wiki subpage ready: 'August 2026 Release'.\nLink: https://wiki/x?a=1"})
    assert d.startswith("Payload wiki subpage ready") and "[link](https://wiki/x?a=1)" in d
    assert "\n" not in d
    # pipes escaped so the table can't break
    assert step_detail({"state": "done", "note": "a | b | c"}) == "a \\| b \\| c"
    # outstanding step with no note → em dash; already-done with no note → empty
    assert step_detail({"state": "reminder", "note": None}) == "—"
    assert step_detail({"state": "done", "note": None}) == ""




def test_step_links_stored_on_state_and_rendered():
    """Durable links (e.g. CG alerts) are stored as structured StepState.links
    — not just embedded in note text — and surface in the Details column."""
    from datetime import date
    from orchestrator import render
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", ccd_source="default",
                      owner_email="pedroro@microsoft.com")
    mocks = _safe({"preflight.cg": {"alerts": [
        {"alertState": "active", "severity": "high", "title": "CVE-X",
         "url": "https://ado/alert/1"}]}})
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 9, 2), mocks=mocks)
    _pass_scout_checks(orch); orch.gate.sign()
    orch.run_until_gate()
    steps = {s["id"]: s for s in orch.status_report()["current_steps"]}
    # CG: config alerts page + the per-alert deep link, both stored
    cg_urls = [l["url"] for l in steps["cg"]["links"]]
    assert "https://ado/alert/1" in cg_urls and any("_componentGovernance" in u for u in cg_urls)
    # rendered column carries the link markdown
    view = render.status_view(orch.status_report())
    assert "[CVE-X](https://ado/alert/1)" in view




def test_step_knowledge_base_answers_step_questions():
    """The knowledge base returns accurate per-step help; the vitals entry carries
    the correct Play Console navigation, and unknown steps return None (honest)."""
    from orchestrator import knowledge as kb
    v = kb.get_knowledge("preflight", "vitals")
    assert v and any("Monitor and improve" in w and "Android vitals" in w for w in v["where"])
    assert any("Policy and programs" in w and "Policy status" in w for w in v["where"])
    # rendered markdown surfaces the FAQ answers
    md = kb.render_knowledge("preflight", "vitals", v)
    assert "Where is policy status?" in md
    # every Phase-0 step has an entry
    for sid in ("notice", "flight_reminder", "confirm_reminders", "lockdown",
                "breaking", "cg", "vitals", "cron"):
        assert kb.get_knowledge("preflight", sid), sid
    # a step with no entry → None (skill says "no knowledge yet", doesn't invent)
    assert kb.get_knowledge("monitor", "adoption") is None




def test_step_knowledge_module_overlays_yaml():
    """A step module's KNOWLEDGE overlays the yaml per-field (module wins)."""
    from orchestrator import knowledge as kb
    import steps.preflight.cg as cgmod
    saved = getattr(cgmod, "KNOWLEDGE", None)
    cgmod.KNOWLEDGE = {"summary": "OVERRIDDEN"}
    try:
        k = kb.get_knowledge("preflight", "cg")
        assert k["summary"] == "OVERRIDDEN"        # module field wins
        assert k.get("what")                        # yaml fields still present
    finally:
        if saved is None:
            delattr(cgmod, "KNOWLEDGE")
        else:
            cgmod.KNOWLEDGE = saved




def test_step_modules_and_config_stay_in_sync():
    """STRUCTURAL GUARDRAIL — makes the modular structure self-enforcing so adding a
    step can't silently drift. Every auto-discovered step module must map to a
    config/phases.yaml step, and its KIND must match the config flags. This fails
    LOUDLY if a module's ID is wrong/orphaned or its KIND disagrees with the flow."""
    import yaml
    import steps
    cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    cfg_steps = {f"{ph['id']}.{s['id']}": s
                 for ph in cfg["phases"] for s in ph["steps"]}

    def cfg_kind(s):
        if s.get("gate"):
            return "gate"
        if s.get("source") == "scout":
            return "scout"
        if s.get("attest"):
            return "attest"
        if s.get("owner") == "human":
            return "reminder"
        return "agent"

    discovered = steps.discover()
    assert discovered, "no step modules discovered — auto-discovery broke"
    for key, mod in discovered.items():
        # 1. no orphan module: every module corresponds to a real flow step
        assert key in cfg_steps, f"step module '{key}' has no config/phases.yaml entry"
        # 2. KIND matches the config's classification (no drift)
        k, c = getattr(mod, "KIND", None), cfg_kind(cfg_steps[key])
        if k == "agent":
            assert c == "agent", f"{key}: module KIND=agent but config classifies as {c}"
        elif k == "scout":
            assert c == "scout", f"{key}: module KIND=scout but config classifies as {c}"
        elif k == "attest":
            assert c in ("attest", "reminder"), f"{key}: module KIND=attest but config={c}"




def test_pending_scout_step_is_not_a_user_action():
    """A pending scout step (Scout scrapes/sends it, e.g. lockdown) must NOT render
    as 'Your action' — it's Scout's automatic work. It shows status 'scout' and is
    not flagged needs_owner. Only when it BLOCKS does it become a user task."""
    st, orch = _mock_orch({})            # nothing mocked; lockdown holds as scout
    orch.run_until_gate()
    ld = next(s for s in orch.status_report()["active_phase"]["steps"]
              if s["id"] == "lockdown")
    assert ld["status"] == "scout" and not ld["needs_owner"]
    # attest steps ARE user tasks (contrast) — still flagged
    vt = next(s for s in orch.status_report()["active_phase"]["steps"]
              if s["id"] == "vitals")
    assert vt["needs_owner"]
    # a scout step recorded as attention (overlap) DOES become a user task
    orch.record_scout_step("preflight", "lockdown", "attention", "CCOA overlap — shift CCD")
    ld2 = next(s for s in orch.status_report()["active_phase"]["steps"]
               if s["id"] == "lockdown")
    assert ld2["status"] == "blocked" and ld2["needs_owner"]




def test_find_orchestrator_pending_approval_and_submit():
    """pipelines: discover the parked stage's pending approval (timeline + approvals API) and
    submit it."""
    from tools import pipelines as P
    run = {"id": 555, "tags": ["AuthenticatorBranch=release-2026-08-13"]}
    timeline = [
        {"id": "s1", "type": "Stage", "name": "Remove RC Tags", "state": "pending"},
        {"id": "cp", "type": "Checkpoint.Approval", "state": "inProgress", "parentId": "ph"},
        {"id": "ph", "type": "Phase", "name": "Wait for approval", "parentId": "s1"},
    ]
    approvals = {"value": [
        {"id": "OTHER", "status": "approved",
         "pipeline": {"owner": {"_links": {"web": {"href": ".../_build/results?buildId=999"}}}}},
        {"id": "APPR-555", "status": "pending",
         "pipeline": {"owner": {"_links": {"web": {"href": ".../_build/results?buildId=555"}}}}},
    ]}
    o = (P.find_orchestrator_run, P.get_timeline, P._ado_rest_get, P._ado_rest_send)
    P.find_orchestrator_run = lambda *a, **k: (True, run, "")
    P.get_timeline = lambda *a, **k: (True, timeline, "")
    P._ado_rest_get = lambda url, timeout: (True, approvals, "")
    sent = {}

    def fake_send(url, method, body, timeout):
        sent["body"] = body
        return (True, {"value": [{"status": "approved"}]}, "")

    P._ado_rest_send = fake_send
    try:
        ok, info, d = P.find_orchestrator_pending_approval("ORG", "PROJ", "2026-08")
        assert ok and info and info["approval_id"] == "APPR-555" and info["stage"] == "Remove RC Tags"
        assert info["build_id"] == 555
        oks, ds = P.submit_pipeline_approval("ORG", "PROJ", "APPR-555", "go")
        assert oks and "approved" in ds
        assert sent["body"][0]["approvalId"] == "APPR-555" and sent["body"][0]["status"] == "approved"
    finally:
        (P.find_orchestrator_run, P.get_timeline, P._ado_rest_get, P._ado_rest_send) = o




def test_orchestrator_stage_state_reads_named_stage(monkeypatch):
    """orchestrator_stage_state returns the named Stage's timeline state/result."""
    from tools import pipelines as P
    monkeypatch.setattr(P, "find_orchestrator_run", lambda *a, **k: (True, {"id": 777}, ""))
    monkeypatch.setattr(P, "get_timeline", lambda *a, **k: (True, [
        {"type": "Stage", "name": "Remove RC Tags", "state": "completed", "result": "succeeded"},
        {"type": "Stage", "name": "Publish GitHub Release Notes", "state": "inProgress", "result": None},
    ], ""))
    ok, info, _ = P.orchestrator_stage_state("O", "P", "2026-08", "Publish GitHub Release Notes")
    assert ok and info == {"state": "inProgress", "result": None, "build_id": 777}




def test_discover_versions_from_orchestrator_tags():
    """pipelines.discover_versions maps the orchestrator run's Next*Version tags."""
    from tools import pipelines as P
    o = P.find_orchestrator_run
    P.find_orchestrator_run = lambda *a, **k: (True, {"id": 9, "tags": [
        "AuthenticatorBranch=release-2026-08-13", "NextCommonVersion=24.6.0",
        "NextMsalVersion=8.4.2", "NextBrokerVersion=20.1.0"]}, "")
    try:
        ok, v, _d = P.discover_versions("ORG", "PROJ", "2026-08")
    finally:
        P.find_orchestrator_run = o
    assert ok and v == {"common": "24.6.0", "msal": "8.4.2", "broker": "20.1.0"}




def test_release_state_records_versions_roundtrip():
    """record_versions merges non-blank versions and survives save/load."""
    import tempfile
    from orchestrator import cli_common as _C
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0", "authenticator": ""})
    assert st.versions == {"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"}  # blank dropped
    with tempfile.TemporaryDirectory() as d:
        _C.save_state(st, d, "2026-08")
        again = _C.load_state(d, "2026-08")
    assert again.versions["msal"] == "8.4.2" and "authenticator" not in again.versions




def test_gh_release_exists_parses_present_absent_error(monkeypatch):
    """gh_release_exists: present (json) -> published; 'release not found' -> missing; other -> error."""
    from tools import prs as PR
    calls = {}

    def fake_run(args, cwd=None, timeout=120):
        calls["args"] = args
        tag = args[3]
        if tag == "v1.0.0":
            return (0, '{"tagName":"v1.0.0","name":"Version 1.0.0","isDraft":false,"url":"U"}', "")
        if tag == "v9.9.9":
            return (1, "", "release not found")
        if tag == "vDRAFT":
            return (0, '{"tagName":"vDRAFT","name":"d","isDraft":true,"url":"U"}', "")
        return (1, "", "HTTP 500 gateway error")
    monkeypatch.setattr(PR, "_run", fake_run)
    ok, pub, info, d = PR.gh_release_exists("owner/repo", "v1.0.0")
    assert ok and pub and info["url"] == "U" and calls["args"][:4] == ["gh", "release", "view", "v1.0.0"]
    ok2, pub2, _i2, d2 = PR.gh_release_exists("owner/repo", "v9.9.9")
    assert ok2 and pub2 is False and d2 == "release not found"
    ok3, pub3, _i3, d3 = PR.gh_release_exists("owner/repo", "vDRAFT")
    assert ok3 and pub3 is False and "draft" in d3                 # a draft is NOT published
    ok4, pub4, _i4, d4 = PR.gh_release_exists("owner/repo", "vERR")
    assert ok4 is False and "500" in d4




def test_create_lightweight_tag_creates_and_is_idempotent(monkeypatch):
    """create_lightweight_tag: POSTs a new ref when absent; returns the existing target when the
    tag already exists (never recreated)."""
    from tools import pipelines as P
    # absent → create
    monkeypatch.setattr(P, "_ado_rest_get", lambda url, t: (True, {"value": []}, ""))
    sent = {}

    def fake_send(url, method, body, t):
        sent["body"] = body
        return (True, {"value": [{"success": True}]}, "")
    monkeypatch.setattr(P, "_ado_rest_send", fake_send)
    ok, info, _ = P.create_lightweight_tag(P.AUTH_ORG, P.AUTH_PROJECT, "repoX", "6.2608.5658", _TA_COMMIT)
    assert ok and info == {"created": True, "objectId": _TA_COMMIT}
    assert sent["body"][0]["name"] == "refs/tags/6.2608.5658"
    assert sent["body"][0]["oldObjectId"] == "0" * 40 and sent["body"][0]["newObjectId"] == _TA_COMMIT

    # present → idempotent (returns existing objectId, no send)
    monkeypatch.setattr(P, "_ado_rest_get",
                        lambda url, t: (True, {"value": [{"name": "refs/tags/6.2608.5658", "objectId": _TA_COMMIT}]}, ""))
    monkeypatch.setattr(P, "_ado_rest_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not POST")))
    ok2, info2, _ = P.create_lightweight_tag(P.AUTH_ORG, P.AUTH_PROJECT, "repoX", "6.2608.5658", _TA_COMMIT)
    assert ok2 and info2 == {"created": False, "objectId": _TA_COMMIT}




def test_oneauth_edit_functions_are_minimal_and_correct():
    """Each of the 4 edit functions makes exactly the intended change; toml does NOT touch the
    separate msIdentityCommonTest version."""
    from tools import oneauth as OA
    t = OA.edit_toml(_OA_FILES["toml"], "24.7.0")
    assert 'msIdentityCommon = "24.7.0"' in t
    assert 'msIdentityCommonTest = "0.0.20260506.3"' in t          # untouched
    cg = OA.edit_cgmanifest(_OA_FILES["cgmanifest"], "24.7.0")
    assert '"version": "24.7.0"' in cg and '"artifactId": "common"' in cg
    rd = OA.edit_readme(_OA_FILES["readme"], "24.7.0")
    assert "| MSAL Android Common              | 24.7.0" in rd
    cl = OA.edit_changelog(_OA_FILES["changelog"], "24.7.0", "8.5.0")
    line = "- (Android) Ingest AndroidCommon 24.7.0. Any apps that still use MSAL.Android *MUST* update to 8.5.0."
    # inserted as the FIRST bullet under Unreleased -> Other Changes (before the iOS line)
    assert line in cl and cl.index(line) < cl.index("- (iOS) something.")




def test_oneauth_edit_functions_raise_on_missing_anchor():
    """A missing anchor raises ValueError (never a silent no-op bump)."""
    from tools import oneauth as OA
    import pytest as _pytest
    for fn, args in [(OA.edit_toml, ("nope\n", "1.0")),
                     (OA.edit_cgmanifest, ("{}", "1.0")),
                     (OA.edit_readme, ("| other |\n", "1.0")),
                     (OA.edit_changelog, ("# Changelog\n", "1.0", "2.0"))]:
        with _pytest.raises(ValueError):
            fn(*args)




def test_oneauth_edit_changelog_idempotent():
    """Re-running with the same version doesn't add a duplicate bullet."""
    from tools import oneauth as OA
    once = OA.edit_changelog(_OA_FILES["changelog"], "24.7.0", "8.5.0")
    twice = OA.edit_changelog(once, "24.7.0", "8.5.0")
    assert once == twice




def test_oneauth_apply_edits_returns_only_changed_paths():
    """apply_edits maps changed files to their new content, keyed by repo path."""
    from tools import oneauth as OA
    changed = OA.apply_edits(dict(_OA_FILES), "24.7.0", "8.5.0")
    assert set(changed.keys()) == {OA.FILES["toml"], OA.FILES["cgmanifest"],
                                   OA.FILES["readme"], OA.FILES["changelog"]}




def test_oneauth_merge_conflict_surfaces_not_forced(monkeypatch):
    """merge_dev_into_ingestion returns conflict (ok=False) and abandons the transient PR —
    never forces the merge."""
    from tools import oneauth as OA
    monkeypatch.setattr(OA, "ahead_behind", lambda b, t, timeout=120: (True, {"ahead": 5, "behind": 3}, ""))
    monkeypatch.setattr(OA, "create_pr", lambda s, t, ti, d, timeout=90: (True, {"id": 7, "mergeStatus": "queued", "url": "u"}, ""))
    monkeypatch.setattr(OA, "get_pr", lambda pid, timeout=60: (True, {"mergeStatus": "conflicts", "lastMergeSourceCommit": {"commitId": "x"}}, ""))
    abandoned = {"n": 0}
    monkeypatch.setattr(OA, "abandon_pr", lambda pid, timeout=60: (abandoned.__setitem__("n", abandoned["n"] + 1), (True, ""))[1])
    ok, info, detail = OA.merge_dev_into_ingestion(dry_run=False)
    assert ok is False and info["conflict"] is True and abandoned["n"] == 1 and "CONFLICT" in detail

