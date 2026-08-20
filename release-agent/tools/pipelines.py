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
    or None. Case-sensitive key match; first match wins."""
    pfx = f"{key}="
    for t in tags or []:
        if t.startswith(pfx):
            return t[len(pfx):]
    return None


def _tag_values(tags, key):
    """All values for a `key=value` build tag. A re-triggered 'Trigger RC Testing'
    stage adds NEW RC-<provider>=<id> tags alongside the old, so a provider can have
    several — use `_newest_id` to pick the current run."""
    pfx = f"{key}="
    return [t[len(pfx):] for t in (tags or []) if t.startswith(pfx)]


def _newest_id(ids):
    """The newest build id from a list. ADO build ids increase monotonically, so the
    max numeric id is the most-recent run — this is how a re-trigger's fresh MRWP run
    wins over the failed earlier one. Returns a string, or None if empty."""
    nums = [str(i) for i in (ids or []) if str(i).isdigit()]
    if nums:
        return str(max(int(i) for i in nums))
    return (ids[0] if ids else None)


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
    ecs = _newest_id(_tag_values(tags, "RC-ECS"))
    local = _newest_id(_tag_values(tags, "RC-Local"))
    if ecs and local:
        return (True, {"ECS": ecs, "Local": local}, "", "tags")

    # Fallback: parse the trigger-task logs from the orchestrator's timeline. On a
    # re-trigger there are extra 'Trigger ADO Pipeline' tasks — collect ALL ids per
    # provider and take the newest so the fresh run wins.
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
    found = {"ECS": [], "Local": []}
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
            found[prov].append(m_id.group(1))
    ecs, local = _newest_id(found["ECS"]), _newest_id(found["Local"])
    if ecs and local:
        return (True, {"ECS": ecs, "Local": local}, "", "logs")
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


import re as _re_mod
_UI_API_RE = _re_mod.compile(r"\(API\s*\d+\)", _re_mod.IGNORECASE)
TEST_CATEGORIES = ("unit", "instrumented", "ui")
_CATEGORY_LABEL = {"unit": "Unit", "instrumented": "Instrumented", "ui": "UI automation"}


def classify_test_run(name):
    """Bucket a test-run/suite name into one of THREE categories:
      * '*_UnitTests'          → unit
      * '*_InstrumentedTests'  → instrumented
      * everything else        → ui   (the device UI-automation suites, which carry an
                                       '(API NN)' tag, plus any other run such as
                                       'Lab Api Tests' — 'the rest are UI').
    """
    low = (name or "").lower()
    if "unittest" in low:
        return "unit"
    if "instrumentedtest" in low:
        return "instrumented"
    return "ui"


# Outcomes that are neither a pass nor a real failure (skipped / not run / inconclusive).
_NA_OUTCOMES = {"NotExecuted", "NotApplicable", "None", "Inconclusive", "Warning", None}


def reconcile_retries(results):
    """Collapse ADO's per-attempt test results into ONE verdict per test (by title).

    UNIT tests run under a RETRY rule: a flaky test can appear several times in the same
    run — e.g. Passed, Failed, Passed. ADO's run aggregate still counts that as a failure,
    but the test ultimately PASSED. This groups results by testCaseTitle and rules:
      * PASSED    — at least one attempt Passed.
      * RECOVERED — Passed AND Failed on different attempts (a flaky pass — surfaced as a
                    warning, but counted as passed).
      * FAILED    — has a real (non-NA) attempt and NEVER passed.
    Not-executed / not-applicable attempts are ignored. Counts are DISTINCT tests. Returns
      {passed, failed, recovered:[titles], total, na}."""
    import collections
    by = collections.defaultdict(set)
    for r in results or []:
        title = (r.get("testCaseTitle") or r.get("automatedTestName") or "").strip()
        if not title:
            continue
        by[title].add(r.get("outcome"))
    passed = failed = na = 0
    recovered = []
    for title, outs in by.items():
        eff = {o for o in outs if o not in _NA_OUTCOMES}
        if not eff:
            na += 1
            continue
        if "Passed" in eff:
            passed += 1
            if "Failed" in eff:
                recovered.append(title)
        else:
            failed += 1
    return {"passed": passed, "failed": failed, "recovered": sorted(recovered),
            "total": passed + failed, "na": na}


def _run_results(org, project, run_id, timeout=90, page=1000, cap=10000):
    """All test results for a run (paged). Returns (ok, [results], detail)."""
    base = org.rstrip("/")
    out, skip = [], 0
    while len(out) < cap:
        url = (f"{base}/{project}/_apis/test/Runs/{run_id}/results"
               f"?api-version=7.1&$top={page}&$skip={skip}")
        ok, data, detail = _ado_rest_get(url, timeout)
        if not ok:
            return (False, out, detail)
        batch = (data or {}).get("value", []) or []
        out.extend(batch)
        if len(batch) < page:
            break
        skip += page
    return (True, out, "")


def get_test_summary(org, project, build_id, timeout=60):
    """Return (ok, summary, detail) for a build's Test-tab results. summary =
    {total, passed, failed, runs:[{name,total,passed,failed,category,recovered}],
     categories:{unit|instrumented|ui: {total,passed,failed,recovered:[titles]}}}
    aggregated across all test runs, classified into unit / instrumented / UI-automation.

    UNIT runs apply the retry rule (reconcile_retries): a run WITH failures is re-read at
    the per-result level so a flaky test that failed-then-passed counts as passed (and is
    reported as `recovered`). UI/instrumented runs use ADO's run aggregate unchanged.

    Uses the Test Runs REST API directly (az devops invoke mis-routes this one)."""
    base = org.rstrip("/")
    url = (f"{base}/{project}/_apis/test/runs"
           f"?buildUri=vstfs:///Build/Build/{build_id}&api-version=7.1")
    ok, data, detail = _ado_rest_get(url, timeout)
    if not ok:
        return (False, None, detail)
    runs = (data or {}).get("value", []) or []
    out_runs, tot, passed = [], 0, 0
    cats = {c: {"total": 0, "passed": 0, "failed": 0, "recovered": []} for c in TEST_CATEGORIES}
    for r in runs:
        t = r.get("totalTests") or 0
        p = r.get("passedTests") or 0
        na = r.get("notApplicableTests") or 0
        f = max(t - p - na, 0)
        cat = classify_test_run(r.get("name"))
        recovered = []
        # UNIT retry rule: re-read a failing unit run per-result and reconcile flaky passes.
        if cat == "unit" and f > 0:
            ok2, results, _ = _run_results(org, project, r.get("id"), timeout)
            if ok2 and results:
                rec = reconcile_retries(results)
                t, p, f, recovered = rec["total"], rec["passed"], rec["failed"], rec["recovered"]
        tot += t
        passed += p
        cats[cat]["total"] += t
        cats[cat]["passed"] += p
        cats[cat]["failed"] += f
        if recovered:
            cats[cat]["recovered"].extend(recovered)
        out_runs.append({"name": r.get("name"), "total": t, "passed": p,
                         "failed": f, "category": cat, "recovered": recovered})
    failed_total = sum(c["failed"] for c in cats.values())
    return (True, {"total": tot, "passed": passed, "failed": failed_total,
                   "runs": out_runs, "categories": cats}, "")


def _suite_base_name(name):
    """The test API returns the same suite as several runs, each named
    '<suite> # <buildlabel>' — strip the ' # …' run suffix so same-suite runs merge."""
    return ((name or "").split(" # ")[0].strip()) or "(unnamed suite)"


def get_failed_tests(org, project, build_id, max_result_calls=20, per_suite_cap=40, timeout=90):
    """Return (ok, suites, detail) — the individual FAILING tests for a build, aggregated
    by suite name (the same suite appears as multiple runs; merged). suites is a list of
    {name, failed, total, category, tests:[titles], recovered:[titles]}, sorted by failure
    count desc. Test titles are fetched for the worst runs first, bounded by
    max_result_calls; per suite capped at per_suite_cap names.

    UNIT suites apply the retry rule: a failing unit run is re-read per-result and
    reconciled (reconcile_retries), so a flaky test that failed-then-passed is NOT listed
    as a failure — it's collected under `recovered` and a suite that fully recovers is
    dropped."""
    base = org.rstrip("/")
    url = (f"{base}/{project}/_apis/test/runs"
           f"?buildUri=vstfs:///Build/Build/{build_id}&api-version=7.1")
    ok, data, detail = _ado_rest_get(url, timeout)
    if not ok:
        return (False, None, detail)

    def fcount(r):
        return max((r.get("totalTests") or 0) - (r.get("passedTests") or 0)
                   - (r.get("notApplicableTests") or 0), 0)

    failing = sorted((r for r in (data or {}).get("value", []) if fcount(r) > 0),
                     key=lambda r: -fcount(r))
    suites, calls = {}, 0
    for r in failing:
        name = _suite_base_name(r.get("name"))
        cat = classify_test_run(name)
        s = suites.setdefault(name, {"name": name, "failed": 0, "total": 0,
                                     "category": cat, "tests": [], "recovered": []})
        # UNIT retry rule: reconcile per-result so flaky-recovered tests aren't failures.
        if cat == "unit":
            ok2, results, _ = _run_results(org, project, r.get("id"), timeout)
            if ok2:
                rec = reconcile_retries(results)
                s["failed"] += rec["failed"]
                s["total"] += rec["total"]
                for t in rec["recovered"]:
                    if t not in s["recovered"]:
                        s["recovered"].append(t)
                # only the tests that truly failed (never passed) — reconcile again for names
                import collections
                by = collections.defaultdict(set)
                for res in results:
                    title = (res.get("testCaseTitle") or res.get("automatedTestName") or "").strip()
                    if title:
                        by[title].add(res.get("outcome"))
                for title, outs in by.items():
                    eff = {o for o in outs if o not in _NA_OUTCOMES}
                    if eff and "Passed" not in eff and title not in s["tests"] \
                            and len(s["tests"]) < per_suite_cap:
                        s["tests"].append(title)
            continue
        # NON-unit (UI / instrumented) — ADO aggregate + the failed titles (unchanged).
        s["failed"] += fcount(r)
        s["total"] += r.get("totalTests") or 0
        if calls < max_result_calls:
            calls += 1
            rurl = (f"{base}/{project}/_apis/test/Runs/{r.get('id')}/results"
                    f"?outcomes=Failed&$top=100&api-version=7.1")
            ok2, rdata, _ = _ado_rest_get(rurl, timeout)
            if ok2:
                for res in (rdata or {}).get("value", []):
                    title = (res.get("testCaseTitle") or res.get("automatedTestName") or "").strip()
                    if title and title not in s["tests"] and len(s["tests"]) < per_suite_cap:
                        s["tests"].append(title)
    # Drop suites whose failures all recovered on retry (unit); keep real failures.
    real = [s for s in suites.values() if s["failed"] > 0]
    return (True, sorted(real, key=lambda x: -x["failed"]), "")


# ── ADO targets — the release toolchain spans MULTIPLE orgs/projects ─────────────────
# There is NO single global org/project. Name each target explicitly so a caller can't
# accidentally point a call at the wrong one:
#   • identitydivision  — the collection hosting Engineering (release chain) + IdentityWiki
#   • msazure           — hosts One (localization pipeline 405133, Component Governance)
# These constants cover ONLY the release-VERIFICATION chain (checker / orchestrator / MRWP),
# which lives in identitydivision/Engineering. Other areas own their own coordinates:
# localization → steps/ccd/localization.py (msazure/One); wiki → steps/preflight/wiki.py
# (identitydivision/IdentityWiki); CG → steps/preflight/cg.py (msazure/One).
IDENTITYDIVISION = "https://identitydivision.visualstudio.com"
MSAZURE = "https://msazure.visualstudio.com"

# The release-verification pipelines: identitydivision / Engineering — SINGLE SOURCE.
# `steps/build_verify/_common.py` imports these (it does not redefine them).
ENGINEERING_ORG = IDENTITYDIVISION
ENGINEERING_PROJECT = "Engineering"
CHECKER_DEF = 3038          # Code Complete Calendar Checker (fires the release on the CCD)
ORCHESTRATOR_DEF = 2828     # Release Orchestrator (the spine)
MRWP_DEF = 2519             # Monthly Release Work Pipeline (RC testing; runs ECS + Local)
TRIGGER_JOB = "Trigger Monthly Release"
ORCH_REQUIRED_STAGES = [
    "Validate Branch and Versions availability",
    "Create Release Branches",
    "Trigger RC Testing",
]
ORCH_PARK_STAGE = "Remove RC Tags"


def assemble_rc_model(release, checker, orchestrator, mrwp, *, rc=None,
                      id_source=None, io_problems=None):
    """The ONE canonical Phase-2 RC report model — built from already-resolved pieces,
    whether they came from LIVE reads (release_report) or the state snapshot
    (steps.build_verify._common.rc_report_model). Both paths call this so they can never
    drift on shape or on how `problems` are derived.

    Sections:
      checker      : {fired, run_id, when} | {fired:False} | {error}
      orchestrator : {found, healthy, run_id, versions, parked, failed_stages, park_stage} | {found:False[,error]}
      mrwp         : {ECS:{...}, Local:{...}}   each: run_id, complete, ran, total,
                     failed_stages, yellow_stages, never_ran, tests, failed_suites [, error]
    `problems` is derived here in section order (checker → orchestrator → io_problems →
    mrwp) from each section's `error`/structural state, so the messages are identical for
    both callers. `io_problems` are non-sectional read failures (e.g. MRWP id resolution).
    Returns {release, checker, orchestrator, mrwp, problems, rc[, mrwp_id_source]}.
    """
    checker = checker or {}
    orchestrator = orchestrator or {}
    mrwp = mrwp or {}
    problems = []

    if checker.get("error"):
        problems.append(f"Checker: could not read runs ({checker['error']}).")
    elif checker.get("fired") is False:
        problems.append("Checker: no run has a succeeded 'Trigger Monthly Release' job "
                        "(release not triggered yet, or before Code Complete Day).")

    o = orchestrator
    if o.get("found") is False:
        problems.append(f"Orchestrator: could not read runs ({o['error']})." if o.get("error")
                        else f"Orchestrator: no run found for {release}.")
    elif o.get("error"):
        problems.append(f"Orchestrator: could not read stages ({o['error']}).")
    elif o.get("failed_stages"):
        problems.append("Orchestrator: pre-gate stage(s) not green: "
                        + ", ".join(o["failed_stages"]) + ".")

    problems.extend(io_problems or [])

    for provider in ("ECS", "Local"):
        e = mrwp.get(provider)
        if not e:
            continue
        if e.get("error"):
            problems.append(f"MRWP {provider}: could not read stages ({e['error']}).")
        elif e.get("complete") is False:
            nv = ", ".join(n for n in (e.get("never_ran") or []) if n) or "(unknown)"
            problems.append(f"MRWP {provider}: did NOT run to completion — never-ran: {nv}.")

    model = {"release": release, "checker": checker, "orchestrator": orchestrator,
             "mrwp": mrwp, "problems": problems, "rc": rc}
    if id_source is not None:
        model["mrwp_id_source"] = id_source
    return model


def release_report(org, project, release_month, checker_def=CHECKER_DEF,
                   orch_def=ORCHESTRATOR_DEF, timeout=90, with_failed_tests=True):
    """Assemble the full Phase-2 RC-pipeline + test report for a release month by LIVE
    reads (does NOT gate; it reports). Resolves the checker / orchestrator / both MRWP
    runs, then hands the pieces to `assemble_rc_model` (the shared assembler) so this live
    path and the state-snapshot path produce an identical model shape + problems. Any
    field that couldn't be read carries an `error` note (surfaced as a problem).

    `with_failed_tests` (default True) also fetches the individual failing test names per
    suite (extra REST calls — set False for a faster stage-only view)."""
    # --- checker (did the release fire?) ---
    ok, runs, detail = find_checker_runs(org, project, checker_def, release_month, timeout)
    if not ok:
        checker = {"error": detail}
    else:
        fired = None
        for run in (runs or [])[:25]:
            ok2, recs, _ = get_timeline(org, project, run.get("id"), timeout)
            if not ok2:
                continue
            rec = named_record(recs, TRIGGER_JOB)
            if rec is not None and rec.get("result") == "succeeded":
                fired = run
                break
        checker = ({"fired": True, "run_id": fired.get("id"),
                    "when": (fired.get("queueTime") or "")[:16]} if fired else {"fired": False})

    # --- orchestrator (healthy? parked?) ---
    ok, orun, detail = find_orchestrator_run(org, project, orch_def, release_month, timeout)
    if not ok:
        return assemble_rc_model(release_month, checker, {"found": False, "error": detail}, {})
    if not orun:
        return assemble_rc_model(release_month, checker, {"found": False}, {})

    oid, tags = orun.get("id"), (orun.get("tags") or [])
    versions = {k: _tag_value(tags, f"Next{k}Version") for k in ("Common", "Msal", "Broker")}
    ok, ostages, detail = get_stages(org, project, oid, timeout)
    o = {"found": True, "run_id": oid, "versions": versions,
         "park_stage": ORCH_PARK_STAGE, "failed_stages": [], "healthy": None, "parked": None}
    if not ok:
        o["error"] = detail
    else:
        by = {s.get("name"): s for s in ostages}
        o["failed_stages"] = [n for n in ORCH_REQUIRED_STAGES
                              if (by.get(n) or {}).get("result") != "succeeded"]
        o["healthy"] = not o["failed_stages"]
        park = by.get(ORCH_PARK_STAGE)
        o["parked"] = bool(park and park.get("state") != "completed")

    # --- the two MRWP runs (ran to completion? tests?) ---
    ok, ids, detail, source = mrwp_run_ids(org, project, orun, timeout)
    if not ok:
        return assemble_rc_model(release_month, checker, o, {},
                                 io_problems=[f"MRWP: could not resolve run ids ({detail})."])
    mrwp = {}
    for provider in ("ECS", "Local"):
        bid = ids.get(provider)
        entry = {"run_id": bid}
        ok, stages, detail = get_stages(org, project, bid, timeout)
        if not ok:
            entry["error"] = detail
        else:
            comp = stage_completion(stages)
            entry.update({"complete": comp["complete"], "ran": comp["ran"],
                          "total": comp["total"], "failed_stages": comp["failed"],
                          "yellow_stages": comp["yellow"], "never_ran": comp["never_ran"]})
        okt, tests, _ = get_test_summary(org, project, bid, timeout)
        entry["tests"] = tests if okt else None
        # Individual failing tests, aggregated by suite (deduped across repeated runs).
        if with_failed_tests and bid and tests and tests.get("failed"):
            okf, suites, _ = get_failed_tests(org, project, bid, timeout=timeout)
            entry["failed_suites"] = suites if okf else None
        mrwp[provider] = entry
    return assemble_rc_model(release_month, checker, o, mrwp, id_source=source)
