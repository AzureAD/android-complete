"""Guardrail tests for the engine↔skill contract.

A scout/agent step returns a `NeedsSkill(tool, payload, record_as, ...)`. The skill executes
`tool` (an MCP tool OR an engine follow-up command) and, when the payload names a
`followup_command`, runs that engine command instead of a blind `record-step`. Those engine
command names live in code as string literals (and are described in prose in skill/SKILL.md), so
they can silently drift from the actual registered CLI commands when a command is renamed.

These tests close that gap: every engine-command verb referenced by the contract — a NeedsSkill
`tool` that is an engine command, and every `followup_command` — MUST be a registered CLI command.
Rename a command without updating the string → this fails loudly.
"""
from tests._harness import *  # noqa: F401,F403

import os
import re
import argparse

from orchestrator.outcomes import command_verb


def _registered_verbs():
    """The set of subcommand names the CLI actually registers."""
    from orchestrator.cli import build_parser
    parser = build_parser()
    verbs = set()
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            verbs |= set(act.choices.keys())
    return verbs


def _scan_files():
    """All engine source files that may declare a NeedsSkill tool / followup_command."""
    roots = [os.path.join(ROOT, "steps"), os.path.join(ROOT, "orchestrator")]
    out = []
    for r in roots:
        for dirpath, _dirs, files in os.walk(r):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if fn.endswith(".py"):
                    out.append(os.path.join(dirpath, fn))
    return out


# Capture the FIRST string token of a `followup_command` payload value and of a NeedsSkill
# `tool=` kwarg, for plain and f-strings (the verb precedes any f-string `{...}`). `tool=`
# (kwarg) — NOT `"tool":` (which is how unrelated config dicts, e.g. integ_prs.CONFIG's
# gh/ado backend selector, spell it) — so config data isn't mistaken for a NeedsSkill tool.
_FOLLOWUP_RE = re.compile(r"""followup_command["']?\s*[:=]\s*f?["']([^"'{]+)""")
_TOOL_RE = re.compile(r"""\btool\s*=\s*f?["']([^"'{]+)""")


def test_command_verb_classifies_engine_vs_mcp():
    """The contract helper separates engine commands (lowercase-hyphen) from MCP tools."""
    assert command_verb("create-payload-wiki --release 2026-08 --dry-run") == "create-payload-wiki"
    assert command_verb("record-rc-report") == "record-rc-report"
    assert command_verb("check-lockdown") == "check-lockdown"
    # MCP / skill tools → None (underscore or Service-Prefix / CamelCase)
    assert command_verb("workiq_send_email") is None
    assert command_verb("microsoft_teams-SendMessageToChannel") is None
    assert command_verb("kusto_query") is None
    assert command_verb("azure_devops-pipelines_run_pipeline") is None
    assert command_verb("") is None and command_verb(None) is None


def test_followup_commands_are_registered_cli_commands():
    """Every `followup_command` string in the engine names a registered CLI command."""
    verbs = _registered_verbs()
    offenders = []
    for path in _scan_files():
        src = open(path, encoding="utf-8").read()
        for m in _FOLLOWUP_RE.finditer(src):
            v = command_verb(m.group(1))
            rel = os.path.relpath(path, ROOT)
            if v is None:
                offenders.append(f"{rel}: followup_command '{m.group(1).strip()}' is not engine-command-shaped")
            elif v not in verbs:
                offenders.append(f"{rel}: followup_command verb '{v}' is not a registered CLI command")
    assert not offenders, "engine↔skill contract drift:\n  " + "\n  ".join(offenders)


def test_engine_command_tools_are_registered_cli_commands():
    """Every NeedsSkill `tool` that is an ENGINE command (not an MCP tool) is registered."""
    verbs = _registered_verbs()
    offenders = []
    for path in _scan_files():
        src = open(path, encoding="utf-8").read()
        for m in _TOOL_RE.finditer(src):
            raw = m.group(1).strip()
            v = command_verb(raw)
            if v is None:            # an MCP/skill tool — outside the CLI registry, skip
                continue
            if v not in verbs:
                rel = os.path.relpath(path, ROOT)
                offenders.append(f"{rel}: NeedsSkill engine-command tool '{v}' is not registered")
    assert not offenders, "engine↔skill contract drift:\n  " + "\n  ".join(offenders)


def test_guardrail_actually_sees_the_contract():
    """Sanity: the scan finds a representative sample (so a broken regex can't pass vacuously)."""
    seen_followups, seen_tools = set(), set()
    for path in _scan_files():
        src = open(path, encoding="utf-8").read()
        for m in _FOLLOWUP_RE.finditer(src):
            v = command_verb(m.group(1))
            if v:
                seen_followups.add(v)
        for m in _TOOL_RE.finditer(src):
            v = command_verb(m.group(1))
            if v:
                seen_tools.add(v)
    # known contract members must be discovered by the scan
    assert {"record-rc-report", "record-telemetry", "record-status-email"} <= seen_followups
    assert {"create-payload-wiki", "create-oneauth-common-pr", "check-lockdown"} <= seen_tools
