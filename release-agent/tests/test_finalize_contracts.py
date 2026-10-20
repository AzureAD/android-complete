"""Phase 4 provider and closing-email contracts; no connected release operations."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from orchestrator import knowledge, status_email
from orchestrator.state import ReleaseState
from steps.finalize import final_status_email, release_announcement
from tests._context import fresh_orchestrator as Orchestrator, invoke
from tests._harness import CONFIG, _active_step, _status_state, _PHASE_ORDER
from tools import pipelines as P, prs


def test_each_phase4_step_has_distinct_non_overwritten_help():
    phase = next(p for p in yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8"))["phases"]
                 if p["id"] == "finalize")
    document = yaml.compose(Path(knowledge.knowledge_path()).read_text(encoding="utf-8"))
    entries = {key.value: value for key, value in document.value}
    for step in phase["steps"]:
        key = "finalize." + step["id"]
        assert key in entries, key
        fields = [name.value for name, _ in entries[key].value]
        assert len(fields) == len(set(fields)), key
        help_text = knowledge.get_knowledge("finalize", step["id"])
        assert help_text["summary"] and help_text["what"] and help_text["how"]
    for gate in ("remove_rc_tags_gate", "publish_notes_gate"):
        how = knowledge.get_knowledge("finalize", gate)["how"]
        assert "--review-hash" in how and "--execution-id" in how
    assert "isolated" in knowledge.get_knowledge("finalize", "oneauth_common_pr")["how"]


@pytest.mark.parametrize("recipients", [["rehearsal-owner@example.test"], []])
def test_closing_email_uses_this_invocations_recipient_config(tmp_path, monkeypatch, recipients):
    canonical = Path(CONFIG)
    config = tmp_path / "phases.yaml"
    config.write_bytes(canonical.read_bytes())
    (tmp_path / "readiness.yaml").write_bytes(canonical.with_name("readiness.yaml").read_bytes())
    (tmp_path / "notifications.yaml").write_text(
        yaml.safe_dump({"status_email": {"recipients": recipients}}), encoding="utf-8")
    monkeypatch.setattr(prs, "broker_change_list", lambda *a, **kw: (True, [], ""))
    state = ReleaseState(release_id="2026-09", ccd="2026-09-09",
                         owner_email="rehearsal-owner@example.test")
    _active_step(state, "finalize", "final_status_email")
    state.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"})
    before = deepcopy(state.steps)
    orch = Orchestrator(str(config), state, mocks={})
    if recipients:
        result = final_status_email.build(orch.context("finalize", "final_status_email"))
        assert result.kind == "needs_skill"
        assert result.payload["to"] == recipients
    else:
        with pytest.raises(ValueError, match="Configure status_email.recipients"):
            final_status_email.build(orch.context("finalize", "final_status_email"))
    assert state.steps == before


@pytest.mark.parametrize("missing", [None, "rc_report_publish", "rc_report_notify", "rc_report_gate"])
def test_completed_rc_milestone_uses_current_publication_and_quality_gate_steps(missing):
    state = _status_state("finalize")
    assert "build_verify.rc_report" not in state.steps
    if missing:
        state.steps["build_verify." + missing]["status"] = "pending"
        state.gate_decisions = [
            item for item in state.gate_decisions if item["step"] != "build_verify.rc_report_gate"]
    selection = Orchestrator(CONFIG, state, mocks={}).scheduling()
    milestone = next(
        item for item in status_email.milestones(state, _PHASE_ORDER, selection)
        if item["label"] == "RC builds and test apks generated, automation run")
    assert (milestone["status"] == "complete") == (missing is None)


@pytest.mark.parametrize("payload", [
    {}, [], None,
    {"tagName": "vWRONG", "isDraft": False, "url": "https://example.test/release"},
    {"tagName": "v1.0.0", "url": "https://example.test/release"},
    {"tagName": "v1.0.0", "isDraft": None, "url": "https://example.test/release"},
    {"tagName": "v1.0.0", "isDraft": False},
])
def test_github_publication_requires_exact_tag_and_complete_provider_evidence(monkeypatch, payload):
    monkeypatch.setattr(prs, "_run", lambda *a, **kw: (0, json.dumps(payload), ""))
    ok, published, _, detail = prs.gh_release_exists("owner/repo", "v1.0.0")
    assert not ok and not published and detail


def test_repository_access_error_is_not_a_missing_release(monkeypatch):
    monkeypatch.setattr(prs, "_run", lambda *a, **kw: (
        1, "", "HTTP 404: Repository not found (https://api.github.com/repos/owner/repo)"))
    ok, published, _, detail = prs.gh_release_exists("owner/repo", "v1.0.0")
    assert not ok and not published and "Repository not found" in detail


@pytest.mark.parametrize("contents", [
    None, "groups: [", "groups: not-a-map", "groups:\n  OneAuth: not-a-list",
    "groups:\n  OneAuth:\n    - {name: Broken entry}",
])
def test_announcement_does_not_silently_drop_required_cc_registry(
        tmp_path, monkeypatch, contents):
    path = tmp_path / "announcement_cc.yaml"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(release_announcement, "_CC_PATH", str(path))
    state = ReleaseState(release_id="2026-09")
    state.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"})
    result = invoke(release_announcement.build, state)
    assert result.kind == "blocked" and "cc registry" in result.reason


@pytest.mark.parametrize("damage", ["wrong_build", "wrong_pipeline", "missing_pipeline", "malformed"])
def test_final1_requires_the_exact_mrwp_build(monkeypatch, damage):
    monkeypatch.setattr(P, "find_orchestrator_run", lambda *a, **kw: (True, {
        "id": 100, "status": "inProgress", "tags": ["Final1=200"],
    }, ""))
    monkeypatch.setattr(P, "get_timeline", lambda *a, **kw: (True, [
        {"id": "stage", "type": "Stage", "identifier": "PublishGitHubReleaseNotes",
         "name": "Publish GitHub Release Notes", "state": "inProgress", "result": None},
        {"id": "approval", "parentId": "stage", "type": "Checkpoint.Approval",
         "state": "inProgress"},
    ], ""))
    run = {"id": 200, "definitionId": P.MRWP_DEF, "status": "completed", "result": "succeeded"}
    if damage == "wrong_build":
        run["id"] = 201
    elif damage == "wrong_pipeline":
        run["definitionId"] = P.MRWP_DEF + 1
    elif damage == "missing_pipeline":
        run.pop("definitionId")
    else:
        run = []
    monkeypatch.setattr(P, "_az_json", lambda *a, **kw: (True, run, ""))
    ok, result, detail = P.orchestrator_finalization_status(
        P.ENGINEERING_ORG, P.ENGINEERING_PROJECT, "2026-09", "PublishGitHubReleaseNotes")
    assert not ok and result is None and detail
