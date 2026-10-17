"""RC report split flow: publish immutable HTML, notify the owner, then human gate."""
from argparse import Namespace
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path

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


def publication_observation(tool, arguments, result):
    return {
        "version": 1, "tool": tool, "arguments": arguments,
        "account": "test@example.com", "observed_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }


def publication_proof(target, html):
    url = (f"{target['site_url']}/Shared Documents/{target['folder']}/"
           f"{target['file_name']}")
    size = len(html.encode("utf-8"))
    return {
        "item_url": url,
        "resolve": publication_observation("workiq_resolve_m365_link", {"url": url}, {
            "driveId": "drive", "itemId": "item",
            "item": {"id": "item", "name": target["file_name"], "webUrl": url,
                     "size": size, "eTag": "etag", "parentReference": {"driveId": "drive"}},
        }),
        "download": publication_observation("workiq_download_file", {
            "fileId": "drive,item", "maxBytes": size + 1,
        }, {"encoding": "text", "content": html}),
    }


def browser_upload_request(target, local_path):
    return {
        "version": 1, "route": "browser_new_file", "local_path": str(local_path),
        "library_context": {
            **{key: target[key] for key in ("site_url", "library", "folder")},
            "folder_url": f"{target['site_url']}/Shared Documents/{target['folder']}",
            "owner_confirmed": True, "confirmed_by": "test@example.com",
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def issue_publication(ready, tmp_path, capsys, *, existing=True, browser=False):
    from orchestrator.commands import rc_report_publish as command
    st, orch = ready
    C.save_state(st, str(tmp_path), st.release_id)
    operation = command.plan_rc_report_publish(orch).operations[0]
    arguments = ["--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
                 "--execute", "--auto-approve", "--executor", "test"]
    if existing or browser:
        local = tmp_path / operation.target["file_name"]
        local.write_bytes(operation.content["html"].encode("utf-8"))
        request = (browser_upload_request(operation.target, local) if browser else {
            "version": 1, "route": "existing_item", "local_path": str(local),
            **publication_proof(operation.target, operation.content["html"]),
        })
        path = tmp_path / "upload-request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        arguments += ["--upload-request-file", str(path)]
    result = cli.main(arguments)
    output = capsys.readouterr().out
    handoff = json.loads(output[output.index("{"):])
    assert result == (0 if existing or browser else 2), output
    return handoff, arguments


def publication_receipt(tmp_path, handoff):
    value = {"receipt_binding": handoff["receipt_binding"],
             **publication_proof(handoff["receipt_binding"]["target"], handoff["html"])}
    path = tmp_path / "publication-receipt.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


def receipt_arguments(tmp_path, handoff, path):
    return ["--runs-root", str(tmp_path), S.WRITE_COMMAND,
            "--release", handoff["receipt_binding"]["release"],
            "--execution-id", handoff["execution_id"], "--receipt-file", str(path)]


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
    st, _ = ready
    handoff, _ = issue_publication(ready, tmp_path, capsys)
    path, _ = publication_receipt(tmp_path, handoff)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    capsys.readouterr()
    saved = C.load_state(str(tmp_path), st.release_id)
    step = saved.get_step("build_verify", "rc_report_publish")
    assert step.status == "done"
    assert step.data["report_link"] == handoff["action"]["existing_item"]["report_link"]
    assert step.links == [{"name": "RC verification report", "url": step.data["report_link"]}]
    expected = R.report_lineage(context(saved), R.rc_email(context(saved))[1])
    assert all(step.data[key] == value for key, value in expected.items())
    assert step.data["file_name"].endswith(f"-{expected['content_sha256']}.html")
    assert step.data["gate_preconditions"]
    saved_orch = Orchestrator(CONFIG, saved, mocks={}, as_of=date(2026, 9, 11))
    delivered_notify(saved, saved_orch)
    assert saved.get_step("build_verify", "rc_report_notify").data["report_lineage"] == expected
    assert saved_orch.approve_gate("Reviewed immutable report").kind == "ran"


@pytest.mark.parametrize("encoding", ["text", "base64"])
def test_canonical_resolution_requests_metadata_and_accepts_native_download(
        ready, tmp_path, capsys, encoding):
    import base64
    st, _ = ready
    handoff, _ = issue_publication(ready, tmp_path, capsys, browser=True)
    path, value = publication_receipt(tmp_path, handoff)
    metadata = value["resolve"]["result"].pop("item")
    html = handoff["html"]
    value["download"]["result"] = (html if encoding == "text" else
                                    base64.b64encode(html.encode("utf-8")).decode("ascii"))
    path.write_text(json.dumps(value), encoding="utf-8")
    before = (tmp_path / st.release_id / "release-state.json").read_bytes()
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    request = json.loads(capsys.readouterr().out)
    assert request["kind"] == "needs-connector-evidence"
    assert not request["permission_to_execute"] and not request["completed"]
    assert request["observation_key"] == "metadata"
    assert (tmp_path / st.release_id / "release-state.json").read_bytes() == before
    read = request["requests"][0]
    assert read["tool"] == "workiq.fetch"
    assert read["arguments"]["entityUrls"][0].startswith("/drives/drive/items/item?")
    value["metadata"] = publication_observation(read["tool"], read["arguments"], metadata)
    path.write_text(json.dumps(value), encoding="utf-8")
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    capsys.readouterr()
    assert C.load_state(str(tmp_path), st.release_id).is_done("build_verify", S.ID)


def test_explicit_metadata_is_bound_to_canonical_provider_ids_and_destination(ready, tmp_path, capsys):
    st, _ = ready
    handoff, _ = issue_publication(ready, tmp_path, capsys, browser=True)
    path, value = publication_receipt(tmp_path, handoff)
    metadata = value["resolve"]["result"].pop("item")
    arguments = {"entityUrls": [
        "/drives/drive/items/item?$select=id,name,size,webUrl,eTag,parentReference,file,folder",
    ]}
    value["metadata"] = publication_observation("workiq.fetch", arguments, metadata)
    before = (tmp_path / st.release_id / "release-state.json").read_bytes()
    for key, bad in (("id", "another-item"), ("webUrl", "https://example.test/another-file")):
        changed = deepcopy(value)
        changed["metadata"]["result"][key] = bad
        path.write_text(json.dumps(changed), encoding="utf-8")
        assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 1
        assert not json.loads(capsys.readouterr().out)["permission_to_execute"]
        assert (tmp_path / st.release_id / "release-state.json").read_bytes() == before


def test_publish_preview_renders_checked_plan_without_state_or_provider_writes(
        ready, tmp_path, monkeypatch, capsys):
    import json
    from hashlib import sha256
    from orchestrator.commands import rc_report_publish as command

    st, _ = ready
    C.save_state(st, str(tmp_path), st.release_id)
    path = tmp_path / st.release_id / "release-state.json"
    before = path.read_bytes()

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


def test_publication_has_no_azure_cli_or_direct_graph_path():
    from orchestrator.commands import rc_report_publish as command
    source = Path(command.__file__).read_text(encoding="utf-8")
    for forbidden in ("_graph_token", "_graph_json", "subprocess", "urlopen", "upload_blob",
                      "graph.microsoft.com", "createLink"):
        assert forbidden not in source


def test_exact_existing_item_permission_is_issued_once(ready, tmp_path, capsys):
    st, _ = ready
    handoff, arguments = issue_publication(ready, tmp_path, capsys)
    assert handoff["permission_to_execute"] is True
    assert handoff["action"]["tool"] == "workiq_upload_file"
    assert handoff["action"]["arguments"] == {
        "localPath": str(tmp_path / handoff["receipt_binding"]["target"]["file_name"]),
        "driveId": "drive", "itemId": "item",
    }
    saved = C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID)
    assert saved.status == "in_flight"
    assert saved.data["publication_handoff"]["execution_id"] == handoff["execution_id"]
    assert cli.main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False
    path, _ = publication_receipt(tmp_path, handoff)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    capsys.readouterr()
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    assert json.loads(capsys.readouterr().out)["already_recorded"] is True
    assert cli.main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False


def test_new_filename_requires_explicit_owner_assisted_recovery(ready, tmp_path, capsys):
    st, _ = ready
    handoff, arguments = issue_publication(ready, tmp_path, capsys, existing=False)
    assert handoff["permission_to_execute"] is False
    assert handoff["kind"] == "owner_assisted_upload" and handoff["action"] is None
    assert "no native new-file uploader is released" in handoff["limitations"].lower()
    assert C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID).status == "blocked"
    assert cli.main(arguments) == 1
    capsys.readouterr()
    path, _ = publication_receipt(tmp_path, handoff)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    capsys.readouterr()
    assert C.load_state(str(tmp_path), st.release_id).is_done("build_verify", S.ID)


@pytest.mark.parametrize("browser", [False, True])
@pytest.mark.parametrize("failure", ["source", "fence"])
def test_uploaded_receipt_persists_if_later_completion_fails(
        ready, tmp_path, monkeypatch, capsys, failure, browser):
    from orchestrator.commands import rc_report_publish as command
    st, _ = ready
    handoff, arguments = issue_publication(ready, tmp_path, capsys, browser=browser)
    path, _ = publication_receipt(tmp_path, handoff)
    save_data = command._save_data

    def save_then_fail(orch, args, data, **kwargs):
        save_data(orch, args, data, **kwargs)
        saved = C.load_state(str(tmp_path), st.release_id)
        assert saved.get_step("build_verify", S.ID).data["item_id"] == "item"
        if failure == "source":
            changed_source(orch.state, "metadata")

    monkeypatch.setattr(command, "_save_data", save_then_fail)
    if failure == "fence":
        record = command._record

        def fail_completion(orch, args, execution_id, status, summary):
            if status == "pass":
                raise ValueError("Later completion fence failed")
            return record(orch, args, execution_id, status, summary)

        monkeypatch.setattr(command, "_record", fail_completion)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 2
    saved = C.load_state(str(tmp_path), st.release_id)
    record = saved.get_step("build_verify", S.ID)
    assert record.status == "blocked"
    assert record.execution["id"]
    assert record.data["item_id"] == "item"
    assert record.data["e_tag"] == "etag"
    assert record.data["content_sha256"] in record.data["file_name"]
    assert not saved.is_done("build_verify", N.ID)
    assert cli.main(arguments) != 0
    capsys.readouterr()


def test_stale_write_review_rejected_before_upload(ready, tmp_path, monkeypatch, capsys):
    from orchestrator.commands import rc_report_publish as command
    from orchestrator import write_review as W
    st, orch = ready
    approved = W.review_hash(orch, "build_verify", S.ID, command.plan_rc_report_publish(orch))
    changed_source(st, "metadata")
    C.save_state(st, str(tmp_path), st.release_id)

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

    monkeypatch.setattr(W, "authorize", authorize_then_change)
    assert cli.main([
        "--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
        "--execute", "--auto-approve", "--executor", "test",
    ]) == 2
    record = C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID)
    assert record.status == "blocked" and record.execution
    assert "item_id" not in record.data
    assert "source/content changed" in capsys.readouterr().out


@pytest.mark.parametrize("browser", [False, True])
@pytest.mark.parametrize("field", ["owner", "destination", "hash", "size", "identity", "execution",
                                  "authorization", "predates", "stale", "tool", "arguments"])
def test_receipt_guards_require_exact_owner_destination_content_and_execution(
        ready, tmp_path, capsys, field, browser):
    st, _ = ready
    handoff, _ = issue_publication(ready, tmp_path, capsys, browser=browser)
    path, value = publication_receipt(tmp_path, handoff)
    if field == "owner":
        value["resolve"]["account"] = "other@example.com"
    elif field == "destination":
        value["item_url"] = value["item_url"].replace("/ReleaseAgentReports/", "/OtherFolder/")
    elif field == "hash":
        value["download"]["result"]["content"] = value["download"]["result"]["content"].replace(
            "<", ">", 1)
    elif field == "size":
        value["resolve"]["result"]["item"]["size"] += 1
    elif field == "identity":
        value["resolve"]["result"]["itemId"] = "wrong"
    elif field == "execution":
        handoff["execution_id"] = "wrong"
    elif field == "authorization":
        value["receipt_binding"]["authorization_id"] = "sha256:" + "0" * 64
    elif field == "predates":
        value["resolve"]["observed_at"] = "2020-01-01T00:00:00Z"
    elif field == "stale":
        value["download"]["observed_at"] = "2020-01-01T00:00:00Z"
    elif field == "tool":
        value["resolve"]["tool"] = "untrusted"
    elif field == "arguments":
        value["download"]["arguments"]["fileId"] = "drive,other"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 1
    record = C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID)
    assert record.status == "in_flight" and record.execution
    assert "item_id" not in record.data
    assert not record.links
    assert '"permission_to_execute": false' in capsys.readouterr().out


@pytest.mark.parametrize("field", ["local", "destination", "hash", "new_file"])
def test_existing_item_permission_requires_proven_identical_content(
        ready, tmp_path, capsys, field):
    from orchestrator.commands import rc_report_publish as command
    st, orch = ready
    C.save_state(st, str(tmp_path), st.release_id)
    operation = command.plan_rc_report_publish(orch).operations[0]
    local = tmp_path / operation.target["file_name"]
    local.write_bytes(operation.content["html"].encode("utf-8"))
    value = {"version": 1, "route": "existing_item", "local_path": str(local),
             **publication_proof(operation.target, operation.content["html"])}
    if field == "local":
        local.write_text("changed", encoding="utf-8")
    elif field == "destination":
        value["item_url"] = value["item_url"].replace("/ReleaseAgentReports/", "/OtherFolder/")
    elif field == "hash":
        value["download"]["result"]["content"] = "changed"
    else:
        value["route"] = "new_file"
    path = tmp_path / "bad-upload.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert cli.main([
        "--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
        "--execute", "--auto-approve", "--upload-request-file", str(path),
    ]) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False
    assert not C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID).execution


def test_readback_must_follow_the_issued_action_not_reuse_initial_proof(
        ready, tmp_path, capsys):
    handoff, _ = issue_publication(ready, tmp_path, capsys)
    path, value = publication_receipt(tmp_path, handoff)
    request = json.loads((tmp_path / "upload-request.json").read_text(encoding="utf-8"))
    value["resolve"] = request["resolve"]
    value["download"] = request["download"]
    path.write_text(json.dumps(value), encoding="utf-8")
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 1
    assert "predates the owned publication handoff" in capsys.readouterr().out


def test_result_after_interruption_reuses_receipt_without_permission(
        ready, tmp_path, monkeypatch, capsys):
    from orchestrator.commands import rc_report_publish as command
    st, _ = ready
    handoff, arguments = issue_publication(ready, tmp_path, capsys)
    path, _ = publication_receipt(tmp_path, handoff)
    record = command._record

    def interrupt(*args, **kwargs):
        raise OSError("interrupted after receipt checkpoint")

    monkeypatch.setattr(command, "_record", interrupt)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 1
    capsys.readouterr()
    saved = C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID)
    assert saved.status == "in_flight" and saved.data["item_id"] == "item"
    monkeypatch.setattr(command, "_record", record)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    capsys.readouterr()
    assert cli.main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False


def test_original_owner_receipt_is_retained_after_source_owner_changes(
        ready, tmp_path, capsys):
    st, _ = ready
    handoff, _ = issue_publication(ready, tmp_path, capsys)
    path, _ = publication_receipt(tmp_path, handoff)
    saved = C.load_state(str(tmp_path), st.release_id)
    saved.owner_email = "new-owner@example.com"
    C.save_state(saved, str(tmp_path), st.release_id)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 2
    record = C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID)
    assert record.data["item_id"] == "item" and record.status == "blocked"
    assert "receipt retained" in capsys.readouterr().out


def test_receipt_action_cannot_authorize_an_upload(ready, tmp_path, capsys):
    handoff, _ = issue_publication(ready, tmp_path, capsys)
    path, _ = publication_receipt(tmp_path, handoff)
    assert cli.main(receipt_arguments(tmp_path, handoff, path) + ["--execute"]) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False


def test_browser_upload_handoff_is_exact_frozen_and_issued_only_once(
        ready, tmp_path, capsys):
    st, _ = ready
    handoff, arguments = issue_publication(ready, tmp_path, capsys, browser=True)
    action = handoff["action"]
    assert handoff["permission_to_execute"] is True
    assert action["route"] == "browser_new_file"
    assert action["tool"] == "playwright-browser_file_upload"
    assert action["arguments"] == {
        "paths": [str(tmp_path / handoff["receipt_binding"]["target"]["file_name"])]}
    assert action["navigate"]["arguments"]["url"] == action["library_context"]["folder_url"]
    assert action["snapshot"] == {"tool": "playwright-browser_snapshot", "arguments": {}}
    assert action["preconditions"]["never_overwrite"] is True
    assert action["preconditions"]["content_sha256"] == handoff["receipt_binding"]["content_sha256"]
    assert any("Cancel any Replace/Overwrite" in item for item in action["instructions"])
    assert any("real page snapshot" in item for item in action["instructions"])
    saved = C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID)
    assert saved.status == "in_flight"
    assert saved.data["publication_handoff"]["action"] == action
    assert cli.main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False
    path, _ = publication_receipt(tmp_path, handoff)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    capsys.readouterr()
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False


@pytest.mark.parametrize("field", [
    "wrongpath", "wrongsite", "wronglibrary", "wrongfolder", "wrongowner",
    "hash", "filename", "unconfirmed", "stale", "missing",
])
def test_browser_upload_rejects_unreviewed_context_path_or_hash(
        ready, tmp_path, capsys, field):
    from orchestrator.commands import rc_report_publish as command
    st, orch = ready
    C.save_state(st, str(tmp_path), st.release_id)
    operation = command.plan_rc_report_publish(orch).operations[0]
    local = tmp_path / operation.target["file_name"]
    local.write_bytes(operation.content["html"].encode("utf-8"))
    value = browser_upload_request(operation.target, local)
    library = value["library_context"]
    if field == "wrongpath":
        library["folder_url"] = library["folder_url"].replace("/ReleaseAgentReports", "/Wrong")
    elif field == "wrongsite":
        library["site_url"] = "https://other.sharepoint.com/sites/wrong"
    elif field == "wronglibrary":
        library["library"] = "Other"
    elif field == "wrongfolder":
        library["folder"] = "Other"
    elif field == "wrongowner":
        library["confirmed_by"] = "other@example.com"
    elif field == "hash":
        local.write_text("modified", encoding="utf-8")
    elif field == "filename":
        value["local_path"] = str(tmp_path / "different.html")
    elif field == "unconfirmed":
        library["owner_confirmed"] = False
    elif field == "stale":
        library["confirmed_at"] = "2020-01-01T00:00:00Z"
    elif field == "missing":
        value.pop("library_context")
    path = tmp_path / "bad-browser.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert cli.main([
        "--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
        "--execute", "--auto-approve", "--upload-request-file", str(path),
    ]) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False
    assert not C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID).execution


def test_browser_route_and_local_path_are_part_of_the_review_hash(ready, tmp_path):
    from orchestrator.commands import rc_report_publish as command
    from orchestrator import write_review as W
    st, orch = ready
    operation = command.plan_rc_report_publish(orch).operations[0]
    local = tmp_path / operation.target["file_name"]
    local.write_bytes(operation.content["html"].encode("utf-8"))
    request = browser_upload_request(operation.target, local)
    action = command._upload_action(request, operation, st.owner_email)
    reviewed = command.plan_rc_report_publish(orch, action)
    changed = deepcopy(action)
    changed["arguments"]["paths"] = [str(tmp_path / "different.html")]
    assert W.review_hash(orch, "build_verify", S.ID, reviewed) != W.review_hash(
        orch, "build_verify", S.ID, command.plan_rc_report_publish(orch, changed))
    assert W.review_hash(orch, "build_verify", S.ID, reviewed) != W.review_hash(
        orch, "build_verify", S.ID, command.plan_rc_report_publish(orch))


def test_browser_unknown_outcome_holds_and_recovers_only_through_receipt(
        ready, tmp_path, capsys):
    st, _ = ready
    handoff, arguments = issue_publication(ready, tmp_path, capsys, browser=True)
    path = tmp_path / "unknown-browser-result.json"
    path.write_text(json.dumps({"receipt_binding": handoff["receipt_binding"],
                                "outcome": "unknown"}), encoding="utf-8")
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 1
    capsys.readouterr()
    assert C.load_state(str(tmp_path), st.release_id).get_step("build_verify", S.ID).status == "in_flight"
    assert cli.main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False
    path, _ = publication_receipt(tmp_path, handoff)
    assert cli.main(receipt_arguments(tmp_path, handoff, path)) == 0
    capsys.readouterr()


def test_browser_accepts_fresh_folder_observation_and_fences_receipt_drive(
        ready, tmp_path, capsys):
    from orchestrator.commands import rc_report_publish as command
    st, orch = ready
    C.save_state(st, str(tmp_path), st.release_id)
    operation = command.plan_rc_report_publish(orch).operations[0]
    local = tmp_path / operation.target["file_name"]
    local.write_bytes(operation.content["html"].encode("utf-8"))
    request = browser_upload_request(operation.target, local)
    library = request["library_context"]
    for key in ("owner_confirmed", "confirmed_by", "confirmed_at"):
        library.pop(key)
    library["resolve"] = publication_observation(
        "workiq_resolve_m365_link", {"url": library["folder_url"]}, {
            "driveId": "drive", "itemId": "folder",
            "item": {"id": "folder", "name": operation.target["folder"],
                     "webUrl": library["folder_url"], "folder": {},
                     "parentReference": {"driveId": "drive"}},
        })
    request_path = tmp_path / "browser-folder-observation.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    arguments = ["--runs-root", str(tmp_path), S.WRITE_COMMAND, "--release", st.release_id,
                 "--execute", "--auto-approve", "--upload-request-file", str(request_path)]
    assert cli.main(arguments) == 0
    handoff = json.loads(capsys.readouterr().out)
    assert handoff["action"]["folder_identity"] == {"drive_id": "drive", "item_id": "folder"}
    assert handoff["receipt_reads"][0]["arguments"]["url"] == handoff["action"]["item_url"]
    receipt_path, value = publication_receipt(tmp_path, handoff)
    value["resolve"]["result"]["driveId"] = "different-drive"
    value["resolve"]["result"]["item"]["parentReference"]["driveId"] = "different-drive"
    value["download"]["arguments"]["fileId"] = "different-drive,item"
    receipt_path.write_text(json.dumps(value), encoding="utf-8")
    assert cli.main(receipt_arguments(tmp_path, handoff, receipt_path)) == 1
    assert "differs from the frozen folder" in capsys.readouterr().out
    receipt_path, _ = publication_receipt(tmp_path, handoff)
    assert cli.main(receipt_arguments(tmp_path, handoff, receipt_path)) == 0
    capsys.readouterr()
