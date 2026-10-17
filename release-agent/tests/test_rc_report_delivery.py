"""RC report split flow: publish immutable HTML, notify the owner, then human gate."""
from argparse import Namespace
from copy import deepcopy
from datetime import date

import pytest

from orchestrator import cli_common as C, delivery as D, mocks, schedule
from orchestrator import cli
from orchestrator.commands.delivery_cmd import finish
from orchestrator.commands.step_action import prepare_step
from orchestrator.state import ReleaseState, StepState
from tests._context import context, fresh_orchestrator as Orchestrator
from tests._harness import CONFIG, _ready_for_rc_report, _seed_rc_pipeline
from steps.build_verify import rc_report as R, rc_report_publish as S, rc_report_notify as N


@pytest.fixture
def ready(monkeypatch):
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26", owner_email="test@example.com")
    _ready_for_rc_report(st)
    _seed_rc_pipeline(st, {"total": 100, "passed": 100, "failed": 0},
                      {"total": 100, "passed": 100, "failed": 0})
    orch = Orchestrator(CONFIG, st, mocks={}, as_of=date(2026, 9, 11))
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    monkeypatch.setattr(schedule, "now_local", lambda zone: orch.now_local.astimezone(zone))
    return st, orch


def published(st, link="https://sharepoint/report.html"):
    ctx = context(st)
    _, html, _, model = R.rc_email(ctx)
    lineage = R.report_lineage(ctx, html)
    evidence = S.publication_data({
        "report_link": link, "web_url": link, "drive_id": "drive-1", "item_id": "item-1",
        "e_tag": "etag-1", "size": len(html.encode("utf-8")),
    }, lineage, S.report_file_name(ctx, model, content_sha256=lineage["content_sha256"]))
    st.set_step("build_verify", "rc_report_publish", StepState(
        status="done",
        note=f"Published RC verification report: {link}",
        data=evidence,
        links=[{"name": "RC verification report", "url": link}],
    ))


def prepared_notify(st, orch):
    args = Namespace(phase="build_verify", step="rc_report_notify", release=st.release_id)
    item = prepare_step(args, st, orch)["notifications"][0]
    D.offer(orch, item)
    return item


def delivered_notify(st, orch, *, finalize=True):
    item = prepared_notify(st, orch)
    claim = D.claim(orch, item["id"], item["hash"], "test-worker")
    D.result(
        orch,
        item["id"],
        claim["execution_id"],
        "sent",
        "Simulated Scout bot accepted",
        D.exact_payload_receipt(item, {"id": "test-message"}),
    )
    if finalize:
        assert finish(orch, item["id"])
    return item


def test_report_link_email_does_not_apply_quality_gate(ready):
    st, orch = ready
    _seed_rc_pipeline(st, {"total": 100, "passed": 50, "failed": 50},
                      {"total": 100, "passed": 50, "failed": 50})
    before = deepcopy(st.pipeline_runs)
    published(st)
    item = delivered_notify(st, orch)

    assert item["completion"]["status"] == "pass"
    assert st.is_done("build_verify", "rc_report_notify")
    assert not st.is_done("build_verify", "rc_report_gate")
    assert st.pipeline_runs == before
    report = orch.status_report()
    assert report["current_step"] == "rc_report_gate"
    assert "build_verify.rc_report_gate" in report["pending_human"]
    assert orch.approve_gate("Owner accepts the documented 50% quality risk").kind == "ran"


def test_report_notification_keeps_provider_rates_separate(ready):
    st, orch = ready
    _seed_rc_pipeline(st, {"total": 100, "passed": 80, "failed": 20},
                      {"total": 100, "passed": 100, "failed": 0})
    published(st)
    outcome = N.build(orch.context("build_verify", N.ID))
    assert outcome.kind == "needs_skill"
    message = outcome.payload["message"]
    assert "MRWP ECS: 80.0% pass; MRWP Local: 100.0% pass" in message
    assert "Authenticator ECS: pass (97.0% lowest-suite pass)" in message
    assert "MRWP UI:" not in message
    assert R.rc_ui_gate(R.rc_report_model(context(st)))["pass_pct"] == 90.0


def test_publish_rc_report_command_records_sharepoint_link(ready, tmp_path, monkeypatch, capsys):
    from orchestrator.commands import rc_report_publish as command
    st, _ = ready
    C.save_state(st, str(tmp_path), st.release_id)
    monkeypatch.setattr(command, "publish_report", lambda target, content: {
        "drive_id": "drive",
        "item_id": "item",
        "web_url": "https://sharepoint/report.html",
        "report_link": "https://sharepoint/link",
        "size": len(content["html"].encode("utf-8")),
    })

    assert cli.main([
        "--runs-root", str(tmp_path),
        "publish-rc-report",
        "--release", st.release_id,
        "--execute",
        "--auto-approve",
        "--executor", "test",
    ]) == 0
    capsys.readouterr()
    saved = C.load_state(str(tmp_path), st.release_id)
    step = saved.get_step("build_verify", "rc_report_publish")
    assert step.status == "done"
    assert step.data["report_link"] == "https://sharepoint/link"
    assert step.links == [{"name": "RC verification report", "url": "https://sharepoint/link"}]
    expected = R.report_lineage(context(saved), R.rc_email(context(saved))[1])
    assert all(step.data[key] == value for key, value in expected.items())
    assert step.data["file_name"].endswith(f"-{expected['content_sha256']}.html")
    assert step.data["gate_preconditions"]
    saved_orch = Orchestrator(CONFIG, saved, mocks={}, as_of=date(2026, 9, 11))
    delivered_notify(saved, saved_orch)
    assert saved.get_step("build_verify", "rc_report_notify").data["report_lineage"] == expected
    assert saved_orch.approve_gate("Reviewed immutable report").kind == "ran"


def test_publish_preview_renders_checked_plan_without_state_or_provider_writes(
        ready, tmp_path, monkeypatch, capsys):
    import json
    from hashlib import sha256
    from orchestrator.commands import rc_report_publish as command

    st, _ = ready
    C.save_state(st, str(tmp_path), st.release_id)
    path = tmp_path / st.release_id / "release-state.json"
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Publication preview must not call SharePoint")

    monkeypatch.setattr(command, "publish_report", forbidden)
    assert cli.main([
        "--runs-root", str(tmp_path), "publish-rc-report", "--release", st.release_id,
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    operation = preview["plan"]["operations"][0]
    assert preview["permission_to_execute"] is False
    assert preview["review_hash"].startswith("sha256:")
    assert preview["plan"]["command"] == "publish-rc-report"
    assert operation["kind"] == "upload_sharepoint_html"
    digest = sha256(operation["content"]["html"].encode("utf-8")).hexdigest()
    assert operation["target"]["file_name"].endswith(f"-{digest}.html")
    assert path.read_bytes() == before


def test_report_link_delivery_is_bound_to_published_artifact(ready):
    st, orch = ready
    published(st, "https://sharepoint/original.html")
    item = prepared_notify(st, orch)
    step = st.get_step("build_verify", "rc_report_publish")
    step.data["report_link"] = "https://sharepoint/changed.html"
    st.set_step("build_verify", "rc_report_publish", step)
    with pytest.raises(ValueError, match="source checkpoint changed"):
        D.claim(orch, item["id"], item["hash"], "worker")


def test_human_gate_controls_phase_after_report_email(ready):
    st, orch = ready
    published(st)
    delivered_notify(st, orch)

    assert orch.current_phase_id() == "build_verify"
    assert orch.approve_gate("Owner reviewed linked report; proceed to Bug Bash").kind == "ran"
    assert st.is_done("build_verify", "rc_report_gate")
    assert orch.current_phase_id() == "bug_bash"


def test_gate_denial_blocks_without_resending_report(ready):
    st, orch = ready
    published(st)
    item = delivered_notify(st, orch)

    assert orch.deny_gate("Need Authenticator ECS rerun").kind == "gate"
    assert any(item["step"] == "build_verify.rc_report_gate" and item["decision"] == "denied"
               for item in st.gate_decisions)
    assert len(st.notification_deliveries[item["id"]]["attempts"]) == 1
    assert not D.claim(orch, item["id"], item["hash"], "other")["permission_to_send"]


def changed_source(st, change):
    if change == "metadata":
        st.pipeline_runs["checker"]["when"] = "new capture timestamp"
    else:
        _seed_rc_pipeline(st, {"total": 100, "passed": 50, "failed": 50},
                          {"total": 100, "passed": 50, "failed": 50})


@pytest.mark.parametrize("change", ["metadata", "quality"])
def test_stale_publication_cannot_prepare_notification(ready, change):
    st, orch = ready
    published(st)
    before = R.rc_email(context(st))[1]
    changed_source(st, change)
    if change == "metadata":
        assert R.rc_email(context(st))[1] == before
    outcome = N.build(orch.context("build_verify", N.ID))
    assert outcome.kind == "blocked"
    assert "source/content is stale" in outcome.reason


@pytest.mark.parametrize("change", ["metadata", "quality"])
@pytest.mark.parametrize("stage", ["claim", "finalize", "gate"])
def test_report_source_binding_survives_delivery_and_gate(ready, change, stage):
    st, orch = ready
    published(st)
    if stage == "gate":
        delivered_notify(st, orch)
        changed_source(st, change)
        decisions = deepcopy(st.gate_decisions)
        outcome = orch.approve_gate("Previously reviewed clean report")
        assert outcome.kind != "ran"
        assert "Gate precondition source changed" in outcome.message
        assert st.gate_decisions == decisions
        assert orch.current_phase_id() == "build_verify"
        assert not st.is_done("build_verify", "rc_report_gate")
        assert orch.deny_gate("Updated source needs review").kind == "gate"
        return
    item = prepared_notify(st, orch)
    if stage == "claim":
        changed_source(st, change)
        with pytest.raises(ValueError, match="source checkpoint changed"):
            D.claim(orch, item["id"], item["hash"], "worker")
        assert not st.notification_deliveries[item["id"]]["attempts"]
        return
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    changed_source(st, change)
    D.result(orch, item["id"], claim["execution_id"], "sent", "Accepted exact old payload",
             D.exact_payload_receipt(item, {"id": "old-message"}))
    assert finish(orch, item["id"])
    delivery = st.notification_deliveries[item["id"]]
    assert delivery["status"] == "sent"
    assert delivery["completion"]["status"] == "suppressed"
    assert delivery["completion"]["reason"] == "source checkpoint changed"
    assert not st.is_done("build_verify", N.ID)
    assert not st.is_done("build_verify", "rc_report_gate")
    assert not D.claim(orch, item["id"], item["hash"], "other")["permission_to_send"]


def test_gate_requires_delivery_of_the_current_publication(ready):
    st, orch = ready
    published(st)
    delivered_notify(st, orch)
    changed_source(st, "quality")
    published(st, "https://sharepoint/new-content.html")
    result = orch.approve_gate("Old notification is not the new report")
    assert result.kind != "ran"
    assert "Gate precondition source changed" in result.message
    assert not st.is_done("build_verify", "rc_report_gate")


def test_cli_gate_rejects_changed_source_after_persisted_delivery(
        ready, tmp_path, capsys):
    st, orch = ready
    published(st)
    delivered_notify(st, orch)
    changed_source(st, "quality")
    C.save_state(st, str(tmp_path), st.release_id)
    assert cli.main([
        "--runs-root", str(tmp_path), "approve", "--release", st.release_id,
        "--comment", "Reviewed the earlier clean report",
    ]) == 1
    assert "Gate precondition source changed" in capsys.readouterr().out
    saved = C.load_state(str(tmp_path), st.release_id)
    assert not saved.is_done("build_verify", "rc_report_gate")
    assert not saved.gate_decisions
    assert saved.get_step("build_verify", N.ID).data["report_lineage"]["source_digest"]


def test_skipping_report_notification_does_not_bypass_delivery_binding(ready):
    st, orch = ready
    published(st)
    assert orch.skip_step("build_verify", N.ID, "No delivery").kind == "ran"
    result = orch.approve_gate("Cannot approve an undelivered publication")
    assert result.kind != "ran"
    assert "Gate precondition" in result.message


def test_gate_preconditions_fail_closed_if_malformed(ready):
    st, orch = ready
    published(st)
    delivered_notify(st, orch)
    notification = st.get_step("build_verify", N.ID)
    notification.data["gate_preconditions"] = [{"path": []}]
    st.set_step("build_verify", N.ID, notification)
    result = orch.approve_gate("Malformed source binding")
    assert result.kind != "ran"
    assert "malformed" in result.message


def test_legacy_publication_without_receipt_lineage_requires_republication(ready):
    st, orch = ready
    st.set_step("build_verify", S.ID, StepState(
        status="done", data={"report_link": "https://sharepoint/legacy.html"}))
    outcome = N.build(orch.context("build_verify", N.ID))
    assert outcome.kind == "blocked"
    assert "publish the current report first" in outcome.reason


def test_same_rc_refresh_uses_immutable_content_addressed_file_names(ready):
    from orchestrator.commands.rc_report_publish import plan_rc_report_publish
    st, orch = ready
    first = plan_rc_report_publish(orch)
    assert first.as_dict() == plan_rc_report_publish(orch).as_dict()
    changed_source(st, "quality")
    second = plan_rc_report_publish(orch)
    assert st.pipeline_runs["rcs"][-1]["rc"] == 1
    assert first.parameters["file_name"] != second.parameters["file_name"]
    for plan in (first, second):
        assert plan.parameters["file_name"] == (
            f"rc-verification-report-{st.release_id}-rc1-"
            f"{plan.preconditions['content_sha256']}.html")


def test_sharepoint_upload_uses_existing_web_access_without_create_link(ready, monkeypatch):
    from orchestrator.commands import rc_report_publish as command
    _, orch = ready
    operation = command.plan_rc_report_publish(orch).operations[0]
    calls = []
    monkeypatch.setattr(command, "_graph_token", lambda: "offline-token")
    monkeypatch.setattr(command, "_drive_for_site", lambda *a: {"id": "drive"})

    def graph(method, url, **kwargs):
        calls.append((method, url))
        assert method == "PUT", "No createLink is needed or permitted after the upload"
        assert kwargs["body"] == operation.content["html"].encode("utf-8")
        return {"id": "item", "webUrl": "https://sharepoint/immutable.html",
                "eTag": "etag", "size": len(kwargs["body"])}

    monkeypatch.setattr(command, "_graph_json", graph)
    receipt = command.publish_report(operation.target, operation.content)
    assert receipt == {"drive_id": "drive", "item_id": "item",
                       "web_url": "https://sharepoint/immutable.html",
                       "report_link": "https://sharepoint/immutable.html",
                       "e_tag": "etag", "size": len(operation.content["html"].encode("utf-8"))}
    assert len(calls) == 1
    assert operation.target["file_name"] in calls[0][1]


@pytest.mark.parametrize("failure", ["fence", "source"])
def test_uploaded_receipt_persists_if_later_completion_fails(
        ready, tmp_path, monkeypatch, capsys, failure):
    from orchestrator.commands import rc_report_publish as command
    from orchestrator import write_review as W
    st, _ = ready
    C.save_state(st, str(tmp_path), st.release_id)
    calls = []

    def upload(target, content):
        calls.append(target["file_name"])
        return {"drive_id": "drive", "item_id": "uploaded", "e_tag": "etag",
                "web_url": "https://sharepoint/uploaded.html",
                "report_link": "https://sharepoint/uploaded.html",
                "size": len(content["html"].encode("utf-8"))}

    validate = W.WriteAuthorization.validate
    validations = []

    def fail_after_upload(authorization):
        validate(authorization)
        validations.append(True)
        if len(validations) == 2:
            saved = C.load_state(str(tmp_path), st.release_id)
            assert saved.get_step("build_verify", S.ID).data["item_id"] == "uploaded"
            if failure == "fence":
                raise ValueError("Later completion fence failed")
            changed_source(authorization._orch.state, "metadata")

    monkeypatch.setattr(command, "publish_report", upload)
    monkeypatch.setattr(W.WriteAuthorization, "validate", fail_after_upload)
    args = ["--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
            "--execute", "--auto-approve", "--executor", "test"]
    assert cli.main(args) == 2
    saved = C.load_state(str(tmp_path), st.release_id)
    record = saved.get_step("build_verify", S.ID)
    assert record.status == "blocked"
    assert record.execution["id"]
    assert record.data["item_id"] == "uploaded"
    assert record.data["e_tag"] == "etag"
    assert record.data["content_sha256"] in record.data["file_name"]
    assert not saved.is_done("build_verify", N.ID)
    assert cli.main(args) != 0
    assert len(calls) == 1
    capsys.readouterr()


def test_stale_write_review_rejected_before_upload(ready, tmp_path, monkeypatch, capsys):
    from orchestrator.commands import rc_report_publish as command
    from orchestrator import write_review as W
    st, orch = ready
    approved = W.review_hash(orch, "build_verify", S.ID, command.plan_rc_report_publish(orch))
    changed_source(st, "metadata")
    C.save_state(st, str(tmp_path), st.release_id)

    def no_upload(*args):
        pytest.fail("Stale source review must never upload")

    monkeypatch.setattr(command, "publish_report", no_upload)
    assert cli.main([
        "--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
        "--execute", "--review-hash", approved, "--approved-by", "owner",
    ]) == 1
    assert "Write review hash is stale" in capsys.readouterr().out
    assert not C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID).execution


def test_source_change_after_authorization_prevents_upload(ready, tmp_path, monkeypatch, capsys):
    from orchestrator.commands import rc_report_publish as command
    from orchestrator import write_review as W
    st, _ = ready
    C.save_state(st, str(tmp_path), st.release_id)
    authorize = W.authorize

    def authorize_then_change(*args, **kwargs):
        authorization = authorize(*args, **kwargs)
        changed_source(authorization._orch.state, "metadata")
        return authorization

    def no_upload(*args):
        pytest.fail("Changed source after authorization must not upload")

    monkeypatch.setattr(W, "authorize", authorize_then_change)
    monkeypatch.setattr(command, "publish_report", no_upload)
    assert cli.main([
        "--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
        "--execute", "--auto-approve", "--executor", "test",
    ]) == 2
    record = C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID)
    assert record.status == "blocked" and record.execution
    assert "item_id" not in record.data
    assert "source/content changed" in capsys.readouterr().out


def test_upload_without_provider_identity_is_not_recorded_as_publication(
        ready, tmp_path, monkeypatch, capsys):
    from orchestrator.commands import rc_report_publish as command
    st, _ = ready
    C.save_state(st, str(tmp_path), st.release_id)
    monkeypatch.setattr(command, "publish_report", lambda target, content: {
        "web_url": "https://sharepoint/unidentified.html",
        "report_link": "https://sharepoint/unidentified.html",
        "size": len(content["html"].encode("utf-8")),
    })
    assert cli.main([
        "--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
        "--execute", "--auto-approve", "--executor", "test",
    ]) == 2
    record = C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID)
    assert record.status == "blocked" and record.execution
    assert "item_id" not in record.data
    assert not record.links
    assert "requires provider identity" in capsys.readouterr().out
