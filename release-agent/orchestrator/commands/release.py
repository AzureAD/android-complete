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
    st.save(sp)

    C.elog(args.runs_root, args.release).log(
        "release_started", forced=bool(args.force),
        ccd=st.ccd, ccd_source=st.ccd_source, ccd_conflict=st.ccd_conflict, owner=st.owner_email)
    owner_line = f"  Owner: {st.owner_name + ' ' if st.owner_name else ''}{st.owner_email or '(unresolved)'}{owner_note}"
    if st.ccd:
        opens = schedule.anchor_date(default, "CCD-7").isoformat()
        lines = [f"Initialized release {args.release}.", f"  state: {sp}", owner_line,
                 f"  Code Complete Date: {st.ccd} (2nd Wednesday){note}",
                 f"  Phase 0 (Pre-flight) opens {opens} (CCD-7). Until then nothing fires."]
        if conflict:
            lines.append(f"  ⚠ Pipeline override is {conflict.isoformat()}, which differs from the "
                         f"2nd-Wednesday default. Confirm which is the real CCD before proceeding.")
        print("\n".join(lines))
    else:
        print(f"Initialized release {args.release}.\n  state: {sp}\n{owner_line}\n{note}")
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


def register(sub):
    i = sub.add_parser("init", help="Start a new release run")
    i.add_argument("--release", required=True)
    i.add_argument("--force", action="store_true")
    i.add_argument("--owner-email", default=None, help="Release owner email (default: signed-in az user)")
    i.add_argument("--owner-name", default=None, help="Release owner display name (optional)")
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
