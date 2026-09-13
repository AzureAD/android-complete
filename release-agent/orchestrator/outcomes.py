"""Step outcomes — the ONE uniform return contract for every release step.

Historically the engine had two disjoint mechanisms: `agent` steps returned a
`StepResult` and were run in-process, while `scout` steps had no runner at all —
the engine just held, and the skill did the work via ad-hoc `prepare-X` commands
scattered across `orchestrator/commands/`. That split is why adding a scout step
touched ~6 files.

This module gives EVERY step one vocabulary. A step handler returns exactly one
of these, and the engine/skill react uniformly:

    Done       — the step is complete (an agent did it, or nothing to do).
    Blocked    — an agent hit a real problem the owner must resolve.
    InProgress — underlying work is still running; retain ownership and poll later.
    NeedsHuman — a person must confirm/act (attestation or reminder).
    NeedsSkill — scout-assisted: the SKILL must run `tool` with `payload` (an MCP
                 call the engine can't make), then record the step. The step
                 DESCRIBES the action as data, so the skill executor is generic —
                 no per-step instructions in the skill's reference docs.

Auto handlers return AutoOutcome directly from build(context), execute(context), or
reconcile(context). The engine rejects other return types before applying an outcome.
There is no separate runner result or compatibility adapter.

Pure data — no IO, no engine imports — shared by the engine, CLI, and handlers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from .evidence import EvidenceUpdate


@dataclass
class Done:
    note: str = ""
    by: str = "agent"          # 'agent' | 'human'
    links: list = field(default_factory=list)   # [{name, url}] durable refs
    kind: str = "done"
    updates: tuple[EvidenceUpdate, ...] = ()


@dataclass
class Blocked:
    reason: str
    links: list = field(default_factory=list)   # [{name, url}] durable refs
    by: str = "agent"
    kind: str = "blocked"
    updates: tuple[EvidenceUpdate, ...] = ()


@dataclass
class InProgress:
    """An agent step whose underlying work is STILL RUNNING (not a failure, not done).

    Used by the Phase-2 MRWP verification when the RC pipeline run's overall status is
    notStarted/inProgress: the step must NOT block as 'aborted' (a never-ran stage during
    an in-flight run is just not-run-YET). The engine holds the phase as 'waiting on the
    pipeline' — no user action — and a poller re-runs the step every `poll_in_min` minutes
    until the run completes, at which point the normal Done/Blocked rules apply."""
    note: str = ""
    links: list = field(default_factory=list)
    poll_in_min: int = 30
    kind: str = "in_progress"
    updates: tuple[EvidenceUpdate, ...] = ()


AutoOutcome = Done | Blocked | InProgress


def valid_links(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(link, dict) for link in value)


def require_auto_outcome(value: object) -> AutoOutcome:
    """Reject unsupported handler results before changing step lifecycle state."""
    if isinstance(value, (Done, Blocked, InProgress)):
        expected_kind = "done" if isinstance(value, Done) else (
            "blocked" if isinstance(value, Blocked) else "in_progress")
        if value.kind != expected_kind:
            raise ValueError("Outcome kind must match its canonical type")
        text = value.reason if isinstance(value, Blocked) else value.note
        if not isinstance(text, str) or not valid_links(value.links):
            raise TypeError("Outcome text and links must be a string and list")
        if isinstance(value, (Done, Blocked)) and not isinstance(value.by, str):
            raise TypeError("Outcome by must be an attribution string")
        if isinstance(value, InProgress) and (
            type(value.poll_in_min) is not int or value.poll_in_min <= 0
        ):
            raise ValueError("Outcome poll_in_min must be a positive integer")
        return value
    raise TypeError(
        f"Auto handler returned {type(value).__name__}; expected Done/Blocked/InProgress"
    )


@dataclass
class NeedsHuman:
    prompt: str
    attest: bool = False       # True → attestation (confirm), False → plain reminder/to-do
    kind: str = "needs_human"
    updates: tuple[EvidenceUpdate, ...] = ()


@dataclass
class NeedsSkill:
    """A scout-assisted action the SKILL must execute (an MCP/browser call the
    deterministic engine can't make), described as data so the skill is generic.

      tool       — the skill tool/verb to run, e.g. 'workiq_send_email',
                   'workiq_send_chat_message', or a follow-up engine command name.
      payload    — kwargs for that tool (already resolved: recipients, subject,
                   html body, chat target, …). The skill passes it through.
      record_as  — owning step; notifications complete through claim/result, other
                    work uses record-step or its domain follow-up.
      summary    — a one-line human description ('email the code-complete notice
                   to <n> recipients') for the skill to show / log.
      note       — optional detail stored with the recorded step.
      outbound   — True when performing this action sends something EXTERNAL
                   (an email, a Teams post, a pipeline trigger) as opposed to a
                   local follow-up engine command (e.g. check-lockdown). When an
                   automation runs the step headless, an outbound action gets a
                   courtesy copy to the owner's Scout DM so they see what went out.
    """
    tool: str
    payload: dict = field(default_factory=dict)
    record_as: str = ""
    summary: str = ""
    note: str = ""
    outbound: bool = False
    notification: dict = field(default_factory=dict)  # optional checkpoint + completion metadata
    kind: str = "needs_skill"
    updates: tuple[EvidenceUpdate, ...] = ()


Outcome = AutoOutcome | NeedsHuman | NeedsSkill


def as_dict(outcome: Any) -> dict:
    """Serialize any outcome to a plain dict (for `--json` CLI output / the skill)."""
    from .step_context import thaw
    d = {k: thaw(v) for k, v in vars(outcome).items() if k != "updates"}
    return d


import re as _re

# An ENGINE follow-up command verb is all-lowercase, hyphen-separated (e.g. 'record-rc-report',
# 'create-payload-wiki'). An MCP/skill tool has a different shape — a service prefix and/or
# underscores/uppercase (e.g. 'workiq_send_email', 'microsoft_teams-SendMessageToChannel',
# 'kusto_query'). This distinction is the engine↔skill contract seam: a NeedsSkill.tool that is
# an engine command, and every NeedsSkill payload `followup_command`, must name a registered CLI
# command. See tests/test_contract.py for the guardrail that enforces it.
_ENGINE_CMD_RE = _re.compile(r"^[a-z][a-z0-9-]*$")


def command_verb(s):
    """The leading engine-CLI verb of a `followup_command` or an engine-command `tool` string,
    or None when `s` is an MCP/skill tool (not an engine command).

    'create-payload-wiki --release 2026-08 --dry-run' -> 'create-payload-wiki'
    'record-rc-report'                                 -> 'record-rc-report'
    'workiq_send_email' / 'microsoft_teams-SendMessageToChannel' / 'kusto_query' -> None
    """
    if not s:
        return None
    head = str(s).strip().split()[0]
    return head if _ENGINE_CMD_RE.match(head) else None
