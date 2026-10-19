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

Assignment authority is the release plan/suite test-point Tester. Shared test-case
System.AssignedTo is never read or changed by distribution or UI-failure triage.

The `distribute()` algorithm is a pure function (no I/O) so it is fully unit-testable;
the ADO/connector gatherers and the write are separate.
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
from uuid import UUID

import yaml

from orchestrator import m365
from tools import pipelines as P
from tools import testplans as T

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
ORG = T.ORG
PROJECT = T.PROJECT


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


def confirm_oof(selection, roster, owner, release_id, *, now=None, ccd=None):
    """Record the release owner's explicit answer, not inferred availability."""
    if not owner:
        raise ValueError("A release owner is required to confirm OOF availability.")
    return {"upns": resolve_oof(selection, roster), "confirmed_by": _identity(owner),
            "source": "release-owner", "confirmed_at": (now or datetime.now(timezone.utc)).isoformat(),
            "release_id": release_id, "ccd": ccd}


def validate_oof(confirmation, roster, owner, release_id, *, ccd=None):
    if not isinstance(confirmation, dict):
        raise ValueError("Owner input needed: Is anyone OOF for this Bug Bash? "
                         "Ask the release owner; record --no-oof or --oof <verified-upn> "
                         "with distribute-tests before computing a preview.")
    if (not owner or confirmation.get("confirmed_by") != _identity(owner)
            or confirmation.get("source") != "release-owner"
            or confirmation.get("release_id") != release_id
            or confirmation.get("ccd") != ccd):
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


# ----------------------------------------------------------------- connector identity observations / ADO I/O

class ConnectorObservationRequired(ValueError):
    """A read-only connector handoff, never permission to write or complete a step."""

    def __init__(self, message, *, tool, arguments, owner, additional_requests=()):
        super().__init__(message)
        self.tool, self.arguments = tool, arguments
        self.requests = [m365.observation_request(tool, arguments, owner), *additional_requests]


def roster_lookup_path(group_mail):
    if not isinstance(group_mail, str) or not _mail_identity(group_mail):
        raise ValueError("Roster group must be an exact mail address")
    literal = group_mail.replace("'", "''")
    return ("/groups?$filter=" + urllib.parse.quote(f"mail eq '{literal}'", safe="")
            + "&$select=id,displayName,mail&$top=100")


def roster_read_request(group_mail, owner):
    """Initial exact-DL connector request, shared by readiness and distribution."""
    return m365.observation_request(
        "workiq.fetch", {"entityUrls": [roster_lookup_path(group_mail)]}, owner)


def _mail_identity(value):
    return (isinstance(value, str) and value == value.strip() and value.count("@") == 1
            and all(value.split("@")) and not any(c.isspace() for c in value))


def _connector_page_path(value):
    if not isinstance(value, str):
        raise ValueError("Invalid connector pagination URL")
    if value.startswith("/"):
        path = value
    else:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
                or not parsed.path.startswith("/v1.0/") or parsed.fragment):
            raise ValueError("Invalid connector pagination URL")
        path = parsed.path[len("/v1.0"):] + ("?" + parsed.query if parsed.query else "")
    if not path.startswith("/groups") or path.startswith("//") or "#" in path:
        raise ValueError("Invalid roster pagination URL")
    return path


def roster_observed(group_mail, observation, owner, *, now=None):
    """Consume exact lookup/member pages, retaining their source and typed identities."""
    lookup = roster_lookup_path(group_mail)
    if observation is None:
        envelopes = []
    elif (isinstance(observation, dict) and set(observation) == {"observations"}
          and isinstance(observation["observations"], list)):
        envelopes = observation["observations"]
    else:
        raise ValueError("Roster file must contain {observations: [owner-bound M365 envelopes]}")
    used, seen = [], set()

    def read(path):
        if path in seen or len(seen) >= 1000:
            raise ValueError("Cyclic/excessive roster pagination")
        seen.add(path)
        arguments = {"entityUrls": [path]}
        if len(used) == len(envelopes):
            raise ConnectorObservationRequired(
                "Roster requires an exact structured WorkIQ entity read. If the required "
                "workiq.fetch entity tool is not loaded, configure the installed M365 connector. Collect "
                "the requested raw structured response (not people search or another group), "
                "and append its fresh owner-bound envelope to --roster-file.",
                tool="workiq.fetch", arguments=arguments, owner=owner)
        envelope = envelopes[len(used)]
        result = m365.validate_observation(
            envelope, tool="workiq.fetch", arguments=arguments, owner=owner, now=now)
        if (not isinstance(result, dict) or not isinstance(result.get("value"), list)
                or result.get("hasMoreResults") or result.get("nextLink")
                or result.get("nextSkipToken") or result.get("isPartial")
                or result.get("truncated") or result.get("complete") is False):
            raise ValueError("Roster requires raw structured entity collection pages, not summaries/partial results")
        used.append(envelope)
        return result

    def count(page, previous):
        if "@odata.count" not in page:
            return previous
        value = page["@odata.count"]
        if type(value) is not int or value < 0 or previous is not None and value != previous:
            raise ValueError("Invalid/changing roster collection count")
        return value

    groups, path, group_count = [], lookup, None
    while path:
        page = read(path)
        group_count = count(page, group_count)
        groups.extend(page["value"])
        next_link = page.get("@odata.nextLink")
        path = _connector_page_path(next_link) if next_link else None
        if path and path.split("?", 1)[0] != "/groups":
            raise ValueError("Roster lookup pagination changed entity")
    if group_count is not None and group_count != len(groups):
        raise ValueError("Incomplete roster group lookup")
    if len(groups) != 1:
        raise ValueError("Exact roster mail lookup must return exactly one group")
    group = groups[0]
    if (not isinstance(group, dict) or not isinstance(group.get("displayName"), str)
            or not group["displayName"].strip()
            or _identity(group.get("mail")) != _identity(group_mail)):
        raise ValueError("Roster lookup returned the wrong group/mail identity")
    try:
        gid = str(UUID(group["id"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("Roster group requires a real directory ID") from None
    root = f"/groups/{gid}/members"
    path = root + "?$select=id,displayName,userPrincipalName,mail&$top=100"
    members, ids, upns, member_count = [], set(), set(), None
    while path:
        page = read(path)
        member_count = count(page, member_count)
        for member in page["value"]:
            if not isinstance(member, dict) or not member.get("@odata.type"):
                raise ValueError("Roster members require typed directory identities")
            try:
                identity = str(UUID(member["id"]))
            except (KeyError, ValueError, TypeError, AttributeError):
                raise ValueError("Roster member requires a real directory ID") from None
            if identity in ids:
                raise ValueError("Duplicate roster directory identity")
            ids.add(identity)
            if member["@odata.type"] != "#microsoft.graph.user":
                if member["@odata.type"] not in {
                        "#microsoft.graph.group", "#microsoft.graph.device",
                        "#microsoft.graph.servicePrincipal", "#microsoft.graph.orgContact"}:
                    raise ValueError("Unknown roster member directory type")
                continue
            upn = member.get("userPrincipalName")
            if (not _mail_identity(upn) or not isinstance(member.get("displayName"), str)
                    or not member["displayName"].strip()
                    or member.get("mail") is not None and not _mail_identity(member["mail"])):
                raise ValueError("Incomplete roster user identity; obtain directory evidence")
            if _identity(upn) in upns:
                raise ValueError("Duplicate roster user principal name")
            upns.add(_identity(upn))
            members.append({"id": identity, "type": member["@odata.type"],
                            "name": member["displayName"], "upn": _identity(upn),
                            "mail": member.get("mail")})
        next_link = page.get("@odata.nextLink")
        path = _connector_page_path(next_link) if next_link else None
        if path and path.split("?", 1)[0] != root:
            raise ValueError("Roster member pagination changed group")
    if member_count is not None and member_count != len(ids):
        raise ValueError("Incomplete roster group membership")
    if len(used) != len(envelopes):
        raise ValueError("Roster file contains unexpected/unconsumed observations")
    return members, {"group_mail": group_mail, "group": group, "observations": used}


def resolve_roster(group_mail, timeout=60, *, owner=None, roster_observation=None):
    """Resolve a complete owner-bound connector roster; never acquire Graph tokens."""
    try:
        members, _ = roster_observed(group_mail, roster_observation, owner)
        return True, members, ""
    except ConnectorObservationRequired:
        raise
    except ValueError as exc:
        return False, None, str(exc)


def _suite_subtree(plan_id, root_suite, timeout=90):
    """All suite ids under root_suite (inclusive)."""
    ok, suites, d = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/suites?api-version=7.1", timeout)
    if not ok:
        return (False, None, d)
    if not isinstance(suites, list):
        return False, None, "Malformed release suite collection"
    children, known = {}, set()
    for s in suites:
        if (not isinstance(s, dict) or not str(s.get("id")).isascii()
                or not str(s.get("id")).isdigit() or int(s["id"]) <= 0
                or str(s["id"]) in known):
            return False, None, "Invalid/duplicate release suite identity"
        sid = str(s["id"])
        known.add(sid)
        parent = s.get("parentSuite") or {}
        if not isinstance(parent, dict):
            return False, None, "Malformed release suite parent"
        children.setdefault(str(parent.get("id")), []).append(sid)
    if str(root_suite) not in known:
        return False, None, "Release suite no longer exists in this plan"
    out, stack, visited = [], [str(root_suite)], set()
    while stack:
        sid = stack.pop()
        if sid in visited:
            return False, None, "Cyclic release suite hierarchy"
        visited.add(sid)
        out.append(int(sid))
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
    """(ok, [{id}], detail) - the Broker "Manual Tests (Android Broker)" subtree
    cases. Ownership is read separately from this release's test points."""
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
    return (True, [{"id": cid} for cid in sorted(case_ids)], "")


def find_suite_id_by_name(plan_id, name, timeout=90):
    """The id of the suite named `name` in `plan_id` (case-insensitive), or None."""
    ok, suites, d = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/suites?api-version=7.1", timeout)
    if not ok:
        return (False, None, d)
    want = (name or "").strip().lower()
    matches = [s for s in suites if (s.get("name") or "").strip().lower() == want]
    if len(matches) > 1:
        return False, None, f"Ambiguous suite {name!r} in plan {plan_id}"
    return True, matches[0].get("id") if matches else None, ""


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
               f"&fields=System.Id,System.Tags&api-version=7.1")
        ok, j, _h, d = P._ado_rest_get_h(url, timeout)
        if not ok:
            return (False, None, d)
        if not isinstance(j, dict) or not isinstance(j.get("value"), list):
            return False, None, "Malformed test-case tag metadata"
        for w in j["value"]:
            if not isinstance(w, dict) or not isinstance(w.get("fields"), dict):
                return False, None, "Malformed test-case tag entry"
            f = w.get("fields") or {}
            cid = str(f.get("System.Id"))
            if cid not in ids[i:i + 190] or cid in out or not isinstance(f.get("System.Tags", ""), str):
                return False, None, "Unexpected/duplicate test-case tag metadata"
            tags = [x.strip() for x in (f.get("System.Tags") or "").split(";") if x.strip()]
            out[cid] = {"tags": tags}
    return (True, out, "")


def auth_bugbash_cases(timeout=90):
    """Return every query case with tags; ownership belongs to release points."""
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
        out.append({"id": cid, "tags": m["tags"]})
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
                try:
                    tester = point_tester(p.get("assignedTo"), timeout=timeout)
                except ValueError as exc:
                    return False, None, str(exc)
                rows.append({"id": p["id"], "case_id": cid,
                             "config_id": str(p["configuration"]["id"]),
                             "tester_id": tester["id"], "tester_upn": tester["upn"]})
        if rows:
            groups.append({"plan_id": plan_id, "suite_id": sid,
                           "points": sorted(rows, key=lambda p: int(p["id"]))})
    if covered != selected:
        return False, None, "Selected cases missing from release suite: " + ", ".join(sorted(selected - covered))
    return True, groups, ""


def _identity_url(org):
    parsed = urllib.parse.urlparse(org)
    if parsed.hostname == "dev.azure.com":
        organization = parsed.path.strip("/").split("/")[0]
    elif (parsed.hostname or "").endswith(".visualstudio.com"):
        organization = parsed.hostname[:-len(".visualstudio.com")]
    else:
        raise ValueError("Unsupported distribution ADO organization")
    return f"https://vssps.dev.azure.com/{urllib.parse.quote(organization, safe='')}/_apis/identities"


def _point_tester_id(point):
    identity = (point.get("assignedTo") or {}).get("id")
    return None if identity == "00000000-0000-0000-0000-000000000000" else identity


def point_tester(value, *, org=ORG, timeout=60):
    """Resolve the point's own Tester identity, never the shared test-case owner."""
    if value is None or value == {}:
        return {"id": None, "upn": None}
    if (isinstance(value, dict) and value.get("id") == "00000000-0000-0000-0000-000000000000"
            and not value.get("uniqueName")):
        return {"id": None, "upn": None}
    if not isinstance(value, dict) or not value.get("id"):
        raise ValueError("Malformed release-point Tester identity")
    upn = _identity(value.get("uniqueName"))
    if not upn:
        ok, page, detail = P._ado_rest_get(
            f"{_identity_url(org)}?identityIds={urllib.parse.quote(str(value['id']), safe='')}"
            "&queryMembership=None&api-version=7.1", timeout)
        rows = page.get("value") if ok and isinstance(page, dict) else None
        if (not isinstance(rows, list) or len(rows) != 1
                or not isinstance(rows[0], dict)
                or str(rows[0].get("id")) != str(value["id"])):
            raise ValueError(f"Cannot resolve release-point Tester {value['id']}: {detail}")
        properties = rows[0].get("properties") or {}
        if (not isinstance(properties, dict)
                or not isinstance(properties.get("Account") or {}, dict)):
            raise ValueError(f"Malformed release-point Tester account {value['id']}")
        upn = _identity((properties.get("Account") or {}).get("$value"))
    if upn.count("@") != 1 or any(c.isspace() for c in upn) or not all(upn.split("@")):
        raise ValueError(f"Unresolved release-point Tester {value['id']}")
    return {"id": value["id"], "upn": upn}


def release_target_names(broker, auth):
    """Names captured by the owning clone steps, including explicitly reviewed TEST names."""
    return {"broker": broker.get("plan_name"), "auth": auth.get("suite_name")}


def validate_release_targets(release_id, ccd, binding, *, org=ORG, project=PROJECT, timeout=90,
                             expected_names=None):
    """Recheck only destination identity, never refetch frozen RC/source evidence."""
    if org != T.ORG or project != T.PROJECT:
        raise ValueError("Test-plan provider coordinates changed; refresh the owning steps")
    broker, auth = binding["broker"], binding["auth"]
    if expected_names is not None and not isinstance(expected_names, dict):
        raise ValueError("Malformed release destination names")
    names = expected_names or {}
    broker_name = names.get("broker")
    auth_name = names.get("auth")
    for name in (broker_name, auth_name):
        if name is not None and (not isinstance(name, str) or not name.strip() or name != name.strip()):
            raise ValueError("Malformed release destination name")
    broker_name = broker_name if broker_name is not None else T.broker_plan_name(release_id)
    auth_name = auth_name if auth_name is not None else T.auth_suite_name(ccd)
    if str(broker["plan_id"]) == str(T.BROKER_MASTER_PLAN):
        raise ValueError("The Broker template is not a release destination")
    ok, plan, detail = T.get_plan(broker["plan_id"], timeout)
    if not ok or not isinstance(plan, dict):
        raise ValueError(f"Cannot validate Broker release plan: {detail}")
    if (str(plan.get("id")) != str(broker["plan_id"])
            or plan.get("name") != broker_name
            or plan.get("areaPath") != T.BROKER_AREA_PATH
            or plan.get("iteration") != T.BROKER_ITERATION):
        raise ValueError("Broker destination no longer matches this release")
    description = plan.get("description") or ""
    if not isinstance(description, str) or not str(plan.get("rootSuiteId", "")).isdigit():
        raise ValueError("Malformed Broker destination ownership/root-suite identity")
    markers = [line for line in description.splitlines()
               if line.startswith("release-agent:broker-plan:")]
    if markers and markers != ["release-agent:broker-plan:" + release_id]:
        raise ValueError("Broker destination belongs to another release")
    from tools import broker_failure_triage as F
    failure_only = broker.get("layout") == F.LAYOUT
    if broker.get("layout") is not None and not failure_only:
        raise ValueError("Unknown Broker automation target layout")
    if failure_only and (set(broker) != {"plan_id", "layout", "root_suite_id"}
                         or str(broker["root_suite_id"]) != str(plan["rootSuiteId"])
                         or F.MARKER not in description.splitlines()
                         or not isinstance(binding.get("broker_scenarios"), dict)):
        raise ValueError("Failure-only plan-root ownership changed")
    sid = broker["root_suite_id"] if failure_only else broker["suite_id"]
    ok, suite, detail = T.get_suite(broker["plan_id"], sid, timeout, org=org, project=project)
    if (not ok or not isinstance(suite, dict)
            or str(suite.get("id")) != str(sid)
            or suite.get("name") != (broker_name if failure_only else T.BROKER_UI_SUITE_NAME)
            or suite.get("suiteType") != "staticTestSuite"
            or (not failure_only and (not isinstance(suite.get("parentSuite"), dict)
                or str(suite["parentSuite"].get("id")) != str(plan["rootSuiteId"])))):
        raise ValueError(f"Broker UI destination identity changed: {detail}")
    if str(auth["plan_id"]) != str(T.AUTH_PLAN):
        raise ValueError("Authenticator destination plan changed")
    ok, _, detail = T.validate_auth_query_suite(
        auth["suite_id"], auth_name, T.auth_bugbash_query(), timeout,
        org=org, project=project, plan_id=auth["plan_id"], parent_suite_id=T.AUTH_ROOT_SUITE)
    if not ok:
        raise ValueError(f"Authenticator release-suite identity changed: {detail}")


def resolve_tester_identity(upn, *, org=ORG, timeout=60):
    """Resolve one exact ADO identity before review, never after write authorization."""
    upn = _identity(upn)
    url = (f"{_identity_url(org)}"
           f"?searchFilter=General&filterValue={urllib.parse.quote(upn, safe='')}"
           "&queryMembership=None&api-version=7.1")
    ok, rows, detail = P._ado_rest_get(url, timeout)
    if not ok or not isinstance(rows, dict):
        raise ValueError(f"Cannot resolve ADO tester identity for {upn}: {detail}")
    ids = set()
    entries = rows.get("value")
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        raise ValueError(f"Malformed ADO tester lookup for {upn}")
    for row in entries:
        properties = row.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValueError(f"Malformed ADO tester properties for {upn}")
        names = [row.get("providerDisplayName")]
        for key in ("Account", "Mail"):
            value = properties.get(key) or {}
            names.append(value.get("$value") if isinstance(value, dict) else value)
        if row.get("id") and row.get("isActive") is not False and upn in {_identity(v) for v in names}:
            ids.add(row["id"])
    if len(ids) != 1:
        raise ValueError(f"ADO tester {upn} did not resolve to one exact active identity")
    return ids.pop()


def sync_point_testers(plan_id, suite_id, assignments, timeout=90, *, expected_testers=None,
                       org=ORG, project=PROJECT, validate=None, expected_identities=None,
                       reviewed_updates=None, expected_points=None):
    """Write reviewed release-point Testers only; never read/write case AssignedTo."""
    if not assignments:
        return True, ""
    url = f"{org}/{project}/_apis/test/Plans/{plan_id}/Suites/{suite_id}/points"
    ok, points, detail = P._ado_rest_get_all(url + "?api-version=5.0", timeout)
    if not ok:
        return False, detail
    error = T._point_validation_error(points, require_config=True)
    if error:
        return False, error
    scope = set(expected_points) if expected_points is not None else None
    def included(point):
        return (str(point["testCase"]["id"]) in {str(cid) for cid in assignments}
                and (scope is None or str(point["id"]) in scope))
    if expected_testers is not None:
        current = {p["id"]: _point_tester_id(p)
                   for p in points if included(p)}
        if current != expected_testers:
            return False, "Plan testers changed after review; inspect the fresh ADO corrections"
    if expected_points is not None:
        current_points = {str(p["id"]): {"case_id": str(p["testCase"]["id"]),
                                       "config_id": str(p["configuration"]["id"])}
                          for p in points if included(p)}
        if current_points != expected_points:
            return False, "Release point case/configuration identities changed after review"
    selected = {int(cid): upn.casefold() for cid, upn in assignments.items()}
    if not set(selected) <= {int(p["testCase"]["id"]) for p in points if included(p)}:
        return False, "Assigned cases are missing from the target suite; refresh distribution"
    identities = ({int(k): v for k, v in expected_identities.items()}
                  if expected_identities is not None else
                  {cid: resolve_tester_identity(upn, org=org, timeout=timeout)
                   for cid, upn in selected.items()})
    if set(identities) != set(selected) or any(not v for v in identities.values()):
        return False, "Incomplete reviewed point-Tester identities"
    groups = {}
    for point in points:
        cid = int(point["testCase"]["id"])
        if included(point) and _point_tester_id(point) != identities[cid]:
            groups.setdefault(identities[cid], []).append(point["id"])
    if reviewed_updates is not None:
        reviewed = {row["tester_id"]: list(row["point_ids"]) for row in reviewed_updates}
        if {key: sorted(value) for key, value in groups.items()} != reviewed:
            return False, "Point tester updates differ from the reviewed operations"
        groups = reviewed
    for identity, point_ids in groups.items():
        for start in range(0, len(point_ids), 40):
            if validate is not None:
                validate()
            ok, _, detail = P._ado_rest_send(
                f"{url}/{','.join(map(str, point_ids[start:start+40]))}?api-version=5.0",
                "PATCH", {"tester": {"id": identity}}, timeout)
            if not ok:
                return False, f"Tester alignment incomplete: {detail}; read back before retry"
    ok, after, detail = P._ado_rest_get_all(url + "?api-version=5.0", timeout)
    if not ok:
        return False, detail
    error = T._point_validation_error(after, require_config=True)
    if error:
        return False, f"Tester alignment read-back invalid: {error}"
    def signature(point, expected=False):
        cid = int(point["testCase"]["id"])
        return (cid, point["configuration"], point.get("outcome"), point.get("state"),
                point.get("lastTestRun"), point.get("lastResult"),
                identities[cid] if expected and included(point) else _point_tester_id(point))
    if {p["id"]: signature(p, True) for p in points} != {p["id"]: signature(p) for p in after}:
        return False, "Tester alignment read-back differs, or outcomes/membership changed"
    return True, ""


def set_point_testers(plan_id, suite_id, point_ids, upn, *, org=ORG, project=PROJECT, timeout=90,
                      expected_points=None):
    """Assign only acknowledged failed points to the owner, preserving every outcome."""
    selected = {int(pid) for pid in point_ids}
    if not selected:
        return True, ""
    url = f"{org}/{project}/_apis/test/Plans/{plan_id}/Suites/{suite_id}/points"
    ok, points, detail = P._ado_rest_get_all(url + "?api-version=5.0", timeout)
    if not ok:
        return False, detail
    error = T._point_validation_error(points, require_config=True)
    if error:
        return False, error
    failed = {int(p["id"]) for p in points if str(p.get("outcome")).casefold() == "failed"}
    if not selected <= failed:
        return False, "Acknowledged failure points disappeared or changed; no triage Tester written"
    if expected_points is not None:
        observed = {str(p["id"]): p for p in points}
        if set(expected_points) != {str(pid) for pid in selected}:
            return False, "Incomplete acknowledged failure-point identities"
        for pid, expected in expected_points.items():
            actual = observed[pid]
            if (str(actual["testCase"]["id"]) != str(expected["case_id"])
                    or ("config_id" in expected and
                        str(actual["configuration"]["id"]) != str(expected["config_id"]))):
                return False, "Acknowledged failure point case/configuration changed; no triage Tester written"
    identity = resolve_tester_identity(upn, org=org, timeout=timeout)
    def signature(point):
        return {key: point.get(key) for key in (
            "testCase", "configuration", "outcome", "state", "lastTestRun", "lastResult")}
    before = {int(p["id"]): {**signature(p), "tester_id": (
        identity if int(p["id"]) in selected else _point_tester_id(p))} for p in points}
    pending = sorted(int(p["id"]) for p in points if int(p["id"]) in selected
                     and (p.get("assignedTo") or {}).get("id") != identity)
    for start in range(0, len(pending), 40):
        ok, _, detail = P._ado_rest_send(
            f"{url}/{','.join(map(str, pending[start:start + 40]))}?api-version=5.0",
            "PATCH", {"tester": {"id": identity}}, timeout)
        if not ok:
            return False, f"Triage Tester write incomplete: {detail}; read back before retry"
    ok, after, detail = P._ado_rest_get_all(url + "?api-version=5.0", timeout)
    if not ok:
        return False, detail
    error = T._point_validation_error(after, require_config=True)
    if error:
        return False, f"Triage Tester read-back invalid: {error}"
    if before != {int(p["id"]): {**signature(p), "tester_id": _point_tester_id(p)} for p in after}:
        return False, "Triage point identity/outcome changed during Tester assignment"
    if any((p.get("assignedTo") or {}).get("id") != identity for p in after if int(p["id"]) in selected):
        return False, "Triage point Tester read-back differs"
    return True, ""


def set_assigned_to(
    case_id,
    upn,
    timeout=60,
    *,
    expected_revision=None,
    org=ORG,
    project=PROJECT,
):
    """WRITE: set System.AssignedTo on a test-case work item. (ok, detail). This mutates
    the shared work item (visible in the master + every plan referencing it)."""
    url = f"{org}/{project}/_apis/wit/workitems/{case_id}?api-version=7.1"
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
