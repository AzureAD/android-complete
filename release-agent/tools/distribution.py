"""Test distribution for Phase 3 (bug_bash) — the `distribute_tests` step.

Distributes the release's manual bug-bash tests EVENLY across the eligible team while
preserving existing assignments as much as possible ("keep your preference"). Two test
sets are combined into one fair split:

  * BROKER — the test cases in the "Manual Tests (Android Broker)" subtree of the Broker
    plan (the release's cloned plan references the master's cases).
  * AUTHENTICATOR — the ReleaseBugBash query set with tags and assignments; the owning
    step separates actual Android automation and owner triage from runnable manual work.

Eligible testers = members of the roster DL (config/distribution.yaml `roster_group`)
MINUS: the always-excluded people, owner-confirmed OOF people, the release owner, and
the current on-call engineer (OCE). The OCE's team id is read from readiness.yaml (oncall_now.team_id) so the entry
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
from datetime import datetime, timezone

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

def _identity(value):
    return str(value or "").strip().casefold()


def canonical_roster(roster):
    """Stable, unique verified identities; display names are for owner selection only."""
    members = {}
    for member in sorted(roster, key=lambda m: (_identity(m.get("upn")),
                                               str(m.get("name") or ""))):
        upn = _identity(member.get("upn"))
        if upn:
            members.setdefault(upn, {"name": str(member.get("name") or upn).strip(), "upn": upn})
    return [members[u] for u in sorted(members)]


def resolve_oof(selection, roster):
    """Resolve exact names/UPNs against the roster, never guess aliases or partial names."""
    if not isinstance(selection, list):
        raise ValueError("OOF selection must be an explicit list (empty means nobody is OOF).")
    members = canonical_roster(roster)
    selected = set()
    for value in selection:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("OOF names/UPNs must not be blank.")
        matches = {m["upn"] for m in members
                   if _identity(value) in (m["upn"], _identity(m["name"]))}
        if len(matches) != 1:
            why = "Ambiguous" if matches else "Unknown"
            raise ValueError(f"{why} OOF person {value!r}; use a verified roster UPN.")
        selected.update(matches)
    return sorted(selected)


def confirm_oof(selection, roster, owner, release_id):
    """Record the release owner's explicit answer, not inferred availability."""
    if not owner:
        raise ValueError("A release owner is required to confirm OOF availability.")
    return {"upns": resolve_oof(selection, roster), "confirmed_by": _identity(owner),
            "source": "release-owner", "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "release_id": release_id}


def validate_oof(confirmation, roster, owner, release_id):
    if not isinstance(confirmation, dict):
        raise ValueError("Owner input needed: Is anyone OOF for this Bug Bash? "
                         "Ask the release owner; record --no-oof or --oof <verified-upn> "
                         "with distribute-tests before computing a preview.")
    if (not owner or confirmation.get("confirmed_by") != _identity(owner)
            or confirmation.get("source") != "release-owner"
            or confirmation.get("release_id") != release_id):
        raise ValueError("OOF confirmation must come from this release's owner; ask again.")
    try:
        confirmed_at = datetime.fromisoformat(confirmation["confirmed_at"])
        if confirmed_at.tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("OOF confirmation is missing a valid confirmation time.") from None
    upns = resolve_oof(confirmation.get("upns"), roster)
    if upns != confirmation["upns"]:
        raise ValueError("OOF confirmation must contain canonical roster UPNs; ask again.")
    return upns


def review_inputs(roster, always_excluded, owner, oce, confirmation):
    """Availability inputs included in the transient approval digest."""
    return {"roster": [m["upn"] for m in canonical_roster(roster)],
            "always_excluded": sorted({_identity(u) for u in always_excluded or []}),
            "owner": _identity(owner), "oce": _identity(oce),
            "oof": {**confirmation, "upns": list(confirmation["upns"])}}


def eligible_testers(roster, always_excluded, owner=None, oce=None, oof=None):
    """The people tests are distributed to: roster MINUS always_excluded, the owner, and
    the OCE and owner-confirmed OOF people. Comparison is case-insensitive. Order preserved.
    `roster` is a list of identifiers; the excludes are identifiers too."""
    drop = {_identity(x) for x in [*(always_excluded or []), *(oof or [])]}
    for x in (owner, oce):
        if x:
            drop.add(_identity(x))
    return list(dict.fromkeys(_identity(p) for p in roster if _identity(p) not in drop))


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
    ok, snapshot, detail = case_assignment_snapshot(case_ids, timeout)
    return ok, {cid: row["assignee"] for cid, row in snapshot.items()} if ok else None, detail


def case_assignment_snapshot(case_ids, timeout=90):
    """Read assignees and revisions together, rejecting incomplete/ambiguous responses."""
    out = {}
    ids = sorted({str(i) for i in case_ids})
    for i in range(0, len(ids), 190):
        batch = ",".join(ids[i:i + 190])
        url = (f"{ORG}/{PROJECT}/_apis/wit/workitems?ids={batch}"
               f"&fields=System.Id,System.AssignedTo&api-version=7.1")
        ok, j, _h, d = P._ado_rest_get_h(url, timeout)
        if not ok:
            return (False, None, d)
        if not isinstance(j, dict) or not isinstance(j.get("value"), list):
            return False, None, "Malformed case-assignment response"
        for w in j["value"]:
            if not isinstance(w, dict) or not isinstance(w.get("fields"), dict):
                return False, None, "Malformed case-assignment entry"
            cid = str(w["fields"].get("System.Id"))
            a = w["fields"].get("System.AssignedTo")
            if cid in out or cid not in ids[i:i + 190]:
                return False, None, f"Unexpected/duplicate case-assignment entry {cid}"
            if a is not None and (not isinstance(a, dict) or not isinstance(a.get("uniqueName"), str)
                                  or not a["uniqueName"].strip()):
                return False, None, f"Unresolved assignee for case {cid}"
            out[cid] = {"assignee": a["uniqueName"] if a else None,
                        "identity_id": a.get("id") if a else None, "revision": w.get("rev")}
    if set(out) != set(ids):
        return False, None, "Missing case assignments: " + ", ".join(sorted(set(ids) - set(out)))
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


def auth_bugbash_cases(timeout=90):
    """Return every query case with tags/assignee; selection belongs to the step."""
    ok, ids, d = _wiql_ids(T.auth_bugbash_query(), timeout)
    if not ok:
        return (False, None, d)
    okt, meta, dt = _tags_of(ids, timeout)
    if not okt:
        return (False, None, dt)
    out = []
    for cid in ids:
        if cid not in meta:
            return False, None, f"Missing metadata for Authenticator case {cid}"
        m = meta[cid]
        out.append({"id": cid, "assignee": m.get("assignee"), "tags": m["tags"]})
    return (True, out, "")


def read_point_testers(plan_id, root_suite, case_ids, timeout=90):
    """Read selected test-point identities across a suite subtree without changing ADO."""
    selected = {str(cid) for cid in case_ids}
    if not selected:
        return True, [], ""
    ok, suites, detail = _suite_subtree(plan_id, root_suite, timeout)
    if not ok:
        return False, None, detail
    groups, covered = [], set()
    for sid in sorted(set(suites)):
        ok, points, detail = P._ado_rest_get_all(
            f"{ORG}/{PROJECT}/_apis/test/Plans/{plan_id}/Suites/{sid}/points?api-version=5.0", timeout)
        if not ok:
            return False, None, detail
        error = T._point_validation_error(points, require_config=True)
        if error:
            return False, None, error
        rows = []
        for p in points:
            cid = str(p["testCase"]["id"])
            if cid in selected:
                covered.add(cid)
                rows.append({"id": p["id"], "case_id": cid,
                             "tester_id": (p.get("assignedTo") or {}).get("id")})
        if rows:
            groups.append({"plan_id": plan_id, "suite_id": sid,
                           "points": sorted(rows, key=lambda p: int(p["id"]))})
    if covered != selected:
        return False, None, "Selected cases missing from release suite: " + ", ".join(sorted(selected - covered))
    return True, groups, ""


def sync_point_testers(plan_id, suite_id, assignments, timeout=90, *, expected_testers=None):
    """Align selected point testers to already-written case assignees; no outcome writes."""
    if not assignments:
        return True, ""
    url = f"{ORG}/{PROJECT}/_apis/test/Plans/{plan_id}/Suites/{suite_id}/points"
    ok, points, detail = P._ado_rest_get_all(url + "?api-version=5.0", timeout)
    if not ok:
        return False, detail
    error = T._point_validation_error(points, require_config=True)
    if error:
        return False, error
    if expected_testers is not None:
        current = {p["id"]: (p.get("assignedTo") or {}).get("id")
                   for p in points if str(p["testCase"]["id"]) in {str(cid) for cid in assignments}}
        if current != expected_testers:
            return False, "Plan testers changed after review; inspect the fresh ADO corrections"
    selected = {int(cid): upn.casefold() for cid, upn in assignments.items()}
    if not set(selected) <= {int(p["testCase"]["id"]) for p in points}:
        return False, "Assigned cases are missing from the target suite; refresh distribution"
    identities = {}
    ids = sorted(selected)
    for start in range(0, len(ids), 190):
        ok, data, detail = P._ado_rest_get(
            f"{ORG}/{PROJECT}/_apis/wit/workitems?ids={','.join(map(str, ids[start:start+190]))}"
            "&fields=System.Id,System.AssignedTo&api-version=7.1", timeout)
        if not ok:
            return False, detail
        for item in data.get("value", []):
            cid = int(item["id"])
            assignee = item["fields"].get("System.AssignedTo") or {}
            if not assignee.get("id") or assignee.get("uniqueName", "").casefold() != selected.get(cid):
                return False, f"Case {cid} assignee differs from the approved allocation"
            identities[cid] = assignee["id"]
    if set(identities) != set(selected):
        return False, "Incomplete case-assignee identities; no tester alignment attempted"
    groups = {}
    for point in points:
        cid = int(point["testCase"]["id"])
        if cid in selected and (point.get("assignedTo") or {}).get("id") != identities[cid]:
            groups.setdefault(identities[cid], []).append(point["id"])
    for identity, point_ids in groups.items():
        for start in range(0, len(point_ids), 40):
            ok, _, detail = P._ado_rest_send(
                f"{url}/{','.join(map(str, point_ids[start:start+40]))}?api-version=5.0",
                "PATCH", {"tester": {"id": identity}}, timeout)
            if not ok:
                return False, f"Tester alignment incomplete: {detail}; read back before retry"
    ok, after, detail = P._ado_rest_get_all(url + "?api-version=5.0", timeout)
    if not ok:
        return False, detail
    def signature(point, expected=False):
        cid = int(point["testCase"]["id"])
        return (cid, point["configuration"], point.get("outcome"), point.get("state"),
                point.get("lastTestRun"), point.get("lastResult"),
                identities[cid] if expected and cid in selected else (point.get("assignedTo") or {}).get("id"))
    if {p["id"]: signature(p, True) for p in points} != {p["id"]: signature(p) for p in after}:
        return False, "Tester alignment read-back differs, or outcomes/membership changed"
    return True, ""


def set_assigned_to(case_id, upn, timeout=60, *, expected_revision=None):
    """WRITE: set System.AssignedTo on a test-case work item. (ok, detail). This mutates
    the shared work item (visible in the master + every plan referencing it)."""
    url = f"{ORG}/{PROJECT}/_apis/wit/workitems/{case_id}?api-version=7.1"
    body = [{"op": "add", "path": "/fields/System.AssignedTo", "value": upn}]
    if expected_revision is not None:
        if type(expected_revision) is not int or expected_revision < 1:
            return False, "Invalid expected work-item revision; no assignment written"
        body.insert(0, {"op": "test", "path": "/rev", "value": expected_revision})
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
