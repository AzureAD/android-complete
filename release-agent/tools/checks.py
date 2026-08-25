"""Real checks for readiness verifiers (no fakery).

- ado_build_def: uses `az pipelines build definition show` to prove the signed-in
  user can actually access a build definition. Genuine access check.
- http_reachable: HEAD/GET a URL and report the status. NOTE: for auth-gated web
  apps (Play Console, ADX) an HTTP 200 only proves the URL is reachable — it does
  NOT prove the user has sign-in access, since a login page also returns 200.
"""
from __future__ import annotations
import subprocess
import shutil
from dataclasses import dataclass
from urllib import request as _request
from urllib.error import URLError, HTTPError


@dataclass
class CheckResult:
    ok: bool
    verified_access: bool   # True only when we truly proved access (not mere reachability)
    detail: str


def check_ado_build_def(org: str, project: str, def_id: int, timeout: int = 30) -> CheckResult:
    az = shutil.which("az")
    if az is None:
        return CheckResult(False, False, "az CLI not found")
    try:
        out = subprocess.run(
            [az, "pipelines", "build", "definition", "show",
             "--id", str(def_id), "--org", org, "--project", project,
             "--query", "name", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(False, False, f"timeout querying build def {def_id}")
    except OSError as e:
        return CheckResult(False, False, f"failed to run az: {e}")
    if out.returncode == 0 and out.stdout.strip():
        return CheckResult(True, True, f"accessible: '{out.stdout.strip()}'")
    err_text = (out.stderr or "").strip()
    low = err_text.lower()
    # Distinguish a real ACCESS problem from an az-not-authenticated-to-ADO state
    # (org rejects the CLI's AAD token — often Conditional Access — and returns the
    # sign-in page / 401). Give an actionable message instead of a raw 401.
    if ("requires user authentication" in low or "unauthorized" in low
            or "sign in" in low or "tf400813" in low or "no credentials" in low):
        return CheckResult(
            False, True,
            f"az is not authenticated to Azure DevOps (build def {def_id}); the org "
            f"rejected the CLI token. Run `az login`; if it still fails (Conditional "
            f"Access), sign in with a PAT: `az devops login --organization {org}` "
            f"(Build: Read scope).")
    err = err_text.splitlines()
    msg = err[-1] if err else f"cannot access build def {def_id}"
    return CheckResult(False, True, msg[:200])


def current_az_user(timeout: int = 20):
    """Return the signed-in user's email/UPN from `az account show`, or None.
    Used to resolve the release owner without hardcoding an address."""
    az = shutil.which("az")
    if az is None:
        return None
    try:
        out = subprocess.run(
            [az, "account", "show", "--query", "user.name", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode == 0:
        val = (out.stdout or "").strip()
        return val or None
    return None


# ---- OneAuth write-access probe (Phase-0 oneauth_access step) ----
#
# Later phases push a PR to the OneAuth repo, whose branch policy requires
# 'user/<alias>/<branch-name>'. The only reliable proof of write access is to actually
# CREATE such a branch — so this creates a throwaway 'user/<alias>/scout-oneauth-access-check'
# ref and immediately deletes it. Success => write access; a create rejection / 403 => none.
ONEAUTH_ORG = "https://office.visualstudio.com"
ONEAUTH_PROJECT = "OneAuth"
ONEAUTH_REPO = "OneAuth"
ONEAUTH_REPO_URL = "https://office.visualstudio.com/OneAuth/_git/OneAuth"
# The myaccess package that grants OneAuth R/W (repo) for external contributors.
ONEAUTH_ACCESS_PACKAGE = ("https://myaccess.microsoft.com/@microsoft.onmicrosoft.com#/"
                          "access-packages/09fdec6b-eafa-4905-a7c0-b5e514bba368")
_ZERO_SHA = "0" * 40


def oneauth_write_access(alias, timeout: int = 60):
    """Probe write access to the OneAuth repo by CREATING (then deleting) a
    'user/<alias>/scout-oneauth-access-check' branch off master. Returns (granted, detail):
    granted is True only when the branch create succeeds (it is then cleaned up); on any
    create rejection / permission error it is False. Self-cleaning and idempotent — a stale
    probe branch from a crashed run is removed before the create."""
    from tools import pipelines as P
    base = f"{ONEAUTH_ORG}/{ONEAUTH_PROJECT}/_apis/git/repositories/{ONEAUTH_REPO}"
    ref = f"refs/heads/user/{alias}/scout-oneauth-access-check"
    ref_filter = f"heads/user/{alias}/scout-oneauth-access-check"

    ok, refs, d = P._ado_rest_get(f"{base}/refs?filter=heads/master&api-version=7.1", timeout)
    if not ok:
        return (False, f"could not reach the OneAuth repo ({d})")
    vals = (refs or {}).get("value") or []
    tip = vals[0].get("objectId") if vals else None
    if not tip:
        return (False, "could not resolve the OneAuth master branch tip")

    def _update(old, new):
        return P._ado_rest_send(f"{base}/refs?api-version=7.1", "POST",
                                [{"name": ref, "oldObjectId": old, "newObjectId": new}], timeout)

    # remove a stale probe branch left by a crashed prior run (best-effort)
    okx, ex, _dx = P._ado_rest_get(f"{base}/refs?filter={ref_filter}&api-version=7.1", timeout)
    if okx and (ex or {}).get("value"):
        _update((ex["value"][0] or {}).get("objectId"), _ZERO_SHA)

    okc, res, dc = _update(_ZERO_SHA, tip)
    entry = ((res or {}).get("value") or [{}])[0] if isinstance(res, dict) else {}
    if okc and entry.get("success"):
        _update(tip, _ZERO_SHA)                # cleanup
        return (True, f"created and deleted '{ref}'")
    why = entry.get("customMessage") or dc or "branch create rejected"
    return (False, f"cannot create a branch in OneAuth ({why})")


def check_http(url: str, timeout: int = 15) -> CheckResult:
    """Reachability check. verified_access is False by design — a 200 from an
    auth-gated web app does not prove the user has access."""
    req = _request.Request(url, method="HEAD", headers={"User-Agent": "release-agent-readiness/1.0"})
    try:
        with _request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
        return CheckResult(200 <= code < 400, False, f"reachable (HTTP {code})")
    except HTTPError as e:
        # Some servers reject HEAD; treat <500 as reachable
        return CheckResult(e.code < 500, False, f"reachable (HTTP {e.code})")
    except (URLError, TimeoutError) as e:
        return CheckResult(False, False, f"unreachable: {e}")
    except Exception as e:  # noqa
        return CheckResult(False, False, f"error: {e}")


# ---- CCD pipeline variables (the source of record for Code Complete Date) ----
#
# These read/write the definition-level variables on pipeline 3038 via the az CLI.
# `overrideCodeCompleteDate` and `skipRelease` are UI/definition variables, which
# is exactly what `az pipelines variable` operates on.

def read_pipeline_variable(org: str, project: str, def_id: int, name: str,
                           timeout: int = 30):
    """Return (ok, value, detail). value is the variable's string value (may be '')
    or None if the variable isn't defined. ok is False only on a real access/CLI error."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        out = subprocess.run(
            [az, "pipelines", "variable", "list", "--pipeline-id", str(def_id),
             "--org", org, "--project", project, "-o", "json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (False, None, f"timeout listing variables on pipeline {def_id}")
    except OSError as e:
        return (False, None, f"failed to run az: {e}")
    if out.returncode != 0:
        err = (out.stderr or "").strip().splitlines()
        return (False, None, (err[-1] if err else "az returned non-zero")[:160])
    import json as _json
    try:
        data = _json.loads(out.stdout or "{}")
    except ValueError:
        return (False, None, "could not parse az output")
    if name in data:
        return (True, (data[name] or {}).get("value", "") or "", "ok")
    return (True, None, f"variable '{name}' not defined")


def set_pipeline_variable(org: str, project: str, def_id: int, name: str,
                          value: str, timeout: int = 30) -> CheckResult:
    """Write a definition variable (update, creating it if absent). A real
    production change — callers must gate this behind explicit confirmation."""
    az = shutil.which("az")
    if az is None:
        return CheckResult(False, False, "az CLI not found")
    base = [az, "pipelines", "variable", "{verb}", "--pipeline-id", str(def_id),
            "--org", org, "--project", project, "--name", name, "--value", value]

    def _run(verb):
        cmd = [c.replace("{verb}", verb) for c in base]
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        except OSError:
            return None

    out = _run("update")
    if out is not None and out.returncode == 0:
        return CheckResult(True, True, f"{name} = '{value}'")
    # update fails if the variable doesn't exist yet — try create.
    out2 = _run("create")
    if out2 is not None and out2.returncode == 0:
        return CheckResult(True, True, f"{name} created = '{value}'")
    err = ""
    for o in (out2, out):
        if o is not None and (o.stderr or "").strip():
            err = (o.stderr or "").strip().splitlines()[-1]
            break
    return CheckResult(False, True, (err or f"could not set {name}")[:200])


# ---- ADO wiki (payload subpage) --------------------------------------------
#
# Creates a wiki page via `az devops wiki page create`. A real production write,
# so callers must gate it (the pre-flight agent only calls this on a real
# release). Idempotent-ish: an already-existing page is treated as success.

def wiki_page_exists(org: str, project: str, wiki: str, path: str,
                     timeout: int = 30):
    """True/False whether a wiki page exists, or None if it can't be determined
    (e.g. az missing / CLI error) — callers should treat None conservatively."""
    az = shutil.which("az")
    if az is None:
        return None
    try:
        out = subprocess.run(
            [az, "devops", "wiki", "page", "show", "--path", path,
             "--wiki", wiki, "--org", org, "--project", project, "-o", "json"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode == 0:
        return True
    if "could not be found" in (out.stderr or "").lower() or "notfound" in (out.stderr or "").lower():
        return False
    return None


def create_wiki_page(org: str, project: str, wiki: str, path: str,
                     content: str, timeout: int = 60) -> CheckResult:
    az = shutil.which("az")
    if az is None:
        return CheckResult(False, False, "az CLI not found")
    import tempfile
    import os as _os
    fd, tmp = tempfile.mkstemp(suffix=".md")
    _os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        cmd = [az, "devops", "wiki", "page", "create", "--path", path,
               "--wiki", wiki, "--org", org, "--project", project,
               "--file-path", tmp, "--output", "json"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return CheckResult(False, True, f"timeout creating wiki page '{path}'")
        except OSError as e:
            return CheckResult(False, False, f"failed to run az: {e}")
        if out.returncode == 0:
            return CheckResult(True, True, f"created '{path}'")
        stderr = (out.stderr or "").strip()
        if "exist" in stderr.lower():                 # already there — fine
            return CheckResult(True, True, f"page '{path}' already exists")
        msg = stderr.splitlines()[-1] if stderr else f"could not create '{path}'"
        return CheckResult(False, True, msg[:200])
    finally:
        try:
            _os.remove(tmp)
        except OSError:
            pass


# ---- Component Governance alerts (read-only) -------------------------------
#
# CG alerts live on a separate governance host and are read via `az rest`
# (the signed-in user's token). Read-only — we only report.

def fetch_cg_alerts(resource: str, host: str, project_id: str, repo_id: int,
                    branch: str, timeout: int = 60):
    """Return (ok, alerts, detail). `alerts` is the raw list of alert dicts for
    the branch (all states). ok is False on a real CLI/access error."""
    az = shutil.which("az")
    if az is None:
        return (False, [], "az CLI not found")
    url = (f"{host}/{project_id}/_apis/ComponentGovernance/GovernedRepositories/"
           f"{repo_id}/Branches/{branch}/Alerts")
    try:
        out = subprocess.run(
            [az, "rest", "--method", "get", "--resource", resource, "--uri", url, "-o", "json"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        return (False, [], "timeout querying Component Governance alerts")
    except OSError as e:
        return (False, [], f"failed to run az: {e}")
    if out.returncode != 0:
        err = (out.stderr or "").strip().splitlines()
        return (False, [], (err[-1] if err else "az returned non-zero")[:200])
    import json as _json
    try:
        data = _json.loads(out.stdout or "{}")
    except ValueError:
        return (False, [], "could not parse az output")
    return (True, data.get("value", []) or [], "ok")


# ---- scheduled-pipeline verification (Calendar Checker) ---------------------
#
# A YAML pipeline's cron schedule isn't exposed in its definition triggers, but
# whether it's actually FIRING is provable from its build history: a recent
# `schedule`-reason run means the cron is live. Read-only.

def latest_scheduled_build(org: str, project: str, def_id: int, timeout: int = 60):
    """Return (ok, run, detail). `run` is a dict {queueTime, result, status} for
    the most recent schedule-reason build, or None if none in recent history."""
    az = shutil.which("az")
    if az is None:
        return (False, None, "az CLI not found")
    try:
        out = subprocess.run(
            [az, "pipelines", "build", "list", "--definition-ids", str(def_id),
             "--org", org, "--project", project, "--top", "25", "-o", "json"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        return (False, None, f"timeout listing builds for definition {def_id}")
    except OSError as e:
        return (False, None, f"failed to run az: {e}")
    if out.returncode != 0:
        err = (out.stderr or "").strip().splitlines()
        return (False, None, (err[-1] if err else "az returned non-zero")[:200])
    import json as _json
    try:
        builds = _json.loads(out.stdout or "[]")
    except ValueError:
        return (False, None, "could not parse az output")
    sched = [b for b in builds if b.get("reason") == "schedule"]
    if not sched:
        return (True, None, "no scheduled runs in recent history")
    latest = max(sched, key=lambda b: b.get("queueTime") or "")
    return (True, {"queueTime": latest.get("queueTime"), "result": latest.get("result"),
                   "status": latest.get("status")}, "ok")

