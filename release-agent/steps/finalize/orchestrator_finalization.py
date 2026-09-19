"""Monitor final orchestration and capture the final MRWP identity."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.authority import OwnStepData, PipelineScope, PipelineSlot
from orchestrator.evidence import PipelineEvidence, StepData
from orchestrator.step_context import StepContext, thaw
from steps.lib.mockctx import MISSING
from tools import pipelines as P


ID = "orchestrator_finalization"
KIND = "agent"
EFFECT_MODE = "read_only"
EVIDENCE = (OwnStepData(), PipelineScope(PipelineSlot.FINAL))

STAGE_ID = "PublishGitHubReleaseNotes"
STAGE_NAME = "Publish GitHub Release Notes"
POLL_INTERVAL_MIN = 120
ESCALATE_AFTER_HOURS = 8

MOCKABLE = {
    "stage": {
        "kind": "input",
        "desc": "Inject the stage state: 'ready'|'wait'|'failed'.",
    },
    "final": {
        "kind": "input",
        "desc": "Inject {mrwp_run_id}.",
    },
}


def _stage_status(context):
    injected = context.input("stage", MISSING)
    if injected is not MISSING:
        value = str(injected).lower()
        if value in ("ready", "completed", "succeeded", "true"):
            final = context.input("final", MISSING)
            if not isinstance(final, dict):
                return "wait", "injected ready stage has no final output tags", {}
            required = ("mrwp_run_id",)
            missing = [key for key in required if not final.get(key)]
            if missing:
                return "wait", f"injected final output is missing {', '.join(missing)}", {}
            return "ready", f"injected stage={injected}", {
                "orchestrator_run_id": str(final.get("orchestrator_run_id") or "test"),
                **{key: str(final[key]) for key in required},
            }
        if value in ("failed", "canceled", "cancelled"):
            return "failed", f"injected stage={injected}", {}
        return "wait", f"injected stage={injected}", {}

    ok, status, detail = context.services.pipelines.orchestrator_finalization_status(
        P.ENGINEERING_ORG,
        P.ENGINEERING_PROJECT,
        context.release.release_id,
        STAGE_ID,
    )
    if not ok:
        return "unknown", detail, {}
    status = status or {}
    return status.get("status", "wait"), detail, status


def _parse_iso(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _poll_due(context):
    record = context.evidence.step("finalize", ID)
    if record.status != "in_flight":
        return True
    last = _parse_iso(record.data.get("last_polled_at"))
    return last is None or context.clock.utc() >= last + timedelta(minutes=POLL_INTERVAL_MIN)


def build(context: StepContext):
    if not _poll_due(context):
        return InProgress(
            context.evidence.step("finalize", ID).note
            or f"Waiting for the next {POLL_INTERVAL_MIN // 60}-hour orchestrator poll.",
            poll_in_min=POLL_INTERVAL_MIN,
        )

    status, detail, final = _stage_status(context)
    data = thaw(context.evidence.step("finalize", ID).data)
    data["last_polled_at"] = context.clock.iso()
    step_update = StepData(data)
    if status == "failed":
        return Blocked(
            f"orchestrator_finalization: final Release Orchestrator processing failed "
            f"({detail}). Investigate the orchestrator before creating integration PRs.",
            updates=(step_update,),
        )
    if status != "ready":
        return InProgress(
            f"orchestrator_finalization: waiting for the Release Orchestrator to park at "
            f"'{STAGE_NAME}' with final build tags ({detail}).",
            poll_in_min=POLL_INTERVAL_MIN,
            updates=(step_update,),
        )
    runs = thaw(context.evidence.pipeline_runs)
    runs["final"] = {
        "orchestrator_run_id": final["orchestrator_run_id"],
        "mrwp_run_id": final["mrwp_run_id"],
        "resolved_at": context.clock.iso(),
    }
    return Done(
        f"Release Orchestrator is parked at '{STAGE_NAME}'; captured final MRWP "
        f"{final['mrwp_run_id']}.",
        updates=(step_update, PipelineEvidence(runs)),
    )


def automation_prompt(release: str, spec: dict) -> str:
    return (
        f"Release {release} — final Release Orchestrator poller (Phase 4, every "
        f"{spec['interval']}).\n"
        f"After {ESCALATE_AFTER_HOURS} hours without reaching the gate, escalate once to the "
        "release owner and keep polling.\n"
        f"Run `poll-orchestrator-finalization --release {release}` once and act on its decision:\n"
        "  • idle / waiting → send nothing.\n"
        "  • escalate → use source pending and the shared claim/result protocol for every "
        "offered owner notification; never send raw escalation payloads.\n"
        "  • resolved → report the captured final MRWP; "
        "the cleanup planner removes this worker.\n"
        "  • blocked → surface the pipeline failure to the owner and retain the evidence.\n"
        f"Silently journal: `journal --release {release} --source scout --kind automation "
        f"--text \"finalize-orchestrator-poller: <decision>\"`.")
