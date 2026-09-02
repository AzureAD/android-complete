"""Release Orchestrator / Checker / MRWP run discovery, stages, timeline, approvals."""
from __future__ import annotations

from tools.coordinates import coords
from tools import pipelines as _pp
from tools.pipelines._rest import RAN_RESULTS

# ── Engineering release-chain coordinates (identitydivision / Engineering) — SINGLE SOURCE.
# The run-discovery below queries this chain; rc_model imports these for release_report's
# defaults, and steps/build_verify/_common.py imports them from the package. Values come from
# config/coordinates.yaml; the constant NAMES stay so every consumer is unchanged.
# Other areas own their own coordinates (localization/wiki/CG live in their step modules).
IDENTITYDIVISION = coords.org_url("engineering")
MSAZURE = coords.org_url("one")
ENGINEERING_ORG = IDENTITYDIVISION
ENGINEERING_PROJECT = coords.project("engineering")
CHECKER_DEF = coords.pipeline_def("checker")            # Code Complete Calendar Checker
ORCHESTRATOR_DEF = coords.pipeline_def("orchestrator")  # Release Orchestrator (the spine)
MRWP_DEF = coords.pipeline_def("mrwp")                  # Monthly Release Work Pipeline (ECS + Local)
TRIGGER_JOB = "Trigger Monthly Release"
ORCH_REQUIRED_STAGES = [
    "Validate Branch and Versions availability",
    "Create Release Branches",
    "Trigger RC Testing",
]
ORCH_PARK_STAGE = "Remove RC Tags"


def find_orchestrator_run(org, project, def_id, release_month, timeout=60):
    """Find THE Release Orchestrator run for a release month.

    Matches the run's self-tag `AuthenticatorBranch=release-<YYYY>-<MM>-*` (debug
    runs tag `test-release-*`, so they're excluded). On multiple matches returns the
    most recent by queueTime. Returns (ok, run, detail); run is the az build dict
    (incl. `tags`) or None if not found.
    """
    ok, builds, detail = _pp._az_json(
        ["pipelines", "build", "list", "--definition-ids", str(def_id),
         "--org", org, "--project", project, "--top", "50"], timeout)
    if not ok:
        return (False, None, detail)
    prefix = f"AuthenticatorBranch=release-{release_month}-"     # release-2026-08-
    matches = [b for b in (builds or [])
               if any((t or "").startswith(prefix) for t in (b.get("tags") or []))]
    if not matches:
        return (True, None, f"no orchestrator run tagged {prefix}* found")
    latest = max(matches, key=lambda b: b.get("queueTime") or "")
    return (True, latest, "")


def discover_versions(org, project, release_month, orch_def=None, timeout=60):
    """(ok, versions, detail) — resolve per-repo release versions from the orchestrator
    run's build tags (Next{Common,Msal,Broker}Version=<v>). `versions` is keyed by the
    integ_prs repo keys: {'common','msal','broker'} (values may be None if a tag is
    missing). Authenticator is not tagged here and is resolved separately."""
    orch_def = orch_def or ORCHESTRATOR_DEF
    ok, run, detail = _pp.find_orchestrator_run(org, project, orch_def, release_month, timeout)
    if not ok:
        return (False, {}, detail)
    if not run:
        return (True, {}, f"no orchestrator run found for {release_month}")
    tags = run.get("tags") or []
    versions = {
        "common": _pp._tag_value(tags, "NextCommonVersion"),
        "msal": _pp._tag_value(tags, "NextMsalVersion"),
        "broker": _pp._tag_value(tags, "NextBrokerVersion"),
    }
    return (True, versions, "")


def find_checker_runs(org, project, def_id, release_month, timeout=60):
    """Return (ok, runs, detail) — the checker's builds queued in the release month,
    newest first. The checker runs DAILY (a cron); only the run on the actual Code
    Complete Day triggers the release, so the caller scans these for the one whose
    'Trigger Monthly Release' stage succeeded."""
    ok, builds, detail = _pp._az_json(
        ["pipelines", "build", "list", "--definition-ids", str(def_id),
         "--org", org, "--project", project, "--top", "60"], timeout)
    if not ok:
        return (False, None, detail)
    inmonth = [b for b in (builds or [])
               if (b.get("queueTime") or "").startswith(release_month)]
    inmonth.sort(key=lambda b: b.get("queueTime") or "", reverse=True)
    return (True, inmonth, "")


def mrwp_run_ids(org, project, orch_run, timeout=90):
    """Resolve the two MRWP (def 2519) build ids for the CURRENT RC iteration, keyed by
    flight provider. Returns (ok, {"ECS": <id>, "Local": <id>, "rc": <N>}, detail, source).

    PRIMARY — the orchestrator run's self-tags `RC<N>-ECS=<id>` / `RC<N>-Local=<id>`, where
    <N> is the RC iteration (RC1, RC2, ...). A re-triggered RC adds a HIGHER-numbered set
    (e.g. RC2-ECS / RC2-Local alongside RC1-*), so the CURRENT RC is the highest N that has
    BOTH an ECS and a Local id — its two ids win. `rc` is that N.

    FALLBACK — if no RC<N>-* id tags are present, parse the 'Trigger RC Testing' stage's two
    'Trigger ADO Pipeline' task logs for `Run ID: <id>` + `Flight Provider: <p>` (newest per
    provider by build id; rc unknown -> None). `source` is 'tags' or 'logs'.
    """
    import re as _re
    tags = (orch_run or {}).get("tags") or []
    by_rc = {}                                   # N -> {"ECS": id, "Local": id}
    for t in tags:
        m = _re.match(r"RC(\d+)-(ECS|Local)=(\d+)$", str(t).strip())
        if m:
            by_rc.setdefault(int(m.group(1)), {})[m.group(2)] = m.group(3)
    complete = [n for n, d in by_rc.items() if d.get("ECS") and d.get("Local")]
    if complete:
        n = max(complete)                        # highest RC iteration with both providers
        return (True, {"ECS": by_rc[n]["ECS"], "Local": by_rc[n]["Local"], "rc": n}, "", "tags")

    # Fallback: parse the trigger-task logs from the orchestrator's timeline. On a
    # re-trigger there are extra 'Trigger ADO Pipeline' tasks — collect ALL ids per
    # provider and take the newest so the fresh run wins.
    bid = (orch_run or {}).get("id")
    if not bid:
        return (False, None, "orchestrator run has no id", "logs")
    ok, tl, detail = _pp._az_json(
        ["devops", "invoke", "--org", org, "--area", "build", "--resource", "timeline",
         "--route-parameters", f"project={project}", f"buildId={bid}",
         "--api-version", "7.1"], timeout)
    if not ok:
        return (False, None, detail, "logs")
    recs = (tl or {}).get("records", []) or []
    trigger_tasks = [r for r in recs
                     if r.get("type") == "Task" and r.get("name") == "Trigger ADO Pipeline"
                     and (r.get("log") or {}).get("id")]
    found = {"ECS": [], "Local": []}
    base = org.rstrip("/")
    for t in trigger_tasks:
        log_id = t["log"]["id"]
        url = f"{base}/{project}/_apis/build/builds/{bid}/logs/{log_id}?api-version=7.1"
        ok2, txt, _ = _pp._ado_rest_get_text(url, timeout)
        if not ok2 or not txt:
            continue
        m_id = _re.search(r"Run ID:\s*(\d+)", txt)
        m_pr = _re.search(r"Flight Provider:\s*(ECS|Local)", txt, _re.IGNORECASE)
        if m_id and m_pr:
            prov = "ECS" if m_pr.group(1).upper() == "ECS" else "Local"
            found[prov].append(m_id.group(1))
    ecs, local = _pp._newest_id(found["ECS"]), _pp._newest_id(found["Local"])
    if ecs and local:
        return (True, {"ECS": ecs, "Local": local, "rc": None}, "", "logs")
    return (False, None, f"could not resolve both MRWP ids (got {found or 'none'})", "logs")


def get_timeline(org, project, build_id, timeout=60):
    """Return (ok, records, detail) — the raw timeline records for a build (Stage /
    Phase / Job / Task). Callers filter by type/name."""
    ok, tl, detail = _pp._az_json(
        ["devops", "invoke", "--org", org, "--area", "build", "--resource", "timeline",
         "--route-parameters", f"project={project}", f"buildId={build_id}",
         "--api-version", "7.1"], timeout)
    if not ok:
        return (False, None, detail)
    return (True, (tl or {}).get("records", []) or [], "")


def named_record(records, name, types=("Job", "Phase", "Stage")):
    """First timeline record matching `name` among the given record `types`, or None."""
    for r in records or []:
        if r.get("type") in types and r.get("name") == name:
            return r
    return None


def _pending_approval_for_build(org, project, build_id, timeout=90):
    """(ok, approval_id|None, detail) — the PENDING pipeline approval whose owner build is
    `build_id`, from the approvals the signed-in user can act on."""
    ok, data, d = _pp._ado_rest_get(
        f"{org.rstrip('/')}/{project}/_apis/pipelines/approvals?api-version=7.2-preview.1", timeout)
    if not ok:
        return (False, None, d)
    for ap in (data or {}).get("value", []) or []:
        if ap.get("status") in ("approved", "completed", "rejected", "canceled"):
            continue
        owner = (ap.get("pipeline") or {}).get("owner") or {}
        href = ((owner.get("_links") or {}).get("web") or {}).get("href", "")
        if f"buildId={build_id}" in href:
            return (True, ap.get("id"), "")
    return (True, None, "")


def find_orchestrator_pending_approval(org, project, release_month, timeout=90):
    """Find the Release Orchestrator run for `release_month` and, if it's parked at a manual
    approval, return that approval. Returns (ok, info, detail) where info is
    {approval_id, build_id, stage, build_url} — or None when nothing is parked.

    Discovery: the orchestrator run (by AuthenticatorBranch tag) → its timeline for a Stage whose
    Checkpoint.Approval record is still inProgress → the matching PENDING approval (owned by this
    build) from the pipelines approvals API."""
    ok, run, detail = _pp.find_orchestrator_run(org, project, ORCHESTRATOR_DEF, release_month, timeout)
    if not ok:
        return (False, None, detail)
    if not run:
        return (True, None, f"no orchestrator run found for {release_month}")
    bid = run.get("id")
    okt, recs, dt = _pp.get_timeline(org, project, bid, timeout)
    if not okt:
        return (False, None, dt)
    byid = {r.get("id"): r for r in recs}

    def _stage_of(rec):
        cur = rec
        while cur and cur.get("type") != "Stage":
            cur = byid.get(cur.get("parentId"))
        return (cur or {}).get("name")

    pending_stage = None
    for r in recs:
        if r.get("type") == "Checkpoint.Approval" and r.get("state") == "inProgress":
            pending_stage = _stage_of(r)
            break
    if not pending_stage:
        return (True, None, f"orchestrator build {bid} is not parked at a manual approval")
    oka, approval_id, da = _pp._pending_approval_for_build(org, project, bid, timeout)
    if not oka:
        return (False, None, da)
    if not approval_id:
        return (True, None, f"no pending approval visible to you on build {bid}")
    build_url = f"{org.rstrip('/')}/{project}/_build/results?buildId={bid}&view=results"
    return (True, {"approval_id": approval_id, "build_id": bid, "stage": pending_stage,
                   "build_url": build_url}, "")


def orchestrator_stage_state(org, project, release_month, stage_name, timeout=90):
    """(ok, info|None, detail) — the timeline state of a named Release Orchestrator Stage for the
    release. info is {state, result, build_id}; None when the stage isn't in the timeline yet.
    Used by publish_notes_gate to tell 'notes already published' from 'not reached yet'."""
    ok, run, detail = _pp.find_orchestrator_run(org, project, ORCHESTRATOR_DEF, release_month, timeout)
    if not ok:
        return (False, None, detail)
    if not run:
        return (True, None, f"no orchestrator run found for {release_month}")
    okt, recs, dt = _pp.get_timeline(org, project, run.get("id"), timeout)
    if not okt:
        return (False, None, dt)
    for r in recs:
        if r.get("type") == "Stage" and r.get("name") == stage_name:
            return (True, {"state": r.get("state"), "result": r.get("result"),
                           "build_id": run.get("id")}, "")
    return (True, None, f"stage '{stage_name}' not in the orchestrator timeline")


def submit_pipeline_approval(org, project, approval_id, comment="", status="approved", timeout=60):
    """Submit a decision on a pipeline approval — status 'approved' | 'rejected'. (ok, detail)."""
    url = f"{org.rstrip('/')}/{project}/_apis/pipelines/approvals?api-version=7.2-preview.1"
    body = [{"approvalId": approval_id, "status": status, "comment": comment}]
    ok, res, d = _pp._ado_rest_send(url, "PATCH", body, timeout)
    if not ok:
        return (False, d)
    entry = ((res or {}).get("value") or [{}])[0] if isinstance(res, dict) else {}
    got = entry.get("status")
    if got != status:
        return (False, f"approval status is '{got}' after submit (expected '{status}')")
    return (True, f"approval {approval_id} -> {got}")


def get_build_status(org, project, build_id, timeout=60):
    """Return (ok, status, result, detail) for a build's OVERALL run.

    status  ∈ {notStarted, inProgress, completed, cancelling, postponed, none}
    result  ∈ {succeeded, partiallySucceeded, failed, canceled, none} (only meaningful
             once status == 'completed').

    This is the Phase-2 completion signal: a run is DONE only when status == 'completed'.
    While it's notStarted/inProgress the verify step must treat un-run stages as
    'not run YET' (in-flight), NOT as an aborted release."""
    ok, data, detail = _pp._az_json(
        ["pipelines", "build", "show", "--org", org, "--project", project,
         "--id", str(build_id), "--query", "{status:status,result:result}"], timeout)
    if not ok:
        return (False, None, None, detail)
    d = data or {}
    return (True, d.get("status"), d.get("result"), "")


def get_stages(org, project, build_id, timeout=60):
    """Return (ok, stages, detail). `stages` is an ORDER-sorted list of
    {name, state, result} from the build's timeline (Stage records only)."""
    ok, recs, detail = _pp.get_timeline(org, project, build_id, timeout)
    if not ok:
        return (False, None, detail)
    stages = [{"name": r.get("name"), "state": r.get("state"), "result": r.get("result"),
               "order": r.get("order") or 0}
              for r in recs if r.get("type") == "Stage"]
    stages.sort(key=lambda s: s["order"])
    return (True, stages, "")


def stage_completion(stages):
    """Classify a stage list against the release rule. Returns
    {total, ran, never_ran:[names], failed:[names], yellow:[names], complete:bool}.

    complete = every stage executed (state completed AND result in RAN_RESULTS).
    never_ran = stages still pending/in-progress OR skipped/canceled (the abort
    signal). failed/yellow are reported but do NOT block.
    """
    never, failed, yellow = [], [], []
    for s in stages or []:
        res = s.get("result")
        if s.get("state") != "completed" or res not in RAN_RESULTS:
            never.append(s.get("name"))
        elif res == "failed":
            failed.append(s.get("name"))
        elif res == "succeededWithIssues":
            yellow.append(s.get("name"))
    total = len(stages or [])
    return {"total": total, "ran": total - len(never), "never_ran": never,
            "failed": failed, "yellow": yellow, "complete": not never and total > 0}

__all__ = ['CHECKER_DEF', 'ENGINEERING_ORG', 'ENGINEERING_PROJECT', 'IDENTITYDIVISION', 'MRWP_DEF', 'MSAZURE', 'ORCHESTRATOR_DEF', 'ORCH_PARK_STAGE', 'ORCH_REQUIRED_STAGES', 'TRIGGER_JOB', '_pending_approval_for_build', 'discover_versions', 'find_checker_runs', 'find_orchestrator_pending_approval', 'find_orchestrator_run', 'get_build_status', 'get_stages', 'get_timeline', 'mrwp_run_ids', 'named_record', 'orchestrator_stage_state', 'stage_completion', 'submit_pipeline_approval']
