from tests._context import context as _unit_context
from orchestrator.handler_contracts import HookRole


def _context(state):
    return _unit_context(state, role=HookRole.PREPARE, step_key="phase.effect")
"""Generic in-process effect ownership and crash-recovery contract."""
from copy import deepcopy

import pytest
import yaml

import orchestrator.engine as engine_module
from orchestrator import cli, cli_common as C, effects
from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.handlers import HandlerCatalog
from orchestrator.outcomes import Blocked, Done, InProgress, NeedsSkill
from orchestrator.state import ReleaseState, StepState
from orchestrator.workflow import (
    WorkflowConfigError,
    WorkflowDefinition,
)


def _config(
    mode, execution="sequential", effect_retry=False, effect_recovery=None
):
    recovery = (
        effect_recovery
        if effect_recovery is not None
        else ("frozen" if mode in ("idempotent", "transactional") else None)
    )
    return {
        "phases": [{
            "id": "phase",
            "name": "Phase",
            "execution": execution,
            "steps": [{
                "id": "effect",
                "name": "Effect",
                "kind": "auto",
                "effect_mode": mode,
                **({"effect_recovery": recovery} if recovery else {}),
                **({"effect_retry": True} if effect_retry else {}),
            }],
        }],
    }


def _effect_descriptor(handler):
    workflow = WorkflowDefinition.compile(
        _config(handler.EFFECT_MODE, effect_recovery=handler.EFFECT_RECOVERY,
                effect_retry=callable(getattr(handler, "authorize_retry", None)))
    )
    catalog = HandlerCatalog.compile(workflow, lambda *_: handler)
    effect = catalog.get("phase", "effect").effect
    assert effect is not None
    return effect


def _orchestrator(
    tmp_path,
    monkeypatch,
    handler,
    mode,
    state=None,
    execution="sequential",
    effect_recovery=None,
):
    path = tmp_path / "phases.yaml"
    path.write_text(
        yaml.safe_dump(
            _config(mode, execution, effect_recovery=effect_recovery)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        engine_module.steps,
        "get_step",
        lambda phase, step: handler if (phase, step) == ("phase", "effect") else None,
    )
    return Orchestrator(
        str(path), state or ReleaseState(release_id="r"), mocks={}
    )


def test_read_only_auto_step_keeps_direct_execution_path(tmp_path, monkeypatch):
    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "read_only"

        @staticmethod
        def build(_state):
            return Done("read complete")

    orch = _orchestrator(tmp_path, monkeypatch, Handler, "read_only")

    action = orch.step_once()

    assert action.kind == "ran"
    assert orch.state.get_step("phase", "effect").status == "done"
    assert orch.state.get_step("phase", "effect").execution is None


@pytest.mark.parametrize("mode", ["idempotent", "transactional"])
@pytest.mark.parametrize("stage,change", [
    ("prepare", "halt"), ("prepare", "invalidate"),
    ("execute", "halt"), ("execute", "invalidate"),
    ("execute", "malformed"), ("execute", "exception"),
])
def test_effect_permission_rechecks_preserve_checkpoint_and_owner(
        tmp_path, monkeypatch, mode, stage, change):
    calls, checkpoints = [], []

    def mutate(state):
        if change == "halt":
            state.halt = {"reason": "incident"}
        elif change == "invalidate":
            record = state.get_step("phase", "effect")
            record.invalidated_at = "2099-09-12T12:01:00Z"
            record.status = "blocked"
            state.set_step("phase", "effect", record)
        elif change == "exception":
            raise RuntimeError("provider failed")

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = mode
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: Done("unused"))

        @staticmethod
        def prepare_effect(context):
            calls.append("prepare")
            if stage == "prepare":
                mutate(state)
            return {"identity": "stable"}

        @staticmethod
        def execute(context):
            execution = context.effect.execution
            calls.append("execute")
            assert checkpoints[-1].execution["id"] == execution["id"]
            mutate(state)
            return Done(note=None) if change == "malformed" else Done("receipt")

        reconcile = execute

    state = ReleaseState(release_id="r")
    state._checkpoint = lambda: checkpoints.append(state.get_step("phase", "effect"))
    orch = _orchestrator(tmp_path, monkeypatch, Handler, mode, state)
    if change in ("exception", "malformed"):
        with pytest.raises((RuntimeError, TypeError)):
            orch.step_once()
    else:
        action = orch.step_once()
        assert not action.continue_drain
    record = state.get_step("phase", "effect")
    if stage == "prepare":
        assert calls == ["prepare"] and not checkpoints and record.execution is None
    elif change == "halt":
        assert record.status == "done" and record.execution is None
        assert orch.step_once().kind == "halted"
        assert calls == ["prepare", "execute"]
    else:
        assert record.execution["id"] == checkpoints[-1].execution["id"]
        assert record.status == ("blocked" if change == "invalidate" else "running")


def test_idempotent_effect_persists_once_and_reuses_execution_after_crash(
        tmp_path, monkeypatch):
    calls = []
    checkpoints = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "idempotent"
        EFFECT_RECOVERY = "frozen"

        @staticmethod
        def prepare_effect(context):
            return {"identity": f"stable:{context.release.release_id}"}

        @staticmethod
        def execute(context):
            execution = context.effect.execution
            calls.append(execution)
            assert checkpoints and checkpoints[-1]["status"] == "running"
            if len(calls) == 1:
                raise RuntimeError("crash after provider accepted the operation")
            return Done("provider confirms stable operation")

        build = staticmethod(lambda _state: Done("direct"))

    state = ReleaseState(release_id="r")
    state._checkpoint = lambda: checkpoints.append(
        deepcopy(state.get_step("phase", "effect").__dict__)
    )
    orch = _orchestrator(
        tmp_path, monkeypatch, Handler, "idempotent", state
    )

    with pytest.raises(RuntimeError, match="provider accepted"):
        orch.step_once()
    interrupted = state.get_step("phase", "effect")
    execution_id = interrupted.execution["id"]
    assert interrupted.status == "running"
    assert interrupted.execution["effect_input"] == {"identity": "stable:r"}

    recovered = _orchestrator(
        tmp_path, monkeypatch, Handler, "idempotent", state
    ).step_once()

    assert recovered.kind == "ran"
    assert [call["id"] for call in calls] == [execution_id, execution_id]
    assert state.get_step("phase", "effect").status == "done"
    assert state.get_step("phase", "effect").execution is None


def test_transactional_effect_reconciles_without_reexecuting_after_crash(
        tmp_path, monkeypatch):
    calls = {"build": 0, "reconcile": 0}
    checkpoints = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "transactional"
        EFFECT_RECOVERY = "frozen"

        @staticmethod
        def prepare_effect(context):
            return {"identity": f"create:{context.release.release_id}"}

        @staticmethod
        def execute(context):
            calls["build"] += 1
            raise RuntimeError("crash after non-idempotent create")

        build = staticmethod(lambda _state: Done("direct"))

        @staticmethod
        def reconcile(context):
            execution = context.effect.execution
            calls["reconcile"] += 1
            assert execution == state.get_step("phase", "effect").execution
            return Done("adopted created resource")

    state = ReleaseState(release_id="r")
    state._checkpoint = lambda: checkpoints.append(
        deepcopy(state.get_step("phase", "effect").__dict__)
    )

    with pytest.raises(RuntimeError, match="non-idempotent"):
        _orchestrator(
            tmp_path, monkeypatch, Handler, "transactional", state
        ).step_once()
    execution_id = state.get_step("phase", "effect").execution["id"]

    recovered = _orchestrator(
        tmp_path, monkeypatch, Handler, "transactional", state
    ).step_once()

    assert recovered.kind == "ran"
    assert calls == {"build": 1, "reconcile": 1}
    assert checkpoints[0]["execution"]["id"] == execution_id
    assert state.get_step("phase", "effect").status == "done"


def test_blocked_transactional_effect_preserves_owner_and_reconciles_next_run(
        tmp_path, monkeypatch):
    calls = {"execute": 0, "reconcile": 0}

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "transactional"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: Done("direct"))
        prepare_effect = staticmethod(lambda _state: {"target": "resource"})

        @staticmethod
        def execute(context):
            calls["execute"] += 1
            from orchestrator.outcomes import Blocked
            return Blocked("provider outcome uncertain")

        @staticmethod
        def reconcile(context):
            calls["reconcile"] += 1
            return Done("provider confirms resource")

    state = ReleaseState(release_id="r")
    state._checkpoint = lambda: None
    first = _orchestrator(
        tmp_path, monkeypatch, Handler, "transactional", state
    ).step_once()
    blocked = state.get_step("phase", "effect")
    execution_id = blocked.execution["id"]

    assert first.kind == "reminder"
    assert blocked.status == "blocked"
    assert blocked.execution["effect_mode"] == "transactional"
    assert not _orchestrator(
        tmp_path, monkeypatch, Handler, "transactional", state
    ).reopen_step("phase", "effect", "unsafe").message.startswith("Reopened")
    assert _orchestrator(
        tmp_path, monkeypatch, Handler, "transactional", state
    ).complete_step("phase", "effect", "unsafe").kind == "idle"
    assert not _orchestrator(
        tmp_path, monkeypatch, Handler, "transactional", state
    )._transition_kernel().skip(
        "phase", "effect", "unsafe", execution_id=execution_id
    ).changed
    assert state.get_step("phase", "effect").execution["id"] == execution_id

    recovered = _orchestrator(
        tmp_path, monkeypatch, Handler, "transactional", state
    ).step_once()

    assert recovered.kind == "ran"
    assert calls == {"execute": 1, "reconcile": 1}
    assert state.get_step("phase", "effect").status == "done"


def test_parallel_dispatch_recovers_owned_transactional_effect(
        tmp_path, monkeypatch):
    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "transactional"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: pytest.fail("must reconcile"))
        prepare_effect = staticmethod(lambda _state: {"identity": "create:r"})
        execute = staticmethod(
            lambda context: pytest.fail("must reconcile")
        )
        reconcile = staticmethod(
            lambda context: Done("parallel recovery complete")
        )

    state = ReleaseState(release_id="r")
    prepared = effects.prepare(_effect_descriptor(Handler), _context(state))
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="running",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "transactional",
                "effect_recovery": "frozen",
                "operation_key": prepared["operation_key"],
                "effect_input": prepared["input"],
            },
        ),
    )
    orch = _orchestrator(
        tmp_path,
        monkeypatch,
        Handler,
        "transactional",
        state,
        execution="parallel",
    )

    action = orch.step_once()

    assert action.kind == "ran"
    assert state.get_step("phase", "effect").status == "done"


def test_recovery_blocks_when_effect_operation_identity_changes(
        tmp_path, monkeypatch):
    called = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "idempotent"
        EFFECT_RECOVERY = "match_current"

        @staticmethod
        def execute(context):
            called.append(True)
            return Done("unsafe")

        @staticmethod
        def prepare_effect(_state):
            return {"identity": "current-inputs"}

        build = staticmethod(lambda _state: Done("direct"))

    state = ReleaseState(release_id="r")
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="running",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "idempotent",
                "effect_recovery": "match_current",
                "operation_key": effects.input_key(
                    "effect", {"identity": "old-inputs"}
                ),
                "effect_input": {"identity": "old-inputs"},
            },
        ),
    )

    action = _orchestrator(
        tmp_path,
        monkeypatch,
        Handler,
        "idempotent",
        state,
        effect_recovery="match_current",
    ).step_once()

    record = state.get_step("phase", "effect")
    assert action.kind == "reminder"
    assert not called
    assert record.status == "blocked"
    assert record.execution["id"] == "owned"
    assert "inputs changed" in record.note


def test_active_effect_ignores_outcome_mock_and_reconciles(
        tmp_path, monkeypatch):
    calls = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "transactional"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: Done("direct"))
        prepare_effect = staticmethod(lambda _state: {"target": "resource"})
        execute = staticmethod(
            lambda context: pytest.fail("must reconcile")
        )

        @staticmethod
        def reconcile(context):
            calls.append("reconcile")
            return Done("real reconciliation")

    state = ReleaseState(release_id="r")
    prepared = effects.prepare(_effect_descriptor(Handler), _context(state))
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="running",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "transactional",
                "effect_recovery": "frozen",
                "operation_key": prepared["operation_key"],
                "effect_input": prepared["input"],
            },
        ),
    )
    orch = _orchestrator(
        tmp_path, monkeypatch, Handler, "transactional", state
    )
    orch.mocks = {
        "phase.effect": {
            "outcome": "blocked",
            "reason": "mock must not erase ownership",
        }
    }

    action = orch.step_once()

    assert action.kind == "ran"
    assert calls == ["reconcile"]
    assert state.get_step("phase", "effect").status == "done"


def test_recovery_uses_frozen_effect_mocks_not_ambient_changes(
        tmp_path, monkeypatch):
    from steps.lib.mockctx import mock_input, MISSING

    seen = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "idempotent"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: Done("direct"))
        prepare_effect = staticmethod(lambda _state: {"target": "resource"})

        @staticmethod
        def execute(context):
            seen.append(context.input("access", MISSING))
            return Done("mocked operation recovered")

    state = ReleaseState(release_id="r")
    prepared = effects.prepare(
        _effect_descriptor(Handler), _context(state), frozen_mocks={"access": "granted"}
    )
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="running",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "idempotent",
                "effect_recovery": "frozen",
                "operation_key": prepared["operation_key"],
                "effect_input": prepared["input"],
            },
        ),
    )
    orch = _orchestrator(
        tmp_path, monkeypatch, Handler, "idempotent", state
    )
    orch.mocks = {"phase.effect": {"access": "changed"}}

    assert orch.step_once().kind == "ran"
    assert seen == ["granted"]


def test_match_current_recovery_hashes_and_uses_persisted_mocks(
        tmp_path, monkeypatch):
    from steps.lib import mockctx
    from steps.lib.mockctx import mock_input, MISSING

    seen = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "idempotent"
        EFFECT_RECOVERY = "match_current"
        build = staticmethod(lambda _state: Done("direct"))
        prepare_effect = staticmethod(
            lambda context: {"target": context.input("target", MISSING)}
        )

        @staticmethod
        def execute(context):
            seen.append(context.input("target", MISSING))
            return Done("mocked desired state recovered")

    state = ReleaseState(release_id="r")
    with mockctx.active({"target": "frozen"}):
        prepared = effects.prepare(
            _effect_descriptor(Handler), _context(state), frozen_mocks={"target": "frozen"}
        )
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="running",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "idempotent",
                "effect_recovery": "match_current",
                "operation_key": prepared["operation_key"],
                "effect_input": prepared["input"],
            },
        ),
    )
    orch = _orchestrator(
        tmp_path,
        monkeypatch,
        Handler,
        "idempotent",
        state,
        effect_recovery="match_current",
    )
    orch.mocks = {"phase.effect": {"target": "ambient-change"}}

    assert orch.step_once().kind == "ran"
    assert seen == ["frozen"]


def test_handler_receives_immutable_frozen_effect_input():
    class Handler:
        ID = "effect"

        @staticmethod
        def execute(context):
            execution = context.effect.execution
            execution["effect_input"]["nested"]["target"] = "mutated"
            return Done("complete")

    original = {
        "operation_key": effects.input_key(
            "effect", {"nested": {"target": "original"}}
        ),
        "effect_input": {"nested": {"target": "original"}},
    }

    handler = effects.EffectHandler(
        step_id="effect",
        mode=effects.EffectMode.IDEMPOTENT,
        recovery=effects.EffectRecovery.FROZEN,
        prepare=lambda _state: {"nested": {"target": "original"}},
        execute=Handler.execute,
    )
    from dataclasses import replace
    from orchestrator.step_context import EffectContext
    from orchestrator.services import EffectServices
    context = replace(_context(ReleaseState(release_id="r")),
                      effect=EffectContext(original, None, EffectServices()))
    with pytest.raises(TypeError, match="immutable"):
        effects.invoke(handler, context, recovering=True)

    assert original["effect_input"]["nested"]["target"] == "original"
    assert effects.execution_input_is_valid("effect", original)


def test_state_loader_rejects_effect_input_hash_mismatch(tmp_path):
    import json

    frozen = {"target": "original"}
    state = ReleaseState(release_id="r")
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="running",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "idempotent",
                "effect_recovery": "frozen",
                "operation_key": effects.input_key("effect", frozen),
                "effect_input": frozen,
            },
        ),
    )
    path = tmp_path / "state.json"
    state.save(str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["steps"]["phase.effect"]["execution"]["effect_input"]["target"] = "changed"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid step effect execution"):
        ReleaseState.load(str(path))


def test_missing_effect_handler_rejects_new_orchestrator_without_erasing_ownership(
        tmp_path, monkeypatch):
    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "transactional"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: Done("direct"))
        prepare_effect = staticmethod(lambda _state: {"target": "resource"})
        execute = staticmethod(lambda context: Done("created"))
        reconcile = staticmethod(lambda context: Done("reconciled"))

    state = ReleaseState(release_id="r")
    prepared = effects.prepare(_effect_descriptor(Handler), _context(state))
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="running",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "transactional",
                "effect_recovery": "frozen",
                "operation_key": prepared["operation_key"],
                "effect_input": prepared["input"],
            },
        ),
    )
    _orchestrator(
        tmp_path, monkeypatch, Handler, "transactional", state
    )
    monkeypatch.setattr(engine_module.steps, "get_step", lambda *_args: None)
    before = deepcopy(state.steps)
    with pytest.raises(WorkflowConfigError, match="requires a handler module"):
        Orchestrator(str(tmp_path / "phases.yaml"), state, mocks={})

    record = state.get_step("phase", "effect")
    assert state.steps == before
    assert record.status == "running"
    assert record.execution["id"] == "owned"


def test_blocked_prepare_never_reserves_or_checkpoints(
        tmp_path, monkeypatch):
    from orchestrator.outcomes import Blocked

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "idempotent"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: Blocked("missing source"))
        prepare_effect = staticmethod(lambda _state: Blocked("missing source"))
        execute = staticmethod(
            lambda context: pytest.fail("must not execute")
        )

    state = ReleaseState(release_id="r")
    checkpoints = []
    state._checkpoint = lambda: checkpoints.append(True)

    action = _orchestrator(
        tmp_path, monkeypatch, Handler, "idempotent", state
    ).step_once()

    record = state.get_step("phase", "effect")
    assert action.kind == "reminder"
    assert record.status == "blocked"
    assert record.execution is None
    assert not checkpoints


def test_effect_never_runs_when_reservation_checkpoint_fails(
        tmp_path, monkeypatch):
    called = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "idempotent"
        EFFECT_RECOVERY = "frozen"

        @staticmethod
        def prepare_effect(_state):
            return {"identity": "stable"}

        @staticmethod
        def execute(context):
            called.append(True)
            return Done("unsafe")

        build = staticmethod(lambda _state: Done("direct"))

    state = ReleaseState(release_id="r")

    def fail_checkpoint():
        raise OSError("disk full")

    state._checkpoint = fail_checkpoint
    orch = _orchestrator(
        tmp_path, monkeypatch, Handler, "idempotent", state
    )

    with pytest.raises(OSError, match="disk full"):
        orch.step_once()

    assert not called
    assert state.get_step("phase", "effect").status == "pending"
    assert state.get_step("phase", "effect").execution is None


def test_effect_handler_contract_is_explicit_and_mode_specific():
    workflow = WorkflowDefinition.compile(_config("transactional"))
    with pytest.raises(WorkflowConfigError, match="requires a handler module"):
        HandlerCatalog.compile(workflow, lambda *_: None)

    class MissingRecovery:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "transactional"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: Done())
        prepare_effect = staticmethod(lambda _state: {"identity": "key"})
        execute = staticmethod(lambda context: Done())

    with pytest.raises(WorkflowConfigError, match="must define reconcile"):
        HandlerCatalog.compile(workflow, lambda *_: MissingRecovery)

    class WrongMode(MissingRecovery):
        EFFECT_MODE = "idempotent"
        reconcile = staticmethod(lambda context: Done())

    with pytest.raises(WorkflowConfigError, match="does not match"):
        HandlerCatalog.compile(workflow, lambda *_: WrongMode)

    implicit = _config("read_only")
    implicit["phases"][0]["steps"][0].pop("effect_mode")
    with pytest.raises(WorkflowConfigError, match="must declare effect_mode"):
        HandlerCatalog.compile(
            WorkflowDefinition.compile(implicit), lambda *_: WrongMode
        )


@pytest.mark.parametrize("mode", ["unknown", "", 42])
def test_workflow_rejects_invalid_effect_mode(mode):
    config = _config(mode)
    if mode == "":
        config["phases"][0]["steps"][0]["effect_mode"] = ""
    with pytest.raises(WorkflowConfigError, match="invalid effect_mode"):
        WorkflowDefinition.compile(config)


def test_non_auto_step_cannot_declare_auto_effect_mode():
    config = _config("idempotent")
    config["phases"][0]["steps"][0]["kind"] = "external"
    with pytest.raises(WorkflowConfigError, match="Only auto steps"):
        WorkflowDefinition.compile(config)


def test_verified_retry_command_is_the_only_way_to_clear_blocked_effect(
        tmp_path, monkeypatch, capsys):
    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "transactional"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: Done("direct"))
        prepare_effect = staticmethod(lambda _state: {"target": "resource"})
        execute = staticmethod(lambda context: Done("created"))
        reconcile = staticmethod(lambda context: Done("reconciled"))

        @staticmethod
        def authorize_retry(context):
            reason = context.parameters.reason
            from orchestrator.evidence import RetryDecision
            assert "exhaustive provider search" in reason
            return RetryDecision(True, "provider confirms absence")

    from steps.bug_bash.clone_plans_auth import RetryParameters
    Handler.PARAMETERS = {"authorize_retry": RetryParameters}

    config_path = tmp_path / "phases.yaml"
    config_path.write_text(
        yaml.safe_dump(_config("transactional", effect_retry=True)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        engine_module.steps,
        "get_step",
        lambda phase, step: Handler
        if (phase, step) == ("phase", "effect")
        else None,
    )
    state = ReleaseState(release_id="r")
    Orchestrator(str(config_path), state, mocks={})
    prepared = effects.prepare(_effect_descriptor(Handler), _context(state))
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="blocked",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "transactional",
                "effect_recovery": "frozen",
                "operation_key": prepared["operation_key"],
                "effect_input": prepared["input"],
            },
        ),
    )
    state.save(str(tmp_path / "r" / "release-state.json"))
    base = [
        "--config", str(config_path),
        "--runs-root", str(tmp_path),
        "retry-effect",
        "--release", "r",
        "--phase", "phase",
        "--step", "effect",
        "--execution-id", "owned",
        "--reason", "exhaustive provider search returned no resource",
    ]

    assert cli.main(base) == 1
    capsys.readouterr()
    assert ReleaseState.load(
        str(tmp_path / "r" / "release-state.json")
    ).get_step("phase", "effect").execution["id"] == "owned"

    assert cli.main(base + ["--confirm-absent"]) == 0
    recovered = ReleaseState.load(str(tmp_path / "r" / "release-state.json"))
    record = recovered.get_step("phase", "effect")
    assert record.status == "pending"
    assert record.execution is None
    assert record.data == {}


def test_idempotent_supersede_requires_exact_owner_and_confirmation(
        tmp_path, monkeypatch, capsys):
    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "idempotent"
        EFFECT_RECOVERY = "match_current"
        build = staticmethod(lambda _state: Done("direct"))
        prepare_effect = staticmethod(lambda _state: {"target": "current"})
        execute = staticmethod(lambda context: Done("applied"))

    config_path = tmp_path / "phases.yaml"
    config_path.write_text(
        yaml.safe_dump(
            _config("idempotent", effect_recovery="match_current")
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        engine_module.steps,
        "get_step",
        lambda phase, step: Handler
        if (phase, step) == ("phase", "effect")
        else None,
    )
    state = ReleaseState(release_id="r")
    Orchestrator(str(config_path), state, mocks={})
    prepared = effects.prepare(_effect_descriptor(Handler), _context(state))
    state.set_step(
        "phase",
        "effect",
        StepState(
            status="blocked",
            execution={
                "id": "owned",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
                "effect_mode": "idempotent",
                "effect_recovery": "match_current",
                "operation_key": prepared["operation_key"],
                "effect_input": prepared["input"],
            },
        ),
    )
    state.save(str(tmp_path / "r" / "release-state.json"))
    base = [
        "--config", str(config_path),
        "--runs-root", str(tmp_path),
        "supersede-effect",
        "--release", "r",
        "--phase", "phase",
        "--step", "effect",
        "--execution-id", "owned",
        "--reason", "new validated evidence supersedes the partial desired state",
    ]

    assert cli.main(base) == 1
    capsys.readouterr()
    assert cli.main(base + ["--confirm-idempotent"]) == 0
    record = ReleaseState.load(
        str(tmp_path / "r" / "release-state.json")
    ).get_step("phase", "effect")
    assert record.status == "pending"
    assert record.execution is None


def test_every_implemented_auto_handler_declares_effect_policy():
    import steps

    with open(C.DEFAULT_CONFIG, encoding="utf-8") as handle:
        workflow = WorkflowDefinition.compile(yaml.safe_load(handle))
    handlers = {
        key: module
        for key, module in steps.discover().items()
        if getattr(module, "KIND", None) == "agent"
    }
    declared = {
        key: workflow.step(*key.split(".", 1)).effect_mode.value
        for key in handlers
        if workflow.step(*key.split(".", 1))
    }

    assert set(declared) == set(handlers)
    assert {
        key for key, mode in declared.items() if mode != "read_only"
    } == {
        "preflight.oneauth_access",
        "bug_bash.clone_plans_broker",
        "bug_bash.clone_plans_auth",
        "bug_bash.ui_test_status",
        "rollout_start.tag_authenticator",
    }
    HandlerCatalog.compile(workflow, steps.get_step)


@pytest.mark.parametrize("mode", ["idempotent", "transactional"])
@pytest.mark.parametrize(
    "first_outcome,status",
    [
        (Done("complete"), "done"),
        (Blocked("provider uncertain"), "blocked"),
        (InProgress("provider running", poll_in_min=9), "in_flight"),
    ],
)
def test_canonical_effect_outcomes_preserve_ownership_until_completion(
        tmp_path, monkeypatch, mode, first_outcome, status):
    first_outcome = deepcopy(first_outcome)
    first_outcome.links = [{"name": "Provider", "url": "https://example.invalid/job"}]
    calls = []
    checkpoints = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = mode
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: pytest.fail("must use effect hooks"))
        prepare_effect = staticmethod(lambda _state: {"target": "resource"})

        @staticmethod
        def execute(context):
            execution = context.effect.execution
            assert checkpoints[-1].execution == execution
            calls.append(("execute", execution["id"]))
            return first_outcome if len(calls) == 1 else Done("settled")

        @staticmethod
        def reconcile(context):
            execution = context.effect.execution
            calls.append(("reconcile", execution["id"]))
            return Done("settled")

    state = ReleaseState(release_id="r")
    state._checkpoint = lambda: checkpoints.append(state.get_step("phase", "effect"))
    orch = _orchestrator(tmp_path, monkeypatch, Handler, mode, state)
    orch.step_once()
    record = state.get_step("phase", "effect")

    assert len(checkpoints) == 1
    assert record.status == status
    assert record.links == first_outcome.links
    if status == "done":
        assert record.execution is None
        return

    execution_id = checkpoints[0].execution["id"]
    assert record.execution["id"] == execution_id
    if status == "in_flight":
        assert record.data["poll_in_min"] == 9
    orch.step_once()

    recovery = "reconcile" if mode == "transactional" else "execute"
    assert calls == [("execute", execution_id), (recovery, execution_id)]
    assert len(checkpoints) == 1
    assert state.get_step("phase", "effect").status == "done"
    assert state.get_step("phase", "effect").execution is None


@pytest.mark.parametrize("value", [None, {"kind": "done"}, NeedsSkill(tool="unexpected")])
def test_invalid_effect_outcome_keeps_checkpointed_owner_for_reconciliation(
        tmp_path, monkeypatch, value):
    calls = []
    checkpoints = []

    class Handler:
        ID = "effect"
        KIND = "agent"
        EFFECT_MODE = "transactional"
        EFFECT_RECOVERY = "frozen"
        build = staticmethod(lambda _state: pytest.fail("must use effect hooks"))
        prepare_effect = staticmethod(lambda _state: {"target": "resource"})

        @staticmethod
        def execute(context):
            calls.append("execute")
            return value

        @staticmethod
        def reconcile(context):
            calls.append("reconcile")
            return Done("reconciled")

    state = ReleaseState(release_id="r")
    state._checkpoint = lambda: checkpoints.append(state.get_step("phase", "effect"))
    orch = _orchestrator(tmp_path, monkeypatch, Handler, "transactional", state)

    with pytest.raises(TypeError, match="expected Done/Blocked/InProgress"):
        orch.step_once()

    record = state.get_step("phase", "effect")
    assert record.status == "running"
    assert record.execution == checkpoints[0].execution
    assert orch.step_once().kind == "ran"
    assert calls == ["execute", "reconcile"]
    assert state.get_step("phase", "effect").execution is None
