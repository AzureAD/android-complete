"""Read-only ADO pipeline queries for Phase 2 (build_verify) release verification.

Every function shells out to `az` and returns an (ok, data, detail) triple — no
writes, deterministic, so the build_verify agent steps stay pure verification. The
release chain these read (all in identitydivision/Engineering):

    3038 Code Complete Calendar Checker  → on the CCD, triggers →
    2828 Release Orchestrator            → self-tags AuthenticatorBranch=release-YYYY-MM-DD
                                           + RC-ECS=<id> / RC-Local=<id> (the two MRWP runs)
    2519 Monthly Release Work Pipeline   → runs twice (ECS + Local), ~23 stages each

The orchestrator's self-tags are the traceability anchor: find the 2828 run for a
release month by its AuthenticatorBranch tag, then read RC-<provider>=<id> to get the
MRWP build ids directly (no log parsing).
"""
from __future__ import annotations

import json as _json
import shutil
import subprocess

# ADO stage `result` values that mean the stage actually EXECUTED (vs never-ran).
# succeeded/succeededWithIssues (green/yellow) and failed (red) all count as "ran"
# — matches the release rule: only a stage that never ran (skipped/canceled/pending)
# blocks. See build_verify.mrwp_* steps.
RAN_RESULTS = {"succeeded", "succeededWithIssues", "failed"}


def _az_json(args, timeout):
    """Run `az <args> -o json` and return (ok, parsed_json, detail)."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        out = subprocess.run(
            [az, *args, "-o", "json"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        return (False, None, f"timeout running az {' '.join(args[:2])}")
    except OSError as e:
        return (False, None, f"failed to run az: {e}")
    if out.returncode != 0:
        err = (out.stderr or "").strip().splitlines()
        detail = (err[-1] if err else "az returned non-zero")[:200]
        # Surface auth problems distinctly so the step can prompt `az login`.
        low = detail.lower()
        if "login" in low or "401" in low or "unauthor" in low or "token" in low:
            detail = f"AUTH: {detail}"
        return (False, None, detail)
    try:
        return (True, _json.loads(out.stdout or "null"), "")
    except ValueError:
        return (False, None, "could not parse az output")


# ADO resource id for Azure DevOps — used to mint an access token for the few REST
# endpoints `az devops invoke` mis-routes (e.g. the Test Runs API).
_ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"


def _ado_rest_get(url, timeout):
    """GET an ADO REST url with an az-minted bearer token. Returns (ok, json, detail).
    Used only where `az devops invoke` can't reach an endpoint cleanly."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        tok = subprocess.run(
            [az, "account", "get-access-token", "--resource", _ADO_RESOURCE,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    except (subprocess.TimeoutExpired, OSError) as e:
        return (False, None, f"failed to get token: {e}")
    if tok.returncode != 0:
        return (False, None, "AUTH: could not get an ADO token (run `az login`)")
    token = (tok.stdout or "").strip()
    if not token:
        return (False, None, "AUTH: empty ADO token (run `az login`)")
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (True, _json.loads(resp.read().decode("utf-8")), "")
    except urllib.error.HTTPError as e:
        code = e.code
        detail = f"HTTP {code}"
        if code in (401, 403):
            detail = f"AUTH: HTTP {code} (run `az login` / check access)"
        return (False, None, detail)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, None, f"REST GET failed: {e}")


def _ado_rest_get_text(url, timeout):
    """GET an ADO REST url returning PLAIN TEXT (e.g. a build log). (ok, text, detail)."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        tok = subprocess.run(
            [az, "account", "get-access-token", "--resource", _ADO_RESOURCE,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    except (subprocess.TimeoutExpired, OSError) as e:
        return (False, None, f"failed to get token: {e}")
    if tok.returncode != 0 or not (tok.stdout or "").strip():
        return (False, None, "AUTH: could not get an ADO token (run `az login`)")
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {(tok.stdout or '').strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (True, resp.read().decode("utf-8", "replace"), "")
    except urllib.error.HTTPError as e:
        detail = f"AUTH: HTTP {e.code}" if e.code in (401, 403) else f"HTTP {e.code}"
        return (False, None, detail)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, None, f"REST GET failed: {e}")


def _tag_value(tags, key):
    """Return the value of a `key=value` build tag (e.g. RC-ECS=1678863 → '1678863'),
    or None. Case-sensitive key match."""
    pfx = f"{key}="
    for t in tags or []:
        if t.startswith(pfx):
            return t[len(pfx):]
    return None


def find_orchestrator_run(org, project, def_id, release_month, timeout=60):
    """Find THE Release Orchestrator run for a release month.

    Matches the run's self-tag `AuthenticatorBranch=release-<YYYY>-<MM>-*` (debug
    runs tag `test-release-*`, so they're excluded). On multiple matches returns the
    most recent by queueTime. Returns (ok, run, detail); run is the az build dict
    (incl. `tags`) or None if not found.
    """
    ok, builds, detail = _az_json(
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


def find_checker_runs(org, project, def_id, release_month, timeout=60):
    """Return (ok, runs, detail) — the checker's builds queued in the release month,
    newest first. The checker runs DAILY (a cron); only the run on the actual Code
    Complete Day triggers the release, so the caller scans these for the one whose
    'Trigger Monthly Release' stage succeeded."""
    ok, builds, detail = _az_json(
        ["pipelines", "build", "list", "--definition-ids", str(def_id),
         "--org", org, "--project", project, "--top", "60"], timeout)
    if not ok:
        return (False, None, detail)
    inmonth = [b for b in (builds or [])
               if (b.get("queueTime") or "").startswith(release_month)]
    inmonth.sort(key=lambda b: b.get("queueTime") or "", reverse=True)
    return (True, inmonth, "")


def mrwp_run_ids(org, project, orch_run, timeout=90):
    """Resolve the two MRWP (def 2519) build ids the orchestrator triggered, keyed by
    flight provider. Returns (ok, {"ECS": <id>, "Local": <id>}, detail, source).

    PRIMARY — the orchestrator run's self-tags `RC-ECS=<id>` / `RC-Local=<id>` (added
    by PR: tag orchestrator run with triggered MRWP ids). One field, no log reads.

    FALLBACK — for runs predating that tag, parse the 'Trigger RC Testing' stage's two
    'Trigger ADO Pipeline' task logs for `Run ID: <id>` + `Flight Provider: <p>`.
    `source` is 'tags' or 'logs' so callers can note which path was used.
    """
    tags = (orch_run or {}).get("tags") or []
    ecs, local = _tag_value(tags, "RC-ECS"), _tag_value(tags, "RC-Local")
    if ecs and local:
        return (True, {"ECS": ecs, "Local": local}, "", "tags")

    # Fallback: parse the trigger-task logs from the orchestrator's timeline.
    bid = (orch_run or {}).get("id")
    if not bid:
        return (False, None, "orchestrator run has no id", "logs")
    ok, tl, detail = _az_json(
        ["devops", "invoke", "--org", org, "--area", "build", "--resource", "timeline",
         "--route-parameters", f"project={project}", f"buildId={bid}",
         "--api-version", "7.1"], timeout)
    if not ok:
        return (False, None, detail, "logs")
    recs = (tl or {}).get("records", []) or []
    trigger_tasks = [r for r in recs
                     if r.get("type") == "Task" and r.get("name") == "Trigger ADO Pipeline"
                     and (r.get("log") or {}).get("id")]
    import re as _re
    found = {}
    base = org.rstrip("/")
    for t in trigger_tasks:
        log_id = t["log"]["id"]
        url = f"{base}/{project}/_apis/build/builds/{bid}/logs/{log_id}?api-version=7.1"
        ok2, txt, _ = _ado_rest_get_text(url, timeout)
        if not ok2 or not txt:
            continue
        m_id = _re.search(r"Run ID:\s*(\d+)", txt)
        m_pr = _re.search(r"Flight Provider:\s*(ECS|Local)", txt, _re.IGNORECASE)
        if m_id and m_pr:
            prov = "ECS" if m_pr.group(1).upper() == "ECS" else "Local"
            found[prov] = m_id.group(1)
    if found.get("ECS") and found.get("Local"):
        return (True, {"ECS": found["ECS"], "Local": found["Local"]}, "", "logs")
    return (False, None, f"could not resolve both MRWP ids (got {found or 'none'})", "logs")


def get_timeline(org, project, build_id, timeout=60):
    """Return (ok, records, detail) — the raw timeline records for a build (Stage /
    Phase / Job / Task). Callers filter by type/name."""
    ok, tl, detail = _az_json(
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


def get_stages(org, project, build_id, timeout=60):
    """Return (ok, stages, detail). `stages` is an ORDER-sorted list of
    {name, state, result} from the build's timeline (Stage records only)."""
    ok, recs, detail = get_timeline(org, project, build_id, timeout)
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


def get_test_summary(org, project, build_id, timeout=60):
    """Return (ok, summary, detail) for a build's Test-tab results. summary =
    {total, passed, failed, runs:[{name,total,passed,failed}]} aggregated across all
    test runs associated with the build (unit / instrumented / UI-automation).

    Uses the Test Runs REST API directly (az devops invoke mis-routes this one)."""
    base = org.rstrip("/")
    url = (f"{base}/{project}/_apis/test/runs"
           f"?buildUri=vstfs:///Build/Build/{build_id}&api-version=7.1")
    ok, data, detail = _ado_rest_get(url, timeout)
    if not ok:
        return (False, None, detail)
    runs = (data or {}).get("value", []) or []
    out_runs, tot, passed = [], 0, 0
    for r in runs:
        t = r.get("totalTests") or 0
        p = r.get("passedTests") or 0
        na = r.get("notApplicableTests") or 0
        f = max(t - p - na, 0)
        tot += t
        passed += p
        out_runs.append({"name": r.get("name"), "total": t, "passed": p, "failed": f})
    return (True, {"total": tot, "passed": passed, "failed": max(tot - passed, 0),
                   "runs": out_runs}, "")
