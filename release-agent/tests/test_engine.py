"""Unit + flow-replay tests for the Release Orchestrator (X4+X5).

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
from orchestrator import mocks as _mocks_mod

CONFIG = os.path.join(ROOT, "config", "phases.yaml")

# Make tests hermetic: stub the AUTO verifier so it never hits az / the network.
# Auto verifiers return pass/fail (no half-measures). Default: pass, so the gate
# can clear in flow tests. Individual tests override this to test failure.
import phases.readiness_verifiers as _rv
from phases.readiness_verifiers import VerifyResult
def _bd_result(status):
    def _fn(item):
        details = [{"name": c.get("name", str(c.get("id"))), "url": c.get("url"),
                    "ok": status == "pass", "detail": "stubbed"}
                   for c in item.get("checks", [])]
        return VerifyResult(status, f"stubbed {status}", details)
    return _fn

_rv.REGISTRY = {"build_defs": _bd_result("pass"), "mcp_servers": _bd_result("pass")}


# Safe-agent profile: injected inputs so Phase-0 agents run their REAL logic
# offline (no network / no ADO write) during run_until_gate. Replaces what the old
# removed simulate branches used to do — now offline via injected inputs.
def _recent_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SAFE_AGENTS = {
    "preflight.cg": {"alerts": []},                                     # → 0 active → pass
    "preflight.cron": {"run": {"queueTime": _recent_iso(), "result": "succeeded"}},  # fresh → pass
    "preflight.breaking": {"changelog": "vNext\n----\n- [MINOR] x (#1)\nVersion 1.0.0\n"},  # no [MAJOR] → pass
    "preflight.wiki": {"outcome": "done", "note": "wiki payload page ready (test)"},  # skip ADO write
    # Phase-2 build_verify agent steps — injected offline inputs so they pass without az.
    "build_verify.checker_fired": {
        "triggering": {"run": {"id": 1678599, "queueTime": "2026-07-08T06:00:00Z"},
                       "result": "succeeded"}},
    "build_verify.orchestrator_health": {
        "run": {"id": 1678611, "tags": ["AuthenticatorBranch=release-2026-07-08",
                                        "NextCommonVersion=1.0.0", "NextMsalVersion=1.0.0",
                                        "NextBrokerVersion=1.0.0"]},
        "stages": [
            {"name": "Validate Branch and Versions availability", "state": "completed", "result": "succeeded"},
            {"name": "Create Release Branches", "state": "completed", "result": "succeeded"},
            {"name": "Trigger RC Testing", "state": "completed", "result": "succeeded"},
            {"name": "Remove RC Tags", "state": "pending", "result": None},
        ]},
    "build_verify.mrwp_ecs": {
        "mrwp_id": "900001",
        "stages": [{"name": "Build", "state": "completed", "result": "succeeded"},
                   {"name": "UI Automation", "state": "completed", "result": "failed"}],
        "tests": {"total": 100, "passed": 96, "failed": 4}},
    "build_verify.mrwp_local": {
        "mrwp_id": "900002",
        "stages": [{"name": "Build", "state": "completed", "result": "succeeded"},
                   {"name": "UI Automation", "state": "completed", "result": "failed"}],
        "tests": {"total": 100, "passed": 98, "failed": 2}},
    "build_verify.rc_report": {"outcome": "done", "note": "RC report emailed (test)"},  # skip live az + send
}


def _safe(mocks=None):
    """Merge the safe-agent profile with a test's specific mocks (test wins)."""
    return {**_SAFE_AGENTS, **(mocks or {})}


# Every test flow (incl. tests that build Orchestrator(CONFIG, st) directly) runs
# with the safe-agent profile so agents never hit the network — replaces the
# offline via injected inputs. Tests needing specific mocks pass mocks= explicitly.
_mocks_mod.load_mocks = lambda *a, **k: dict(_SAFE_AGENTS)


def _stub_build_defs(status):
    _rv.REGISTRY["build_defs"] = _bd_result(status)


def _pass_scout_checks(orch):
    """Record all scout-assisted (source: scout) auto checks as pass — mirrors the
    skill running the ICM + Kusto + settings + Teams checks and recording results."""
    orch.gate.record_check("oncall_now", "pass", "stubbed: not on-call")
    orch.gate.record_check("adx_access", "pass", "stubbed: can query cluster")
    orch.gate.record_check("silent_perms", "pass", "stubbed: all servers auto-approved")
    orch.gate.record_check("teams_notify", "pass", "stubbed: Scout Teams bot reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "stubbed: CCD reconciled with pipeline")


def _clear_notice(orch):
    """Record the scout-assisted `notice` step as pass (skill sent the email)."""
    orch.record_scout_step("preflight", "notice", "pass", "test: notice sent")


def _clear_lockdown(orch):
    """Record the CCOA lockdown scout-step as pass (no overlap) — mirrors the skill
    running the browser check. Needed for the phase flow to advance past this step."""
    orch.record_scout_step("preflight", "lockdown", "pass", "test: no CCOA overlap")


def _clear_phase0_scout(orch):
    """Clear ALL Phase-0 human/scout holds (notice + flight_reminder + confirm_reminders
    + lockdown) so the flow can advance out of Phase 0."""
    _clear_notice(orch)
    orch.record_scout_step("preflight", "flight_reminder", "pass", "test: reminders posted")
    orch.complete_step("preflight", "confirm_reminders", "test: owner confirmed")
    _clear_lockdown(orch)
    orch.complete_step("preflight", "vitals", "test: vitals & policy reviewed")


def _clear_ccd_scout(orch):
    """Clear the Phase-1 scout comms/trigger holds (final_reminder + pr_reminder +
    localization) so the flow can advance out of the (gateless) Phase 1. Separate
    from Phase-0 clearing so a test can still target these steps individually."""
    orch.record_scout_step("ccd", "final_reminder", "pass", "test: reminder emailed")
    orch.record_scout_step("ccd", "pr_reminder", "pass", "test: PR reminder posted")
    orch.record_scout_step("ccd", "localization", "pass", "test: localization triggered")


def _clear_early_phase0_scout(orch):
    """Clear the first two Phase-0 scout/human holds (notice + flight_reminder) but LEAVE
    confirm_reminders holding — so the flow stays IN Phase 0 with progress + a hold."""
    _clear_notice(orch)
    orch.record_scout_step("preflight", "flight_reminder", "pass", "test: reminders posted")


def _drain_phase0_scout_only(orch):
    """Run the three Phase-0 SCOUT steps (notice, flight_reminder, lockdown) — Scout's
    own automatic work — but LEAVE the human holds (confirm_reminders, vitals). This is
    the state where the daily digest is actually DUE: Scout has finished its own steps,
    and the phase is still open on the human's confirmations. (A headless `tick` can't
    reach this state on its own — the skill drains scout steps; tests simulate that.)"""
    _clear_notice(orch)
    orch.record_scout_step("preflight", "flight_reminder", "pass", "test: reminders posted")
    _clear_lockdown(orch)


def _orch(signed=True):
    """Fresh orchestrator. By default the readiness entry gate is pre-signed so
    tests can focus on the phase flow; readiness itself is tested separately."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    if signed:
        # scout-assisted checks (skill records them) + attest the rest + verify auto
        _pass_scout_checks(orch)
        orch.gate.sign()  # attest attest-items + verify python-auto
        _clear_phase0_scout(orch)   # skill-run notice + CCOA lockdown → pass
        _clear_ccd_scout(orch)      # Phase-1 comms/trigger scout steps → pass (Phase 1 is gateless)
    return st, orch


def _advance_to_first_gate(orch):
    """Now that go_test is gone, the first real GATE is Phase-3 `bug_bash.bash_done`,
    reached after the Phase-3 `ui_failures` human reminder. Drive to that reminder, clear
    it, then drive to the bash_done gate."""
    orch.run_until_gate()                                     # holds at ui_failures (reminder)
    orch.complete_step("bug_bash", "ui_failures", "test: UI failures reviewed")
    orch.run_until_gate()                                     # holds at bash_done (gate)


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
    _clear_ccd_scout(orch)
    orch.run_until_gate()
    # Phases 0, 1 and 2 have no human gate (rc_report's 90% UI gate is auto); the first
    # hold is the Phase-3 'ui_failures' human reminder.
    assert st.current_step == "ui_failures"
    assert st.status == "awaiting_action"


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
        st0 = ReleaseState(release_id="t")
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


def test_oncall_window_shows_computed_dates():
    """The windowed attest item exposes CCD-relative dates (CCD-7 .. CCD+14)."""
    from orchestrator import render
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default")
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
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    orch.gate.sign()  # attests humans, verifies auto (fails)
    assert not st.readiness_signed
    assert st.readiness_items["build_access"]["status"] == "fail"
    orch.run_until_gate()
    assert st.status == "readiness_gate"
    _stub_build_defs("pass")  # restore


def test_passing_auto_plus_attest_clears_gate():
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
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
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    orch.gate.verify()
    assert st.readiness_items["build_access"]["status"] == "pass"   # python auto ran
    assert st.readiness_items.get("oncall_now", {}).get("status", "pending") == "pending"


def test_record_check_pass_then_sign_clears_gate():
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    orch.gate.record_check("adx_access", "pass", "can query")
    orch.gate.record_check("silent_perms", "pass", "servers auto-approved")
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    orch.gate.sign()                       # everything but oncall_now satisfied
    assert not st.readiness_signed
    orch.gate.record_check("oncall_now", "pass", "not in roster")
    assert st.readiness_signed             # last item satisfied -> gate clears


def test_record_check_fail_keeps_gate_closed():
    """If the ICM check says you ARE on-call, the item fails and the gate stays shut."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
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
    st = ReleaseState(release_id="t")
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
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    orch.gate.sign()
    assert not st.readiness_signed
    orch.gate.record_check("silent_perms", "pass", "all servers auto-approved")
    assert st.readiness_signed


def test_mcp_servers_is_python_auto_item():
    """mcp_servers is a Python-verified auto item — verify() runs it (not the skill)."""
    _stub_build_defs("pass")           # also stubs mcp_servers -> pass in test REGISTRY
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    orch.gate.verify()
    assert st.readiness_items["mcp_servers"]["status"] == "pass"
    m = next(i for i in orch.gate.checklist()["items"] if i["id"] == "mcp_servers")
    assert m["verify"] == "auto" and not m["source"]   # python, not scout


def test_silent_perms_opt_out_degraded_satisfies_gate():
    """silent_perms is soft/opt-out: recording 'degraded' (user proceeds without
    silent runs) SATISFIES the gate, unlike a normal auto item where only pass counts."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    orch.gate.record_check("oncall_now", "pass", "not on-call")
    orch.gate.record_check("adx_access", "pass", "can query")
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
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
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    res = orch.gate.record_check("oncall_now", "degraded", "nope")
    assert "error" in res


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
    # auto steps that RUN before the first hold: Phase-0 breaking/cg/cron/wiki (4) +
    # Phase-2 checker_fired/orchestrator_health/mrwp_ecs/mrwp_local (4) + rc_report (scout
    # email, mocked done here) (1) + Phase-3 clone_plans/coordinate stubs (2). The Phase-1
    # scout steps are pre-recorded by _orch's _clear_ccd_scout (not "ran").
    assert sum(1 for a in actions if a.kind == "ran") == 11


def test_gate_blocks_until_approved():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    assert st.status == "holding_gate"
    orch.run_until_gate()
    assert st.status == "holding_gate"
    assert not st.is_done("bug_bash", "bash_done")


def test_approve_advances():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    orch.approve_gate("ok")
    assert st.is_done("bug_bash", "bash_done")
    orch.run_until_gate()
    assert st.current_step == "gate_watch"       # next stop after bash_done: the Phase-4 finalize gate
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
        assert st2.current_step == "bash_done"
        assert st2.readiness_signed  # readiness survives the roundtrip
        orch2 = Orchestrator(CONFIG, st2)
        orch2.approve_gate("resumed")
        assert st2.is_done("bug_bash", "bash_done")


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
    _advance_to_first_gate(orch)   # holds at bash_done
    act = orch.skip_step("bug_bash", "bash_done", "")   # no reason
    assert act.kind == "idle"
    assert not st.is_done("bug_bash", "bash_done")       # unchanged


def test_skip_advances_past_gate():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    orch.skip_step("bug_bash", "bash_done", "n/a this release")
    assert st.is_done("bug_bash", "bash_done")            # skipped counts as done
    rec = st.steps[st.key("bug_bash", "bash_done")]
    assert rec["status"] == "skipped"
    orch.run_until_gate()
    assert st.current_step == "gate_watch"                # advanced past the gate to the Phase-4 gate


def test_reopen_step():
    st, orch = _orch()
    _advance_to_first_gate(orch); orch.approve_gate("ok")
    assert st.is_done("bug_bash", "bash_done")
    orch.reopen_step("bug_bash", "bash_done")
    assert not st.is_done("bug_bash", "bash_done")        # back to pending
    orch.run_until_gate()
    assert st.current_step == "bash_done"                 # gate re-holds


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
        # cg blocks with a real-shaped reason; breaking/cron/wiki run clean
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


def test_ccd_conflict_surfaced_in_status():
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08",
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


# ---- phase time-anchoring (Phase 0 opens CCD-7) ----

def _ccd_orch(as_of):
    """Signed orchestrator with CCD=2026-07-08 (Phase 0 opens 2026-07-01)."""
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default")
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
    for sid in ("clone_plans", "coordinate"):
        orch.state.set_step("bug_bash", sid, StepState(status="done", by="test"))
    phases = {p["id"]: p for p in orch.status_report()["phases"]}
    assert phases["build_verify"]["state"] == "current" and phases["build_verify"]["current"]
    assert phases["bug_bash"]["state"] == "pending"        # stale progress ≠ a 2nd current
    assert phases["bug_bash"]["done"] == 2                 # the leftover count still shows
    assert not phases["bug_bash"]["current"]
    # exactly one phase is current
    assert sum(1 for p in phases.values() if p["state"] == "current") == 1


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
    st = ReleaseState(release_id="2026-08", ccd="2026-08-12", ccd_source="default")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 6))  # after CCD-7 but unsigned
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


def test_notify_digest_reports_gate_and_progress():
    from orchestrator import render
    st, orch = _orch()                   # signed, no CCD → phase due immediately
    orch.run_until_gate()                # Phases 0-2 gateless; holds at the Phase-3 ui_failures action
    msg = render.notification(orch.status_report())
    assert "Progress:" in msg
    assert "Action needed now" in msg    # ui_failures is the live hold
    assert "your approval" in msg        # the bash_done gate is listed among the human touchpoints


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


def test_automations_cover_every_scheduled_step():
    """SELF-ENFORCING TRACEABILITY GUARDRAIL — every step that declares a
    fire_at_local must be owned by EXACTLY ONE automation in config/automations.yaml,
    and each automation's steps must exist and share one fire time. Fails LOUDLY on
    drift so a timed step can't be added without wiring its automation."""
    from orchestrator import automations as A
    problems = A.validate(CONFIG)
    assert problems == [], "automation/step mapping drift:\n  " + "\n  ".join(problems)


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


def test_automation_prompt_delegates_to_step_module():
    """The planner is generic: a step that declares `automation_prompt` owns its bespoke
    instruction (localization trigger vs poller), and steps without one get the default
    send + record-step prompt — no step id is special-cased in automations.py."""
    from orchestrator import automations as A
    by = {a["slug"]: a for a in A.plan(CONFIG, "2026-09", "2026-09-09")["automations"]}
    # localization's module owns both bespoke prompts (delegated, not hardcoded here)
    assert "trigger localization" in by["ccd-noon"]["prompt"]
    assert "localization poller" in by["ccd-localization-poller"]["prompt"]
    # a plain multi-step reminder automation uses the generic default prompt
    assert "For EACH of these steps in order" in by["ccd-morning"]["prompt"]


def test_ccd_cron_pins_to_exact_date():
    """_ccd_cron builds a cron 'M H D Mo *' targeting the CCD's day+month+time, so a
    one-shot fires ON the CCD — never the next matching weekday (the early-fire bug)."""
    from orchestrator import automations as A
    from datetime import date
    assert A._ccd_cron(date(2026, 8, 26), "09:00") == "cron: 0 9 26 8 *"
    assert A._ccd_cron(date(2026, 8, 26), "12:00") == "cron: 0 12 26 8 *"
    assert A._ccd_cron(date(2026, 12, 9), "13:30") == "cron: 30 13 9 12 *"
    # missing/invalid inputs → None (caller falls back / skips)
    assert A._ccd_cron(None, "09:00") is None
    assert A._ccd_cron(date(2026, 8, 26), "") is None
    assert A._ccd_cron(date(2026, 8, 26), "nonsense") is None


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
    Details column shows each step's outcome; multi-line reports expand below."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-07-02")          # Phase 0 open
    orch.run_until_gate()                        # runs breaking/cg/cron/wiki (set notes)
    r = orch.status_report()
    steps = {s["id"]: s for s in r["current_steps"]}
    assert steps["cg"].get("note") and steps["wiki"].get("note")
    view = render.status_view(r)
    assert "| Details |" in view                 # the third column exists
    assert "Component Governance" in view        # cg's real report note surfaces


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
    from steps.preflight.breaking import parse_breaking
    hits = parse_breaking(_SAMPLE_CHANGELOG, section="vNext", tag="[MAJOR]")
    assert len(hits) == 1
    assert "(#2)" in hits[0] and "(#0)" not in hits[0]
    assert hits[0].startswith("[MAJOR]")          # leading "- " bullet stripped (no doubling)


def test_parse_breaking_none_when_no_major():
    from steps.preflight.breaking import parse_breaking
    txt = "vNext\n----------\n- [MINOR] x (#1)\nVersion 1.0.0\n----------\n"
    assert parse_breaking(txt) == []


def test_breaking_agent_detects_and_drafts():
    from steps.preflight import breaking, wiki, cg, cron
    from steps.preflight import breaking as _bk
    orig = _bk._fetch_text
    _bk._fetch_text = lambda *a, **k: _SAMPLE_CHANGELOG
    try:
        st = ReleaseState(release_id="2026-08")
        r = breaking.run("preflight", {"id": "breaking"}, st)
        assert r.ok
        assert "Detected 1 breaking" in r.action
        assert "(#2)" in r.action and "DRAFT COMMS" in r.action
    finally:
        _bk._fetch_text = orig


def test_breaking_agent_none_found_passes():
    from steps.preflight import breaking, wiki, cg, cron
    from steps.preflight import breaking as _bk
    orig = _bk._fetch_text
    _bk._fetch_text = lambda *a, **k: "vNext\n----------\n- [MINOR] x (#1)\nVersion 1.0.0\n"
    try:
        r = breaking.run("preflight", {"id": "breaking"},
                            ReleaseState(release_id="2026-08"))
        assert r.ok and "No breaking" in r.action
    finally:
        _bk._fetch_text = orig


def test_breaking_agent_fetch_error_holds():
    from steps.preflight import breaking, wiki, cg, cron
    from steps.preflight import breaking as _bk
    orig = _bk._fetch_text

    def _boom(*a, **k):
        raise RuntimeError("network down")
    _bk._fetch_text = _boom
    try:
        r = breaking.run("preflight", {"id": "breaking"},
                            ReleaseState(release_id="2026-08"))
        assert not r.ok and "could not fetch" in r.action
    finally:
        _bk._fetch_text = orig


def test_wiki_page_name_convention():
    from steps.preflight.wiki import _page_name
    st = ReleaseState(release_id="2026-08")
    assert _page_name(st) == "August 2026 Release"
    assert _page_name(st, 2) == "August 2026 2 Release"


def test_wiki_agent_real_create(monkeypatch=None):
    from steps.preflight import breaking, wiki, cg, cron
    from tools import checks
    orig_create, orig_exists = checks.create_wiki_page, checks.wiki_page_exists
    seen = {}

    def _fake(org, project, wiki, path, content, timeout=60):
        seen.update(path=path, project=project)
        return checks.CheckResult(True, True, f"created '{path}'")
    checks.create_wiki_page = _fake
    checks.wiki_page_exists = lambda *a, **k: False    # month page absent
    try:
        st = ReleaseState(release_id="2026-08")
        r = wiki.run("preflight", {"id": "wiki"}, st)
        assert r.ok and seen["path"].endswith("August 2026 Release")
        assert seen["project"] == "IdentityWiki"
    finally:
        checks.create_wiki_page, checks.wiki_page_exists = orig_create, orig_exists


def test_wiki_agent_duplicate_creates_numbered_and_notifies():
    """If the month's page exists, the agent leaves it alone, NOTIFIES, and
    creates the next free numbered page."""
    from steps.preflight import breaking, wiki, cg, cron
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
        st = ReleaseState(release_id="2026-08")
        r = wiki.run("preflight", {"id": "wiki"}, st)
        assert r.ok
        assert created["path"].endswith("August 2026 2 Release")
        assert "already exist" in r.action.lower() and "SECOND" in r.action
    finally:
        checks.create_wiki_page, checks.wiki_page_exists = orig_create, orig_exists


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


def test_notification_renderers_share_one_silence_gate():
    """All three digest renderers (plain/markdown/html) derive silence from the same
    _digest_model — so they're empty together and non-empty together. Guards against
    the three functions drifting apart."""
    from orchestrator import render
    from datetime import date
    # unsigned setup release → all silent
    st0 = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default")
    r0 = Orchestrator(CONFIG, st0).status_report()
    assert render.notification(r0) == "" and render.notification_markdown(r0) == "" \
        and render.notification_html(r0) == ""
    assert render._digest_model(r0) is None
    # signed + due → all non-empty, and the model caps counts (shows totals)
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default",
                      owner_email="o@x.com")
    orch = Orchestrator(CONFIG, st)
    _pass_scout_checks(orch); orch.gate.sign()
    orch.as_of = date(2026, 7, 8); orch.run_until_gate()
    _drain_phase0_scout_only(orch)          # Scout's own steps done → digest is due
    r = orch.status_report()
    m = render._digest_model(r)
    assert m is not None
    assert m["completed_total"] == len(r["active_phase"].get("completed") or [])
    assert len(m["completed"]) <= 8 and len(m["human"]) <= 6
    assert render.notification(r) and render.notification_markdown(r) and render.notification_html(r)



    """The Teams-bot markdown digest uses blank-line paragraph breaks (survive the
    Scout bot's newline collapse), `-` bullets, and bold — unlike the plain text."""
    from orchestrator import render
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default",
                      owner_email="o@x.com")
    orch = Orchestrator(CONFIG, st)
    _pass_scout_checks(orch); orch.gate.sign()
    orch.as_of = date(2026, 7, 8)
    orch.run_until_gate()
    _drain_phase0_scout_only(orch)          # Scout's own steps done → digest is due
    r = orch.status_report()
    md = render.notification_markdown(r)
    assert md and "\n\n" in md              # paragraph breaks
    assert md.startswith("**Release 2026-07 — Phase 0")
    assert "\n- " in md                     # markdown bullets
    # silent states → empty, same rule as notification()
    st2 = ReleaseState(release_id="x")       # unsigned
    assert render.notification_markdown(Orchestrator(CONFIG, st2).status_report()) == ""


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
                 "Confirm Play Console vitals", "Create release payload wiki subpage"):
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


def _bv_state(mocks):
    """A signed release positioned in Phase 2 with the given build_verify mocks."""
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26", ccd_source="confirmed")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 26), mocks=_safe(mocks))
    return st, orch


def _bv_build(orch, st, sid):
    """Build a build_verify step outcome with its mock context active (as the engine
    does), so injected inputs apply instead of hitting live az."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    with mockctx.active(orch.mocks.get(f"build_verify.{sid}", {})):
        return as_dict(_steps.get_step("build_verify", sid).build(st))


def _seed_rc_pipeline(st, ecs_ui, local_ui, *, ecs_suites=None,
                      ecs_id="1678863", local_id="1678864"):
    """Seed state.pipeline_runs with a full RC snapshot (checker + orchestrator + one RC
    pair) the way the verify steps would, so rc_report / record-rc-report read it from
    state (no live re-discovery). `ecs_ui`/`local_ui` are the UI category dicts the gate
    consumes ({total,passed,failed})."""
    from steps.build_verify import _common as K
    K.stash_checker(st, "1678599", "2026-08-13T06:00")
    K.stash_orchestrator(st, "1678611",
                         versions={"Common": "24.6.0", "Msal": "8.4.2", "Broker": "16.5.0"},
                         parked=True)

    def snap(run_id, ui, suites):
        return {"run_id": run_id, "complete": True, "ran": 23, "total": 23,
                "failed_stages": [], "yellow_stages": [], "never_ran": [],
                "tests": {"categories": {"ui": ui}}, "failed_suites": suites or []}
    K.stash_mrwp(st, "ECS", snap(ecs_id, ecs_ui, ecs_suites))
    K.stash_mrwp(st, "Local", snap(local_id, local_ui, None))


def test_build_verify_steps_pass_with_healthy_mocks():
    """With injected healthy inputs, all four build_verify agent steps return done —
    and mrwp steps surface the test summary + red/yellow counts in the note."""
    st, orch = _bv_state({})       # _safe() carries the healthy build_verify profile
    outs = {sid: _bv_build(orch, st, sid)
            for sid in ("checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local")}
    assert all(o["kind"] == "done" for o in outs.values()), outs
    assert "parked at 'Remove RC Tags'" in outs["orchestrator_health"]["note"]
    assert "ran to completion" in outs["mrwp_ecs"]["note"]
    assert "Tests:" in outs["mrwp_ecs"]["note"] and "1 red" in outs["mrwp_ecs"]["note"]


def test_build_verify_mrwp_in_flight_when_run_still_executing():
    """An MRWP run whose OVERALL status is still inProgress is NOT a failure — verify
    returns in_progress (Scout polls + re-evaluates on completion) instead of blocking it
    as an aborted pipeline. A pending stage on an in-flight run is 'not run YET'."""
    st, orch = _bv_state({"build_verify.mrwp_ecs": {
        "mrwp_id": "999", "build_status": "inProgress"}})
    out = _bv_build(orch, st, "mrwp_ecs")
    assert out["kind"] == "in_progress"
    assert "still running" in out["note"] and "poll" in out["note"].lower()
    assert out["poll_in_min"] == 30
    # a completed run still runs the normal rule (control): injected stages, no in-flight
    st2, orch2 = _bv_state({})
    assert _bv_build(orch2, st2, "mrwp_ecs")["kind"] == "done"


def test_engine_in_flight_step_holds_without_flagging_owner():
    """The engine records an in-flight agent step as status 'in_flight' (not blocked):
    it is NOT added to pending_human, the release stays 'running' (no user action), the
    drain returns a 'waiting' action, and first-seen time is stamped for the 6h nudge."""
    st, orch = _bv_state({"build_verify.mrwp_ecs": {
        "mrwp_id": "999", "build_status": "inProgress"}})
    phase = next(p for p in orch.config["phases"] if p["id"] == "build_verify")
    step = next(s for s in phase["steps"] if s["id"] == "mrwp_ecs")
    act = orch._run_auto_step(phase, step, block_holds=True)
    assert act.kind == "waiting"
    rec = st.get_step("build_verify", "mrwp_ecs")
    assert rec.status == "in_flight"
    assert "build_verify.mrwp_ecs" not in st.pending_human
    assert st.status == "running"
    assert rec.data.get("in_flight_since") and rec.data.get("poll_in_min") == 30
    assert not st.is_done("build_verify", "mrwp_ecs")     # not done → re-runs on next poll


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


def test_build_verify_orchestrator_blocks_on_failed_pregate_stage():
    """A failed pre-gate orchestrator stage (e.g. Create Release Branches) blocks."""
    st, orch = _bv_state({"build_verify.orchestrator_health": {
        "run": {"id": 42, "tags": []},
        "stages": [
            {"name": "Validate Branch and Versions availability", "state": "completed", "result": "succeeded"},
            {"name": "Create Release Branches", "state": "completed", "result": "failed"},
            {"name": "Trigger RC Testing", "state": "pending", "result": None},
        ]}})
    out = _bv_build(orch, st, "orchestrator_health")
    assert out["kind"] == "blocked"
    assert "Create Release Branches" in out["reason"]


def test_build_verify_checker_blocks_when_not_triggered():
    """checker_fired blocks when no run has a succeeded 'Trigger Monthly Release' job."""
    st, orch = _bv_state({"build_verify.checker_fired": {"triggering": None}})
    out = _bv_build(orch, st, "checker_fired")
    assert out["kind"] == "blocked" and "triggered the release" in out["reason"]


def test_build_verify_phase_shape():
    """Phase 2 has the 4 verification agent steps + the rc_report scout step (which emails
    the RC report AND applies the 90% UI gate). rc_report is the terminal step — there is
    NO separate human gate (the gate IS the decision). CCD+1 anchored."""
    import yaml as _yaml
    cfg = _yaml.safe_load(open(CONFIG, encoding="utf-8"))
    bv = next(p for p in cfg["phases"] if p["id"] == "build_verify")
    ids = [s["id"] for s in bv["steps"]]
    assert ids == ["checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local",
                   "rc_report"]
    assert bv.get("anchor") == "CCD+1"
    rc = next(s for s in bv["steps"] if s["id"] == "rc_report")
    assert rc.get("source") == "scout" and rc.get("owner") == "agent"
    assert bv["steps"][-1]["id"] == "rc_report"          # terminal Phase-2 step
    assert not any(s.get("gate") for s in bv["steps"])   # no human gate in Phase 2


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
    K.stash_orchestrator(st, "1678611",
                         versions={"Common": "24.6.0", "Msal": "8.4.2", "Broker": "16.5.0"},
                         parked=True)
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


def test_reconcile_retries_pure():
    """reconcile_retries collapses per-attempt results by title: passed if any attempt
    passed; recovered if it also failed; failed only if it never passed; NA ignored."""
    from tools import pipelines as P
    res = [
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Failed"},
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},   # flaky → recovered
        {"testCaseTitle": "testAlwaysGreen", "outcome": "Passed"},
        {"testCaseTitle": "testHardFail", "outcome": "Failed"},
        {"testCaseTitle": "testHardFail", "outcome": "Failed"},          # failed every attempt
        {"testCaseTitle": "testSkipped", "outcome": "NotExecuted"},      # ignored
    ]
    r = P.reconcile_retries(res)
    assert r["passed"] == 2 and r["failed"] == 1
    assert r["recovered"] == ["testNullDrsMetadata"]
    assert r["total"] == 3 and r["na"] == 1
    assert P.reconcile_retries([]) == {"passed": 0, "failed": 0, "recovered": [], "total": 0, "na": 0}


def test_get_test_summary_unit_retry_reconciles():
    """A UNIT run whose only failure is a flaky test that passed on retry reconciles to
    0 failed + a `recovered` warning — even though ADO's run aggregate said 1 failed.
    (The rule is unit-only; UI/instrumented use the aggregate unchanged.)"""
    from tools import pipelines as P
    runs = {"value": [{"id": 700, "name": "broker4j_UnitTests",
                       "totalTests": 5, "passedTests": 4, "notApplicableTests": 0}]}
    results = {"value": [
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Failed"},
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},
        {"testCaseTitle": "t2", "outcome": "Passed"},
        {"testCaseTitle": "t3", "outcome": "Passed"},
        {"testCaseTitle": "t4", "outcome": "Passed"},
    ]}
    orig = P._ado_rest_get
    P._ado_rest_get = lambda url, timeout: (True, runs if "buildUri" in url else results, "")
    try:
        ok, s, _ = P.get_test_summary("O", "P", 700)
        assert ok
        unit = s["categories"]["unit"]
        assert unit["failed"] == 0 and unit["recovered"] == ["testNullDrsMetadata"]
        assert unit["passed"] == 4 and unit["total"] == 4
    finally:
        P._ado_rest_get = orig


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


def test_classify_test_run_categories():
    """The test-run classifier buckets into exactly three: unit / instrumented / ui;
    anything that isn't unit/instrumented is UI ('the rest are UI', incl. Lab Api Tests)."""
    from tools import pipelines as P
    assert P.classify_test_run("common4j_UnitTests") == "unit"
    assert P.classify_test_run("common_InstrumentedTests") == "instrumented"
    assert P.classify_test_run("PROD MSAL - RC Broker (API 32)") == "ui"
    assert P.classify_test_run("RC MSAL - PROD Broker (API 28) # 123_build.1") == "ui"
    assert P.classify_test_run("Lab Api Tests") == "ui"             # NOT 'other'
    assert P.classify_test_run("") == "ui"


def test_get_failed_tests_aggregates_repeated_suites():
    """get_failed_tests merges the SAME suite that appears as several runs (the cause of
    the confusing duplicates) into one entry, summing failures and collecting test names."""
    from tools import pipelines as P
    calls = {"n": 0}
    runs = {"value": [
        {"id": 1, "name": "PROD MSAL - RC Broker (API 32) # 1678863_MRWP_main.1",
         "totalTests": 44, "passedTests": 26, "notApplicableTests": 0},   # 18 failed
        {"id": 2, "name": "LTW, RC MSAL - RC Broker (API 32) # 1678863_MRWP_main.1",
         "totalTests": 8, "passedTests": 6, "notApplicableTests": 0},     # 2 failed
        {"id": 3, "name": "LTW, RC MSAL - RC Broker (API 32) # 1678863_MRWP_main.2",
         "totalTests": 8, "passedTests": 6, "notApplicableTests": 0},     # 2 failed (same suite!)
    ]}
    results = {
        1: {"value": [{"testCaseTitle": f"test_a{i}"} for i in range(18)]},
        2: {"value": [{"testCaseTitle": "test_ltw_x"}, {"testCaseTitle": "test_ltw_y"}]},
        3: {"value": [{"testCaseTitle": "test_ltw_y"}, {"testCaseTitle": "test_ltw_z"}]},  # y dup
    }
    orig = P._ado_rest_get

    def fake(url, timeout):
        calls["n"] += 1
        if "buildUri" in url:
            return (True, runs, "")
        import re
        rid = int(re.search(r"/Runs/(\d+)/results", url).group(1))
        return (True, results[rid], "")
    P._ado_rest_get = fake
    try:
        ok, suites, _ = P.get_failed_tests("O", "P", 1678863)
        assert ok
        by = {s["name"]: s for s in suites}
        # the two LTW runs merged into one suite: 2+2 failed, total 16, names deduped
        ltw = by["LTW, RC MSAL - RC Broker (API 32)"]
        assert ltw["failed"] == 4 and ltw["total"] == 16
        assert sorted(ltw["tests"]) == ["test_ltw_x", "test_ltw_y", "test_ltw_z"]
        # sorted by failure count desc → PROD MSAL suite (18) first
        assert suites[0]["name"] == "PROD MSAL - RC Broker (API 32)" and suites[0]["failed"] == 18
    finally:
        P._ado_rest_get = orig


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
        P.mrwp_run_ids = lambda *a, **k: (True, {"ECS": 111, "Local": 222}, "", "tags")
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


def test_mrwp_run_ids_picks_newest_on_retrigger():
    """A re-triggered 'Trigger RC Testing' stage leaves MULTIPLE RC-<provider> tags on
    the orchestrator run (old + new). mrwp_run_ids must pick the NEWEST (max build id) so
    the fresh MRWP run wins over the stale failed one."""
    from tools import pipelines as P
    run = {"id": 20, "tags": ["RC-ECS=1678863", "RC-ECS=1679999",
                              "RC-Local=1678864", "RC-Local=1679000"]}
    ok, ids, _, source = P.mrwp_run_ids("O", "P", run)
    assert ok and source == "tags"
    assert ids["ECS"] == "1679999" and ids["Local"] == "1679000"
    # single tag per provider still works (no re-trigger)
    ok2, ids2, _, _ = P.mrwp_run_ids("O", "P", {"id": 1, "tags": ["RC-ECS=5", "RC-Local=6"]})
    assert ok2 and ids2 == {"ECS": "5", "Local": "6"}


def _run_poll_rc(runs_root, rid, now_iso):
    """Run `poll-rc` with the drain stubbed (no live az) and return the decision dict."""
    import io, contextlib, json as _json
    from unittest.mock import patch
    from orchestrator.commands import rc_poll as RP

    class A:
        runs_root = None; release = None; config = CONFIG; as_of = None; now = None
    A.runs_root, A.release, A.now = runs_root, rid, now_iso
    buf = io.StringIO()
    with patch("orchestrator.engine.Orchestrator.run_until_gate", lambda self, **k: []):
        with contextlib.redirect_stdout(buf):
            rc = RP.cmd_poll_rc(A)
    assert rc == 0
    return _json.loads(buf.getvalue().strip().splitlines()[-1])


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


def test_build_verify_persists_pipeline_run_ids():
    """The build_verify steps stash the checker/orchestrator/MRWP runs onto
    state.pipeline_runs in the nested RC schema, and it round-trips through save/load."""
    import tempfile
    from orchestrator import cli_common as _C
    st, orch = _bv_state({})
    for sid in ("checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local"):
        _bv_build(orch, st, sid)
    pr = st.pipeline_runs
    assert pr["checker"]["run_id"] == "1678599"
    assert pr["orchestrator"]["run_id"] == "1678611"
    assert pr["orchestrator"]["versions"].get("Broker") == "1.0.0"
    rc = pr["rcs"][-1]
    assert rc["rc"] == 1 and rc.get("resolved_at")
    assert rc["ecs"]["run_id"] == "900001" and rc["local"]["run_id"] == "900002"
    assert rc["ecs"]["complete"] and rc["ecs"]["tests"]["failed"] == 4   # snapshot stored
    with tempfile.TemporaryDirectory() as tmp:
        _C.save_state(st, tmp, "2026-08")
        again = _C.load_state(tmp, "2026-08")
        assert again.pipeline_runs["rcs"][-1]["ecs"]["run_id"] == "900001"


def test_migrate_pipeline_runs_flat_to_nested():
    """A legacy FLAT pipeline_runs shape migrates to the nested RC schema on load
    (idempotent); an already-nested value passes through unchanged."""
    from orchestrator.state import migrate_pipeline_runs as M
    flat = {"checker": "111", "orchestrator": "222",
            "versions": "Common 24.6.0, Msal 8.4.2, Broker 16.5.0",
            "mrwp_ecs": "333", "mrwp_local": "444", "mrwp_id_source": "tags",
            "resolved_at": "2026-08-20T00:00:00Z"}
    m = M(flat)
    assert m["checker"]["run_id"] == "111"
    assert m["orchestrator"]["run_id"] == "222"
    assert m["orchestrator"]["versions"] == {"Common": "24.6.0", "Msal": "8.4.2", "Broker": "16.5.0"}
    assert m["rcs"] == [{"rc": 1, "resolved_at": "2026-08-20T00:00:00Z",
                         "ecs": {"run_id": "333", "id_source": "tags"},
                         "local": {"run_id": "444", "id_source": "tags"}}]
    assert M(m) == m            # idempotent
    assert M({}) == {}


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
               ("notice", "confirm_reminders", "vitals", "wiki"))
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
    assert st.is_done("preflight", "wiki") and st.is_done("ccd", "localization")
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


def test_sim_gate_mode_on_gateless_phase_reports_problem():
    """`at: gate` on a phase with no gate step (partner) doesn't hang — it completes the
    phase and reports that the gate was never reached (reached=False)."""
    import tempfile
    from orchestrator import sim as SIM
    scenario = {"name": "t_nogate", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD+30", "data": "mock",
                "target": {"phase": "partner", "at": "gate"}}
    with tempfile.TemporaryDirectory() as tmp:
        res = SIM.run_scenario(scenario, runs_root=tmp)
    assert not res.reached
    assert any("no gate" in p for p in res.problems)


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


def test_schedule_today_uses_owner_timezone():
    """schedule.today() is the date in the owner's zone (PT), not the host's."""
    from datetime import datetime
    from orchestrator import schedule
    assert schedule.today() == datetime.now(schedule.get_tz()).date()


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


def test_timed_step_gated_until_fire_time():
    """A step with a fire_at_local (the 09:00 CCD comms) is excluded from scout_pending
    until 09:00 owner-local on its fire day — so the every-hour worker can't drain it
    early — then becomes pending at/after 09:00."""
    import yaml as _yaml
    from datetime import datetime
    from orchestrator.state import StepState
    from orchestrator import schedule
    tz = schedule.get_tz()
    cfg = _yaml.safe_load(open(CONFIG, encoding="utf-8"))
    pf_steps = [s["id"] for p in cfg["phases"] if p["id"] == "preflight" for s in p["steps"]]

    def _ccd_active(now_dt):
        st = ReleaseState(release_id="2026-08", ccd="2026-08-26",
                          ccd_source="confirmed", readiness_signed=True)
        for sid in pf_steps:                       # Phase 0 done ⇒ ccd is the active phase
            st.set_step("preflight", sid, StepState(status="done"))
        return Orchestrator(CONFIG, st, now=now_dt, mocks={})

    # 08:00 on CCD day — phase is due, but the 09:00 comms are NOT yet runnable.
    r = _ccd_active(datetime(2026, 8, 26, 8, 0, tzinfo=tz)).status_report()
    assert r["active_phase"]["id"] == "ccd" and r["active_phase"]["due"]
    assert "final_reminder" not in r["scout_pending"]
    assert "pr_reminder" not in r["scout_pending"]

    # 09:00 — the 9am comms are now runnable; the noon localization still isn't.
    r2 = _ccd_active(datetime(2026, 8, 26, 9, 0, tzinfo=tz)).status_report()
    assert "final_reminder" in r2["scout_pending"]
    assert "localization" not in r2["scout_pending"]           # fires at noon

    # a day LATER (missed) — a timed step runs ASAP (catch-up), no longer gated.
    r3 = _ccd_active(datetime(2026, 8, 27, 1, 0, tzinfo=tz)).status_report()
    assert "final_reminder" in r3["scout_pending"]


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
    """Scout steps are the SKILL's work: they surface in `scout_pending` (for the
    skill to run via step-action), NOT as the user's `current_step`/`action`. Clearing
    them drains the list; an attest step (confirm_reminders) becomes a real user hold."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()                 # NOTE: no scout steps cleared yet
    orch.run_until_gate()
    assert st.status == "awaiting_action"
    rep = orch.status_report()
    # the three scout steps are pending FOR THE SKILL — not surfaced as a user action
    assert set(rep["scout_pending"]) == {"notice", "flight_reminder", "lockdown"}
    assert (rep["action"] or {}).get("step") not in ("notice", "flight_reminder", "lockdown")
    assert "preflight.notice" in st.pending_human
    # the skill runs each scout step (record_scout_step = its step-action + record-step)
    _clear_notice(orch)
    orch.run_until_gate()
    assert "notice" not in orch.status_report()["scout_pending"]
    orch.record_scout_step("preflight", "flight_reminder", "pass", "posted")
    orch.run_until_gate()
    assert "flight_reminder" not in orch.status_report()["scout_pending"]
    # confirm_reminders (attest, dep on flight_reminder now done) is a genuine user hold
    assert "preflight.confirm_reminders" in st.pending_human
    orch.complete_step("preflight", "confirm_reminders", "owner confirmed")
    orch.record_scout_step("preflight", "lockdown", "pass", "no overlap")
    orch.run_until_gate()
    assert orch.status_report()["scout_pending"] == []   # all scout work drained


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


def test_check_lockdown_pass_and_attention():
    """check-lockdown records pass when nothing overlaps and holds (attention)
    when a Production CCOA overlaps the release window."""
    import tempfile as _tf, json as _json
    from orchestrator.commands import lockdown as lk
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-12", ccd_source="default")
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


def test_notice_build_real_recipients_and_html():
    """Real send: notice.build targets the REAL DL and renders a proper <table> +
    clean hotfix anchor. (Redirect to yourself is the send_to mock knob, not automatic.)"""
    from steps.preflight import notice
    from steps.lib import templating as T
    st = ReleaseState(release_id="2026-08", ccd="2026-08-12", ccd_source="default",
                      owner_email="pedroro@microsoft.com", owner_name="Pedro")
    out = notice.build(st)
    html = out.payload["body"]
    assert "<table" in html and "</table>" in html
    assert ">the hotfix cherry-pick guide</a>" in html          # clean anchor, no raw URL
    assert "August" in html and "08/12/2026" in html and "@pedroro" in html
    assert "androididentity@microsoft.com" in out.payload["to"]  # real DL by default
    assert not out.payload["subject"].startswith("[TEST")
    assert T.ordinal(12) == "12th" and T.ordinal(21) == "21st"


def test_no_localization_strings_step():
    """The old #5 localization strings step was removed."""
    st, orch = _orch()
    preflight = next(p for p in orch.config["phases"] if p["id"] == "preflight")
    assert "strings" not in [s["id"] for s in preflight["steps"]]


# ---- Phase 1 (ccd) step modules ----

def _ccd_state():
    return ReleaseState(release_id="2026-09", ccd="2026-09-09", ccd_source="default",
                        owner_email="pedroro@microsoft.com", owner_name="Pedro")


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
    assert "<table" in html and "September" in html and "@pedroro" in html


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


# ---- localization trigger → poll → complete/timeout state machine ----

def _loc_state(started_min_ago=None, build_id="177219192"):
    from datetime import datetime, timezone, timedelta
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09",
                      owner_email="pedroro@microsoft.com", owner_name="Pedro")
    if started_min_ago is not None:
        start = datetime.now(timezone.utc) - timedelta(minutes=started_min_ago)
        step = st.get_step("ccd", "localization")
        step.data["started_at"] = start.isoformat()
        if build_id:
            step.data["build_id"] = build_id     # so decide() can attach the run proof link
        st.set_step("ccd", "localization", step)
    return st


_PR_LOG = "2026-09-09T20:00:00 blah\nPull request created with ID '16790317'\nmore lines"
# The real OneLocBuild@3 log prints the full PR URL after the id (build 176407869):
_PR_LOG_WITH_URL = ("OneLocBuildClient.exe Information: 0 : Pull request created with ID "
                    "'16790317': https://msazure.visualstudio.com/DefaultCollection/One/"
                    "_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/16790317")


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
    from steps.preflight.cg import _cg_summary, _cg_report
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


def test_cg_agent_blocks_on_high():
    """High/Critical active alerts BLOCK the step (ok=False) with a fix-and-rerun message."""
    from steps.preflight import breaking, wiki, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (True, [
        {"alertState": "active", "severity": "high", "title": "CVE-9",
         "component": {"displayName": "pkg", "displayVersion": "1.0"}},
    ], "ok")
    try:
        r = cg.run("preflight", {"id": "cg"}, None)
        assert not r.ok                       # High → blocks
        assert "CVE-9" in r.action and "RERUN" in r.action
    finally:
        checks.fetch_cg_alerts = orig


def test_cg_agent_passes_when_no_high():
    """Only Medium/Low active alerts → the step passes (report captured)."""
    from steps.preflight import breaking, wiki, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (True, [
        {"alertState": "active", "severity": "medium", "title": "CVE-M"},
    ], "ok")
    try:
        r = cg.run("preflight", {"id": "cg"}, None)
        assert r.ok and "1 active" in r.action
    finally:
        checks.fetch_cg_alerts = orig


def test_cg_blocked_step_reruns_and_clears_when_fixed():
    """A CG block holds the step; fixing (alerts now clean) + rerunning `next`
    re-checks and lets the flow continue."""
    st, orch = _mock_orch({"preflight.cg": {"outcome": "blocked",
                          "reason": "CG: 1 critical active\n→ Fix and RERUN or skip."}})
    _clear_phase0_scout(orch)          # clear the earlier scout/human holds
    orch.run_until_gate()
    assert st.status == "awaiting_action" and st.current_step == "cg"
    assert st.get_step("preflight", "cg").status == "blocked"
    # the digest shows it blocked / needs owner
    step = next(s for s in orch.status_report()["active_phase"]["steps"] if s["id"] == "cg")
    assert step["status"] == "blocked" and step["needs_owner"]
    # FIX: alerts now clean → RERUN (next) re-checks and passes
    orch.mocks["preflight.cg"] = {"outcome": "done", "note": "CG: 0 active alerts."}
    orch.run_until_gate()
    assert st.is_done("preflight", "cg")


def test_cg_blocked_step_skip_override():
    """The owner can override a CG block by skipping the step (with a reason)."""
    st, orch = _mock_orch({"preflight.cg": {"outcome": "blocked", "reason": "CG: 1 high active"}})
    _clear_phase0_scout(orch)
    orch.run_until_gate()
    assert st.current_step == "cg" and st.get_step("preflight", "cg").status == "blocked"
    orch.skip_step("preflight", "cg", "accepted risk; tracked separately")
    assert st.is_done("preflight", "cg")   # skipped counts as done
    assert st.get_step("preflight", "cg").status == "skipped"


def test_cg_agent_fetch_error_holds():
    from steps.preflight import breaking, wiki, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (False, [], "403 forbidden")
    try:
        r = cg.run("preflight", {"id": "cg"}, None)
        assert not r.ok and "could not read alerts" in r.action
    finally:
        checks.fetch_cg_alerts = orig


def test_cron_check_passes_on_recent_scheduled_run():
    from steps.preflight import breaking, wiki, cg, cron
    from tools import checks
    from datetime import datetime, timezone
    orig = checks.latest_scheduled_build
    now_iso = datetime.now(timezone.utc).isoformat()
    checks.latest_scheduled_build = lambda *a, **k: (True, {
        "queueTime": now_iso, "result": "succeeded", "status": "completed"}, "ok")
    try:
        r = cron.run("preflight", {"id": "cron"}, None)
        assert r.ok and "scheduled and firing" in r.action
    finally:
        checks.latest_scheduled_build = orig


def test_cron_check_blocks_when_stale():
    from steps.preflight import breaking, wiki, cg, cron
    from tools import checks
    orig = checks.latest_scheduled_build
    checks.latest_scheduled_build = lambda *a, **k: (True, {
        "queueTime": "2026-01-01T06:00:00Z", "result": "succeeded", "status": "completed"}, "ok")
    try:
        r = cron.run("preflight", {"id": "cron"}, None)
        assert not r.ok and "stale" in r.action
    finally:
        checks.latest_scheduled_build = orig


def test_cron_check_blocks_when_no_scheduled_run():
    from steps.preflight import breaking, wiki, cg, cron
    from tools import checks
    orig = checks.latest_scheduled_build
    checks.latest_scheduled_build = lambda *a, **k: (True, None, "no scheduled runs in recent history")
    try:
        r = cron.run("preflight", {"id": "cron"}, None)
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


# ---- local step mocks (personal mocks.local.yaml, injected as mocks=) --------
def _mock_orch(mocks, as_of="2026-07-02"):
    """Signed, Phase-0-open orchestrator with an explicit mocks map (isolated from
    any developer's mocks.local.yaml)."""
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default")
    orch = Orchestrator(CONFIG, st, as_of=date(*[int(x) for x in as_of.split("-")]), mocks=_safe(mocks))
    _pass_scout_checks(orch)
    orch.gate.sign()
    return st, orch


def test_local_mock_blocks_agent_step():
    """A mock `outcome: blocked` replaces a real agent step and holds it; other
    (unmocked) steps still run for real."""
    st, orch = _mock_orch({"preflight.cg": {"outcome": "blocked", "reason": "mocked: boom"}})
    orch.run_until_gate()
    assert st.get_step("preflight", "cg").status == "blocked"
    assert "preflight.cg" in st.pending_human
    assert st.get_step("preflight", "cg").note == "mocked: boom"
    assert st.is_done("preflight", "wiki")            # unmocked agent ran for real


def test_local_mock_completes_scout_step():
    """A mock `outcome: done` on a scout step auto-resolves it during `next` — the
    skill is never asked to send — while other scout steps still hold."""
    st, orch = _mock_orch({"preflight.notice": {"outcome": "done", "note": "mocked send"}})
    orch.run_until_gate()
    assert st.is_done("preflight", "notice")
    assert "preflight.notice" not in st.pending_human
    assert st.get_step("preflight", "notice").note == "mocked send"
    assert "preflight.flight_reminder" in st.pending_human   # unmocked scout still holds


def test_local_mock_never_mocks_a_gate():
    """Gate steps are not mockable — a gate still holds for a real decision even if
    someone lists it in the mock file."""
    st, orch = _mock_orch({"bug_bash.bash_done": {"outcome": "done"}}, as_of="2026-07-09")
    _clear_phase0_scout(orch)          # clear Phase-0 holds
    _clear_ccd_scout(orch)             # clear Phase-1 scout comms (Phase 1 is gateless)
    _advance_to_first_gate(orch)       # Phases 0-2 gateless; clear ui_failures → hold at bash_done
    assert not st.is_done("bug_bash", "bash_done")
    assert st.status == "holding_gate"


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


def test_readiness_mock_clears_auto_gate_offline():
    """`readiness.<item>` mocks force the entry-gate AUTO checks (real ADO/config/
    MCP) pass/fail without the real calls — lets a test clear the gate offline."""
    from datetime import date
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default")
    mocks = {f"readiness.{i}": {"outcome": "pass"} for i in
             ("build_access", "mcp_servers", "silent_perms", "adx_access", "oncall_now", "teams_notify")}
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 7, 2), mocks=mocks)
    orch.gate.verify()                        # no _stub_build_defs → real verifier bypassed
    items = st.readiness_items
    assert items["build_access"]["status"] == "pass"      # python-auto mocked
    assert items["oncall_now"]["status"] == "pass"        # scout-auto mocked too


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
    """Durable links (wiki page, CG alerts) are stored as structured StepState.links
    — not just embedded in note text — and surface in the Details column."""
    from datetime import date
    from orchestrator import render
    from tools import checks
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", ccd_source="default",
                      owner_email="pedroro@microsoft.com")
    mocks = _safe({"preflight.cg": {"alerts": [
        {"alertState": "active", "severity": "high", "title": "CVE-X",
         "url": "https://ado/alert/1"}]}})
    # let wiki.build run its real link logic offline
    checks.wiki_page_exists = lambda *a, **k: False
    checks.create_wiki_page = lambda *a, **k: checks.CheckResult(True, True, "created")
    mocks.pop("preflight.wiki", None)               # unmock wiki → real build (link)
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 9, 2), mocks=mocks)
    _pass_scout_checks(orch); orch.gate.sign()
    orch.run_until_gate()
    steps = {s["id"]: s for s in orch.status_report()["current_steps"]}
    # CG: config alerts page + the per-alert deep link, both stored
    cg_urls = [l["url"] for l in steps["cg"]["links"]]
    assert "https://ado/alert/1" in cg_urls and any("_componentGovernance" in u for u in cg_urls)
    # wiki: its page url stored as a structured link
    assert steps["wiki"]["links"] and "pagePath=" in steps["wiki"]["links"][0]["url"]
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
                "breaking", "cg", "vitals", "cron", "wiki"):
        assert kb.get_knowledge("preflight", sid), sid
    # a step with no entry → None (skill says "no knowledge yet", doesn't invent)
    assert kb.get_knowledge("monitor", "adoption") is None


def test_gate_knowledge_covers_every_readiness_item():
    """GUARDRAIL: every entry-gate readiness item has a knowledge entry (readiness.<id>)
    so `gate-info` can answer gate questions accurately instead of guessing."""
    import yaml
    from orchestrator import knowledge as kb
    items = [x["id"] for x in yaml.safe_load(open(
        os.path.join(os.path.dirname(CONFIG), "readiness.yaml"), encoding="utf-8"))["items"]]
    assert items, "no readiness items loaded"
    for iid in items:
        k = kb.get_knowledge("readiness", iid)
        assert k and k.get("summary") and k.get("what"), f"missing gate knowledge for {iid}"
    # spot-check specifics we curated
    ba = kb.get_knowledge("readiness", "build_access")
    assert any("Broker" in w for w in ba["where"]) and any("Authenticator" in w for w in ba["where"])
    pc = kb.get_knowledge("readiness", "play_console_access")
    assert any("google-play-console" in l["url"] for l in pc.get("links", []))


def test_gate_info_command_renders_and_handles_unknown():
    """`gate-info --item <id>` prints the rendered knowledge; unknown item is honest."""
    import io, contextlib
    from orchestrator.commands import step_action as sa

    class A:
        item = "oncall_now"; json = False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sa.cmd_gate_info(A)
    out = buf.getvalue()
    assert "readiness.oncall_now" in out and "primary" in out.lower()

    class B:
        item = "does_not_exist"; json = False
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        sa.cmd_gate_info(B)
    assert "No knowledge entry yet" in buf2.getvalue()


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")
