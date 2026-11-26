"""Monitor final orchestration and capture the final MRWP identity."""
from __future__ import annotations

from datetime import timedelta

from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.authority import OwnStepData, PipelineScope, PipelineSlot
from orchestrator.evidence import PipelineEvidence, StepData
from orchestrator.step_context import StepContext, thaw
from orchestrator.step_context import MISSING
from tools import pipelines as P

ID = "orchestrator_finalization"
KIND = "agent"
NAME = "Monitor Release Orchestrator to 'Publish GitHub Release Notes'"
REQUIRED_TOOLS = ('az',)
EFFECT_MODE = "read_only"
RECHECK_WHEN_BLOCKED = True
EVIDENCE = (OwnStepData(), PipelineScope(PipelineSlot.FINAL))

STAGE_ID = "PublishGitHubReleaseNotes"
STAGE_NAME = "Publish GitHub Release Notes"
POLL_INTERVAL_MIN = 120
ESCALATE_AFTER_HOURS = 8

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

def _poll_due(context):
    from orchestrator import schedule
    record = context.evidence.step("finalize", ID)
    if record.status != "in_flight":
        return True
    last = schedule.parse_iso_utc(record.data.get("last_polled_at"))
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
    orch_run_id = (final or {}).get("orchestrator_run_id") or ((context.evidence.pipeline_runs.get("orchestrator") or {}).get("run_id"))
    mrwp_run_id = (final or {}).get("mrwp_run_id")
    if orch_run_id:
        data["run_id"] = str(orch_run_id)
    if mrwp_run_id:
        data["mrwp_run_id"] = str(mrwp_run_id)
    links = []
    if str(orch_run_id or "").isdigit():
        links.append({
            "name": f"Release Orchestrator #{orch_run_id}",
            "url": f"{P.ENGINEERING_ORG.rstrip('/')}/{P.ENGINEERING_PROJECT}/_build/results?buildId={orch_run_id}",
        })
    if str(mrwp_run_id or "").isdigit():
        links.append({
            "name": f"Final1 MRWP #{mrwp_run_id}",
            "url": f"{P.ENGINEERING_ORG.rstrip('/')}/{P.ENGINEERING_PROJECT}/_build/results?buildId={mrwp_run_id}",
        })
    step_update = StepData(data)
    if status == "failed":
        return Blocked(
            f"orchestrator_finalization: final Release Orchestrator processing failed "
            f"({detail}). Investigate the orchestrator before creating integration PRs.",
            links=links,
            updates=(step_update,),
        )
    if status != "ready":
        return InProgress(
            f"orchestrator_finalization: waiting for the Release Orchestrator to park at "
            f"'{STAGE_NAME}' with final build tags ({detail}).",
            poll_in_min=POLL_INTERVAL_MIN,
            links=links,
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
        links=links,
        updates=(step_update, PipelineEvidence(runs)),
    )

KNOWLEDGE = {'summary': 'Monitor final orchestration until the Publish GitHub Release Notes gate.',
 'what': "After 'Remove RC Tags', this read-only step follows the Release Orchestrator through "
         'final MRWP generation and release-integration branch creation until it parks at '
         "'Publish GitHub Release Notes'. It resolves Final1 from the orchestrator tags and "
         'verifies the returned build ID and configured MRWP pipeline definition before '
         'accepting final success. Authenticator build/version capture happens later in '
         'rollout_start.identify_auth_build.\n',
 'trigger': 'Runs after `remove_rc_tags_gate`. `release-drainer` reconciles this step '
            'every 2 hours and binds to the stable YAML stage identifier '
            "`PublishGitHubReleaseNotes`, whose display name is 'Publish GitHub Release "
            "Notes'.\n",
 'who': 'The release-agent performs the read-only Azure Pipelines checks automatically.',
 'how': 'While the target gate or required tags are not ready, the step remains in progress. '
        'After 8 hours it escalates once to the release owner and continues polling. A failed '
        'orchestrator stage or final MRWP blocks Phase 4 for investigation.\n',
 'consequences': 'It stores state.pipeline_runs.final with orchestrator_run_id, mrwp_run_id, '
                 'and resolved_at. Authenticator build/version capture happens later in Phase '
                 '5 (`rollout_start.identify_auth_build`). It cannot approve the pipeline, '
                 'create branches, modify repositories or open PRs.\n'}
