"""Pipeline-write commands (real production changes to ADO pipeline 3038):
set-ccd and skip-release. Both are gated (preview → --confirm) and audited."""
from __future__ import annotations

from orchestrator import schedule
from orchestrator import cli_common as C
from tools import checks


def cmd_set_ccd(args):
    """Change the Code Complete Date. Writes the pipeline override (real change) —
    requires --confirm and a --reason. Without --confirm, previews the write."""
    st = C.load_state(args.runs_root, args.release)
    src = C.ccd_source()
    if not src.get("pipeline_id"):
        print("No CCD source configured (config/schedule.yaml).")
        return 1
    if not (args.reason and args.reason.strip()):
        print("A --reason is required (audited).")
        return 1

    if args.default:
        new_ccd, source, value = schedule.default_ccd(args.release), "default", ""
        what = f"clear the override → default {new_ccd.isoformat()} (2nd Wednesday)"
    else:
        d = schedule.parse_date(args.date)
        if not d:
            print(f"Bad --date '{args.date}' (expected YYYY-MM-DD).")
            return 1
        ry, rm = schedule.parse_release_month(args.release)
        if (d.year, d.month) != (ry, rm):
            print(f"CCD {d.isoformat()} is not in release month {args.release}. The pipeline "
                  f"override is month-scoped, so a different month wouldn't apply. "
                  f"Use the release id for that month instead.")
            return 1
        new_ccd, source, value = d, "manual", d.isoformat()
        what = f"set CCD override → {value}"

    if not args.confirm:
        print(f"[preview] Would {what} on pipeline {src['pipeline_id']} "
              f"({src['override_variable']}).\n  Re-run with --confirm to write it. Reason: {args.reason.strip()}")
        return 0

    res = C.write_ccd_var(src, value)
    if not res.ok:
        print(f"Failed to write pipeline variable: {res.detail}")
        return 1
    st.ccd = new_ccd.isoformat()
    st.ccd_source = source
    st.ccd_conflict = None            # the date is now settled — clear any conflict
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "ccd_changed", value=value or "(default)", source=source, driver=args.reason.strip())
    opens = schedule.anchor_date(new_ccd, "CCD-7").isoformat()
    C.emit(args.runs_root, args.release,
           f"✅ CCD set to **{st.ccd}** ({source}); pipeline updated. "
           f"Phase 0 opens {opens} (CCD-7).\n  {res.detail}", kind="ccd")
    return 0


def cmd_skip_release(args):
    """Suppress the release by setting the pipeline 'skipRelease' switch (real change)."""
    st = C.load_state(args.runs_root, args.release)
    src = C.ccd_source()
    if not (args.reason and args.reason.strip()):
        print("A --reason is required (audited).")
        return 1
    clearing = bool(getattr(args, "clear", False))
    value = "" if clearing else "skipped"
    verb = "clear" if clearing else "set"
    if not args.confirm:
        print(f"[preview] Would {verb} '{src.get('skip_variable')}' on pipeline "
              f"{src.get('pipeline_id')}.\n  Re-run with --confirm. Reason: {args.reason.strip()}")
        return 0
    res = checks.set_pipeline_variable(
        src["org"], src["project"], src["pipeline_id"], src["skip_variable"], value)
    if not res.ok:
        print(f"Failed to write pipeline variable: {res.detail}")
        return 1
    st.skip_release = not clearing
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "release_skip_cleared" if clearing else "release_skip_set", driver=args.reason.strip())
    msg = ("✅ Release un-skipped — pipeline will trigger normally."
           if clearing else
           "🛑 Release marked SKIP in the pipeline — the monthly trigger is suppressed until cleared.")
    C.emit(args.runs_root, args.release, msg + f"\n  {res.detail}", kind="skip_release")
    return 0


def register(sub):
    sc = sub.add_parser("set-ccd", help="Change the Code Complete Date (writes pipeline override; --confirm)")
    sc.add_argument("--release", required=True)
    sc.add_argument("--date", default="", help="New CCD (YYYY-MM-DD), must be in the release month")
    sc.add_argument("--default", action="store_true", help="Clear the override → 2nd-Wednesday default")
    sc.add_argument("--reason", default="", help="Why (audited — required)")
    sc.add_argument("--confirm", action="store_true", help="Actually write to the pipeline (else preview)")
    sc.set_defaults(func=cmd_set_ccd)

    sr = sub.add_parser("skip-release", help="Suppress/cancel the release via the pipeline switch (--confirm)")
    sr.add_argument("--release", required=True)
    sr.add_argument("--clear", action="store_true", help="Clear the skip (re-enable the release)")
    sr.add_argument("--reason", default="", help="Why (audited — required)")
    sr.add_argument("--confirm", action="store_true", help="Actually write to the pipeline (else preview)")
    sr.set_defaults(func=cmd_skip_release)
