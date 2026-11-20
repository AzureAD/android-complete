# Copyright (c) Microsoft Corporation. All rights reserved.
"""Tests for deterministic pipeline/test failure evidence collection (`diagnose`),
AI root-cause triage persistence (`record-diagnosis`), and automatic pre-CTA / pre-RC-report
integration in `release-drainer`."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from orchestrator import cli, cli_common as C, diagnosis as DIAG, drain as D, render as R
from orchestrator.state import ReleaseState, StepState
from tests._context import fresh_orchestrator as Orchestrator
from tests._harness import CONFIG, _active_phase
from tools import pipelines as P


LA = ZoneInfo("America/Los_Angeles")


def _mark_prior_phases_done(st: ReleaseState) -> None:
    for sid in ("notice", "flight_reminder", "lockdown", "breaking", "cg", "oneauth_access", "cron", "vitals"):
        st.set_step("preflight", sid, StepState(status="done"))
    for sid in ("final_reminder", "pr_reminder", "localization", "localization_pr"):
        st.set_step("ccd", sid, StepState(status="done"))


@pytest.mark.real_revision
def test_pipeline_block_pauses_cta_for_ai_diagnosis_then_delivers_enriched_cta_and_caches(
    tmp_path, monkeypatch, capsys
):
    rid = "2026-09"
    st = ReleaseState(
        release_id=rid,
        ccd="2026-09-09",
        timezone="America/Los_Angeles",
        owner_email="owner@example.test",
        owner_name="Pedro",
    )
    _active_phase(st, "build_verify")
    _mark_prior_phases_done(st)
    st.set_step("build_verify", "checker_fired", StepState(status="done", data={"run_id": "9001"}))
    st.set_step(
        "build_verify",
        "orchestrator_health",
        StepState(
            status="blocked",
            note="Orchestrator run 9002 UNHEALTHY — stage 'Validate Branch and Versions' failed.",
            data={"run_id": "9002"},
            links=[{"name": "Release Orchestrator run", "url": "https://identitydivision.visualstudio.com/Engineering/_build/results?buildId=9002"}],
        ),
    )
    st.pipeline_runs = {"checker": {"run_id": "9001"}, "orchestrator": {"run_id": "9002"}, "rcs": []}
    C.save_state(st, str(tmp_path), rid)

    monkeypatch.setattr(
        P,
        "get_timeline",
        lambda org, project, bid, timeout=45: (
            True,
            [
                {"id": "stg-1", "type": "Stage", "name": "Validate Branch and Versions", "state": "completed", "result": "failed", "issues": [{"message": "1 task(s) failed"}]},
                {"id": "job-1", "parentId": "stg-1", "type": "Job", "name": "ValidateVersionsJob", "state": "completed", "result": "failed"},
                {
                    "id": "tsk-1",
                    "parentId": "job-1",
                    "type": "Task",
                    "name": "Run get_next_version.py",
                    "order": 3,
                    "state": "completed",
                    "result": "failed",
                    "log": {"id": 17},
                    "issues": [{"message": "Tag msal-8.5.0 already exists on AzureAD/microsoft-authentication-library-for-android"}],
                },
            ],
            "",
        ),
    )
    monkeypatch.setattr(
        P,
        "_ado_rest_get_text",
        lambda url, timeout=45: (
            True,
            "2026-09-10T08:01:00.0000000Z Checking tags...\n"
            "2026-09-10T08:01:02.0000000Z ##[error]ValueError: Tag msal-8.5.0 already exists on remote!\n",
            "",
        ),
    )

    sent_emails: list[dict] = []
    monkeypatch.setattr(
        "orchestrator.commands.deliver_cmd.deliver_descriptor_items",
        lambda runs_root, release, cfg, items, **kw: [
            sent_emails.append(it) or {"status": "done", "notification_id": it["id"]}
            for it in items
        ],
    )

    # 1) Initial `run_drain(..., deliver=True)` MUST hold CTA delivery and emit `scope: "ai_diagnosis"`!
    t1 = datetime(2026, 9, 10, 9, 0, tzinfo=LA)
    first = D.run_drain(
        str(tmp_path), rid, CONFIG, deliver=True, now=t1
    )
    assert first["status"] == "awaiting_host_work"
    assert sent_emails == []
    assert len(first["pending_host_work"]) == 1
    hw = first["pending_host_work"][0]
    assert hw["scope"] == "ai_diagnosis"
    assert hw["step"] == "build_verify.orchestrator_health"

    # 2) `cli diagnose --json` extracts the exact task, log excerpt, deep link, and `1ES-Pipelines/` YAML hint.
    assert cli.main([
        "--config", CONFIG,
        "--runs-root", str(tmp_path),
        "diagnose",
        "--release", rid,
        "--phase", "build_verify",
        "--step", "orchestrator_health",
        "--json",
    ]) == 0
    diag_ev = json.loads(capsys.readouterr().out)
    assert diag_ev["failure_signature"] == hw["failure_signature"]
    assert "AuthClientAndroidPipelines/production/monthly-release/release-orchestrator.yml" in diag_ev["skill_hints"]["workspace_files_to_inspect"]
    assert "AuthClientAndroidPipelines/templates/release/validate-branches-and-versions.yml" in diag_ev["matched_yaml_files"]
    assert "release-helper" in diag_ev["skill_hints"]["recommended_skills"]
    ft = diag_ev["builds"][0]["failed_tasks"][0]
    assert ft["task_name"] == "Run get_next_version.py"
    assert "&j=job-1&t=tsk-1" in ft["task_log_url"]
    assert any("Tag msal-8.5.0 already exists" in ln for ln in ft["log_excerpt"])

    # 3) Scout records AI triage via `record-diagnosis`, then finalizes the drain run.
    det_lnk = diag_ev["deterministic_brief"]["links"][0]
    assert cli.main([
        "--config", CONFIG,
        "--runs-root", str(tmp_path),
        "--scheduled",
        "record-diagnosis",
        "--release", rid,
        "--phase", "build_verify",
        "--step", "orchestrator_health",
        "--signature", diag_ev["failure_signature"],
        "--confidence", "high",
        "--headline", "Tag collision (`msal-8.5.0`) in Validate Branch and Versions",
        "--root-cause", "`get_next_version.py` aborted because tag `msal-8.5.0` already exists on MSAL.",
        "--evidence", "Task `Run get_next_version.py` (log #17): `Tag msal-8.5.0 already exists on remote!`",
        "--action", "Bump patch/minor version in MSAL gradle.properties or remove stale tag, then rerun pipeline 2828.",
        "--skill", "release-helper",
        "--link", f"{det_lnk['name']}={det_lnk['url']}",
        "--json",
    ]) == 0
    capsys.readouterr()

    finalized = D.run_drain(
        str(tmp_path), rid, CONFIG, finalize_run=first["run_id"], deliver=True, now=t1
    )
    assert finalized["finalized"] is True
    assert len(sent_emails) == 1
    email_html = sent_emails[0]["payload"]["body"]
    assert "AI Root-Cause Triage" in email_html
    assert "Tag collision (`msal-8.5.0`)" in email_html
    assert "get_next_version.py" in email_html
    assert "&amp;j=job-1&amp;t=tsk-1" in email_html

    bot_msg = finalized["scout_bot_cta"]["message"]
    assert "🤖 AI Triage (HIGH confidence)" in bot_msg
    assert "Tag collision (`msal-8.5.0`)" in bot_msg

    # `/release-agent status` also displays the recorded AI triage under Actionable fixes!
    _, orch_after = C.load_orch(str(tmp_path), rid, CONFIG, t1)
    status_md = R.status_view(orch_after.status_report())
    assert "**🤖 AI Triage (HIGH confidence):** Tag collision (`msal-8.5.0`)" in status_md

    # 4) Next 2h drain slot sees identical `failure_signature` -> cached! Does NOT re-queue `ai_diagnosis`.
    second = D.run_drain(
        str(tmp_path), rid, CONFIG, deliver=True, now=datetime(2026, 9, 10, 11, 0, tzinfo=LA)
    )
    assert second["status"] == "parked"
    assert second["finalized"] is True


@pytest.mark.real_revision
def test_rc_test_failures_cluster_overlap_and_embed_into_rc_report_before_send(tmp_path, monkeypatch, capsys):
    from orchestrator import mocks as _mocks_mod
    from steps.build_verify import _common as K
    from tests._harness import _pipeline, _ready_for_rc_report, _seed_auth
    from tests._mrwp_evidence import PROD, snapshot

    monkeypatch.setattr(_mocks_mod, "load_mocks", lambda *a, **k: {})
    rid = "2026-09"
    st = ReleaseState(
        release_id=rid,
        ccd="2026-09-09",
        timezone="America/Los_Angeles",
        owner_email="owner@example.test",
        owner_name="Pedro",
    )
    _ready_for_rc_report(st)
    _pipeline(K.stash_checker, st, "9001", "2026-09-09T06:00")
    _pipeline(K.stash_orchestrator, st, "9002", parked=True)
    st.record_versions({"common": "4.1.0", "msal": "8.5.0", "broker": "14.2.0"})

    ecs_snap = snapshot("9101", {PROD: [
        *[(f"test_{1000 + i}_Pass", "Passed") for i in range(17)],
        ("FlakyNetworkTest", "Failed"),
        ("FlakyNetworkTest", "Passed"),
        ("BrokerWpJJoinTest", "Failed"),
        ("BrokerSharedDeviceTest", "Failed"),
        ("BrokerEcsFlightOnlyTest", "Failed"),
    ]})
    local_snap = snapshot("9102", {PROD: [
        *[(f"test_{1000 + i}_Pass", "Passed") for i in range(19)],
        ("BrokerWpJJoinTest", "Failed"),
    ]})
    # Attach real `errorMessage` + `stackTrace` on failed attempts to exercise clustering.
    err_by_title = {
        "BrokerWpJJoinTest": "com.microsoft.identity.labapi.LabApiException: HTTP 429 LabUser throttled for id=99182",
        "BrokerSharedDeviceTest": "com.microsoft.identity.labapi.LabApiException: HTTP 429 LabUser throttled for id=44210",
        "BrokerEcsFlightOnlyTest": "java.lang.AssertionError: Expected ECS flag X=true",
    }
    for snap_obj, id_suffix in ((ecs_snap, "1"), (local_snap, "2")):
        for s_obj in snap_obj["tests"]["suites"]:
            for tr in s_obj["test_results"]:
                if tr["verdict"] == "Failed":
                    tr["attempts"][0]["errorMessage"] = err_by_title[tr["title"]] + id_suffix
                    tr["attempts"][0]["stackTrace"] = "at com.microsoft.identity.labapi.LabClient.fetchUser(LabClient.java:88)"

    _pipeline(K.stash_mrwp, st, "ECS", ecs_snap)
    _pipeline(K.stash_mrwp, st, "Local", local_snap)
    _seed_auth(st)
    st.set_step("build_verify", "telemetry_verify", StepState(status="done", data={"version": "6.2609.1234", "rows": 42}))
    C.save_state(st, str(tmp_path), rid)

    # 1) Verify clustering normalizes dynamic IDs (`id=991821`, `id=442101`, `id=991822` -> 1 cluster of 3x!)
    clusters = DIAG.collect_rc_test_clusters(str(tmp_path), rid, st)
    assert clusters["total_failed_tests"] >= 4
    lab_cluster = next(c for c in clusters["clusters"] if "LabApiException" in c["signature"])
    assert lab_cluster["count"] == 3
    assert lab_cluster["hint"] == "lab_or_test_account_infra"
    assert clusters["provider_overlap"]["both_ecs_and_local_samples"] == ["BrokerWpJJoinTest"]
    assert clusters["provider_overlap"]["ecs_only_samples"] == ["BrokerEcsFlightOnlyTest", "BrokerSharedDeviceTest"]
    assert clusters["flaky_recovered_count"] >= 1

    # 2) `run_drain(..., deliver=True)` queues `ai_diagnosis` BEFORE `scout_notification` (`rc_report_notify`)!
    r1 = D.run_drain(
        str(tmp_path), rid, CONFIG, deliver=True, now=datetime(2026, 9, 10, 11, 0, tzinfo=LA)
    )
    assert r1["status"] == "awaiting_host_work"
    scopes = [(w["scope"], w["step"]) for w in r1["pending_host_work"]]
    assert scopes == [
        ("ai_diagnosis", "build_verify.rc_report_gate"),
        ("scout_notification", "build_verify.rc_report_notify"),
    ]

    # 3) Record AI diagnosis on `rc_report_gate` and verify `rc_report_notify` embeds it in BOTH bot message & HTML email!
    DIAG.record_step_diagnosis(
        str(tmp_path),
        rid,
        CONFIG,
        phase_id="build_verify",
        step_id="rc_report_gate",
        signature=r1["pending_host_work"][0]["failure_signature"],
        headline="3/4 Broker UI failures caused by LabUser HTTP 429 throttling; 1 ECS-only flag assertion",
        root_cause="LabUserManager API returned HTTP 429 across ECS & Local; `BrokerEcsFlightOnlyTest` failed only on ECS.",
        confidence="high",
        evidence_bullets=["3x LabApiException HTTP 429 across ECS + Local", "1x ECS-only assertion in `BrokerEcsFlightOnlyTest`"],
        recommended_actions=["Verify ECS flight X for `BrokerEcsFlightOnlyTest`; Lab 429s are infra throttling."],
        skills_used=["release-helper"],
    )
    assert cli.main([
        "--config", CONFIG,
        "--runs-root", str(tmp_path),
        "step-action",
        "--release", rid,
        "--phase", "build_verify",
        "--step", "rc_report_notify",
    ]) == 0
    step_out = json.loads(capsys.readouterr().out)
    assert step_out.get("kind") == "needs_skill", step_out
    msg_out = (step_out.get("mcp_arguments") or {}).get("message") or step_out["notifications"][0]["payload"]["message"]
    assert "🤖 AI Triage (HIGH confidence)" in msg_out
    assert "3/4 Broker UI failures caused by LabUser HTTP 429" in msg_out
    assert "RC Test Failure Triage" in step_out["notifications"][1]["payload"]["body"]
    assert "LabUserManager API returned HTTP 429" in step_out["notifications"][1]["payload"]["body"]


@pytest.mark.real_revision
def test_telemetry_verify_zero_rows_diagnosis_includes_kql_and_elapsed_time(tmp_path):
    rid = "2026-09"
    st = ReleaseState(
        release_id=rid,
        ccd="2026-09-09",
        timezone="America/Los_Angeles",
        owner_email="owner@example.test",
    )
    _active_phase(st, "build_verify")
    _mark_prior_phases_done(st)
    for sid in ("checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local"):
        st.set_step("build_verify", sid, StepState(status="done"))
    st.set_step(
        "build_verify",
        "auth_ecs",
        StepState(
            status="done",
            completed_at=datetime(2026, 9, 10, 10, 15, tzinfo=LA).astimezone(timezone.utc).isoformat(),
            data={"apk_version": "6.2609.1234"},
        ),
    )
    st.set_step(
        "build_verify",
        "telemetry_verify",
        StepState(
            status="blocked",
            note="0 telemetry rows for Authenticator 6.2609.1234 over 30d.",
            data={"version": "6.2609.1234", "rows": 0},
        ),
    )
    C.save_state(st, str(tmp_path), rid)

    _, orch = C.load_orch(str(tmp_path), rid, CONFIG, datetime(2026, 9, 10, 11, 0, tzinfo=LA))
    ev = DIAG.collect_step_evidence(
        orch, str(tmp_path), CONFIG, "build_verify", "telemetry_verify", fetch_live_logs=False
    )
    assert ev["telemetry"]["minutes_since_auth_ecs"] == 45
    assert "loadaccountsoperations" in ev["telemetry"]["diagnostic_kql"]
    assert "kusto-analyst" in ev["skill_hints"]["recommended_skills"]
    assert "(45m elapsed since `auth_ecs` completed)" in ev["deterministic_brief"]["headline"]


@pytest.mark.real_revision
def test_mrwp_blocked_with_empty_data_resolves_build_from_links_follows_child_pipeline_and_rejects_parrot(
    tmp_path, monkeypatch, capsys
):
    """Exact regression test for live 2026-11 MRWP 1725622/1725623:
    1. `mrwp_ecs` blocks with `data={}` and `run_id` ONLY in `links` + `note`.
    2. `diagnose` resolves `build_id=1725622`, inspects parent `/timeline`, follows child build
       `#1725630` queued by `queue-build.ps1`, and loads `build-and-publish-utilities.yml` from disk.
    3. `record-diagnosis` REJECTS shallow parroted summaries (`... is blocked` / `15 stages never ran`).
    4. When no timeline/log signal is available (offline), no redundant LOW-confidence parrot card is rendered.
    5. When real triage is present, the CTA email & markdown condense the 15-stage wall of text and extract
       clean clickable `[Recovery TSG]` / `[Escalate (Teams)]` links with `<br>` separation.
    """
    rid = "2026-11"
    st = ReleaseState(
        release_id=rid,
        ccd="2026-11-19",
        timezone="America/Los_Angeles",
        owner_email="pedroro@microsoft.com",
    )
    _active_phase(st, "build_verify")
    _mark_prior_phases_done(st)
    st.set_step("build_verify", "checker_fired", StepState(status="done"))
    st.set_step("build_verify", "orchestrator_health", StepState(status="done"))
    mrwp_note = (
        "MRWP ECS run 1725622 did NOT run to completion — 15 stage(s) never ran "
        "(pending/skipped/canceled): Instrumented Tests, AADAuthenticator, Promote published packages, "
        "Authenticator Broker Apk Generation, Company Portal Broker Apk Generation. "
        "A stage that never ran means the pipeline aborted partway.\n"
        "→ Each failed stage's output describes the root cause + corrective action. Follow it, then click "
        "Retry on the failed stage. Recovery TSG: https://eng.ms/docs/recovery-tsg . "
        "If unresolved within 2h, escalate: https://teams.microsoft.com/l/chat/19:core@thread.v2/conversations"
    )
    st.set_step(
        "build_verify",
        "mrwp_ecs",
        StepState(
            status="blocked",
            note=mrwp_note,
            links=[{"name": "MRWP ECS run", "url": "https://identitydivision.visualstudio.com/Engineering/_build/results?buildId=1725622"}],
            data={},  # `data` is empty on incomplete MRWP!
        ),
    )
    st.set_step(
        "build_verify",
        "mrwp_local",
        StepState(
            status="blocked",
            note=mrwp_note.replace("ECS run 1725622", "Local run 1725623"),
            links=[{"name": "MRWP Local run", "url": "https://identitydivision.visualstudio.com/Engineering/_build/results?buildId=1725623"}],
            data={},
        ),
    )
    C.save_state(st, str(tmp_path), rid)

    # Offline check: when timeline is not fetched, `effective_diagnosis_for_step` returns `None` (never a LOW-confidence parrot!).
    _, orch_offline = C.load_orch(str(tmp_path), rid, CONFIG, datetime(2026, 11, 20, 9, 0, tzinfo=LA))
    assert DIAG.effective_diagnosis_for_step(
        orch_offline, str(tmp_path), CONFIG, "build_verify", "mrwp_ecs", fetch_live_logs=False
    ) is None

    # Mock parent build `1725622` -> stage `Utility Libraries` queues child build `1725630` via `queue-build.ps1` -> child fails compiling `LabApiUtilities`!
    def fake_timeline(org, project, bid, timeout=45):
        if int(bid) == 1725622:
            return (True, [
                {"id": "s0", "type": "Stage", "name": "Add flight tag", "order": 1, "state": "completed", "result": "succeeded"},
                {"id": "s1", "type": "Stage", "name": "Utility Libraries", "identifier": "publishCommonUtilities", "order": 2, "state": "completed", "result": "failed"},
                {"id": "s2", "type": "Stage", "name": "Instrumented Tests", "order": 3, "state": "completed", "result": "skipped"},
                {"id": "j1", "parentId": "s1", "type": "Job", "name": "Update Testing libraries"},
                {"id": "t1", "parentId": "j1", "type": "Task", "name": "Queue and wait for utility libraries", "order": 2, "result": "failed", "log": {"id": 9},
                 "issues": [{"message": "Build https://identitydivision.visualstudio.com//Engineering/_build/results?buildId=1725630 did not complete successfully, BuildResult: failed."}]},
            ], "")
        if int(bid) == 1725630:
            return (True, [
                {"id": "cs1", "type": "Stage", "name": "Build and Publish LabApiUtilities", "order": 1, "state": "completed", "result": "failed"},
                {"id": "cj1", "parentId": "cs1", "type": "Job", "name": "PublishJob"},
                {"id": "ct1", "parentId": "cj1", "type": "Task", "name": "Gradle publish", "order": 4, "result": "failed", "log": {"id": 22},
                 "issues": [{"message": "Execution failed for task ':LabApiUtilities:publishMavenPublicationToVstsRepository': 403 Forbidden"}]},
            ], "")
        return (False, None, "unexpected build")

    def fake_log(url, timeout=45):
        if "/builds/1725622/logs/9" in url:
            return (True, "Build is queued: https://identitydivision.visualstudio.com//Engineering/_build/results?buildId=1725630\n##[error]Build https://identitydivision.visualstudio.com//Engineering/_build/results?buildId=1725630 did not complete successfully, BuildResult: failed.\n", "")
        if "/builds/1725630/logs/22" in url:
            return (True, "##[error]Execution failed for task ':LabApiUtilities:publishMavenPublicationToVstsRepository': status code 403 Forbidden from Azure Artifacts feed\n", "")
        return (False, "", "no log")

    monkeypatch.setattr(P, "get_timeline", fake_timeline)
    monkeypatch.setattr(P, "_ado_rest_get_text", fake_log)

    ev = DIAG.collect_step_evidence(
        orch_offline, str(tmp_path), CONFIG, "build_verify", "mrwp_ecs", fetch_live_logs=True
    )
    assert len(ev["builds"]) == 1 and ev["builds"][0]["build_id"] == 1725622
    assert len(ev["builds"][0]["child_builds"]) == 1
    assert ev["builds"][0]["child_builds"][0]["build_id"] == 1725630
    assert "AuthClientAndroidPipelines/templates/build/utilities/build-and-publish-utilities.yml" in ev["matched_yaml_files"]
    assert "AuthClientAndroidPipelines/templates/build/utilities/build-and-publish-utilities.yml" in ev["matched_yaml_snippets"]
    assert ev["sibling_context"]["sibling_step"] == "build_verify.mrwp_local"
    assert "child build #1725630" in ev["deterministic_brief"]["headline"]
    assert "403 Forbidden" in ev["deterministic_brief"]["root_cause"]

    # Parroted AI diagnosis MUST be rejected!
    with pytest.raises(ValueError, match="Rejected redundant/parroted AI diagnosis"):
        DIAG.record_step_diagnosis(
            str(tmp_path),
            rid,
            CONFIG,
            phase_id="build_verify",
            step_id="mrwp_ecs",
            signature=ev["failure_signature"],
            headline="Verify MRWP (ECS) ran to completion + tests is blocked",
            root_cause="MRWP ECS run 1725622 aborted before 15 downstream stages ran.",
        )

    # Real deep AI diagnosis succeeds and renders cleanly without wall-of-text duplication!
    rec_res = DIAG.record_step_diagnosis(
        str(tmp_path),
        rid,
        CONFIG,
        phase_id="build_verify",
        step_id="mrwp_ecs",
        signature=ev["failure_signature"],
        headline="Stage 'Utility Libraries' failed in child build #1725630 (`Gradle publish` 403 Forbidden)",
        root_cause="Child utility pipeline #1725630 (definition 1464) returned HTTP 403 publishing `LabApiUtilities` to Azure Artifacts.",
        confidence="high",
        evidence_bullets=["Child build #1725630 task `Gradle publish`: 403 Forbidden on `:LabApiUtilities:publishMavenPublicationToVstsRepository`"],
        recommended_actions=["Verify feed write permissions for pipeline #1464, then click 'Retry stage' on `Utility Libraries` in both #1725622 and #1725623."],
        links=ev["deterministic_brief"]["links"],
    )
    st_after, orch_after = C.load_orch(str(tmp_path), rid, CONFIG, datetime(2026, 11, 20, 9, 0, tzinfo=LA))
    waiting = D.classify_waiting(
        orch_after, datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc), runs_root=str(tmp_path), config_path=CONFIG
    )
    snap = D.capture_snapshot(orch_after)
    _, md_cta, html_cta = D.render_cta_messages(
        st_after, snap, [], D._actionable_waiting_items(waiting), {"level": "normal", "unattended_business_hours": 0.5, "external_cutoff": None}
    )
    # 15-stage list is compacted on the diagnosed item, links are clean, and `<br>` separates the triage card!
    assert "15 stage(s) never ran (pending/skipped/canceled) — pipeline aborted partway." in md_cta
    assert "[Recovery TSG](https://eng.ms/docs/recovery-tsg)" in md_cta
    assert "<br><div" in html_cta
    assert "child build #1725630" in html_cta


