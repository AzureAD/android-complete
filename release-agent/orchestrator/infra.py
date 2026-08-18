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
    return os.path.join(os.path.expanduser("~"), ".scout", "m-mcp-servers.json")


def expand(s: str) -> str:
    """Expand %VARS% / $VARS and ~ in a path string."""
    return os.path.expanduser(os.path.expandvars(s or ""))


def load_requirements(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _shell_ok(cmd: str, timeout: int = 30) -> bool:
    """True if the shell command exits 0. Used for CLI + provider checks."""
    if not cmd:
        return False
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def check_requirements(req: dict) -> list:
    """Return [{id,name,ok,install}] for each shell-checkable requirement."""
    out = []
    for r in req.get("requirements", []):
        out.append({
            "id": r.get("id", ""), "name": r.get("name", r.get("id", "?")),
            "ok": _shell_ok(r.get("check", "")), "install": r.get("install", ""),
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


def ensure_mcp_servers(req: dict, register: bool) -> list:
    """Check each required MCP server against Scout's config. When `register` is
    True, add any that are missing (whose provider launcher exists), backing up
    the config once before the first write.

    Returns [{id,name,scout_key,status,detail}] where status is one of:
      present | registered | would_register | provider_missing | launcher_missing
    """
    servers = req.get("mcp_servers", [])
    if not servers:
        return []
    cfg_path = scout_mcp_config_path()
    cfg = _load_scout_config(cfg_path)
    existing = cfg.get("servers", {})
    results, dirty, backed_up = [], False, None

    for m in servers:
        key = m.get("scout_key") or m.get("id")
        name = m.get("name", key)
        rec = {"id": m.get("id"), "name": name, "scout_key": key}
        if key in existing:
            results.append({**rec, "status": "present", "detail": "already in Scout config"})
            continue
        # Not registered. Is its launcher present?
        provider_ok = _shell_ok(m.get("provider", ""))
        cmd = expand(m.get("command", ""))
        launcher_ok = bool(cmd) and os.path.exists(cmd)
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


def run(req_path: str, register: bool = True) -> dict:
    """Full infra preflight. Returns a structured report.

    If Scout itself isn't present (~/.scout missing), MCP registration is skipped
    (there's no config to write) and the report flags scout_missing — install Scout
    first, then re-run.
    """
    req = load_requirements(req_path)
    reqs = check_requirements(req)
    scout_present = os.path.isdir(os.path.join(os.path.expanduser("~"), ".scout"))
    if scout_present:
        mcps = ensure_mcp_servers(req, register)
    else:
        # can't register into a non-existent Scout config — report as pending
        mcps = [{"id": m.get("id"), "name": m.get("name", m.get("id")),
                 "scout_key": m.get("scout_key") or m.get("id"),
                 "status": "scout_missing", "detail": "install Scout first"}
                for m in req.get("mcp_servers", [])]
    restart_needed = any(m["status"] == "registered" for m in mcps)
    ok = (scout_present and all(r["ok"] for r in reqs)
          and all(m["status"] in ("present", "registered") for m in mcps))
    return {"requirements": reqs, "mcp_servers": mcps,
            "scout_present": scout_present,
            "ok": ok, "restart_needed": restart_needed,
            "scout_mcp_config": scout_mcp_config_path()}
