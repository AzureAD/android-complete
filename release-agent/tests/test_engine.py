"""Unit + dry-run-replay tests for the Release Orchestrator (X4+X5).

Run:  python tests/test_engine.py     (plain-run smoke)
 or:  python -m pytest tests -q
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from orchestrator.state import ReleaseState
from orchestrator.engine import Orchestrator
from orchestrator import cli_common as C

CONFIG = os.path.join(ROOT, "config", "phases.yaml")

# Make tests hermetic: stub the AUTO verifier so it never hits az / the network.
# Auto verifiers return pass/fail (no half-measures). Default: pass, so the gate
# can clear in flow tests. Individual tests override this to test failure.
import phases.readiness_verifiers as _rv
from phases.readiness_verifiers import VerifyResult
def _bd_result(status):
    def _fn(item, dry_run):
        details = [{"name": c.get("name", str(c.get("id"))), "url": c.get("url"),
                    "ok": status == "pass", "detail": "stubbed"}
                   for c in item.get("checks", [])]
        return VerifyResult(status, f"stubbed {status}", details)
    return _fn

_rv.REGISTRY = {"build_defs": _bd_result("pass"), "mcp_servers": _bd_result("pass")}


def _stub_build_defs(status):
    _rv.REGISTRY["build_defs"] = _bd_result(status)


def _pass_scout_checks(orch):
    """Record all scout-assisted (source: scout) auto checks as pass — mirrors the
    skill running the ICM + Kusto + settings checks and recording their results."""
    orch.gate.record_check("oncall_now", "pass", "stubbed: not on-call")
    orch.gate.record_check("adx_access", "pass", "stubbed: can query cluster")
    orch.gate.record_check("silent_perms", "pass", "stubbed: all servers auto-approved")


def _clear_notice(orch):
    """Record the scout-assisted `notice` step as pass (skill sent the email)."""
    orch.record_scout_step("preflight", "notice", "pass", "test: notice sent")


def _clear_lockdown(orch):
    """Record the CCOA lockdown scout-step as pass (no overlap) — mirrors the skill
    running the browser check. Needed for the phase flow to advance past this step."""
    orch.record_scout_step("preflight", "lockdown", "pass", "test: no CCOA overlap")


def _clear_phase0_scout(orch):
    """Clear ALL Phase-0 human/scout holds (notice + flight_reminder + confirm_reminders
    + lockdown) so the flow can advance out of Phase 0 to the first gate (branch_cut)."""
    _clear_notice(orch)
    orch.record_scout_step("preflight", "flight_reminder", "pass", "test: reminders posted")
    orch.complete_step("preflight", "confirm_reminders", "test: owner confirmed")
    _clear_lockdown(orch)
    orch.complete_step("preflight", "vitals", "test: vitals & policy reviewed")


def _clear_early_phase0_scout(orch):
    """Clear the first two Phase-0 scout/human holds (notice + flight_reminder) but LEAVE
    confirm_reminders holding — so the flow stays IN Phase 0 with progress + a hold."""
    _clear_notice(orch)
    orch.record_scout_step("preflight", "flight_reminder", "pass", "test: reminders posted")


def _orch(signed=True):
    """Fresh orchestrator. By default the readiness entry gate is pre-signed so
    tests can focus on the phase flow; readiness itself is tested separately."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    if signed:
        # scout-assisted checks (skill records them) + attest the rest + verify auto
        _pass_scout_checks(orch)
        orch.gate.sign()  # attest attest-items + verify python-auto
        _clear_phase0_scout(orch)   # skill-run notice + CCOA lockdown → pass
    return st, orch


# ---- readiness entry gate ----

def test_entry_gate_blocks_before_signing():
    st, orch = _orch(signed=False)
    actions = orch.run_until_gate()
    assert actions[-1].kind == "readiness"
    assert st.status == "readiness_gate"
    # nothing ran
    assert sum(1 for a in actions if a.kind == "ran") == 0
    assert not st.is_done("preflight", "notice")


def test_signing_clears_entry_gate():
    st, orch = _orch(signed=False)
    orch.run_until_gate()
    assert st.status == "readiness_gate"
    _pass_scout_checks(orch)
    orch.gate.sign()
    assert st.readiness_signed
    _clear_phase0_scout(orch)
    orch.run_until_gate()
    # pre-flight is all reminders/auto now; the first real gate is the branch cut
    assert st.current_step == "branch_cut"
    assert st.status == "holding_gate"


def test_partial_sign_does_not_clear():
    st, orch = _orch(signed=False)
    orch.gate.sign(["yubikey"])          # only one attest item
    assert not st.readiness_signed
    orch.run_until_gate()
    assert st.status == "readiness_gate"


def test_sign_records_evidence_note():
    """Attestations carry the engineer's confirmation as evidence (note)."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign(["play_console_access", "oncall_window", "saw_ame", "yubikey"],
                   note="engineer confirmed all four")
    assert st.readiness_signed
    assert st.readiness_items["yubikey"].get("note") == "engineer confirmed all four"


def test_cli_sign_refuses_bare_and_has_no_all_flag():
    """The CLI must NOT let a bare `sign` (or a blanket --all) clear the gate — that
    was the integrity hole where every human item got attested with no confirmation.
    A bare sign returns non-zero and signs nothing; --all no longer exists."""
    import argparse, tempfile, os as _os
    from orchestrator.commands import readiness as rcmd
    # --all must be gone from the parser
    sub = argparse.ArgumentParser().add_subparsers()
    rcmd.register(sub)
    sign_parser = sub.choices["sign"]
    opt_strings = {s for a in sign_parser._actions for s in a.option_strings}
    assert "--all" not in opt_strings
    assert "--item" in opt_strings and "--note" in opt_strings
    # a bare sign (no --item) refuses and does not sign
    with tempfile.TemporaryDirectory() as tmp:
        _stub_build_defs("pass")
        ns = argparse.Namespace(runs_root=tmp, release="t", config=CONFIG,
                                item=None, note="")
        # seed a release so load_orch works
        from orchestrator.state import ReleaseState
        st0 = ReleaseState(release_id="t", dry_run=True)
        from orchestrator import cli_common as _C
        _C.save_state(st0, tmp, "t")
        rc = rcmd.cmd_sign(ns)
        assert rc != 0
        st_after, _ = _C.load_orch(tmp, "t", CONFIG)
        assert not st_after.readiness_signed


def test_decline_any_item_blocks_gate():
    """Every item is required — declining ANY item blocks the gate."""
    st, orch = _orch(signed=False)
    orch.gate.decline(["yubikey"])
    assert st.blocked
    assert "yubikey" in st.blocked_items
    actions = orch.run_until_gate()
    assert actions[-1].kind == "blocked"
    assert st.status == "blocked"
    assert not st.readiness_signed
    assert not st.is_done("preflight", "notice")


def test_decline_nonhard_item_also_blocks():
    """No hard/soft distinction — declining on-call blocks just the same."""
    st, orch = _orch(signed=False)
    orch.gate.decline(["oncall_now"])
    assert st.blocked
    assert "oncall_now" in st.blocked_items


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
    st = ReleaseState(release_id="t", dry_run=True, ccd="2026-07-08", ccd_source="default")
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


def test_oncall_window_shows_computed_dates():
    """The windowed attest item exposes CCD-relative dates (CCD-7 .. CCD+14)."""
    from orchestrator import render
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-07", dry_run=True, ccd="2026-07-08", ccd_source="default")
    orch = Orchestrator(CONFIG, st)
    win = next(i for i in orch.gate.checklist()["items"] if i["id"] == "oncall_window")["window"]
    assert win == {"start": "2026-07-01", "end": "2026-07-22"}   # CCD-7 .. CCD+14
    out = render.readiness_table(orch.gate.checklist(), "2026-07")
    assert "2026-07-01" in out and "2026-07-22" in out


def test_no_blocking_attribute():
    """The 'blocking' per-item distinction was removed — all items equally required."""
    st, orch = _orch(signed=False)
    chk = orch.gate.checklist()
    for it in chk["items"]:
        assert "blocking" not in it


def test_failing_auto_keeps_gate_closed():
    """An auto check that FAILS is not satisfiable by attestation — gate stays shut."""
    _stub_build_defs("fail")
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    orch.gate.sign()  # attests humans, verifies auto (fails)
    assert not st.readiness_signed
    assert st.readiness_items["build_access"]["status"] == "fail"
    orch.run_until_gate()
    assert st.status == "readiness_gate"
    _stub_build_defs("pass")  # restore


def test_passing_auto_plus_attest_clears_gate():
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    _pass_scout_checks(orch)   # scout-assisted (ICM + Kusto)
    orch.gate.sign()
    assert st.readiness_signed
    assert st.readiness_items["build_access"]["status"] == "pass"
    assert st.readiness_items["oncall_now"]["status"] == "pass"   # verified, not attested


# ---- scout-assisted auto verifier (ICM on-call) ----

def test_verify_skips_scout_assisted_item():
    """The Python verify() must NOT run or fail a source:scout item — the skill
    records it. It should stay pending until record_check is called."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    orch.gate.verify()
    assert st.readiness_items["build_access"]["status"] == "pass"   # python auto ran
    assert st.readiness_items.get("oncall_now", {}).get("status", "pending") == "pending"


def test_record_check_pass_then_sign_clears_gate():
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    orch.gate.record_check("adx_access", "pass", "can query")
    orch.gate.record_check("silent_perms", "pass", "servers auto-approved")
    orch.gate.sign()                       # everything but oncall_now satisfied
    assert not st.readiness_signed
    orch.gate.record_check("oncall_now", "pass", "not in roster")
    assert st.readiness_signed             # last item satisfied -> gate clears


def test_record_check_fail_keeps_gate_closed():
    """If the ICM check says you ARE on-call, the item fails and the gate stays shut."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    orch.gate.sign()
    orch.gate.record_check("oncall_now", "fail", "you are on-call this rotation")
    assert not st.readiness_signed
    chk = orch.gate.checklist()
    noc = next(i for i in chk["items"] if i["id"] == "oncall_now")
    assert noc["status"] == "fail" and not noc["satisfied"]


def test_record_check_rejects_non_scout_item():
    """A python-verified auto item (build_access) cannot be hand-recorded."""
    st, orch = _orch(signed=False)
    res = orch.gate.record_check("build_access", "pass", "hand-wave")
    assert "error" in res
    # unknown item and bad status also rejected
    assert "error" in orch.gate.record_check("nope", "pass")
    assert "error" in orch.gate.record_check("oncall_now", "maybe")


def test_silent_perms_is_required_scout_item():
    """silent_perms (source: scout) is a required auto item — the gate stays closed
    until the skill records it, and it carries required_servers as data."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    # it's a scout item, so verify() must NOT touch it
    orch.gate.verify()
    assert st.readiness_items.get("silent_perms", {}).get("status", "pending") == "pending"
    # required_servers is exposed as data for the skill (m_get_settings check)
    sp = next(i for i in orch.gate.checklist()["items"] if i["id"] == "silent_perms")
    assert sp["verify"] == "auto" and sp["source"] == "scout"
    assert sp["required_servers"] == ["shell", "workiq", "playwright", "kusto", "icm"]
    # everything else satisfied but silent_perms → gate still closed
    orch.gate.record_check("oncall_now", "pass", "not on-call")
    orch.gate.record_check("adx_access", "pass", "can query")
    orch.gate.sign()
    assert not st.readiness_signed
    orch.gate.record_check("silent_perms", "pass", "all servers auto-approved")
    assert st.readiness_signed


def test_mcp_servers_is_python_auto_item():
    """mcp_servers is a Python-verified auto item — verify() runs it (not the skill)."""
    _stub_build_defs("pass")           # also stubs mcp_servers -> pass in test REGISTRY
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    orch.gate.verify()
    assert st.readiness_items["mcp_servers"]["status"] == "pass"
    m = next(i for i in orch.gate.checklist()["items"] if i["id"] == "mcp_servers")
    assert m["verify"] == "auto" and not m["source"]   # python, not scout


def test_silent_perms_opt_out_degraded_satisfies_gate():
    """silent_perms is soft/opt-out: recording 'degraded' (user proceeds without
    silent runs) SATISFIES the gate, unlike a normal auto item where only pass counts."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", dry_run=True)
    orch = Orchestrator(CONFIG, st)
    orch.gate.record_check("oncall_now", "pass", "not on-call")
    orch.gate.record_check("adx_access", "pass", "can query")
    # user opts out of silent runs -> degraded, but the gate still clears on sign
    res = orch.gate.record_check("silent_perms", "degraded", "proceeding without silent runs")
    assert "error" not in res
    sp = next(i for i in orch.gate.checklist()["items"] if i["id"] == "silent_perms")
    assert sp["status"] == "degraded" and sp["satisfied"]
    orch.gate.sign()
    assert st.readiness_signed


def test_degraded_rejected_for_non_opt_out_item():
    """'degraded' is only valid for opt_out items — a normal scout item rejects it."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t", dry_run=True)
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

def test_holds_at_first_gate():
    st, orch = _orch()
    actions = orch.run_until_gate()
    assert actions[-1].kind == "gate"
    assert actions[-1].step == "branch_cut"      # Phase 0 has no gate now; first gate is the branch cut
    # auto steps that run: Phase-0 breaking/cg/cron/wiki (4) + Phase-1 final_reminder/localization/precheck_prs (3)
    assert sum(1 for a in actions if a.kind == "ran") == 7


def test_gate_blocks_until_approved():
    st, orch = _orch()
    orch.run_until_gate()
    assert st.status == "holding_gate"
    orch.run_until_gate()
    assert st.status == "holding_gate"
    assert not st.is_done("ccd", "branch_cut")


def test_approve_advances():
    st, orch = _orch()
    orch.run_until_gate()
    orch.approve_gate("ok")
    assert st.is_done("ccd", "branch_cut")
    orch.run_until_gate()
    assert st.current_step == "go_test"          # next gate after the branch cut
    assert st.status == "holding_gate"


def test_deny_blocks():
    st, orch = _orch()
    orch.run_until_gate()
    orch.deny_gate("flag not approved")
    assert st.status == "blocked"
    assert any("denied" in p for p in st.pending_human)


def test_full_dry_run_replay_completes():
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
        st = ReleaseState(release_id="rt", dry_run=True)
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch)
        orch.gate.sign()
        _clear_phase0_scout(orch)
        orch.run_until_gate()
        st.save(path)
        # reload — simulates resuming next day
        st2 = ReleaseState.load(path)
        assert st2.status == "holding_gate"
        assert st2.current_step == "branch_cut"
        assert st2.readiness_signed  # readiness survives the roundtrip
        orch2 = Orchestrator(CONFIG, st2)
        orch2.approve_gate("resumed")
        assert st2.is_done("ccd", "branch_cut")


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
    orch.run_until_gate()   # holds at branch_cut
    act = orch.skip_step("ccd", "branch_cut", "")   # no reason
    assert act.kind == "idle"
    assert not st.is_done("ccd", "branch_cut")       # unchanged


def test_skip_advances_past_gate():
    st, orch = _orch()
    orch.run_until_gate()
    orch.skip_step("ccd", "branch_cut", "n/a this release")
    assert st.is_done("ccd", "branch_cut")                  # skipped counts as done
    rec = st.steps[st.key("ccd", "branch_cut")]
    assert rec["status"] == "skipped"
    orch.run_until_gate()
    assert st.current_step == "go_test"                     # advanced to next gate


def test_reopen_step():
    st, orch = _orch()
    orch.run_until_gate(); orch.approve_gate("ok")
    assert st.is_done("ccd", "branch_cut")
    orch.reopen_step("ccd", "branch_cut")
    assert not st.is_done("ccd", "branch_cut")              # back to pending
    orch.run_until_gate()
    assert st.current_step == "branch_cut"                  # gate re-holds


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
        el.log("release_started", mode="dry-run")
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


def test_eventlog_never_raises_on_bad_path():
    from orchestrator.eventlog import EventLog
    el = EventLog("\x00::invalid::", "x")
    el.log("release_started")   # must silently no-op, not raise
    el.scout_said("x"); el.user_said("y")


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


def test_ccd_conflict_surfaced_in_status():
    st = ReleaseState(release_id="2026-07", dry_run=True, ccd="2026-07-08",
                      ccd_source="default", ccd_conflict="2026-07-09")
    orch = Orchestrator(CONFIG, st)
    rpt = orch.status_report()
    assert rpt["ccd_conflict"] == "2026-07-09"
    from orchestrator import render
    out = render.status_view(rpt)
    assert "Confirm the date" in out and "2026-07-09" in out


def test_anchor_offset_and_date():
    from orchestrator import schedule
    from datetime import date
    assert schedule.anchor_offset("CCD-7") == -7
    assert schedule.anchor_offset("CCD+1") == 1
    assert schedule.anchor_offset("CCD") == 0
    assert schedule.anchor_date(date(2026, 7, 8), "CCD-7") == date(2026, 7, 1)


# ---- phase time-anchoring (Phase 0 opens CCD-7) ----

def _ccd_orch(as_of):
    """Signed orchestrator with CCD=2026-07-08 (Phase 0 opens 2026-07-01)."""
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-07", dry_run=True, ccd="2026-07-08", ccd_source="default")
    orch = Orchestrator(CONFIG, st, as_of=date(*[int(x) for x in as_of.split("-")]))
    _pass_scout_checks(orch)
    orch.gate.sign()
    return st, orch


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


def test_phase0_opens_on_ccd_minus_7():
    st, orch = _ccd_orch("2026-07-01")     # window opens exactly today
    _clear_early_phase0_scout(orch)        # clear notice + flight_reminder, hold at confirm_reminders
    actions = orch.run_until_gate()
    # Phase 0 is open and running: holding at the confirm-reminders attestation step
    assert st.status == "awaiting_action"
    assert st.current_step == "confirm_reminders"
    assert st.is_done("preflight", "notice")


def test_phase_map_marks_scheduled():
    st, orch = _ccd_orch("2026-06-28")
    orch.run_until_gate()
    rpt = orch.status_report()
    p0 = next(p for p in rpt["phases"] if p["id"] == "preflight")
    assert p0["state"] == "scheduled"
    assert p0["opens"] == "2026-07-01"


def test_no_anchor_when_ccd_unknown_runs_immediately():
    """Backward-compatible: with no CCD stored, the anchor is inert and Phase 0 runs."""
    st, orch = _orch()   # ccd is None
    orch.run_until_gate()
    assert st.status == "holding_gate"   # reached flag_freeze, not 'scheduled'


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


def test_reminder_is_not_a_gate():
    from orchestrator.engine import Orchestrator as _O
    st, orch = _orch()
    # ui_failures is a reminder; flag_freeze is a gate
    assert _O._is_reminder({"owner": "human"}) is True
    assert _O._is_reminder({"owner": "human", "gate": True}) is False
    assert _O._is_reminder({"owner": "agent"}) is False


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


def test_notify_unsigned_readiness_is_silent():
    """Readiness is interactive setup — it must NOT push (reversed from before)."""
    from orchestrator import render
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", dry_run=True, ccd="2026-08-12", ccd_source="default")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 6))  # after CCD-7 but unsigned
    assert render.notification(orch.status_report()) == ""


def test_notify_phase0_digest_when_open():
    """Signed + Phase 0 open → a daily phase digest (not-started form)."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-07-01")   # Phase 0 opens today, signed by _ccd_orch
    msg = render.notification(orch.status_report())
    assert "Phase 0" in msg and "Pre-flight" in msg
    assert "has opened" in msg
    assert render.notification_subject(orch.status_report()) == "Release 2026-07 — Phase 0 status"


def test_notify_digest_reports_gate_and_progress():
    from orchestrator import render
    st, orch = _orch()                   # signed, no CCD → phase due immediately
    orch.run_until_gate()                # Phase 0 all reminders/auto; holds at branch_cut (Phase 1)
    msg = render.notification(orch.status_report())
    assert "Progress:" in msg
    assert "Waiting on your decision" in msg and "Cut the release branch" in msg
    assert "your approval" in msg       # lists the human touchpoint


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


def test_owner_stored_and_in_status():
    st = ReleaseState(release_id="2026-08", dry_run=True, ccd="2026-08-12",
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


def test_concurrent_record_check_both_persist():
    """Two record-check CLI invocations fired at the same instant must BOTH
    persist — the per-release lock prevents the last-writer-wins clobber that
    dropped `notice` in the live test (parallel state-write race)."""
    import threading
    from orchestrator import cli as _cli
    with tempfile.TemporaryDirectory() as rr:
        R = "2099-03"
        _cli.main(["--runs-root", rr, "init", "--release", R,
                   "--owner-email", "t@example.com", "--owner-name", "T"])
        barrier = threading.Barrier(2)

        def rec(item):
            barrier.wait()                         # maximize overlap
            _cli.main(["--runs-root", rr, "record-check", "--release", R,
                       "--item", item, "--status", "pass", "--detail", item])
        threads = [threading.Thread(target=rec, args=(i,))
                   for i in ("oncall_now", "adx_access")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        st = C.load_state(rr, R)
        assert st.readiness_items.get("oncall_now", {}).get("status") == "pass"
        assert st.readiness_items.get("adx_access", {}).get("status") == "pass"


def test_status_surfaces_agent_result_notes_and_wiki_link():
    """Agent results are stored as the step note and surfaced in status: the
    Results & activity section shows each done agent's output, incl. the wiki link."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-07-02")          # Phase 0 open
    orch.run_until_gate()                        # runs breaking/cg/cron/wiki (set notes)
    r = orch.status_report()
    steps = {s["id"]: s for s in r["current_steps"]}
    assert steps["cg"].get("note") and steps["wiki"].get("note")
    view = render.status_view(r)
    assert "Results & activity" in view
    assert "Would live at" in view               # the wiki link surfaces (dry-run)


# ---- Phase-0 real pre-flight agents (breaking detect, wiki payload) ----
_SAMPLE_CHANGELOG = """vNext
----------
- [MINOR] add a thing (#1)
- [MAJOR] breaking change OneAuth consumers must handle (#2)
- [PATCH] small fix (#3)
Version 24.5.0
----------
- [MAJOR] an OLD breaking change already shipped (#0)
"""


def test_parse_breaking_only_scans_vnext_section():
    from phases.agents.preflight import parse_breaking
    hits = parse_breaking(_SAMPLE_CHANGELOG, section="vNext", tag="[MAJOR]")
    assert len(hits) == 1
    assert "(#2)" in hits[0] and "(#0)" not in hits[0]


def test_parse_breaking_none_when_no_major():
    from phases.agents.preflight import parse_breaking
    txt = "vNext\n----------\n- [MINOR] x (#1)\nVersion 1.0.0\n----------\n"
    assert parse_breaking(txt) == []


def test_breaking_agent_dry_run_simulates(monkeypatch=None):
    """Dry-run must NOT hit the network — it describes what it would do."""
    from phases.agents import preflight as pa
    called = {"n": 0}
    orig = pa._fetch_text
    pa._fetch_text = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ""
    try:
        st = ReleaseState(release_id="2026-08", dry_run=True)
        r = pa.run_breaking("preflight", {"id": "breaking"}, True, st)
        assert r.ok and "dry-run" in r.action.lower() and called["n"] == 0
    finally:
        pa._fetch_text = orig


def test_breaking_agent_detects_and_drafts():
    from phases.agents import preflight as pa
    orig = pa._fetch_text
    pa._fetch_text = lambda *a, **k: _SAMPLE_CHANGELOG
    try:
        st = ReleaseState(release_id="2026-08", dry_run=False)
        r = pa.run_breaking("preflight", {"id": "breaking"}, False, st)
        assert r.ok
        assert "Detected 1 breaking" in r.action
        assert "(#2)" in r.action and "DRAFT COMMS" in r.action
    finally:
        pa._fetch_text = orig


def test_breaking_agent_none_found_passes():
    from phases.agents import preflight as pa
    orig = pa._fetch_text
    pa._fetch_text = lambda *a, **k: "vNext\n----------\n- [MINOR] x (#1)\nVersion 1.0.0\n"
    try:
        r = pa.run_breaking("preflight", {"id": "breaking"}, False,
                            ReleaseState(release_id="2026-08", dry_run=False))
        assert r.ok and "No breaking" in r.action
    finally:
        pa._fetch_text = orig


def test_breaking_agent_fetch_error_holds():
    from phases.agents import preflight as pa
    orig = pa._fetch_text

    def _boom(*a, **k):
        raise RuntimeError("network down")
    pa._fetch_text = _boom
    try:
        r = pa.run_breaking("preflight", {"id": "breaking"}, False,
                            ReleaseState(release_id="2026-08", dry_run=False))
        assert not r.ok and "could not fetch" in r.action
    finally:
        pa._fetch_text = orig


def test_wiki_agent_dry_run_simulates():
    from phases.agents import preflight as pa
    st = ReleaseState(release_id="2026-08", dry_run=True)
    r = pa.run_wiki("preflight", {"id": "wiki"}, True, st)
    assert r.ok and "dry-run" in r.action.lower() and "August 2026 Release" in r.action


def test_wiki_page_name_convention():
    from phases.agents.preflight import _page_name
    st = ReleaseState(release_id="2026-08")
    assert _page_name(st) == "August 2026 Release"
    assert _page_name(st, 2) == "August 2026 2 Release"


def test_wiki_agent_real_create(monkeypatch=None):
    from phases.agents import preflight as pa
    from tools import checks
    orig_create, orig_exists = checks.create_wiki_page, checks.wiki_page_exists
    seen = {}

    def _fake(org, project, wiki, path, content, timeout=60):
        seen.update(path=path, project=project)
        return checks.CheckResult(True, True, f"created '{path}'")
    checks.create_wiki_page = _fake
    checks.wiki_page_exists = lambda *a, **k: False    # month page absent
    try:
        st = ReleaseState(release_id="2026-08", dry_run=False)
        r = pa.run_wiki("preflight", {"id": "wiki"}, False, st)
        assert r.ok and seen["path"].endswith("August 2026 Release")
        assert seen["project"] == "IdentityWiki"
    finally:
        checks.create_wiki_page, checks.wiki_page_exists = orig_create, orig_exists


def test_wiki_agent_duplicate_creates_numbered_and_notifies():
    """If the month's page exists, the agent leaves it alone, NOTIFIES, and
    creates the next free numbered page."""
    from phases.agents import preflight as pa
    from tools import checks
    orig_create, orig_exists = checks.create_wiki_page, checks.wiki_page_exists
    created = {}

    # "August 2026 Release" exists, "August 2026 2 Release" is free.
    def _exists(org, project, wiki, path, timeout=30):
        return path.endswith("August 2026 Release")
    def _create(org, project, wiki, path, content, timeout=60):
        created["path"] = path
        return checks.CheckResult(True, True, f"created '{path}'")
    checks.wiki_page_exists = _exists
    checks.create_wiki_page = _create
    try:
        st = ReleaseState(release_id="2026-08", dry_run=False)
        r = pa.run_wiki("preflight", {"id": "wiki"}, False, st)
        assert r.ok
        assert created["path"].endswith("August 2026 2 Release")
        assert "already exist" in r.action.lower() and "SECOND" in r.action
    finally:
        checks.create_wiki_page, checks.wiki_page_exists = orig_create, orig_exists


def test_failing_agent_holds_as_action_needed():
    """A pre-flight agent that returns ok=False must HOLD the release as
    awaiting_action (not silently mark the step done)."""
    from phases import agents as pa
    from phases.stub_runner import StepResult
    orig = pa.REGISTRY.get("breaking_detect")
    pa.REGISTRY["breaking_detect"] = lambda *a, **k: StepResult(False, "boom", "agent")
    try:
        st, orch = _ccd_orch("2026-07-08")   # signed, Phase 0 open
        _clear_phase0_scout(orch)
        orch.run_until_gate()
        assert st.status == "awaiting_action"
        assert "preflight.breaking" in st.pending_human
        # human resolves + marks done -> flow resumes
        orch.complete_step("preflight", "breaking", "handled")
        assert st.status == "running"
    finally:
        if orig is not None:
            pa.REGISTRY["breaking_detect"] = orig


def test_tick_advances_and_reports(tmp=None):
    """`tick` advances the release (runs Phase-0 agent steps to the first gate)
    AND returns a digest that lists completed steps + what needs the user."""
    import tempfile as _tf
    from orchestrator.commands import notify as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "2026-07"
        # signed release, CCD reached so Phase 0 is open
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, dry_run=True, ccd="2026-07-08",
                          ccd_source="default", owner_email="o@x.com")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch)
        orch.gate.sign()
        _clear_early_phase0_scout(orch)       # first 3 scout steps done; hold at flag reminder (Phase 0)
        C.save_state(st, d, rid)

        class A:
            runs_root = d
            release = rid
            config = CONFIG
            as_of = "2026-07-08"
            force = False
            json = True
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
        st = ReleaseState(release_id=rid, dry_run=True, ccd="2026-07-08",
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
        first = ncmd._notify_payload(A, rid, advance=True)
        second = ncmd._notify_payload(A, rid, advance=True)
        assert first["message"] and second["message"] == ""


def test_notification_html_lists_all_tasks_and_flags_attention():
    """The HTML digest shows every step with a status pill and flags the hold."""
    from orchestrator import render
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", dry_run=True, ccd="2026-08-12",
                      ccd_source="default", owner_email="o@x.com")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 5))
    _pass_scout_checks(orch)
    orch.gate.sign()
    _clear_early_phase0_scout(orch)       # clear notice + flight_reminder; hold at lockdown (still Phase 0)
    orch.run_until_gate()                 # holds at lockdown scout step
    html = render.notification_html(orch.status_report())
    assert html.startswith("<div") and "Release 2026-08" in html
    # every Phase-0 step name appears in the table
    for name in ("Send early release notice", "Detect lockdown/holiday overlap",
                 "Confirm Play Console vitals", "Create release payload wiki subpage"):
        assert name in html
    # the hold is flagged for attention + a done pill exists
    assert "Needs your attention" in html and "Needs you now" in html
    assert "✓ Done" in html


def test_notification_html_silent_when_plain_is_silent():
    from orchestrator import render
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", dry_run=True, ccd="2026-08-12",
                      ccd_source="default")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 6))  # unsigned → silent
    assert render.notification_html(orch.status_report()) == ""


def test_lockdown_overlap_only_production():
    """Overlap rule: only Production-env periods that intersect the window count;
    Banner-only advisories are ignored even if they overlap."""
    from orchestrator.commands.lockdown import overlapping_periods
    from datetime import date
    ws, we = date(2026, 8, 5), date(2026, 8, 26)
    periods = [
        {"name": "FIFA", "environment": "Banner", "start": date(2026, 8, 1), "end": date(2026, 8, 10)},
        {"name": "YearEnd", "environment": "Production", "start": date(2026, 8, 20), "end": date(2026, 9, 5)},
        {"name": "Old", "environment": "Production", "start": date(2026, 6, 1), "end": date(2026, 7, 1)},
    ]
    hits = overlapping_periods(ws, we, periods, "Production")
    assert [h["name"] for h in hits] == ["YearEnd"]


def test_engine_holds_scout_assisted_lockdown():
    """The scout/human steps hold the flow in sequence. run_until_gate walks
    notice → flight_reminder → confirm_reminders → lockdown as each is cleared."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()                 # NOTE: no scout steps cleared yet
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    assert st.current_step == "notice"          # first scout step holds
    assert "preflight.notice" in st.pending_human
    _clear_notice(orch)
    orch.run_until_gate()
    assert st.current_step == "flight_reminder"  # send step holds
    orch.record_scout_step("preflight", "flight_reminder", "pass", "posted")
    orch.run_until_gate()
    assert st.current_step == "confirm_reminders" # attestation holds
    orch.complete_step("preflight", "confirm_reminders", "owner confirmed")
    orch.run_until_gate()
    assert st.current_step == "lockdown"         # lockdown scout step holds
    assert "preflight.lockdown" in st.pending_human


def test_check_lockdown_pass_and_attention():
    """check-lockdown records pass when nothing overlaps and holds (attention)
    when a Production CCOA overlaps the release window."""
    import tempfile as _tf, json as _json
    from orchestrator.commands import lockdown as lk
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, dry_run=True, ccd="2026-08-12", ccd_source="default")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG; as_of = None
            periods_json = _json.dumps([
                {"name": "Banner Only Thing", "environment": "Banner",
                 "start": "2026-08-10", "end": "2026-08-14"}])
        assert lk.cmd_check_lockdown(A) == 0
        assert C.load_state(d, rid).is_done("preflight", "lockdown")   # banner ignored → pass

        # now a Production overlap → attention (held, not done)
        from orchestrator.state import StepState
        st2 = C.load_state(d, rid)
        st2.set_step("preflight", "lockdown", StepState())   # reset to pending
        C.save_state(st2, d, rid)

        class B(A):
            periods_json = _json.dumps([
                {"name": "IDNA Year-End", "environment": "Production",
                 "start": "2026-08-11", "end": "2026-12-31"}])
        assert lk.cmd_check_lockdown(B) == 0
        st3 = C.load_state(d, rid)
        assert not st3.is_done("preflight", "lockdown")
        assert st3.status == "awaiting_action"


def test_prepare_notice_dry_run_targets_owner():
    """prepare-notice fills the template and, in dry-run, redirects to the owner."""
    import tempfile as _tf, json as _json, io, contextlib
    from orchestrator.commands import notice as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        st = ReleaseState(release_id=rid, dry_run=True, ccd="2026-08-12",
                          ccd_source="default", owner_email="pedroro@microsoft.com",
                          owner_name="Pedro")
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG; variant = None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ncmd.cmd_prepare_notice(A)
        out = _json.loads(buf.getvalue())
        assert out["dry_run"] is True
        assert out["recipients"] == ["pedroro@microsoft.com"]     # redirected
        assert "August" in out["subject"] and "[DRY-RUN" in out["subject"]
        assert "Wednesday, August 12th, 2026" in out["body"]
        assert "08/12/2026" in out["body"] and "@pedroro" in out["body"]


def test_prepare_notice_live_uses_real_recipients():
    import tempfile as _tf, json as _json, io, contextlib
    from orchestrator.commands import notice as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        st = ReleaseState(release_id=rid, dry_run=False, ccd="2026-08-12",
                          ccd_source="default", owner_email="pedroro@microsoft.com")
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG; variant = None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ncmd.cmd_prepare_notice(A)
        out = _json.loads(buf.getvalue())
        assert out["dry_run"] is False
        assert "androididentity@microsoft.com" in out["recipients"]
        assert "jialh@microsoft.com" in out["recipients"]
        assert not out["subject"].startswith("[DRY-RUN")


def test_prepare_notice_html_has_table_and_clean_link():
    """The HTML notice uses a real <table> and a clean <a href> (no raw URL text),
    so Outlook renders it properly instead of mangling markdown."""
    import tempfile as _tf, json as _json, io, contextlib
    from orchestrator.commands import notice as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        st = ReleaseState(release_id=rid, dry_run=True, ccd="2026-08-12",
                          ccd_source="default", owner_email="pedroro@microsoft.com",
                          owner_name="Pedro")
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG; variant = None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ncmd.cmd_prepare_notice(A)
        out = _json.loads(buf.getvalue())
        html = out["html"]
        assert "<table" in html and "</table>" in html
        assert '>the hotfix cherry-pick guide</a>' in html   # clean anchor text
        assert ncmd.HOTFIX_GUIDE_URL in html
        assert "August" in html and "08/12/2026" in html and "@pedroro" in html


def test_ordinal_suffix():
    from orchestrator.commands.notice import _ordinal
    assert _ordinal(1) == "1st" and _ordinal(2) == "2nd" and _ordinal(3) == "3rd"
    assert _ordinal(11) == "11th" and _ordinal(12) == "12th" and _ordinal(21) == "21st"


def test_record_step_generic_pass():
    """record-step marks a scout-assisted step done (skill's post-send call)."""
    import tempfile as _tf
    from orchestrator.commands import notice as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "t"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, dry_run=True)
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG
            phase = "preflight"; step = "notice"; status = "pass"; detail = "sent"; as_of = None
        ncmd.cmd_record_step(A)
        assert C.load_state(d, rid).is_done("preflight", "notice")


def test_prepare_flight_reminder_dry_run_and_live():
    """Flight reminder: dry-run targets the owner's own chat with a [DRY-RUN] prefix;
    live targets the Android Core Team group chat id. Content has all 3 reminders."""
    import tempfile as _tf, json as _json, io, contextlib
    from orchestrator.commands import notice as ncmd
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        # dry-run
        st = ReleaseState(release_id=rid, dry_run=True, ccd="2026-08-12",
                          ccd_source="default", owner_email="pedroro@microsoft.com",
                          owner_name="Pedro")
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ncmd.cmd_prepare_flight_reminder(A)
        out = _json.loads(buf.getvalue())
        assert out["dry_run"] is True and out["send_to"] == "owner"
        assert out["chat_id"] is None and out["owner_email"] == "pedroro@microsoft.com"
        assert "[DRY-RUN" in out["content"]
        # all four reminders present
        assert "Update local flights" in out["content"]
        assert "pre-mortem" in out["content"]
        assert "user-facing string" in out["content"]
        assert "Feature-flag freeze" in out["content"] and "EcsFlight.kt" in out["content"]
        assert out["content"].count("<li>") == 4
        assert "variableGroupId=40" in out["content"]      # variable-group link

        # live
        st2 = ReleaseState(release_id=rid, dry_run=False, ccd="2026-08-12",
                           ccd_source="default", owner_email="pedroro@microsoft.com")
        C.save_state(st2, d, rid)
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            ncmd.cmd_prepare_flight_reminder(A)
        out2 = _json.loads(buf2.getvalue())
        assert out2["dry_run"] is False and out2["send_to"] == "group"
        assert out2["chat_id"] == "19:976a859f167f44e59c4ceca8b1d23581@thread.v2"
        assert "[DRY-RUN" not in out2["content"]


def test_no_localization_strings_step():
    """The old #5 localization strings step was removed."""
    st, orch = _orch()
    preflight = next(p for p in orch.config["phases"] if p["id"] == "preflight")
    assert "strings" not in [s["id"] for s in preflight["steps"]]


def test_confirm_reminders_is_attestation_hold():
    """In the parallel Phase 0, confirm_reminders is a human attestation that becomes
    ready only after flight_reminder is sent; it surfaces as a hold with a confirm
    pill and clears via `done`."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()
    _clear_notice(orch)
    orch.record_scout_step("preflight", "flight_reminder", "pass", "sent")
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    # confirm_reminders (dep on flight_reminder, now done) is among the pending holds
    assert "preflight.confirm_reminders" in st.pending_human
    ap = orch.status_report()["active_phase"]
    step = next(s for s in ap["steps"] if s["id"] == "confirm_reminders")
    assert step["status"] == "confirm" and step["needs_owner"]
    # owner attests → advances
    orch.complete_step("preflight", "confirm_reminders", "verified with feature owners")
    assert st.is_done("preflight", "confirm_reminders")


def test_confirm_reminders_gated_by_flight_send():
    """confirm_reminders must NOT be offered until flight_reminder is sent (dependency)."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()
    _clear_notice(orch)
    # flight_reminder NOT yet recorded → confirm_reminders is not ready
    orch.run_until_gate()
    assert "preflight.confirm_reminders" not in st.pending_human
    assert "preflight.flight_reminder" in st.pending_human   # the send is what's pending


def test_parallel_autos_run_despite_pending_holds():
    """Independent auto steps (breaking/cg/cron/wiki) complete even while scout/attest
    steps are still holding — a hold no longer blocks its siblings."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()
    # nothing cleared: notice/flight/lockdown (scout) + vitals (attest) all hold
    orch.run_until_gate()
    # yet the independent auto agents ran to completion
    for sid in ("breaking", "cg", "cron", "wiki"):
        assert st.is_done("preflight", sid), sid
    # and the holds are all surfaced together
    for sid in ("notice", "flight_reminder", "lockdown", "vitals"):
        assert f"preflight.{sid}" in st.pending_human, sid


def test_cg_report_summarizes_and_flags_high():
    """The CG report groups active alerts by severity and lists High/Critical."""
    from phases.agents.preflight import _cg_summary, _cg_report
    alerts = [
        {"alertState": "active", "severity": "high", "title": "CVE-1",
         "component": {"displayName": "io.netty:x", "displayVersion": "4.2.15"},
         "actionItems": "Upgrade to 4.2.16"},
        {"alertState": "active", "severity": "medium", "title": "CVE-2"},
        {"alertState": "autoDismissed", "severity": "high", "title": "CVE-OLD"},
        {"alertState": "fixed", "severity": "critical", "title": "CVE-FIXED"},
    ]
    active, high = _cg_summary(alerts, ["critical", "high"])
    assert len(active) == 2 and len(high) == 1
    rep = _cg_report(active, high)
    assert "2 active alert(s)" in rep and "1 high" in rep
    assert "CVE-1" in rep and "io.netty:x" in rep and "Upgrade to 4.2.16" in rep
    assert "CVE-OLD" not in rep and "CVE-FIXED" not in rep   # non-active excluded


def test_cg_agent_dry_run_simulates():
    from phases.agents import preflight as pa
    r = pa.run_cg_alerts("preflight", {"id": "cg"}, True, None)
    assert r.ok and "dry-run" in r.action.lower()


def test_cg_agent_blocks_on_high():
    """High/Critical active alerts BLOCK the step (ok=False) with a fix-and-rerun message."""
    from phases.agents import preflight as pa
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (True, [
        {"alertState": "active", "severity": "high", "title": "CVE-9",
         "component": {"displayName": "pkg", "displayVersion": "1.0"}},
    ], "ok")
    try:
        r = pa.run_cg_alerts("preflight", {"id": "cg"}, False, None)
        assert not r.ok                       # High → blocks
        assert "CVE-9" in r.action and "RERUN" in r.action
    finally:
        checks.fetch_cg_alerts = orig


def test_cg_agent_passes_when_no_high():
    """Only Medium/Low active alerts → the step passes (report captured)."""
    from phases.agents import preflight as pa
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (True, [
        {"alertState": "active", "severity": "medium", "title": "CVE-M"},
    ], "ok")
    try:
        r = pa.run_cg_alerts("preflight", {"id": "cg"}, False, None)
        assert r.ok and "1 active" in r.action
    finally:
        checks.fetch_cg_alerts = orig


def test_cg_blocked_step_reruns_and_clears_when_fixed():
    """A CG block holds the step; fixing (alerts now clean) + rerunning `next`
    re-checks and lets the flow continue."""
    from phases import agents as pa
    from phases.stub_runner import StepResult
    flag = {"high": True}

    def fake_cg(phase, step, dry_run, st):
        if flag["high"]:
            return StepResult(False, "CG: 1 critical active\n→ Fix and RERUN or skip.", "agent")
        return StepResult(True, "CG: 0 active alerts.", "agent")
    orig = pa.REGISTRY["cg_alerts"]
    pa.REGISTRY["cg_alerts"] = fake_cg
    try:
        st, orch = _orch()
        _clear_phase0_scout(orch)          # clear the earlier scout/human holds
        orch.run_until_gate()
        assert st.status == "awaiting_action" and st.current_step == "cg"
        assert st.get_step("preflight", "cg").status == "blocked"
        # the digest shows it blocked / needs owner
        step = next(s for s in orch.status_report()["active_phase"]["steps"] if s["id"] == "cg")
        assert step["status"] == "blocked" and step["needs_owner"]
        # FIX: alerts now clean → RERUN (next) re-checks and passes
        flag["high"] = False
        orch.run_until_gate()
        assert st.is_done("preflight", "cg")
    finally:
        pa.REGISTRY["cg_alerts"] = orig


def test_cg_blocked_step_skip_override():
    """The owner can override a CG block by skipping the step (with a reason)."""
    from phases import agents as pa
    from phases.stub_runner import StepResult
    orig = pa.REGISTRY["cg_alerts"]
    pa.REGISTRY["cg_alerts"] = lambda *a, **k: StepResult(False, "CG: 1 high active", "agent")
    try:
        st, orch = _orch()
        _clear_phase0_scout(orch)
        orch.run_until_gate()
        assert st.current_step == "cg" and st.get_step("preflight", "cg").status == "blocked"
        orch.skip_step("preflight", "cg", "accepted risk; tracked separately")
        assert st.is_done("preflight", "cg")   # skipped counts as done
        assert st.get_step("preflight", "cg").status == "skipped"
    finally:
        pa.REGISTRY["cg_alerts"] = orig


def test_cg_agent_fetch_error_holds():
    from phases.agents import preflight as pa
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (False, [], "403 forbidden")
    try:
        r = pa.run_cg_alerts("preflight", {"id": "cg"}, False, None)
        assert not r.ok and "could not read alerts" in r.action
    finally:
        checks.fetch_cg_alerts = orig


def test_cron_check_dry_run_simulates():
    from phases.agents import preflight as pa
    r = pa.run_cron_check("preflight", {"id": "cron"}, True, None)
    assert r.ok and "dry-run" in r.action.lower()


def test_cron_check_passes_on_recent_scheduled_run():
    from phases.agents import preflight as pa
    from tools import checks
    from datetime import datetime, timezone
    orig = checks.latest_scheduled_build
    now_iso = datetime.now(timezone.utc).isoformat()
    checks.latest_scheduled_build = lambda *a, **k: (True, {
        "queueTime": now_iso, "result": "succeeded", "status": "completed"}, "ok")
    try:
        r = pa.run_cron_check("preflight", {"id": "cron"}, False, None)
        assert r.ok and "scheduled and firing" in r.action
    finally:
        checks.latest_scheduled_build = orig


def test_cron_check_blocks_when_stale():
    from phases.agents import preflight as pa
    from tools import checks
    orig = checks.latest_scheduled_build
    checks.latest_scheduled_build = lambda *a, **k: (True, {
        "queueTime": "2026-01-01T06:00:00Z", "result": "succeeded", "status": "completed"}, "ok")
    try:
        r = pa.run_cron_check("preflight", {"id": "cron"}, False, None)
        assert not r.ok and "stale" in r.action
    finally:
        checks.latest_scheduled_build = orig


def test_cron_check_blocks_when_no_scheduled_run():
    from phases.agents import preflight as pa
    from tools import checks
    orig = checks.latest_scheduled_build
    checks.latest_scheduled_build = lambda *a, **k: (True, None, "no scheduled runs in recent history")
    try:
        r = pa.run_cron_check("preflight", {"id": "cron"}, False, None)
        assert not r.ok and "no scheduled run" in r.action
    finally:
        checks.latest_scheduled_build = orig


def test_vitals_is_attestation_hold():
    """Play Console vitals/policy is an attest step (no API for policy): it HOLDS
    for the owner to confirm they reviewed it, and clears via `done`."""
    st, orch = _orch()   # clears the earlier holds (incl. vitals via _clear_phase0_scout)
    # re-open vitals to observe its natural hold
    orch.reopen_step("preflight", "vitals")
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    assert st.current_step == "vitals"
    step = next(s for s in orch.status_report()["active_phase"]["steps"] if s["id"] == "vitals")
    assert step["status"] == "confirm" and step["needs_owner"]
    orch.complete_step("preflight", "vitals", "reviewed vitals + policy status in Play Console")
    assert st.is_done("preflight", "vitals")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")
