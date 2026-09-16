"""Compiled, validated workflow definition for the release state machine."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional

from orchestrator.command_catalog import APPROVAL_COMMANDS, EXTERNAL_WRITE_COMMANDS
from orchestrator.effects import EffectMode, EffectRecovery


class WorkflowConfigError(ValueError):
    """The workflow configuration cannot form a deterministic state machine."""


class StepKind(str, Enum):
    AUTO = "auto"
    EXTERNAL = "external"
    HUMAN_ACTION = "human_action"
    ATTESTATION = "attestation"
    APPROVAL_GATE = "approval_gate"


class StepImplementation(str, Enum):
    HANDLER = "handler"
    DUMMY = "dummy"


@dataclass(frozen=True)
class StepDefinition:
    phase_id: str
    id: str
    name: str
    kind: StepKind
    implementation: StepImplementation
    owner: str
    source: Optional[str]
    approval_command: Optional[str]
    write_command: Optional[str]
    write_precondition: str
    repeatable: bool
    pollable: bool
    refresh_invalidation: str
    effect_mode: Optional[EffectMode]
    effect_recovery: Optional[EffectRecovery]
    effect_retry: bool
    depends_on: tuple[str, ...]
    raw: Mapping[str, Any]

    @property
    def key(self) -> str:
        return f"{self.phase_id}.{self.id}"

    @property
    def is_gate(self) -> bool:
        return self.kind == StepKind.APPROVAL_GATE

    @property
    def is_human_action(self) -> bool:
        return self.kind in (StepKind.HUMAN_ACTION, StepKind.ATTESTATION)


@dataclass(frozen=True)
class PhaseDefinition:
    id: str
    name: str
    execution: str
    anchor: Optional[str]
    conditional: bool
    steps: tuple[StepDefinition, ...]
    raw: Mapping[str, Any]

    def step(self, step_id: str) -> Optional[StepDefinition]:
        return next((step for step in self.steps if step.id == step_id), None)


@dataclass(frozen=True)
class WorkflowDefinition:
    version: int
    phases: tuple[PhaseDefinition, ...]
    phase_by_id: Mapping[str, PhaseDefinition]
    step_by_key: Mapping[str, StepDefinition]
    fingerprint: str

    @classmethod
    def compile(cls, config: Mapping[str, Any]) -> "WorkflowDefinition":
        if not isinstance(config, Mapping):
            raise WorkflowConfigError("Workflow config must be a mapping.")
        _reject_unknown_keys(config, {"version", "phases"}, "workflow")
        raw_phases = config.get("phases")
        if not isinstance(raw_phases, list) or not raw_phases:
            raise WorkflowConfigError("Workflow config must declare at least one phase.")

        phases: list[PhaseDefinition] = []
        phase_ids: set[str] = set()
        step_by_key: dict[str, StepDefinition] = {}
        for phase_index, raw_phase in enumerate(raw_phases):
            if not isinstance(raw_phase, Mapping):
                raise WorkflowConfigError(f"Phase #{phase_index + 1} must be a mapping.")
            phase_id = _required_id(raw_phase.get("id"), f"phase #{phase_index + 1}")
            _reject_unknown_keys(
                raw_phase,
                {
                    "id", "name", "checklist_phase", "anchor", "execution",
                    "steps", "show_pipeline_runs", "conditional",
                },
                f"phase {phase_id}",
            )
            if phase_id in phase_ids:
                raise WorkflowConfigError(f"Duplicate phase id: {phase_id}")
            phase_ids.add(phase_id)

            execution = raw_phase.get("execution", "sequential")
            if execution not in ("sequential", "parallel"):
                raise WorkflowConfigError(
                    f"Phase {phase_id} has invalid execution mode {execution!r}."
                )
            anchor = raw_phase.get("anchor")
            if anchor is not None and (
                not isinstance(anchor, str) or not re.fullmatch(r"CCD(?:[+-]\d+)?", anchor)
            ):
                raise WorkflowConfigError(f"Phase {phase_id} has invalid anchor {anchor!r}.")
            phase_name = _required_name(raw_phase.get("name"), f"phase {phase_id}")
            conditional = raw_phase.get("conditional", False)
            if not isinstance(conditional, bool):
                raise WorkflowConfigError(
                    f"Phase {phase_id} conditional must be a boolean."
                )

            raw_steps = raw_phase.get("steps")
            if not isinstance(raw_steps, list) or not raw_steps:
                raise WorkflowConfigError(f"Phase {phase_id} must declare at least one step.")
            step_ids: set[str] = set()
            steps: list[StepDefinition] = []
            for step_index, raw_step in enumerate(raw_steps):
                if not isinstance(raw_step, Mapping):
                    raise WorkflowConfigError(
                        f"Step #{step_index + 1} in phase {phase_id} must be a mapping."
                    )
                step_id = _required_id(
                    raw_step.get("id"), f"step #{step_index + 1} in phase {phase_id}"
                )
                _reject_unknown_keys(
                    raw_step,
                    {
                        "id", "name", "kind", "owner", "source", "gate",
                        "attest", "depends_on", "maps_to", "approval_command",
                        "write_command", "write_precondition", "repeatable",
                        "pollable", "refresh_invalidation", "effect_mode",
                        "effect_recovery", "effect_retry", "implementation",
                    },
                    f"step {phase_id}.{step_id}",
                )
                if step_id in step_ids:
                    raise WorkflowConfigError(
                        f"Duplicate step id in phase {phase_id}: {step_id}"
                    )
                step_ids.add(step_id)
                kind = _classify_step(phase_id, step_id, raw_step)
                try:
                    implementation = StepImplementation(
                        raw_step.get("implementation", StepImplementation.HANDLER.value)
                    )
                except (TypeError, ValueError) as exc:
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} has invalid implementation; "
                        "expected handler or dummy."
                    ) from exc
                owner = (
                    "human"
                    if kind
                    in (
                        StepKind.HUMAN_ACTION,
                        StepKind.ATTESTATION,
                        StepKind.APPROVAL_GATE,
                    )
                    else "agent"
                )
                source = "scout" if kind == StepKind.EXTERNAL else None
                _validate_compatibility_metadata(
                    phase_id, step_id, raw_step, kind, owner, source
                )
                approval_command = raw_step.get("approval_command")
                if approval_command is not None:
                    if kind != StepKind.APPROVAL_GATE:
                        raise WorkflowConfigError(
                            f"Only approval gates may declare approval_command: "
                            f"{phase_id}.{step_id}"
                        )
                    if (
                        not isinstance(approval_command, str)
                        or not re.fullmatch(r"[a-z][a-z0-9-]*", approval_command)
                    ):
                        raise WorkflowConfigError(
                            f"Step {phase_id}.{step_id} has invalid approval_command."
                        )
                    if approval_command not in APPROVAL_COMMANDS:
                        raise WorkflowConfigError(
                            f"Step {phase_id}.{step_id} uses unregistered "
                            f"approval_command {approval_command!r}."
                        )
                repeatable = raw_step.get("repeatable", False)
                if not isinstance(repeatable, bool):
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} repeatable must be a boolean."
                    )
                if repeatable and kind != StepKind.EXTERNAL:
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} repeatable is supported only for "
                        "external steps."
                    )
                refresh_invalidation = raw_step.get(
                    "refresh_invalidation", "status")
                if refresh_invalidation not in ("status", "always", "never"):
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} has invalid "
                        "refresh_invalidation."
                    )
                if not repeatable and "refresh_invalidation" in raw_step:
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} cannot configure refresh "
                        "invalidation unless repeatable."
                    )
                pollable = raw_step.get("pollable", False)
                if not isinstance(pollable, bool):
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} pollable must be a boolean."
                    )
                if pollable and kind != StepKind.EXTERNAL:
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} pollable is supported only for "
                        "external steps."
                    )
                write_command = raw_step.get("write_command")
                if write_command is not None:
                    if kind != StepKind.EXTERNAL:
                        raise WorkflowConfigError(
                            f"Only external steps may declare write_command: "
                            f"{phase_id}.{step_id}"
                        )
                    if write_command not in EXTERNAL_WRITE_COMMANDS:
                        raise WorkflowConfigError(
                            f"Step {phase_id}.{step_id} uses unregistered "
                            f"write_command {write_command!r}."
                        )
                write_precondition = raw_step.get("write_precondition", "handler")
                if write_precondition not in ("handler", "command"):
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} has invalid write_precondition."
                    )
                if write_precondition == "command" and not write_command:
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} requires write_command when "
                        "write_precondition is command."
                    )
                raw_effect_mode = raw_step.get("effect_mode")
                if kind == StepKind.AUTO:
                    try:
                        effect_mode = EffectMode(
                            EffectMode.READ_ONLY.value
                            if raw_effect_mode is None
                            else raw_effect_mode
                        )
                    except ValueError as exc:
                        allowed = ", ".join(mode.value for mode in EffectMode)
                        raise WorkflowConfigError(
                            f"Step {phase_id}.{step_id} has invalid effect_mode "
                            f"{raw_effect_mode!r}; expected one of: {allowed}."
                        ) from exc
                else:
                    if raw_effect_mode is not None:
                        raise WorkflowConfigError(
                            f"Only auto steps may declare effect_mode: "
                            f"{phase_id}.{step_id}"
                        )
                    effect_mode = None
                if implementation == StepImplementation.DUMMY and (
                    kind != StepKind.AUTO or effect_mode != EffectMode.READ_ONLY
                ):
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} dummy implementation requires "
                        "a read_only auto step."
                    )
                raw_effect_recovery = raw_step.get("effect_recovery")
                if effect_mode and effect_mode.writes_external_state:
                    try:
                        effect_recovery = EffectRecovery(raw_effect_recovery)
                    except (TypeError, ValueError) as exc:
                        allowed = ", ".join(mode.value for mode in EffectRecovery)
                        raise WorkflowConfigError(
                            f"Effectful auto step {phase_id}.{step_id} requires "
                            f"effect_recovery ({allowed})."
                        ) from exc
                else:
                    if raw_effect_recovery is not None:
                        raise WorkflowConfigError(
                            f"Only effectful auto steps may declare effect_recovery: "
                            f"{phase_id}.{step_id}"
                        )
                    effect_recovery = None
                effect_retry = raw_step.get("effect_retry", False)
                if not isinstance(effect_retry, bool):
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} effect_retry must be a boolean."
                    )
                if "effect_retry" in raw_step and (
                    kind != StepKind.AUTO
                    or effect_mode != EffectMode.TRANSACTIONAL
                ):
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} effect_retry requires a "
                        "transactional auto step."
                    )
                step_name = _required_name(
                    raw_step.get("name"), f"step {phase_id}.{step_id}"
                )
                dependencies = raw_step.get("depends_on", [])
                if not isinstance(dependencies, list) or any(
                    not isinstance(dep, str) or not dep.strip() or dep != dep.strip()
                    for dep in dependencies
                ):
                    raise WorkflowConfigError(
                        f"Step {phase_id}.{step_id} has invalid depends_on."
                    )
                step = StepDefinition(
                    phase_id=phase_id,
                    id=step_id,
                    name=step_name,
                    kind=kind,
                    implementation=implementation,
                    owner=owner,
                    source=source,
                    approval_command=approval_command,
                    write_command=write_command,
                    write_precondition=write_precondition,
                    repeatable=repeatable,
                    pollable=pollable,
                    refresh_invalidation=refresh_invalidation,
                    effect_mode=effect_mode,
                    effect_recovery=effect_recovery,
                    effect_retry=effect_retry,
                    depends_on=tuple(dependencies),
                    raw=_freeze(raw_step),
                )
                steps.append(step)
                step_by_key[step.key] = step

            _validate_dependencies(phase_id, execution, steps)
            phases.append(
                PhaseDefinition(
                    id=phase_id,
                    name=phase_name,
                    execution=execution,
                    anchor=anchor,
                    conditional=conditional,
                    steps=tuple(steps),
                    raw=_freeze(raw_phase),
                )
            )

        phase_by_id = {phase.id: phase for phase in phases}
        return cls(
            version=int(config.get("version", 1)),
            phases=tuple(phases),
            phase_by_id=MappingProxyType(phase_by_id),
            step_by_key=MappingProxyType(step_by_key),
            fingerprint=workflow_fingerprint(config),
        )

    def phase(self, phase_id: str) -> Optional[PhaseDefinition]:
        return self.phase_by_id.get(phase_id)

    def step(self, phase_id: str, step_id: str) -> Optional[StepDefinition]:
        return self.step_by_key.get(f"{phase_id}.{step_id}")

    def invalidation_closure(
        self, phase_id: str, step_id: str
    ) -> tuple[StepDefinition, ...]:
        """Target plus every same/later workflow step whose result can depend on it."""
        phase = self.phase(phase_id)
        target = self.step(phase_id, step_id)
        if not phase or not target:
            return ()
        phase_index = next(
            index for index, candidate in enumerate(self.phases)
            if candidate.id == phase_id
        )
        affected = {target.key}
        changed = True
        while changed:
            changed = False
            for candidate in phase.steps:
                if candidate.key in affected:
                    continue
                if phase.execution == "sequential":
                    target_index = next(
                        index for index, item in enumerate(phase.steps)
                        if item.id == step_id
                    )
                    dependent = next(
                        index for index, item in enumerate(phase.steps)
                        if item.id == candidate.id
                    ) > target_index
                else:
                    dependent = any(
                        f"{phase_id}.{dependency}" in affected
                        for dependency in candidate.depends_on
                    )
                if dependent:
                    affected.add(candidate.key)
                    changed = True
        for later in self.phases[phase_index + 1:]:
            affected.update(step.key for step in later.steps)
        return tuple(
            step
            for phase_def in self.phases
            for step in phase_def.steps
            if step.key in affected
        )

    def steps_from_phase(self, phase_id: str) -> tuple[StepDefinition, ...]:
        phase_index = next(
            (
                index
                for index, phase in enumerate(self.phases)
                if phase.id == phase_id
            ),
            None,
        )
        if phase_index is None:
            return ()
        return tuple(
            step
            for phase in self.phases[phase_index:]
            for step in phase.steps
        )


def workflow_fingerprint(config: Mapping[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    """Copy metadata into recursively read-only containers."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return deepcopy(value)


def _required_id(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowConfigError(f"{context} requires a non-empty string id.")
    if value != value.strip():
        raise WorkflowConfigError(f"{context} id cannot contain surrounding whitespace.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise WorkflowConfigError(
            f"{context} id {value!r} must contain only letters, numbers, '_' or '-'."
        )
    return value


def _required_name(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowConfigError(f"{context} requires a non-empty string name.")
    return value


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: set[str], context: str
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise WorkflowConfigError(
            f"Unknown {context} field(s): {', '.join(sorted(unknown))}"
        )


def _classify_step(
    phase_id: str, step_id: str, raw_step: Mapping[str, Any]
) -> StepKind:
    key = f"{phase_id}.{step_id}"
    value = raw_step.get("kind")
    if not isinstance(value, str):
        raise WorkflowConfigError(f"Step {key} requires an explicit kind.")
    try:
        return StepKind(value)
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in StepKind)
        raise WorkflowConfigError(
            f"Step {key} has invalid kind {value!r}; expected one of: {allowed}."
        ) from exc


def _validate_compatibility_metadata(
    phase_id: str,
    step_id: str,
    raw_step: Mapping[str, Any],
    kind: StepKind,
    owner: str,
    source: Optional[str],
) -> None:
    """Reject legacy metadata that contradicts the explicit kind."""
    key = f"{phase_id}.{step_id}"
    if "owner" in raw_step and raw_step["owner"] != owner:
        raise WorkflowConfigError(
            f"Step {key} owner contradicts kind {kind.value!r}; expected {owner!r}."
        )
    if "source" in raw_step and raw_step["source"] != source:
        raise WorkflowConfigError(
            f"Step {key} source contradicts kind {kind.value!r}; expected {source!r}."
        )
    expected_gate = kind == StepKind.APPROVAL_GATE
    if "gate" in raw_step and raw_step["gate"] is not expected_gate:
        raise WorkflowConfigError(
            f"Step {key} gate contradicts kind {kind.value!r}."
        )
    expected_attest = kind == StepKind.ATTESTATION
    if "attest" in raw_step and raw_step["attest"] is not expected_attest:
        raise WorkflowConfigError(
            f"Step {key} attest contradicts kind {kind.value!r}."
        )


def _validate_dependencies(
    phase_id: str, execution: str, steps: list[StepDefinition]
) -> None:
    by_id = {step.id: step for step in steps}
    positions = {step.id: index for index, step in enumerate(steps)}
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in by_id:
                raise WorkflowConfigError(
                    f"Step {step.key} depends on unknown step {phase_id}.{dependency}."
                )
            if dependency == step.id:
                raise WorkflowConfigError(f"Step {step.key} cannot depend on itself.")
            if execution == "sequential" and positions[dependency] > positions[step.id]:
                raise WorkflowConfigError(
                    f"Sequential step {step.key} cannot depend on later step "
                    f"{phase_id}.{dependency}."
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str, path: tuple[str, ...]) -> None:
        if step_id in visiting:
            cycle = " -> ".join((*path, step_id))
            raise WorkflowConfigError(f"Dependency cycle in phase {phase_id}: {cycle}")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in by_id[step_id].depends_on:
            visit(dependency, (*path, step_id))
        visiting.remove(step_id)
        visited.add(step_id)

    for step in steps:
        visit(step.id, ())
