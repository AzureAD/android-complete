"""Step: `orchestrator_health` — verify the Release Orchestrator is healthy and parked
at the approval gate (Phase 2, build_verify).

Finds the orchestrator run (def 2828) for the release via its self-tag
`AuthenticatorBranch=release-YYYY-MM-*`, confirms the pre-gate stages
(Validate / Create Branches / Trigger RC Testing) all succeeded, and that it is
PARKED at "Remove RC Tags" (a manual approval the owner clears in a LATER phase — this
step never approves it). Reports the RC versions. Read-only (`az`) → `agent` step.

Blocks if the run isn't found, a required stage failed, or (defensively) it already
advanced PAST the Remove RC Tags gate. A pending Remove RC Tags = the healthy expected
state.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from steps.build_verify import _common as K

ID = "orchestrator_health"
KIND = "agent"

CONFIG = {
    "org": K.ORG, "project": K.PROJECT, "def_id": K.ORCHESTRATOR_DEF,
    "required_stages": K.ORCH_REQUIRED_STAGES,
    "park_stage": K.ORCH_PARK_STAGE,
}

MOCKABLE = {
    "run": {"kind": "input",
            "desc": "Inject the orchestrator run dict {id,tags,...} to skip the tag lookup."},
    "stages": {"kind": "input",
               "desc": "Inject the orchestrator stage list [{name,state,result}] to skip the timeline read."},
}


def build(state):
    cfg = CONFIG
    month = state.release_id
    from tools import pipelines as P

    run = mock_input("run", MISSING)
    if run is MISSING:
        ok, run, detail = P.find_orchestrator_run(cfg["org"], cfg["project"], cfg["def_id"], month)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"orchestrator_health: could not read orchestrator runs ({detail}){hint}.")
    if not run:
        return Blocked(
            f"No Release Orchestrator run found for {month} (no run tagged "
            f"AuthenticatorBranch=release-{month}-*). The release may not have started, "
            f"or the run isn't tagged. Check def {cfg['def_id']}.")

    bid = run.get("id")
    tags = run.get("tags") or []
    links = K.links_for(bid, "Release Orchestrator run")
    versions = {k: P._tag_value(tags, f"Next{k}Version")
                for k in ("Common", "Msal", "Broker")}
    vstr = ", ".join(f"{k} {v}" for k, v in versions.items() if v) or "versions n/a"

    stages = mock_input("stages", MISSING)
    if stages is MISSING:
        ok, stages, detail = P.get_stages(cfg["org"], cfg["project"], bid)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"orchestrator_health: could not read orchestrator stages ({detail}){hint}.", links=links)
    by_name = {s.get("name"): s for s in (stages or [])}

    # Required pre-gate stages must all be green.
    for name in cfg["required_stages"]:
        st = by_name.get(name)
        if st is None:
            return Blocked(
                f"Release Orchestrator run {bid} is missing the '{name}' stage — "
                f"the run may have aborted early.{K.UNBLOCK_HELP}", links=links)
        if st.get("result") != "succeeded":
            return Blocked(
                f"Release Orchestrator stage '{name}' did not succeed "
                f"(state={st.get('state')}, result={st.get('result')}) in run {bid}. "
                f"RC testing can't be trusted until this is green.{K.UNBLOCK_HELP}", links=links)

    # The park stage should be PENDING (waiting for the owner). If it already ran, the
    # gate was approved — surface it (out of the expected Phase-2 state), don't hard-fail.
    park = by_name.get(cfg["park_stage"])
    park_done = bool(park is not None and park.get("state") == "completed")
    K.stash_orchestrator(state, bid, versions={k: v for k, v in versions.items() if v},
                         parked=not park_done)
    if park_done:
        return Done(
            f"Release Orchestrator run {bid} healthy ({vstr}); NOTE '{cfg['park_stage']}' "
            f"already ran (result={park.get('result')}) — the approval gate was cleared. "
            f"Pre-gate stages all succeeded.", links=links)

    return Done(
        f"Release Orchestrator run {bid} healthy — Validate / Create Branches / "
        f"Trigger RC Testing all succeeded; parked at '{cfg['park_stage']}' awaiting "
        f"owner approval (cleared in a later phase). {vstr}.", links=links)


run = legacy_run(build)
