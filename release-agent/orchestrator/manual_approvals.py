"""Read-only provider proof and checked import of approvals made outside Scout."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from uuid import UUID

from . import revision
from .approvals import ApprovalRequest, _timestamp, checkpoint_transition


@dataclass(frozen=True)
class ManualApprovalTarget:
    org: str
    project: str
    build_id: int
    stage: str
    approval_id: str

    def __post_init__(self):
        ApprovalRequest(**asdict(self), comment="")
        identifier = UUID(self.approval_id)
        if not identifier.int or str(identifier) != self.approval_id:
            raise ValueError("Manual import requires an explicit canonical approval UUID")


def target_from_dict(value):
    if not isinstance(value, dict) or set(value) != {f.name for f in fields(ManualApprovalTarget)}:
        raise ValueError("Invalid manual approval target fields")
    return ManualApprovalTarget(**value)


def _build_id(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    raise ValueError("Recorded orchestrator requires a positive build identity")


def configured_target(orch, phase, step, approval_id):
    definition = orch._workflow_definition().step(phase, step)
    return target_for_definition(orch.state, definition, approval_id)


def target_for_definition(state, definition, approval_id):
    # Deliberately bounded to the two supported orchestrator gates, not arbitrary
    # configured gates or discovery of the newest release run.
    from steps.finalize import publish_notes_gate, remove_rc_tags_gate

    modules = {
        ("finalize", remove_rc_tags_gate.ID): remove_rc_tags_gate,
        ("finalize", publish_notes_gate.ID): publish_notes_gate,
    }
    module = modules.get((definition.phase_id, definition.id)) if definition else None
    if (module is None or not definition or not definition.is_gate
            or definition.approval_command != module.APPROVAL_COMMAND):
        raise ValueError("Manual import requires a configured Release Orchestrator gate")
    run = state.pipeline_runs.get("orchestrator")
    if not isinstance(run, Mapping):
        raise ValueError("No recorded Release Orchestrator build; discovery cannot authorize an import")
    return ManualApprovalTarget(
        module.CONFIG["org"], module.CONFIG["project"], _build_id(run.get("run_id")),
        module.STAGE, approval_id,
    )


def checkpoint_path(records, approval_id, expected_stage):
    """Require the SAME approval id and a unique, acyclic parent chain to its stage."""
    if not isinstance(records, list) or not records:
        raise ValueError("Missing or malformed approval timeline")
    by_id = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("Malformed approval timeline record")
        identifier = record.get("id")
        if (not isinstance(identifier, str) or not identifier.strip()
                or identifier in by_id):
            raise ValueError("Missing or ambiguous timeline identity")
        by_id[identifier] = record
    current = by_id.get(approval_id)
    if current is None or current.get("type") != "Checkpoint.Approval":
        raise ValueError("No same-ID approval checkpoint in the recorded build timeline")
    path, seen = [], set()
    while True:
        identifier = current["id"]
        if identifier in seen:
            raise ValueError("Cyclic approval checkpoint ancestry")
        seen.add(identifier)
        kind, name, parent = current.get("type"), current.get("name"), current.get("parentId")
        if (not isinstance(kind, str) or not kind
                or (name is not None and not isinstance(name, str))
                or (parent is not None and not isinstance(parent, str))):
            raise ValueError("Malformed approval checkpoint ancestry")
        path.append({"id": identifier, "type": kind, "name": name, "parentId": parent})
        if kind == "Stage":
            if name != expected_stage:
                raise ValueError(f"Approval checkpoint belongs to {name!r}, not {expected_stage!r}")
            if sum(r.get("type") == "Stage" and r.get("name") == expected_stage
                   for r in records) != 1:
                raise ValueError("Ambiguous expected stage in approval timeline")
            return path
        current = by_id.get(parent)
        if current is None:
            raise ValueError("Missing approval checkpoint ancestor")


def read_proof(orch, phase, step, target):
    # BUILD contexts expose reads only: neither an approval permit nor a write
    # adapter is constructed anywhere in this import path.
    reads = orch.context(phase, step).services.pipelines
    ok, approval, detail = reads.get_pipeline_approval(
        target.org, target.project, target.approval_id)
    if ok is not True:
        raise ValueError(f"Cannot read exact provider approval: {detail}")
    if (not isinstance(approval, Mapping) or approval.get("id") != target.approval_id
            or approval.get("status") != "approved"):
        raise ValueError("Exact provider approval must already be approved")
    pipeline = approval.get("pipeline")
    owner = pipeline.get("owner") if isinstance(pipeline, Mapping) else None
    if not isinstance(owner, Mapping) or _build_id(owner.get("id")) != target.build_id:
        raise ValueError("Provider approval owner does not match the recorded orchestrator build")
    if "_links" in owner:
        from tools.pipelines import approval_owner_build_id
        if approval_owner_build_id(approval) != target.build_id:
            raise ValueError("Provider approval owner link contradicts its recorded build")
    ok, records, detail = reads.get_timeline(target.org, target.project, target.build_id)
    if ok is not True:
        raise ValueError(f"Cannot read recorded build timeline: {detail}")
    return {
        "approval_id": target.approval_id, "status": "approved", "owner_build_id": target.build_id,
        "checkpoint_path": checkpoint_path(records, target.approval_id, target.stage),
    }


def validate_evidence(value):
    if not isinstance(value, dict) or set(value) != {
            "provenance", "release", "step", "generation", "workflow_revision",
            "target", "proof", "review_hash", "recorded_by", "recorded_at"}:
        raise ValueError("Invalid manual approval evidence fields")
    if value["provenance"] != "external_manual":
        raise ValueError("Invalid manual approval provenance")
    target = target_from_dict(value["target"])
    for key in ("release", "step", "generation", "recorded_by"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"Manual approval requires {key}")
    if value["generation"] != "initial":
        _timestamp(value["generation"])
    _timestamp(value["recorded_at"])
    if not revision.is_hash(value["workflow_revision"]) or not revision.is_hash(value["review_hash"]):
        raise ValueError("Manual approval requires workflow and review hashes")
    proof = value["proof"]
    if (not isinstance(proof, dict) or set(proof) != {
            "approval_id", "status", "owner_build_id", "checkpoint_path"}
            or proof["approval_id"] != target.approval_id or proof["status"] != "approved"
            or type(proof["owner_build_id"]) is not int or proof["owner_build_id"] != target.build_id):
        raise ValueError("Invalid exact manual provider approval proof")
    path = proof["checkpoint_path"]
    if (not isinstance(path, list)
            or any(not isinstance(r, dict) or set(r) != {"id", "type", "name", "parentId"} for r in path)
            or checkpoint_path(path, target.approval_id, target.stage) != path):
        raise ValueError("Invalid manual approval checkpoint path")
    if value["review_hash"] != evidence_hash(value):
        raise ValueError("Manual approval review hash does not match its evidence")
    return target


def evidence_hash(value):
    return revision.digest({
        "operation": "record-orchestrator-approval",
        **{key: value[key] for key in (
            "release", "step", "generation", "workflow_revision", "target", "proof")},
    })


def _guard(orch, phase, step):
    revision.assert_current(orch)
    if orch.mocks.get(f"{phase}.{step}"):
        raise ValueError("Manual import rejects nonempty gate mocks; inject provider read ports in tests")
    if rejected := orch._transition_kernel().validate_manual_approval_start(phase, step):
        raise ValueError(rejected.message)


def preview(orch, phase, step, *, approval_id):
    _guard(orch, phase, step)
    target = configured_target(orch, phase, step, approval_id)
    generation = orch.state.get_step(phase, step).invalidated_at or "initial"
    binding = revision.revision_id(orch.state.workflow_revision)
    proof = read_proof(orch, phase, step, target)
    _guard(orch, phase, step)
    if (configured_target(orch, phase, step, approval_id) != target
            or (orch.state.get_step(phase, step).invalidated_at or "initial") != generation
            or revision.revision_id(orch.state.workflow_revision) != binding):
        raise ValueError("Manual approval target/workflow/generation changed during provider reads")
    body = {
        "release": orch.state.release_id, "step": f"{phase}.{step}", "generation": generation,
        "workflow_revision": binding, "target": asdict(target), "proof": proof,
    }
    return {**body, "review_hash": evidence_hash(body), "permission_to_execute": False,
            "impact": {"gate": f"{phase}.{step}", "provider_writes": False, "advance_downstream": False}}


def execute(orch, phase, step, *, approval_id, review_hash, recorded_by):
    if not isinstance(recorded_by, str) or not recorded_by.strip():
        raise ValueError("Manual import requires --recorded-by (the importer, not the provider approver)")
    prepared = preview(orch, phase, step, approval_id=approval_id)
    if review_hash != prepared["review_hash"]:
        raise ValueError("An exact current --review-hash is required; preview the manual import first")
    evidence = {
        **{key: deepcopy(prepared[key]) for key in (
            "release", "step", "generation", "workflow_revision", "target", "proof", "review_hash")},
        "provenance": "external_manual", "recorded_by": recorded_by.strip(),
        "recorded_at": orch._transition_kernel().now(),
    }
    record = orch.state.get_step(phase, step)
    if "last_approval" in record.data:
        previous = record.data["last_approval"]
        if previous["review_hash"] != review_hash or previous["recorded_by"] != evidence["recorded_by"]:
            raise ValueError("Existing manual approval differs; it will not be overwritten")
        return {"status": "already_recorded", "permission_to_execute": False}
    transition = checkpoint_transition(
        orch, lambda: orch._transition_kernel().record_manual_approval(phase, step, evidence))
    return {"status": "recorded", "message": transition.message, "permission_to_execute": False,
            "advance_downstream": False}
