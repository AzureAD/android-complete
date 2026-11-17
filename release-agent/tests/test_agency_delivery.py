"""Focused tests for direct Python notification delivery via Agency MCP (`deliver-step`, `next --deliver`)."""
from __future__ import annotations

import json

import yaml

from orchestrator import agency_mcp, cli, cli_common as C, mocks as mocks_mod
from orchestrator.commands.deliver_cmd import deliver_one_step, drain_auto_notifications
from orchestrator.state import ReleaseState
from tests._harness import CONFIG, _active_phase


def test_resolve_agency_exe_finds_explicit_env_and_requirements_lookup(tmp_path, monkeypatch):
    fake_exe = tmp_path / "agency.exe"
    fake_exe.write_bytes(b"MZ")
    assert agency_mcp.resolve_agency_exe(str(fake_exe)) == str(fake_exe)
    monkeypatch.setenv("RELEASE_AGENT_AGENCY_EXE", str(fake_exe))
    assert agency_mcp.resolve_agency_exe() == str(fake_exe)
    # Also exercise the no-explicit-arg infra.resolve_agency_path code path directly
    monkeypatch.delenv("RELEASE_AGENT_AGENCY_EXE", raising=False)
    monkeypatch.delenv("RELEASE_AGENT_AGENCY_PATH", raising=False)
    monkeypatch.setattr("orchestrator.infra.resolve_agency_path", lambda *a, **kw: str(fake_exe))
    assert agency_mcp.resolve_agency_exe() == str(fake_exe)


def test_call_mcp_tool_parses_json_with_agency_correlation_trailer(monkeypatch):
    import io

    responses = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n",
        json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{
                    "type": "text",
                    "text": '{"id":"evt-42","onlineMeeting":{"joinUrl":"https://teams.microsoft.com/l/meetup-join/x"}}\nCorrelationId: 123, TimeStamp: 2026-11-17',
                }],
            },
        }) + "\n",
    ]

    class FakeProc:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("".join(responses))
            self.stderr = io.StringIO("")

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(agency_mcp, "resolve_agency_exe", lambda _e=None: "agency.exe")
    monkeypatch.setattr(agency_mcp.subprocess, "Popen", lambda *a, **kw: FakeProc())
    out = agency_mcp.call_mcp_tool("calendar", "CreateEvent", {"subject": "Test"})
    assert out["id"] == "evt-42"
    assert "CorrelationId" in out["_trailer"]


def test_agency_mcp_maps_email_self_chat_group_chat_mentions_and_calendar():
    calls = []

    def fake_mcp(server: str, tool: str, args: dict) -> dict:
        calls.append((server, tool, args))
        if server == "calendar":
            return {"id": "evt-123", "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/1"}}
        return {"id": f"{server}-msg-1"}

    res_mail = agency_mcp.send_notification(
        "workiq_send_email",
        {"to": ["pedroro@microsoft.com"], "subject": "[TEST -> me] Notice", "body": "<p>Hi</p>", "isHtml": True},
        owner_email="pedroro@microsoft.com",
        mcp_caller=fake_mcp,
    )
    assert res_mail["mcp_tool"] == "SendEmailWithAttachments"
    assert calls[-1] == (
        "mail",
        "SendEmailWithAttachments",
        {"to": ["pedroro@microsoft.com"], "subject": "[TEST -> me] Notice", "body": "<p>Hi</p>", "contentType": "HTML"},
    )

    res_self = agency_mcp.send_notification(
        "workiq_send_chat_message",
        {"chatId": "48:notes", "content": "<p>Self reminder</p>", "contentType": "html"},
        owner_email="pedroro@microsoft.com",
        mcp_caller=fake_mcp,
    )
    assert res_self["mcp_tool"] == "SendMessageToSelf"
    assert calls[-1] == ("teams", "SendMessageToSelf", {"content": "<p>Self reminder</p>", "contentType": "html"})

    res_chat = agency_mcp.send_notification(
        "workiq_send_chat_message",
        {
            "chatId": "19:abc@thread.v2",
            "content": '<b><at id="0">Alice Smith</at></b> pending',
            "contentType": "html",
            "mentions": [
                {
                    "id": 0,
                    "mentionText": "Alice Smith",
                    "mentioned": {"user": {"id": "11111111-2222-3333-4444-555555555555", "displayName": "Alice Smith"}},
                }
            ],
        },
        owner_email="pedroro@microsoft.com",
        mcp_caller=fake_mcp,
    )
    assert res_chat["mcp_tool"] == "SendMessageToChat"
    assert calls[-1][0:2] == ("teams", "SendMessageToChat")
    assert calls[-1][2]["content"] == "<b>@Alice Smith</b> pending"
    assert json.loads(calls[-1][2]["mentions"]) == [
        {"displayName": "Alice Smith", "id": "11111111-2222-3333-4444-555555555555", "type": "user"}
    ]

    res_cal = agency_mcp.send_notification(
        "workiq_create_event",
        {
            "subject": "November 2026 Release Bug Bash",
            "attendees": ["pedroro@microsoft.com"],
            "start": "2026-11-23T09:00:00",
            "end": "2026-11-23T11:00:00",
            "timeZone": "America/Los_Angeles",
            "body": "<p>Invite</p>",
            "isHtml": True,
        },
        owner_email="pedroro@microsoft.com",
        mcp_caller=fake_mcp,
    )
    assert res_cal["mcp_tool"] == "CreateEvent"
    assert res_cal["response"]["id"] == "evt-123"


def test_deliver_one_step_and_drain_auto_notifications_complete_preflight_silently(tmp_path, monkeypatch):
    monkeypatch.setattr(
        mocks_mod,
        "load_mocks",
        lambda: {
            "preflight.notice": {"send_to": "pedroro@microsoft.com"},
            "preflight.flight_reminder": {"send_to": "me"},
        },
    )
    st = ReleaseState(
        release_id="2026-11",
        ccd="2026-11-20",
        ccd_source="confirmed",
        owner_email="pedroro@microsoft.com",
        timezone="America/Los_Angeles",
    )
    _active_phase(st, "preflight")
    C.save_state(st, str(tmp_path), "2026-11")

    sent = []

    def fake_mcp(server: str, tool: str, args: dict) -> dict:
        sent.append((server, tool, args))
        return {"id": f"{server}-ok-{len(sent)}"}

    results = drain_auto_notifications(str(tmp_path), "2026-11", CONFIG, mcp_caller=fake_mcp)
    assert [r["step"] for r in results] == ["notice", "flight_reminder"]
    assert all(r["status"] == "done" for r in results)
    assert [s[0:2] for s in sent] == [
        ("mail", "SendEmailWithAttachments"),
        ("teams", "SendMessageToSelf"),
    ]
    assert sent[0][2]["to"] == ["pedroro@microsoft.com"]
    assert sent[0][2]["subject"].startswith("[TEST \u2192 me]")

    reloaded = C.load_state(str(tmp_path), "2026-11")
    assert reloaded.is_done("preflight", "notice")
    assert reloaded.is_done("preflight", "flight_reminder")
    assert not reloaded.is_done("preflight", "lockdown")


def test_deliver_one_step_records_nonblocking_failure_and_enforces_requires_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(mocks_mod, "load_mocks", lambda: {"preflight.notice": {"send_to": "pedroro@microsoft.com"}})
    with open(CONFIG, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for ph in cfg["phases"]:
        if ph["id"] == "preflight":
            for step in ph["steps"]:
                if step["id"] == "flight_reminder":
                    step["requires_approval"] = True
    cfg_path = tmp_path / "phases.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    from tests._context import fresh_orchestrator

    st = ReleaseState(
        release_id="2026-11",
        ccd="2026-11-20",
        ccd_source="confirmed",
        owner_email="pedroro@microsoft.com",
        timezone="America/Los_Angeles",
    )
    _active_phase(st, "preflight")
    st.workflow_revision = None
    fresh_orchestrator(str(cfg_path), st)
    C.save_state(st, str(tmp_path), "2026-11")

    blocked_gate = deliver_one_step(
        str(tmp_path), "2026-11", str(cfg_path), "preflight", "flight_reminder",
        approved=False, mcp_caller=lambda *_a: {"id": "unused"},
    )
    assert blocked_gate["status"] == "needs_approval"
    assert C.load_state(str(tmp_path), "2026-11").get_step("preflight", "flight_reminder").status == "pending"

    def failing_mcp(_s: str, _t: str, _a: dict) -> dict:
        raise RuntimeError("403 Mailbox unavailable")

    failed = deliver_one_step(
        str(tmp_path), "2026-11", str(cfg_path), "preflight", "notice",
        approved=False, mcp_caller=failing_mcp,
    )
    assert failed["status"] == "failed"
    step_rec = C.load_state(str(tmp_path), "2026-11").get_step("preflight", "notice")
    assert step_rec.status == "blocked"
    assert "403 Mailbox unavailable" in step_rec.note


def test_cli_next_deliver_flag_drains_auto_and_notifications(tmp_path, monkeypatch, capsys):
    import orchestrator.commands.deliver_cmd as deliver_mod

    monkeypatch.setattr(
        mocks_mod,
        "load_mocks",
        lambda: {
            "preflight.breaking": {"outcome": "pass", "note": "ok"},
            "preflight.cg": {"outcome": "pass", "note": "ok"},
            "preflight.oneauth_access": {"outcome": "pass", "note": "ok"},
            "preflight.cron": {"outcome": "pass", "note": "ok"},
            "preflight.notice": {"send_to": "pedroro@microsoft.com"},
            "preflight.flight_reminder": {"send_to": "me"},
        },
    )
    sent = []
    monkeypatch.setattr(
        deliver_mod,
        "call_mcp_tool",
        lambda s, t, a, **_kw: (sent.append((s, t)) or {"id": "ok"}),
    )
    # Rebind default arg reference through drain_auto_notifications wrapper
    orig_drain = deliver_mod.drain_auto_notifications
    monkeypatch.setattr(
        deliver_mod,
        "drain_auto_notifications",
        lambda rr, rel, cfg, **_kw: orig_drain(
            rr, rel, cfg, mcp_caller=lambda s, t, a: (sent.append((s, t)) or {"id": "ok"})
        ),
    )

    st = ReleaseState(
        release_id="2026-11",
        ccd="2026-11-20",
        ccd_source="confirmed",
        owner_email="pedroro@microsoft.com",
        timezone="America/Los_Angeles",
    )
    _active_phase(st, "preflight")
    C.save_state(st, str(tmp_path), "2026-11")

    rc = cli.main(["--runs-root", str(tmp_path), "next", "--release", "2026-11", "--deliver", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["delivered_notifications"]) == 2
    assert payload["scout_pending"] == ["lockdown"]
    assert sent == [("mail", "SendEmailWithAttachments"), ("teams", "SendMessageToSelf")]
