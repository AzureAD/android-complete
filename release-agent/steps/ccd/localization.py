"""Step: `localization` — trigger the localization pipeline at noon (Phase 1, P1-2).

At noon on Code Complete Day the localization build is triggered with the
`isCreatePrSelected=true` variable. When it finishes, its OneLocBuild@3 task
AUTO-CREATES a translations PR in the Auth App repo IFF there are new/updated
user-facing strings:

  * PR created  → there ARE strings to merge — review + merge that PR into the branch.
  * no PR       → there were no new strings this release — nothing to merge.

Triggering an ADO pipeline (and later reading the resulting PR) needs an MCP/az call
the deterministic engine can't make under Conditional Access, so this is a `scout`
step: `build()` returns a NeedsSkill describing the exact pipeline run + a follow-up
note on where the OneLoc PR will appear.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked

ID = "localization"
KIND = "scout"

# Step config (co-located). Pipeline 405133 (msazure / One), run with
# isCreatePrSelected=true; its OneLocBuild@3 task opens the translations PR in the
# Auth App repo. fire_at_local is read by the per-release CCD-noon automation.
CONFIG = {
    "org": "https://msazure.visualstudio.com",
    "project": "One",
    "pipeline_id": 405133,
    "variables": {"isCreatePrSelected": "true"},
    "fire_at_local": "12:00",                 # noon on CCD (automation-driven)
    "oneloc_task": "OneLocBuild@3",           # the task that opens the translations PR
    "links": {
        "pipeline": "https://dev.azure.com/msazure/One/_build?definitionId=405133",
        "repo_prs": "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequests",
    },
}

# This step has no per-step mock knobs: triggering is a single MCP/az call with no
# real input to inject. Engine-level `outcome: done|blocked` (see `mock-spec`) still
# lets you clear or hold it offline.
MOCKABLE = {}


def _links(cfg: dict) -> list:
    lk = cfg.get("links", {}) or {}
    out = []
    if lk.get("pipeline"):
        out.append({"name": "Localization pipeline (405133)", "url": lk["pipeline"]})
    if lk.get("repo_prs"):
        out.append({"name": "Auth App PRs (OneLoc PR lands here)", "url": lk["repo_prs"]})
    return out


def build(state):
    """Resolve into a NeedsSkill that triggers pipeline 405133 with
    isCreatePrSelected=true, or Blocked if the release has no CCD / config is
    incomplete."""
    if not state.ccd:
        return Blocked("no CCD set for this release")

    cfg = CONFIG
    if not all(cfg.get(k) for k in ("org", "project", "pipeline_id")):
        return Blocked("localization: incomplete pipeline configuration")

    variables = cfg.get("variables", {}) or {}
    # Shape variables for the ADO MCP run tool (value-wrapped) while keeping a flat
    # copy for an az fallback.
    mcp_vars = {k: {"value": str(v)} for k, v in variables.items()}
    var_str = ", ".join(f"{k}={v}" for k, v in variables.items())

    return NeedsSkill(
        tool="azure_devops-pipelines_run_pipeline",
        payload={
            "project": cfg["project"],
            "pipelineId": cfg["pipeline_id"],
            "variables": mcp_vars,
            "_trigger": {
                "org": cfg["org"],
                "pipeline_id": cfg["pipeline_id"],
                "variables_flat": variables,
                "oneloc_task": cfg.get("oneloc_task"),
                # az fallback if the ADO MCP can't reach this org under Conditional Access.
                "az_fallback": (
                    f"az pipelines run --id {cfg['pipeline_id']} "
                    f"--org {cfg['org']} --project {cfg['project']} "
                    f"--variables {var_str}"),
                "after": (
                    "When the run finishes, check the Auth App repo PRs: the "
                    "OneLocBuild@3 task auto-creates a translations PR IFF there are "
                    "new/updated strings. PR created → strings to merge (record the PR "
                    "link); no PR → no strings this release."),
            },
            "links": _links(cfg),
        },
        record_as=ID,
        summary=f"Trigger localization pipeline {cfg['pipeline_id']} ({var_str})",
        note=f"triggered pipeline {cfg['pipeline_id']} with {var_str}; OneLoc PR (if any strings) lands in the Auth App repo PRs",
    )
