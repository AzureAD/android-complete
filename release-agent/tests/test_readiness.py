"""Release-agent tests — readiness. Shared harness in tests/_harness.py."""
from unittest.mock import patch

import pytest

from tests._harness import *  # noqa: F401,F403




# ---- readiness entry gate ----

def test_entry_gate_blocks_before_signing():
    st, orch = _orch(signed=False)
    actions = orch.run_until_gate()
    assert actions[-1].kind == "readiness"
    assert orch.status_report()["status"] == "readiness_gate"
    # nothing ran
    assert sum(1 for a in actions if a.kind == "ran") == 0
    assert not st.is_done("preflight", "notice")




def test_signing_clears_entry_gate():
    st, orch = _orch(signed=False)
    orch.run_until_gate()
    assert orch.status_report()["status"] == "readiness_gate"
    _pass_scout_checks(orch)
    orch.gate.sign()
    assert orch.gate.signed
    _clear_phase0_scout(orch)
    _clear_ccd_scout(orch)
    orch.run_until_gate()
    report = orch.status_report()
    # Phases 0, 1 and 2 have no human gate (rc_report's 90% UI gate is auto); the first
    # hold is the Phase-3 'ui_failures' human reminder.
    assert report["current_step"] == "ui_failures"
    assert report["status"] == "awaiting_action"




def test_partial_sign_does_not_clear():
    st, orch = _orch(signed=False)
    orch.gate.sign(["yubikey"])          # only one attest item
    assert not orch.gate.signed
    orch.run_until_gate()
    assert orch.status_report()["status"] == "readiness_gate"




def test_sign_records_evidence_note():
    """Attestations carry the engineer's confirmation as evidence (note)."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign(["play_console_access", "oncall_window", "saw_ame", "yubikey"],
                   note="engineer confirmed all four")
    assert orch.gate.signed
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
        _, orch_after = _C.load_orch(tmp, "t", CONFIG)
        assert not orch_after.gate.signed




def test_decline_any_item_blocks_gate():
    """Every item is required — declining ANY item blocks the gate."""
    st, orch = _orch(signed=False)
    orch.gate.decline(["yubikey"])
    assert orch.gate.blocked
    assert "yubikey" in orch.gate.blocked_item_ids
    actions = orch.run_until_gate()
    assert actions[-1].kind == "blocked"
    assert orch.status_report()["status"] == "blocked"
    assert not orch.gate.signed
    assert not st.is_done("preflight", "notice")




def test_decline_nonhard_item_also_blocks():
    """No hard/soft distinction — declining on-call blocks just the same."""
    st, orch = _orch(signed=False)
    orch.gate.decline(["oncall_now"])
    assert orch.gate.blocked
    assert "oncall_now" in orch.gate.blocked_item_ids


def test_readiness_failure_revokes_signature_and_recovery_clears_block():
    st, orch = _orch()
    assert orch.gate.signed
    orch.gate.record_check("oncall_now", "fail", "now on-call")
    assert not orch.gate.signed
    assert orch.gate.signed_at is None

    orch.gate.record_check("oncall_now", "pass", "not on-call")
    assert orch.gate.signed

    orch.gate.decline(["yubikey"])
    assert orch.gate.blocked and not orch.gate.signed
    orch.gate.sign(["yubikey"], note="replacement key acquired")
    assert not orch.gate.blocked
    assert orch.gate.blocked_item_ids == []
    assert orch.gate.signed




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




def test_failing_auto_keeps_gate_closed():
    """An auto check that FAILS is not satisfiable by attestation — gate stays shut."""
    _stub_build_defs("fail")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    orch.gate.sign()  # attests humans, verifies auto (fails)
    assert not orch.gate.signed
    assert st.readiness_items["build_access"]["status"] == "fail"
    orch.run_until_gate()
    assert orch.status_report()["status"] == "readiness_gate"
    _stub_build_defs("pass")  # restore




def test_passing_auto_plus_attest_clears_gate():
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    _pass_scout_checks(orch)   # scout-assisted (ICM + Kusto)
    orch.gate.sign()
    assert orch.gate.signed
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
    orch.gate.record_check("mail_fallback_live", "pass", "Mail fallback loaded")
    orch.gate.record_check("silent_perms", "pass", "servers auto-approved")
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    orch.gate.sign()                       # everything but oncall_now satisfied
    assert not orch.gate.signed
    orch.gate.record_check("oncall_now", "pass", "not in roster")
    assert orch.gate.signed             # last item satisfied -> gate clears




def test_record_check_fail_keeps_gate_closed():
    """If the ICM check says you ARE on-call, the item fails and the gate stays shut."""
    _stub_build_defs("pass")
    st = ReleaseState(release_id="t")
    orch = Orchestrator(CONFIG, st)
    orch.gate.sign()
    orch.gate.record_check("oncall_now", "fail", "you are on-call this rotation")
    assert not orch.gate.signed
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
    assert sp["required_servers"] == ["shell", "workiq", "playwright", "kusto", "icm", "mail"]
    mail = next(i for i in orch.gate.checklist()["items"]
                if i["id"] == "mail_fallback_live")
    assert mail["verify"] == "auto" and mail["source"] == "scout"
    assert mail["required_tools"] == ["microsoft_mail-SendEmailWithAttachments"]
    # everything else satisfied but silent_perms → gate still closed
    orch.gate.record_check("oncall_now", "pass", "not on-call")
    orch.gate.record_check("adx_access", "pass", "can query")
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    orch.gate.sign()
    assert not orch.gate.signed
    orch.gate.record_check("mail_fallback_live", "pass", "exact tool loaded")
    assert not orch.gate.signed
    orch.gate.record_check("silent_perms", "pass", "all servers auto-approved")
    assert orch.gate.signed


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
    orch.gate.record_check("mail_fallback_live", "pass", "Mail fallback loaded")
    orch.gate.record_check("teams_notify", "pass", "teams reachable")
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    # user opts out of silent runs -> degraded, but the gate still clears on sign
    res = orch.gate.record_check("silent_perms", "degraded", "proceeding without silent runs")
    assert "error" not in res
    sp = next(i for i in orch.gate.checklist()["items"] if i["id"] == "silent_perms")
    assert sp["status"] == "degraded" and sp["satisfied"]
    orch.gate.sign()
    assert orch.gate.signed




def test_gate_blocks_until_approved():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    assert orch.status_report()["status"] == "holding_gate"
    orch.run_until_gate()
    assert orch.status_report()["status"] == "holding_gate"
    assert not st.is_done("bug_bash", "bugbash_complete")




def test_skip_cannot_bypass_gate():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    act = orch.skip_step("bug_bash", "bugbash_complete", "n/a this release")
    assert act.kind == "idle"
    assert "gate steps require approve or deny" in act.message
    assert not st.is_done("bug_bash", "bugbash_complete")
    orch.run_until_gate()
    assert orch.status_report()["current_step"] == "bugbash_complete"


def test_done_cannot_bypass_gate():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    act = orch.complete_step("bug_bash", "bugbash_complete", "done")
    assert act.kind == "idle"
    assert "gate steps require approve or deny" in act.message
    assert not st.is_done("bug_bash", "bugbash_complete")
    assert st.gate_decisions == []


def test_generic_record_and_reservation_cannot_bypass_gate():
    from orchestrator.outcomes import NeedsSkill

    st, orch = _orch()
    _advance_to_first_gate(orch)
    with pytest.raises(ValueError, match="record-step cannot complete a human gate"):
        orch.record_scout_step("bug_bash", "bugbash_complete", "pass")
    reserved = orch.reserve_step(
        "bug_bash",
        "bugbash_complete",
        NeedsSkill(tool="workiq_send_email", record_as="bugbash_complete"),
        "test-executor",
    )
    assert reserved.kind == "blocked"
    assert "Gate steps cannot be reserved" in reserved.reason
    assert not st.is_done("bug_bash", "bugbash_complete")
    assert st.gate_decisions == []


def test_terminal_gate_without_approval_is_projected_pending():
    from orchestrator.state import StepState

    st, orch = _orch()
    _advance_to_first_gate(orch)
    st.set_step(
        "bug_bash",
        "bugbash_complete",
        StepState(status="skipped", completed_at="legacy", note="old override", by="human"),
    )
    reloaded = Orchestrator(CONFIG, st, mocks={})
    record = st.get_step("bug_bash", "bugbash_complete")
    assert record.status == "skipped"
    assert record.note == "old override"
    assert not reloaded._step_complete("bug_bash", "bugbash_complete")
    assert reloaded.status_report()["status"] == "holding_gate"
    assert reloaded.run_until_gate()[-1].kind == "gate"
    assert reloaded.status_report()["current_step"] == "bugbash_complete"


def test_skip_cannot_target_future_non_gate_step():
    st, orch = _orch()
    _advance_to_first_gate(orch)
    act = orch.skip_step("finalize", "integ_prs", "done elsewhere")
    assert act.kind == "idle"
    assert "not currently eligible" in act.message
    assert not st.is_done("finalize", "integ_prs")


def test_cli_done_and_skip_fail_without_mutating_gate(tmp_path, capsys):
    from orchestrator import cli

    rid = "2026-08"
    st, orch = _orch()
    st.release_id = rid
    _advance_to_first_gate(orch)
    C.save_state(st, str(tmp_path), rid)
    path = tmp_path / rid / "release-state.json"
    before = path.read_bytes()
    base = ["--runs-root", str(tmp_path)]
    target = ["--release", rid, "--phase", "bug_bash", "--step", "bugbash_complete"]

    assert cli.main(base + ["skip"] + target + ["--reason", "not needed"]) == 1
    assert "gate steps require approve or deny" in capsys.readouterr().out
    assert path.read_bytes() == before

    assert cli.main(base + ["done"] + target + ["--note", "done"]) == 1
    assert "gate steps require approve or deny" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_reminder_is_not_a_gate():
    from orchestrator.engine import Orchestrator as _O
    st, orch = _orch()
    # ui_failures is a reminder; flag_freeze is a gate
    assert _O._is_reminder({"kind": "human_action"}) is True
    assert _O._is_reminder({"kind": "approval_gate"}) is False
    assert _O._is_reminder({"owner": "agent"}) is False




def test_notify_unsigned_readiness_is_silent():
    """Readiness is interactive setup — it must NOT push (reversed from before)."""
    from orchestrator import render
    from datetime import date
    _stub_build_defs("pass")
    st = ReleaseState(release_id="2026-08", ccd="2026-08-12", ccd_source="default")
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 8, 6))  # after CCD-7 but unsigned
    assert render.notification(orch.status_report()) == ""




def test_notify_digest_reports_gate_and_progress():
    from orchestrator import render
    st, orch = _orch()                   # signed, no CCD → phase due immediately
    orch.run_until_gate()                # Phases 0-2 gateless; holds at the Phase-3 ui_failures action
    msg = render.notification(orch.status_report())
    assert "Progress:" in msg
    assert "Action needed now" in msg    # ui_failures is the live hold
    assert "your approval" in msg        # the bugbash_complete gate is listed among the human touchpoints




def test_automation_prompt_delegates_to_step_module():
    """The planner is generic: a step that declares `automation_prompt` owns its bespoke
    instruction (localization trigger vs poller), and steps without one get the default
    send + record-step prompt — no step id is special-cased in automations.py."""
    from orchestrator import automations as A
    by = {a["slug"]: a for a in A.plan(CONFIG, "2026-09", "2026-09-09")["automations"]}
    # localization's module owns both bespoke prompts (delegated, not hardcoded here)
    assert "launch-localization" in by["ccd-noon"]["prompt"]
    assert "--auto-approve" in by["ccd-noon"]["prompt"]
    assert "--review-hash" not in by["ccd-noon"]["prompt"]
    assert "--approved-by" not in by["ccd-noon"]["prompt"]
    assert "localization poller" in by["ccd-localization-poller"]["prompt"]
    # a plain multi-step reminder automation uses the generic default prompt
    assert "For EACH of these steps in order" in by["ccd-morning"]["prompt"]


def test_digest_uses_distinct_same_day_hold_ids():
    from orchestrator.commands.notify import digest_logical_id
    base = {"current_phase": "ccd", "pending_human": []}
    assert digest_logical_id(base, "2026-09-17") == "digest:2026-09-17:phase:ccd"
    action = {**base, "action": {"phase": "ccd", "step": "localization", "reason": "approval"}}
    gate = {**base, "gate": {"phase": "finalize", "step": "publish_notes_gate", "kind": "gate"}}
    assert digest_logical_id(action, "2026-09-17") != digest_logical_id(base, "2026-09-17")
    assert digest_logical_id(gate, "2026-09-17") != digest_logical_id(base, "2026-09-17")


def test_digest_contacts_owner_for_human_hold_after_scout_work_is_drained():
    from orchestrator import render
    report = {
        "release_id": "r",
        "status": "awaiting_action",
        "readiness_signed": True,
        "halted": False,
        "blocked": False,
        "gate": None,
        "action": {"step_name": "Approve thing"},
        "pending_human": ["phase.approve"],
        "scout_pending": [],
        "pipeline_runs": {},
        "active_phase": {
            "num": 1,
            "name": "Phase",
            "due": True,
            "started": True,
            "done": 1,
            "total": 2,
            "completed": [],
            "outstanding": [],
            "steps": [],
        },
    }
    assert "Action needed now: Approve thing" in render.notification(report)


def test_status_headline_prefers_gate_over_scout_pending():
    from orchestrator import render
    report = {
        "release_id": "r",
        "done": 1,
        "total": 2,
        "percent": 50,
        "status": "holding_gate",
        "ccd": "2026-09-17",
        "ccd_source": "manual",
        "as_of": "2026-09-17",
        "owner_email": "owner@example.com",
        "invariant_violations": [],
        "halted": False,
        "blocked": False,
        "readiness_signed": True,
        "scheduled": None,
        "action": None,
        "gate": {"phase": "finalize", "phase_name": "Finalize", "step": "publish", "step_name": "Publish gate"},
        "scout_pending": ["wiki_payload"],
        "phases": [],
        "current_steps": [{
            "id": "wiki_payload",
            "name": "Create wiki",
            "state": "scout",
            "note": None,
            "gate": False,
            "reminder": False,
            "links": [],
            "execution": {},
        }],
    }
    view = render.status_view(report)
    assert "Next: your decision" in view
    assert "Scout action pending" not in view




@pytest.mark.real_revision
def test_concurrent_record_check_both_persist(monkeypatch):
    """Two record-check CLI invocations fired at the same instant must BOTH
    persist — the per-release lock prevents the last-writer-wins clobber that
    dropped `notice` in the live test (parallel state-write race)."""
    import threading
    from orchestrator import cli as _cli
    from tools import checks
    monkeypatch.setattr(checks, "read_pipeline_variable", lambda *a, **k: (False, None, "offline"))
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




def test_auth_gate_clean_when_both_suites_pass():
    from steps.build_verify import auth_ecs
    g = auth_ecs.auth_gate(_auth_suites(96.0, 100.0))
    assert g["verdict"] == "clean" and g["blocking"] is False
    assert "clear the 90% bar" in g["detail"]




def test_auth_gate_blocks_when_a_suite_below_threshold():
    from steps.build_verify import auth_ecs
    g = auth_ecs.auth_gate(_auth_suites(82.76, 100.0))    # the live example: E2E 24/29
    assert g["verdict"] == "attention" and g["blocking"] is True
    assert "NOT met" in g["detail"] and "UIAutomator" in g["detail"]




def test_auth_gate_blocks_when_a_suite_missing():
    from steps.build_verify import auth_ecs
    g = auth_ecs.auth_gate(_auth_suites(100.0, 100.0, monthly_present=False))
    assert g["blocking"] is True and "no result" in g["detail"]




def test_build_verify_phase_shape():
    """Phase 2 has the 4 verification agent steps + the rc_report scout step (which emails
    the RC report AND applies the 90% UI gate). rc_report is the terminal step — there is
    NO separate human gate (the gate IS the decision). CCD+1 anchored."""
    import yaml as _yaml
    cfg = _yaml.safe_load(open(CONFIG, encoding="utf-8"))
    bv = next(p for p in cfg["phases"] if p["id"] == "build_verify")
    ids = [s["id"] for s in bv["steps"]]
    assert ids == ["checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local",
                   "auth_ecs", "telemetry_verify", "rc_report"]
    assert bv.get("anchor") == "CCD+1"
    tv = next(s for s in bv["steps"] if s["id"] == "telemetry_verify")
    assert tv.get("source") == "scout" and tv.get("owner") == "agent"
    rc = next(s for s in bv["steps"] if s["id"] == "rc_report")
    assert rc.get("source") == "scout" and rc.get("owner") == "agent"
    assert bv["steps"][-1]["id"] == "rc_report"          # terminal Phase-2 step
    assert not any(s.get("gate") for s in bv["steps"])   # no human gate in Phase 2




def test_get_failed_tests_aggregates_repeated_suites():
    """get_failed_tests merges the SAME suite that appears as several runs (the cause of
    the confusing duplicates) into one entry, reconciling exact titles across all runs."""
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
        1: {"value": [{"testCaseTitle": f"test_a{i}", "outcome": "Failed"} for i in range(18)]
                      + [{"testCaseTitle": f"pass_a{i}", "outcome": "Passed"} for i in range(26)]},
        2: {"value": [{"testCaseTitle": f"test_ltw_{t}", "outcome": "Failed"} for t in ("x", "y")]
                      + [{"testCaseTitle": f"pass_ltw_{i}", "outcome": "Passed"} for i in range(6)]},
        3: {"value": [{"testCaseTitle": f"test_ltw_{t}", "outcome": "Failed"} for t in ("y", "z")]
                      + [{"testCaseTitle": f"pass_ltw_{i}", "outcome": "Passed"} for i in range(6)]},
    }
    for response in results.values():
        for result_id, row in enumerate(response["value"], 1):
            row["id"] = result_id
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
        # Repeated passes and failures both collapse; no cross-suite title merging.
        ltw = by["LTW, RC MSAL - RC Broker (API 32)"]
        assert ltw["failed"] == 3 and ltw["total"] == 9
        assert sorted(ltw["tests"]) == ["test_ltw_x", "test_ltw_y", "test_ltw_z"]
        assert next(t for t in ltw["test_results"] if t["title"] == "test_ltw_y")[
            "outcome_counts"] == {"Failed": 2}
        assert ltw["run_ids"] == [2, 3] and ltw["count_basis"] == P.MRWP_COUNT_BASIS
        # sorted by failure count desc → PROD MSAL suite (18) first
        assert suites[0]["name"] == "PROD MSAL - RC Broker (API 32)" and suites[0]["failed"] == 18
    finally:
        P._ado_rest_get = orig




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
    assert st.versions.get("broker") == "1.0.0"          # versions now live in state.versions
    rc = pr["rcs"][-1]
    assert rc["rc"] == 1 and rc.get("resolved_at")
    assert rc["ecs"]["run_id"] == "900001" and rc["local"]["run_id"] == "900002"
    assert rc["ecs"]["complete"] and rc["ecs"]["tests"]["failed"] == 4   # snapshot stored
    with tempfile.TemporaryDirectory() as tmp:
        _C.save_state(st, tmp, "2026-08")
        again = _C.load_state(tmp, "2026-08")
        assert again.pipeline_runs["rcs"][-1]["ecs"]["run_id"] == "900001"




def test_oncall_team_is_single_source_from_readiness():
    """The distribution step reads the on-call team from readiness.yaml's oncall_now item —
    the SAME source the entry gate uses (no second copy to drift)."""
    from tools import distribution as D
    tid, tname = D.oncall_team()
    assert tid == 78848 and "Android Shield" in (tname or "")




def test_auth_ui_projection_aggregates(monkeypatch):
    """Exact-title recovery, distinct-scenario failure dominance and NA-only exclusion."""
    from tools import pipelines as P
    from tests._auth_evidence import auth_snapshot
    rc = {"rc": 1, "auth": auth_snapshot(rows={P.AUTH_UI_SUITES[0]: [
        ("test_100_x", "Failed"), ("test_100_x", "Passed"),
        ("test_200_freshInstall", "Passed"), ("test_200_upgrade", "Failed"),
        ("test_300_z", "NotApplicable"), ("no_case_id_here", "Failed")]})}
    ok, out, _ = P.project_auth_ui_results(rc)
    assert ok and {cid: c["outcome"] for cid, c in out["cases"].items()} == {
        100: "Passed", 200: "Failed", 300: "NotApplicable"}




def test_sim_gate_mode_on_gateless_phase_reports_problem():
    """`at: gate` on a gateless phase fails fast instead of traversing unrelated gates."""
    import tempfile
    from orchestrator import sim as SIM
    scenario = {"name": "t_nogate", "release_id": "2026-08", "ccd": "2026-08-26",
                "as_of": "CCD+30", "data": "mock",
                "target": {"phase": "partner", "at": "gate"}}
    with tempfile.TemporaryDirectory() as tmp:
        res = SIM.run_scenario(scenario, runs_root=tmp)
    assert not res.reached
    assert res.steps_forwarded == 0
    assert any("no gate" in p for p in res.problems)




def test_timed_step_gated_until_fire_time():
    """A step with a fire_at_local (the 09:00 CCD comms) is excluded from scout_pending
    until 09:00 owner-local on its fire day — so the every-hour worker can't drain it
    early — then becomes pending at/after 09:00."""
    import yaml as _yaml
    from datetime import datetime
    from orchestrator.state import StepState
    from orchestrator import schedule
    tz = schedule.get_tz()
    def _ccd_active(now_dt):
        st = ReleaseState(
            release_id="2026-08", ccd="2026-08-26", ccd_source="confirmed")
        _active_phase(st, "ccd")
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




def test_local_mock_never_mocks_a_gate():
    """Gate steps are not mockable — a gate still holds for a real decision even if
    someone lists it in the mock file."""
    st, orch = _mock_orch({"bug_bash.bugbash_complete": {"outcome": "done"}}, as_of="2026-07-09")
    _clear_phase0_scout(orch)          # clear Phase-0 holds
    _clear_ccd_scout(orch)             # clear Phase-1 scout comms (Phase 1 is gateless)
    _advance_to_first_gate(orch)       # Phases 0-2 gateless; clear ui_failures → hold at bugbash_complete
    assert not st.is_done("bug_bash", "bugbash_complete")
    assert orch.status_report()["status"] == "holding_gate"




def test_readiness_mock_clears_auto_gate_offline():
    """`readiness.<item>` mocks force the entry-gate AUTO checks (real ADO/config/
    MCP) pass/fail without the real calls — lets a test clear the gate offline."""
    from datetime import date
    st = ReleaseState(release_id="2026-07", ccd="2026-07-08", ccd_source="default")
    mocks = {f"readiness.{i}": {"outcome": "pass"} for i in
             ("build_access", "mcp_servers", "mail_fallback_live", "silent_perms",
              "adx_access", "oncall_now", "teams_notify")}
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 7, 2), mocks=mocks)
    orch.gate.verify()                        # no _stub_build_defs → real verifier bypassed
    items = st.readiness_items
    assert items["build_access"]["status"] == "pass"      # python-auto mocked
    assert items["oncall_now"]["status"] == "pass"        # scout-auto mocked too




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


def _run_remove_rc_tags_command(command_module, cli_common, args, submit):
    from tools import pipelines as P

    pending = {
        "approval_id": "approval-555",
        "build_id": 555,
        "stage": "Remove RC Tags",
        "build_url": "https://example.invalid/build/555",
    }
    with (
        patch("orchestrator.mocks.load_mocks", return_value={}),
        patch.object(P, "find_orchestrator_pending_approval",
                     return_value=(True, pending, "")),
        patch.object(P, "orchestrator_finalization_status",
                     return_value=(True, {"status": "waiting"}, "test monitor")),
        patch.object(P, "submit_pipeline_approval", side_effect=submit),
        cli_common.state_lock(args.runs_root, args.release),
    ):
        _, preview_orch = cli_common.load_orch(
            args.runs_root, args.release, args.config, None)
        preview = preview_orch.preview_gate_approval(
            "finalize", "remove_rc_tags_gate", comment=args.comment)
        args.review_hash = preview["review_hash"]
        args.approved_by = "test-reviewer"
        args.executor = "test-executor"
        args.execution_id = None
        args.reserve = False
        args.preview = False
        return command_module.cmd_approve_orchestrator_gate(args)


def test_approve_orchestrator_gate_command_submits_then_advances():
    """The `approve-orchestrator-gate` command submits the ADO approval (via
    remove_rc_tags_gate.submit_approval) and, on success, records the
    finalize.remove_rc_tags_gate gate + advances.
    No engine hook is involved — the command composes submit + the normal approve."""
    import tempfile, argparse
    from orchestrator.commands import gate_approve as GA
    from orchestrator import cli_common as _C
    st, orch = _orch()
    _advance_to_first_gate(orch); orch.approve_gate("ok"); orch.run_until_gate()
    report = orch.status_report()
    assert report["current_step"] == "remove_rc_tags_gate"
    assert report["status"] == "holding_gate"
    assert report["gate"]["approval_command"] == "approve-orchestrator-gate"

    calls = {}

    def fake_submit(_org, _project, _approval_id, comment):
        calls["comment"] = comment
        return (True, "submitted the 'Remove RC Tags' approval on build 555")

    with tempfile.TemporaryDirectory() as d:
        _C.save_state(st, d, "t")
        ns = argparse.Namespace(runs_root=d, release="t", config=CONFIG,
                                as_of=None, comment="ship it")
        rc = _run_remove_rc_tags_command(GA, _C, ns, fake_submit)
        after = _C.load_state(d, "t")
    assert rc == 0
    assert calls["comment"] == "ship it"                 # human's comment reaches the ADO submit
    assert after.is_done("finalize", "remove_rc_tags_gate")  # recorded only after provider success
    note = after.get_step("finalize", "remove_rc_tags_gate").note or ""
    assert "approval-555" in note and "build 555" in note


def test_generic_approve_rejects_gate_with_external_approval_command():
    import argparse
    import tempfile
    from orchestrator import cli_common as _C
    from orchestrator.commands import release as release_cmd

    st, orch = _orch()
    _advance_to_first_gate(orch)
    orch.approve_gate("ok")
    orch.run_until_gate()
    assert orch.status_report()["current_step"] == "remove_rc_tags_gate"

    with tempfile.TemporaryDirectory() as directory:
        _C.save_state(st, directory, "t")
        args = argparse.Namespace(
            runs_root=directory,
            release="t",
            config=CONFIG,
            as_of=None,
            comment="unsafe local approval",
        )
        assert release_cmd.cmd_approve(args) == 1
        after = _C.load_state(directory, "t")
    assert not after.is_done("finalize", "remove_rc_tags_gate")
    assert not any(
        decision.get("step") == "finalize.remove_rc_tags_gate"
        for decision in after.gate_decisions
    )


def test_approve_orchestrator_gate_rejects_unapproved_terminal_gate_record():
    import argparse
    import tempfile
    from orchestrator import cli_common as _C
    from orchestrator.commands import gate_approve as GA
    from orchestrator.state import StepState
    st, orch = _orch()
    _advance_to_first_gate(orch)
    orch.approve_gate("ok")
    orch.run_until_gate()
    st.set_step(
        "finalize",
        "remove_rc_tags_gate",
        StepState(status="skipped", completed_at="legacy", note="old override"),
    )
    st.gate_decisions = [
        decision
        for decision in st.gate_decisions
        if decision.get("step") != "finalize.remove_rc_tags_gate"
    ]
    with tempfile.TemporaryDirectory() as directory:
        _C.save_state(st, directory, "t")
        args = argparse.Namespace(
            runs_root=directory,
            release="t",
            config=CONFIG,
            as_of=None,
            comment="recovered",
        )
        rc = _run_remove_rc_tags_command(
            GA, _C, args, lambda *_args: (True, "submitted build 555"))
        after = _C.load_state(directory, "t")
    assert rc == 0
    assert after.get_step("finalize", "remove_rc_tags_gate").status == "done"




def test_approve_orchestrator_gate_command_holds_when_submit_fails():
    """Safety property: if the ADO submit FAILS, the command returns non-zero and does NOT record
    the gate — release-agent retains the exact execution for reconciliation."""
    import tempfile, argparse
    from orchestrator.commands import gate_approve as GA
    from orchestrator import cli_common as _C
    st, orch = _orch()
    _advance_to_first_gate(orch); orch.approve_gate("ok"); orch.run_until_gate()
    assert orch.status_report()["current_step"] == "remove_rc_tags_gate"
    assert orch.status_report()["status"] == "holding_gate"

    with tempfile.TemporaryDirectory() as d:
        _C.save_state(st, d, "t")
        ns = argparse.Namespace(runs_root=d, release="t", config=CONFIG,
                                as_of=None, comment="ship it")
        rc = _run_remove_rc_tags_command(
            GA, _C, ns, lambda *_args: (False, "ADO approval submit FAILED (boom)."))
        after = _C.load_state(d, "t")
    assert rc == 1
    assert not after.is_done("finalize", "remove_rc_tags_gate")  # gate NOT recorded
    execution = after.get_step("finalize", "remove_rc_tags_gate").execution
    assert execution and execution["approval"]["receipt"] is None
    after_orch = Orchestrator(CONFIG, after, mocks={})
    assert after_orch.status_report()["status"] == "awaiting_action"
    assert after_orch.status_report()["current_step"] == "remove_rc_tags_gate"




def test_approve_orchestrator_gate_command_rejects_wrong_gate():
    """The command requires the release to hold at finalize.remove_rc_tags_gate."""
    import tempfile, argparse
    from orchestrator.commands import gate_approve as GA
    from orchestrator import cli_common as _C
    from steps.finalize import remove_rc_tags_gate as gw
    st, orch = _orch()
    _advance_to_first_gate(orch)  # at bug_bash_complete, not remove_rc_tags_gate
    assert orch.status_report()["current_step"] == "bugbash_complete"

    o = gw.submit_approval
    called = {"n": 0}
    def guard(*a, **k):
        called["n"] += 1
        return (True, "should not be called")
    gw.submit_approval = guard
    try:
        with tempfile.TemporaryDirectory() as d:
            _C.save_state(st, d, "t")
            ns = argparse.Namespace(runs_root=d, release="t", config=CONFIG,
                                    as_of=None, comment="")
            with patch("orchestrator.mocks.load_mocks", return_value={}):
                rc = GA.cmd_approve_orchestrator_gate(ns)
    finally:
        gw.submit_approval = o
    assert rc == 1 and called["n"] == 0                  # no ADO submit attempted on the wrong gate
