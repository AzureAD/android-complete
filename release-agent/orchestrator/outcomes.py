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
    NeedsHuman — a person must confirm/act (attestation or reminder).
    NeedsSkill — scout-assisted: the SKILL must run `tool` with `payload` (an MCP
                 call the engine can't make), then record the step. The step
                 DESCRIBES the action as data, so the skill executor is generic —
                 no per-step instructions in the skill's reference docs.

Pure data — no IO, no engine imports — so it's trivially testable and shared by
the engine, the CLI, and the step handlers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Done:
    note: str = ""
    by: str = "agent"          # 'agent' | 'human'
    links: list = field(default_factory=list)   # [{name, url}] durable refs
    kind: str = "done"


@dataclass
class Blocked:
    reason: str
    links: list = field(default_factory=list)   # [{name, url}] durable refs
    kind: str = "blocked"


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


@dataclass
class NeedsHuman:
    prompt: str
    attest: bool = False       # True → attestation (confirm), False → plain reminder/to-do
    kind: str = "needs_human"


@dataclass
class NeedsSkill:
    """A scout-assisted action the SKILL must execute (an MCP/browser call the
    deterministic engine can't make), described as data so the skill is generic.

      tool       — the skill tool/verb to run, e.g. 'workiq_send_email',
                   'workiq_send_chat_message', or a follow-up engine command name.
      payload    — kwargs for that tool (already resolved: recipients, subject,
                   html body, chat target, …). The skill passes it through.
      record_as  — the step id to `record-step` once the tool succeeds.
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
    kind: str = "needs_skill"


def as_dict(outcome: Any) -> dict:
    """Serialize any outcome to a plain dict (for `--json` CLI output / the skill)."""
    d = {k: v for k, v in vars(outcome).items()}
    return d
