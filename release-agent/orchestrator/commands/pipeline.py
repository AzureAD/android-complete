"""Pipeline-write commands (real production changes to ADO pipeline 3038):
set-ccd and skip-release. Both are gated (preview → --confirm) and audited.
Also `check-ccd` (read-only) — reconcile the release CCD with the pipeline override
for the entry gate."""
from __future__ import annotations
import json as _json

from orchestrator import schedule
from orchestrator import cli_common as C
from tools import checks


def cmd_check_ccd(args):
    """Read-only CCD validation for the entry gate. Answers the complete
    "is this CCD good?" question — temporally viable AND reconciled with the
    pipeline. Classifies (priority order):

        unset      — no CCD on the release (release id isn't YYYY-MM)
        past       — CCD is before today: INVALID, can't code-complete in the past (BLOCK)
        conflict   — pipeline override is an in-month date that DIFFERS from the CCD (BLOCK)
        unreadable — couldn't read the pipeline (transient / auth) — attest fallback
        match      — CCD is future-dated and agrees with the pipeline

    Regardless of status (once a CCD is set) it also emits the calendar picture —
    days_to_ccd, phase0_open, runway_days, compressed — so the gate can WARN when
    Phase 0's normal CCD-7 window is squeezed (compressed) without blocking.

    Prints JSON. The skill records the `ccd_confirmed` gate item from this:
    match→pass (warn if compressed); past/conflict→resolve via set-ccd then re-check;
    unreadable→attest the CCD manually."""
    st = C.load_state(args.runs_root, args.release)
    out = {"release": st.release_id, "ccd": st.ccd, "ccd_source": st.ccd_source,
           "override": None, "ccd_conflict": None, "status": "unset"}
    if not st.ccd:
        if getattr(args, "json", False):
            print(_json.dumps(out))
        else:
            print("No CCD set for this release (release id isn't YYYY-MM). Use set-ccd.")
        return 0

    # Reconciliation with the pipeline (which date) — populate override/conflict first
    # so the resolver has the pipeline value on hand even when temporal wins below.
    recon = "match"
    src = C.ccd_source()
    if src.get("pipeline_id"):
        ok, val, detail = checks.read_pipeline_variable(
            src["org"], src["project"], src["pipeline_id"], src["override_variable"])
        if not ok:
            recon = "unreadable"
            out["detail"] = detail
        else:
            out["override"] = val or None
            conflict = schedule.pipeline_conflict(st.release_id, val, st.ccd)
            if conflict:
                recon = "conflict"
                out["ccd_conflict"] = conflict.isoformat()
                st.ccd_conflict = conflict.isoformat()
            else:
                st.ccd_conflict = None
            C.save_state(st, args.runs_root, args.release)

    # Temporal viability (whether that date is even runnable) — layered on top.
    as_of = C.parse_as_of(args) or schedule.today()
    ccd_d = schedule.parse_date(st.ccd)
    via = schedule.ccd_viability(ccd_d, as_of) if ccd_d else {}
    out.update({"today": as_of.isoformat(), **{k: via.get(k) for k in
                ("days_to_ccd", "phase0_open", "runway_days", "compressed")}})
    # `past` is the worst problem — it overrides the reconciliation status (a past CCD
    # must be rescheduled regardless of whether it matches the pipeline). override/
    # ccd_conflict stay populated so the resolver can offer a future override to adopt.
    out["status"] = "past" if via.get("past") else recon

    if getattr(args, "json", False):
        print(_json.dumps(out))
        return 0
    when = schedule.humanize_delta(via.get("days_to_ccd", 0)) if via else ""
    warn = (f"  ⚠ Phase 0 is compressed: only {via.get('runway_days')} of the normal "
            f"{via.get('normal_window', 7)} prep days remain." if via.get("compressed") else "")
    msg = {
        "match": f"CCD {st.ccd} ({when}) is future-dated and reconciled with the pipeline.{warn}",
        "compressed": "",  # folded into match/others via `warn`
        "past": f"⛔ CCD {st.ccd} is in the PAST ({when}) — reschedule to a future date "
                f"with set-ccd before proceeding.",
        "conflict": f"⚠ Pipeline override {out['ccd_conflict']} DIFFERS from CCD {st.ccd} "
                    f"({when}) — reconcile with set-ccd before proceeding.{warn}",
        "unreadable": f"Couldn't read the pipeline override to validate CCD {st.ccd} "
                      f"({out.get('detail','')}). Confirm the CCD manually.{warn}",
        "unset": "No CCD set.",
    }[out["status"]]
    print(msg)
    return 0


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
    cc = sub.add_parser("check-ccd", help="Validate the release CCD — past/reconciled/compressed (read-only; for the entry gate)")
    cc.add_argument("--release", required=True)
    cc.add_argument("--as-of", dest="as_of", default="", help="Simulated clock YYYY-MM-DD (default: today)")
    cc.add_argument("--json", action="store_true", help="Emit {ccd,override,ccd_conflict,status,days_to_ccd,runway_days,compressed,...}")
    cc.set_defaults(func=cmd_check_ccd)

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
