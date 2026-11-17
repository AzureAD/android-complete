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


def test_skill_defaults_to_guided_progression_and_preserves_approval_and_transport_review():
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    for command in ("status --release", "task --release", "record-fact --release", "next --release"):
        assert command in skill
    assert "automatic guided progression" in skill.lower()
    assert "An external/Scout step is still agent-owned work" in skill
    assert '"only this' in skill and "without draining siblings" in skill
    assert "at most once per unchanged progress snapshot" in skill
    assert "`invalidation.summary`" in skill
    assert "direct MCP mail tool" in skill and "never" in skill
    assert "browser/owa/outlook/manual" in skill.lower()
    assert "provider_receipt.transport" in skill
    assert "renderer-produced payload" in skill


def test_skill_requires_actionable_user_holds():
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    assert "one focused question" in skill and "needs_input" in skill
    assert "repeat a clear confirmation" in skill
    assert 'Never infer approval from "what\'s next?"' in skill
    assert "The owner may leave a task pending" in skill


def test_skill_requires_the_full_table_before_every_input_or_wait_handoff():
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    presentation = open(
        os.path.join(ROOT, "skill", "reference", "presenting-status.md"), encoding="utf-8").read()
    readiness = open(
        os.path.join(ROOT, "skill", "reference", "readiness-gate.md"), encoding="utf-8").read()
    for text in (skill, presentation, readiness):
        assert "display_markdown" in text
        assert "ready:false" in text
        assert "Every release iteration ends with one fresh table" not in text
    assert "--details" in skill and "--details" in presentation
    assert "always print the existing full status" in presentation
    assert "before the question/wait explanation" in presentation
    assert "before prompting" in skill
    assert "optional, not mandatory" not in presentation


def test_all_readiness_and_workflow_steps_are_mandatory_with_explicit_dependencies_and_tools():
    import yaml

    phases_raw = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    for phase in phases_raw["phases"]:
        assert phase.get("execution") == "parallel"
        for step in phase["steps"]:
            assert "required" not in step
            assert "depends_on" in step and isinstance(step["depends_on"], list)
            assert "required_tools" in step and isinstance(step["required_tools"], list)

    readiness_path = os.path.join(ROOT, "config", "readiness.yaml")
    readiness_raw = yaml.safe_load(open(readiness_path, encoding="utf-8"))
    for item in readiness_raw["items"]:
        assert "required" not in item
        assert "depends_on" in item and isinstance(item["depends_on"], list)
        assert "required_tools" in item and isinstance(item["required_tools"], list)

    documents = [
        open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        for path in ("SKILL.md", os.path.join("reference", "readiness-gate.md"),
                     os.path.join("reference", "starting-and-scheduling.md"))]
    for text in documents:
        assert "when used" not in text
        assert "mandatory" in text.lower()
        assert "consent" in text
    from orchestrator.state import ReleaseState
    assert ReleaseState().execution_mode == "interactive"
    assert "Do not loop/retry" in documents[0] and "failed_auto_checks" in documents[0]
    assert "fail-step" in documents[0]


def test_init_json_and_per_write_authorization_match_the_skill():
    from orchestrator.cli import build_parser
    args = build_parser().parse_args(["init", "--release", "2026-11", "--json"])
    assert args.json and args.release == "2026-11" and not args.unattended
    documents = [
        open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        for path in ("SKILL.md", os.path.join("reference", "starting-and-scheduling.md"),
                     os.path.join("reference", "readiness-gate.md"))]
    for text in documents:
        assert "m365_write_access" not in text
        assert "blanket" in text and "owning write step" in text
    for text in documents[:2]:
        assert "init --release <id> --json" in text
        assert "guided" in text.lower() and "--unattended" in text
        assert "--help" in text


def test_status_only_is_scoped_but_recorded_results_resume_guided_progression():
    documents = [
        open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        for path in ("SKILL.md", os.path.join("reference", "starting-and-scheduling.md"),
                     os.path.join("reference", "readiness-gate.md"),
                     os.path.join("reference", "presenting-status.md"))]
    assert "Status-only" in documents[0] and '"only this' in documents[0]
    assert "resume automatic guided progression" in documents[3]
    assert "Status-only" in documents[3] and "do not drain the broader queue" in documents[3]
    assert all("may_return_to_user:false" not in text for text in documents)
    assert "No blanket write authority" in documents[2]


def test_default_guided_dispatch_is_separate_from_opted_in_worker_execution():
    from orchestrator import automations
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    assert "By default, use `work_dispatch`" in skill
    assert "For explicitly requested unattended execution" in skill
    assert "Status-only" in skill and "explicitly one-task" in skill
    for spec in automations.plan(CONFIG, "2026-09", "2026-09-09")["automations"]:
        assert "work_dispatch" in spec["prompt"] and "may_return_to_user:false" in spec["prompt"]
        assert "Notification-only/poll workers retain their" in spec["prompt"]


def test_required_driver_is_only_an_opted_in_unattended_obligation():
    from orchestrator import automations
    required = [item for item in automations.load_defs(CONFIG) if item.get("required_on_start")]
    assert [item["slug"] for item in required] == ["push-reminders"]
    for path in ("SKILL.md", os.path.join("reference", "starting-and-scheduling.md"),
                 os.path.join("reference", "readiness-gate.md"),
                 os.path.join("reference", "presenting-status.md")):
        text = open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        assert ("driver" in text or "worker" in text) and ("receipt" in text or "acknowledged" in text)
        assert "unattended" in text.lower() and "consent" in text
    entry = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    assert "foreground `interactive` mode" in entry and "Do not create reminder workers" in entry


def test_worker_prompts_and_skill_ensure_desired_state_instead_of_owner_reset():
    from orchestrator import automations
    for worker in automations.plan(CONFIG, "2026-12", "2026-12-09")["automations"]:
        assert "At each operational run first" in worker["prompt"]
        assert "reconcile-create --ensure --claim" in worker["prompt"]
        assert "proven-absent settled worker" in worker["prompt"]
    for path in (os.path.join("reference", "starting-and-scheduling.md"),
                 os.path.join("reference", "commands.md")):
        text = open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        assert "--ensure" in text
        assert "absen" in text.lower() and "unknown" in text.lower()


def test_start_release_requires_explicit_folder_and_date_confirmation_before_init():
    for path in ("SKILL.md", os.path.join("reference", "starting-and-scheduling.md")):
        text = open(os.path.join(ROOT, "skill", path), encoding="utf-8").read()
        assert "android-complete" in text
        assert "preview-release --json" in text
        assert "ccd_past" in text
        assert "Custom day in" in text and "Any date in the future" in text
        assert "calendar month of the elected release date" in text
        assert "CCD month + 1" not in text
        assert "m_ask_user" in text
        assert "Recommended model" not in text
        for banned in ("claude", "opus", "sonnet", "gpt-", "gemini"):
            assert banned not in text.lower()
    skill = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
    readiness = open(os.path.join(ROOT, "skill", "reference", "readiness-gate.md"), encoding="utf-8").read()
    assert "complete-manually" in skill and "skip" in skill and "fail-step" in skill
    assert "Tool/connector failure (icm)" in readiness


def test_external_steps_declare_requires_approval_order_gather_first_and_bound_error_markdown():
    from datetime import date
    import yaml
    from orchestrator import render

    raw = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    for phase in raw["phases"]:
        for step in phase["steps"]:
            if step.get("kind") == "external":
                assert "requires_approval" in step and isinstance(step["requires_approval"], bool), (
                    f"{phase['id']}.{step['id']} must explicitly declare boolean requires_approval"
                )

    st = ReleaseState(
        release_id="2026-11", ccd="2026-11-20", ccd_source="manual",
        owner_email="pedroro@microsoft.com", timezone="America/Los_Angeles",
    )
    orch = Orchestrator(CONFIG, st, mocks=_safe(), as_of=date(2026, 11, 16))
    _pass_scout_checks(orch)
    orch.gate.sign()

    scout_keys = [
        item["key"]
        for item in orch.status_report()["work_dispatch"]["pending_work"]
        if item["kind"] == "scout"
    ]
    # Non-send gather step `lockdown` runs BEFORE outbound notifications (`notice`, `flight_reminder`)
    assert scout_keys[0] == "preflight.lockdown"
    assert set(scout_keys[1:]) == {"preflight.notice", "preflight.flight_reminder"}

    # Even a 10,000-char CLI error never bloats `status_view` / `display_markdown`
    orch.fail_step("preflight", "notice", "ERROR_LINE\n" * 1000, tool="workiq_send_email")
    orch.fail_step("preflight", "flight_reminder", "ERROR_LINE\n" * 1000, tool="workiq_send_chat_message")
    orch.fail_step("preflight", "lockdown", "SSO needed", tool="playwright")
    orch.run_until_gate()
    md = render.status_view(orch.status_report())
    assert "### Phases" in md and "### ▶ Current phase" in md
    assert len(md) < 5_000



