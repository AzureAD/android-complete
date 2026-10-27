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
_FOLLOWUP_RE = re.compile(r"""followup_command["']?\s*[:=]\s*\(?\s*f?["']([^"'{]+)""")
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
    assert {"record-bugbash-chat", "record-telemetry", "create-integration-prs"} <= seen_followups
    assert {"create-payload-wiki", "create-oneauth-common-pr", "check-lockdown"} <= seen_tools


def test_skill_requires_parallel_scout_drain_and_adoption_impact_review():
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    assert "all** entries in `scout_pending`" in skill
    assert "even when an independent" in skill and "step is blocked" in skill
    assert "only then render the final status" in skill
    assert "`invalidation.summary`" in skill
    assert "generic “new" in skill and "approval" in skill and "insufficient" in skill
    assert "direct MCP mail tool" in skill and "never" in skill
    assert "browser/OWA/Outlook/manual" in skill
    assert "provider_receipt.transport" in skill
    assert "Validation copies" in skill and "renderer-produced HTML/plain payload" in skill
    assert "Never hand-compose a shortened" in skill and "validation email" in skill


def test_skill_requires_actionable_user_holds():
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    assert "Every focused user action gets an actionable prompt" in skill
    assert "**Completed**" in skill and "**Not yet**" in skill and "**Need help**" in skill
    assert "**Fixed — rerun**" in skill and "**Override**" in skill and "**Keep blocked**" in skill
    assert "Never infer completion from “what’s next,”" in skill
    assert "Override first asks for a free-text reason" in skill


def test_skill_requires_fresh_table_at_every_release_iteration_and_prompt():
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    presentation = open(
        os.path.join(ROOT, "skill", "reference", "presenting-status.md"), encoding="utf-8").read()
    readiness = open(
        os.path.join(ROOT, "skill", "reference", "readiness-gate.md"), encoding="utf-8").read()
    assert "Every release iteration ends with one fresh table" in skill
    assert "final response or a handoff to `m_ask_user`" in skill
    for text in (skill, presentation, readiness):
        assert "display_markdown" in text
        assert "ready:false" in text
        assert "even if the table above didn't render" not in text
        assert "card is the guaranteed source of truth" not in text
    assert "stopping the loop never skips display" in skill
    assert "Setup-only onboarding" in skill
    assert "pending/failing rows" in readiness
    assert "before returning or" in presentation


def test_start_requires_automatic_readiness_before_the_final_table_handoff():
    documents = [
        open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        for path in ("SKILL.md", os.path.join("reference", "readiness-gate.md"),
                     os.path.join("reference", "starting-and-scheduling.md"))]
    for text in documents:
        assert "pending_auto_checks" in text and "same" in text.lower() and "turn" in text
        assert "not a stopping point" in text or "not just initialization" in text
        assert "record" in text and "permission" in text
    assert "`ready:false` alone is" in documents[0]
    assert "Do not loop/retry `failed_auto_checks`" in documents[0]
    assert "finish independent pending checks" in documents[0]
    assert "Future" in documents[1] and "not readiness" in documents[1]


def test_init_json_and_per_write_authorization_match_the_skill():
    from orchestrator.cli import build_parser
    args = build_parser().parse_args(["init", "--release", "2026-11", "--json"])
    assert args.json and args.release == "2026-11"
    documents = [
        open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        for path in ("SKILL.md", os.path.join("reference", "starting-and-scheduling.md"),
                     os.path.join("reference", "readiness-gate.md"))]
    for text in documents:
        assert "m365_write_access" not in text
        assert "blanket" in text and "owning write step" in text
    for text in documents[:2]:
        assert "init --release <id> --json" in text
        assert "next_action:provision_startup_automation" in text and "checklist_command" in text
        assert "--help" in text


def test_every_readiness_handoff_requires_exhaustion_not_a_python_only_table():
    documents = [
        open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        for path in ("SKILL.md", os.path.join("reference", "starting-and-scheduling.md"),
                     os.path.join("reference", "readiness-gate.md"),
                     os.path.join("reference", "presenting-status.md"))]
    for text in documents:
        assert "--attest-prompt --handoff --json" in text
        assert "may_return_to_user:false" in text and "exit 2" in text.lower()
        assert "needs_input" in text and "independent" in text
        assert "host consent" in text
    assert "Readiness execution boundary" in documents[0]
    assert "ordinary `checklist --verify` are not completion signals" in documents[0]
    assert "no final table yet" in documents[2]


def test_universal_dispatch_contract_is_not_a_phase_specific_rule():
    from orchestrator import automations
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    assert "Universal execution boundary" in skill and "ALL phases" in skill
    for path in ("SKILL.md", os.path.join("reference", "presenting-status.md"),
                 os.path.join("reference", "commands.md"), os.path.join("reference", "phases", "preflight.md")):
        text = open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        assert "work_dispatch" in text and "--handoff" in text
        assert "--hold-step" in text and "consent" in text
    for spec in automations.plan(CONFIG, "2026-09", "2026-09-09")["automations"]:
        assert "work_dispatch" in spec["prompt"] and "may_return_to_user:false" in spec["prompt"]
        assert "Notification-only/poll workers retain their" in spec["prompt"]


def test_required_driver_is_a_receipted_startup_obligation_not_an_attestation():
    from orchestrator import automations
    required = [item for item in automations.load_defs(CONFIG) if item.get("required_on_start")]
    assert [item["slug"] for item in required] == ["push-reminders"]
    for path in ("SKILL.md", os.path.join("reference", "starting-and-scheduling.md"),
                 os.path.join("reference", "readiness-gate.md"),
                 os.path.join("reference", "presenting-status.md")):
        text = open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        assert "driver" in text and ("receipt" in text or "acknowledged" in text)
        assert "startup" in text.lower() and "consent" in text
    entry = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    assert "startup_complete:false" in entry and "not unattended startup success" in entry


def test_worker_prompts_and_skill_ensure_desired_state_instead_of_owner_reset():
    from orchestrator import automations
    for worker in automations.plan(CONFIG, "2026-12", "2026-12-09")["automations"]:
        assert "At each operational run first" in worker["prompt"]
        assert "reconcile-create --ensure --claim" in worker["prompt"]
        assert "proven-absent settled worker" in worker["prompt"]
    for path in ("SKILL.md", os.path.join("reference", "starting-and-scheduling.md"),
                 os.path.join("reference", "commands.md")):
        text = open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        assert "--ensure" in text
        assert "absen" in text.lower() and "unknown" in text.lower()
