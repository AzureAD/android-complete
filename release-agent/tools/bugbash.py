"""Bug-bash progress + scheduling helpers for Phase 3 (bug_bash) — the periodic-update
poster (`bugbash_updates`).

Two concerns:
  * scheduling — is_working_time(now): weekday, not a US holiday, 09:00–18:00 local. The
    poster runs every 2h inside this window (America/Los_Angeles), skipping weekends and
    hardcoded US federal holidays; it resumes at 09:00 the next working day.
  * progress — gather_progress(...): read the live test-point outcomes from BOTH the Broker
    'Manual Tests (Android Broker)' subtree and the Authenticator bug-bash suite, grouped
    by the case's System.AssignedTo (what distribute_tests set — the point's own `tester`
    field is NOT reliably synced with AssignedTo). render_update(...) turns that into the
    Teams message HTML + the @mention list (mention owners with remaining tests; name-only
    for owners who finished all).

All ADO reads go through tools.pipelines / tools.distribution helpers (bearer token).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from tools import pipelines as P
from tools import testplans as T
from tools import distribution as D

ORG = T.ORG
PROJECT = T.PROJECT

WORK_START_HOUR = 9        # 09:00 local — first post of the day / window open
WORK_END_HOUR = 18         # 18:00 local — stop for the day
POLL_INTERVAL_HOURS = 2


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
    """(ok, [{case_id, name, outcome}], detail) — the test points of ONE suite."""
    url = (f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}"
           f"/TestPoint?api-version=7.1")
    ok, items, d = P._ado_rest_get_all(url, timeout)
    if not ok:
        return (False, None, d)
    out = []
    for pt in items:
        tc = pt.get("testCaseReference") or {}
        res = pt.get("results") or {}
        out.append({"case_id": str(tc.get("id")), "name": tc.get("name"),
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


def gather_progress(broker_plan_id, broker_suite_name, auth_plan_id, auth_suite_id,
                    timeout=90, auto_failed_ids=None):
    """(ok, progress, detail). Reads live test points from the Broker manual subtree +
    the Auth bug-bash suite, groups the CASES by their System.AssignedTo, and computes a
    per-owner + overall breakdown.

    `auto_failed_ids` are the Authenticator cases that FAILED in automation and were
    pre-assigned to the release owner by `ui_test_status` — they're flagged per-test as
    `auto_failed` so the update can show them as 'triage', distinct from manual tests the
    owner still needs to RUN. They still count as remaining (a failure needs resolution).

    progress = {
      total, done, remaining, auto_failed_remaining,
      owners: { upn: {name, total, done, remaining,
                      tests: [{id, name, url, state, auto_failed}]} },
      unassigned: <count of cases with no AssignedTo> }
    """
    auto_failed = {str(i) for i in (auto_failed_ids or [])}
    okb, bpts, db = _broker_points(broker_plan_id, broker_suite_name, timeout)
    if not okb:
        return (False, None, f"broker: {db}")
    oka, apts, da = _points_of_suite(auth_plan_id, auth_suite_id, timeout)
    if not oka:
        return (False, None, f"auth: {da}")

    # case_id -> {name, outcomes:[...]}
    cases = {}
    for pt in bpts + apts:
        cid = pt["case_id"]
        c = cases.setdefault(cid, {"name": pt.get("name"), "outcomes": []})
        c["outcomes"].append(pt.get("outcome"))

    # owner (AssignedTo) per case
    oka2, amap, da2 = D._cases_assignedto(list(cases.keys()), timeout)
    if not oka2:
        return (False, None, f"assignedTo: {da2}")

    owners, unassigned = {}, 0
    total = done = auto_failed_remaining = 0
    for cid, c in cases.items():
        total += 1
        state = _case_state(c["outcomes"])
        is_done = state in ("passed", "na")     # only clean-pass / N-A count as done;
        if is_done:                              # failed + blocked + notrun are "remaining"
            done += 1
        af = cid in auto_failed
        if af and not is_done:
            auto_failed_remaining += 1
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
                           "auto_failed": af})

    return (True, {"total": total, "done": done, "remaining": total - done,
                   "auto_failed_remaining": auto_failed_remaining,
                   "owners": owners, "unassigned": unassigned}, "")


# ----------------------------------------------------------------- render

def all_complete(progress) -> bool:
    return bool(progress) and progress.get("total", 0) > 0 and progress.get("remaining", 0) == 0


def render_update(progress, month_year, plan_links, name_by_upn=None):
    """(html, mentions) for the Teams chat update.

    mentions = [{upn, name}] — the owners who still have REMAINING tests (they get an
    @mention). Owners who finished all appear by NAME with an 'all completed' line and are
    NOT mentioned. `plan_links` = [{name,url}] (the two live test plans).
    """
    name_by_upn = name_by_upn or {}

    def disp(upn, fallback):
        return name_by_upn.get(upn) or fallback

    total, done = progress.get("total", 0), progress.get("done", 0)
    pct = round(done * 100.0 / total) if total else 0
    owners = progress.get("owners") or {}
    mentions = []

    rows = []
    # remaining-first, most-remaining at the top
    for upn in sorted(owners, key=lambda u: (-owners[u]["remaining"], disp(u, u).lower())):
        o = owners[upn]
        who = disp(upn, o["name"])
        if o["remaining"] == 0:
            rows.append(f'<div style="margin:8px 0;"><b>{who}</b> — '
                        f'<span style="color:#107c10;">all {o["total"]} tests completed ✅</span></div>')
            continue
        mi = len(mentions)
        mentions.append({"id": mi, "upn": upn, "name": who})
        # list every not-done test (failed → blocked → not-run), each with its state icon.
        # A pre-triaged automated auth failure (auto_failed) is shown distinctly — it's an
        # investigation the owner already owns, NOT a manual test to run.
        _ORDER = {"failed": 0, "blocked": 1, "notrun": 2}
        pending = sorted((t for t in o["tests"] if t["state"] in _ORDER),
                         key=lambda t: (0 if t.get("auto_failed") else 1,
                                        _ORDER[t["state"]], t["id"]))

        def _row(t):
            if t.get("auto_failed"):
                return (f'<li style="margin:2px 0;">\U0001f52c '
                        f'<a href="{t["url"]}">{t["id"]}</a> — {t["name"]} '
                        f'<span style="color:#a4262c;">(Automated failure — triage)</span></li>')
            return (f'<li style="margin:2px 0;">{_STATE_ICON.get(t["state"], "⬜")} '
                    f'<a href="{t["url"]}">{t["id"]}</a> — {t["name"]} '
                    f'<span style="color:#605e5c;">({_STATE_WORD.get(t["state"], t["state"])})</span></li>')
        items = "".join(_row(t) for t in pending)
        rows.append(
            f'<div style="margin:10px 0;"><b><at id="{mi}">{who}</at></b> — '
            f'{o["done"]}/{o["total"]} done, <b>{o["remaining"]} remaining</b>:'
            f'<ul style="margin:4px 0 0;padding-left:20px;">{items}</ul></div>')

    links = " &nbsp;·&nbsp; ".join(f'<a href="{l["url"]}">{l["name"]}</a>' for l in (plan_links or []))
    auto_n = progress.get("auto_failed_remaining", 0)
    auto_note = (f'<p style="font-size:13px;color:#a4262c;">\U0001f52c {auto_n} failed '
                 f'automated Authenticator case(s) are pre-assigned for owner triage '
                 f'(investigate — not manual re-runs).</p>' if auto_n else "")
    html = (
        f'<div style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:14px;">'
        f'<p><b>🐞 {month_year} Bug Bash — progress update</b><br>'
        f'<b>{done}/{total} tests done ({pct}%)</b> · {progress.get("remaining",0)} remaining. '
        f'Mark pass/fail in the ADO test plan; report bugs/logs here.</p>'
        f'<p style="font-size:13px;color:#605e5c;">{links}</p>'
        f'{auto_note}'
        f'{"".join(rows)}'
        f'</div>')
    return html, mentions
