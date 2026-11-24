"""Release Orchestrator / Checker / MRWP run discovery, stages, timeline, approvals."""
from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qs, quote, urlsplit

from tools.coordinates import coords
from tools import pipelines as _pp
from tools.pipelines._rest import RAN_RESULTS

# ── Engineering release-chain coordinates (identitydivision / Engineering) — SINGLE SOURCE.
# The run-discovery below queries this chain; rc_model imports these for release_report's
# defaults, and build_verify steps import them from the package. Values come from
# config/coordinates.yaml; the constant NAMES stay so every consumer is unchanged.
# Other areas own their own coordinates (localization/wiki/CG live in their step modules).
IDENTITYDIVISION = coords.org_url("engineering")
MSAZURE = coords.org_url("one")
ENGINEERING_ORG = IDENTITYDIVISION
ENGINEERING_PROJECT = coords.project("engineering")
CHECKER_DEF = coords.pipeline_def("checker")            # Code Complete Calendar Checker
ORCHESTRATOR_DEF = coords.pipeline_def("orchestrator")  # Release Orchestrator (the spine)
MRWP_DEF = coords.pipeline_def("mrwp")                  # Monthly Release Work Pipeline (ECS + Local)
TRIGGER_JOB_ID = "TriggerMonthlyRelease"
TRIGGER_JOB = "Trigger Monthly Release"
ORCH_STAGE_LABELS = {
    "ValidateBranchesAndVersions": "Validate Branch and Versions availability",
    "BranchSetup": "Create Release Branches",
    "TriggerRC": "Trigger RC Testing",
    "RemoveRCTags": "Remove RC Tags",
    "PublishInternal": "Publish Internal",
    "PublishExternal": "Publish to Maven Central",
    "UpdatePipelineVariables": "Update Pipeline Variables",
    "CreateReleaseIntegrationBranches": "Create Release Integration Branches",
    "PublishGitHubReleaseNotes": "Publish GitHub Release Notes",
}
ORCH_LABEL_TO_STAGE_ID = {label: sid for sid, label in ORCH_STAGE_LABELS.items()}
ORCH_REQUIRED_STAGE_IDS = [
    "ValidateBranchesAndVersions",
    "BranchSetup",
    "TriggerRC",
]
ORCH_PARK_STAGE_ID = "RemoveRCTags"
ORCH_NOTES_STAGE_ID = "PublishGitHubReleaseNotes"
ORCH_REQUIRED_STAGES = [ORCH_STAGE_LABELS[sid] for sid in ORCH_REQUIRED_STAGE_IDS]
ORCH_PARK_STAGE = ORCH_STAGE_LABELS[ORCH_PARK_STAGE_ID]


def find_orchestrator_run(org, project, def_id, release_month, timeout=60, *, ccd=None):
    """Find THE Release Orchestrator run for a release month (and optional elected CCD).

    Matches the run's self-tag `AuthenticatorBranch=release-<YYYY>-<MM>-*` (debug
    runs tag `test-release-*`, so they're excluded). When `ccd` ('YYYY-MM-DD') is
    supplied, prefers exact `AuthenticatorBranch=release-<ccd>`, and otherwise ignores
    earlier-in-month runs whose branch date precedes `ccd`.
    """
    from datetime import timedelta
    from orchestrator import schedule
    ok, builds, detail = _pp._az_json(
        ["pipelines", "build", "list", "--definition-ids", str(def_id),
         "--org", org, "--project", project, "--top", "50"], timeout)
    if not ok:
        return (False, None, detail)
    if not isinstance(builds, list) or any(
            not isinstance(build, Mapping)
            or _numeric_build_id(build.get("id")) is None
            or (build.get("tags") is not None and (
                not isinstance(build["tags"], list)
                or any(not isinstance(tag, str) for tag in build["tags"])))
            or build.get("queueTime") is not None and not isinstance(build["queueTime"], str)
            for build in builds):
        return False, None, "malformed orchestrator run collection"
    ccd_date = schedule.parse_date(ccd) if ccd else None
    if ccd_date is not None:
        exact_tag = f"AuthenticatorBranch=release-{ccd_date.isoformat()}"
        exact = [b for b in (builds or []) if exact_tag in (b.get("tags") or [])]
        if exact:
            return (True, max(exact, key=lambda b: b.get("queueTime") or ""), "")
        candidates = []
        for b in (builds or []):
            for t in (b.get("tags") or []):
                if isinstance(t, str) and t.startswith("AuthenticatorBranch=release-"):
                    bdate = schedule.parse_date(t[len("AuthenticatorBranch=release-"):])
                    if bdate is not None and ccd_date <= bdate <= ccd_date + timedelta(days=14):
                        candidates.append(b)
                        break
        if not candidates:
            return (True, None, f"no orchestrator run tagged {exact_tag} found")
        return (True, max(candidates, key=lambda b: b.get("queueTime") or ""), "")

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


def find_checker_runs(org, project, def_id, release_month, timeout=60, *, ccd=None, timezone_name=None):
    """Return (ok, runs, detail) — the checker's builds queued for the release,
    newest first. When `ccd` ('YYYY-MM-DD') is supplied, filters to runs queued on
    or after `ccd` (within 14 days, checking both owner-local and UTC dates so an
    end-of-month 11 PM PT run whose UTC timestamp rolls into the 1st of next month is
    still captured, while earlier-in-month runs before `ccd` are excluded)."""
    from datetime import timedelta
    from orchestrator import schedule
    ok, builds, detail = _pp._az_json(
        ["pipelines", "build", "list", "--definition-ids", str(def_id),
         "--org", org, "--project", project, "--top", "60"], timeout)
    if not ok:
        return (False, None, detail)
    if not isinstance(builds, list) or any(
            not isinstance(build, Mapping) or _numeric_build_id(build.get("id")) is None
            or not isinstance(build.get("queueTime"), str) for build in builds):
        return False, None, "malformed checker run collection"
    ccd_date = schedule.parse_date(ccd) if ccd else None
    if ccd_date is not None:
        tz = schedule.get_tz(timezone_name)
        matched = []
        upper = ccd_date + timedelta(days=14)
        for b in (builds or []):
            qdt = schedule.parse_iso_utc(b.get("queueTime"))
            if qdt is None:
                continue
            utc_d = qdt.date()
            loc_d = qdt.astimezone(tz).date() if tz is not None else utc_d
            if (ccd_date <= loc_d <= upper) or (ccd_date <= utc_d <= upper):
                matched.append(b)
        matched.sort(key=lambda b: b.get("queueTime") or "", reverse=True)
        return (True, matched, "")
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
    if (not isinstance(tl, Mapping) or not isinstance(tl.get("records"), list)
            or any(not isinstance(record, Mapping) for record in tl["records"])):
        return False, None, "malformed build timeline: expected records array"
    return (True, tl["records"], "")


def named_record(records, ref, types=("Job", "Phase", "Stage"), *, aliases=()):
    """First timeline record matching canonical YAML `identifier` `ref` among `types`.

    Falls back to `name` matching against `(ref, *aliases)` only when a record omits
    `identifier` (e.g. minimal offline test fixtures), so live ADO timelines always
    resolve by stable YAML `identifier` rather than mutable display titles.
    """
    acceptable_names = {ref, *aliases}
    for r in records or []:
        if r.get("type") not in types:
            continue
        ident = r.get("identifier")
        if isinstance(ident, str) and ident.strip():
            if ident == ref:
                return r
        elif r.get("name") in acceptable_names:
            return r
    return None


def _numeric_build_id(value):
    if type(value) is int:
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        try:
            number = int(value)
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def _valid_approval_id(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def approval_owner_build_id(approval):
    """Read the exact numeric buildId from the provider's pipeline owner web link.

    This is the owner shape exposed by pipeline approval discovery. Missing,
    malformed, or repeated buildId parameters are not ownership evidence; neither
    a substring in a URL nor an unrelated pipeline/definition id is a build id.
    """
    owner_link = approval
    for key in ("pipeline", "owner", "_links", "web", "href"):
        if not isinstance(owner_link, Mapping):
            return None
        owner_link = owner_link.get(key)
    if not isinstance(owner_link, str):
        return None
    try:
        ids = parse_qs(urlsplit(owner_link).query, keep_blank_values=True).get("buildId", [])
    except ValueError:
        return None
    return _numeric_build_id(ids[0]) if len(ids) == 1 else None


def _pending_approval_for_build(org, project, build_id, timeout=90):
    """(ok, approval_id|None, detail) — a uniquely identified PENDING build approval.

    The list has no documented stage correlation in the observed provider shape.
    Multiple pending approvals on this exact build must therefore fail closed,
    rather than selecting an arbitrary approval from the same run.
    """
    expected_build = _numeric_build_id(build_id)
    if expected_build is None:
        return (False, None, "invalid approval owner build id")
    ok, data, d = _pp._ado_rest_get(
        f"{org.rstrip('/')}/{project}/_apis/pipelines/approvals?api-version=7.2-preview.1", timeout)
    if not ok:
        return (False, None, d)
    if not isinstance(data, Mapping) or not isinstance(data.get("value"), list):
        return (False, None, "malformed pipeline approvals response")
    pending = []
    for ap in data["value"]:
        if not isinstance(ap, Mapping):
            return (False, None, "malformed pipeline approval entry")
        if approval_owner_build_id(ap) != expected_build or ap.get("status") != "pending":
            continue
        if not _valid_approval_id(ap.get("id")):
            return (False, None, f"pending approval on build {build_id} has no valid id")
        pending.append(ap["id"])
    if len(pending) > 1:
        return (False, None, f"ambiguous pending approvals on build {build_id}; inspect the ADO gates")
    if pending:
        return (True, pending[0], "")
    return (True, None, "")


def _pending_checkpoint(records):
    """Resolve one active checkpoint and its stage without inventing a stage join."""
    if not isinstance(records, list) or any(not isinstance(r, Mapping) for r in records):
        return (False, None, "malformed orchestrator timeline")
    pending = [r for r in records
               if r.get("type") == "Checkpoint.Approval" and r.get("state") == "inProgress"]
    if not pending:
        return (True, None, "")
    if len(pending) != 1:
        return (False, None, "ambiguous active approval checkpoints; inspect the ADO stages")
    byid = {}
    for record in records:
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in byid:
            return (False, None, "missing or duplicate timeline record identity")
        byid[identifier] = record
    checkpoint = pending[0]
    cur = checkpoint
    seen = set()
    while cur.get("type") != "Stage":
        identifier = cur["id"]
        if identifier in seen:
            return (False, None, "cyclic approval checkpoint ancestry")
        seen.add(identifier)
        parent_id = cur.get("parentId")
        cur = byid.get(parent_id) if isinstance(parent_id, str) else None
        if cur is None:
            return (False, None, "cannot identify the approval checkpoint's stage")
    stage_id = cur.get("identifier")
    stage = cur.get("name") or stage_id
    if not isinstance(stage, str) or not stage.strip():
        return (False, None, "approval checkpoint's stage has no identifier or name")
    clean_stage_id = stage_id.strip() if isinstance(stage_id, str) and stage_id.strip() else None
    return (True, (checkpoint["id"], cur["id"], stage, clean_stage_id), "")


def find_orchestrator_pending_approval(org, project, release_month, timeout=90):
    """Find the Release Orchestrator run for `release_month` and, if it's parked at a manual
    approval, return that approval. Returns (ok, info, detail) where info is
    {approval_id, build_id, stage, build_url} — or None when nothing is parked.

    Discovery: the orchestrator run (by AuthenticatorBranch tag) → its timeline for a Stage whose
    Checkpoint.Approval record is still inProgress → the unique PENDING approval owned by
    this exact build. Both sides must be unique because the observed approvals payload
    does not identify a stage. Re-read the same build's timeline before returning to
    catch stage advancement during discovery. Concurrent/ambiguous gates fail closed;
    timeline record ids are NOT assumed to equal approval ids."""
    ok, run, detail = _pp.find_orchestrator_run(org, project, ORCHESTRATOR_DEF, release_month, timeout)
    if not ok:
        return (False, None, detail)
    if not run:
        return (True, None, f"no orchestrator run found for {release_month}")
    bid = _numeric_build_id(run.get("id")) if isinstance(run, Mapping) else None
    if bid is None:
        return (False, None, "orchestrator run has no valid build id")
    okt, recs, dt = _pp.get_timeline(org, project, bid, timeout)
    if not okt:
        return (False, None, dt)
    checked, checkpoint, dc = _pending_checkpoint(recs)
    if not checked:
        return (False, None, dc)
    if checkpoint is None:
        return (True, None, f"orchestrator build {bid} is not parked at a manual approval")
    oka, approval_id, da = _pp._pending_approval_for_build(org, project, bid, timeout)
    if not oka:
        return (False, None, da)
    if not approval_id:
        return (True, None, f"no pending approval visible to you on build {bid}")
    checked, current_records, dc = _pp.get_timeline(org, project, bid, timeout)
    if not checked:
        return (False, None, dc)
    checked, current_checkpoint, dc = _pending_checkpoint(current_records)
    if not checked or current_checkpoint != checkpoint:
        return (False, None, dc or "approval checkpoint changed during discovery; inspect the ADO gate")
    build_url = f"{org.rstrip('/')}/{project}/_build/results?buildId={bid}&view=results"
    result = {
        "approval_id": approval_id,
        "build_id": bid,
        "stage": checkpoint[2],
        "build_url": build_url,
    }
    if len(checkpoint) > 3 and checkpoint[3]:
        result["stage_id"] = checkpoint[3]
    return (True, result, "")


def _match_orchestrator_stage(records, stage_ref):
    canonical_id = ORCH_LABEL_TO_STAGE_ID.get(stage_ref, stage_ref)
    acceptable_names = {stage_ref, canonical_id}
    if canonical_id in ORCH_STAGE_LABELS:
        acceptable_names.add(ORCH_STAGE_LABELS[canonical_id])
    for r in records or []:
        if r.get("type") != "Stage":
            continue
        ident = r.get("identifier")
        if isinstance(ident, str) and ident.strip():
            if ident.strip() in (stage_ref, canonical_id):
                return r
        elif r.get("name") in acceptable_names:
            return r
    return None


def orchestrator_stage_state(org, project, release_month, stage_ref, timeout=90):
    """Return the timeline state of a Release Orchestrator stage by canonical YAML `identifier`."""
    ok, run, detail = _pp.find_orchestrator_run(org, project, ORCHESTRATOR_DEF, release_month, timeout)
    if not ok:
        return (False, None, detail)
    if not run:
        return (True, None, f"no orchestrator run found for {release_month}")
    okt, recs, dt = _pp.get_timeline(org, project, run.get("id"), timeout)
    if not okt:
        return (False, None, dt)
    r = _match_orchestrator_stage(recs, stage_ref)
    if r is not None:
        return (True, {"state": r.get("state"), "result": r.get("result"),
                       "stage_id": r.get("identifier"), "build_id": run.get("id")}, "")
    return (True, None, f"stage '{stage_ref}' not in the orchestrator timeline")


def orchestrator_finalization_status(org, project, release_month, stage_ref, timeout=90):
    """Resolve the final publication-gate state and its final MRWP/Auth outputs.

    `Final1=<mrwp id>` is emitted on the orchestrator run. Authenticator build/version
    evidence is captured later by rollout_start.identify_auth_build from pipeline 475778.
    """
    ok, run, detail = _pp.find_orchestrator_run(
        org, project, ORCHESTRATOR_DEF, release_month, timeout)
    if not ok:
        return (False, None, detail)
    if not run:
        return (True, {"status": "waiting"}, detail)
    build_id = _numeric_build_id(run.get("id"))
    if build_id is None:
        return (False, None, "orchestrator run has no valid build id")

    okt, records, timeline_detail = _pp.get_timeline(org, project, build_id, timeout)
    if not okt:
        return (False, None, timeline_detail)
    target = _match_orchestrator_stage(records, stage_ref)
    checked, checkpoint, checkpoint_detail = _pending_checkpoint(records)
    if not checked:
        return (False, None, checkpoint_detail)

    info = {
        "status": "waiting",
        "orchestrator_run_id": str(build_id),
        "stage_state": target.get("state") if target else None,
        "stage_result": target.get("result") if target else None,
    }
    if target and target.get("state") == "completed":
        if target.get("result") not in ("succeeded", "succeededWithIssues"):
            return (True, {**info, "status": "failed"},
                    f"stage '{target.get('name')}' completed with result={target.get('result')}")
        reached_target = True
        info["already_advanced"] = True
    else:
        reached_target = bool(
            checkpoint and target and checkpoint[1] == target.get("id"))
        if reached_target:
            info["parked"] = True

    if not reached_target:
        if run.get("status") == "completed":
            return (True, {**info, "status": "failed"},
                    f"orchestrator build {build_id} completed before reaching '{stage_ref}'")
        return (True, info, f"orchestrator build {build_id} has not reached '{stage_ref}'")

    final_mrwp = _pp._tag_value(run.get("tags") or [], "Final1")
    if _numeric_build_id(final_mrwp) is None:
        return (True, info, f"orchestrator build {build_id} has no valid Final1 tag yet")
    final_mrwp = str(_numeric_build_id(final_mrwp))
    info["mrwp_run_id"] = final_mrwp
    okf, final_run, final_detail = _pp._az_json(
        ["pipelines", "build", "show", "--org", org, "--project", project,
         "--id", final_mrwp,
         "--query", "{id:id,definitionId:definition.id,status:status,result:result,tags:tags}"], timeout)
    if not okf:
        return (False, None, final_detail)
    if (not isinstance(final_run, Mapping)
           or _numeric_build_id(final_run.get("id")) != int(final_mrwp)
           or _numeric_build_id(final_run.get("definitionId")) != MRWP_DEF):
        return (False, None, f"Final1 must identify MRWP build {final_mrwp} "
               f"from pipeline {MRWP_DEF}; provider identity is missing or mismatched")
    if final_run.get("status") != "completed":
        return (True, info, f"final MRWP {final_mrwp} is {final_run.get('status') or 'not started'}")
    if final_run.get("result") not in ("succeeded", "partiallySucceeded"):
        return (True, {**info, "status": "failed"},
                f"final MRWP {final_mrwp} completed with result={final_run.get('result')}")

    info["status"] = "ready"
    return (True, info, "")


def get_pipeline_approval(org, project, approval_id, timeout=60):
    """Read one frozen approval id, never the newest run or a stage-completion proxy.

    Returns (ok, approval|None, detail). Even a successful HTTP response must carry
    the requested id; null, malformed, and mismatched payloads are not evidence.
    Ownership/status validation is left to the caller's frozen request.
    """
    if not _valid_approval_id(approval_id):
        return (False, None, "invalid pipeline approval id")
    url = (f"{org.rstrip('/')}/{project}/_apis/pipelines/approvals/"
           f"{quote(approval_id, safe='')}?api-version=7.2-preview.1")
    ok, approval, detail = _pp._ado_rest_get(url, timeout)
    if not ok:
        return (False, None, detail)
    if not isinstance(approval, Mapping):
        return (False, None, f"approval {approval_id} is missing or malformed")
    if not _valid_approval_id(approval.get("id")) or approval["id"] != approval_id:
        return (False, None, f"approval response identity does not match {approval_id}")
    return (True, approval, "")


def submit_pipeline_approval(org, project, approval_id, comment="", status="approved", timeout=60):
    """Submit a decision and require exact identity/status evidence. (ok, detail).

    Some PATCH responses omit the id. Those are not receipts: confirm with a GET
    of the requested id. An explicitly different id or status fails closed.
    This helper never retries the PATCH, including when confirmation is unavailable.
    """
    if not _valid_approval_id(approval_id):
        return (False, "invalid pipeline approval id")
    if status not in ("approved", "rejected"):
        return (False, "pipeline approval decision must be approved or rejected")
    url = f"{org.rstrip('/')}/{project}/_apis/pipelines/approvals?api-version=7.2-preview.1"
    body = [{"approvalId": approval_id, "status": status, "comment": comment}]
    ok, res, d = _pp._ado_rest_send(url, "PATCH", body, timeout)
    if not ok:
        return (False, d)
    entries = res.get("value") if isinstance(res, Mapping) else None
    if entries is None and isinstance(res, Mapping) and "id" in res:
        entries = [res]
    entries = entries if isinstance(entries, list) else []
    identified = [entry for entry in entries if isinstance(entry, Mapping) and "id" in entry]
    if identified:
        if len(identified) != 1 or identified[0].get("id") != approval_id:
            return (False, f"approval response identity does not match {approval_id}")
        entry = identified[0]
    else:
        checked, entry, detail = _pp.get_pipeline_approval(org, project, approval_id, timeout)
        if not checked or not isinstance(entry, Mapping) or entry.get("id") != approval_id:
            return (False, f"cannot confirm approval {approval_id} after submit ({detail})")
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
    {identifier, name, state, result, attempt} from the build's timeline (Stage records only)."""
    ok, recs, detail = _pp.get_timeline(org, project, build_id, timeout)
    if not ok:
        return (False, None, detail)
    stages = [{"identifier": r.get("identifier"), "name": r.get("name"),
               "state": r.get("state"), "result": r.get("result"),
               "attempt": r.get("attempt"), "order": r.get("order") or 0}
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
    by_stage = {}
    for stage in stages or []:
        # Re-running a stage can leave multiple timeline entries with the same
        # logical stage name. Use the highest attempt so previous canceled/skipped
        # attempts do not incorrectly mark the release as incomplete.
        key = stage.get("name")
        attempt = stage.get("attempt")
        try:
            attempt_rank = int(attempt)
        except (TypeError, ValueError):
            attempt_rank = 0
        current = by_stage.get(key)
        current_rank = current[0] if current else -1
        if current is None or attempt_rank >= current_rank:
            by_stage[key] = (attempt_rank, stage)

    effective_stages = [item[1] for item in by_stage.values()]
    never, failed, yellow = [], [], []
    for s in effective_stages:
        res = s.get("result")
        if s.get("state") != "completed" or res not in RAN_RESULTS:
            never.append(s.get("name"))
        elif res == "failed":
            failed.append(s.get("name"))
        elif res == "succeededWithIssues":
            yellow.append(s.get("name"))
    total = len(effective_stages)
    return {"total": total, "ran": total - len(never), "never_ran": never,
            "failed": failed, "yellow": yellow, "complete": not never and total > 0}

__all__ = ['CHECKER_DEF', 'ENGINEERING_ORG', 'ENGINEERING_PROJECT', 'IDENTITYDIVISION', 'MRWP_DEF', 'MSAZURE', 'ORCHESTRATOR_DEF', 'ORCH_LABEL_TO_STAGE_ID', 'ORCH_NOTES_STAGE_ID', 'ORCH_PARK_STAGE', 'ORCH_PARK_STAGE_ID', 'ORCH_REQUIRED_STAGES', 'ORCH_REQUIRED_STAGE_IDS', 'ORCH_STAGE_LABELS', 'TRIGGER_JOB', 'TRIGGER_JOB_ID', '_pending_approval_for_build', 'approval_owner_build_id', 'discover_versions', 'find_checker_runs', 'find_orchestrator_pending_approval', 'find_orchestrator_run', 'get_build_status', 'get_pipeline_approval', 'get_stages', 'get_timeline', 'mrwp_run_ids', 'named_record', 'orchestrator_finalization_status', 'orchestrator_stage_state', 'stage_completion', 'submit_pipeline_approval']
