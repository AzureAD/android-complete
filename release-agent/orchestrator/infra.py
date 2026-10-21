"""Infrastructure preflight — verify (and auto-provision) everything the skill
needs on a machine BEFORE the tool-level requirements: CLIs, the launchers that
back MCP servers, and the MCP servers themselves registered into Scout's config.

Design:
  * CLI/host/python deps ("requirements") → shell-checked (same as before).
  * MCP servers ("mcp_servers") → live in Scout's own config file
    (~/.scout/m-mcp-servers.json), which loads at startup. We can't "install" them,
    but we CAN register a missing one into that file (backing it up first) so it
    loads on the next Scout restart. Each entry names its `provider` (a shell check
    that the launcher exists) so we never register a server whose launcher is absent.
  * M365 connectors ("m365_connectors") -> require a fresh inventory from inside
    Scout. Configured servers and native WorkIQ tools do not prove hosted entity
    fetch is loaded. Setup reports a hold until the required tools are observed.

This module is pure-Python and import-light so bootstrap.ps1 can call it via the
CLI (`python -m orchestrator.cli infra`). It only touches Scout's MCP config when
asked to register, and always backs it up.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-{stamp}"
    shutil.copy2(path, dst)
    return dst


def ensure_mcp_servers(req: dict, register: bool, *, scout_root=None, agency_path=None) -> list:
    """Check each required MCP server against Scout's config. When `register` is
    True, add any that are missing (whose provider launcher exists), backing up
    the config once before the first write.

    Returns [{id,name,scout_key,status,detail}] where status is one of:
      present | registered | would_register | provider_missing | launcher_missing
    """
    servers = req.get("mcp_servers", [])
    if not servers:
        return []
    cfg_path = (scout_mcp_config_path() if scout_root is None else
                os.path.join(scout_profile_root(scout_root), "m-mcp-servers.json"))
    cfg = _load_scout_config(cfg_path)
    existing = cfg.get("servers", {})
    agency = resolve_agency_path(req, existing, agency_path)
    results, dirty, backed_up = [], False, None

    for m in servers:
        key = m.get("scout_key") or m.get("id")
        name = m.get("name", key)
        rec = {"id": m.get("id"), "name": name, "scout_key": key}
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
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, cfg_path)
    return results


def check_m365_connectors(req: dict, tool_inventory=None, *, now=None):
    if "m365_connectors" not in req:
        return None
    from . import m365, m365_capabilities

    config = req["m365_connectors"]
    if not isinstance(config, dict):
        raise ValueError("m365_connectors must declare the hosted WorkIQ endpoint")
    endpoint = config.get("hosted_endpoint")
    if not isinstance(endpoint, str):
        raise ValueError("m365_connectors must declare the hosted WorkIQ endpoint")
    m365_capabilities.site_path(endpoint)
    report = {
        "status": "inventory_required",
        "required_tools": list(m365_capabilities.REQUIRED_TOOLS),
        "missing_tools": [],
        "hosted_endpoint": endpoint,
        "detail": (
            "Loaded Scout tool inventory is required before release initialization. "
            "Configure and sign in to the supported hosted WorkIQ connector in Scout, "
            "reload its tools, then rerun infra --no-register --tools-file <inventory.json>. "
            "Native workiq_* wrappers and MCP registration are not hosted entity access."
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
    missing = m365_capabilities.missing_tools(tool_inventory.get("available_tools"))
    report["missing_tools"] = missing
    if missing:
        report["status"] = "missing_tools"
        report["detail"] = "Missing loaded connector capabilities: " + ", ".join(missing) + ". " + report["detail"]
    else:
        report["status"] = "tools_available"
        report["detail"] = (
            "Required tools were observed in Scout. This is not authenticated access proof: "
            "release entry still requires fresh owner/target-bound m365-capabilities reads "
            "and separate human send/upload authorization."
        )
    return report


def run(req_path: str, register: bool = True, *, tool_inventory=None,
        scout_root=None, agency_path=None) -> dict:
    """Full infra preflight. Returns a structured report.

    If Scout itself isn't present (~/.scout missing), MCP registration is skipped
    (there's no config to write) and the report flags scout_missing — install Scout
    first, then re-run.
    """
    req = load_requirements(req_path)
    connectors = check_m365_connectors(req, tool_inventory)
    profile = scout_profile_root(scout_root)
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
            "scout_present": scout_present,
            "ok": ok, "restart_needed": restart_needed,
            "scout_root": profile, "scout_mcp_config": cfg_path,
            "agency_path": agency, "path_requests": path_requests}
