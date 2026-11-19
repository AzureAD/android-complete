"""Direct Python stdio JSON-RPC client for Agency CLI MCP servers (mail, teams, calendar).

Executes `requires_approval: false` outbound notifications directly inside Python via
`agency.exe mcp <server>` with `shell=False`, honoring `mocks.local.yaml` redirects
without routing through interactive LLM `workiq_send_*` tools.
"""
from __future__ import annotations

from html import escape
import atexit
import json
import os
import queue
import re
import subprocess
import threading
import time
from typing import Any, Callable


MCPCaller = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class AgencyTransportUnavailableError(RuntimeError):
    """Raised when `agency.exe` is absent or its MCP stdio server fails to start/respond."""


class AgencyAmbiguousDeliveryError(RuntimeError):
    """Raised when `tools/call` was dispatched over stdio and timed out or exited before replying."""


def resolve_agency_exe(explicit: str | None = None) -> str:
    """Locate a working `agency.exe` binary on this machine."""
    import shutil
    from orchestrator import infra

    env_override = explicit or os.environ.get("RELEASE_AGENT_AGENCY_EXE") or os.environ.get("RELEASE_AGENT_AGENCY_PATH")
    req_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "requirements.yaml")
    req = infra.load_requirements(req_path) if os.path.isfile(req_path) else {}
    cfg_path = infra.scout_mcp_config_path()
    existing = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                existing = (json.load(fh) or {}).get("servers") or {}
        except (OSError, ValueError):
            existing = {}
    exe = infra.resolve_agency_path(req, existing, preferred=env_override)
    if not exe:
        for candidate in (
            shutil.which("agency"),
            shutil.which("agency.exe"),
            os.path.expandvars(r"%APPDATA%\agency\CurrentVersion\agency.exe"),
        ):
            if candidate and os.path.isfile(candidate):
                exe = os.path.abspath(candidate)
                break
    if not exe or not os.path.isfile(exe):
        raise AgencyTransportUnavailableError(
            "Agency CLI executable not found. Install Agency (https://aka.ms/agency) "
            "or set RELEASE_AGENT_AGENCY_EXE."
        )
    return exe


def call_mcp_tool(
    server: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    timeout: float = 60.0,
    agency_exe: str | None = None,
) -> dict[str, Any]:
    """Invoke one MCP tool on `agency.exe mcp <server>` over stdio JSON-RPC."""
    exe = resolve_agency_exe(agency_exe)
    proc = subprocess.Popen(
        [exe, "mcp", server],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                lines.put(raw)
        finally:
            lines.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    deadline = time.monotonic() + timeout

    def _send(payload: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def _recv(expected_id: int) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Timed out waiting for Agency MCP '{server}.{tool_name}' response")
            try:
                raw = lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise RuntimeError(f"Timed out waiting for Agency MCP '{server}.{tool_name}' response") from exc
            if raw is None:
                stderr = proc.stderr.read().strip() if proc.stderr else ""
                raise RuntimeError(
                    f"Agency MCP '{server}' exited unexpectedly before replying"
                    + (f": {stderr[:300]}" if stderr else "")
                )
            text = raw.strip()
            if not text.startswith("{"):
                continue
            msg = json.loads(text)
            if msg.get("id") == expected_id:
                return msg

    try:
        _send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "android-release-orchestrator", "version": "2.0"},
            },
        })
        init_resp = _recv(1)
        if "error" in init_resp:
            raise RuntimeError(f"Agency MCP '{server}' initialize failed: {init_resp['error']}")
        _send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        try:
            call_resp = _recv(2)
        except RuntimeError as exc:
            raise AgencyAmbiguousDeliveryError(str(exc)) from exc
        return _unwrap_mcp_result(server, tool_name, call_resp)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


def _unwrap_mcp_result(server: str, tool_name: str, call_resp: dict[str, Any]) -> dict[str, Any]:
    if "error" in call_resp:
        raise RuntimeError(f"Agency MCP '{server}.{tool_name}' error: {call_resp['error']}")
    result = call_resp.get("result") or {}
    content_items = result.get("content") if isinstance(result, dict) else None
    texts = [
        item.get("text", "")
        for item in (content_items or [])
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]
    combined = "\n".join(t for t in texts if t).strip()
    if isinstance(result, dict) and result.get("isError"):
        raise RuntimeError(f"Agency MCP '{server}.{tool_name}' failed: {combined or result}")
    if combined:
        stripped = combined.lstrip()
        parsed = None
        if stripped.startswith(("{", "[")):
            try:
                val, end = json.JSONDecoder().raw_decode(stripped)
                trailer = stripped[end:].strip()
                if isinstance(val, dict):
                    parsed = dict(val)
                    if trailer:
                        parsed.setdefault("_trailer", trailer)
                else:
                    parsed = {"result": val, **({"_trailer": trailer} if trailer else {})}
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict):
            if parsed.get("isError") or parsed.get("success") is False or (
                isinstance(parsed.get("error"), str) and parsed["error"].strip()
            ):
                raise RuntimeError(
                    f"Agency MCP '{server}.{tool_name}' rejected request: "
                    f"{parsed.get('error') or parsed.get('message') or combined}"
                )
            return parsed
        return {"message": combined}
    return result if isinstance(result, dict) else {"result": result}


class AgencyMcpSession:
    """Persistent stdio JSON-RPC client for multi-call read workflows (e.g. `agency mcp workiq`)."""

    def __init__(self, server: str, *, agency_exe: str | None = None, startup_timeout: float = 45.0):
        self.server = server
        self.exe = resolve_agency_exe(agency_exe)
        self._lock = threading.Lock()
        self._next_id = 1
        self.verified_owner: str | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._proc = subprocess.Popen(
            [self.exe, "mcp", server],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            init_id = self._alloc_id()
            self._send({
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "android-release-orchestrator", "version": "2.0"},
                },
            })
            init_resp = self._recv(init_id, "initialize", time.monotonic() + startup_timeout)
            if "error" in init_resp:
                raise AgencyTransportUnavailableError(
                    f"Agency MCP '{server}' initialize failed: {init_resp['error']}"
                )
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception as exc:
            self.close()
            if isinstance(exc, AgencyTransportUnavailableError):
                raise
            raise AgencyTransportUnavailableError(str(exc)) from exc

    def _alloc_id(self) -> int:
        rid = self._next_id
        self._next_id += 1
        return rid

    def _reader(self) -> None:
        assert self._proc.stdout is not None
        try:
            for raw in self._proc.stdout:
                self._lines.put(raw)
        finally:
            self._lines.put(None)

    def _send(self, payload: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _recv(self, expected_id: int, tool_name: str, deadline: float) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Timed out waiting for Agency MCP '{self.server}.{tool_name}' response")
            try:
                raw = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise RuntimeError(f"Timed out waiting for Agency MCP '{self.server}.{tool_name}' response") from exc
            if raw is None:
                stderr = self._proc.stderr.read().strip() if self._proc.stderr else ""
                raise RuntimeError(
                    f"Agency MCP '{self.server}' exited unexpectedly before replying"
                    + (f": {stderr[:300]}" if stderr else "")
                )
            text = raw.strip()
            if not text.startswith("{"):
                continue
            msg = json.loads(text)
            if msg.get("id") == expected_id:
                return msg

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def call(self, tool_name: str, arguments: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
        with self._lock:
            req_id = self._alloc_id()
            self._send({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            })
            resp = self._recv(req_id, tool_name, time.monotonic() + timeout)
            return _unwrap_mcp_result(self.server, tool_name, resp)

    def close(self) -> None:
        if self._proc.poll() is not None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


_SHARED_SESSIONS_LOCK = threading.Lock()
_SHARED_SESSIONS: dict[tuple[str, str], AgencyMcpSession] = {}


def _close_all_shared_sessions() -> None:
    with _SHARED_SESSIONS_LOCK:
        sessions = list(_SHARED_SESSIONS.values())
        _SHARED_SESSIONS.clear()
    for sess in sessions:
        sess.close()


atexit.register(_close_all_shared_sessions)


def get_shared_session(server: str, *, agency_exe: str | None = None) -> AgencyMcpSession:
    exe = resolve_agency_exe(agency_exe)
    key = (exe, server)
    with _SHARED_SESSIONS_LOCK:
        sess = _SHARED_SESSIONS.get(key)
        if sess is not None and sess.alive:
            return sess
        if sess is not None:
            sess.close()
        sess = AgencyMcpSession(server, agency_exe=exe)
        _SHARED_SESSIONS[key] = sess
        return sess


def _extract_workiq_fetch_data(raw: dict[str, Any], path: str) -> Any:
    structured = raw.get("structuredContent") if isinstance(raw, dict) else None
    results = (structured.get("results") if isinstance(structured, dict) else None) or (
        raw.get("results") if isinstance(raw, dict) else None
    )
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise ValueError(f"Agency WorkIQ MCP fetch returned malformed response for '{path}'")
    entry = results[0]
    status = entry.get("statusCode")
    err = entry.get("error")
    if err or (isinstance(status, int) and status >= 400):
        raise ValueError(
            f"WorkIQ fetch failed (HTTP {status or 'error'}): {err or 'request rejected'}"
        )
    data = entry.get("data")
    if not isinstance(data, (dict, list)):
        raise ValueError(f"WorkIQ fetch returned empty or non-JSON entity payload for '{path}'")
    return data


def fetch_workiq_entity(
    path: str,
    owner: str,
    *,
    timeout: float = 60.0,
    agency_exe: str | None = None,
) -> Any:
    """Read a single Graph-relative entity URL via `agency.exe mcp workiq` (`fetch`),
    verifying once per session that the authenticated M365 user matches `owner`."""
    from orchestrator import m365

    sess = get_shared_session("workiq", agency_exe=agency_exe)
    if (sess.verified_owner or "").casefold() != owner.casefold():
        me_raw = sess.call(
            "fetch",
            {"entityUrls": ["/me?$select=id,displayName,mail,userPrincipalName"]},
            timeout=timeout,
        )
        me_data = _extract_workiq_fetch_data(me_raw, "/me")
        if not m365.profile_matches(me_data, owner):
            raise ValueError("Connector observation account differs from the release owner")
        sess.verified_owner = owner
        if path == "/me?$select=id,displayName,mail,userPrincipalName":
            return me_data

    raw = sess.call("fetch", {"entityUrls": [path]}, timeout=timeout)
    return _extract_workiq_fetch_data(raw, path)


def _convert_teams_mentions(content: str, mentions: Any) -> tuple[str, str | None]:
    """Convert Graph `<at id=\"N\">Name</at>` + mention objects into Agency Teams MCP format."""
    if not mentions:
        return content, None
    if isinstance(mentions, str):
        return content, mentions
    if not isinstance(mentions, list):
        return content, None
    agency_mentions = []
    rewritten = content
    for entry in mentions:
        if not isinstance(entry, dict):
            continue
        if "type" in entry and "displayName" in entry and "id" in entry:
            agency_mentions.append(entry)
            continue
        mid = entry.get("id")
        user = ((entry.get("mentioned") or {}).get("user") or {})
        uid = user.get("id")
        name = entry.get("mentionText") or user.get("displayName")
        if mid is None or not uid or not name:
            continue
        pattern = re.compile(rf"<at\s+id=[\"']?{re.escape(str(mid))}[\"']?\s*>.*?</at>", re.I | re.S)
        rewritten = pattern.sub(f"@{name}", rewritten)
        rewritten = rewritten.replace(f"@{escape(name)}", f"@{name}")
        agency_mentions.append({"displayName": name, "id": uid, "type": "user"})
    if not agency_mentions:
        return content, None
    return rewritten, json.dumps(agency_mentions, ensure_ascii=False)


def _is_self_target(target: str | None, owner_email: str | None) -> bool:
    if not target:
        return False
    norm = target.strip().casefold()
    if norm in ("me", "48:notes", "self"):
        return True
    return bool(owner_email and norm == owner_email.strip().casefold())


def send_notification(
    tool: str,
    payload: dict[str, Any],
    *,
    owner_email: str | None = None,
    mcp_caller: MCPCaller = call_mcp_tool,
) -> dict[str, Any]:
    """Dispatch a canonical notification descriptor payload through Agency MCP."""
    if tool == "workiq_send_email":
        args: dict[str, Any] = {
            "to": list(payload["to"]),
            "subject": payload["subject"],
            "body": payload["body"],
            "contentType": "Text" if payload.get("isHtml") is False else "HTML",
        }
        if payload.get("cc"):
            args["cc"] = list(payload["cc"])
        if payload.get("bcc"):
            args["bcc"] = list(payload["bcc"])
        resp = mcp_caller("mail", "SendEmailWithAttachments", args)
        return {"server": "mail", "mcp_tool": "SendEmailWithAttachments", "response": resp}

    if tool == "workiq_send_chat_message":
        chat_id = str(payload.get("chatId") or "").strip()
        if not chat_id:
            raise ValueError("workiq_send_chat_message requires chatId")
        content, mentions_json = _convert_teams_mentions(
            str(payload.get("content") or ""), payload.get("mentions")
        )
        ctype = str(payload.get("contentType") or "html").lower()
        if _is_self_target(chat_id, owner_email):
            args = {"content": content, "contentType": ctype}
            if mentions_json:
                args["mentions"] = mentions_json
            resp = mcp_caller("teams", "SendMessageToSelf", args)
            return {"server": "teams", "mcp_tool": "SendMessageToSelf", "response": resp}
        args = {"chatId": chat_id, "content": content, "contentType": ctype}
        if mentions_json:
            args["mentions"] = mentions_json
        resp = mcp_caller("teams", "SendMessageToChat", args)
        return {"server": "teams", "mcp_tool": "SendMessageToChat", "response": resp}

    if tool in ("m_send_teams_message", "microsoft_teams-SendMessageToUser"):
        recipient = str(payload.get("userIdOrUpn") or payload.get("owner") or owner_email or "").strip()
        content = str(payload.get("content") or payload.get("message") or "")
        ctype = str(payload.get("contentType") or "html").lower()
        if _is_self_target(recipient, owner_email):
            resp = mcp_caller("teams", "SendMessageToSelf", {"content": content, "contentType": ctype})
            return {"server": "teams", "mcp_tool": "SendMessageToSelf", "response": resp}
        resp = mcp_caller(
            "teams",
            "SendMessageToUser",
            {"userIdOrUpn": recipient, "content": content, "contentType": ctype},
        )
        return {"server": "teams", "mcp_tool": "SendMessageToUser", "response": resp}

    if tool == "microsoft_teams-SendMessageToChannel":
        content, mentions_json = _convert_teams_mentions(
            str(payload.get("content") or ""), payload.get("mentions")
        )
        args = {
            "teamId": payload["teamId"],
            "channelId": payload["channelId"],
            "content": content,
            "contentType": str(payload.get("contentType") or "html").lower(),
        }
        if payload.get("subject"):
            args["subject"] = payload["subject"]
        if mentions_json:
            args["mentions"] = mentions_json
        resp = mcp_caller("teams", "SendMessageToChannel", args)
        return {"server": "teams", "mcp_tool": "SendMessageToChannel", "response": resp}

    if tool == "workiq_create_event":
        attendees = list(payload.get("attendees") or [])
        for extra_key in ("optionalAttendees", "resourceAttendees"):
            for addr in payload.get(extra_key) or []:
                if addr not in attendees:
                    attendees.append(addr)
        args = {
            "subject": payload["subject"],
            "attendeeEmails": attendees,
            "startDateTime": payload["start"],
            "endDateTime": payload["end"],
            "bodyContent": payload.get("body") or "",
            "bodyContentType": "Text" if payload.get("isHtml") is False else "HTML",
            "isOnlineMeeting": payload.get("isOnlineMeeting", True) is not False,
        }
        if payload.get("timeZone"):
            args["timeZone"] = payload["timeZone"]
        if payload.get("location"):
            args["location"] = payload["location"]
        resp = mcp_caller("calendar", "CreateEvent", args)
        return {"server": "calendar", "mcp_tool": "CreateEvent", "response": resp}

    raise ValueError(f"Unsupported notification transport for direct Agency MCP delivery: {tool}")
