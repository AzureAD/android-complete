"""Release Orchestrator — the conductor (deterministic engine, X4).

Responsibilities (per §7.1):
  1. Load the release state machine from config/phases.yaml.
  2. Own the dispatch loop: find next step -> run its (stub) agent ->
     record result -> advance, or HOLD at a gate for human approval.
  3. Persist run-state via ReleaseState (X5).

The engine is the BRAIN: it decides what's next. The skill is only the mouth/ears.
No LLM logic here — this is fully unit-testable and replayable.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import yaml

from .state import ReleaseState, StepState, GateDecision, _now
from .readiness import ReadinessGate
from . import schedule
from . import mocks as mocks_mod
from steps.lib import mockctx
import steps
from phases import stub_runner


@dataclass
class NextAction:
    """What the conductor decided on this invocation — the engine's output."""
    kind: str                 # 'ran' | 'gate' | 'reminder' | 'scheduled' | 'complete' | 'idle' | 'readiness' | 'blocked' | 'halted'
    phase: Optional[str] = None
    step: Optional[str] = None
    name: Optional[str] = None
    message: str = ""


class Orchestrator:
    """The conductor: owns the state machine, dispatch loop, gates, and structured
    status. The readiness entry gate is delegated to ReadinessGate (self.gate);
    presentation lives in render.py. This class holds no formatting logic."""

    def __init__(self, config_path: str, state: ReleaseState, readiness_path: str = None,
                 as_of: date = None, mocks: dict = None):
        with open(config_path, "r", encoding="utf-8") as fh:
            self.config = yaml.safe_load(fh)
        readiness_cfg = None
        if readiness_path is None:
            readiness_path = os.path.join(os.path.dirname(config_path), "readiness.yaml")
        if os.path.exists(readiness_path):
            with open(readiness_path, "r", encoding="utf-8") as fh:
                readiness_cfg = yaml.safe_load(fh)
        self.state = state
        self.gate = ReadinessGate(readiness_cfg, state)
        # Local step mocks (personal, gitignored mocks.local.yaml). Absent → {}.
        # Pass mocks={} in tests for isolation from any developer's local file.
        self.mocks = mocks if mocks is not None else mocks_mod.load_mocks()
        self.gate.mocks = self.mocks             # readiness.<item> mocks for the entry gate
        # The simulated clock. Defaults to today; `--as-of` overrides it so a
        # `--as-of` can jump to CCD-7 and prove a phase opens on schedule.
        self.as_of = as_of or schedule.today()

    # ---- time anchoring (CCD-relative phase windows) ----
    def _ccd(self) -> Optional[date]:
        return schedule.parse_date(self.state.ccd)

    def _phase_anchor_date(self, phase: dict) -> Optional[date]:
        """The date a phase opens, or None if it has no anchor / CCD is unknown."""
        spec = phase.get("anchor")
        ccd = self._ccd()
        if not spec or ccd is None:
            return None
        return schedule.anchor_date(ccd, spec)

    def _phase_due(self, phase: dict) -> bool:
        """A phase is due once the clock reaches its anchor. No anchor ⇒ always due."""
        ad = self._phase_anchor_date(phase)
        return ad is None or self.as_of >= ad

    @staticmethod
    def _is_reminder(step: dict) -> bool:
        """A human, non-gate step is a reminder: the engine can't do it, so it
        holds and tells the person to do it, then waits for them to mark it done."""
        return step.get("owner") == "human" and not step.get("gate")

    # ---- state-machine traversal ----
    def _iter_steps(self):
        """Yield (phase_dict, step_dict) in definition order, skipping conditional
        phases unless explicitly activated on the state."""
        for phase in self.config["phases"]:
            if phase.get("conditional") and phase["id"] not in self._activated_conditionals():
                continue
            for step in phase["steps"]:
                yield phase, step

    def _activated_conditionals(self) -> set:
        # A conditional phase (e.g. hotfix) is activated by an explicit note flag.
        return {n.split("activate:")[1].strip()
                for n in self.state.notes if isinstance(n, str) and n.startswith("activate:")}

    def activate_conditional(self, phase_id: str) -> None:
        self.state.notes.append(f"activate:{phase_id}")

    def _first_incomplete(self):
        for phase, step in self._iter_steps():
            if not self.state.is_done(phase["id"], step["id"]):
                return phase, step
        return None, None

    # ---- dispatch ----
    def _current_phase(self):
        """The first included phase that still has incomplete steps (definition
        order). Conditional phases are skipped unless activated."""
        for phase in self.config["phases"]:
            if not self._phase_included(phase):
                continue
            if all(self.state.is_done(phase["id"], s["id"]) for s in phase["steps"]):
                continue
            return phase
        return None

    @staticmethod
    def _step_kind(step: dict) -> str:
        """Classify a step: gate | scout | attest | reminder | auto."""
        if step.get("gate"):
            return "gate"
        if step.get("source") == "scout":
            return "scout"
        if step.get("attest"):
            return "attest"
        if step.get("owner") == "human":
            return "reminder"
        return "auto"

    def _is_mocked(self, pid: str, step: dict) -> bool:
        """True if a local mock replaces this step. Gate steps are never mockable
        (a gate needs a real human decision)."""
        return ((not step.get("gate"))
                and mocks_mod.stepresult_for(self.mocks, pid, step["id"]) is not None)


    def _deps_met(self, pid: str, step: dict) -> bool:
        """True when every step this one depends_on is done (deps are within-phase)."""
        for dep in step.get("depends_on", []) or []:
            if not self.state.is_done(pid, dep):
                return False
        return True

    def step_once(self, attempted=None) -> NextAction:
        """Advance exactly one step (or hold). For a sequential phase this is the
        classic first-incomplete-step logic. For a parallel phase it runs one ready
        step whose dependencies are met, letting independent steps progress even
        when a sibling is holding. `attempted` (a set, managed by run_until_gate)
        prevents re-running an auto step twice within one drain."""
        if self.state.status == "complete":
            return NextAction(kind="complete", message="Release already complete.")

        # HALTED: emergency hold set by a human. Nothing advances until resume().
        if self.state.halted:
            self.state.status = "halted"
            return NextAction(
                kind="halted",
                message="Release is HALTED"
                        + (f": {self.state.halt_reason}" if self.state.halt_reason else "")
                        + ". Run resume to continue.",
            )

        # BLOCKED: an entry-gate item was declared unsatisfiable.
        if self.state.blocked:
            self.state.status = "blocked"
            labels = self.gate.blocked_labels()
            msg = (self.gate.config or {}).get("blocked_message", "").strip()
            return NextAction(
                kind="blocked",
                message="Entry gate blocked — cannot start: " + ", ".join(labels) + ". " + msg,
            )

        # ENTRY GATE: nothing runs until the readiness checklist is signed.
        if not self.state.readiness_signed:
            self.state.status = "readiness_gate"
            return NextAction(
                kind="readiness",
                message="HOLDING at the readiness entry gate. Sign the checklist before Phase 0 can start.",
            )

        phase = self._current_phase()
        if phase is None:
            self.state.status = "complete"
            self.state.current_phase = None
            self.state.current_step = None
            return NextAction(kind="complete", message="All steps done — release complete.")

        self.state.current_phase = phase["id"]

        # TIME GATE: if this phase hasn't reached its anchor date yet, hold as scheduled.
        if not self._phase_due(phase):
            opens = self._phase_anchor_date(phase)
            self.state.status = "scheduled"
            days = (opens - self.as_of).days
            first = next((s for s in phase["steps"]
                          if not self.state.is_done(phase["id"], s["id"])), None)
            return NextAction(
                kind="scheduled", phase=phase["id"],
                step=first["id"] if first else None,
                name=first["name"] if first else None,
                message=f"{phase['name']} opens {opens.isoformat()} "
                        f"({schedule.humanize_delta(days)}). Nothing to do yet.",
            )

        if phase.get("execution") == "parallel":
            return self._step_parallel(phase, attempted)
        return self._step_sequential(phase)

    # ---- sequential dispatch (classic: one step at a time, stop at first hold) ----
    def _step_sequential(self, phase: dict) -> NextAction:
        step = next(s for s in phase["steps"]
                    if not self.state.is_done(phase["id"], s["id"]))
        self.state.current_step = step["id"]

        # A locally-mocked step is resolved right here (skips its real scout/attest/
        # agent handling) so the flow advances naturally under Scout.
        if self._is_mocked(phase["id"], step):
            return self._run_auto_step(phase, step, block_holds=True)

        if step.get("gate") and not self._gate_approved(phase["id"], step["id"]):
            self.state.status = "holding_gate"
            return NextAction(
                kind="gate", phase=phase["id"], step=step["id"], name=step["name"],
                message=f"HOLDING at gate: {phase['name']} → {step['name']}. Awaiting human decision.",
            )

        if self._is_reminder(step):
            self.state.status = "awaiting_action"
            key = f"{phase['id']}.{step['id']}"
            if key not in self.state.pending_human:
                self.state.pending_human.append(key)
            if step.get("attest"):
                msg = (f"CONFIRM — attest that this is done to proceed: {step['name']}. "
                       f"Mark it done once you've verified it.")
            else:
                msg = f"ACTION NEEDED — you need to: {step['name']}. Mark it done when complete."
            return NextAction(kind="reminder", phase=phase["id"], step=step["id"],
                              name=step["name"], message=msg)

        if step.get("source") == "scout":
            self.state.status = "awaiting_action"
            key = f"{phase['id']}.{step['id']}"
            if key not in self.state.pending_human:
                self.state.pending_human.append(key)
            return NextAction(
                kind="reminder", phase=phase["id"], step=step["id"], name=step["name"],
                message=f"Scout-assisted check pending — {step['name']}. "
                        f"Scout runs this automatically when you open it.",
            )

        return self._run_auto_step(phase, step, block_holds=True)

    # ---- parallel dispatch (dependency-aware; independent steps don't block) ----
    def _step_parallel(self, phase: dict, attempted) -> NextAction:
        pid = phase["id"]

        def ready(s):
            return (not self.state.is_done(pid, s["id"])) and self._deps_met(pid, s)

        # 1) Run ONE ready, not-yet-attempted runnable step (auto agent, or an
        #    already-approved gate). Independent steps progress even if a sibling holds.
        for s in phase["steps"]:
            if not ready(s):
                continue
            kind = self._step_kind(s)
            runnable = (kind == "auto"
                        or (kind == "gate" and self._gate_approved(pid, s["id"]))
                        or self._is_mocked(pid, s))          # mocked steps run here too
            if not runnable:
                continue
            key = f"{pid}.{s['id']}"
            if attempted is not None and key in attempted:
                continue
            if attempted is not None:
                attempted.add(key)
            return self._run_auto_step(phase, s, block_holds=False)

        # 2) No more auto progress — surface the holds (all at once).
        holds = []
        for s in phase["steps"]:
            if not ready(s):
                continue
            kind = self._step_kind(s)
            blocked_auto = kind == "auto" and self.state.get_step(pid, s["id"]).status == "blocked"
            unapproved_gate = kind == "gate" and not self._gate_approved(pid, s["id"])
            if kind in ("scout", "attest", "reminder") or blocked_auto or unapproved_gate:
                holds.append(s)

        gates = [s for s in holds if self._step_kind(s) == "gate"]
        if gates:
            g = gates[0]
            self.state.status = "holding_gate"
            self.state.current_step = g["id"]
            return NextAction(kind="gate", phase=pid, step=g["id"], name=g["name"],
                              message=f"HOLDING at gate: {phase['name']} → {g['name']}. Awaiting human decision.")

        non_gate = [s for s in holds if self._step_kind(s) != "gate"]
        for s in non_gate:
            key = f"{pid}.{s['id']}"
            if key not in self.state.pending_human:
                self.state.pending_human.append(key)
        if non_gate:
            self.state.status = "awaiting_action"
            # Scout steps are the SKILL's automated work (it runs them via step-action),
            # NOT a user hold — so the current-step / action cue should point at a
            # genuine USER hold (attest / blocked / reminder) when one exists, and only
            # fall back to a scout step when scout work is all that's left.
            user_holds = [s for s in non_gate if self._step_kind(s) != "scout"]
            focus = (user_holds or non_gate)[0]
            self.state.current_step = focus["id"]
            names = "; ".join(s["name"] for s in non_gate)
            return NextAction(kind="reminder", phase=pid, step=focus["id"],
                              name=focus["name"],
                              message=f"{len(non_gate)} item(s) need attention: {names}")

        # Not complete, but nothing is ready — remaining steps wait on unmet deps.
        self.state.status = "awaiting_action"
        return NextAction(kind="reminder", phase=pid,
                          message="Waiting on prerequisite steps to complete.")

    def _run_auto_step(self, phase: dict, step: dict, block_holds: bool) -> NextAction:
        """Run an agent step. On success → done. On failure: in sequential mode
        (block_holds=True) HOLD as action-needed and break; in parallel mode
        (block_holds=False) mark it blocked + register it, but return 'ran' so the
        drain continues with independent steps."""
        pid = phase["id"]
        # A local mock short-circuits the real runner (agent call), returning the
        # declared StepResult (done → complete; blocked → hold).
        result = mocks_mod.stepresult_for(self.mocks, pid, step["id"])
        if result is None:
            # Resolve the runner from the co-located step module (KIND == 'agent');
            # steps without a module fall back to the stub. No agent registry.
            mod = steps.get_step(pid, step["id"])
            if mod is not None and getattr(mod, "KIND", None) == "agent" and hasattr(mod, "run"):
                runner = mod.run
            else:
                runner = stub_runner.run_stub
            # Expose any declared `input` knobs (e.g. cg `alerts`) to the step's
            # build() so its REAL logic runs on the injected data.
            with mockctx.active(self.mocks.get(f"{pid}.{step['id']}", {})):
                result = runner(pid, step, self.state)
        key = f"{pid}.{step['id']}"
        if not result.ok:
            self.state.set_step(pid, step["id"],
                                StepState(status="blocked", note=result.action, by=result.by,
                                          links=list(getattr(result, "links", None) or [])))
            if key not in self.state.pending_human:
                self.state.pending_human.append(key)
            if block_holds:
                self.state.status = "awaiting_action"
                return NextAction(kind="reminder", phase=pid, step=step["id"],
                                  name=step["name"],
                                  message=f"ACTION NEEDED — {step['name']}: {result.action}")
            return NextAction(kind="ran", phase=pid, step=step["id"], name=step["name"],
                              message=f"BLOCKED — {step['name']}: {result.action}")
        self.state.set_step(pid, step["id"],
                            StepState(status="done", completed_at=_now(),
                                      note=result.action, by=result.by,
                                      links=list(getattr(result, "links", None) or [])))
        if result.by == "human":
            self.state.pending_human = [p for p in self.state.pending_human if p != key]
        self.state.status = "running"
        return NextAction(kind="ran", phase=pid, step=step["id"],
                          name=step["name"], message=result.action)

    def run_until_gate(self, max_steps: int = 500) -> list:
        """Drive the loop until a gate hold, completion, or step cap.
        Returns the list of NextAction taken. `attempted` prevents re-running an
        auto step twice within this drain (so a re-blocking step can't loop)."""
        actions = []
        attempted = set()
        for _ in range(max_steps):
            act = self.step_once(attempted)
            actions.append(act)
            if act.kind in ("gate", "reminder", "scheduled", "complete", "readiness", "blocked", "halted"):
                break
        return actions

    # ---- manual overrides (human-driven transitions, §7.1 constraint #5) ----
    def _find_step(self, phase_id: str, step_id: str):
        phase = next((p for p in self.config["phases"] if p["id"] == phase_id), None)
        if phase and any(s["id"] == step_id for s in phase["steps"]):
            return phase
        return None

    def skip_step(self, phase_id: str, step_id: str, reason: str) -> NextAction:
        """Mark a step skipped (counts as done for progression) without running it.
        A reason is REQUIRED (audit). For 'doesn't apply' or 'done manually outside the tool'."""
        if not (reason and reason.strip()):
            return NextAction(kind="idle", message="A reason is required to skip a step.")
        if not self._find_step(phase_id, step_id):
            return NextAction(kind="idle", message=f"No such step: {phase_id}/{step_id}")
        self.state.set_step(phase_id, step_id,
                            StepState(status="skipped", completed_at=_now(),
                                      note=f"Skipped: {reason.strip()}", by="human"))
        if self.state.status == "holding_gate" and self.state.current_step == step_id:
            self.state.status = "running"
        return NextAction(kind="ran", phase=phase_id, step=step_id,
                          message=f"Skipped {phase_id}/{step_id} — {reason.strip()}")

    def complete_step(self, phase_id: str = None, step_id: str = None, note: str = "") -> NextAction:
        """Mark a reminder (human, non-gate) step done. Defaults to the step the
        conductor is currently holding on. This is how a person clears an
        'ACTION NEEDED' hold once they've actually done the task."""
        phase_id = phase_id or self.state.current_phase
        step_id = step_id or self.state.current_step
        if not (phase_id and step_id) or not self._find_step(phase_id, step_id):
            return NextAction(kind="idle", message=f"No such step: {phase_id}/{step_id}")
        self.state.set_step(phase_id, step_id,
                            StepState(status="done", completed_at=_now(),
                                      note=(note.strip() or "Marked done"), by="human"))
        key = f"{phase_id}.{step_id}"
        self.state.pending_human = [p for p in self.state.pending_human
                                    if p != key and not p.startswith(key + " ")]
        if self.state.status == "awaiting_action":
            self.state.status = "running"
        tail = f" — {note.strip()}" if note and note.strip() else ""
        return NextAction(kind="ran", phase=phase_id, step=step_id,
                          message=f"Done: {phase_id}/{step_id}{tail}")

    def record_scout_step(self, phase_id: str, step_id: str, status: str,
                          detail: str = "") -> NextAction:
        """Record the outcome of a scout-assisted step (one the skill ran via MCP/
        browser, e.g. the CCOA lockdown check).
          * status == "pass"      -> mark the step done and let the flow continue.
          * status == "attention" -> keep it held (needs the owner) with the detail
            (e.g. a Production CCOA lockdown overlaps — the owner must shift CCD)."""
        if not self._find_step(phase_id, step_id):
            return NextAction(kind="idle", message=f"No such step: {phase_id}/{step_id}")
        if status == "pass":
            return self.complete_step(phase_id, step_id, detail)
        # attention: leave the step outstanding, flagged for the owner.
        self.state.set_step(phase_id, step_id,
                            StepState(status="blocked", note=detail, by="scout"))
        self.state.status = "awaiting_action"
        key = f"{phase_id}.{step_id}"
        if key not in self.state.pending_human:
            self.state.pending_human.append(key)
        return NextAction(kind="reminder", phase=phase_id, step=step_id,
                          message=f"Needs your attention — {detail}")

    def reopen_step(self, phase_id: str, step_id: str, reason: str = "") -> NextAction:
        """Undo a done/skipped step so the conductor runs it again. Reason optional."""
        if not self._find_step(phase_id, step_id):
            return NextAction(kind="idle", message=f"No such step: {phase_id}/{step_id}")
        self.state.steps.pop(self.state.key(phase_id, step_id), None)  # remove -> pending
        # drop any prior gate approval for this step so a gate re-holds
        self.state.gate_decisions = [g for g in self.state.gate_decisions
                                     if g.get("step") != f"{phase_id}.{step_id}"]
        if self.state.status == "complete":
            self.state.status = "running"
        note = f" — {reason.strip()}" if reason and reason.strip() else ""
        return NextAction(kind="ran", phase=phase_id, step=step_id,
                          message=f"Reopened {phase_id}/{step_id}{note}")

    def halt(self, reason: str) -> NextAction:
        """Emergency hold — nothing advances until resume(). Reason REQUIRED (audit)."""
        if not (reason and reason.strip()):
            return NextAction(kind="idle", message="A reason is required to halt the release.")
        self.state.halted = True
        self.state.halt_reason = reason.strip()
        self.state.status = "halted"
        return NextAction(kind="halted", message=f"Release HALTED — {reason.strip()}")

    def resume(self, reason: str = "") -> NextAction:
        """Clear an emergency halt. Reason optional."""
        if not self.state.halted:
            return NextAction(kind="idle", message="Release is not halted.")
        self.state.halted = False
        self.state.halt_reason = None
        self.state.status = "running"
        note = f" — {reason.strip()}" if reason and reason.strip() else ""
        return NextAction(kind="idle", message=f"Release resumed{note}.")

    # ---- gates ----
    def _gate_approved(self, phase: str, step: str) -> bool:
        for gd in self.state.gate_decisions:
            if gd.get("step") == f"{phase}.{step}" and gd.get("decision") == "approved":
                return True
        return False

    def approve_gate(self, comment: str = "") -> NextAction:
        """Record approval for the current holding gate and continue."""
        phase = self.state.current_phase
        step = self.state.current_step
        if self.state.status != "holding_gate" or not phase:
            return NextAction(kind="idle", message="No gate is currently holding.")
        self.state.gate_decisions.append(
            asdict_gate(GateDecision(step=f"{phase}.{step}", decision="approved",
                                     at=_now(), comment=comment)))
        # mark the gate step done and advance
        self.state.set_step(phase, step,
                            StepState(status="done", completed_at=_now(),
                                      note=f"Gate approved. {comment}".strip(), by="human"))
        self.state.status = "running"
        return NextAction(kind="ran", phase=phase, step=step,
                          message=f"Gate approved: {phase} → {step}. {comment}".strip())

    def deny_gate(self, comment: str = "") -> NextAction:
        phase = self.state.current_phase
        step = self.state.current_step
        if self.state.status != "holding_gate" or not phase:
            return NextAction(kind="idle", message="No gate is currently holding.")
        self.state.gate_decisions.append(
            asdict_gate(GateDecision(step=f"{phase}.{step}", decision="denied",
                                     at=_now(), comment=comment)))
        self.state.pending_human.append(f"{phase}.{step} (denied: {comment})")
        self.state.status = "blocked"
        return NextAction(kind="gate", phase=phase, step=step,
                          message=f"Gate DENIED: {phase} → {step}. Release blocked. {comment}".strip())

    # ---- reporting ----
    def _phase_included(self, phase: dict) -> bool:
        return (not phase.get("conditional")) or phase["id"] in self._activated_conditionals()

    def _active_phase_report(self) -> Optional[dict]:
        """The first incomplete included phase, with its outstanding steps and
        whether its time-window is open (due). This is what the daily phase
        notification reports on — independent of state.current_phase (which is
        only set once the release has been advanced)."""
        for phase in self.config["phases"]:
            if not self._phase_included(phase):
                continue
            steps = phase["steps"]
            done = sum(1 for s in steps if self.state.is_done(phase["id"], s["id"]))
            if done == len(steps):
                continue                      # phase complete — look at the next one
            outstanding = [
                {"id": s["id"], "name": s["name"], "gate": bool(s.get("gate")),
                 "reminder": self._is_reminder(s), "owner": s.get("owner", "agent")}
                for s in steps if not self.state.is_done(phase["id"], s["id"])
            ]
            completed = [s["name"] for s in steps
                         if self.state.is_done(phase["id"], s["id"])]
            cur = self.state.current_step
            steps_view = []
            for s in steps:
                sid = s["id"]
                stp = self.state.get_step(phase["id"], sid)
                s_done = self.state.is_done(phase["id"], sid)
                s_blocked = stp.status == "blocked"
                is_gate = bool(s.get("gate"))
                is_rem = self._is_reminder(s)
                is_scout = s.get("source") == "scout"
                is_attest = bool(s.get("attest"))
                if s_done:
                    status = "done"
                elif s_blocked:
                    status = "blocked"
                elif is_gate:
                    status = "approval"
                elif is_attest:
                    status = "confirm"
                elif is_rem:
                    status = "action"          # a human to-do — the user must act
                elif is_scout:
                    status = "scout"           # Scout runs it automatically (scrape/send via MCP)
                else:
                    status = "auto"
                # needs_owner = a genuine USER task. A pending scout step is Scout's
                # automatic work (not the user's) until it BLOCKS (s_blocked), so it is
                # NOT flagged — only gates, reminders, attests, and blocks are.
                needs = bool((is_gate or is_rem or is_attest or s_blocked) and not s_done)
                steps_view.append({
                    "id": sid, "name": s["name"], "status": status,
                    "needs_owner": needs,
                    "note": stp.note,          # agent result / block reason / detail
                    "now": bool(sid == cur and not s_done and (is_gate or is_rem or is_attest or s_blocked)),
                })
            opens = self._phase_anchor_date(phase)
            return {
                "id": phase["id"], "name": phase["name"],
                "num": phase.get("checklist_phase"),
                "done": done, "total": len(steps),
                "due": self._phase_due(phase), "started": done > 0,
                "opens": opens.isoformat() if opens else None,
                "opens_in_days": (opens - self.as_of).days if opens else None,
                "outstanding": outstanding,
                "completed": completed,
                "steps": steps_view,
            }
        return None

    def _phase_map(self):
        """Build the phase overview + running totals. Returns
        (phases, total, done, current_phase_name, current_phase_obj, current_step_name)."""
        phases = []
        total = done = 0
        current_phase_name = current_step_name = None
        current_phase_obj = None
        for idx, phase in enumerate(self.config["phases"]):
            if not self._phase_included(phase):
                continue
            p_total = len(phase["steps"])
            p_done = sum(1 for s in phase["steps"] if self.state.is_done(phase["id"], s["id"]))
            total += p_total
            done += p_done
            is_current = self.state.current_phase == phase["id"]
            due = self._phase_due(phase)
            opens = self._phase_anchor_date(phase)
            if p_total and p_done == p_total:
                state = "done"
            elif not due and p_done == 0:
                state = "scheduled"
            elif is_current or p_done > 0:
                state = "current"
            else:
                state = "pending"
            if is_current:
                current_phase_name = phase["name"]
                current_phase_obj = phase
            phases.append({
                "id": phase["id"], "name": phase["name"],
                "num": phase.get("checklist_phase", idx),
                "done": p_done, "total": p_total, "state": state,
                "current": is_current,
                "anchor": phase.get("anchor"),
                "opens": opens.isoformat() if opens else None,
                "opens_in_days": (opens - self.as_of).days if opens else None,
            })
            for s in phase["steps"]:
                if s["id"] == self.state.current_step and phase["id"] == self.state.current_phase:
                    current_step_name = s["name"]
        return phases, total, done, current_phase_name, current_phase_obj, current_step_name

    def _current_steps(self, current_phase_obj) -> list:
        """The current phase's steps, each tagged with a display state."""
        if not current_phase_obj:
            return []
        phase_due = self._phase_due(current_phase_obj)
        out = []
        for s in current_phase_obj["steps"]:
            rec = self.state.steps.get(self.state.key(current_phase_obj["id"], s["id"]), {}) or {}
            is_scout = s.get("source") == "scout" and not s.get("attest")
            if rec.get("status") == "skipped":
                s_state = "skipped"
            elif self.state.is_done(current_phase_obj["id"], s["id"]):
                s_state = "done"
            elif rec.get("status") == "blocked":
                s_state = "blocked"          # a step hit a real problem — needs the owner
            elif s["id"] == self.state.current_step and self.state.status == "holding_gate":
                s_state = "gate"
            elif is_scout:
                s_state = "scout"            # Scout's automatic work — never a user "do this"
            elif s["id"] == self.state.current_step and self.state.status == "awaiting_action":
                s_state = "reminder"
            elif not phase_due:
                s_state = "scheduled"
            else:
                s_state = "pending"
            out.append({
                "id": s["id"], "name": s["name"],
                "gate": bool(s.get("gate")),
                "reminder": self._is_reminder(s),
                "owner": s.get("owner", "agent"),
                "state": s_state,
                "note": rec.get("note"),          # agent result / block reason / detail
                "links": rec.get("links") or [],  # durable refs (wiki page, CG alerts)
            })
        return out

    def _hold_view(self, phase_name, step_name) -> dict:
        """Detail of the current hold (gate or action-needed) — same shape for both."""
        return {
            "phase": self.state.current_phase,
            "phase_name": phase_name,
            "step": self.state.current_step,
            "step_name": step_name,
        }

    def _scheduled_view(self) -> Optional[dict]:
        """The phase we're waiting on the clock for — derived from the first
        incomplete phase's due-ness, so `status` shows it even before `next`."""
        first_incomplete = next(
            (p for p in self.config["phases"]
             if self._phase_included(p)
             and not all(self.state.is_done(p["id"], s["id"]) for s in p["steps"])),
            None)
        if (first_incomplete is None or self._phase_due(first_incomplete)
                or self.state.status in ("complete", "halted", "blocked")):
            return None
        opens = self._phase_anchor_date(first_incomplete)
        return {
            "phase": first_incomplete["id"],
            "phase_name": first_incomplete["name"],
            "opens": opens.isoformat() if opens else None,
            "opens_in_days": (opens - self.as_of).days if opens else None,
        }

    def status_report(self) -> dict:
        """Structured status — presentation layer (render.py) turns this into a view.
        Deterministic; no formatting baked in. Assembled from focused builders:
        phase map, current-phase steps, current hold, scheduled window, active phase."""
        (phases, total, done, current_phase_name,
         current_phase_obj, current_step_name) = self._phase_map()
        current_steps = self._current_steps(current_phase_obj)

        gate = action = None
        if self.state.status == "holding_gate" and self.state.current_phase:
            gate = self._hold_view(current_phase_name, current_step_name)
        elif self.state.status == "awaiting_action" and self.state.current_phase:
            # A scout step is the SKILL's work (run via step-action), not a USER action —
            # never surface it as `action` (which the digest reads as "Action needed now").
            phase = next((p for p in self.config["phases"]
                          if p["id"] == self.state.current_phase), None)
            cur = next((s for s in (phase or {}).get("steps", [])
                        if s["id"] == self.state.current_step), None) if self.state.current_step else None
            is_scout_focus = bool(cur and cur.get("source") == "scout" and not cur.get("attest"))
            cur_blocked = (self.state.get_step(self.state.current_phase, self.state.current_step).status
                           == "blocked") if self.state.current_step else False
            if not is_scout_focus or cur_blocked:
                action = self._hold_view(current_phase_name, current_step_name)
        scheduled = self._scheduled_view()

        chk = self.gate.checklist()
        active_phase = self._active_phase_report()
        # Scout steps ready for the SKILL to execute (perform the MCP send/scrape, then
        # record-step). They are NOT user holds — the skill drains these itself; only if
        # a scout step records 'attention' does it become a blocked user task.
        # GATED ON PHASE DUE: a phase that hasn't reached its anchor (e.g. Code Complete
        # Day before the CCD) must expose NO pending scout work — otherwise the autonomous
        # automation would drain those steps early, running CCD-day comms ahead of the CCD.
        scout_pending = ([s["id"] for s in (active_phase or {}).get("steps", [])
                          if s.get("status") == "scout"]
                         if (active_phase and active_phase.get("due")) else [])
        return {
            "release_id": self.state.release_id,
            "status": self.state.status,
            "owner_email": self.state.owner_email,
            "owner_name": self.state.owner_name,
            "ccd": self.state.ccd,
            "ccd_source": self.state.ccd_source,
            "ccd_conflict": self.state.ccd_conflict,
            "as_of": self.as_of.isoformat(),
            "skip_release": self.state.skip_release,
            "readiness_signed": self.state.readiness_signed,
            "readiness_pending": [i["id"] for i in chk["items"] if not i["satisfied"]],
            "blocked": self.state.blocked,
            "blocked_items": list(self.state.blocked_items),
            "blocked_message": chk.get("blocked_message", ""),
            "halted": self.state.halted,
            "halt_reason": self.state.halt_reason,
            "done": done, "total": total,
            "percent": round(100 * done / total) if total else 0,
            "phases": phases,
            "current_phase": self.state.current_phase,
            "current_phase_name": current_phase_name,
            "current_step": self.state.current_step,
            "current_step_name": current_step_name,
            "current_steps": current_steps,
            "gate": gate,
            "action": action,
            "scheduled": scheduled,
            "active_phase": active_phase,
            "scout_pending": scout_pending,
            "pending_human": list(self.state.pending_human),
            "gate_decisions": len(self.state.gate_decisions),
            "updated_at": self.state.updated_at,
        }


def asdict_gate(gd: GateDecision) -> dict:
    return {"step": gd.step, "decision": gd.decision, "at": gd.at,
            "by": gd.by, "comment": gd.comment}
