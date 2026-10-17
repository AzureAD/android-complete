"""Step: `orchestrator_health` — verify the Release Orchestrator is healthy and parked
at the approval gate (Phase 2, build_verify).

Finds the orchestrator run (def 2828) for the release via its self-tag
`AuthenticatorBranch=release-YYYY-MM-*`, confirms the pre-gate stages
(Validate / Create Branches / Trigger RC Testing) all succeeded, and that it is
PARKED at "Remove RC Tags" (a manual approval the owner clears in a LATER phase — this
step never approves it). Reports the RC versions. Read-only (`az`) → `agent` step.

Blocks if run/stage/version evidence is missing, malformed or failed. Pending
Remove RC Tags is expected; an already completed successful park stage is
reported explicitly without claiming that it is still awaiting approval.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext, thaw

from orchestrator.outcomes import Done, Blocked
from orchestrator import schedule
from orchestrator.evidence import ReleaseVersions
from steps.lib.mockctx import MISSING
from steps.build_verify import _common as K
from tools.pipelines import ORCHESTRATOR_DEF, ORCH_REQUIRED_STAGES, ORCH_PARK_STAGE

from orchestrator.authority import PipelineScope, PipelineSlot, VersionEvidence

EVIDENCE = (PipelineScope(PipelineSlot.ORCHESTRATOR), VersionEvidence())
ID = "orchestrator_health"
KIND = "agent"
EFFECT_MODE = "read_only"

CONFIG = {
    "org": K.ORG, "project": K.PROJECT, "def_id": ORCHESTRATOR_DEF,
    "required_stages": ORCH_REQUIRED_STAGES,
    "park_stage": ORCH_PARK_STAGE,
}

MOCKABLE = {
    "run": {"kind": "input",
            "desc": "Inject the orchestrator run dict {id,tags,...} to skip the tag lookup."},
    "stages": {"kind": "input",
               "desc": "Inject the orchestrator stage list [{name,state,result}] to skip the timeline read."},
}


def build(context: StepContext):
    cfg = CONFIG
    month = context.release.release_id
    from tools import pipelines as P

    run = context.input("run", MISSING)
    if run is MISSING:
        ok, run, detail = context.services.pipelines.find_orchestrator_run(cfg["org"], cfg["project"], cfg["def_id"], month)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"orchestrator_health: could not read orchestrator runs ({detail}){hint}.")
    if not run:
        return Blocked(
            f"No Release Orchestrator run found for {month} (no run tagged "
            f"AuthenticatorBranch=release-{month}-*). The release may not have started, "
            f"or the run isn't tagged. Check def {cfg['def_id']}.")

    if not isinstance(run, dict) or not K.valid_id(run.get("id")):
        return Blocked("orchestrator_health: run evidence has no valid build identity")
    bid = run["id"]
    tags = run.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        return Blocked("orchestrator_health: run tags must be an array of text")
    links = K.links_for(bid, "Release Orchestrator run")
    versions = {k: P._tag_value(tags, f"Next{k}Version")
                for k in ("Common", "Msal", "Broker")}
    vstr = P.format_versions(versions, fallback="versions n/a")
    # Authenticator has no Next*Version tag — its release BRANCH is carried on the
    # AuthenticatorBranch tag as 'release-YYYY-MM-DD'; store it as 'release/YYYY/MM/DD'
    # (the actual app version is resolved later and isn't needed here).
    auth_tag = P._tag_value(tags, "AuthenticatorBranch")
    auth_branch = auth_tag.replace("-", "/") if auth_tag else None

    stages = context.input("stages", MISSING)
    if stages is MISSING:
        ok, stages, detail = context.services.pipelines.get_stages(cfg["org"], cfg["project"], bid)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"orchestrator_health: could not read orchestrator stages ({detail}){hint}.", links=links)
    if not isinstance(stages, list) or any(
            not isinstance(stage, dict) or not isinstance(stage.get("name"), str) for stage in stages):
        return Blocked("orchestrator_health: malformed stage evidence", links=links)
    by_name = {}
    relevant = set(cfg["required_stages"]) | {cfg["park_stage"]}
    for stage in stages:
        name = stage["name"]
        if name in relevant and name in by_name:
            return Blocked(f"orchestrator_health: ambiguous duplicate stage '{name}'", links=links)
        by_name[name] = stage

    # Required pre-gate stages must all be green.
    for name in cfg["required_stages"]:
        st = by_name.get(name)
        if st is None:
            return Blocked(
                f"Release Orchestrator run {bid} is missing the '{name}' stage — "
                f"the run may have aborted early.{K.UNBLOCK_HELP}", links=links)
        if st.get("state") != "completed" or st.get("result") != "succeeded":
            return Blocked(
                f"Release Orchestrator stage '{name}' did not succeed "
                f"(state={st.get('state')}, result={st.get('result')}) in run {bid}. "
                f"RC testing can't be trusted until this is green.{K.UNBLOCK_HELP}", links=links)

    # The park stage should be PENDING (waiting for the owner). If it already ran, the
    # gate was approved — surface it (out of the expected Phase-2 state), don't hard-fail.
    park = by_name.get(cfg["park_stage"])
    if park is None:
        return Blocked(
            f"Release Orchestrator run {bid} is missing the '{cfg['park_stage']}' stage; "
            "cannot verify that it is parked.", links=links)
    if park.get("state") not in ("pending", "completed"):
        return Blocked(
            f"Release Orchestrator '{cfg['park_stage']}' has unverified park state "
            f"{park.get('state')!r}.", links=links)
    if park.get("state") == "pending" and park.get("result") is not None:
        return Blocked("orchestrator_health: pending park stage has contradictory result evidence",
                       links=links)
    park_done = bool(park is not None and park.get("state") == "completed")
    if park_done and park.get("result") not in ("succeeded", "succeededWithIssues"):
        return Blocked(
            f"Release Orchestrator '{cfg['park_stage']}' completed with result "
            f"{park.get('result')!r}; inspect the run.", links=links)
    required_tags = ("NextCommonVersion", "NextMsalVersion", "NextBrokerVersion", "AuthenticatorBranch")
    for key in required_tags:
        values = [tag[len(key) + 1:] for tag in tags if tag.startswith(key + "=")]
        if len(values) != 1 or not values[0].strip() or values[0] != values[0].strip():
            return Blocked(f"orchestrator_health: missing or ambiguous '{key}' tag", links=links)
    branch_date = schedule.parse_date(auth_tag[len("release-"):]) if auth_tag.startswith("release-") else None
    if branch_date is None or auth_tag != f"release-{branch_date.isoformat()}":
        return Blocked("orchestrator_health: invalid Authenticator release branch tag", links=links)
    pipeline_update = K.stash_orchestrator(context, bid, parked=not park_done)
    # Canonical release payload versions (lowercase keys) — the earliest authoritative point.
    # Later steps (e.g. release_announcement) read state.versions instead of re-discovering.
    updates = (pipeline_update, ReleaseVersions({
        "common": versions.get("Common"), "msal": versions.get("Msal"),
        "broker": versions.get("Broker"), "authenticator": auth_branch}))
    if park_done:
        return Done(
            f"Release Orchestrator run {bid} healthy ({vstr}); NOTE '{cfg['park_stage']}' "
            f"already ran (result={park.get('result')}) — the approval gate was cleared. "
            f"Pre-gate stages all succeeded.", links=links, updates=updates)

    return Done(
        f"Release Orchestrator run {bid} healthy — Validate / Create Branches / "
        f"Trigger RC Testing all succeeded; parked at '{cfg['park_stage']}' awaiting "
        f"owner approval (cleared in a later phase). {vstr}.", links=links, updates=updates)
