"""Identify the final Authenticator build from pipeline 475778 on the release branch."""
from __future__ import annotations

from orchestrator.authority import PipelineScope, PipelineSlot
from orchestrator.evidence import PipelineEvidence
from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.step_context import StepContext, thaw
from steps.lib.mockctx import MISSING
from tools.pipelines import auth_build_url

ID = "identify_auth_build"
KIND = "agent"
EFFECT_MODE = "read_only"
EVIDENCE = (PipelineScope(PipelineSlot.FINAL_AUTH),)

MOCKABLE = {
    "build": {
        "kind": "input",
        "desc": "Inject final Authenticator build {build_id, version, commit, build_number, status, result}.",
    },
}


def _branch(context):
    return (getattr(context.release, "versions", None) or {}).get("authenticator")


def _build_info(context):
    injected = context.input("build", MISSING)
    if injected is not MISSING:
        return injected, ""
    branch = _branch(context)
    if not branch:
        return None, "no Authenticator release branch on state.versions"
    ok, info, detail = context.services.pipelines.find_final_auth_build(branch)
    if not ok:
        return None, detail
    return info, detail


def build(context: StepContext):
    info, detail = _build_info(context)
    branch = _branch(context)
    if not info:
        return Blocked(
            "identify_auth_build: no final Authenticator build found for "
            f"{branch or 'the release branch'} in pipeline 475778 ({detail}). "
            "Investigate the release branch/build; the release digest will notify the owner "
            "by email and Scout."
        )
    if str(info.get("status") or "").lower() != "completed":
        return InProgress(
            f"identify_auth_build: final Authenticator build {info.get('build_id')} is "
            f"{info.get('status') or 'not completed'}; Scout will keep checking.",
            poll_in_min=30,
        )
    if str(info.get("result") or "").lower() not in ("succeeded", "partiallysucceeded"):
        return Blocked(
            f"identify_auth_build: final Authenticator build {info.get('build_id')} completed "
            f"with result={info.get('result') or 'unknown'}; investigate before rollout."
        )
    if not info.get("build_id") or not info.get("version") or not info.get("commit"):
        return Blocked("identify_auth_build: final Authenticator build evidence is incomplete.")
    runs = thaw(context.evidence.pipeline_runs)
    final_auth = dict(runs.get("final_auth") or {})
    final_auth.update(
        authenticator_build_id=str(info["build_id"]),
        authenticator_version=str(info["version"]),
        authenticator_commit=str(info["commit"]),
        authenticator_build_number=str(info.get("build_number") or ""),
        authenticator_resolved_at=context.clock.iso(),
    )
    runs["final_auth"] = final_auth
    url = auth_build_url(info["build_id"])
    return Done(
        f"Identified final Authenticator build {info['build_id']} "
        f"({info['version']}) on {branch}.",
        links=[{"name": "Final Authenticator build", "url": url}],
        updates=(PipelineEvidence(runs),),
    )
