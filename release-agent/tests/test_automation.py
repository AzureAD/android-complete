"""Release-agent tests — automation. Shared harness in tests/_harness.py."""
from tests._context import context as _context
from tests._harness import *  # noqa: F401,F403
import pytest


@pytest.fixture(autouse=True)
def _planning_clock(monkeypatch):
    from datetime import datetime, timezone
    from orchestrator import automations, schedule
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone(tz or timezone.utc)
    monkeypatch.setattr(automations, "datetime", Clock)
    monkeypatch.setattr(schedule, "detect_local_tz", lambda: "America/Los_Angeles")




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
    orch.gate.record_check("ccd_confirmed", "pass", "CCD reconciled")
    _pass_m365_check(orch)
    orch.gate.sign()
    assert not orch.gate.signed
    # degraded (Teams unreachable → email only) satisfies the opt-out item
    res = orch.gate.record_check("teams_notify", "degraded", "Teams unreachable — email only")
    assert "error" not in res
    tn2 = next(i for i in orch.gate.checklist()["items"] if i["id"] == "teams_notify")
    assert tn2["status"] == "degraded" and tn2["satisfied"]
    assert orch.gate.signed




def test_registry_relocates_release_automations_into_release_folder():
    """Release-scoped automations live in <runs_root>/<release>/_automations.json (owned
    by the release); shared ones stay machine-wide."""
    from orchestrator.registry import AutomationRegistry
    import os as _os, json as _json
    with tempfile.TemporaryDirectory() as tmp:
        reg = AutomationRegistry(tmp, release="2026-08")
        _register_automation(
            reg, "a2", "Release drainer", release="2026-08",
            steps=[], cleanup_when="release_done")
        _register_automation(
            reg, "sh", "Release push reminders", shared=True, purpose="push",
            cleanup_when="manual")
        rel_file = _os.path.join(tmp, "2026-08", "_automations.json")
        shared_file = _os.path.join(tmp, "_automations.json")
        # the release automation is co-located with the release; shared stays machine-wide
        assert [e["id"] for e in _json.load(open(rel_file))["entries"]] == ["a2"]
        assert [e["id"] for e in _json.load(open(shared_file))["entries"]] == ["sh"]
        # release listing reads the release file; claimed deletion removes it safely
        assert {e["id"] for e in reg.list(release="2026-08")} == {"a2"}
        deletion = reg.claim_delete("a2", "test")
        assert reg.delete_result(
            "a2", deletion["attempt_id"], "deleted", "provider deleted"
        )["status"] == "deleted"
        assert reg.list(release="2026-08") == []




def test_automation_plan_derives_specs_from_ccd():
    """`plan` turns automations.yaml into the single unified `release-drainer`
    accelerator (`07:00-19:00/2h` -> `cron: 0 7-19/2 * * *`)."""
    from orchestrator import automations as A
    result = A.plan(
        CONFIG, "2026-09", "2026-09-09",
        owner_timezone="America/Los_Angeles",
        scheduler_timezone="America/Los_Angeles",
    )
    assert result["problems"] == []
    by = {a["slug"]: a for a in result["automations"]}
    assert set(by) == {"release-drainer"}
    assert by["release-drainer"]["daily_window"] == "07:00-19:00/2h"
    assert by["release-drainer"]["schedule"] == "cron: 0 7-19/2 * * *"
    assert by["release-drainer"]["one_shot"] is False
    assert by["release-drainer"]["optional"] is True
    assert by["release-drainer"]["registration"]["slug"] == "release-drainer"
    assert by["release-drainer"]["registration"]["schedule"] == "cron: 0 7-19/2 * * *"
    assert by["release-drainer"]["registration"]["cleanup_when"] == "release_done"


def test_on_demand_obligation_remains_optional_and_non_blocking():
    """The unified `release-drainer` surfaces under `optional`, never `required`."""
    from orchestrator import automations as A

    st = ReleaseState(
        release_id="2026-09", ccd="2026-09-09",
        timezone="America/Los_Angeles")
    Orchestrator(CONFIG, st, step_defaults={})

    missing = A.provisioning_obligations(st, [], CONFIG)
    assert missing["required"] == []
    assert [item["slug"] for item in missing["optional"]] == ["release-drainer"]
    assert missing["recoveries"] == []

    planned = {item["slug"]: item for item in A.plan(
        CONFIG, st.release_id, st.ccd,
        owner_timezone=st.timezone)["automations"]}
    active = [{
        **planned["release-drainer"]["registration"],
        "key": "release:2026-09:release-drainer",
        "id": "drainer-id",
        "status": "active",
    }]
    satisfied = A.provisioning_obligations(st, active, CONFIG)
    assert satisfied["required"] == []
    assert satisfied["active"] == ["release-drainer"]


def test_release_drainer_prompt_drains_journals_and_never_resurrects():
    from orchestrator import automations as A

    by = {item["slug"]: item for item in A.plan(
        CONFIG, "2026-09", "2026-09-09",
        owner_timezone="America/Los_Angeles")["automations"]}
    prompt = by["release-drainer"]["prompt"]
    assert "drain --release 2026-09 --trigger scheduled --deliver --json" in prompt
    assert "drain-runs.jsonl" in prompt
    assert "scout_bot_cta" in prompt
    assert "reconcile-create --ensure" not in prompt
    assert "never recreate or re-enable" in prompt


def test_automation_names_follow_standard_format():
    """Every provisioned automation title is `<release-id> · <scope> — <label>`."""
    from orchestrator import automations as A
    result = A.plan(CONFIG, "2026-09", "2026-09-09", owner_timezone="America/Los_Angeles")
    by = {a["slug"]: a for a in result["automations"]}
    assert by["release-drainer"]["name"] == "2026-09 · Release-wide — release drainer"
    assert by["release-drainer"]["registration"]["name"] == by["release-drainer"]["name"]
    for a in result["automations"]:
        assert a["name"].startswith("2026-09 · ")
        assert " — " in a["name"]


def test_automation_name_helper_and_phase_label():
    """The name-builder + phase-label helpers are the single source of the standard title."""
    from orchestrator import automations as A
    assert A.automation_name("2026-08", "Release-wide", "morning step drain") == \
        "2026-08 · Release-wide — morning step drain"
    # scope falls back to 'Release-wide' when empty (non-phase automations)
    assert A.automation_name("2026-08", "", "x") == "2026-08 · Release-wide — x"
    assert A.phase_label(CONFIG, "ccd") == "Code Complete Day"
    assert A.phase_label(CONFIG, None) == "Release-wide"


def test_cli_plan_returns_single_release_drainer(capsys):
    import json
    import tempfile as _tf
    from orchestrator import cli
    with _tf.TemporaryDirectory() as d:
        rid = "2026-09"
        state = ReleaseState(release_id=rid, ccd="2026-09-09", timezone="America/Los_Angeles")
        Orchestrator(CONFIG, state, step_defaults={})
        C.save_state(state, d, rid)
        base = ["--runs-root", d, "automation", "plan", "--release", rid, "--json"]
        assert cli.main(base) == 0
        startup = json.loads(capsys.readouterr().out)["automations"]
        assert [a["slug"] for a in startup] == ["release-drainer"]
        assert startup[0]["optional"] is True

        obligations = [
            "--runs-root", d, "automation", "obligations",
            "--release", rid, "--json"]
        assert cli.main(obligations) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["required"] == []
        assert [a["slug"] for a in out["optional"]] == ["release-drainer"]


def test_cleanup_plan_applies_declared_lifecycle_rules():
    from orchestrator import automations as A
    from orchestrator.state import StepState
    st = ReleaseState(release_id="2026-09")
    Orchestrator(CONFIG, st, step_defaults={})
    entries = [
        {"id": "drainer", "name": "Release drainer", "kind": "release-level",
         "steps": [], "cleanup_when": "release_done", "scope": "release", "release": st.release_id},
        {"id": "manual", "name": "Manual", "kind": "release-level",
         "steps": [], "cleanup_when": "manual", "scope": "release", "release": st.release_id},
    ]
    first = A.cleanup_plan(st, entries, CONFIG)
    assert first["removals"] == [] and first["problems"] == []
    completion = Orchestrator(CONFIG, st, step_defaults={})
    for phase in completion.config["phases"]:
        if phase.get("conditional"):
            continue
        for step in phase["steps"]:
            record = st.get_step(phase["id"], step["id"])
            record.status = "done"
            st.set_step(phase["id"], step["id"], record)
            if step.get("kind") == "approval_gate":
                st.gate_decisions.append(
                    {"step": f"{phase['id']}.{step['id']}", "decision": "approved"}
                )
    second = A.cleanup_plan(st, entries, CONFIG)
    assert [r["id"] for r in second["removals"]] == ["drainer"]


def test_registry_requires_cleanup_rule():
    from orchestrator.registry import AutomationRegistry
    import tempfile as _tf
    import pytest
    with _tf.TemporaryDirectory() as d:
        with pytest.raises(ValueError, match="cleanup_when"):
            AutomationRegistry(d).prepare(
                "No lifecycle", release="2026-09", slug="x", spec={})


def test_generated_prompts_run_central_cleanup():
    from orchestrator import automations as A
    plan = A.plan(CONFIG, "2026-09", "2026-09-09", owner_timezone="America/Los_Angeles")
    for spec in plan["automations"]:
        assert "automation cleanup --release 2026-09 --json" in spec["prompt"]
        assert "m_delete_automation" in spec["prompt"]
        assert "permission_to_delete" in spec["prompt"]
        assert "delete-result" in spec["prompt"]


def test_cleanup_command_returns_registered_ids_without_mutating_registry(capsys):
    import json
    import tempfile as _tf
    from orchestrator import cli
    from orchestrator.registry import AutomationRegistry
    with _tf.TemporaryDirectory() as d:
        rid = "2026-09"
        st = ReleaseState(release_id=rid, cancellation={"reason": "cancelled", "at": "2026-09-09T00:00:00Z"})
        Orchestrator(CONFIG, st, step_defaults={})
        C.save_state(st, d, rid)
        reg = AutomationRegistry(d, rid)
        _register_automation(
            reg, "drainer", "Release drainer", release=rid,
            steps=[], cleanup_when="release_done")
        assert cli.main(["--runs-root", d, "automation", "cleanup",
                         "--release", rid, "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert [r["id"] for r in result["removals"]] == ["drainer"]
        assert reg.list(release=rid)[0]["id"] == "drainer"  # cleanup is a non-mutating plan


def test_cleanup_does_not_trust_unapproved_gate_completion():
    from orchestrator import automations
    from orchestrator.state import StepState

    rid = "2026-09"
    st = ReleaseState(release_id=rid)
    orch = Orchestrator(CONFIG, st, step_defaults={})
    for phase in orch.config["phases"]:
        if phase.get("conditional"):
            continue
        for step in phase["steps"]:
            if step.get("kind") == "approval_gate":
                st.gate_decisions.append(
                    {"step": f"{phase['id']}.{step['id']}", "decision": "approved"}
                )
            st.set_step(phase["id"], step["id"], StepState(status="done"))
    st.gate_decisions = [
        decision
        for decision in st.gate_decisions
        if decision["step"] != "bug_bash.bugbash_complete"
    ]
    st.set_step(
        "bug_bash",
        "bugbash_complete",
        StepState(status="skipped", note="legacy override"),
    )
    entry = {
        "id": "release-worker",
        "scope": "release",
        "release": rid,
        "cleanup_when": "release_done",
        "steps": [],
    }
    result = automations.cleanup_plan(st, [entry], CONFIG)
    assert result["removals"] == []




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
        st = ReleaseState(release_id=rid, ccd="2026-08-26", ccd_source="confirmed",
                          timezone="America/Los_Angeles")
        C.save_state(st, d, rid)
        reg = AutomationRegistry(d)
        from orchestrator import automations
        from tests._automation import observed
        planned = automations.plan(CONFIG, rid, st.ccd, owner_timezone=st.timezone)
        for item in planned["automations"]:
            entry = reg.prepare(**item["registration"], spec=item["provider_spec"])
            reg.reconcile_create(entry["key"], observed({"id": item["slug"], "spec": item["provider_spec"]}),
                                 spec=item["provider_spec"])

        def sync():
            ns = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, json=True)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                A._cmd_sync(ns)
            return _json.loads(buf.getvalue())

        # in sync → nothing changed
        u0 = {u["slug"]: u for u in sync()["updates"]}
        assert all(not u["changed"] for u in u0.values())
        assert u0["release-drainer"]["cleanup_when"] == "release_done"
        # change owner timezone → release-drainer host-converted schedule drifts
        st.timezone = "America/New_York"; C.save_state(st, d, rid)
        u1 = {u["slug"]: u for u in sync()["updates"]}
        assert u1["release-drainer"]["changed"]


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
    assert v == {100: {("ECS", "prod_msal_rc_broker"): "Passed", ("ECS", "rc_msal_prod_broker"): "Failed",
                       ("Local", "prod_msal_rc_broker"): "NotApplicable"}}
    assert 999 not in v
    assert projection["provenance"]["providers"][1]["skipped_mapping"][0]["reason"] == "unknown_suite_variant"




def test_fill_ui_automation_results_maps_configs():
    """Each plan point (case, config) takes the outcome of its matching (flight, variant); a
    config with no verdict is untouched. Mirrors the user's test_3321136 example."""
    from tools import pipelines as P
    from tools import testplans as T
    verdicts = {3321136: {("ECS", "prod_msal_rc_broker"): "Passed", ("ECS", "rc_msal_prod_broker"): "Failed",
                          ("Local", "prod_msal_rc_broker"): "NotApplicable"}}  # absent pair untouched
    points = [
        {"id": 1, "outcome": "Unspecified", "testCase": {"id": "3321136"}, "configuration": {"id": "292"}},  # ECS prod -> Passed
        {"id": 2, "outcome": "Unspecified", "testCase": {"id": "3321136"}, "configuration": {"id": "294"}},  # ECS rc  -> Failed
        {"id": 3, "outcome": "Unspecified", "testCase": {"id": "3321136"}, "configuration": {"id": "328"}},  # Local prod -> N/A
        {"id": 4, "outcome": "Unspecified", "testCase": {"id": "3321136"}, "configuration": {"id": "344"}},  # no verdict
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
        assert BC.cmd_record_nativeauth_notify(ns) == 1
        st = _C.load_state(d, rid)
        _active_phase(st, "bug_bash")
        for s in Orchestrator(CONFIG, st, step_defaults={}).config["phases"][3]["steps"]:
            if s["id"] == "notify_native_auth":
                break
            from orchestrator.state import StepState
            prior = st.get_step("bug_bash", s["id"])
            prior.status = "done"
            st.set_step("bug_bash", s["id"], prior)
        _C.save_state(st, d, rid)
        _ack_step(d, rid, "bug_bash", "notify_native_auth",
                  engineer="verified@example.com", engineer_source="directory lookup + on-call schedule")
        again = _C.load_state(d, rid)
        assert again.is_done("bug_bash", "notify_native_auth")
        assert notified_engineer(_context(again)) == "verified@example.com"
        original = again.get_step("bug_bash", "notify_native_auth")
        assert BC.cmd_record_nativeauth_notify(ns) == 0
        assert _C.load_state(d, rid).get_step("bug_bash", "notify_native_auth") == original

        # no engineer -> attention hold
        _C.save_state(_na_state(), d, rid)
        ns2 = argparse.Namespace(runs_root=d, release=rid, config=CONFIG, as_of=None, engineer=None)
        assert BC.cmd_record_nativeauth_notify(ns2) == 1
        after = _C.load_state(d, rid)
        assert not after.is_done("bug_bash", "notify_native_auth")
        assert after.get_step("bug_bash", "notify_native_auth").status == "pending"
