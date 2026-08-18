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
from datetime import date, timedelta
from typing import Optional


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


def today() -> date:
    """'Now' at date granularity. `--as-of` overrides this for a testable clock."""
    return date.today()


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
