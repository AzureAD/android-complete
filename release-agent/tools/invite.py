"""Bug-bash invite helpers for Phase 3 (bug_bash) — the `send_invite` step.

Three concerns, all here so the step stays thin:
  * schedule_bugbash(now) — the deterministic "when" rule (pure, testable).
  * local_flights(...) — fetch the local-flights flags JSON from ADO variable group 40.
  * render_invite(...) — fill the HTML template (templates/bug-bash-invite.html) with the
    release's real links + flags.

Scheduling rule (agreed):
  * Reached AFTER 3pm, or on a weekend  -> next BUSINESS morning at 09:00.
  * Reached before 3pm on a weekday      -> later the SAME day (now + a short notice,
    rounded up to the next half hour).
  Weekends always roll forward to Monday.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from tools import pipelines as P

ORG = P.ENGINEERING_ORG
PROJECT = P.ENGINEERING_PROJECT
_TPL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

CUTOFF_HOUR = 15          # 3pm — after this, schedule for the next business morning
MORNING_HOUR = 9          # next-day start
SAME_DAY_NOTICE_H = 2     # same-day: start this many hours out (rounded up to :30)
DURATION_HOURS = 2
FLAGS_GROUP_ID = 40       # ADO variable group "release" holding `local-flights`

FLAGS_GROUP_URL = (f"{ORG}/{PROJECT}/_library?view=VariableGroupView"
                   f"&variableGroupId={FLAGS_GROUP_ID}&path=release")


def _next_business_day(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:          # Sat=5, Sun=6
        d += timedelta(days=1)
    return d


def _round_up_half_hour(dt):
    dt = dt.replace(second=0, microsecond=0)
    if dt.minute == 0 or dt.minute == 30:
        return dt
    if dt.minute < 30:
        return dt.replace(minute=30)
    return (dt.replace(minute=0) + timedelta(hours=1))


def schedule_bugbash(now, duration_hours=DURATION_HOURS):
    """Return (start, end, when_note) — naive local datetimes for the meeting.

    `now` is the local datetime the step runs at. See the module docstring for the rule."""
    weekend = now.weekday() >= 5
    late = now.hour >= CUTOFF_HOUR
    if weekend or late:
        day = _next_business_day(now.date())
        start = datetime(day.year, day.month, day.day, MORNING_HOUR, 0)
    else:
        start = _round_up_half_hour(now + timedelta(hours=SAME_DAY_NOTICE_H))
    end = start + timedelta(hours=duration_hours)
    when = f"{start.strftime('%A, %b %d')} · {start.strftime('%-I:%M %p') if os.name != 'nt' else start.strftime('%#I:%M %p')}" \
           f"–{end.strftime('%-I:%M %p') if os.name != 'nt' else end.strftime('%#I:%M %p')}"
    return (start, end, when)


def local_flights(group_id=FLAGS_GROUP_ID, timeout=60):
    """(ok, flags_str, detail) — the raw `local-flights` value from ADO variable group
    `group_id`. The value is a brace-wrapped key:value list (not strict JSON)."""
    url = (f"{ORG}/{PROJECT}/_apis/distributedtask/variablegroups/{group_id}"
           f"?api-version=7.1")
    ok, j, _h, d = P._ado_rest_get_h(url, timeout)
    if not ok:
        return (False, None, d)
    v = ((j or {}).get("variables") or {}).get("local-flights")
    val = v.get("value") if isinstance(v, dict) else v
    if not val:
        return (False, None, f"variable group {group_id} has no 'local-flights' value")
    return (True, val, "")


def format_flags_html(flags_str):
    """The local-flights value → readable HTML (comma-separated, space after each comma).
    Kept simple; the raw braces are preserved."""
    if not flags_str:
        return "&lt;not available&gt;"
    return (flags_str.replace("{", "{ ").replace("}", " }")
            .replace(",", ", ").replace("  ", " "))


def build_url(build_id):
    return f"{ORG}/{PROJECT}/_build/results?buildId={build_id}" if build_id else ""


def testplan_url(plan_id, suite_id=None):
    u = f"{ORG}/{PROJECT}/_testPlans/execute?planId={plan_id}"
    if suite_id:
        u += f"&suiteId={suite_id}"
    return u


def load_template(name="bug-bash-invite.html"):
    with open(os.path.join(_TPL_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def render_invite(tokens, template_name="bug-bash-invite.html"):
    """Fill the HTML template by replacing {{TOKEN}} markers. Manual replace (NOT
    str.format) so the braces in the flags JSON are never treated as fields."""
    html = load_template(template_name)
    for k, v in tokens.items():
        html = html.replace("{{" + k + "}}", str(v if v is not None else ""))
    return html
