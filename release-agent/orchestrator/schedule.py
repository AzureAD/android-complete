"""Schedule math for CCD-anchored phases — pure functions, no IO.

The Code Complete Date (CCD) is the anchor the whole release hangs off of.
This module mirrors the logic of ADO pipeline 3038 "Code Complete Calendar
Checker" so the orchestrator resolves the *same* date the pipeline would:

  * Default : the 2nd Wednesday of the release month.
  * Override: a full YYYY-MM-DD, but only if it belongs to the release month
              (a stale cross-month override is ignored, exactly like the pipeline).

Phases anchor to CCD via a spec like "CCD-7" (7 days before) or "CCD+1".
Kept IO-free so it stays deterministic and unit-testable; reading/writing the
pipeline variable lives in tools/checks.py, and CCD is stored on ReleaseState.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

# The release runs on the OWNER's wall clock, not the host's. On a UTC host, a bare
# date.today() rolls to the next day at UTC-midnight (evening the day before, Pacific),
# which opened phases — and fired timed comms — hours early. So "today"/"now" are
# computed in this zone unless a caller overrides it. Needs the `tzdata` package on
# Windows (no system IANA db); falls back to host-local if the zone can't be loaded.
DEFAULT_TZ = "America/Los_Angeles"


def get_tz(name: Optional[str] = None):
    """A tzinfo for `name` (default DEFAULT_TZ), or None if it can't be loaded
    (missing tzdata) — callers then fall back to host-local time."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or DEFAULT_TZ)
    except (ImportError, KeyError, ValueError):   # no tzdata / unknown zone name
        return None


def detect_local_tz() -> Optional[str]:
    """The IANA name of the machine's local timezone (e.g. 'America/Los_Angeles'), or
    None if it can't be determined. Captured at `init` (on the owner's interactive
    machine) and persisted, so later headless automation runs — which may execute in a
    UTC process context — use the OWNER's zone, not the host's."""
    try:
        import tzlocal
        return tzlocal.get_localzone_name()
    except Exception:
        return None


def now_local(tz=None) -> datetime:
    """Timezone-aware 'now' in the release zone (default DEFAULT_TZ). Falls back to a
    naive host-local now only if the zone can't be loaded."""
    z = tz if tz is not None else get_tz()
    return datetime.now(z) if z is not None else datetime.now()


def parse_release_month(release_id: str) -> Tuple[int, int]:
    """'2026-07' -> (2026, 7)."""
    parts = release_id.split("-")
    return int(parts[0]), int(parts[1])


def second_wednesday(year: int, month: int) -> date:
    """The 2nd Wednesday of the month (pipeline 3038's default rule)."""
    weeks = calendar.monthcalendar(year, month)
    wednesdays = [w[calendar.WEDNESDAY] for w in weeks if w[calendar.WEDNESDAY] != 0]
    return date(year, month, wednesdays[1])


def parse_date(s: Optional[str]) -> Optional[date]:
    """Parse 'YYYY-MM-DD' -> date, or None if empty/invalid."""
    if not s or not str(s).strip():
        return None
    try:
        y, m, d = (int(x) for x in str(s).strip().split("-"))
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def default_ccd(release_id: str) -> date:
    """The canonical CCD for a release month: the 2nd Wednesday. This is the
    source of truth — a differing pipeline override is treated as a *question*
    to confirm, not a value to adopt silently."""
    year, month = parse_release_month(release_id)
    return second_wednesday(year, month)


def default_target_month(release_id: str) -> Optional[str]:
    """The default display month a release is NAMED for: the CCD/work month + 1 (the ship
    month). 'YYYY-MM' -> 'YYYY-MM' rolling the year at Dec->Jan. e.g. '2026-08' -> '2026-09'.
    Returns None when the release id isn't YYYY-MM. Defaulted at init and confirmed by the owner."""
    try:
        year, month = parse_release_month(release_id)
    except (ValueError, IndexError):
        return None
    month += 1
    if month == 13:
        month, year = 1, year + 1
    return f"{year:04d}-{month:02d}"


def target_month_id(state) -> Optional[str]:
    """The stored ship-month ('YYYY-MM') the release is named for, or the CCD-month+1 default
    when it hasn't been set yet (so naming works even before the owner confirms it at init)."""
    tm = getattr(state, "target_month", None)
    return tm or default_target_month(getattr(state, "release_id", "") or "")


def target_month_label(state, with_year: bool = True) -> str:
    """The release's display month — 'September 2026' (or just 'September' when with_year=False).
    Reads the stored target_month, else the CCD-month+1 default. '' when unresolvable."""
    tm = target_month_id(state)
    if not tm:
        return ""
    try:
        y, m = (int(x) for x in str(tm).split("-")[:2])
        return f"{calendar.month_name[m]} {y}" if with_year else calendar.month_name[m]
    except (ValueError, IndexError):
        return ""


def month_add(release_id: str, n: int) -> Optional[str]:
    """Shift a 'YYYY-MM' month by n months (n may be negative), rolling the year.
    '2026-09' + 1 -> '2026-10'; '2026-12' + 1 -> '2027-01'. None on a bad id."""
    try:
        year, month = parse_release_month(release_id)
    except (ValueError, IndexError):
        return None
    total = year * 12 + (month - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _ccd_pretty(d: date) -> str:
    """'Wednesday, Sep 9, 2026' — built without %-d/%#d so it's platform-safe."""
    return f"{d.strftime('%A, %b')} {d.day}, {d.year}"


def preview_release(release_id: str) -> Optional[dict]:
    """The full identity of a release for a PRE-init prompt, so the skill can show one
    coherent, unambiguous confirmation instead of 'pick a month' then 'confirm a different
    month'. All derived (no state, no IO): the release id (the CCD/work month), the ship-month
    it's NAMED for (CCD month + 1), and the default CCD (2nd Wednesday). None on a bad id."""
    ship = default_target_month(release_id)
    if not ship:
        return None
    try:
        ccd = default_ccd(release_id)
        sy, sm = parse_release_month(ship)
    except (ValueError, IndexError):
        return None
    return {
        "release_id": release_id,                       # internal id = the CCD/work month
        "ship_month": ship,                             # 'YYYY-MM' the release is named for
        "ship_label": f"{calendar.month_name[sm]} {sy}",  # 'October 2026' — the display name
        "ccd": ccd.isoformat(),
        "ccd_pretty": _ccd_pretty(ccd),                 # 'Wednesday, Sep 9, 2026'
        "ccd_weekday": ccd.strftime("%A"),
    }


def preview_releases(start_release_id: Optional[str] = None, count: int = 4) -> list:
    """`count` consecutive release candidates starting at `start_release_id` (default: the
    current calendar month). Each is a preview_release() dict; the FIRST is the default the
    skill leads with, the rest are the 'a different month' alternatives — all shown by
    name + CCD so the user never picks a bare, ambiguous month."""
    start = start_release_id or now_local().strftime("%Y-%m")
    out = []
    for i in range(max(1, count)):
        mid = month_add(start, i)
        p = preview_release(mid) if mid else None
        if p:
            p["is_default"] = (i == 0)
            out.append(p)
    return out


def pipeline_conflict(release_id: str, override: Optional[str], stored_ccd: Optional[str] = None):
    """Return the pipeline override date IF it is a valid in-month date that
    DIFFERS from our reference CCD — i.e. a divergence the user must resolve.
    Otherwise None (override empty, cross-month, or already in agreement).

    The reference is `stored_ccd` when provided (what this release is anchored
    to), else the 2nd-Wednesday default (used at init before anything is stored).
    """
    od = parse_date(override)
    if not od:
        return None
    year, month = parse_release_month(release_id)
    if (od.year, od.month) != (year, month):
        return None                      # month-scoped, like the pipeline
    reference = parse_date(stored_ccd) or default_ccd(release_id)
    return od if od != reference else None


_ANCHOR_RE = re.compile(r"^CCD\s*([+-]\s*\d+)?$", re.IGNORECASE)


def anchor_offset(spec: str) -> int:
    """'CCD-7' -> -7, 'CCD+1' -> 1, 'CCD' -> 0."""
    m = _ANCHOR_RE.match((spec or "").strip())
    if not m:
        raise ValueError(f"bad anchor spec: {spec!r} (expected e.g. 'CCD-7')")
    grp = m.group(1)
    return int(grp.replace(" ", "")) if grp else 0


def anchor_date(ccd: date, spec: str) -> date:
    """The calendar date a phase with `spec` opens, given CCD."""
    return ccd + timedelta(days=anchor_offset(spec))


def today(tz=None) -> date:
    """'Now' at date granularity, in the release timezone (default DEFAULT_TZ) — NOT
    the host's, so a UTC host doesn't roll the date early. `--as-of` overrides this."""
    return now_local(tz).date()


def humanize_delta(days: int) -> str:
    """'in 3 days' / 'today' / '2 days ago' — for countdowns."""
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    if days > 0:
        return f"in {days} days"
    return f"{-days} days ago"


def ccd_viability(ccd: date, as_of: date, open_spec: str = "CCD-7") -> dict:
    """Is a CCD temporally viable, measured against the clock `as_of`?

    Reconciliation with the pipeline (pipeline_conflict) answers *which* date;
    this answers whether that date is even runnable on the calendar. `open_spec`
    is the earliest phase anchor (Phase 0 opens "CCD-7"), so the normal prep
    window is open_spec..CCD.

    Returns:
      days_to_ccd  : (ccd - as_of).days           (negative ⇒ CCD already past)
      past         : ccd < as_of                   (INVALID — can't code-complete in the past)
      phase0_open  : the CCD-7 date (when prep normally starts)
      normal_window: len of a full prep window in days (e.g. 7 for CCD-7)
      runway_days  : prep days actually left = days from max(as_of, phase0_open)..ccd (>=0)
      compressed   : not past, but as_of is already inside the CCD-7 window
                     (runway_days < normal_window) ⇒ Phase 0 is squeezed — WARN, don't block
    """
    days_to_ccd = (ccd - as_of).days
    phase0_open = anchor_date(ccd, open_spec)
    normal_window = -anchor_offset(open_spec)          # "CCD-7" -> 7
    past = days_to_ccd < 0
    start = max(as_of, phase0_open)
    runway_days = max((ccd - start).days, 0)
    compressed = (not past) and runway_days < normal_window
    return {
        "days_to_ccd": days_to_ccd,
        "past": past,
        "phase0_open": phase0_open.isoformat(),
        "normal_window": normal_window,
        "runway_days": runway_days,
        "compressed": compressed,
    }
