"""RC report split flow: publish HTML, email lightweight link, then human gate."""
from argparse import Namespace
from copy import deepcopy
from datetime import date

import pytest

from orchestrator import cli_common as C, delivery as D, mocks, schedule
from orchestrator import cli
from orchestrator.commands.delivery_cmd import finish
from orchestrator.commands.step_action import prepare_step
from orchestrator.state import ReleaseState, StepState
from tests._context import fresh_orchestrator as Orchestrator
from tests._harness import CONFIG, _ready_for_rc_report, _seed_rc_pipeline


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
    st.set_step("build_verify", "rc_report_publish", StepState(
        status="done",
        note=f"Published RC verification report: {link}",
        data={"report_link": link, "web_url": link, "item_id": "item-1"},
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
