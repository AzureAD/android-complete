"""End-to-end tests for the configuration-based environment profile system (`config/environment.yaml`)."""
from __future__ import annotations

import json
import os
from shutil import copyfile

import pytest

from orchestrator import cli, cli_common as C, environment as E, render
from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import notify_native_auth, send_invite
from steps.ccd import final_reminder, pr_reminder
from steps.finalize import final_status_email, release_announcement
from steps.preflight import flight_reminder, notice
from steps.rollout_start import wiki_payload
from tests._context import context as _ctx, fresh_orchestrator
from tools import testplans as T


def test_committed_environment_yaml_defaults_to_safe_test_profile(monkeypatch):
    """Out-of-the-box repository default MUST be `test` so unconfigured clones never hit live DLs."""
    monkeypatch.delenv("RELEASE_AGENT_PROFILE", raising=False)
    raw = E.load_raw_environment()
    assert raw["active_profile"] == "test"
    assert E.active_profile_name() == "test"


def test_test_vs_prod_profiles_route_all_communications_and_artifact_names(monkeypatch):
    st = ReleaseState(
        release_id="2026-11",
        ccd="2026-11-20",
        ccd_source="confirmed",
        owner_email="pedroro@microsoft.com",
        owner_name="Pedro Romero",
        timezone="America/Los_Angeles",
        versions={"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"},
        pipeline_runs={
            "rcs": [{
                "rc": 1,
                "ecs": {"run_id": 1001},
                "local": {"run_id": 1002},
                "auth": {"build": {"run_id": 2001, "rc": 1}, "test": {"run_id": 2002}},
            }]
        },
    )
    st.set_step("bug_bash", "clone_plans_broker", StepState(status="done", data={"plan_id": 9001}))
    st.set_step("bug_bash", "clone_plans_auth", StepState(status="done", data={"suite_id": 9002}))

    # --- 1. TEST PROFILE ---
    monkeypatch.setenv("RELEASE_AGENT_PROFILE", "test")
    orch_test = fresh_orchestrator(C.DEFAULT_CONFIG, st, step_defaults={})

    # Partner emails -> @owner + [TEST] subject prefix
    out_notice = notice.build(orch_test.context("preflight", "notice"))
    assert out_notice.payload["to"] == ["pedroro@microsoft.com"]
    assert out_notice.payload["subject"].startswith("[TEST] ")

    out_ccd_mail = final_reminder.build(orch_test.context("ccd", "final_reminder"))
    assert out_ccd_mail.payload["to"] == ["pedroro@microsoft.com"]
    assert out_ccd_mail.payload["subject"].startswith("[TEST] ")

    out_final_mail = final_status_email.build(
        orch_test.context("finalize", "final_status_email", inputs={"changelogs": {}})
    )
    assert out_final_mail.payload["to"] == ["pedroro@microsoft.com"]
    assert out_final_mail.payload["subject"].startswith("[TEST] ")

    # Calendar invite -> @owner + [TEST] subject prefix
    out_invite = send_invite.build(orch_test.context("bug_bash", "send_invite", inputs={"flags": "{}"}))
    assert out_invite.payload["attendees"] == ["pedroro@microsoft.com"]
    assert out_invite.payload["subject"] == "[TEST] November 2026 Release Bug Bash"

    # Teams group chats -> self-chat (48:notes)
    out_flight = flight_reminder.build(orch_test.context("preflight", "flight_reminder"))
    assert out_flight.payload["chatId"] == "48:notes"
    assert "[TEST" in out_flight.payload["content"]

    out_pr_rem = pr_reminder.build(orch_test.context("ccd", "pr_reminder"))
    assert out_pr_rem.payload["chatId"] == "48:notes"
    assert "[TEST" in out_pr_rem.payload["content"]

    # Release channel announcement -> self-chat (48:notes) with plain-text CC (zero @mentions)
    out_ann = release_announcement.build(orch_test.context("finalize", "release_announcement"))
    assert out_ann.tool == "workiq_send_chat_message"
    assert out_ann.payload["chatId"] == "48:notes"
    assert "mentions" not in out_ann.payload

    # Native Auth 1:1 DM -> owner_email (SendMessageToSelf)
    out_na = notify_native_auth.build(
        orch_test.context(
            "bug_bash",
            "notify_native_auth",
            parameters={"engineer": "silviu.petrescu@microsoft.com", "engineer_source": "schedule doc"},
        )
    )
    assert out_na.payload["userIdOrUpn"] == "pedroro@microsoft.com"
    assert out_na.notification["completion"]["data"]["redirected_to"] == "pedroro@microsoft.com"

    # Artifact prefixes -> [TEST]
    assert T.broker_plan_name("2026-11") == "[TEST] Android Monthly Release - Nov 2026"
    assert T.auth_suite_name("2026-11-20") == "[TEST] Android release/11/20/2026"
    assert wiki_payload.page_name(_ctx(st)) == "[TEST] November 2026 Release"

    # Status header shows safe test profile badge
    for item in orch_test.gate.config["items"]:
        st.readiness_items[item["id"]] = {"status": "pass" if item["verify"] == "auto" else "attested", "at": "2026-11-16T00:00:00Z"}
    from orchestrator.m365_capabilities import binding
    st.readiness_items["m365_access"]["capability_binding"] = binding(st)
    md_test = render.status_view(orch_test.status_report())
    assert "🧪 **test**" in md_test

    # --- 2. PROD PROFILE ---
    monkeypatch.setenv("RELEASE_AGENT_PROFILE", "prod")
    orch_prod = fresh_orchestrator(C.DEFAULT_CONFIG, st, step_defaults={})

    prod_notice = notice.build(orch_prod.context("preflight", "notice"))
    assert prod_notice.payload["to"] == ["authsdkrelease@microsoft.com", "androididentity@microsoft.com"]
    assert not prod_notice.payload["subject"].startswith("[TEST]")

    prod_invite = send_invite.build(orch_prod.context("bug_bash", "send_invite", inputs={"flags": "{}"}))
    assert prod_invite.payload["attendees"] == ["androididentity@microsoft.com", "idnadevexciamdublin@microsoft.com"]
    assert prod_invite.payload["subject"] == "November 2026 Release Bug Bash"

    prod_ann = release_announcement.build(orch_prod.context("finalize", "release_announcement"))
    assert prod_ann.tool == "microsoft_teams-SendMessageToChannel"
    assert "mentions" in prod_ann.payload

    prod_na = notify_native_auth.build(
        orch_prod.context(
            "bug_bash",
            "notify_native_auth",
            parameters={"engineer": "silviu.petrescu@microsoft.com", "engineer_source": "schedule doc"},
        )
    )
    assert prod_na.payload["userIdOrUpn"] == "silviu.petrescu@microsoft.com"
    assert "redirected_to" not in prod_na.notification["completion"]["data"]

    assert T.broker_plan_name("2026-11") == "Android Monthly Release - Nov 2026"
    assert T.auth_suite_name("2026-11-20") == "Android release/11/20/2026"
    assert wiki_payload.page_name(_ctx(st)) == "November 2026 Release"


def test_environment_and_set_profile_cli(tmp_path, monkeypatch, capsys):
    env_copy = tmp_path / "environment.yaml"
    copyfile(E.environment_path(), env_copy)
    monkeypatch.setenv("RELEASE_AGENT_ENVIRONMENT_FILE", str(env_copy))
    monkeypatch.delenv("RELEASE_AGENT_PROFILE", raising=False)

    assert cli.main(["--runs-root", str(tmp_path), "environment", "--json"]) == 0
    initial = json.loads(capsys.readouterr().out)
    assert initial["active_profile"] == "test" and not initial["is_production"]

    assert cli.main(["--runs-root", str(tmp_path), "set-profile", "--profile", "prod", "--json"]) == 0
    switched = json.loads(capsys.readouterr().out)
    assert switched["ok"] is True and switched["active_profile"] == "prod" and switched["is_production"] is True
