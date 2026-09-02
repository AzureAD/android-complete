"""Release lifecycle + manual overrides: init, list, status, next, approve, deny,
done, activate, skip, reopen, halt, resume."""
from __future__ import annotations
import json as _json
import os

from orchestrator.state import ReleaseState
from orchestrator.engine import Orchestrator
from orchestrator import discovery, render, schedule
from orchestrator import cli_common as C
from tools import checks


def cmd_init(args):
    sp = C.state_path(args.runs_root, args.release)
    if os.path.exists(sp) and not args.force:
        print(f"Release {args.release} already exists at {sp} (use --force to recreate).")
        return 1
    st = ReleaseState(release_id=args.release, status="not_started")

    # Release owner (the engineer running this release) — release metadata.
    # Priority: explicit --owner-email (skill can pass the richer profile) then
    # the signed-in az user. Never hardcoded.
    st.owner_email = (getattr(args, "owner_email", None) or checks.current_az_user())
    st.owner_name = getattr(args, "owner_name", None) or None
    owner_note = "" if st.owner_email else "  (couldn't resolve owner — set with set-owner)"

    # Capture the owner's timezone NOW (at init, on their interactive machine) and
    # persist it — so later headless automation runs, which may execute in a UTC process
    # context, still evaluate due-ness + fire_at_local on the OWNER's clock. Priority:
    # explicit --timezone, else auto-detect the local IANA zone; falls back to the
    # config/schedule.yaml default when neither is available.
    st.timezone = (getattr(args, "timezone", None) or schedule.detect_local_tz())
    tz_note = "" if st.timezone else "  (couldn't detect — using default)"

    # CCD is canonically the 2nd Wednesday. We still READ the pipeline override,
    # but we do NOT silently adopt it — if it differs, we flag a conflict for the
    # user to resolve (2nd-Wed default vs the pipeline date).
    src = C.ccd_source()
    override, note = None, ""
    if src.get("pipeline_id"):
        ok, val, detail = checks.read_pipeline_variable(
            src["org"], src["project"], src["pipeline_id"], src["override_variable"])
        if ok:
            override = val
        else:
            note = f"  (couldn't read pipeline override: {detail})"
    try:
        default = schedule.default_ccd(args.release)
        st.ccd = default.isoformat()
        st.ccd_source = "default"
        conflict = schedule.pipeline_conflict(args.release, override, st.ccd)
        st.ccd_conflict = conflict.isoformat() if conflict else None
    except (ValueError, IndexError):
        default, conflict = None, None
        note = "  (release id isn't YYYY-MM — CCD not set; use set-ccd)"
    # The month the release is NAMED for (ship month) = CCD month + 1 by default. Stored so the
    # docs/comms never misname the release; the owner confirms/adjusts it at init (set-target-month).
    st.target_month = schedule.default_target_month(args.release)
    st.save(sp)

    C.elog(args.runs_root, args.release).log(
        "release_started", forced=bool(args.force),
        ccd=st.ccd, ccd_source=st.ccd_source, ccd_conflict=st.ccd_conflict,
        owner=st.owner_email, timezone=st.timezone)
    owner_line = f"  Owner: {st.owner_name + ' ' if st.owner_name else ''}{st.owner_email or '(unresolved)'}{owner_note}"
    tz_line = f"  Timezone: {st.timezone or schedule.DEFAULT_TZ}{tz_note}"
    if st.ccd:
        opens = schedule.anchor_date(default, "CCD-7").isoformat()
        tm_label = schedule.target_month_label(st)
        lines = [f"Initialized release {args.release}.", f"  state: {sp}", owner_line, tz_line,
                 f"  Code Complete Date: {st.ccd} (2nd Wednesday){note}",
                 f"  Release name: {tm_label or '(unresolved)'} release "
                 f"(ships the month after code-complete — confirm/adjust with set-target-month)",
                 f"  Phase 0 (Pre-flight) opens {opens} (CCD-7). Until then nothing fires."]
        if conflict:
            lines.append(f"  ⚠ Pipeline override is {conflict.isoformat()}, which differs from the "
                         f"2nd-Wednesday default. Confirm which is the real CCD before proceeding.")
        print("\n".join(lines))
    else:
        print(f"Initialized release {args.release}.\n  state: {sp}\n{owner_line}\n{tz_line}\n{note}")
    return 0


def cmd_list(args):
    """List discovered releases (none/one/many). --json for the skill."""
    res = discovery.resolve(args.runs_root, getattr(args, "release", None))
    if args.json:
        print(_json.dumps(res, indent=2))
        return 0
    all_ = res["all"]
    if res["resolution"] == "none":
        if getattr(args, "release", None):
            print(f"No release '{args.release}' found. Start one with:  init --release {args.release}")
        else:
            print("No active release on this machine. Start one with:  init --release <YYYY-MM>")
        return 0
    print(f"Found {len(all_)} release(s):")
    for r in all_:
        mark = "->" if r is res["release"] else "  "
        print(f"  {mark} {r['release_id']}  [{r['status']}]  updated {r['updated_at']}")
    if res["resolution"] == "ambiguous":
        print(f"\nMultiple releases found — assuming most recent: {res['release']['release_id']} "
              f"(confirm before acting).")
    return 0


def cmd_status(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    if not getattr(args, "no_pipeline_check", False) and C.refresh_conflict(st):
        C.save_state(st, args.runs_root, args.release)
    if getattr(args, "json", False):
        print(_json.dumps(orch.status_report(), indent=2))
        return 0
    rep = orch.status_report()
    C.emit(args.runs_root, args.release, render.status_view(rep), kind="status",
           log_text=(f"status viewed — {rep['release_id']} {rep['done']}/{rep['total']} "
                     f"({rep['percent']}%), phase: {rep.get('current_phase_name','')}"))
    return 0


def cmd_next(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    actions = orch.run_until_gate()
    C.save_state(st, args.runs_root, args.release)   # persist BEFORE any display
    C.log_actions(C.elog(args.runs_root, args.release), actions, state=st)
    if getattr(args, "json", False):
        # Advance, then emit the same report as `status --json` (carries scout_pending) so a
        # caller can advance + read the pending scout steps in ONE call.
        print(_json.dumps(orch.status_report(), indent=2))
        return 0
    C.emit(args.runs_root, args.release, C.advance_block(actions, orch), kind="advance",
           log_text=C.advance_log_summary(actions))
    return 0


def cmd_approve(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    gate_phase, gate_step = st.current_phase, st.current_step
    act = orch.approve_gate(args.comment or "")
    el = C.elog(args.runs_root, args.release)
    if act.kind != "idle":
        el.log("gate_approved", phase=gate_phase, step=gate_step, driver=args.comment or None)
    actions = orch.run_until_gate()
    C.save_state(st, args.runs_root, args.release)   # persist BEFORE any display
    C.log_actions(el, actions, state=st)
    C.emit(args.runs_root, args.release,
           C.advance_block(actions, orch, lead=[f"  {act.message}"]), kind="advance",
           log_text=C.advance_log_summary(actions, lead=[act.message]))
    return 0


def cmd_deny(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    gate_phase, gate_step = st.current_phase, st.current_step
    act = orch.deny_gate(args.comment or "")
    if act.kind != "idle":
        C.elog(args.runs_root, args.release).log(
            "gate_denied", phase=gate_phase, step=gate_step, driver=args.comment or None)
    C.save_state(st, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"  {act.message}\n\n" + render.status_view(orch.status_report()), kind="deny",
           log_text=act.message)
    return 0


def cmd_done(args):
    """Mark a reminder (human, non-gate) step done, then advance to the next hold."""
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    act = orch.complete_step(getattr(args, "phase", None), getattr(args, "step", None), args.note or "")
    if act.kind == "idle":
        print(act.message)
        return 1
    el = C.elog(args.runs_root, args.release)
    el.log("reminder_done", phase=act.phase, step=act.step, driver=args.note or None)
    actions = orch.run_until_gate()
    C.save_state(st, args.runs_root, args.release)
    C.log_actions(el, actions, state=st)
    C.emit(args.runs_root, args.release,
           C.advance_block(actions, orch, lead=[f"  {act.message}"]), kind="advance",
           log_text=C.advance_log_summary(actions, lead=[act.message]))
    return 0


def cmd_skip(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    act = orch.skip_step(args.phase, args.step, args.reason or "")
    if act.kind == "idle":            # rejected (no reason / bad step) — nothing changed
        print(act.message)
        return 1
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log("step_skipped", phase=args.phase, step=args.step, driver=args.reason)
    C.emit(args.runs_root, args.release, act.message, kind="override")
    return 0


def cmd_reopen(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    act = orch.reopen_step(args.phase, args.step, args.reason or "")
    if act.kind == "idle":
        print(act.message)
        return 1
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log("step_reopened", phase=args.phase, step=args.step, driver=args.reason or None)
    C.emit(args.runs_root, args.release, act.message, kind="override")
    return 0


# The Phase-2 RC-testing steps a re-triggered RC invalidates: the two MRWP verifications
# and the terminal RC report/gate. checker_fired / orchestrator_health are NOT reopened —
# a re-triggered RC re-runs MRWP against the same orchestrator run.
_RC_RETRIGGER_STEPS = ("mrwp_ecs", "mrwp_local", "rc_report")


def cmd_rc_retriggered(args):
    """The human explicitly signals that a NEW RC has been triggered (a flaky-run
    re-trigger, or the orchestrator re-running RC testing after a broker cherry-pick).

    Reopens the Phase-2 RC-testing steps so the engine re-resolves the NEWEST MRWP run
    (mrwp_run_ids already picks the highest id) and re-applies the gate. Scout's poller /
    next then holds while the new run is in-flight and re-evaluates on completion — so an
    early poll can't mark an in-progress RC as a false failure."""
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    reason = (args.reason or "RC re-triggered").strip()
    reopened = []
    for sid in _RC_RETRIGGER_STEPS:
        act = orch.reopen_step("build_verify", sid, reason)
        if act.kind != "idle":
            reopened.append(sid)
        # a reopened step is no longer an owner action / block
        key = f"build_verify.{sid}"
        st.pending_human = [p for p in st.pending_human if p != key]
    if not reopened:
        print("No Phase-2 RC steps found to reopen (is this release in Build & RC "
              "Verification?).")
        return 1
    if st.status in ("awaiting_action", "holding_gate", "complete", "halted"):
        st.status = "running"
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "rc_retriggered", driver=reason, steps=",".join(reopened))
    msg = (f"RC re-trigger acknowledged — reopened {', '.join(reopened)}. Scout will "
           f"re-resolve the newest RC and re-apply the gate; it holds (no action needed) "
           f"while the run is still in-flight and polls every 30 min. Reason: {reason}")
    C.emit(args.runs_root, args.release, msg, kind="override")
    print(msg)
    return 0


def cmd_halt(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    act = orch.halt(args.reason or "")
    if act.kind == "idle":
        print(act.message)
        return 1
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log("release_halted", driver=args.reason)
    C.emit(args.runs_root, args.release, act.message, kind="override")
    return 0


def cmd_resume(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    act = orch.resume(args.reason or "")
    if not getattr(args, "no_pipeline_check", False):
        C.refresh_conflict(st)
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log("release_resumed", driver=args.reason or None)
    tail = ""
    if st.ccd_conflict:
        tail = (f"\n  ⚠ Pipeline override {st.ccd_conflict} differs from CCD {st.ccd} — "
                f"confirm which is correct (see status).")
    C.emit(args.runs_root, args.release, (act.message + tail), kind="override")
    return 0


def cmd_activate(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    orch.activate_conditional(args.phase)
    C.save_state(st, args.runs_root, args.release)
    print(f"Activated conditional phase: {args.phase}")
    return 0


def cmd_set_target_month(args):
    """Set the ship/display month the release is NAMED for ('YYYY-MM'). Display-only — it does
    NOT touch release_id, the CCD, branches, or scheduling. With no --month, resets to the
    CCD-month+1 default."""
    st = C.load_state(args.runs_root, args.release)
    if args.month:
        import re as _re
        if not _re.match(r"^\d{4}-\d{2}$", str(args.month).strip()):
            print(f"Bad --month '{args.month}' (expected YYYY-MM).")
            return 1
        mm = int(str(args.month).split("-")[1])
        if not (1 <= mm <= 12):
            print(f"Bad --month '{args.month}' (month must be 01-12).")
            return 1
        st.target_month = str(args.month).strip()
    else:
        st.target_month = schedule.default_target_month(args.release)
    C.save_state(st, args.runs_root, args.release)
    print(f"Release {args.release} is the {schedule.target_month_label(st)} release "
          f"(target_month={st.target_month}).")
    return 0


def register(sub):
    i = sub.add_parser("init", help="Start a new release run")
    i.add_argument("--release", required=True)
    i.add_argument("--force", action="store_true")
    i.add_argument("--owner-email", default=None, help="Release owner email (default: signed-in az user)")
    i.add_argument("--owner-name", default=None, help="Release owner display name (optional)")
    i.add_argument("--timezone", default=None,
                   help="Owner IANA timezone (default: auto-detected from this machine, e.g. America/Los_Angeles)")
    i.set_defaults(func=cmd_init)

    l = sub.add_parser("list", help="Discover releases (none/one/many)")
    l.add_argument("--release", required=False, default=None)
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("status", help="Show the run-state brief")
    s.add_argument("--release", required=True)
    s.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-pipeline-check", action="store_true",
                   help="Skip re-reading the pipeline to detect CCD drift (faster/offline).")
    s.set_defaults(func=cmd_status)

    n = sub.add_parser("next", help="Advance until the next gate / completion")
    n.add_argument("--release", required=True)
    n.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    n.add_argument("--json", action="store_true",
                   help="After advancing, print the status report as JSON (carries scout_pending).")
    n.set_defaults(func=cmd_next)

    a = sub.add_parser("approve", help="Approve the current holding gate, continue")
    a.add_argument("--release", required=True)
    a.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    a.add_argument("--comment", default="")
    a.set_defaults(func=cmd_approve)

    d = sub.add_parser("deny", help="Deny the current holding gate")
    d.add_argument("--release", required=True)
    d.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    d.add_argument("--comment", default="")
    d.set_defaults(func=cmd_deny)

    dn = sub.add_parser("done", help="Mark a reminder (human, non-gate) step done, then advance")
    dn.add_argument("--release", required=True)
    dn.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    dn.add_argument("--phase", default=None, help="Defaults to the current holding step")
    dn.add_argument("--step", default=None)
    dn.add_argument("--note", default="", help="Optional note (audited)")
    dn.set_defaults(func=cmd_done)

    # ---- manual overrides ----
    sk = sub.add_parser("skip", help="Skip a step without running it (reason REQUIRED)")
    sk.add_argument("--release", required=True)
    sk.add_argument("--phase", required=True)
    sk.add_argument("--step", required=True)
    sk.add_argument("--reason", required=True, help="Why (audit — required)")
    sk.set_defaults(func=cmd_skip)

    ro = sub.add_parser("reopen", help="Reopen a done/skipped step so it runs again")
    ro.add_argument("--release", required=True)
    ro.add_argument("--phase", required=True)
    ro.add_argument("--step", required=True)
    ro.add_argument("--reason", default="", help="Why (optional)")
    ro.set_defaults(func=cmd_reopen)

    rt = sub.add_parser("rc-retriggered",
                        help="Signal a NEW RC was triggered — reopens the Phase-2 RC steps "
                             "so Scout re-evaluates the newest RC (holds while in-flight)")
    rt.add_argument("--release", required=True)
    rt.add_argument("--reason", default="",
                    help="Why it was re-triggered (e.g. 'flaky broker suite re-run' or "
                         "'broker cherry-pick #123') — recorded for audit")
    rt.set_defaults(func=cmd_rc_retriggered)

    ht = sub.add_parser("halt", help="Emergency hold — nothing advances until resume (reason REQUIRED)")
    ht.add_argument("--release", required=True)
    ht.add_argument("--reason", required=True, help="Why (audit — required)")
    ht.set_defaults(func=cmd_halt)

    rs = sub.add_parser("resume", help="Clear an emergency halt")
    rs.add_argument("--release", required=True)
    rs.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    rs.add_argument("--no-pipeline-check", action="store_true", help="Skip CCD-drift check")
    rs.add_argument("--reason", default="", help="Why (optional)")
    rs.set_defaults(func=cmd_resume)

    ac = sub.add_parser("activate", help="Turn on a conditional phase (e.g. hotfix)")
    ac.add_argument("--release", required=True)
    ac.add_argument("--phase", required=True)
    ac.set_defaults(func=cmd_activate)

    tm = sub.add_parser("set-target-month",
                        help="Set the ship/display month the release is named for (YYYY-MM; "
                             "display-only). Omit --month to reset to the CCD-month+1 default.")
    tm.add_argument("--release", required=True)
    tm.add_argument("--month", default=None, help="Ship month as YYYY-MM (e.g. 2026-09)")
    tm.set_defaults(func=cmd_set_target_month)
