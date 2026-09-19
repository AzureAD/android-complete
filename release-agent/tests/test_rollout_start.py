"""Phase-5 Authenticator rollout-start notice."""
from dataclasses import replace
from datetime import datetime, timezone
from argparse import Namespace

import pytest

from orchestrator import cli_common as C, delivery, mocks
from orchestrator.commands.step_action import prepare_step
from orchestrator.engine import Orchestrator
from orchestrator.outcomes import Blocked, NeedsSkill
from orchestrator.state import ReleaseState, StepState
from steps.rollout_start import notice
from tests._context import context, fresh_orchestrator
from tests._harness import CONFIG, _active_phase


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _state():
    state = ReleaseState(
        release_id="2026-09",
        target_month="2026-10",
        owner_email="owner@microsoft.com",
        owner_name="Release Owner",
    )
    state.versions = {
        "authenticator": "release/2026/09/10",
        "broker": "16.6.0",
        "common": "24.7.0",
        "msal": "8.5.0",
    }
    state.pipeline_runs = {
        "rcs": [{
            "rc": 1,
            "auth": {
                "build": {
                    "run_id": "180400001",
                    "result": "succeeded",
                    "complete": True,
                },
            },
        }],
        "final_auth": {
            "authenticator_build_id": "180500000",
            "authenticator_version": "6.2609.6000",
            "authenticator_commit": COMMIT,
            "authenticator_build_number": "20260910.5",
        },
    }
    state.set_step("bug_bash", "clone_plans_auth", StepState(
        status="done", data={"suite_id": 3752749, "suite_name": "October Authenticator"}))
    state.set_step("bug_bash", "native_auth_signoff", StepState(
        status="done", completed_at="2026-09-12T18:00:00+00:00"))
    state.set_step("bug_bash", "bugbash_complete", StepState(
        status="done", completed_at="2026-09-12T19:00:00+00:00"))
    state.set_step("rollout_start", "wiki_payload", StepState(
        status="done",
        links=[{"name": "Release payload page", "url": "https://wiki/payload"}]))
    return state


def _manifest():
    return {
        "version": 1,
        "branch": "release/2026/09/10",
        "target_commit": COMMIT,
        "baseline_branch": "release/2026/08/13",
        "baseline_commit": "1" * 40,
        "merge_base": "2" * 40,
        "reachable_commit_count": 3,
        "general": [
            {"id": 101, "commit": "3" * 40, "title": "General <change>",
             "url": "https://ado/pr/101", "components": ["Authenticator"], "mixed": False},
        ],
        "did": [
            {"id": 102, "commit": "4" * 40, "title": "Shared change",
             "url": "https://ado/pr/102", "components": ["Authenticator", "DID"], "mixed": True},
            {"id": 103, "commit": "5" * 40, "title": "VID telemetry",
             "url": "https://ado/pr/103", "components": ["DID"], "mixed": False},
        ],
        "generated_omitted": [{"commit": "6" * 40, "title": "OneLoc"}],
        "flight_changes": {
            "added": [{"name": "NewFlag", "key": "NewFlag", "default": "true"}],
            "default_changed": [{
                "name": "ExistingFlag", "key": "ExistingFlag",
                "previous_default": "false", "default": "true",
            }],
        },
    }


def _inputs(**extra):
    return {
        "build": {
            "build_id": 180500000,
            "version": "6.2609.6000",
            "commit": COMMIT,
            "build_number": "20260910.5",
        },
        "manifest": _manifest(),
        **extra,
    }


def _production_context(state, *, now=None):
    ctx = context(state, now=now)
    pipelines = replace(
        ctx.services.pipelines,
        find_final_auth_build=lambda branch, **kwargs: (True, _inputs()["build"], ""),
        release_change_manifest=lambda branch, commit: (True, _manifest(), ""),
    )
    return replace(ctx, services=replace(ctx.services, pipelines=pipelines))


def test_notice_is_exact_html_email_with_deterministic_sections():
    result = notice.build(_production_context(
        _state(),
        now=datetime(2026, 9, 15, 17, 0, tzinfo=timezone.utc),
    ))
    assert isinstance(result, NeedsSkill)
    assert result.tool == "workiq_send_email" and result.record_as == "notice"
    assert result.payload["to"] == ["MAuthenticatorRel@microsoft.com"]
    assert result.payload["cc"] == ["windevxeng@microsoft.com"]
    assert result.payload["isHtml"] is True
    assert result.payload["subject"] == \
        "Android Authenticator October 2026 release intent — 6.2609.6000"
    html = result.payload["body"]
    for value in (
        "Android Authenticator — Release Intent", "6.2609.6000",
        "October Authenticator", "release/2026/09/10", ">Authenticator</h3>",
        "VID telemetry", "Authenticator + DID", "NewFlag", "ExistingFlag",
        "Source defaults, not rollout intent", "Release Progression",
    ):
        assert value in html
    assert "IDWiki" not in html and "EngHub" not in html
    assert "Sign-offs" not in html and "General payload" not in html
    assert "Broker test plan" not in html and "October Broker" not in html
    assert "Run 180400001" in html and "buildId=180400001" in html
    assert "path=%2F&amp;version=GBrelease%2F2026%2F09%2F10&amp;_a=contents" in html
    assert html.count("Shared change") == 1
    assert "General &lt;change&gt;" in html and "General <change>" not in html
    assert "Initial Release Notification Email" in html
    assert "background:#d1fadf" in html and ">Done</span>" in html
    assert "TODO" not in html
    assert result.notification["completion"]["status"] == "pass"
    assert len(result.notification["state_matches"]) == 6


def test_notice_test_redirect_clears_real_cc_and_tags_subject():
    result = notice.build(context(_state(), inputs=_inputs(
        send_to="pedroro@microsoft.com")))
    assert isinstance(result, NeedsSkill)
    assert result.payload["to"] == ["pedroro@microsoft.com"]
    assert result.payload["cc"] == []
    assert result.payload["subject"].startswith("[TEST → me] ")


def test_identify_auth_build_records_final_pipeline_evidence():
    from steps.rollout_start import identify_auth_build as S
    build = {
        "build_id": 180500000,
        "version": "6.2609.6000",
        "commit": COMMIT,
        "build_number": "6.2609.6000",
        "status": "completed",
        "result": "succeeded",
    }
    state = _state()
    result = S.build(context(state, inputs={"build": build}))
    assert result.kind == "done"
    evidence = result.updates[0].values["final_auth"]
    assert evidence["authenticator_build_id"] == "180500000"
    assert evidence["authenticator_version"] == "6.2609.6000"
    assert evidence["authenticator_commit"] == COMMIT


def test_identify_auth_build_blocks_when_no_run_found():
    from steps.rollout_start import identify_auth_build as S
    ctx = _production_context(_state())
    pipelines = replace(
        ctx.services.pipelines,
        find_final_auth_build=lambda branch: (True, None, "no run on release branch"),
    )
    result = S.build(replace(ctx, services=replace(ctx.services, pipelines=pipelines)))
    assert isinstance(result, Blocked)
    assert "no final Authenticator build found" in result.reason
    assert "email and Scout" in result.reason


def test_injected_source_evidence_cannot_target_production_recipients():
    result = notice.build(context(_state(), inputs=_inputs()))
    assert isinstance(result, Blocked)
    assert "requires send_to" in result.reason


def test_notice_blocks_incomplete_or_unbound_evidence():
    state = _state()
    bad_build = notice.build(context(
        state, inputs=_inputs(
            send_to="pedroro@microsoft.com",
            build={"build_id": 1, "version": "not-a-version", "commit": COMMIT})))
    assert isinstance(bad_build, Blocked) and "build identity" in bad_build.reason

    state.set_step("rollout_start", "wiki_payload", StepState(status="done"))
    no_payload = notice.build(context(
        state, inputs=_inputs(send_to="pedroro@microsoft.com")))
    assert isinstance(no_payload, Blocked) and "payload page link" in no_payload.reason

    state = _state()
    stale = _manifest()
    stale["target_commit"] = "9" * 40
    bad_manifest = notice.build(context(
        state, inputs=_inputs(send_to="pedroro@microsoft.com", manifest=stale)))
    assert isinstance(bad_manifest, Blocked) and "manifest" in bad_manifest.reason

    malformed = _manifest()
    malformed["did"][0]["url"] = None
    invalid_entry = notice.build(context(
        _state(), inputs=_inputs(send_to="pedroro@microsoft.com", manifest=malformed)))
    assert isinstance(invalid_entry, Blocked)
    assert "invalid did entry" in invalid_entry.reason

    malformed_flight = _manifest()
    del malformed_flight["flight_changes"]["default_changed"][0]["previous_default"]
    invalid_flight = notice.build(context(
        _state(), inputs=_inputs(
            send_to="pedroro@microsoft.com", manifest=malformed_flight)))
    assert isinstance(invalid_flight, Blocked)
    assert "invalid changed-flight entry" in invalid_flight.reason

    state = _state()
    state.pipeline_runs = {}
    no_release_build = notice.build(context(
        state, inputs=_inputs(send_to="pedroro@microsoft.com")))
    assert isinstance(no_release_build, Blocked)
    assert "pipeline 475778" in no_release_build.reason


def test_supplied_release_branch_blocks_without_successful_final_build():
    """The September branch currently has no releasable version; never derive one."""
    state = _state()
    result = notice.build(context(state, inputs={
        "send_to": "pedroro@microsoft.com", "build": None, "manifest": _manifest()}))
    assert isinstance(result, Blocked)
    assert "build identity/version/commit is incomplete" in result.reason


def test_notice_preparation_binds_exact_state_and_rejects_drift(tmp_path, monkeypatch):
    state = _state()
    _active_phase(state, "rollout_start")
    # _active_phase creates generic completed records; restore the exact notice sources.
    sourced = _state()
    for key in (
        "bug_bash.clone_plans_auth",
        "rollout_start.wiki_payload",
    ):
        phase, step = key.split(".", 1)
        state.set_step(phase, step, sourced.get_step(phase, step))
    state.set_step("rollout_start", "tag_authenticator", StepState(status="done"))
    state.set_step("rollout_start", "identify_auth_build", StepState(status="done"))
    spec = _inputs(send_to="pedroro@microsoft.com")
    orch = fresh_orchestrator(
        CONFIG, state, mocks={"rollout_start.notice": spec})
    monkeypatch.setattr(mocks, "load_mocks",
                        lambda: {"rollout_start.notice": spec})
    args = Namespace(
        phase="rollout_start", step="notice", release=state.release_id,
        param=[], reserve=False, executor=None,
    )
    out = prepare_step(args, state, orch)
    assert out["kind"] == "needs_skill" and out["permission_to_send"] is False
    assert len(out["notifications"]) == 1
    item = out["notifications"][0]
    assert item["tool"] == "workiq_send_email"
    assert item["payload"]["to"] == ["pedroro@microsoft.com"] and item["payload"]["cc"] == []
    delivery.offer(orch, item)
    state.pipeline_runs["rcs"][-1]["auth"]["build"]["run_id"] = "180400002"
    with pytest.raises(ValueError, match="source checkpoint changed"):
        delivery.claim(orch, item["id"], item["hash"], "test-worker")
