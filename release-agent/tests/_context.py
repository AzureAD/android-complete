"""Explicit context and evidence wiring for isolated handler unit tests."""
from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module
from uuid import uuid4
from contextlib import contextmanager
from unittest.mock import patch

from orchestrator.context_boundary import EvidenceSession, evidence_view, release_view
from orchestrator.authority import EvidenceAuthority, WriteCapabilities
from orchestrator.outcomes import Blocked
from orchestrator.service_adapters import production_effects, production_services
from orchestrator.step_context import Clock, EffectContext, StepContext, freeze, thaw
from orchestrator.handler_contracts import HookRole
from orchestrator.parameters import NoParameters, ParameterSchema
from orchestrator.transitions import OutcomePermit, TransitionIntent
from orchestrator.state import ReleaseState
from steps.lib import mockctx


def fresh_orchestrator(*args, **kwargs):
    """Construct an explicitly bound test workflow; never repair loaded unbound state."""
    from orchestrator.engine import Orchestrator
    from orchestrator.revision import bind_initial

    orch = Orchestrator(*args, **kwargs)
    if orch.state.workflow_revision is None:
        bind_initial(orch)
    if orch.state._checkpoint is None:
        orch.state._checkpoint = lambda: None
    return orch


def adopt_test_revision(orch):
    """Explicitly adopt a test's edited executable definition before reseeding work."""
    from orchestrator import revision

    plan = revision.adoption_preview(orch)
    return revision.adopt(orch, plan["hash"], by="test-reviewer",
                          reason="Adopt the test workflow configuration")


def bind_model_state(state, workflow):
    """Bind standalone kernel/projection fixtures with explicit empty handler capabilities."""
    from types import SimpleNamespace
    from orchestrator.revision import bind_initial

    descriptor = SimpleNamespace(
        parameters={}, evidence=EvidenceAuthority(), writes=WriteCapabilities(),
        notification=False, status_email=False, fire_at_local=None)
    model = SimpleNamespace(
        state=state, config_path=None, readiness_path=None,
        _workflow_definition=lambda: workflow,
        handlers=SimpleNamespace(get=lambda _phase, _step: descriptor))
    if state.workflow_revision is None:
        bind_initial(model)
    return state


@contextmanager
def command_inputs(key, values):
    """Inject a command's engine inputs and the corresponding isolated unit context."""
    with patch("orchestrator.mocks.load_mocks", return_value={key: values}), mockctx.active(values):
        yield


def context(state, *, inputs=None, now=None, parameters=None, model=NoParameters,
            role=HookRole.BUILD, step_key=None):
    state = state or ReleaseState()
    return StepContext(
        release_view(state), evidence_view(state),
        Clock(now or datetime.now(timezone.utc)), production_services(),
        ParameterSchema.compile(model, "Test context").parse(parameters),
        freeze(mockctx._current.get() if inputs is None else inputs),
        new_id=lambda: uuid4().hex,
        role=role, step_key=step_key,
    )


def invoke(function, state, **parameters):
    state = state or ReleaseState()
    module = import_module(function.__module__)
    ctx = context(state, parameters=parameters,
                  model=getattr(module, "PARAMETERS", {}).get("build", NoParameters))
    phase = function.__module__.split(".")[1]
    permit = OutcomePermit(phase, module.ID, TransitionIntent.EFFECT)
    session = EvidenceSession(state, permit, lambda _: None,
                              authority=EvidenceAuthority(getattr(module, "EVIDENCE", ())), durable=True)
    if getattr(module, "EFFECT_MODE", "read_only") != "read_only":
        prepared = module.prepare_effect(ctx)
        if isinstance(prepared, Blocked):
            return prepared
        ctx = effect_context(function, state, {"id": "unit-effect", "effect_input": prepared},
                             parameters=parameters, session=session)
    result = function(ctx)
    session.apply(result.updates)
    return result


def effect_context(function, state, execution, *, parameters=None, session=None):
    module = import_module(function.__module__)
    role = HookRole(function.__name__)
    ctx = context(state, parameters=parameters, role=role,
                  model=getattr(module, "PARAMETERS", {}).get(role, NoParameters))
    if role == HookRole.RETRY:
        return ctx
    if session is None:
        permit = OutcomePermit(function.__module__.split(".")[1], module.ID, TransitionIntent.EFFECT)
        session = EvidenceSession(state, permit, lambda _: None,
                                  authority=EvidenceAuthority(getattr(module, "EVIDENCE", ())), durable=True)
    writers = production_effects(
        WriteCapabilities(getattr(module, "WRITES", ())),
        validate=lambda: None, committer=session, clock=ctx.clock)
    return replace(ctx, effect=EffectContext(freeze(execution), session.committer(), writers))


def invoke_effect(function, state, execution, reason=""):
    module = import_module(function.__module__)
    permit = OutcomePermit(function.__module__.split(".")[1], module.ID, TransitionIntent.EFFECT)
    session = EvidenceSession(state, permit, lambda _: None,
                              authority=EvidenceAuthority(getattr(module, "EVIDENCE", ())), durable=True)
    ctx = effect_context(function, state, execution, session=session, parameters=(
        {"reason": reason} if function.__name__ == "authorize_retry" else None))
    if function.__name__ == "authorize_retry":
        # Retry observes the currently owned execution, it is not another invocation.
        record = replace(ctx.evidence.step("bug_bash", "clone_plans_auth"),
                         execution=freeze(execution))
        ctx = replace(ctx, evidence=replace(ctx.evidence, steps=freeze({
            **ctx.evidence.steps, "bug_bash.clone_plans_auth": record})))
    result = function(ctx)
    session.apply(result.updates)
    return result


def pipeline(function, state, *args, **kwargs):
    update = function(context(state), *args, **kwargs)
    state.pipeline_runs = thaw(update.values)
    return update


def inspect(function, state, **parameters):
    ctx = context(state)
    outcome, report = function(ctx, **parameters)
    module = import_module(function.__module__)
    permit = OutcomePermit(function.__module__.split(".")[1], module.ID, TransitionIntent.PREPARE)
    EvidenceSession(state, permit, lambda _: None,
                    authority=EvidenceAuthority(getattr(module, "EVIDENCE", ())),
                    durable=False).apply(outcome.updates)
    return outcome, report


def approval(function, state, comment):
    """Exercise prepare/submit with injected provider ports, without durable state writes."""
    from orchestrator.step_context import ApprovalContext
    from tools import pipelines
    module = import_module(function.__module__)
    prepared_context = context(
        state, parameters={"comment": comment},
        model=module.PARAMETERS["prepare_approval"], role=HookRole.APPROVAL_PREPARE,
    )
    request = module.prepare_approval(prepared_context)
    if isinstance(request, Blocked):
        return False, request.reason
    return function(replace(
        prepared_context, parameters=NoParameters(), role=HookRole.APPROVAL,
        approval=ApprovalContext("unit-approval", request, lambda: pipelines.submit_pipeline_approval(
            request.org, request.project, request.approval_id, request.comment,
        )),
    ))
