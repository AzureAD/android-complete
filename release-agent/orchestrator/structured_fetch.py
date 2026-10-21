"""Discover structured WorkIQ reads without adding servers or acquiring tokens."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit

from . import m365


FETCH_TOOL = "workiq.fetch"
FETCH_ALIASES = {FETCH_TOOL, "workiq-fetch", "workiq:fetch", "mcp__workiq__fetch"}


def _command(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("WorkIQ launcher not found; supply --workiq-path with its actual installation path")
    if path.suffix.casefold() != ".cmd":
        return [str(path)]
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    launch = [line.strip() for line in lines if line.strip().endswith("%*")]
    if len(launch) != 1:
        raise ValueError("Unsupported WorkIQ command wrapper; inspect its supported launcher before continuing")
    parts = re.findall(r'"([^"]+)"', launch[0])
    if (len(parts) != 4 or launch[0] != " ".join(f'"{part}"' for part in parts) + " %*"
            or [Path(part).name.casefold() for part in parts] != [
                "node-runner.exe", "node.exe", "workiq-wrapper.cjs", "workiq.exe"]
            or any(not Path(part).is_absolute() or not Path(part).is_file() for part in parts)):
        raise ValueError("Unsupported or missing Scout WorkIQ wrapper components; do not bypass the wrapper")
    return parts


def discover_cli(scout_root, preferred=None):
    """Probe local help only. Discovery is not authenticated resource-access proof."""
    candidates = ([preferred] if preferred is not None else [
        os.path.join(scout_root, "bin", "workiq.cmd"),
        os.path.join(scout_root, "bin", "workiq.exe"),
        shutil.which("workiq"), shutil.which("workiq.exe"),
    ])
    path = next((value for value in candidates if value and os.path.isfile(value)), None)
    if path is None:
        return {"status": "missing", "detail": (
            "No WorkIQ launcher found for this Scout profile. Inspect actual loaded MCP fetch tools "
            "or supply --workiq-path; do not infer that directory reads are impossible.")}
    try:
        command = _command(path)
        result = subprocess.run(
            [*command, "fetch", "--help"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=30,
            env={**os.environ, "NODE_OPTIONS": ""}, shell=False)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "unsupported", "detail": str(exc)}
    if result.returncode or "--urls" not in result.stdout:
        return {"status": "unsupported", "detail": (
            "This WorkIQ launcher did not confirm fetch --urls support. "
            "Inspect its version/help; Ask-only support is not structured fetch.")}
    return {
        "status": "available", "kind": "cli", "command": command,
        "launcher": os.path.abspath(path), "scout_root": os.path.abspath(scout_root),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "fetch_help": {"exit_code": 0, "urls_option": True},
    }


def route(available, cli=None, *, now=None):
    actual = available.get(FETCH_TOOL) if isinstance(available, dict) else None
    if isinstance(actual, str) and actual in FETCH_ALIASES:
        return {"kind": "mcp", "tool": actual}
    if cli is None:
        return None
    if not isinstance(cli, dict):
        raise ValueError("Structured fetch discovery must be an object")
    if cli.get("status") in ("missing", "unsupported"):
        return None
    command = cli.get("command")
    if (cli.get("status") != "available" or cli.get("kind") != "cli"
            or not isinstance(command, list) or not command
            or any(not isinstance(part, str) or not os.path.isabs(part)
                   or not os.path.isfile(part) for part in command)
            or cli.get("fetch_help") != {"exit_code": 0, "urls_option": True}):
        raise ValueError("Structured fetch requires a real launcher and successful fetch --help evidence")
    m365.validate_freshness(cli.get("observed_at"), now=now)
    return cli


def request(paths, owner):
    result = m365.observation_request(FETCH_TOOL, {"entityUrls": paths}, owner)
    result["routing"] = (
        "Use the actual exposed WorkIQ MCP fetch tool, or the verified native route via "
        "workiq-read --scout-root <selected-profile> --account <verified-owner> "
        "--entity-url <exact requested path>. Never use Ask/people search as directory evidence.")
    return result


def read(scout_root, owner, path, *, preferred=None):
    """One exact read through the discovered CLI; shell metacharacters stay literal."""
    if (not isinstance(owner, str) or owner != owner.strip() or owner.count("@") != 1
            or not all(owner.split("@"))
            or any(char.isspace() for char in owner)):
        raise ValueError("WorkIQ reads require the verified account address")
    parsed = urlsplit(path)
    if (not path.startswith("/") or path.startswith("//") or parsed.scheme or parsed.netloc
            or parsed.fragment or any(char in path for char in "\r\n")):
        raise ValueError("WorkIQ requires an exact Graph-relative entity URL")
    discovered = discover_cli(scout_root, preferred)
    selected = route({}, discovered)
    if selected is None:
        raise ValueError(discovered["detail"])
    result = subprocess.run(
        [*selected["command"], "fetch", "--urls", path, "--account", owner],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120, env={**os.environ, "NODE_OPTIONS": ""}, shell=False)
    if result.returncode:
        diagnostics = sorted(set(re.findall(
            r"\b(?:AccessDenied|Authorization_RequestDenied|Forbidden|Unauthorized|"
            r"AuthenticationRequired|AADSTS\d+|401|403)\b", result.stdout + "\n" + result.stderr)))
        reported = f"; reported: {', '.join(diagnostics)}" if diagnostics else ""
        raise ValueError(
            f"WorkIQ fetch failed (exit {result.returncode}{reported}); inspect the tool's authentication/access "
            "diagnostic. No account, transport or resource fallback was attempted.")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("WorkIQ fetch did not return structured JSON; do not use a synthesis response") from exc
    envelope = {
        "version": 1, "tool": FETCH_TOOL, "arguments": {"entityUrls": [path]},
        "account": owner, "observed_at": datetime.now(timezone.utc).isoformat(),
        "result": value, "structured_fetch": selected,
    }
    m365.validate_observation(envelope, tool=FETCH_TOOL, arguments=envelope["arguments"], owner=owner)
    return envelope
