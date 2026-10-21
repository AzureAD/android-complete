"""Infrastructure preflight — verify (and auto-provision) everything the skill
needs on a machine BEFORE the tool-level requirements: CLIs, the launchers that
back MCP servers, and the MCP servers themselves registered into Scout's config.

Design:
  * CLI/host/python deps ("requirements") → shell-checked (same as before).
  * MCP servers ("mcp_servers"), both command and HTTP, live in Scout's config
    (~/.scout/m-mcp-servers.json), which loads at startup. We can't "install" them,
    but we CAN register a missing one into that file (backing it up first) so it
    loads on the next Scout restart. Each entry names its `provider` (a shell check
    that the launcher exists) so we never register a server whose launcher is absent.
  * M365 connectors ("m365_connectors") -> require a fresh inventory from inside
    Scout. Configured servers do not prove native WorkIQ/Agency tools are loaded.
    Setup reports a hold until the required tools are observed.

This module is pure-Python and import-light so bootstrap.ps1 can call it via the
CLI (`python -m orchestrator.cli infra`). It only touches Scout's MCP config when
asked to register, and always backs it up.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from datetime import datetime

import yaml


def scout_mcp_config_path() -> str:
    return os.path.join(scout_profile_root(), "m-mcp-servers.json")


def scout_profile_root(path=None):
    candidate = (path if path is not None else os.environ.get("RELEASE_AGENT_SCOUT_ROOT"))
    return os.path.abspath(expand(candidate or os.path.join(os.path.expanduser("~"), ".scout")))


def expand(s: str) -> str:
    """Expand %VARS% / $VARS and ~ in a path string."""
    return os.path.expanduser(os.path.expandvars(s or ""))


def load_requirements(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _shell_ok(cmd, timeout: int = 30) -> bool:
    """True if the shell command exits 0. Used for CLI + provider checks."""
    if not cmd:
        return False
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def resolve_agency_path(req, existing=None, preferred=None):
    selected = preferred if preferred is not None else os.environ.get("RELEASE_AGENT_AGENCY_PATH")
    if selected:
        path = os.path.abspath(expand(selected))
        return path if os.path.isfile(path) else None
    candidates = [shutil.which("agency"), shutil.which("agency.exe")]
    for entry in (existing or {}).values():
        config = entry.get("config", {}) if isinstance(entry, dict) else {}
        command = config.get("command") if isinstance(config, dict) else None
        if isinstance(command, str) and os.path.basename(command).casefold() in ("agency", "agency.exe"):
            candidates.append(command)
    candidates.extend(m.get("command") for m in req.get("mcp_servers", [])
                      if m.get("launcher") == "agency")
    for candidate in candidates:
        if candidate:
            path = os.path.abspath(expand(candidate))
            if os.path.isfile(path):
                return path
    return None


def check_requirements(req: dict, *, scout_root=None, agency_path=None) -> list:
    """Return [{id,name,ok,install}] for each shell-checkable requirement."""
    out = []
    for r in req.get("requirements", []):
        if r.get("id") == "scout":
            ok = os.path.isdir(scout_profile_root(scout_root))
        elif r.get("id") == "agency-cli":
            ok = bool(agency_path) and _shell_ok([agency_path, "--version"])
        else:
            ok = _shell_ok(r.get("check", ""))
        out.append({
            "id": r.get("id", ""), "name": r.get("name", r.get("id", "?")),
            "ok": ok, "install": r.get("install", ""),
        })
    return out


def _load_scout_config(path: str) -> dict:
    if not os.path.exists(path):
        return {"servers": {}}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("servers", {})
    return data


def _backup(path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dst = f"{path}.bak-{stamp}"
    shutil.copy2(path, dst)
    return dst


def ensure_mcp_servers(req: dict, register: bool, *, scout_root=None, agency_path=None) -> list:
    """Check required MCPs; register absent command/HTTP entries with one backup.
    HTTP discovery/auth remains host-owned; matching existing entries are preserved.

    Returns [{id,name,scout_key,status,detail}] where status is one of:
      present | registered | would_register | provider_missing | launcher_missing
    """
    servers = req.get("mcp_servers", [])
    if not servers:
        return []
    cfg_path = (scout_mcp_config_path() if scout_root is None else
                os.path.join(scout_profile_root(scout_root), "m-mcp-servers.json"))
    before = None
    if os.path.exists(cfg_path):
        with open(cfg_path, "rb") as stream:
            before = stream.read()
    cfg = _load_scout_config(cfg_path)
    existing = cfg.get("servers", {})
    agency = resolve_agency_path(req, existing, agency_path)
    results, dirty, backed_up = [], False, None

    for m in servers:
        key = m.get("scout_key") or m.get("id")
        name = m.get("name", key)
        transport = m.get("type", "command")
        rec = {"id": m.get("id"), "name": name, "scout_key": key, "transport": transport}
        if transport == "http":
            url = m.get("url")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if (parsed is None or parsed.scheme != "https" or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError(f"HTTP MCP {key} requires a credential-free HTTPS endpoint")
            matches = []
            for existing_key, entry in existing.items():
                config = entry.get("config") if isinstance(entry, dict) else None
                if (existing_key == key or isinstance(config, dict) and (
                        config.get("name") == m.get("server_name", key)
                        or isinstance(config.get("url"), str)
                        and config["url"].rstrip("/") == url.rstrip("/"))):
                    matches.append((existing_key, entry))
            if len(matches) > 1:
                results.append({**rec, "status": "configuration_conflict",
                                "detail": "Multiple registrations match this HTTP connection; preserve them "
                                          "and review the duplicates before setup continues."})
                continue
            if matches:
                existing_key, entry = matches[0]
                config = entry.get("config") if isinstance(entry, dict) else None
                if (not isinstance(config, dict) or entry.get("builtin") is not False
                        or entry.get("enabled") is False or config.get("enabled") is False
                        or config.get("type", "http") != "http"
                        or not isinstance(config.get("url"), str)
                        or config["url"].rstrip("/") != url.rstrip("/")):
                    results.append({**rec, "status": "configuration_conflict",
                                    "detail": "Existing connection is disabled or differs from the required HTTP "
                                              "endpoint. It was not overwritten or duplicated."})
                else:
                    results.append({**rec, "scout_key": existing_key, "status": "present",
                                    "detail": "HTTP connection already registered; Scout owns discovery and sign-in."})
                continue
            if not register:
                results.append({**rec, "status": "would_register",
                                "detail": "Setup will register this HTTP connection automatically; no form entry needed."})
                continue
            if not backed_up and os.path.exists(cfg_path):
                backed_up = _backup(cfg_path)
            existing[key] = {
                "builtin": False,
                "config": {"name": m.get("server_name", key), "type": "http", "url": url},
                "tools": [],
            }
            dirty = True
            results.append({**rec, "status": "registered",
                            "detail": "HTTP connection registered automatically - RESTART Scout for tool discovery "
                                      "and complete a supported sign-in prompt if required."})
            continue
        if transport != "command":
            raise ValueError(f"Unsupported setup MCP transport: {transport}")
        if key in existing:
            entry = existing[key]
            required = set(m.get("readiness_tools", m.get("tools", [])) or [])
            configured = entry.get("tools") if isinstance(entry, dict) else None
            if (not isinstance(entry, dict) or entry.get("enabled") is False
                    or required and (not isinstance(configured, list)
                                     or any(not isinstance(tool, str) or not tool for tool in configured)
                                     or not required.issubset(configured))):
                results.append({**rec, "status": "capabilities_missing",
                                "detail": "Server disabled or required tool allowlist missing; "
                                          "correct configuration and restart Scout. Registration is not access proof."})
                continue
            if m.get("launcher") == "agency":
                config = entry.get("config")
                command = config.get("command") if isinstance(config, dict) else None
                if (not isinstance(command, str)
                        or not (os.path.isfile(expand(command)) or shutil.which(expand(command)))):
                    results.append({**rec, "status": "launcher_missing",
                                    "detail": "Configured Agency launcher is missing; ask for its actual path "
                                              "and review the existing server in Scout. No entry was replaced."})
                    continue
            results.append({**rec, "status": "present", "detail": "already in Scout config"})
            continue
        # Not registered. Is its launcher present?
        if m.get("launcher") == "agency":
            if agency is None:
                results.append({**rec, "status": "launcher_missing",
                                "detail": "Agency launcher not found. Ask for its installation path, then "
                                          "rerun with --agency-path <executable>; defaults are only candidates."})
                continue
            cmd, provider_ok = agency, _shell_ok([agency, "--version"])
            if not provider_ok:
                results.append({**rec, "status": "provider_missing",
                                "detail": "The selected Agency executable failed --version; "
                                          "verify its installation before registering any server."})
                continue
        else:
            provider_ok = _shell_ok(m.get("provider", ""))
            cmd = expand(m.get("command", ""))
        launcher_ok = bool(cmd) and os.path.isfile(cmd)
        if not provider_ok and not launcher_ok:
            results.append({**rec, "status": "provider_missing",
                            "detail": m.get("note", "provider/launcher not found")})
            continue
        if not launcher_ok:
            results.append({**rec, "status": "launcher_missing",
                            "detail": f"launcher not found at {cmd or '(unset)'}"})
            continue
        if not register:
            results.append({**rec, "status": "would_register",
                            "detail": "run bootstrap (or infra --register) to add it"})
            continue
        # Build args, expanding any dynamic directive (e.g. Kusto known-services
        # from a data list) so multi-cluster config stays pure data.
        args = list(m.get("args", []))
        ks_from = m.get("known_services_from")
        if ks_from:
            clusters = req.get(ks_from, []) or []
            known = [{"service_uri": c.get("service_uri"),
                      "default_database": c.get("default_database"),
                      "description": c.get("description", "")}
                     for c in clusters if c.get("service_uri")]
            if known:
                args += ["--known-services", json.dumps(known)]
        # Register it into the config (backup once).
        if not backed_up and os.path.exists(cfg_path):
            backed_up = _backup(cfg_path)
        existing[key] = {
            "builtin": False,
            "config": {"name": name.split(" MCP")[0].strip() or key,
                       "type": "command", "command": cmd, "args": args},
            # Command-based servers: Scout reads this allowlist STATICALLY and drops
            # the server if it's empty for a server that needs it. Honor a declared
            # `tools` list (e.g. the Teams MCP's 36 tools); default [] otherwise.
            "tools": list(m.get("tools", []) or []),
        }
        dirty = True
        results.append({**rec, "status": "registered",
                        "detail": "added to Scout config — RESTART Scout to load"})

    if dirty:
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=os.path.dirname(cfg_path),
                prefix=".mcp-setup-", suffix=".tmp", delete=False) as fh:
            tmp = fh.name
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
        try:
            current = None
            if os.path.exists(cfg_path):
                with open(cfg_path, "rb") as stream:
                    current = stream.read()
            if current != before:
                raise ValueError("Scout MCP configuration changed during setup; reload it before retrying. "
                                 "No connection was overwritten.")
            os.replace(tmp, cfg_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return results


def check_m365_connectors(req: dict, tool_inventory=None, *, now=None, structured_fetch=None):
    if "m365_connectors" not in req:
        return None
    from . import m365, m365_capabilities, structured_fetch as F

    config = req["m365_connectors"]
    if (not isinstance(config, dict)
            or any(not isinstance(config.get(key), str) or not config[key].strip()
                   for key in ("calendar_mail_files", "teams"))):
        raise ValueError("m365_connectors must declare native calendar/mail/files and Teams integrations")
    report = {
        "status": "inventory_required",
        "required_tools": list(m365_capabilities.REQUIRED_TOOLS),
        "missing_tools": [],
        "structured_fetch": structured_fetch,
        "detail": (
            "The terminal cannot see which Microsoft 365 tools Scout has loaded. "
            "This check must happen inside Scout before release initialization; "
            "it does not mean a connector is missing."
        ),
        "next_action": (
            "In Scout, run: /release-agent check setup. "
            "Scout performs the check; you do not need to create files or run terminal commands."
        ),
        "inventory_format": {
            "version": 1, "observed_at": "<actual offset-aware observation time>",
            "available_tools": {"<canonical required tool>": "<actual exposed tool name>"},
        },
        "read_probes_required": True,
        "permission_to_send": False, "permission_to_upload": False,
    }
    if tool_inventory is None:
        return report
    if (not isinstance(tool_inventory, dict)
            or type(tool_inventory.get("version")) is not int or tool_inventory["version"] != 1):
        raise ValueError("M365 setup requires a version-1 loaded-tool inventory")
    m365.validate_freshness(tool_inventory.get("observed_at"), now=now)
    missing = m365_capabilities.missing_tools(
        tool_inventory.get("available_tools"),
        structured_fetch=structured_fetch or tool_inventory.get("structured_fetch"), now=now)
    report["structured_fetch"] = F.route(
        tool_inventory["available_tools"],
        structured_fetch or tool_inventory.get("structured_fetch"), now=now) or structured_fetch
    report["missing_tools"] = missing
    if missing:
        report["status"] = "missing_tools"
        integrations = []
        for label, needed in (
                ("WorkIQ structured fetch (MCP or native CLI)", lambda tool: tool == "workiq.fetch"),
                ("Microsoft 365 calendar/mail/files", lambda tool: tool.startswith("workiq_")),
                ("Microsoft Teams", lambda tool: tool.startswith("microsoft_teams-")),
                ("Browser", lambda tool: tool.startswith("playwright-"))):
            if any(needed(tool) for tool in missing):
                integrations.append(label)
        report["detail"] = "Required tools are not available in Scout: " + ", ".join(integrations) + "."
        report["next_action"] = (
            "In Scout, run: /release-agent check setup. "
            "Scout will identify which integration to enable or reconnect."
        )
    else:
        report["status"] = "tools_available"
        report["detail"] = (
            "Required tools were observed in Scout. This is not authenticated access proof: "
            "release entry still requires fresh owner/target-bound m365-capabilities reads "
            "and separate human send/upload authorization."
        )
        report["next_action"] = "Setup prerequisites are ready. In Scout, run: /release-agent start a release."
    return report


def run(req_path: str, register: bool = True, *, tool_inventory=None,
        scout_root=None, agency_path=None, workiq_path=None) -> dict:
    """Full infra preflight. Returns a structured report.

    If Scout itself isn't present (~/.scout missing), MCP registration is skipped
    (there's no config to write) and the report flags scout_missing — install Scout
    first, then re-run.
    """
    req = load_requirements(req_path)
    profile = scout_profile_root(scout_root)
    from .structured_fetch import discover_cli
    fetch = discover_cli(profile, workiq_path) if "m365_connectors" in req else None
    connectors = check_m365_connectors(req, tool_inventory, structured_fetch=fetch)
    cfg_path = (scout_mcp_config_path() if scout_root is None else
                os.path.join(profile, "m-mcp-servers.json"))
    needs_agency = (any(r.get("id") == "agency-cli" for r in req.get("requirements", []))
                   or any(m.get("launcher") == "agency" for m in req.get("mcp_servers", [])))
    agency = (resolve_agency_path(req, _load_scout_config(cfg_path)["servers"], agency_path)
              if needs_agency else None)
    reqs = check_requirements(req, scout_root=profile, agency_path=agency)
    scout_present = os.path.isdir(profile)
    if scout_present:
        mcps = ensure_mcp_servers(req, register, scout_root=scout_root, agency_path=agency_path)
    else:
        # can't register into a non-existent Scout config — report as pending
        mcps = [{"id": m.get("id"), "name": m.get("name", m.get("id")),
                 "scout_key": m.get("scout_key") or m.get("id"),
                 "status": "scout_missing", "detail": "install Scout first"}
                for m in req.get("mcp_servers", [])]
    restart_needed = any(m["status"] == "registered" for m in mcps)
    infrastructure_ok = (scout_present and all(r["ok"] for r in reqs)
                         and all(m["status"] in ("present", "registered") for m in mcps))
    ok = infrastructure_ok and (connectors is None or connectors["status"] == "tools_available")
    if not infrastructure_ok:
        setup_status = "blocked"
        next_action = "Fix the reported installation items, then rerun setup."
    else:
        setup_status = ("ready" if ok else "needs_scout_check"
                        if connectors["status"] == "inventory_required" else "blocked")
        next_action = (connectors["next_action"] if connectors else
                       "Setup prerequisites are ready. In Scout, run: /release-agent start a release.")
    path_requests = []
    if not scout_present:
        path_requests.append({
            "id": "scout_root", "flag": "--scout-root",
            "question": "Where is the existing Scout profile directory for this installation?",
        })
    if needs_agency and agency is None:
        path_requests.append({
            "id": "agency_path", "flag": "--agency-path",
            "question": "Where is the Agency CLI executable installed on this machine?",
        })
    return {"requirements": reqs, "mcp_servers": mcps, "m365_connectors": connectors,
            "infrastructure_ok": infrastructure_ok,
            "setup_status": setup_status, "next_action": next_action,
            "scout_present": scout_present,
            "ok": ok, "restart_needed": restart_needed,
            "scout_root": profile, "scout_mcp_config": cfg_path,
            "agency_path": agency, "path_requests": path_requests}
