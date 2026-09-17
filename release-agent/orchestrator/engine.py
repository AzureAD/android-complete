"""Release Orchestrator — the conductor (deterministic engine, X4).

Responsibilities (per §7.1):
  1. Compile config/phases.yaml and bind its validated handler catalog.
  2. Own the dispatch loop: find next step -> invoke its bound handler ->
     record result -> advance, or HOLD at a gate for human approval.
  3. Persist run-state via ReleaseState (X5).

The engine is the BRAIN: it decides what's next. The skill is only the mouth/ears.
No LLM logic here — this is fully unit-testable and replayable.
"""
from __future__ import annotations
import hashlib
import os
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Iterable, Optional

import yaml

from .state import ReleaseState, _now
from .context_boundary import EvidenceSession, evidence_view, release_view
from .step_context import Clock, EffectContext, StepContext, freeze, thaw
from .handler_contracts import HookRole
from .evidence import RetryDecision
from .outcomes import AutoOutcome, Done, Blocked, InProgress, NeedsSkill, require_auto_outcome
from .invariants import validate_snapshot
from . import effects
from .effects import EffectMode, EffectRecovery
from .handlers import HandlerCatalog, HandlerResolver, StepHandler
from .projection import SchedulingResult, StateProjection
from .readiness import ReadinessGate
from .revision import active_revision_provider, operation_cache, revision_checked
from . import schedule
from . import mocks as mocks_mod
from .status_views import StatusViewMixin
from .transitions import OutcomePermit, TransitionIntent, TransitionKernel, TransitionResult
from .workflow import (
    StepKind,
    WorkflowDefinition,
    workflow_fingerprint,
)
import steps


@dataclass
class NextAction:
    """What the conductor decided on this invocation — the engine's output."""
    kind: str                 # presentation category; drain control is separate
    phase: Optional[str] = None
    step: Optional[str] = None
    name: Optional[str] = None
    message: str = ""
    continue_drain: bool = False


class Orchestrator(StatusViewMixin):
    """The conductor: owns the state machine, dispatch loop, gates, and structured
    status. The readiness entry gate is delegated to ReadinessGate (self.gate);
    presentation lives in render.py. This class holds no formatting logic."""

    def __init__(self, config_path: str, state: ReleaseState, readiness_path: str = None,
                 as_of: date = None, mocks: dict = None, tz=None, now: datetime = None,
                 *, handler_resolver: HandlerResolver | None = None, services=None,
                 effect_services=None, clock=None, new_id=None, revision_provider=None):
        self.config_path = os.path.abspath(config_path)
        with open(config_path, "rb") as fh:
            config_bytes = fh.read()
        self._config_file_hash = hashlib.sha256(config_bytes).digest()
        self.config = yaml.safe_load(config_bytes)
        self._config_file_fingerprint = workflow_fingerprint(self.config)
        self.workflow = WorkflowDefinition.compile(self.config)
        self._handler_resolver = handler_resolver if handler_resolver is not None else steps.get_step
        self.handlers = HandlerCatalog.compile(self.workflow, self._handler_resolver)
        readiness_cfg = None
        if readiness_path is None:
            readiness_path = os.path.join(os.path.dirname(config_path), "readiness.yaml")
        if os.path.exists(readiness_path):
            with open(readiness_path, "r", encoding="utf-8") as fh:
                readiness_cfg = yaml.safe_load(fh)
        self.readiness_path = os.path.abspath(readiness_path)
        self._revision_provider = (
            revision_provider if revision_provider is not None
            else active_revision_provider()
        )
        if not callable(getattr(self._revision_provider, "identity", None)):
            raise TypeError("Revision provider must define identity()")
        self._loaded_runtime_hash, _ = self._revision_provider.identity(
            config_path=self.config_path, readiness_path=self.readiness_path)
        self.state = state
        self._services = services
        self._effect_services = effect_services
        self._clock = clock
        self._new_id = new_id
        self._evidence_sessions = {}
        self._approval_results = {}
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
        if self.tz is not None:
            self.now_local = (self.now_local.astimezone(self.tz) if self.now_local.tzinfo
                              else self.now_local.replace(tzinfo=self.tz))
            if isinstance(as_of, datetime) or (as_of is None and now is not None):
                self.as_of = self.now_local.date()

    def _workflow_definition(self) -> WorkflowDefinition:
        """Read once per operation and recompile only when config content changes."""
        cache = operation_cache(self)
        if cache is not None and "workflow" in cache:
            cached = cache["workflow"]
            if workflow_fingerprint(self.config) == cached.fingerprint:
                return cached
        with open(self.config_path, "rb") as fh:
            config_bytes = fh.read()
        config_hash = hashlib.sha256(config_bytes).digest()
        if config_hash != self._config_file_hash:
            disk_config = yaml.safe_load(config_bytes)
            disk_fingerprint = workflow_fingerprint(disk_config)
            if disk_fingerprint != self._config_file_fingerprint:
                self.config = disk_config
                self._config_file_fingerprint = disk_fingerprint
            self._config_file_hash = config_hash
        if workflow_fingerprint(self.config) != self.workflow.fingerprint:
            workflow = WorkflowDefinition.compile(self.config)
            handlers = HandlerCatalog.compile(workflow, self._handler_resolver)
            self.workflow, self.handlers = workflow, handlers
        if cache is not None:
            cache["workflow"] = self.workflow
        return self.workflow

    def handler(self, phase_id: str, step_id: str) -> StepHandler:
        """Return the bound handler for a configured step."""
        self._workflow_definition()
        return self.handlers.get(phase_id, step_id)

    def context(self, phase_id, step_id, *, permit=None, parameters=None,
                inputs=None, role=HookRole.BUILD, execution_id=None) -> StepContext:
        """Build one immutable invocation. Writers require current engine authority."""
        from uuid import uuid4
        from . import service_adapters

        handler = self.handler(phase_id, step_id)
        parameters = handler.parse_parameters(role, parameters)
        role = HookRole(role)
        effect = role in (HookRole.EXECUTE, HookRole.RECONCILE)
        approval = role in (HookRole.APPROVAL, HookRole.APPROVAL_RECONCILE)
        clock = self._clock or Clock(self.now_local)
        services = self._services
        if services is None:
            status_email = None
            if handler.status_email:
                from . import status_email as SE
                from . import notifications
                snapshot = freeze(SE.compose(
                    self.state, [p.id for p in self.workflow.phases], [],
                    selection=self.scheduling()))

                def status_email(recipients, *, changes):
                    result = thaw(snapshot)
                    result["to"] = list(recipients)
                    result["model"]["changes"] = thaw(changes)
                    result["html"] = SE.render_html(result["model"])
                    return result

                def status_recipients():
                    cfg = notifications.load_config(os.path.join(
                        os.path.dirname(__file__), "..", "config", "phases.yaml")) or {}
                    recipients = (cfg.get("status_email") or {}).get("recipients")
                    if not isinstance(recipients, list) or not recipients:
                        raise ValueError("Configure status_email.recipients as a non-empty list")
                    return recipients
            else:
                status_recipients = None
            services = service_adapters.production_services(
                status_email=status_email, status_recipients=status_recipients)
        session = None
        if permit is not None and not approval:
            self.validate_outcome_permit(permit)
            if (permit.phase, permit.step) != (phase_id, step_id):
                raise ValueError("Outcome permit targets another handler")
            session = self._evidence_sessions.get(permit)
            if session is None:
                session = EvidenceSession(self.state, permit, self.validate_evidence_permit,
                                          authority=handler.evidence, durable=effect)
                self._evidence_sessions[permit] = session
            elif effect:
                session.enable_durable()
        effect_context = None
        if effect:
            execution = self.step_execution(phase_id, step_id)
            if (session is None or permit.intent != TransitionIntent.EFFECT
                    or not execution.get("id") or handler.effect is None
                    or not effects.execution_input_is_valid(step_id, execution)):
                raise ValueError("Durable effects require a valid owned execution permit")
            writers = (
                self._effect_services(handler.writes, lambda: self.validate_outcome_permit(permit), session, clock)
                if self._effect_services else service_adapters.production_effects(
                    handler.writes, validate=lambda: self.validate_outcome_permit(permit),
                    committer=session, clock=clock))
            effect_context = EffectContext(
                freeze(execution), session.committer(), handler.writes.validate_services(writers))
        approval_writer = None
        if approval:
            from .approvals import ApprovalPermit, request_from_dict
            from .step_context import ApprovalContext
            kernel = self._transition_kernel()
            if role == HookRole.APPROVAL:
                if (not isinstance(permit, ApprovalPermit)
                        or (permit.phase, permit.step) != (phase_id, step_id)):
                    raise ValueError("Submission requires an exact live approval permit")
                if rejected := kernel.validate_approval_permit(permit):
                    raise ValueError(rejected.message)
                execution_id = permit.execution_id
            elif permit is not None:
                raise ValueError("Approval reconciliation cannot carry a submission permit")
            if rejected := kernel.validate_approval_owner(phase_id, step_id, execution_id):
                raise ValueError(rejected.message)
            record = self.state.get_step(phase_id, step_id)
            generation = kernel._generation(record)
            request = request_from_dict(record.execution["approval"]["request"])
            submit = None
            if role == HookRole.APPROVAL:
                def validate_approval():
                    if rejected := kernel.validate_approval_owner(phase_id, step_id, execution_id, active=True):
                        raise ValueError(rejected.message)
                    if generation != kernel._generation(self.state.get_step(phase_id, step_id)):
                        raise ValueError("Approval invocation is no longer authorized")

                approval_services = (
                    self._effect_services(handler.writes, validate_approval, None, clock)
                    if self._effect_services else service_adapters.production_effects(
                        handler.writes, validate=validate_approval, committer=None, clock=clock))
                provider = handler.writes.validate_services(approval_services).submit_pipeline_approval

                def submit():
                    kernel.consume_approval_permit(permit)
                    result = provider(request.org, request.project, request.approval_id, request.comment)
                    from .handler_contracts import validate_result
                    validate_result(result, HookRole.APPROVAL)
                    self._approval_results[permit] = result[0]
                    return result
            approval_writer = ApprovalContext(execution_id, request, submit)
        return StepContext(
            release_view(self.state), evidence_view(self.state), clock, services,
            parameters,
            freeze(inputs if inputs is not None else self.mocks.get(f"{phase_id}.{step_id}", {})),
            effect_context, self._new_id or (lambda: uuid4().hex), approval_writer,
            role, handler.definition.key,
        )

    def apply_evidence(self, permit, outcome, *, checkpoint=False):
        updates = outcome.updates
        if updates:
            self.validate_evidence_permit(permit)
            session = self._evidence_sessions.get(permit)
            if session is None:
                raise ValueError("Evidence requires an invocation context")
            session.apply(updates, checkpoint=checkpoint)

    @revision_checked
    def preview_gate_approval(self, phase_id, step_id, *, comment=""):
        from .approvals import preview
        return preview(self, phase_id, step_id, comment=comment)

    @revision_checked
    def execute_gate_approval(self, phase_id, step_id, **authorization):
        from .approvals import execute
        return execute(self, phase_id, step_id, **authorization)

    def _projection(self) -> StateProjection:
        from .revision import mismatch_reason
        workflow = self._workflow_definition()
        handlers = self.handlers
        return StateProjection(
            self.state,
            workflow,
            self.as_of,
            self.now_local,
            readiness_signed=self.gate.signed,
            readiness_blocked=self.gate.blocked,
            is_mocked=lambda step: self._is_mocked(step.phase_id, step.raw),
            fire_at=lambda step: handlers.get(step.phase_id, step.id).fire_at_local,
            revision_problem=mismatch_reason(self),
        )

    def _transition_kernel(self) -> TransitionKernel:
        workflow = self._workflow_definition()
        kernel = getattr(self, "_kernel", None)
        if kernel is None or kernel.workflow is not workflow:
            self._kernel = TransitionKernel(self.state, workflow, self._projection, _now)
        return self._kernel

    @revision_checked
    def scheduling(self, attempted: Iterable[str] = ()) -> SchedulingResult:
        """Public, pure scheduling query shared by dispatch and presentation."""
        return self._projection().scheduling(attempted=attempted)

    def _next_from_transition(self, result) -> NextAction:
        definition = (
            self._workflow_definition().step(result.phase, result.step)
            if result.phase and result.step
            else None
        )
        return NextAction(
            kind=result.kind,
            phase=result.phase,
            step=result.step,
            name=definition.name if definition else None,
            message=result.message,
            continue_drain=result.changed and result.kind == "ran" and not self.scheduling().suspension,
        )

    def _step_complete(self, phase_id: str, step_id: str) -> bool:
        step = self._workflow_definition().step(phase_id, step_id)
        return bool(step and self._projection().step_complete(step))

    def invariant_violations(self):
        return validate_snapshot(
            self.state, self._workflow_definition()
        )

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
        definition = self._workflow_definition().phase(phase["id"])
        return self._projection().phase_anchor_date(definition) if definition else None

    def _phase_due(self, phase: dict) -> bool:
        """A phase is due once the clock reaches its anchor. No anchor ⇒ always due."""
        definition = self._workflow_definition().phase(phase["id"])
        return bool(definition and self._projection().phase_due(definition))

    def _step_time_ready(self, phase: dict, step: dict) -> bool:
        """A step that declares a `fire_at_local` (e.g. the 09:00 CCD comms) is NOT
        runnable by the engine's automatic paths until that wall-clock time arrives, in
        the owner's timezone, on its fire day. This stops the every-hour worker from
        draining a timed step the instant its phase goes due — the step is left for its
        dedicated cron-pinned automation. Direct step-action enforces the same time
        boundary. Non-timed steps are always ready."""
        definition = self._workflow_definition().step(phase["id"], step["id"])
        return bool(definition and self._projection().step_time_ready(definition))

    @staticmethod
    def _is_reminder(step: dict) -> bool:
        """A human, non-gate step is a reminder: the engine can't do it, so it
        holds and tells the person to do it, then waits for them to mark it done."""
        return step.get("kind") in (
            StepKind.HUMAN_ACTION.value,
            StepKind.ATTESTATION.value,
        )

    # ---- state-machine traversal ----
    def _activated_conditionals(self) -> set:
        # A conditional phase (e.g. hotfix) is activated by an explicit note flag.
        return self._projection().activated_conditionals()

    @revision_checked
    def activate_conditional(self, phase_id: str) -> NextAction:
        return self._next_from_transition(
            self._transition_kernel().activate(phase_id)
        )

    # ---- dispatch ----
    def _current_phase(self):
        """The first included phase that still has incomplete steps (definition
        order). Conditional phases are skipped unless activated."""
        phase = self.scheduling().frontier
        return phase.raw if phase else None

    @revision_checked
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
        return {
            StepKind.APPROVAL_GATE.value: "gate",
            StepKind.EXTERNAL.value: "scout",
            StepKind.ATTESTATION.value: "attest",
            StepKind.HUMAN_ACTION.value: "reminder",
            StepKind.AUTO.value: "auto",
        }[step["kind"]]

    def _is_mocked(self, pid: str, step: dict) -> bool:
        """True if a local mock replaces this step. Gate steps are never mockable
        (a gate needs a real human decision)."""
        return ((step.get("kind") != StepKind.APPROVAL_GATE.value)
                and mocks_mod.outcome_for(self.mocks, pid, step["id"]) is not None)


    def _prerequisites_met(self, phase: dict, step: dict) -> bool:
        """Parallel phases use explicit dependencies; sequential phases also require predecessors."""
        workflow = self._workflow_definition()
        phase_def = workflow.phase(phase["id"])
        step_def = workflow.step(phase["id"], step["id"])
        return bool(
            phase_def
            and step_def
            and self._projection().prerequisites_met(phase_def, step_def)
        )

    @revision_checked
    def step_once(self, attempted: set[str] | None = None) -> NextAction:
        """Advance exactly one step (or hold). For a sequential phase this is the
        classic first-incomplete-step logic. For a parallel phase it runs one ready
        step whose dependencies are met, letting independent steps progress even
        when a sibling is holding. `attempted` (a set, managed by run_until_gate)
        prevents re-running an auto step twice within one drain. The returned
        continue_drain distinguishes step-local waits from a settled phase hold."""
        from .revision import mismatch_reason
        revision_problem = mismatch_reason(self)
        if revision_problem:
            return NextAction(kind="blocked", message=revision_problem)
        errors = [
            violation for violation in self.invariant_violations()
            if violation.severity == "error"
        ]
        if errors:
            return NextAction(
                kind="blocked",
                message="Invalid release state: " + "; ".join(
                    violation.message for violation in errors
                ),
            )
        selection = self.scheduling(attempted=attempted or ())
        projected_status = selection.status
        if projected_status == "cancelled":
            return NextAction(
                kind="cancelled",
                message="Release is skipped/cancelled; clear skip-release before continuing.",
            )
        if projected_status == "complete":
            return NextAction(kind="complete", message="Release already complete.")

        if projected_status == "halted":
            return NextAction(
                kind="halted",
                message="Release is HALTED"
                        + (f": {self.state.halt.get('reason')}"
                           if self.state.halt else "")
                        + ". Run resume to continue.",
            )

        if selection.suspension == "blocked":
            labels = self.gate.blocked_labels()
            msg = (self.gate.config or {}).get("blocked_message", "").strip()
            return NextAction(
                kind="blocked",
                message="Entry gate blocked — cannot start: " + ", ".join(labels) + ". " + msg,
            )

        if selection.suspension == "readiness_gate":
            return NextAction(
                kind="readiness",
                message="HOLDING at the readiness entry gate. Sign the checklist before Phase 0 can start.",
            )

        hold = selection.focus_hold
        if selection.suspension == "denied" and hold:
            return NextAction(
                kind="blocked",
                phase=hold.phase_id,
                step=hold.step_id,
                message=f"Gate denied — {hold.reason} Reopen the gate with a reason to reconsider.",
            )

        phase = selection.frontier
        if selection.runnable:
            candidate = selection.runnable[0]
            if attempted is not None:
                attempted.add(candidate.step.key)
            action = self._run_auto_step(
                phase.raw, candidate.step.raw,
                block_holds=phase.execution != "parallel",
            )
            # A step-local wait or recovery hold must not starve its siblings.
            return (
                replace(action, continue_drain=True)
                if phase.execution == "parallel" and not self.scheduling().suspension else action
            )
        return self._scheduled_hold_action(selection)

    def _scheduled_hold_action(self, selection: SchedulingResult) -> NextAction:
        """Render the shared selection; no readiness or hold-priority policy here."""
        phase = selection.frontier
        hold = selection.focus_hold
        step = phase.step(hold.step_id) if hold and hold.step_id else None
        action = NextAction(
            kind="waiting", phase=phase.id,
            step=step.id if step else None, name=step.name if step else None,
        )
        execution = self.step_execution(phase.id, step.id) if step else {}
        if execution.get("approval"):
            action.message = (
                f"External gate approval {execution['id']} retains ownership. Use "
                f"{step.approval_command} --release {self.state.release_id} "
                f"--execution-id {execution['id']} to reconcile. "
                "An unattempted reservation also requires its original review hash/reviewer. "
                "Never resend an attempted approval or clear ownership with done/reopen."
            )
            return action
        if not hold or hold.kind == "waiting":
            action.message = (
                "Step execution is reserved; do not run it again."
                if hold and hold.reason == "reservation" else
                "Ready work has already been attempted in this drain."
                if hold and hold.reason == "attempted" else
                "Waiting on prerequisite steps to complete."
            )
        elif hold.kind == "scheduled":
            action.kind = "scheduled"
            readiness = selection.phase(phase.id)
            if not readiness.due:
                opens = readiness.opens
                days = (opens - self.as_of).days
                action.message = (
                    f"{phase.name} opens {opens.isoformat()} "
                    f"({schedule.humanize_delta(days)}). Nothing to do yet."
                )
            else:
                fire = self.handler(phase.id, step.id).fire_at_local
                action.message = (
                    f"{phase.name} → {step.name} is scheduled for {fire} "
                    "(fires via its timed automation). Nothing to do yet."
                )
        elif hold.kind == "in_flight":
            record = self.state.get_step(phase.id, step.id)
            action.message = (
                f"WAITING — {step.name}: "
                f"{record.note or 'Underlying work is still running.'}"
            )
        elif hold.kind == "gate":
            action.kind = "gate"
            action.message = (
                f"HOLDING at gate: {phase.name} → {step.name}. Awaiting human decision."
            )
        else:
            action.kind = "reminder"
            if phase.execution == "parallel":
                holds = [
                    item for item in selection.action_holds
                    if item.kind in ("action", "scout")
                ]
                names = "; ".join(phase.step(item.step_id).name for item in holds)
                action.message = f"{len(holds)} item(s) need attention: {names}"
            elif step.kind == StepKind.ATTESTATION:
                action.message = (
                    f"CONFIRM — attest that this is done to proceed: {step.name}. "
                    "Mark it done once you've verified it."
                )
            elif hold.kind == "scout":
                action.message = (
                    f"Scout-assisted check pending — {step.name}. "
                    "Scout runs this automatically when you open it."
                )
            else:
                action.message = (
                    f"ACTION NEEDED — you need to: {step.name}. Mark it done when complete."
                )
        return action

    def _run_auto_step(self, phase: dict, step: dict, block_holds: bool) -> NextAction:
        """Run an agent step. On success → done. On failure: in sequential mode
        (block_holds=True) HOLD as action-needed and break; in parallel mode
        (block_holds=False) mark it blocked + register it, but return 'ran' so the
        drain continues with independent steps."""
        pid = phase["id"]
        handler = self.handler(pid, step["id"])
        definition = handler.definition
        active_effect = bool(
            definition
            and definition.effect_mode
            and definition.effect_mode.writes_external_state
            and (self.state.get_step(pid, step["id"]).execution or {}).get(
                "effect_mode"
            )
        )
        # A local mock short-circuits the real handler, returning the
        # declared outcome. Active effects always recover first; a later-added
        # outcome mock must never erase uncertain provider ownership.
        outcome: AutoOutcome | None = (
            None
            if active_effect
            else mocks_mod.outcome_for(self.mocks, pid, step["id"])
        )
        kernel = self._transition_kernel()
        permit = kernel.authorize_outcome(
            TransitionIntent.EFFECT if active_effect else (
                TransitionIntent.MOCK if outcome is not None else TransitionIntent.EXECUTE),
            pid, step["id"],
            execution_id=self.step_execution(pid, step["id"]).get("id") if active_effect else None,
        )
        if isinstance(permit, TransitionResult):
            return self._next_from_transition(permit)
        if outcome is None:
            effect = handler.effect
            if effect is not None:
                execution = self.step_execution(pid, step["id"])
                recovering = bool(execution)
                if not recovering:
                    prepared = effects.prepare(
                        effect,
                        self.context(pid, step["id"], permit=permit, role=HookRole.PREPARE),
                        self.mocks.get(f"{pid}.{step['id']}", {}),
                    )
                    if isinstance(prepared, Blocked):
                        outcome = prepared
                        execution = {}
                    else:
                        previous = self.state.get_step(pid, step["id"])
                        transition = kernel.begin_effect(
                            permit,
                            effect_mode=effect.mode,
                            operation_key=prepared["operation_key"],
                            effect_input=prepared["input"],
                        )
                        if not transition.changed:
                            return self._next_from_transition(transition)
                        self._evidence_sessions.pop(permit, None)
                        try:
                            self.state.checkpoint()
                        except Exception:
                            self.state.set_step(pid, step["id"], previous)
                            raise
                        execution = self.step_execution(pid, step["id"])
                        permit = kernel.authorize_outcome(
                            TransitionIntent.EFFECT, pid, step["id"],
                            execution_id=execution["id"],
                        )
                        if isinstance(permit, TransitionResult):
                            return self._next_from_transition(permit)
                else:
                    if not effects.execution_input_is_valid(
                        step["id"], execution
                    ):
                        return self._next_from_transition(
                            self._transition_kernel().hold_effect(
                                pid,
                                step["id"],
                                "Frozen effect input failed its operation-key "
                                "integrity check. Ownership is preserved for review.",
                            )
                        )
                    if effect.recovery == EffectRecovery.MATCH_CURRENT:
                        frozen_mocks = (execution.get("effect_input") or {}).get(
                            "__effect_mocks__", {}
                        )
                        current = effects.prepare(
                            effect, self.context(pid, step["id"], permit=permit,
                                                 inputs=frozen_mocks, role=HookRole.PREPARE), frozen_mocks
                        )
                        if isinstance(current, Blocked):
                            return self._next_from_transition(
                                self._transition_kernel().hold_effect(
                                    pid,
                                    step["id"],
                                    "Effect inputs cannot currently be verified. "
                                    f"{current.reason}",
                                )
                            )
                        if execution.get("operation_key") != current["operation_key"]:
                            return self._next_from_transition(
                                self._transition_kernel().hold_effect(
                                    pid,
                                    step["id"],
                                    "Effect inputs changed after execution started. "
                                    "The previous operation remains reserved; owner "
                                    "review is required.",
                                )
                            )
                if execution:
                    rejected = kernel.validate_outcome_permit(permit)
                    if rejected:
                        return self._next_from_transition(rejected)
                    effect_mocks = (
                        (execution.get("effect_input") or {}).get(
                            "__effect_mocks__", {}
                        )
                    )
                    outcome = effects.invoke(
                        effect,
                        self.context(pid, step["id"], permit=permit,
                                     inputs=effect_mocks, role=(
                                         HookRole.RECONCILE if recovering and effect.mode == EffectMode.TRANSACTIONAL
                                         else HookRole.EXECUTE)),
                        recovering=recovering,
                    )
            else:
                # Input mocks exercise the bound handler's real build logic.
                outcome = handler.build(self.context(pid, step["id"], permit=permit))
        outcome = require_auto_outcome(outcome)
        rejected = kernel.validate_outcome_application(permit, outcome)
        if rejected:
            return self._next_from_transition(rejected)
        self.apply_evidence(permit, outcome)
        transition = kernel.apply_outcome(permit, outcome, block_holds=block_holds)
        if transition.changed:
            self._evidence_sessions.pop(permit, None)
        return self._next_from_transition(transition)

    def run_until_gate(self, max_steps: int = 500) -> list[NextAction]:
        """Drain independent work until a settled hold, completion, or step cap.

        Step-local parallel waits retain their kind but allow another selection.
        Each handler, including effect recovery, is attempted once per
        drain. A later invocation can poll/reconcile it again.
        """
        actions = []
        attempted = set()
        for _ in range(max_steps):
            act = self.step_once(attempted)
            actions.append(act)
            if not act.continue_drain:
                break
        return actions

    # ---- external step execution (caller holds the release transaction lock) ----
    def step_execution(self, phase_id: str, step_id: str) -> dict:
        value = self.state.get_step(phase_id, step_id).execution
        return dict(value) if isinstance(value, dict) else {}

    @revision_checked
    def authorize_outcome(
        self, intent: TransitionIntent, phase_id: str, step_id: str, *,
        execution_id: str | None = None,
    ) -> OutcomePermit:
        """Capture permission before invoking an observation, prepare, poll or refresh."""
        from .revision import assert_current
        assert_current(self)
        result = self._transition_kernel().authorize_outcome(
            intent, phase_id, step_id, execution_id=execution_id,
        )
        if isinstance(result, TransitionResult):
            raise ValueError(result.message)
        return result

    @revision_checked
    def apply_outcome(
        self, permit: OutcomePermit, outcome: AutoOutcome, *, data: dict | None = None,
    ) -> NextAction:
        outcome = require_auto_outcome(outcome)
        if data is not None and not isinstance(data, dict):
            raise TypeError("Outcome data must be a dictionary")
        self.validate_evidence_permit(permit)
        if permit.intent == TransitionIntent.RECOVER_EVIDENCE:
            raise ValueError("Evidence recovery cannot apply a lifecycle outcome")
        self.validate_outcome_application(permit, outcome, data=data)
        self.apply_evidence(permit, outcome)
        result = self._transition_kernel().apply_outcome(permit, outcome, data=data)
        if not result.changed:
            raise ValueError(result.message)
        self._evidence_sessions.pop(permit, None)
        return self._next_from_transition(result)

    @revision_checked
    def validate_outcome_application(
        self, permit: OutcomePermit, outcome: AutoOutcome, *, data: dict | None = None,
    ) -> None:
        """Check a proposed result without changing evidence, ownership or its permit."""
        rejected = self._transition_kernel().validate_outcome_application(permit, outcome, data=data)
        if rejected:
            raise ValueError(rejected.message)

    @revision_checked
    def validate_outcome_permit(self, permit: OutcomePermit) -> None:
        """Revalidate a prepared action before offering or invoking further work."""
        from .revision import assert_current
        assert_current(self)
        rejected = self._transition_kernel().validate_outcome_permit(permit)
        if rejected:
            raise ValueError(rejected.message)

    @revision_checked
    def validate_evidence_permit(self, permit: OutcomePermit) -> None:
        from .revision import assert_current
        assert_current(self)
        rejected = self._transition_kernel().validate_evidence_permit(permit)
        if rejected:
            raise ValueError(rejected.message)

    @revision_checked
    def settle_execution(
        self, phase_id: str, step_id: str, execution_id: str, outcome: AutoOutcome,
        *, data: dict | None = None,
    ) -> NextAction:
        """Settle an existing owner's receipt; this does not authorize new work."""
        from .revision import assert_current
        assert_current(self)
        result = self._transition_kernel().settle_execution(
            phase_id, step_id, execution_id, outcome, data=data,
        )
        if not result.changed:
            raise ValueError(result.message)
        return self._next_from_transition(result)

    @revision_checked
    def step_action_intent(self, phase_id: str, step_id: str) -> TransitionIntent:
        if rejected := self._transition_kernel()._invalid():
            raise ValueError(rejected.message)
        definition = self._workflow_definition().step(phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        if definition and definition.pollable and record.status == "in_flight":
            return TransitionIntent.POLL
        if definition and definition.repeatable and self._step_complete(phase_id, step_id):
            return TransitionIntent.REFRESH
        return TransitionIntent.PREPARE

    @revision_checked
    def omit_execution(
        self, permit: OutcomePermit, reason: str, *, links: list | None = None,
    ) -> NextAction:
        result = self._transition_kernel().omit_execution(permit, reason, links=links)
        if not result.changed:
            raise ValueError(result.message)
        return self._next_from_transition(result)

    @revision_checked
    def step_action_guard(self, phase_id: str, step_id: str):
        if rejected := self._transition_kernel()._invalid():
            return Blocked(rejected.message)
        definition = self._workflow_definition().step(phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        if definition and definition.pollable and record.status == "in_flight":
            check = self._transition_kernel().eligibility().evaluate(
                TransitionIntent.POLL, phase_id, step_id
            )
            return None if check.allowed else Blocked(check.reason)
        completed = self.completed_step_outcome(phase_id, step_id)
        if completed is not None and definition and definition.repeatable:
            check = self._transition_kernel().eligibility().evaluate(
                TransitionIntent.REFRESH, phase_id, step_id
            )
            return None if check.allowed else Blocked(check.reason)
        if completed is not None:
            return completed
        check = self._transition_kernel().eligibility().evaluate(
            TransitionIntent.PREPARE, phase_id, step_id
        )
        return None if check.allowed else Blocked(check.reason)

    @staticmethod
    def supports_step_reservation(outcome) -> bool:
        """Every non-notification outbound action requires a durable reservation."""
        return isinstance(outcome, NeedsSkill) and outcome.outbound

    @revision_checked
    def reserve_step(self, phase_id: str, step_id: str, outcome, executor: str):
        from .revision import assert_current
        assert_current(self)
        if not isinstance(outcome, NeedsSkill):
            return outcome
        eligibility = self._transition_kernel().eligibility().evaluate(
            TransitionIntent.RESERVE, phase_id, step_id
        )
        if not eligibility.allowed:
            return Blocked(eligibility.reason)
        if not self.supports_step_reservation(outcome) or outcome.record_as != step_id:
            return Blocked("Reservation requires a standard action completed through record-step.")
        result = self._transition_kernel().reserve(phase_id, step_id, executor)
        return outcome if result.changed else Blocked(result.message)

    @revision_checked
    def reserve_execution(self, phase_id: str, step_id: str, executor: str) -> TransitionResult:
        from .revision import assert_current
        assert_current(self)
        return self._transition_kernel().reserve(phase_id, step_id, executor)

    @revision_checked
    def annotate_step(self, phase_id: str, step_id: str, *, data: dict | None = None,
                      links: list | None = None, note: str | None = None,
                      by: str | None = None) -> TransitionResult:
        return self._transition_kernel().annotate_step(
            phase_id, step_id, data=data, links=links, note=note, by=by)

    @revision_checked
    def reopen(self, phase_id: str, step_id: str, reason: str = "") -> TransitionResult:
        return self._transition_kernel().reopen(phase_id, step_id, reason)

    @revision_checked
    def cancel(self, reason: str) -> TransitionResult:
        return self._transition_kernel().cancel(reason)

    @revision_checked
    def reactivate(self, reason: str) -> TransitionResult:
        return self._transition_kernel().reactivate(reason)

    @revision_checked
    def retry_effect(self, phase_id: str, step_id: str, execution_id: str, reason: str,
                     *, confirm_absent: bool = False) -> TransitionResult:
        kernel = self._transition_kernel()
        if confirm_absent is not True:
            return kernel._reject("Review the provider first, then pass --confirm-absent.")
        generation = kernel._prepare_effect_recovery(phase_id, step_id, execution_id, reason, retry=True)
        if isinstance(generation, TransitionResult):
            return generation
        effect = self.handler(phase_id, step_id).effect
        if effect is None or effect.authorize_retry is None:
            return kernel._reject("Configured effect retry handler is unavailable.")
        permit = self.authorize_outcome(TransitionIntent.RECOVER_EVIDENCE, phase_id, step_id,
                                        execution_id=execution_id)
        verified = effect.authorize_retry(self.context(
            phase_id, step_id, permit=permit, role=HookRole.RETRY, parameters={"reason": reason}))
        if not isinstance(verified, RetryDecision):
            raise TypeError("Effect retry verification must return RetryDecision.")
        if not verified.allowed:
            return kernel._reject(verified.detail)
        if rejected := kernel.validate_outcome_permit(permit):
            return rejected
        if verified.updates:
            self._evidence_sessions[permit].apply(verified.updates, checkpoint=True)
        return kernel._apply_effect_recovery(
            phase_id, step_id, execution_id, reason, generation, retry=True)

    @revision_checked
    def supersede_effect(self, phase_id: str, step_id: str, execution_id: str, reason: str,
                         *, confirm_idempotent: bool = False) -> TransitionResult:
        kernel = self._transition_kernel()
        if confirm_idempotent is not True:
            return kernel._reject("Confirm that the desired-state write is idempotent before superseding.")
        generation = kernel._prepare_effect_recovery(phase_id, step_id, execution_id, reason, retry=False)
        if isinstance(generation, TransitionResult):
            return generation
        return kernel._apply_effect_recovery(
            phase_id, step_id, execution_id, reason, generation, retry=False)

    @revision_checked
    def claim_notification_step(self, notification_id: str, approved_hash: str, executor: str) -> TransitionResult:
        from orchestrator import delivery

        if rejected := self._transition_kernel()._invalid():
            return rejected
        if not isinstance(self.state.notification_deliveries, dict):
            return self._transition_kernel()._reject("Notification ledger must be a mapping.")
        try:
            ledger = self.state.notification_deliveries.get(notification_id)
            item = delivery.validate_record(self, ledger)
        except ValueError as exc:
            return self._transition_kernel()._reject(str(exc))
        if delivery.is_progress_receipt(ledger):
            return self._transition_kernel()._reject("A settled notification receipt cannot be claimed.")
        reason = delivery.scope_reason(self, item["scope"])
        if reason:
            return self._transition_kernel()._reject(reason)
        return self._transition_kernel()._claim_notification_step(notification_id, approved_hash, executor)

    @revision_checked
    def release_notification_step(self, notification_id: str, execution_id: str) -> TransitionResult:
        if not isinstance(self.state.notification_deliveries, dict):
            return self._transition_kernel()._reject("Notification ledger must be a mapping.")
        try:
            return self._transition_kernel().release_notification_step(notification_id, execution_id)
        except ValueError as exc:
            return self._transition_kernel()._reject(str(exc))

    @revision_checked
    def record_notification_evidence(self, notification_id: str) -> TransitionResult:
        from orchestrator import delivery

        if not isinstance(self.state.notification_deliveries, dict):
            return self._transition_kernel()._reject("Notification ledger must be a mapping.")
        try:
            ledger = self.state.notification_deliveries.get(notification_id)
            item = delivery.validate_record(self, ledger)
        except ValueError as exc:
            return self._transition_kernel()._reject(str(exc))
        if delivery.is_progress_receipt(ledger):
            return TransitionResult("annotated", "Notification receipt is already settled.")
        reason = delivery.scope_reason(self, item["scope"], acknowledgement=True)
        completed_reasons = ("owning step complete", "release complete", "outside owning phase/window")
        if reason and reason not in completed_reasons:
            return self._transition_kernel()._reject(reason)
        scope = item["scope"]
        completed_owner = False
        if item["completion"].get("kind") == "step" and self.workflow.step(scope.get("phase"), scope.get("step")):
            record = self.state.get_step(scope["phase"], scope["step"])
            ledger = self.state.notification_deliveries[notification_id]
            attempt = ledger["attempts"][-1] if ledger["attempts"] else {}
            completed_owner = (
                record.status == "done" and not record.execution
                and record.data.get("notification_id") == notification_id
                and record.data.get("notification_execution_id") == attempt.get("id"))
        if reason and not completed_owner:
            return self._transition_kernel()._reject(reason)
        return self._transition_kernel().record_notification_evidence(notification_id)

    @revision_checked
    def scout_pending_steps(self) -> list:
        return list(self.scheduling().scout_pending)

    # ---- manual overrides (human-driven transitions, §7.1 constraint #5) ----
    @revision_checked
    def completed_step_outcome(self, phase_id: str, step_id: str) -> Optional[Done]:
        """Terminal steps stay terminal until explicitly reopened."""
        if not self._step_complete(phase_id, step_id):
            return None
        record = self.state.get_step(phase_id, step_id)
        return Done(note=f"Already {record.status}: {phase_id}/{step_id}; no action required.",
                    links=list(record.links or []))

    def _find_step(self, phase_id: str, step_id: str):
        workflow = self._workflow_definition()
        phase = workflow.phase(phase_id)
        return phase.raw if phase and workflow.step(phase_id, step_id) else None

    @revision_checked
    def skip_step(self, phase_id: str, step_id: str, reason: str) -> NextAction:
        """Mark a step skipped (counts as done for progression) without running it.
        A reason is REQUIRED (audit). For 'doesn't apply' or 'done manually outside the tool'."""
        return self._next_from_transition(
            self._transition_kernel().skip(phase_id, step_id, reason)
        )

    @revision_checked
    def complete_step(self, phase_id: str = None, step_id: str = None, note: str = "") -> NextAction:
        """Mark a reminder (human, non-gate) step done. Defaults to the step the
        conductor is currently holding on. This is how a person clears an
        'ACTION NEEDED' hold once they've actually done the task."""
        if rejected := self._transition_kernel()._invalid():
            return self._next_from_transition(rejected)
        if bool(phase_id) != bool(step_id):
            return NextAction(kind="idle", message="Provide both --phase and --step, or neither.")
        if not phase_id:
            hold = self._projection().current_hold()
            phase_id = hold.phase_id if hold else None
            step_id = hold.step_id if hold else None
        return self._next_from_transition(
            self._transition_kernel().complete(phase_id, step_id, note)
        )

    @revision_checked
    def record_scout_step(self, phase_id: str, step_id: str, status: str,
                          detail: str = "", *, execution_id: str = None,
                          refresh: bool = False) -> NextAction:
        """Record the outcome of a scout-assisted step (one the skill ran via MCP/
        browser, e.g. the CCOA lockdown check).
          * status == "pass"      -> mark the step done and let the flow continue.
          * status == "attention" -> keep it held (needs the owner) with the detail
            (e.g. a Production CCOA lockdown overlaps — the owner must shift CCD).
        Repeatable observation commands may refresh prior results explicitly;
        this never overrides a reservation or a human skip."""
        if rejected := self._transition_kernel()._invalid():
            raise ValueError(rejected.message)
        definition = self._workflow_definition().step(phase_id, step_id)
        if not definition:
            return NextAction(kind="idle", message=f"No such step: {phase_id}/{step_id}")
        if definition.is_gate:
            raise ValueError(f"record-step cannot complete a human gate: {phase_id}/{step_id}")
        execution = self.step_execution(phase_id, step_id)
        completed = self.completed_step_outcome(phase_id, step_id)
        if completed is not None and (not refresh or self.state.get_step(phase_id, step_id).status == "skipped"):
            return NextAction(kind="idle", phase=phase_id, step=step_id, message=completed.note)
        if status not in ("pass", "attention"):
            raise ValueError("Scout result must be pass or attention")
        if definition.kind != StepKind.EXTERNAL:
            raise ValueError("Only external steps accept generic record-step results.")
        if definition.write_command and not execution:
            raise ValueError(
                f"{phase_id}/{step_id} requires an active {definition.write_command} reservation."
            )
        links = self.state.get_step(phase_id, step_id).links
        outcome = Done(detail, by="scout", links=links) if status == "pass" else Blocked(
            detail, by="scout", links=links)
        if execution or execution_id:
            return self.settle_execution(phase_id, step_id, execution_id, outcome)
        permit = self.authorize_outcome(
            TransitionIntent.REFRESH if refresh else TransitionIntent.RECORD,
            phase_id, step_id,
        )
        return self.apply_outcome(permit, outcome)

    @revision_checked
    def reopen_step(self, phase_id: str, step_id: str, reason: str = "") -> NextAction:
        """Undo a done/skipped step so the conductor runs it again. Reason optional."""
        return self._next_from_transition(
            self.reopen(phase_id, step_id, reason)
        )

    @revision_checked
    def halt(self, reason: str) -> NextAction:
        """Emergency hold — nothing advances until resume(). Reason REQUIRED (audit)."""
        return self._next_from_transition(self._transition_kernel().halt(reason))

    @revision_checked
    def resume(self, reason: str = "") -> NextAction:
        """Clear an emergency halt. Reason optional."""
        return self._next_from_transition(self._transition_kernel().resume(reason))

    # ---- gates ----
    def _gate_approved(self, phase: str, step: str) -> bool:
        definition = self._workflow_definition().step(phase, step)
        return bool(
            definition
            and definition.kind == StepKind.APPROVAL_GATE
            and self._projection().gate_approved(definition)
        )

    @revision_checked
    def approve_gate(self, comment: str = "") -> NextAction:
        """Record approval for the current holding gate and continue."""
        return self._next_from_transition(
            self._transition_kernel().approve_gate(comment)
        )

    @revision_checked
    def deny_gate(self, comment: str = "") -> NextAction:
        return self._next_from_transition(
            self._transition_kernel().deny_gate(comment)
        )

    # `_phase_included` is a shared helper (used by both the state machine and the status
    # views mixin). The status view-model builders live in orchestrator/status_views.py.
    def _phase_included(self, phase: dict) -> bool:
        definition = self._workflow_definition().phase(phase["id"])
        return bool(definition and self._projection().phase_included(definition))
