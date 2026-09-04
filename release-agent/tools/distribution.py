"""Test distribution for Phase 3 (bug_bash) — the `distribute_tests` step.

Distributes the release's manual bug-bash tests EVENLY across the eligible team while
preserving existing assignments as much as possible ("keep your preference"). Two test
sets are combined into one fair split:

  * BROKER — the test cases in the "Manual Tests (Android Broker)" subtree of the Broker
    plan (the release's cloned plan references the master's cases).
  * AUTHENTICATOR — the ReleaseBugBash query set (tools.testplans.auth_bugbash_query)
    minus cases tagged Automated (run in CI) or Blocked (can't be run manually).

Eligible testers = members of the roster DL (config/distribution.yaml `roster_group`)
MINUS: the always-excluded people, the release owner, and the current on-call engineer
(OCE). The OCE's team id is read from readiness.yaml (oncall_now.team_id) so the entry
gate and this step share ONE source of truth.

Default assignment source is each test case's `System.AssignedTo` (decision: consistent
for both plans). Applying the distribution writes `System.AssignedTo` back on the case
work items — so this release's assignment becomes next release's default preference.

The `distribute()` algorithm is a pure function (no I/O) so it is fully unit-testable;
the ADO/Graph gatherers and the write are separate.
"""
from __future__ import annotations

import json as _json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
import urllib.error

import yaml

from tools import pipelines as P
from tools import testplans as T

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
ORG = T.ORG
PROJECT = T.PROJECT
_GRAPH = "https://graph.microsoft.com/v1.0"
_GRAPH_RESOURCE = "https://graph.microsoft.com"


# ----------------------------------------------------------------- config

def load_config(path: str = None) -> dict:
    """The parsed config/distribution.yaml."""
    p = path or os.path.join(_CONFIG_DIR, "distribution.yaml")
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def oncall_team(readiness_path: str = None):
    """(team_id, team_name) for the on-call team — read from readiness.yaml's oncall_now
    item so this step and the entry gate share ONE source. (None, None) if not configured."""
    p = readiness_path or os.path.join(_CONFIG_DIR, "readiness.yaml")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except OSError:
        return (None, None)
    for it in doc.get("items") or []:
        if it.get("id") == "oncall_now":
            return (it.get("team_id"), it.get("team_name"))
    return (None, None)


# ----------------------------------------------------------------- pure algorithm

def eligible_testers(roster, always_excluded, owner=None, oce=None):
    """The people tests are distributed to: roster MINUS always_excluded, the owner, and
    the OCE. Comparison is case-insensitive on the identifier (UPN/email). Order preserved.
    `roster` is a list of identifiers; the excludes are identifiers too."""
    drop = {str(x).strip().lower() for x in (always_excluded or [])}
    for x in (owner, oce):
        if x:
            drop.add(str(x).strip().lower())
    return [p for p in roster if str(p).strip().lower() not in drop]


def distribute(tests, eligible):
    """Assign each test to an eligible tester, EVEN counts (±1), preserving each test's
    default assignee where possible.

    `tests`    — list of {"id": <case id>, "assignee": <default identifier or None>}.
    `eligible` — list of eligible tester identifiers.
    Returns {"assignments": {test_id: assignee}, "counts": {assignee: n},
             "targets": {assignee: n}, "kept": int, "reassigned": int}.

    Algorithm (converges to even while maximizing kept preferences):
      1. target counts: base = total//n, rem = total%n; the `rem` people with the most
         eligible default tests get base+1 (so heavy-default people keep their +1).
      2. keep pass: a test stays with its default assignee if that assignee is eligible
         AND still under their target.
      3. fill pass: every remaining test (default ineligible, or the default already at
         target) goes to an eligible tester still under target (fewest-first, stable)."""
    n = len(eligible)
    if n == 0:
        return {"assignments": {}, "counts": {}, "targets": {}, "kept": 0, "reassigned": 0}
    elig_set = {str(e).strip().lower(): e for e in eligible}   # lower -> canonical

    def canon(a):
        return elig_set.get(str(a).strip().lower()) if a else None

    total = len(tests)
    base, rem = divmod(total, n)

    # default eligible-test counts (for choosing who gets the +1)
    default_elig = {e: 0 for e in eligible}
    for t in tests:
        c = canon(t.get("assignee"))
        if c is not None:
            default_elig[c] += 1
    # the rem people with the most eligible defaults get base+1 (ties: input order)
    order = sorted(eligible, key=lambda e: (-default_elig[e], eligible.index(e)))
    target = {e: (base + 1 if i < rem else base) for i, e in enumerate(order)}

    counts = {e: 0 for e in eligible}
    assignments = {}
    unplaced = []

    # 1) keep pass — honor the default assignee when eligible and under target
    for t in tests:
        c = canon(t.get("assignee"))
        if c is not None and counts[c] < target[c]:
            assignments[t["id"]] = c
            counts[c] += 1
        else:
            unplaced.append(t)

    # 2) fill pass — place the rest on eligible testers under target (fewest-first, stable)
    for t in unplaced:
        pick = min(eligible, key=lambda e: (counts[e], eligible.index(e)))
        assignments[t["id"]] = pick
        counts[pick] += 1

    kept = sum(1 for t in tests
               if canon(t.get("assignee")) is not None
               and assignments.get(t["id"]) == canon(t.get("assignee")))
    return {"assignments": assignments, "counts": counts, "targets": target,
            "kept": kept, "reassigned": total - kept}


# ----------------------------------------------------------------- ADO / Graph I/O

def _graph_token(timeout=60):
    az = shutil.which("az")
    if az is None:
        return (None, "az CLI not found")
    try:
        tok = subprocess.run(
            [az, "account", "get-access-token", "--resource", _GRAPH_RESOURCE,
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    except (subprocess.TimeoutExpired, OSError) as e:
        return (None, f"failed to get token: {e}")
    if tok.returncode != 0 or not (tok.stdout or "").strip():
        return (None, "AUTH: could not get a Graph token (run `az login`)")
    return (tok.stdout.strip(), "")


def _graph_get(url, timeout=60):
    token, detail = _graph_token(timeout)
    if token is None:
        return (False, None, detail)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (True, _json.loads(resp.read().decode("utf-8")), "")
    except urllib.error.HTTPError as e:
        detail = f"AUTH: HTTP {e.code}" if e.code in (401, 403) else f"HTTP {e.code}"
        return (False, None, detail)
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, None, f"Graph GET failed: {e}")


def resolve_roster(group_mail, timeout=60):
    """Resolve a mail-enabled group's member USERS to identifiers.
    Returns (ok, [{name, upn}], detail). Follows @odata.nextLink; users only."""
    flt = urllib.parse.quote(f"mail eq '{group_mail}'")
    ok, g, d = _graph_get(
        f"{_GRAPH}/groups?$filter={flt}&$select=id,displayName", timeout)
    if not ok:
        return (False, None, d)
    vals = (g or {}).get("value") or []
    if not vals:
        return (False, None, f"group '{group_mail}' not found")
    gid = vals[0]["id"]
    members, url = [], (f"{_GRAPH}/groups/{gid}/members"
                        f"?$select=displayName,userPrincipalName,mail&$top=100")
    for _ in range(20):
        ok, j, d = _graph_get(url, timeout)
        if not ok:
            return (False, None, d)
        for m in (j or {}).get("value") or []:
            if (m.get("@odata.type") or "").endswith("user") or m.get("userPrincipalName"):
                members.append({"name": m.get("displayName"),
                                "upn": m.get("userPrincipalName") or m.get("mail")})
        url = (j or {}).get("@odata.nextLink")
        if not url:
            break
    return (True, members, "")


def _suite_subtree(plan_id, root_suite, timeout=90):
    """All suite ids under root_suite (inclusive)."""
    ok, suites, d = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/suites?api-version=7.1", timeout)
    if not ok:
        return (False, None, d)
    children = {}
    for s in suites:
        children.setdefault((s.get("parentSuite") or {}).get("id"), []).append(s["id"])
    out, stack = [], [root_suite]
    while stack:
        sid = stack.pop()
        out.append(sid)
        stack += children.get(sid, [])
    return (True, out, "")


def _cases_assignedto(case_ids, timeout=90):
    """{case_id(str): assignedTo_upn_or_None} for a batch of test-case work items."""
    out = {}
    ids = [str(i) for i in case_ids if i]
    for i in range(0, len(ids), 190):
        batch = ",".join(ids[i:i + 190])
        url = (f"{ORG}/{PROJECT}/_apis/wit/workitems?ids={batch}"
               f"&fields=System.Id,System.AssignedTo&api-version=7.1")
        ok, j, _h, d = P._ado_rest_get_h(url, timeout)
        if not ok:
            return (False, None, d)
        for w in (j or {}).get("value") or []:
            a = (w.get("fields") or {}).get("System.AssignedTo")
            out[str(w["fields"]["System.Id"])] = (a or {}).get("uniqueName") if isinstance(a, dict) else None
    return (True, out, "")


def broker_manual_cases(plan_id, root_suite, timeout=90):
    """(ok, [{id, assignee}], detail) — the Broker "Manual Tests (Android Broker)" subtree
    cases with their default AssignedTo. `plan_id` is the release's cloned Broker plan
    (or the master); the cases are the same work items either way."""
    ok, subtree, d = _suite_subtree(plan_id, root_suite, timeout)
    if not ok:
        return (False, None, d)
    case_ids = set()
    for sid in subtree:
        okc, cases, dc = P._ado_rest_get_all(
            f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/Suites/{sid}/TestCase?api-version=7.1", timeout)
        if not okc:
            return (False, None, dc)
        for c in cases:
            wid = ((c.get("workItem") or {}).get("id")
                   or (c.get("testCase") or {}).get("id"))
            if wid:
                case_ids.add(str(wid))
    okf, amap, df = _cases_assignedto(case_ids, timeout)
    if not okf:
        return (False, None, df)
    return (True, [{"id": cid, "assignee": amap.get(cid)} for cid in sorted(case_ids)], "")


def find_suite_id_by_name(plan_id, name, timeout=90):
    """The id of the suite named `name` in `plan_id` (case-insensitive), or None."""
    ok, suites, d = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/suites?api-version=7.1", timeout)
    if not ok:
        return (False, None, d)
    want = (name or "").strip().lower()
    for s in suites:
        if (s.get("name") or "").strip().lower() == want:
            return (True, s.get("id"), "")
    return (True, None, "")


def _wiql_ids(query, timeout=90):
    ok, j, _h, d = _ado_wiql(query, timeout)
    if not ok:
        return (False, None, d)
    return (True, [str(w["id"]) for w in (j or {}).get("workItems") or []], "")


def _ado_wiql(query, timeout=90):
    """POST a WIQL query via the ADO REST wiql endpoint. (ok, json, headers, detail)."""
    az = shutil.which("az")
    if az is None:
        return (False, None, {}, "az CLI not found")
    tok = subprocess.run(
        [az, "account", "get-access-token", "--resource", P._ADO_RESOURCE,
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    if tok.returncode != 0 or not (tok.stdout or "").strip():
        return (False, None, {}, "AUTH: could not get an ADO token (run `az login`)")
    data = _json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        f"{ORG}/{PROJECT}/_apis/wit/wiql?api-version=7.1", data=data, method="POST",
        headers={"Authorization": f"Bearer {tok.stdout.strip()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (True, _json.loads(resp.read().decode("utf-8")), {}, "")
    except urllib.error.HTTPError as e:
        return (False, None, {}, f"HTTP {e.code}")
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, None, {}, f"WIQL failed: {e}")


def _tags_of(case_ids, timeout=90):
    """{case_id: [tag,...]} for the given cases."""
    out = {}
    ids = [str(i) for i in case_ids if i]
    for i in range(0, len(ids), 190):
        batch = ",".join(ids[i:i + 190])
        url = (f"{ORG}/{PROJECT}/_apis/wit/workitems?ids={batch}"
               f"&fields=System.Id,System.Tags,System.AssignedTo&api-version=7.1")
        ok, j, _h, d = P._ado_rest_get_h(url, timeout)
        if not ok:
            return (False, None, d)
        for w in (j or {}).get("value") or []:
            f = w.get("fields") or {}
            tags = [x.strip() for x in (f.get("System.Tags") or "").split(";") if x.strip()]
            a = f.get("System.AssignedTo")
            out[str(f["System.Id"])] = {"tags": tags,
                                        "assignee": (a or {}).get("uniqueName") if isinstance(a, dict) else None}
    return (True, out, "")


def auth_bugbash_cases(exclude_tags, timeout=90):
    """(ok, [{id, assignee}], detail) — the Authenticator ReleaseBugBash cases minus any
    carrying an excluded tag (case-insensitive), with their default AssignedTo."""
    ok, ids, d = _wiql_ids(T.auth_bugbash_query(), timeout)
    if not ok:
        return (False, None, d)
    okt, meta, dt = _tags_of(ids, timeout)
    if not okt:
        return (False, None, dt)
    drop = {t.strip().lower() for t in (exclude_tags or [])}
    out = []
    for cid in ids:
        m = meta.get(cid) or {}
        if any(t.lower() in drop for t in m.get("tags", [])):
            continue
        out.append({"id": cid, "assignee": m.get("assignee")})
    return (True, out, "")


def set_assigned_to(case_id, upn, timeout=60):
    """WRITE: set System.AssignedTo on a test-case work item. (ok, detail). This mutates
    the shared work item (visible in the master + every plan referencing it)."""
    url = f"{ORG}/{PROJECT}/_apis/wit/workitems/{case_id}?api-version=7.1"
    body = [{"op": "add", "path": "/fields/System.AssignedTo", "value": upn}]
    az = shutil.which("az")
    if az is None:
        return (False, "az CLI not found")
    tok = subprocess.run(
        [az, "account", "get-access-token", "--resource", P._ADO_RESOURCE,
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    if tok.returncode != 0 or not (tok.stdout or "").strip():
        return (False, "AUTH: could not get an ADO token (run `az login`)")
    req = urllib.request.Request(
        url, data=_json.dumps(body).encode("utf-8"), method="PATCH",
        headers={"Authorization": f"Bearer {tok.stdout.strip()}",
                 "Content-Type": "application/json-patch+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return (True, "")
    except urllib.error.HTTPError as e:
        return (False, f"HTTP {e.code}")
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        return (False, f"PATCH failed: {e}")
