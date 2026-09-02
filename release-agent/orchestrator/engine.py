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
from datetime import date, datetime, time
from typing import Optional

import yaml

from .state import ReleaseState, StepState, GateDecision, _now
from .readiness import ReadinessGate
from . import schedule
from . import mocks as mocks_mod
from .status_views import StatusViewMixin
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


class Orchestrator(StatusViewMixin):
    """The conductor: owns the state machine, dispatch loop, gates, and structured
    status. The readiness entry gate is delegated to ReadinessGate (self.gate);
    presentation lives in render.py. This class holds no formatting logic."""

    def __init__(self, config_path: str, state: ReleaseState, readiness_path: str = None,
                 as_of: date = None, mocks: dict = None, tz=None, now: datetime = None):
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
        # The simulated clock, in the OWNER's timezone (not the host's — a UTC host must
        # not roll the date early). Precedence: an explicit tz arg → the tz captured on
        # the release at init (state.timezone) → config/schedule.yaml → DEFAULT_TZ.
        # `self.as_of` is the date used for phase due-ness; `self.now_local` is the
        # wall-clock time used to gate a step's fire_at_local.
        tz_name = getattr(state, "timezone", None) or self._config_timezone(config_path)
        self.tz = tz if tz is not None else schedule.get_tz(tz_name)
        if now is not None:                      # explicit datetime (precise tests / callers)
            self.now_local = now
            self.as_of = (as_of.date() if isinstance(as_of, datetime)
                          else as_of) or now.date()
        elif isinstance(as_of, datetime):        # a datetime passed as as_of
            self.now_local = as_of
            self.as_of = as_of.date()
        elif as_of is not None:                  # a bare DATE (debug clock / most tests):
            # keep it as the due-date, and treat the wall clock as end-of-day so a
            # date-only test still sees fire_at_local steps as past their fire time.
            self.as_of = as_of
            self.now_local = datetime.combine(as_of, time(23, 59, 59), self.tz)
        else:                                    # live: real now in the owner's zone
            self.now_local = schedule.now_local(self.tz)
            self.as_of = self.now_local.date()

    @staticmethod
    def _config_timezone(config_path: str) -> Optional[str]:
        """Read the release timezone from config/schedule.yaml (`timezone:`), or None
        (⇒ schedule.DEFAULT_TZ). Best-effort; never fails the engine."""
        try:
            p = os.path.join(os.path.dirname(config_path), "schedule.yaml")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as fh:
                    return (yaml.safe_load(fh) or {}).get("timezone")
        except (OSError, yaml.YAMLError):    # missing/unreadable/invalid schedule.yaml
            pass
        return None

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

    def _step_time_ready(self, phase: dict, step: dict) -> bool:
        """A step that declares a `fire_at_local` (e.g. the 09:00 CCD comms) is NOT
        runnable by the engine's automatic paths until that wall-clock time arrives, in
        the owner's timezone, on its fire day. This stops the every-hour worker from
        draining a timed step the instant its phase goes due — the step is left for its
        dedicated cron-pinned automation (which calls step-action directly and so isn't
        gated). Non-timed steps are always ready."""
        from orchestrator import automations
        fire = automations.fire_at(phase["id"], step["id"])
        if not fire:
            return True
        try:
            hh, mm = (int(x) for x in str(fire).split(":")[:2])
        except (ValueError, TypeError):
            return True                          # malformed fire_at_local ⇒ don't gate
        anchor = self._phase_anchor_date(phase)
        if anchor is not None:
            if self.as_of > anchor:              # past the fire day ⇒ run ASAP (catch-up)
                return True
            if self.as_of < anchor:              # before it (phase not due) ⇒ not ready
                return False
        return self.now_local.time() >= time(hh, mm)

    @staticmethod
    def _is_reminder(step: dict) -> bool:
        """A human, non-gate step is a reminder: the engine can't do it, so it
        holds and tells the person to do it, then waits for them to mark it done."""
        return step.get("owner") == "human" and not step.get("gate")

    # ---- state-machine traversal ----
    def _activated_conditionals(self) -> set:
        # A conditional phase (e.g. hotfix) is activated by an explicit note flag.
        return {n.split("activate:")[1].strip()
                for n in self.state.notes if isinstance(n, str) and n.startswith("activate:")}

    def activate_conditional(self, phase_id: str) -> None:
        self.state.notes.append(f"activate:{phase_id}")

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

    def current_phase_id(self) -> Optional[str]:
        """Public: id of the first included phase with incomplete steps, or None when
        the release is complete. The engine's authoritative 'where are we' — derived
        from config order + the done-map, not the cached cursor. Used by the simulator
        to know when a fast-forward has reached a target phase."""
        p = self._current_phase()
        return p["id"] if p else None

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

        # TIME GATE (within the day): a step with a fire_at_local isn't runnable until
        # its wall-clock time — hold as scheduled so the every-hour worker doesn't fire
        # it early; its dedicated cron automation runs it at the pinned time.
        if not self._step_time_ready(phase, step):
            from orchestrator import automations
            fire = automations.fire_at(phase["id"], step["id"])
            self.state.status = "scheduled"
            return NextAction(
                kind="scheduled", phase=phase["id"], step=step["id"], name=step["name"],
                message=f"{phase['name']} → {step['name']} is scheduled for {fire} "
                        f"(fires via its timed automation). Nothing to do yet.")

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
            return ((not self.state.is_done(pid, s["id"])) and self._deps_met(pid, s)
                    and self._step_time_ready(phase, s))

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
        # IN-FLIGHT: the step's underlying pipeline run is still executing — NOT a failure.
        # Hold the phase as 'waiting on the pipeline' (no user action) and let the poller /
        # tick re-run the step until the run completes. Stamp when we first saw it in-flight
        # so a poller can send the 6h courtesy nudge.
        if getattr(result, "in_flight", False):
            prev = self.state.get_step(pid, step["id"])
            data = dict(getattr(prev, "data", {}) or {})
            data.setdefault("in_flight_since", _now())
            data["poll_in_min"] = getattr(result, "poll_in_min", 30)
            self.state.set_step(pid, step["id"],
                                StepState(status="in_flight", note=result.action, by="agent",
                                          links=list(getattr(result, "links", None) or []),
                                          data=data))
            self.state.pending_human = [p for p in self.state.pending_human if p != key]
            self.state.status = "running"
            return NextAction(kind="waiting", phase=pid, step=step["id"], name=step["name"],
                              message=f"WAITING — {step['name']}: {result.action}")
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
            if act.kind in ("gate", "reminder", "scheduled", "complete", "readiness", "blocked", "halted", "waiting"):
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

    # `_phase_included` is a shared helper (used by both the state machine and the status
    # views mixin). The status view-model builders live in orchestrator/status_views.py.
    def _phase_included(self, phase: dict) -> bool:
        return (not phase.get("conditional")) or phase["id"] in self._activated_conditionals()


def asdict_gate(gd: GateDecision) -> dict:
    return {"step": gd.step, "decision": gd.decision, "at": gd.at,
            "by": gd.by, "comment": gd.comment}
