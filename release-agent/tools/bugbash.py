"""Bug-bash progress + scheduling helpers for Phase 3 (bug_bash) — the periodic-update
poster (`bugbash_updates`).

Two concerns:
  * scheduling — is_working_time(now): weekday, not a US holiday, 09:00–18:00 local. The
    poster uses the interval in config/automations.yaml inside this window
    (America/Los_Angeles), skipping weekends and hardcoded US federal holidays.
  * progress — gather_progress(...): read the live test-point outcomes from BOTH the Broker
    manual subtree plus applied Broker UI failure points and the Authenticator bug-bash suite, excluding
    automation-only Auth cases using the completed fill's classification. Grouped
    by each release point's live Tester, never the shared case AssignedTo.
    render_update(...) turns that into the
    Teams message HTML + the @mention list (mention owners with remaining tests; name-only
    for owners who finished all).

All ADO reads go through tools.pipelines / tools.distribution helpers (bearer token).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import quote
from uuid import UUID

from orchestrator import m365
from tools import pipelines as P
from tools import testplans as T
from tools import distribution as D

ORG = T.ORG
PROJECT = T.PROJECT

WORK_START_HOUR = 9        # 09:00 local — first post of the day / window open
WORK_END_HOUR = 18         # 18:00 local — stop for the day


# ----------------------------------------------------------------- US holidays

def _nth_weekday(year, month, weekday, n):
    """The date of the n-th `weekday` (Mon=0..Sun=6) of `month` in `year` (n>=1)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    """The date of the LAST `weekday` of `month`."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    """Federal-holiday observation: Sat -> Fri, Sun -> Mon."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_holidays(year):
    """Set of observed US FEDERAL holiday dates for `year` (hardcoded rules)."""
    h = {
        _observed(date(year, 1, 1)),                 # New Year's Day
        _nth_weekday(year, 1, 0, 3),                 # MLK Jr. — 3rd Mon Jan
        _nth_weekday(year, 2, 0, 3),                 # Presidents' Day — 3rd Mon Feb
        _last_weekday(year, 5, 0),                   # Memorial Day — last Mon May
        _observed(date(year, 6, 19)),                # Juneteenth
        _observed(date(year, 7, 4)),                 # Independence Day
        _nth_weekday(year, 9, 0, 1),                 # Labor Day — 1st Mon Sep
        _nth_weekday(year, 10, 0, 2),                # Columbus/Indigenous — 2nd Mon Oct
        _observed(date(year, 11, 11)),               # Veterans Day
        _nth_weekday(year, 11, 3, 4),                # Thanksgiving — 4th Thu Nov
        _observed(date(year, 12, 25)),               # Christmas
    }
    return h


def is_holiday(d):
    return d in us_holidays(d.year)


def is_business_day(d) -> bool:
    """True if `d` is a weekday (Mon-Fri) and not an observed US federal holiday."""
    return d.weekday() < 5 and not is_holiday(d)


def is_working_time(now: datetime) -> bool:
    """True if `now` (local) is a weekday, not a US holiday, and within 09:00–18:00."""
    d = now.date()
    if now.weekday() >= 5 or is_holiday(d):
        return False
    return WORK_START_HOUR <= now.hour < WORK_END_HOUR


# ----------------------------------------------------------------- progress

# Per-CASE verdict from its point outcomes (a case may have several points/configs).
_DONE_OUTCOMES = {"passed", "failed", "blocked", "notapplicable"}


def _case_state(outcomes):
    """Aggregate a case's point outcomes (lowercased) into one display state key:
    failed > blocked > passed > na > notrun (any not-yet-run point => notrun)."""
    o = {str(x).lower() for x in outcomes}
    if not o or (o - _DONE_OUTCOMES):          # any unspecified/ready/none -> not run yet
        if "failed" in o:
            return "failed"                     # a fail already recorded still shows red
        return "notrun"
    if "failed" in o:
        return "failed"
    if "blocked" in o:
        return "blocked"
    if o <= {"notapplicable"}:
        return "na"
    return "passed"


_STATE_ICON = {"passed": "✅", "failed": "❌", "blocked": "⛔", "na": "➖", "notrun": "⬜"}
_STATE_WORD = {"passed": "Passed", "failed": "Failed", "blocked": "Blocked",
               "na": "N/A", "notrun": "Not run"}


def _points_of_suite(plan_id, suite_id, timeout=90):
    """(ok, [{point_id, case_id, config_id, name, outcome}], detail) for ONE suite."""
    url = (f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}"
           f"/TestPoint?api-version=7.1")
    ok, items, d = P._ado_rest_get_all(url, timeout)
    if not ok:
        return (False, None, d)
    if not isinstance(items, list):
        return False, None, "Malformed release test-point response"
    seen = set()
    out = []
    for pt in items:
        if not isinstance(pt, dict):
            return False, None, "Malformed release test point"
        if any(pt.get(key) is not None and not isinstance(pt[key], dict)
               for key in ("testCaseReference", "results", "configuration")):
            return False, None, "Malformed release test-point fields"
        tc = pt.get("testCaseReference") or {}
        res = pt.get("results") or {}
        ids = (pt.get("id"), tc.get("id"), (pt.get("configuration") or {}).get("id"))
        if any(not str(v).isascii() or not str(v).isdigit() or int(v) <= 0 for v in ids):
            return False, None, "Invalid release test-point identity"
        if str(pt["id"]) in seen:
            return False, None, "Duplicate release test-point identity"
        seen.add(str(pt["id"]))
        try:
            tester = D.point_tester(pt.get("tester"))
        except ValueError as exc:
            return False, None, str(exc)
        out.append({"point_id": str(pt.get("id")), "case_id": str(tc.get("id")),
                    "config_id": str((pt.get("configuration") or {}).get("id")), "name": tc.get("name"),
                    "outcome": res.get("outcome") or pt.get("outcome") or "unspecified",
                    "tester_upn": tester["upn"], "tester_id": tester["id"]})
    return (True, out, "")


def _broker_points(broker_plan_id, suite_name, timeout=90):
    """Points across the Broker 'Manual Tests (Android Broker)' subtree of the plan."""
    okr, root, d = D.find_suite_id_by_name(broker_plan_id, suite_name, timeout)
    if not okr or not root:
        return (False, None, d or f"'{suite_name}' suite not found in plan {broker_plan_id}")
    ok, subtree, d = D._suite_subtree(broker_plan_id, root, timeout)
    if not ok:
        return (False, None, d)
    pts = []
    for sid in subtree:
        okp, sp, dp = _points_of_suite(broker_plan_id, sid, timeout)
        if not okp:
            return (False, None, dp)
        pts += sp
    return (True, pts, "")


def _broker_ui_triage_points(broker_plan_id, result, timeout):
    """Read only the fill's originally failed points, preserving their case/config identity."""
    if isinstance(result, dict) and result.get("mapping_version") in (3, 4):
        from steps.bug_bash.ui_results import _scenario_points
        if not isinstance(result.get("target"), dict) or str(result["target"].get("plan_id")) != str(broker_plan_id):
            return False, None, "Scenario progress destination changed"
        try:
            _scenario_points(result, complete=True)
        except ValueError as exc:
            return False, None, str(exc)
        collected = []
        for sid in result["suite_targets"]:
            points = [p for p in result["applied_points"] if p["suite_id"] == sid]
            partial = {"target": {"plan_id": broker_plan_id, "suite_id": sid},
                       "applied_points": points, "failed_case_ids": sorted({
                           p["case_id"] for p in points if p["outcome"] == "Failed"})}
            ok, rows, detail = _broker_ui_triage_points(broker_plan_id, partial, timeout)
            if not ok:
                return False, None, detail
            collected.extend(rows)
        if {int(p["case_id"]) for p in collected} != set(result["failed_case_ids"]):
            return False, None, "Scenario failed cases do not match retained failure receipts"
        return True, collected, ""
    if (not isinstance(result, dict) or not isinstance(result.get("target"), dict)
            or str(result["target"].get("plan_id")) != str(broker_plan_id)
            or not result["target"].get("suite_id")
            or not isinstance(result.get("failed_case_ids"), list)
            or not isinstance(result.get("applied_points"), list)):
        return False, None, "Missing/mismatched completed Broker UI triage evidence"
    expected = {}
    for point in result["applied_points"]:
        if not isinstance(point, dict):
            return False, None, "Malformed applied Broker UI point"
        if point.get("outcome") != "Failed":
            continue
        pid = str(point.get("point_id"))
        if pid in expected or any(not str(point.get(k)).isdigit() or int(point[k]) <= 0
                                  for k in ("point_id", "case_id", "config_id")):
            return False, None, "Invalid/duplicate applied Broker failure point"
        expected[pid] = (str(point["case_id"]), str(point["config_id"]))
    if {cid for cid, _ in expected.values()} != {str(cid) for cid in result["failed_case_ids"]}:
        return False, None, "Broker failed cases have no matching applied failure points"
    if not expected:
        return True, [], ""
    ok, rows, detail = _points_of_suite(broker_plan_id, result["target"]["suite_id"], timeout)
    if not ok:
        return False, None, detail
    found = {}
    for row in rows:
        pid = row["point_id"]
        if pid not in expected:
            continue
        if pid in found or (row["case_id"], row["config_id"]) != expected[pid]:
            return False, None, "Broker triage point case/configuration changed; refresh the owning fill"
        found[pid] = row
    if set(found) != set(expected):
        return False, None, "Applied Broker triage points missing from the release suite; refresh the owning fill"
    return True, list(found.values()), ""


def gather_progress(broker_plan_id, broker_suite_name, auth_plan_id, auth_suite_id,
                    timeout=90, auto_failed_ids=None, *, auth_automated_ids, broker_ui_result,
                    auth_ui_result=None, monthly_result=None):
    """(ok, progress, detail). Reads live test points from the Broker manual subtree +
    the Auth bug-bash suite, groups cases by their release-point Tester, and computes a
    per-owner + overall breakdown of manual work and retained failure triage for BOTH apps.

    `auth_automated_ids` is the completed fill's automated-case classification, also
    used by distribution. Exclude those Auth cases unless they are applied failures
    requiring triage. Never infer automation from a Passed outcome or a shared tag:
    real manual passes must count, and ownership/outcomes still come from live ADO.

    `auto_failed_ids` are the Authenticator cases written Failed by `ui_test_status`;
    actual owners come from live assignments — they're flagged per-test as
    `auto_failed` so the update can show them as 'triage', distinct from manual tests the
    owner still needs to RUN. They still count as remaining (a failure needs resolution).

    `broker_ui_result` comes from the same completed fill's broker record. Only its
    originally failed points enter triage; successful automation and untouched configurations
    never inflate the workload. Multiple failed configurations count as one case, resolved
    only when all tracked failures have live Passed/N/A outcomes. Ownership is read live.

    progress = {
      total, done, remaining, auto_failed_remaining, auto_failed_remaining_by_product,
      auth_excluded_automated,
      owners: { upn: {name, total, done, remaining,
                      tests: [{id, name, url, state, products, auto_failed}]} },
      unassigned: <count of cases with unassigned release points> }
    """
    if auth_automated_ids is None:
        return False, None, "Missing completed-fill automated-case classification; no progress prepared"
    automated = {str(i) for i in auth_automated_ids}
    auto_failed = {str(i) for i in (auto_failed_ids or [])}
    if not auto_failed <= automated:
        return False, None, "Applied Auth failures are missing from the automated-case classification"
    auth_expected = {}
    auth_target = (auth_ui_result or {}).get("target") or {}
    category = "layout" in auth_target
    if category:
        from tools import auth_partition as A
        try:
            A.validate_target(auth_target)
            if (auth_target["plan_id"] != int(auth_plan_id)
                    or auth_target["parent_suite_id"] != int(auth_suite_id)
                    or set(auth_target["classification"]["mapped_case_ids"]) !=
                       {int(cid) for cid in auth_automated_ids}):
                raise ValueError("Authenticator progress category binding changed")
        except (ValueError, TypeError) as exc:
            return False, None, str(exc)
    if auto_failed:
        if (not isinstance(auth_ui_result, dict)
                or not category and auth_ui_result.get("target") != {
                    "plan_id": int(auth_plan_id), "suite_id": int(auth_suite_id)}
                or not isinstance(auth_ui_result.get("applied_points"), list)):
            return False, None, "Missing/mismatched completed Auth failure-point evidence"
        for point in auth_ui_result["applied_points"]:
            if point.get("outcome") == "Failed":
                pid, cid = str(point.get("point_id")), str(point.get("case_id"))
                if pid in auth_expected or not pid.isascii() or not pid.isdigit() or int(pid) <= 0:
                    return False, None, "Invalid/duplicate applied Auth failure point"
                auth_expected[pid] = (cid, str(point.get("config_id"))) if category else cid
        if {v[0] if category else v for v in auth_expected.values()} != auto_failed:
            return False, None, "Auth failed cases have no matching applied failure points"
    ok_ui, ui_points, detail = _broker_ui_triage_points(broker_plan_id, broker_ui_result, timeout)
    if not ok_ui:
        return False, None, f"broker UI triage: {detail}"
    okb, bpts, db = _broker_points(broker_plan_id, broker_suite_name, timeout)
    if not okb:
        return (False, None, f"broker: {db}")
    if category:
        oka, apts, da = _points_of_suite(auth_plan_id, auth_target["manual_suite_id"], timeout)
        if not oka:
            return False, None, f"auth manual: {da}"
        ok_auto, auto_points, detail = _points_of_suite(auth_plan_id, auth_target["suite_id"], timeout)
        if not ok_auto:
            return False, None, f"auth automated: {detail}"
        if (any(str(p["case_id"]) in automated for p in apts)
                or any(str(p["case_id"]) not in automated for p in auto_points)
                or {p["point_id"] for p in apts} & {p["point_id"] for p in auto_points}):
            return False, None, "Authenticator points no longer match the category partition"
        apts = [*apts, *auto_points]
    else:
        oka, apts, da = _points_of_suite(auth_plan_id, auth_suite_id, timeout)
    if not oka:
        return (False, None, f"auth: {da}")

    found = {}
    for pt in apts:
        pid = str(pt.get("point_id"))
        if pid in auth_expected:
            identity = (str(pt["case_id"]), str(pt.get("config_id"))) if category else str(pt["case_id"])
            if pid in found or identity != auth_expected[pid]:
                return False, None, "Auth failure point case identity changed; refresh the owning fill"
            found[pid] = pt
    if set(found) != set(auth_expected):
        return False, None, "Applied Auth failure points missing from the release suite; refresh the owning fill"
    excluded = {pt["case_id"] for pt in apts if pt["case_id"] in automated - auto_failed}
    apts = [pt for pt in apts if pt["case_id"] not in automated
            or str(pt.get("point_id")) in auth_expected]
    auth_triage = {pt["case_id"] for pt in apts if pt["case_id"] in auto_failed}
    if auth_triage != auto_failed:
        return False, None, "Applied Auth failure cases missing from the release suite; refresh the owning fill"
    broker_triage = {pt["case_id"] for pt in ui_points}
    monthly_points, monthly_ids = [], set()
    if "monthly" in auth_target:
        from tools import auth_monthly as M
        try:
            record = auth_target["monthly"]
            M.validate_fill(monthly_result or {}, record, (monthly_result or {}).get("source") or {})
            if record["suite_id"]:
                ok, monthly_points, detail = _points_of_suite(auth_plan_id, record["suite_id"], timeout)
                if not ok:
                    raise ValueError(detail)
                expected = {(str(b["point_id"]), str(b["case_id"]), str(b["config_id"]))
                            for b in record["bindings"]}
                if {(p["point_id"], p["case_id"], p["config_id"]) for p in monthly_points} != expected:
                    raise ValueError("Monthly progress point identities changed")
                monthly_ids = {p["case_id"] for p in monthly_points}
                if monthly_ids & (automated | {p["case_id"] for p in apts}):
                    raise ValueError("Monthly investigations leaked into curated progress")
        except (ValueError, KeyError, TypeError) as exc:
            return False, None, str(exc)
    # Product comes from the source plan, not the case title or automation classification.
    cases, owner_cases, triage_outcomes = {}, {}, {}
    for product, points, failure_ids in (("Broker", bpts, set()), ("Authenticator", apts, auth_triage),
                                        ("Broker", ui_points, broker_triage),
                                        ("Authenticator", monthly_points, monthly_ids)):
        for pt in points:
            cid = pt["case_id"]
            c = cases.setdefault(cid, {"name": pt.get("name"), "outcomes": [], "products": set()})
            c["outcomes"].append(pt.get("outcome"))
            c["products"].add(product)
            if "tester_upn" not in pt:
                return False, None, "Release-point Tester observation missing; no case-owner fallback"
            upn = D._identity(pt["tester_upn"]) or None
            owned = owner_cases.setdefault((upn, cid), {
                "name": pt.get("name"), "outcomes": [], "products": set(), "auto_products": set()})
            owned["outcomes"].append(pt.get("outcome"))
            owned["products"].add(product)
            if cid in failure_ids:
                owned["auto_products"].add(product)
                triage_outcomes.setdefault((product, cid), []).append(pt.get("outcome"))

    owners, unassigned = {}, 0
    total = done = auto_failed_remaining = 0
    remaining_by_product = {"Broker": 0, "Authenticator": 0}
    for cid, c in cases.items():
        total += 1
        state = _case_state(c["outcomes"])
        is_done = state in ("passed", "na")     # only clean-pass / N-A count as done;
        if is_done:                              # failed + blocked + notrun are "remaining"
            done += 1
        pending_products = [product for (product, case), outcomes in triage_outcomes.items()
                            if case == cid and _case_state(outcomes) not in ("passed", "na")]
        if pending_products:
            auto_failed_remaining += 1
            for product in pending_products:
                remaining_by_product[product] += 1
    for (upn, cid), c in owner_cases.items():
        state = _case_state(c["outcomes"])
        is_done = state in ("passed", "na")
        products = sorted(c["auto_products"])
        af = bool(products)
        if not upn:
            unassigned += 1
            continue
        o = owners.setdefault(upn, {"name": upn, "total": 0, "done": 0, "remaining": 0,
                                    "tests": []})
        o["total"] += 1
        o["done"] += 1 if is_done else 0
        o["remaining"] += 0 if is_done else 1
        o["tests"].append({"id": cid, "name": c.get("name") or f"Test {cid}",
                           "url": f"{ORG}/{PROJECT}/_workitems/edit/{cid}", "state": state,
                           "products": sorted(c["products"]),
                           "auto_failed": af, "auto_products": products,
                           **({"investigation_kind": "monthly"} if cid in monthly_ids else {})})

    return (True, {"total": total, "done": done, "remaining": total - done,
                   "auto_failed_remaining": auto_failed_remaining,
                   "auto_failed_remaining_by_product": remaining_by_product,
                   "auth_excluded_automated": len(excluded),
                   "owners": owners, "unassigned": unassigned}, "")


# ----------------------------------------------------------------- render

def all_complete(progress) -> bool:
    return (bool(progress) and (progress.get("total", 0) > 0 or progress.get("auth_excluded_automated", 0) > 0)
            and progress.get("remaining") == 0)


def mention_read_request(chat_id, owner):
    return m365.observation_request("microsoft_teams-ListChatMembers", {"chatId": chat_id}, owner)


def _mention_members(chat_id, observation, owner):
    if not isinstance(observation, dict):
        raise ValueError("Member observation requires an owner-bound M365 envelope; raw legacy JSON is not fresh evidence")
    tool = observation.get("tool")
    if tool not in {"workiq_get_chat", "microsoft_teams-ListChatMembers"}:
        raise ValueError("Member observation must use workiq_get_chat or microsoft_teams-ListChatMembers")
    result = m365.validate_observation(
        observation, tool=tool, arguments={"chatId": chat_id}, owner=owner)
    if isinstance(result, dict):
        if (any(result.get(key) for key in (
                "@odata.nextLink", "members@odata.nextLink", "nextLink",
                "nextSkipToken", "hasMoreResults", "isPartial", "truncated"))
                or result.get("complete") is False):
            raise ValueError("Member observation must contain complete, unpaginated members for this meeting")
        if tool == "workiq_get_chat":
            if result.get("id") != chat_id or result.get("chatType") != "meeting":
                raise ValueError("Member observation must describe this meeting")
        elif result.get("chatId", chat_id) != chat_id:
            raise ValueError("Member observation belongs to another chat")
        members = (result.get("members") if tool == "workiq_get_chat" else
                   result.get("members", result.get("value")))
        if "@odata.count" in result and (
                not isinstance(members, list) or type(result["@odata.count"]) is not int
                or result["@odata.count"] != len(members)):
            raise ValueError("Incomplete meeting member count")
    else:
        members = result if tool == "microsoft_teams-ListChatMembers" else None
    if not isinstance(members, list):
        raise ValueError("Member observation must contain actual complete structured members")
    ids, emails = set(), set()
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("Malformed meeting-member identity")
        try:
            identity = str(UUID(member["userId"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValueError("Meeting members require real userId values, not membership IDs") from None
        email = member.get("email")
        if email == "":
            email = None
        if (email is not None and not D._mail_identity(email)
                or not isinstance(member.get("displayName"), str)
                or not member["displayName"].strip() or "@" in member["displayName"]):
            raise ValueError("Missing meeting-member displayName/email; obtain explicit directory evidence")
        if identity in ids or email is not None and email.casefold() in emails:
            raise ValueError("Ambiguous/duplicate meeting identity")
        ids.add(identity)
        if email is not None:
            emails.add(email.casefold())
    return members


def mention_directory_path(upn):
    if not D._mail_identity(upn):
        raise ValueError("Pending tester requires an exact directory UPN")
    return f"/users/{quote(upn, safe='')}?$select=id,displayName,userPrincipalName,mail"


def _mention_directory(owners, members, observation, owner):
    supplied = observation.get("directory_observations", [])
    if not isinstance(supplied, list):
        raise ValueError("directory_observations must be a list of fresh owner-bound envelopes")
    requested = {mention_directory_path(upn): upn for upn in owners}
    member_ids = {str(UUID(member["userId"])) for member in members}
    found = {}
    for envelope in supplied:
        if not isinstance(envelope, dict):
            raise ValueError("Directory evidence requires an owner-bound M365 envelope")
        args = envelope.get("arguments")
        paths = args.get("entityUrls") if isinstance(args, dict) else None
        if (not isinstance(paths, list) or len(paths) != 1
                or not isinstance(paths[0], str) or paths[0] not in requested):
            raise ValueError("Directory evidence must match an exact pending tester UPN request")
        path = paths[0]
        upn = requested[path]
        if upn in found:
            raise ValueError("Duplicate directory evidence for pending tester")
        user = m365.validate_observation(
            envelope, tool="workiq.fetch", arguments={"entityUrls": [path]}, owner=owner)
        if (not isinstance(user, dict) or not D._mail_identity(user.get("userPrincipalName"))
                or D._identity(user["userPrincipalName"]) != D._identity(upn)
                or not isinstance(user.get("displayName"), str) or not user["displayName"].strip()
                or user.get("mail") is not None and not D._mail_identity(user["mail"])
                or any(user.get(key) for key in (
                    "@odata.nextLink", "nextLink", "hasMoreResults", "isPartial", "truncated"))
                or user.get("complete") is False):
            raise ValueError("Directory evidence returned an incomplete/different pending tester")
        try:
            identity = str(UUID(user["id"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValueError("Directory evidence requires the real directory user GUID") from None
        if identity not in member_ids:
            raise ValueError("Directory user is not a verified member of this meeting")
        member = next(row for row in members if str(UUID(row["userId"])) == identity)
        if (member.get("email") and user.get("mail")
                and D._identity(member["email"]) not in {
                    D._identity(user["mail"]), D._identity(user["userPrincipalName"])}):
            raise ValueError("Conflicting directory mail and meeting-member identity")
        found[upn] = identity
    return found


def resolve_mention_people(chat_id, owners, timeout=90, *, member_observation=None, owner=None):
    """Use exact-chat connector evidence only; no Graph token/directory fallback."""
    if member_observation is None:
        if not any(row["remaining"] for row in owners.values()):
            return True, {upn: {"name": row["name"]} for upn, row in owners.items()}, ""
        return False, None, (
            f"Gather complete members of this meeting ({chat_id}) using "
            "microsoft_teams-ListChatMembers or workiq_get_chat. Save a fresh owner-bound M365 "
            "observation envelope and supply --members-file <path> "
            "(step-action: --param members_file=<path>). No notification is prepared.")
    try:
        members = _mention_members(chat_id, member_observation, owner)
        directory = _mention_directory(owners, members, member_observation, owner)
    except ValueError as exc:
        return False, None, str(exc)
    people, missing = {}, []
    for upn in sorted(owners):
        matches = [m for m in members if isinstance(m, dict)
                   and str(m.get("email") or "").casefold() == upn.casefold()]
        if len(matches) > 1:
            return False, None, f"Ambiguous meeting identity for {upn}"
        person = matches[0] if matches else None
        identity = directory.get(upn)
        if identity and person and identity != str(UUID(person["userId"])):
            return False, None, f"Conflicting member/directory evidence for {upn}; no notification prepared"
        if identity and person is None:
            person = next(member for member in members if str(UUID(member["userId"])) == identity)
        if person and person.get("displayName") and person.get("userId"):
            people[upn] = {"id": person["userId"], "name": person["displayName"]}
            continue
        if not owners[upn]["remaining"]:
            # Completed former owners aren't tagged; never invent a mention for them.
            people[upn] = {"name": owners[upn]["name"]}
            continue
        missing.append({"entityUrls": [mention_directory_path(upn)]})
    if missing:
        raise D.ConnectorObservationRequired(
            "Missing explicit directory evidence linking pending tester UPNs to this meeting's "
            "member userId values. If the required workiq.fetch entity tool is not loaded, configure "
            "the installed M365 entity connector; collect the exact requested reads. Append "
            "fresh owner-bound envelopes as directory_observations on the members-file envelope, "
            "then rerun with --members-file <path>. Never guess mappings or omit mentions.",
            tool="workiq.fetch", arguments=missing[0], owner=owner,
            additional_requests=[m365.observation_request("workiq.fetch", args, owner)
                                 for args in missing[1:]])
    return True, people, ""


def render_update(progress, month_year, plan_links, people=None, *, mention_pending=True):
    """(html, mentions) for the Teams chat update.

    Returns canonical Graph mentions bound to matching <at> IDs/display names.
    Pending owners require verified people[upn] = {id: AAD GUID, name: display name}.
    Show every case (including resolved work), with its product and live status.
    Completed owners retain their full case list without being tagged.
    A private/manual preview can explicitly omit mentions; outbound callers keep
    the default verified-identity requirement.
    """
    people = people or {}

    def disp(upn, fallback):
        return (people.get(upn) or {}).get("name") or fallback

    total, done = progress.get("total", 0), progress.get("done", 0)
    pct = round(done * 100.0 / total) if total else 0
    owners = progress.get("owners") or {}
    mentions = []

    rows = []
    # remaining-first, most-remaining at the top
    for upn in sorted(owners, key=lambda u: (-owners[u]["remaining"], disp(u, u).lower(), u.casefold())):
        o = owners[upn]
        who = disp(upn, o["name"])
        if o["remaining"] == 0:
            heading = (f'<b>{escape(who)}</b> — {o["done"]}/{o["total"]} done, <b>0 remaining</b> '
                       f'<span style="color:#107c10;">(all {o["total"]} tests completed ✅)</span>')
        elif not mention_pending:
            heading = (f'<b>{escape(who)}</b> — '
                       f'{o["done"]}/{o["total"]} done, <b>{o["remaining"]} remaining</b>')
        else:
            mi = len(mentions)
            person = people.get(upn) or {}
            try:
                identity = str(UUID(person["id"]))
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ValueError(f"Missing verified Teams user ID for {upn}; no progress message prepared") from None
            if not isinstance(person.get("name"), str) or not person["name"].strip() or "@" in person["name"]:
                raise ValueError(f"Missing Teams display name for {upn}; no progress message prepared")
            mentions.append({"id": mi, "mentionText": who,
                            "mentioned": {"user": {"id": identity, "displayName": who,
                                                    "userIdentityType": "aadUser"}}})
            heading = (f'<b><at id="{mi}">{escape(who)}</at></b> — '
                       f'{o["done"]}/{o["total"]} done, <b>{o["remaining"]} remaining</b>')
        _ORDER = {"failed": 0, "blocked": 1, "notrun": 2, "passed": 3, "na": 4}
        tests = sorted(o["tests"], key=lambda t: (
            t["state"] in ("passed", "na"), 0 if t.get("auto_failed") else 1,
            _ORDER.get(t["state"], 2), t["id"]))

        def _row(t):
            products = t.get("products")
            if (not isinstance(products, list) or not products
                    or any(p not in ("Broker", "Authenticator") for p in products)):
                raise ValueError(f"Missing/invalid source product for test {t['id']}; gather fresh progress")
            badge = " / ".join(dict.fromkeys(products))
            resolved = t["state"] in ("passed", "na")
            icon = _STATE_ICON.get(t["state"], "⬜")
            color = "#107c10" if resolved else "#a4262c"
            triage = (f' <span style="color:{color};">(Automation triage)</span>'
                      if t.get("auto_failed") else "")
            return (f'<li>{icon} <b>[{escape(badge)}]</b> '
                    f'<a href="{escape(t["url"], quote=True)}">{escape(str(t["id"]))}</a> — '
                    f'{escape(t["name"])}{triage}</li>')
        items = "".join(_row(t) for t in tests)
        rows.append(
            f'<div style="margin:10px 0;">{heading}:'
            f'<ul style="margin:4px 0 0;padding-left:20px;">{items}</ul></div>')

    links = " &nbsp;·&nbsp; ".join(
        f'<a href="{escape(l["url"], quote=True)}">{escape(l["name"])}</a>' for l in (plan_links or []))
    auto_n = progress.get("auto_failed_remaining", 0)
    by_product = progress.get("auto_failed_remaining_by_product", {"Authenticator": auto_n})
    auto_note = "".join(
        f'<p style="font-size:13px;color:#a4262c;">\U0001f52c {count} failed '
        f'automated {escape(product)} case(s) need investigation (see current assignees) '
        f'(investigate — not manual re-runs).</p>'
        for product, count in by_product.items() if count)
    legend = " · ".join(f"{_STATE_ICON[state]} {_STATE_WORD[state]}"
                        for state in ("passed", "na", "failed", "blocked", "notrun"))
    html = (
        f'<div style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:14px;">'
        f'<p><b>🐞 {escape(month_year)} Bug Bash — progress update</b><br>'
        f'<b>{done}/{total} tests done ({pct}%)</b> · {progress.get("remaining",0)} remaining. '
        f'Mark pass/fail in the ADO test plan; report bugs/logs here.</p>'
        f'<p style="font-size:13px;color:#605e5c;">{links}</p>'
        f'<p style="font-size:12px;color:#605e5c;">{legend}</p>'
        f'{auto_note}'
        f'{"".join(rows)}'
        f'</div>')
    return html, mentions
