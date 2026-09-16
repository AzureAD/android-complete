"""Bug-bash progress + scheduling helpers for Phase 3 (bug_bash) — the periodic-update
poster (`bugbash_updates`).

Two concerns:
  * scheduling — is_working_time(now): weekday, not a US holiday, 09:00–18:00 local. The
    poster uses the interval in config/automations.yaml inside this window
    (America/Los_Angeles), skipping weekends and hardcoded US federal holidays.
  * progress — gather_progress(...): read the live test-point outcomes from BOTH the Broker
    manual subtree plus applied Broker UI failure points and the Authenticator bug-bash suite, excluding
    automation-only Auth cases using the completed fill's classification. Grouped
    by the case's live System.AssignedTo (the point's own `tester`
    field is NOT reliably synced with AssignedTo). render_update(...) turns that into the
    Teams message HTML + the @mention list (mention owners with remaining tests; name-only
    for owners who finished all).

All ADO reads go through tools.pipelines / tools.distribution helpers (bearer token).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import quote, urlparse
from uuid import UUID

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
    out = []
    for pt in items:
        tc = pt.get("testCaseReference") or {}
        res = pt.get("results") or {}
        out.append({"point_id": str(pt.get("id")), "case_id": str(tc.get("id")),
                    "config_id": str((pt.get("configuration") or {}).get("id")), "name": tc.get("name"),
                    "outcome": res.get("outcome") or pt.get("outcome") or "unspecified"})
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
                    timeout=90, auto_failed_ids=None, *, auth_automated_ids, broker_ui_result):
    """(ok, progress, detail). Reads live test points from the Broker manual subtree +
    the Auth bug-bash suite, groups the CASES by their System.AssignedTo, and computes a
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
      unassigned: <count of cases with no AssignedTo> }
    """
    if auth_automated_ids is None:
        return False, None, "Missing completed-fill automated-case classification; no progress prepared"
    automated = {str(i) for i in auth_automated_ids}
    auto_failed = {str(i) for i in (auto_failed_ids or [])}
    if not auto_failed <= automated:
        return False, None, "Applied Auth failures are missing from the automated-case classification"
    ok_ui, ui_points, detail = _broker_ui_triage_points(broker_plan_id, broker_ui_result, timeout)
    if not ok_ui:
        return False, None, f"broker UI triage: {detail}"
    okb, bpts, db = _broker_points(broker_plan_id, broker_suite_name, timeout)
    if not okb:
        return (False, None, f"broker: {db}")
    oka, apts, da = _points_of_suite(auth_plan_id, auth_suite_id, timeout)
    if not oka:
        return (False, None, f"auth: {da}")

    automation_only = automated - auto_failed
    excluded = {pt["case_id"] for pt in apts if pt["case_id"] in automation_only}
    apts = [pt for pt in apts if pt["case_id"] not in excluded]
    auth_triage = {pt["case_id"] for pt in apts if pt["case_id"] in auto_failed}
    broker_triage = {pt["case_id"] for pt in ui_points}
    # Product comes from the source plan, not the case title or automation classification.
    cases = {}
    for product, points in (("Broker", bpts), ("Authenticator", apts), ("Broker", ui_points)):
        for pt in points:
            cid = pt["case_id"]
            c = cases.setdefault(cid, {"name": pt.get("name"), "outcomes": [], "products": set()})
            c["outcomes"].append(pt.get("outcome"))
            c["products"].add(product)

    # owner (AssignedTo) per case
    oka2, amap, da2 = D._cases_assignedto(list(cases.keys()), timeout)
    if not oka2:
        return (False, None, f"assignedTo: {da2}")

    owners, unassigned = {}, 0
    total = done = auto_failed_remaining = 0
    remaining_by_product = {"Broker": 0, "Authenticator": 0}
    for cid, c in cases.items():
        total += 1
        state = _case_state(c["outcomes"])
        is_done = state in ("passed", "na")     # only clean-pass / N-A count as done;
        if is_done:                              # failed + blocked + notrun are "remaining"
            done += 1
        products = [product for product, ids in (("Broker", broker_triage), ("Authenticator", auth_triage))
                    if cid in ids]
        af = bool(products)
        if af and not is_done:
            auto_failed_remaining += 1
            for product in products:
                remaining_by_product[product] += 1
        upn = amap.get(cid)
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
                           "auto_failed": af, "auto_products": products})

    return (True, {"total": total, "done": done, "remaining": total - done,
                   "auto_failed_remaining": auto_failed_remaining,
                   "auto_failed_remaining_by_product": remaining_by_product,
                   "auth_excluded_automated": len(excluded),
                   "owners": owners, "unassigned": unassigned}, "")


# ----------------------------------------------------------------- render

def all_complete(progress) -> bool:
    return (bool(progress) and (progress.get("total", 0) > 0 or progress.get("auth_excluded_automated", 0) > 0)
            and progress.get("remaining") == 0)


def resolve_mention_people(chat_id, owners, timeout=90, *, member_observation=None):
    """Resolve pending owners to real AAD users in the target meeting, not UPN mentions."""
    url = f"{D._GRAPH}/chats/{quote(chat_id, safe='')}/members"
    members, seen = [], set()
    if member_observation is not None:
        if (not isinstance(member_observation, dict) or member_observation.get("id") != chat_id
                or member_observation.get("chatType") != "meeting"
                or not isinstance(member_observation.get("members"), list)
                or member_observation.get("@odata.nextLink")
                or member_observation.get("members@odata.nextLink")):
            return False, None, "Member observation must be the complete workiq_get_chat response for this meeting"
        members, url = member_observation["members"], None
    if not owners:
        return True, {}, ""
    while url:
        if (not isinstance(url, str) or url in seen or len(seen) >= 25 or urlparse(url).scheme != "https"
                or urlparse(url).netloc != "graph.microsoft.com"):
            return False, None, "Incomplete/invalid meeting-member pagination"
        seen.add(url)
        ok, page, detail = D._graph_get(url, timeout)
        if not ok:
            return False, None, (
                f"Cannot resolve meeting members: {detail}. Fetch chat {chat_id} with "
                "workiq_get_chat and supply its fresh response via members_file/--members-file.")
        if not isinstance(page, dict) or not isinstance(page.get("value"), list):
            return False, None, "Malformed meeting-member response"
        members.extend(page["value"])
        url = page.get("@odata.nextLink")
    people = {}
    member_ids = {m.get("userId") for m in members if isinstance(m, dict) and m.get("userId")}
    for upn in sorted(owners):
        matches = [m for m in members if isinstance(m, dict)
                   and str(m.get("email") or "").casefold() == upn.casefold()]
        if len(matches) > 1:
            return False, None, f"Ambiguous meeting identity for {upn}"
        person = matches[0] if matches else None
        if person and person.get("displayName") and person.get("userId"):
            people[upn] = {"id": person["userId"], "name": person["displayName"]}
            continue
        if not owners[upn]["remaining"]:
            # Completed former owners aren't tagged; never invent a mention for them.
            people[upn] = {"name": owners[upn]["name"]}
            continue
        ok, user, detail = D._graph_get(
            f"{D._GRAPH}/users/{quote(upn, safe='')}?$select=id,displayName,userPrincipalName,mail", timeout)
        if not ok:
            return False, None, f"Cannot resolve pending owner {upn}: {detail}"
        if (not isinstance(user, dict) or user.get("id") not in member_ids
                or upn.casefold() not in {str(user.get(k) or "").casefold() for k in ("userPrincipalName", "mail")}
                or not user.get("displayName")):
            return False, None, f"Pending owner {upn} is not a verified user in this meeting; fix membership/assignment"
        people[upn] = {"id": user["id"], "name": user["displayName"]}
    return True, people, ""


def render_update(progress, month_year, plan_links, people=None):
    """(html, mentions) for the Teams chat update.

    Returns canonical Graph mentions bound to matching <at> IDs/display names.
    Pending owners require verified people[upn] = {id: AAD GUID, name: display name}.
    Show every case (including resolved work), with its product and live status.
    Completed owners retain their full case list without being tagged.
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
