"""Schedule math for CCD-anchored phases — pure functions, no IO.

The Code Complete Date (CCD) is the anchor the whole release hangs off of.
This module mirrors the logic of ADO pipeline 3155 "Code Complete Calendar
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
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

# The release runs on the OWNER's wall clock, not the host's. On a UTC host, a bare
# date.today() rolls to the next day at UTC-midnight (evening the day before, Pacific),
# which opened phases — and fired timed comms — hours early. So "today"/"now" are
# computed in this zone unless a caller overrides it. Needs the `tzdata` package on
# Windows (no system IANA db); falls back to host-local if the zone can't be loaded.
DEFAULT_TZ = "America/Los_Angeles"


def utc_now_iso() -> str:
    """Current UTC instant as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso_utc(value, *, default_tz=timezone.utc) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into a datetime (defaulting naive values to `default_tz`), or None."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo or default_tz is None else parsed.replace(tzinfo=default_tz)
    except (TypeError, ValueError):
        return None


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
    """The 2nd Wednesday of the month (pipeline 3155's default rule)."""
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


def default_target_month(release_id: str, ccd: Optional[str] = None) -> Optional[str]:
    """The display month ('YYYY-MM') a release is labeled for: directly matches the
    calendar month of the elected release date (or `release_id` when no date is passed).
    e.g. a release date in November 2026 ('2026-11-18' or '2026-11') -> '2026-11'."""
    d = parse_date(ccd)
    if d is not None:
        return f"{d.year:04d}-{d.month:02d}"
    if not release_id or not str(release_id).strip():
        return None
    parts = str(release_id).strip().split("-")
    if len(parts) != 2:
        return None
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if year < 1970 or not (1 <= month <= 12):
        return None
    return f"{year:04d}-{month:02d}"


def validate_custom_release_date(
    ccd_str: Optional[str],
    *,
    as_of: Optional[date] = None,
) -> Tuple[Optional[date], Optional[str]]:
    """Validate an owner-specified release date ('YYYY-MM-DD').
    Allowed: any day within the current calendar month OR any date in the future (>= as_of).
    Returns (parsed_date, error_message)."""
    d = parse_date(ccd_str)
    if d is None:
        return None, f"Bad release date '{ccd_str}' (expected YYYY-MM-DD)."
    ref = as_of or today()
    in_current_month = (d.year, d.month) == (ref.year, ref.month)
    if not in_current_month and d < ref:
        return (
            None,
            f"Release date {d.isoformat()} is in a past month relative to {ref.isoformat()}; "
            f"choose any day in {ref.strftime('%B %Y')} or any future date.",
        )
    return d, None


def target_month_id(state) -> Optional[str]:
    """The stored display month ('YYYY-MM') the release is labeled for, or the direct
    calendar month of the elected release date / release_id."""
    tm = getattr(state, "target_month", None)
    return tm or default_target_month(
        getattr(state, "release_id", "") or "",
        getattr(state, "ccd", None),
    )


def target_month_label(state, with_year: bool = True) -> str:
    """The release's display month — 'November 2026' (or 'November' when with_year=False).
    Matches the calendar month of the elected release date (or explicit target_month override)."""
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
    if not (1 <= month <= 12):
        return None
    total = year * 12 + (month - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _ccd_pretty(d: date) -> str:
    """'Wednesday, Sep 9, 2026' — built without %-d/%#d so it's platform-safe."""
    return f"{d.strftime('%A, %b')} {d.day}, {d.year}"


def preview_release(release_id: str, *, as_of: Optional[date] = None) -> Optional[dict]:
    """The pre-init identity of a release candidate (pure schedule math, no IO):
    default CCD is the 2nd Wednesday of `release_id`, and the release label directly
    matches that calendar month (e.g. a November 2026 release date -> 'November' / 'November 2026')."""
    label_month = default_target_month(release_id)
    if not label_month:
        return None
    try:
        ccd = default_ccd(release_id)
        ly, lm = parse_release_month(label_month)
    except (ValueError, IndexError):
        return None
    ref = as_of or today()
    via = ccd_viability(ccd, ref)
    month_name = calendar.month_name[lm]
    full_label = f"{month_name} {ly}"
    return {
        "release_id": release_id,
        "label_month": label_month,         # 'YYYY-MM' matching the elected release date's month
        "label": month_name,                # 'November' — calendar month name
        "ship_month": label_month,          # kept for callers reading ship_month
        "ship_label": full_label,           # 'November 2026' — full display label
        "ccd": ccd.isoformat(),
        "ccd_pretty": _ccd_pretty(ccd),     # 'Wednesday, Nov 11, 2026'
        "ccd_weekday": ccd.strftime("%A"),
        "ccd_past": via["past"],
        "days_to_ccd": via["days_to_ccd"],
        "phase0_open": via["phase0_open"],
    }


def preview_releases(
    start_release_id: Optional[str] = None,
    count: int = 4,
    *,
    as_of: Optional[date] = None,
) -> list:
    """`count` consecutive release candidates starting at `start_release_id` (default: the
    current calendar month in the release timezone). `recommended` marks the default offered
    release date: the 2nd Wednesday of the current month if not yet passed, else the 2nd
    Wednesday of the next month."""
    ref = as_of or today()
    start = start_release_id or f"{ref.year:04d}-{ref.month:02d}"
    out = []
    for i in range(max(1, count)):
        mid = month_add(start, i)
        p = preview_release(mid, as_of=ref) if mid else None
        if p:
            p["is_default"] = (i == 0)
            out.append(p)
    rec_idx = next((idx for idx, item in enumerate(out) if not item["ccd_past"]), 0)
    for idx, item in enumerate(out):
        item["recommended"] = (idx == rec_idx)
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
