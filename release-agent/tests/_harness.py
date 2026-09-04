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
    "preflight.oneauth_access": {"alias": "tester", "access": "granted"},  # → write access → pass (no az)
    "preflight.cron": {"run": {"queueTime": _recent_iso(), "result": "succeeded"}},  # fresh → pass
    "preflight.breaking": {"changelog": "vNext\n----\n- [MINOR] x (#1)\nVersion 1.0.0\n"},  # no [MAJOR] → pass
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
    "build_verify.auth_ecs": {
        "auth_build": {"build_id": 900010, "rc": 1, "version": "0.0.02468-rc-RC1-ecs",
                       "status": "completed", "result": "succeeded"},
        "test_build": 900011,
        "suites": {
            "Firebase Test Lab - UIAutomator E2E Tests":
                {"present": True, "passed": 96, "failed": 4, "total": 100, "pct": 96.0},
            "Firebase Test Lab - Monthly UI Tests":
                {"present": True, "passed": 306, "failed": 0, "total": 306, "pct": 100.0}}},
    "build_verify.rc_report": {"outcome": "done", "note": "RC report emailed (test)"},  # skip live az + send
    # Phase-2 telemetry_verify — scout Kusto check; short-circuit so flow tests never hit the MCP.
    "build_verify.telemetry_verify": {"outcome": "done", "note": "bug-bash telemetry verified (test)"},
    # Phase-3 bug_bash clone steps — real agents; keep flow tests offline.
    "bug_bash.clone_plans_broker": {"outcome": "done", "note": "broker plan cloned (test)"},
    "bug_bash.clone_plans_auth": {"outcome": "done", "note": "auth suite created (test)"},
    "bug_bash.distribute_tests": {"outcome": "done", "note": "tests distributed (test)"},
    "bug_bash.ui_test_status": {"outcome": "done", "note": "UI automation results filled (test)"},
    "bug_bash.send_invite": {"outcome": "done", "note": "bug bash invite sent (test)"},
    "bug_bash.notify_native_auth": {"outcome": "done", "note": "native auth RE notified (test)"},
    "bug_bash.activate_chat": {"outcome": "done", "note": "meeting chat activated (test)"},
    "bug_bash.bugbash_updates": {"outcome": "done", "note": "first bug-bash update posted (test)"},
    "bug_bash.native_auth_signoff": {"outcome": "done", "note": "native auth sign-off recorded (test)"},
    # Phase-4 finalize scout post — short-circuit so flow tests never hit Teams.
    "finalize.release_announcement": {"outcome": "done", "note": "release announced (test)"},
    # Phase-4 verify_pub — real agent (Maven Central HEADs). Short-circuit for flow tests.
    "finalize.verify_pub": {"outcome": "done", "note": "maven central verified (test)"},
    # Phase-4 finalize integ_prs — real agent (gh/az/git). Short-circuit so flow tests
    # never hit the network; dedicated integ_prs tests exercise its real logic offline.
    "finalize.integ_prs": {"outcome": "done", "note": "integration PRs opened (test)"},
    # Phase-4 tag_authenticator — real agent (msazure/One git write). Short-circuit for flow
    # tests; dedicated tag_authenticator tests exercise its logic offline.
    "finalize.tag_authenticator": {"outcome": "done", "note": "auth release tagged (test)"},
    # Phase-4 oneauth_common_pr — real agent (OneAuth REST reads/merge/PR). Short-circuit so flow
    # tests never hit the network; dedicated tests exercise its logic offline.
    "finalize.oneauth_common_pr": {"outcome": "done", "note": "OneAuth common ingested (test)"},
    # Phase-4 publish_notes_gate — a GATE (not an agent): keep it offline + holding in flow tests by
    # injecting no parked approval + no stage state (build() -> 'not parked yet' NeedsHuman).
    "finalize.publish_notes_gate": {"approval": None, "stage_state": None},
    # Phase-4 verify_release_notes — real agent (gh release view). Short-circuit for flow tests;
    # dedicated tests exercise its logic offline.
    "finalize.verify_release_notes": {"outcome": "done", "note": "github release notes verified (test)"},
    # Phase-4 wiki_payload — real agent (ADO wiki create/update). Short-circuit so flow tests never
    # hit the network; dedicated wiki_payload tests exercise its compose logic offline.
    "finalize.wiki_payload": {"outcome": "done", "note": "payload wiki page updated (test)"},
    # Phase-4 final_status_email — scout send (workiq). Short-circuit so flow tests never hit email.
    "finalize.final_status_email": {"outcome": "done", "note": "closing status email sent (test)"},

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
    """Now that go_test is gone, the first real GATE is Phase-3 `bug_bash.bugbash_complete`,
    reached after the Phase-3 `ui_failures` human reminder. Drive to that reminder, clear
    it, then drive to the bugbash_complete gate."""
    orch.run_until_gate()                                     # holds at ui_failures (reminder)
    orch.complete_step("bug_bash", "ui_failures", "test: UI failures reviewed")
    orch.run_until_gate()                                     # holds at bugbash_complete (gate)



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



# ---- Phase-0 real pre-flight agents (breaking detect) ----
_SAMPLE_CHANGELOG = """vNext

----------

- [MINOR] add a thing (#1)

- [MAJOR] breaking change OneAuth consumers must handle (#2)

- [PATCH] small fix (#3)

Version 24.5.0

----------

- [MAJOR] an OLD breaking change already shipped (#0)

"""



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
    K.stash_orchestrator(st, "1678611", parked=True)
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"})

    def snap(run_id, ui, suites):
        return {"run_id": run_id, "complete": True, "ran": 23, "total": 23,
                "failed_stages": [], "yellow_stages": [], "never_ran": [],
                "tests": {"categories": {"ui": ui}}, "failed_suites": suites or []}
    K.stash_mrwp(st, "ECS", snap(ecs_id, ecs_ui, ecs_suites))
    K.stash_mrwp(st, "Local", snap(local_id, local_ui, None))



def _auth_suites(e2e_pct, monthly_pct=100.0, e2e_present=True, monthly_present=True):
    """Build the auth_ui_suite_rates map for the two Firebase suites at given pass rates."""
    def suite(pct, present):
        if not present:
            return {"present": False, "passed": 0, "failed": 0, "total": 0, "pct": None}
        passed = int(round(pct))
        return {"present": True, "passed": passed, "failed": 100 - passed,
                "total": 100, "pct": float(pct)}
    from tools.pipelines import AUTH_UI_SUITES
    return {AUTH_UI_SUITES[0]: suite(e2e_pct, e2e_present),
            AUTH_UI_SUITES[1]: suite(monthly_pct, monthly_present)}



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



# ---- Phase 3: clone_plans_broker / clone_plans_auth ----

def _bb_build(sid, mocks, release="2026-08", ccd="2026-08-13"):
    """Build a bug_bash step outcome offline with the given mock inputs active."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id=release, ccd=ccd)
    with mockctx.active(mocks):
        return st, as_dict(_steps.get_step("bug_bash", sid).build(st))



# ---- Phase 3: ui_test_status ----

def _uts_state(plan_id="900", release="2026-08"):
    from orchestrator.state import StepState
    st = ReleaseState(release_id=release)
    if plan_id is not None:
        st.set_step("bug_bash", "clone_plans_broker",
                    StepState(status="done", data={"plan_id": plan_id}))
    return st



def _dist_build(mocks, owner="owner@microsoft.com", broker_plan="900"):
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import StepState
    st = ReleaseState(release_id="2026-08", owner_email=owner)
    st.set_step("bug_bash", "clone_plans_broker", StepState(status="done", data={"plan_id": broker_plan}))
    with mockctx.active(mocks):
        return st, as_dict(_steps.get_step("bug_bash", "distribute_tests").build(st))



def _invite_state():
    from orchestrator.state import StepState
    st = ReleaseState(release_id="2026-08", ccd="2026-08-13",
                      owner_email="owner@microsoft.com", timezone="America/Los_Angeles")
    st.set_step("bug_bash", "clone_plans_broker", StepState(status="done", data={"plan_id": 3730001}))
    st.set_step("bug_bash", "clone_plans_auth", StepState(status="done", data={"suite_id": 3730002}))
    st.pipeline_runs = {"rcs": [{"rc": 1, "ecs": {"run_id": "1678863"}, "local": {"run_id": "1678864"}}]}
    return st



# ---- Phase 3: notify_native_auth ----

def _na_state(release="2026-08", ccd="2026-08-13", broker_plan=3730001, owner="Pedro"):
    from orchestrator.state import StepState
    st = ReleaseState(release_id=release, ccd=ccd, owner_name=owner)
    st.set_step("bug_bash", "clone_plans_broker", StepState(status="done",
                                                            data={"plan_id": broker_plan}))
    return st



def _bb_updates_state(chat_id="19:meeting_X@thread.v2"):
    from orchestrator.state import StepState
    st = ReleaseState(release_id="2026-08", ccd="2026-08-13")
    st.set_step("bug_bash", "clone_plans_broker", StepState(status="done", data={"plan_id": 3730001}))
    st.set_step("bug_bash", "clone_plans_auth", StepState(status="done", data={"suite_id": 3730002}))
    if chat_id:
        st.set_step("bug_bash", "activate_chat", StepState(status="done", data={"chat_id": chat_id}))
    return st



# ---- Phase 1 (ccd) step modules ----

def _ccd_state():
    return ReleaseState(release_id="2026-09", ccd="2026-09-09", ccd_source="default",
                        owner_email="pedroro@microsoft.com", owner_name="Pedro")



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



def _patch_pr_reads(P, exists=True, existing_pr=None, behind=0, gradle=None, conflicts=None):
    """Replace integ_prs' read helpers with offline stubs; returns a restore() callable."""
    orig = {k: getattr(P, k) for k in
            ("remote_branch_exists", "gh_find_open_pr", "az_find_open_pr",
             "behind_count", "gradle_diff_files", "merge_conflict_preview")}
    P.remote_branch_exists = lambda d, b, timeout=60: (True, exists, "")
    P.gh_find_open_pr = lambda r, h, b, timeout=60: (True, existing_pr, "")
    P.az_find_open_pr = lambda org, proj, repo, h, b, timeout=60: (True, existing_pr, "")
    P.behind_count = lambda d, h, b, timeout=60: (True, behind, "")
    P.gradle_diff_files = lambda d, h, b, timeout=60: (True, list(gradle or []), "")
    P.merge_conflict_preview = lambda d, h, b, timeout=90: (True, list(conflicts or []), "")

    def restore():
        for k, v in orig.items():
            setattr(P, k, v)
    return restore



def _integ_release(d, mocks):
    """Save a finalize/integ_prs release and point load_mocks at `mocks`; return (ns, restore)."""
    import argparse
    from orchestrator import cli_common as _C
    st = ReleaseState(release_id="2026-08")
    st.current_phase, st.current_step, st.status = "finalize", "integ_prs", "awaiting_action"
    _C.save_state(st, d, "2026-08")
    o = _mocks_mod.load_mocks
    _mocks_mod.load_mocks = lambda *a, **k: {"finalize.integ_prs": dict(mocks)}
    ns = argparse.Namespace(runs_root=d, release="2026-08", config=CONFIG, as_of=None,
                            execute=False, repos=None, pbi=None, pbi_title=None)

    def restore():
        _mocks_mod.load_mocks = o
    return ns, restore



# ---- Phase 4: verify_release_notes (GitHub releases for Broker + MSAL + Common) ----

def _vrn_state():
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"})
    return st



# ---- Phase 4: tag_authenticator ----

_TA_COMMIT = "87b921ccf73c322a1907936e74c8d1a984e27102"



def _ta_state():
    st = ReleaseState(release_id="2026-08", ccd="2026-08-13")
    st.record_versions({"authenticator": "release/2026/08/13"})
    return st



# ---- Phase 4: oneauth_common_pr (OneAuth Common ingestion) ----

_OA_FILES = {
    "toml": ('[versions]\n'
             'msIdentityCommon = "24.6.0"\n'
             'msIdentityCommonTest = "0.0.20260506.3"\n'),
    "cgmanifest": ('{\n  "Registrations": [\n    {\n      "component": {\n'
                   '        "type": "maven",\n        "maven": {\n'
                   '          "groupId": "com.microsoft.identity",\n'
                   '          "artifactId": "common",\n'
                   '          "version": "24.6.0"\n        }\n      }\n    }\n  ]\n}\n'),
    "readme": ('| Name | Version | License |\n'
               '| MSAL Android Common              | 24.6.0   | [MIT] | Prod | https://x |\n'),
    "changelog": ('# Changelog\n\n## [Unreleased]\n### Breaking Changes\n\n'
                  '### Other Changes\n- (iOS) something.\n\n## [11.2.0] (2026-08-26)\n- old.\n'),

}



def _oa_state():
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.7.0", "msal": "8.5.0"})
    return st



# ======================= partner status email =======================

def _status_state(phase="build_verify"):
    from orchestrator.state import ReleaseState, StepState
    st = ReleaseState(release_id="2026-08", ccd="2026-08-11", target_month="2026-09",
                      owner_name="Praveen", owner_email="praveen@microsoft.com")
    st.current_phase = phase
    st.record_versions({"msal": "8.4.2", "common": "24.6.0", "broker": "16.5.0",
                        "authenticator": "release/2026/08/13"})
    st.set_step("ccd", "final_reminder", StepState(status="done"))
    st.set_step("build_verify", "orchestrator_health", StepState(status="done"))
    st.set_step("bug_bash", "send_invite", StepState(status="done", completed_at="2026-08-13T10:00:00"))
    st.pipeline_runs = {"rcs": [{"rc": 1, "ecs": {"run_id": 1678863,
        "tests": {"categories": {"ui": {"passed": 96, "failed": 4, "total": 100}}}},
        "local": {"run_id": 1678864,
        "tests": {"categories": {"ui": {"passed": 98, "failed": 2, "total": 100}}}}}]}
    return st


_PHASE_ORDER = ["preflight", "ccd", "build_verify", "bug_bash", "finalize", "rollout_start", "monitor"]

# Re-export the entire harness namespace so per-area test files get an identical
# module scope via `from tests._harness import *` (includes underscore helpers).
__all__ = [k for k in list(globals()) if not k.startswith('__')]
