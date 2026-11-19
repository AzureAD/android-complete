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
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional, Tuple

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


# --------------------------------------------------- US holidays & business time

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The date of the n-th `weekday` (Mon=0..Sun=6) of `month` in `year` (n>=1)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The date of the LAST `weekday` of `month`."""
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """Federal-holiday observation: Sat -> Fri, Sun -> Mon."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_holidays(year: int) -> set[date]:
    """Observed US federal holiday dates for `year`."""
    return {
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


def is_holiday(d: date) -> bool:
    return d in us_holidays(d.year)


def is_business_day(d: date) -> bool:
    """True if `d` is Mon-Fri and not an observed US federal holiday."""
    return d.weekday() < 5 and not is_holiday(d)


def next_business_day(d: date) -> date:
    """Return `d` if it is a business day, else the next business day after `d`."""
    cur = d
    while not is_business_day(cur):
        cur += timedelta(days=1)
    return cur


def previous_business_day(d: date) -> date:
    """Return the most recent business day strictly before `d`."""
    cur = d - timedelta(days=1)
    while not is_business_day(cur):
        cur -= timedelta(days=1)
    return cur


def business_days_until(start: date, target: date) -> int:
    """Count business days strictly after `start` up to and including `target`
    (negative when `target < start`, 0 when `target == start`)."""
    if target == start:
        return 0
    if target > start:
        cur = start + timedelta(days=1)
        count = 0
        while cur <= target:
            if is_business_day(cur):
                count += 1
            cur += timedelta(days=1)
        return count
    return -business_days_until(target, start)


def business_hours_between(
    start_dt: datetime,
    end_dt: datetime,
    *,
    tz=None,
    day_start_hour: int = 7,
    day_end_hour: int = 19,
) -> float:
    """Elapsed business hours between `start_dt` and `end_dt` across owner-local
    business days within `[day_start_hour:00, day_end_hour:00)`."""
    zone = tz if tz is not None else (end_dt.tzinfo or start_dt.tzinfo or get_tz())
    s = start_dt.astimezone(zone) if (zone and start_dt.tzinfo) else start_dt
    e = end_dt.astimezone(zone) if (zone and end_dt.tzinfo) else end_dt
    if e <= s:
        return 0.0
    total_seconds = 0.0
    cur_date = s.date()
    end_date = e.date()
    while cur_date <= end_date:
        if is_business_day(cur_date):
            win_start = datetime.combine(cur_date, time(day_start_hour, 0), s.tzinfo)
            win_end = datetime.combine(cur_date, time(day_end_hour, 0), s.tzinfo)
            seg_start = max(s, win_start)
            seg_end = min(e, win_end)
            if seg_end > seg_start:
                total_seconds += (seg_end - seg_start).total_seconds()
        cur_date += timedelta(days=1)
    return round(total_seconds / 3600.0, 2)


# --------------------------------------------------- declarative `available` rules

_NOT_BEFORE_RE = re.compile(
    r"^CCD\s*([+-]\s*\d+)?(?:\s+((?:[01]\d|2[0-3]):[0-5]\d))?$",
    re.IGNORECASE,
)
_HOURS_WINDOW_RE = re.compile(
    r"^((?:[01]\d|2[0-3]):[0-5]\d)-((?:[01]\d|2[0-3]):[0-5]\d)$"
)
_WEEKDAY_BY_LOWER = {name.lower(): name for name in calendar.day_name}


@dataclass(frozen=True)
class AvailableRule:
    """Declarative execution-eligibility window for a phase or step."""

    not_before: Optional[str] = None
    weekdays_only: bool = False
    forbid_weekdays: tuple[str, ...] = ()
    hours_local: Optional[str] = None

    @property
    def anchor_spec(self) -> Optional[str]:
        if not self.not_before:
            return None
        return self.not_before.split(" ", 1)[0]

    @property
    def not_before_time(self) -> Optional[str]:
        if not self.not_before or " " not in self.not_before:
            return None
        return self.not_before.split(" ", 1)[1]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.not_before is not None:
            out["not_before"] = self.not_before
        if self.weekdays_only:
            out["weekdays_only"] = True
        if self.forbid_weekdays:
            out["forbid_weekdays"] = list(self.forbid_weekdays)
        if self.hours_local is not None:
            out["hours_local"] = self.hours_local
        return out


@dataclass(frozen=True)
class AvailabilityStatus:
    """Evaluated availability of a phase or step at a specific owner-local instant."""

    available: bool
    opens_date: Optional[date] = None
    available_at: Optional[str] = None
    reason: Optional[str] = None


def _normalize_not_before(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} not_before must be a non-empty string.")
    m = _NOT_BEFORE_RE.match(value.strip())
    if not m:
        raise ValueError(
            f"{context} has invalid availability spec {value!r} "
            "(expected e.g. 'CCD-7', 'CCD', 'CCD+1', or 'CCD+1 09:00')."
        )
    offset = m.group(1).replace(" ", "") if m.group(1) else ""
    hm = m.group(2)
    base = f"CCD{offset}"
    return f"{base} {hm}" if hm else base


def parse_available_rule(raw: Any, *, context: str = "available") -> Optional[AvailableRule]:
    """Validate and normalize a phase/step `available` specification."""
    if raw is None:
        return None
    if isinstance(raw, AvailableRule):
        return None if raw.to_dict() == {} else raw
    if isinstance(raw, str):
        return AvailableRule(not_before=_normalize_not_before(raw, context))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{context} must be a string or mapping.")
    allowed = {"not_before", "weekdays_only", "forbid_weekdays", "hours_local"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            f"Unknown {context} field(s): {', '.join(sorted(str(k) for k in unknown))}"
        )
    not_before = (
        _normalize_not_before(raw["not_before"], context)
        if raw.get("not_before") is not None
        else None
    )
    weekdays_only = raw.get("weekdays_only", False)
    if type(weekdays_only) is not bool:
        raise ValueError(f"{context}.weekdays_only must be a boolean.")
    raw_forbid = raw.get("forbid_weekdays", ())
    if not isinstance(raw_forbid, (list, tuple)):
        raise ValueError(f"{context}.forbid_weekdays must be a list of weekday names.")
    forbid: list[str] = []
    for item in raw_forbid:
        if not isinstance(item, str) or item.strip().lower() not in _WEEKDAY_BY_LOWER:
            raise ValueError(
                f"{context}.forbid_weekdays contains invalid weekday {item!r}."
            )
        canon = _WEEKDAY_BY_LOWER[item.strip().lower()]
        if canon not in forbid:
            forbid.append(canon)
    if set(forbid) >= set(calendar.day_name) or (
        weekdays_only and set(forbid) >= set(calendar.day_name[:5])
    ):
        raise ValueError(f"{context}.forbid_weekdays leaves no eligible days.")
    hours_local = raw.get("hours_local")
    if hours_local is not None:
        if not isinstance(hours_local, str) or not _HOURS_WINDOW_RE.match(hours_local.strip()):
            raise ValueError(
                f"{context}.hours_local must match 'HH:MM-HH:MM' (got {hours_local!r})."
            )
        hours_local = hours_local.strip()
        s_txt, e_txt = hours_local.split("-", 1)
        sh, sm = (int(v) for v in s_txt.split(":"))
        eh, em = (int(v) for v in e_txt.split(":"))
        if time(sh, sm) >= time(eh, em):
            raise ValueError(
                f"{context}.hours_local start must be earlier than end ({hours_local!r})."
            )
    rule = AvailableRule(
        not_before=not_before,
        weekdays_only=weekdays_only,
        forbid_weekdays=tuple(forbid),
        hours_local=hours_local,
    )
    return None if rule.to_dict() == {} else rule


def _parse_hm(hm: str) -> time:
    h, m = (int(v) for v in hm.split(":"))
    return time(h, m)


def _next_permitted_day(
    start_date: date,
    *,
    weekdays_only: bool,
    forbid_weekdays: tuple[str, ...],
) -> date:
    cur = start_date
    for _ in range(370):
        if weekdays_only and not is_business_day(cur):
            cur += timedelta(days=1)
            continue
        if calendar.day_name[cur.weekday()] in forbid_weekdays:
            cur += timedelta(days=1)
            continue
        return cur
    return cur


def evaluate_availability(
    rule: Optional[AvailableRule],
    ccd: Optional[date],
    *,
    as_of: date,
    now_local: datetime,
    fire_at_local: Optional[str] = None,
    anchor_fallback: Optional[date] = None,
) -> AvailabilityStatus:
    """Evaluate a declarative `AvailableRule` (plus optional `fire_at_local`) on the owner's clock."""
    opens_date: Optional[date] = None
    if rule and rule.anchor_spec and ccd is not None:
        opens_date = anchor_date(ccd, rule.anchor_spec)
    elif anchor_fallback is not None:
        opens_date = anchor_fallback

    nb_time = rule.not_before_time if rule else None
    effective_gate_time = nb_time or fire_at_local
    if opens_date is not None:
        if as_of < opens_date:
            at = f"{opens_date.isoformat()}T{effective_gate_time}" if effective_gate_time else opens_date.isoformat()
            return AvailabilityStatus(False, opens_date, at, f"Not available before {at}")
        if as_of == opens_date and nb_time and now_local.time() < _parse_hm(nb_time):
            at = f"{opens_date.isoformat()}T{nb_time}"
            return AvailabilityStatus(False, opens_date, at, f"Not available before {at}")

    if fire_at_local:
        ft = _parse_hm(fire_at_local)
        if opens_date is not None:
            if as_of == opens_date and now_local.time() < ft:
                at = f"{opens_date.isoformat()}T{fire_at_local}"
                return AvailabilityStatus(False, opens_date, at, f"Not available before {at}")
        elif now_local.time() < ft:
            at = f"{as_of.isoformat()}T{fire_at_local}"
            return AvailabilityStatus(False, opens_date, at, f"Not available before {at}")

    if rule is None:
        return AvailabilityStatus(True, opens_date, None, None)

    if rule.weekdays_only and not is_business_day(as_of):
        nxt = _next_permitted_day(
            as_of + timedelta(days=1),
            weekdays_only=rule.weekdays_only,
            forbid_weekdays=rule.forbid_weekdays,
        )
        start_s = rule.hours_local.split("-", 1)[0] if rule.hours_local else None
        at = f"{nxt.isoformat()}T{start_s}" if start_s else nxt.isoformat()
        return AvailabilityStatus(
            False, opens_date, at, f"Available on business days only (next: {at})"
        )

    day_name = calendar.day_name[as_of.weekday()]
    if day_name in rule.forbid_weekdays:
        nxt = _next_permitted_day(
            as_of + timedelta(days=1),
            weekdays_only=rule.weekdays_only,
            forbid_weekdays=rule.forbid_weekdays,
        )
        start_s = rule.hours_local.split("-", 1)[0] if rule.hours_local else None
        at = f"{nxt.isoformat()}T{start_s}" if start_s else nxt.isoformat()
        return AvailabilityStatus(
            False, opens_date, at, f"Not available on {day_name} (next: {at})"
        )

    if rule.hours_local:
        s_txt, e_txt = rule.hours_local.split("-", 1)
        st, et = _parse_hm(s_txt), _parse_hm(e_txt)
        now_t = now_local.time()
        if now_t < st:
            at = f"{as_of.isoformat()}T{s_txt}"
            return AvailabilityStatus(
                False, opens_date, at, f"Outside local window {rule.hours_local} (opens {at})"
            )
        if now_t > et:
            nxt = _next_permitted_day(
                as_of + timedelta(days=1),
                weekdays_only=rule.weekdays_only,
                forbid_weekdays=rule.forbid_weekdays,
            )
            at = f"{nxt.isoformat()}T{s_txt}"
            return AvailabilityStatus(
                False, opens_date, at, f"Outside local window {rule.hours_local} (next: {at})"
            )

    return AvailabilityStatus(True, opens_date, None, None)
