"""Prepare and reconcile exact Release Orchestrator gate identities.

Preparation may discover a pending gate, but freezes its entire provider target
before core authorizes the one writer invocation. Recovery only reads that target:
neither a newer release run nor a completed stage can prove this approval succeeded.
In directly injected unit contexts, approval_state accepts the same exact-id/status/
owner payload as the read port, including None for missing evidence; it never enables
a write. Production rejects nonempty gate mocks for every approval lifecycle hook.
The legacy submit='skip' marker also blocks preparation; offline tests inject ports.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from orchestrator.approvals import ApprovalRequest
from orchestrator.outcomes import Blocked
from steps.lib.mockctx import MISSING
from tools import pipelines as P


def prepare_expected_approval(
    context,
    *,
    pending: Callable,
    config: dict,
    expected_stage: str,
    comment: str,
    default_comment: str,
) -> ApprovalRequest | Blocked:
    """Freeze only an identified pending approval for this exact stage; never submit."""
    if str(context.input("submit", "")).lower() == "skip":
        return Blocked(
            "submit='skip' cannot authorize an external approval or fabricate a receipt. "
            "Remove gate mocks for production; inject fake provider ports for offline tests."
        )
    ok, info, detail = pending(context)
    if not ok:
        return Blocked(
            f"Couldn't locate the orchestrator approval ({detail}). "
            "Inspect the ADO gate and retry preparation once its identity is visible."
        )
    if not info:
        return Blocked(
            f"No identifiable pending approval for '{expected_stage}'. Inspect the ADO gate "
            "and retry once it is parked. Stage completion alone cannot identify an approval."
        )
    if not isinstance(info, Mapping):
        return Blocked("Malformed pending approval data; inspect the ADO gate before retrying.")
    actual_stage = info.get("stage")
    if actual_stage != expected_stage:
        return Blocked(
            f"The orchestrator is parked at '{actual_stage}', not '{expected_stage}' — "
            "NOT approving. Resolve that gate first."
        )
    build_id, approval_id = info.get("build_id"), info.get("approval_id")
    if (type(build_id) is not int or build_id <= 0
            or not isinstance(approval_id, str) or not approval_id.strip()
            or approval_id != approval_id.strip()):
        return Blocked(
            "Pending approval has no valid build/approval identity. "
            "Inspect the ADO gate; no approval id will be fabricated."
        )
    try:
        return ApprovalRequest(
            org=config["org"], project=config["project"], build_id=build_id,
            stage=expected_stage, approval_id=approval_id, comment=comment or default_comment,
        )
    except (TypeError, ValueError) as error:
        return Blocked(f"Invalid approval request ({error}); correct the gate inputs and retry.")


def reconcile_expected_approval(context, *, expected_stage: str) -> tuple[bool, str]:
    """Confirm only approved evidence for the frozen approval and its exact build.

    Pending, rejected, canceled, missing, unknown, or malformed evidence retains
    the hold. This hook has no submit path, even if a caller supplies a writer.
    """
    approval_context = context.approval
    if approval_context is None:
        return False, "No frozen approval request is available; retain the gate hold."
    request = approval_context.request
    if request.stage != expected_stage:
        return False, f"Frozen approval targets '{request.stage}', not '{expected_stage}'; retain the hold."
    injected = context.input("approval_state", MISSING)
    if injected is MISSING:
        ok, approval, detail = context.services.pipelines.get_pipeline_approval(
            request.org, request.project, request.approval_id,
        )
    else:
        ok, approval, detail = True, injected, ""
    if not ok:
        return False, f"Cannot verify approval {request.approval_id} ({detail}); retain the hold."
    if not isinstance(approval, Mapping):
        return False, f"Approval {request.approval_id} is missing or malformed; retain the hold."
    if approval.get("id") != request.approval_id:
        return False, f"Approval identity does not match {request.approval_id}; retain the hold."
    owner_build = P.approval_owner_build_id(approval)
    if owner_build != request.build_id:
        return (
            False,
            f"Approval {request.approval_id} owner build is {owner_build!r}, "
            f"not {request.build_id}; retain the hold.",
        )
    status = approval.get("status")
    if status != "approved":
        return False, f"Approval {request.approval_id} status is {status!r}, not 'approved'; retain the hold."
    return (
        True,
        f"Confirmed approval {request.approval_id} for '{request.stage}' "
        f"on Release Orchestrator build {request.build_id} is approved.",
    )
