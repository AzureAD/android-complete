"""`rc-report` — the Phase 2 RC-pipeline + test status report (read-only).

Assembles the release chain (checker → orchestrator → the two MRWP RC-testing runs) and
their Test-tab results into one view, on demand. Distinct from the build_verify steps
(which gate): this only REPORTS — it never blocks or changes state. `--json` emits the
raw model for the skill; otherwise a formatted text report is printed.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from steps.build_verify import _common as K


def cmd_rc_report(args):
    from tools import pipelines as P
    st = C.load_state(args.runs_root, args.release)
    month = getattr(st, "release_id", None) or args.release
    model = P.release_report(K.ORG, K.PROJECT, month,
                             checker_def=K.CHECKER_DEF, orch_def=K.ORCHESTRATOR_DEF)
    _persist(st, model, args)
    if getattr(args, "json", False):
        print(_json.dumps(model, indent=2))
        return 0
    print(_format(model))
    return 1 if model.get("problems") else 0


def _persist(st, model, args):
    """Record the resolved runs (+ snapshots) on state so status/digest/rc_report read
    them without a live call. Best-effort — a report must never fail because the state
    write did. (This is the LIVE `rc-report` diagnostic refreshing the record; the verify
    steps are the primary writers.)"""
    if st is None:
        return
    try:
        ch = model.get("checker") or {}
        if ch.get("run_id"):
            K.stash_checker(st, ch["run_id"], ch.get("when"))
        o = model.get("orchestrator") or {}
        if o.get("run_id"):
            K.stash_orchestrator(st, o["run_id"], parked=o.get("parked"))
            # Model versions are capitalized {Common,Msal,Broker}; persist to the canonical
            # (lowercase) state.versions source of truth.
            mv = o.get("versions") or {}
            st.record_versions({"common": mv.get("Common"), "msal": mv.get("Msal"),
                                "broker": mv.get("Broker")})
        mr = model.get("mrwp") or {}
        for slot in ("ECS", "Local"):
            m = mr.get(slot) or {}
            if m.get("run_id"):
                K.stash_mrwp(st, slot, {k: m.get(k) for k in
                                        ("run_id", "complete", "ran", "total", "failed_stages",
                                         "yellow_stages", "never_ran", "tests", "failed_suites")},
                             rc=model.get("rc"))
        C.save_state(st, args.runs_root, args.release)
    except Exception:
        pass


def _u(build_id):
    return K.build_url(build_id) if build_id else ""


def cmd_record_rc_report(args):
    """Record the rc_report step's outcome AFTER the skill has emailed the RC report.

    Re-reads the live model, applies the three-tier UI-automation gate (K.rc_ui_gate),
    records `pass` (>=90% UI pass — clean/warn → step done, release auto-advances into bug
    bash) or `attention` (<90% → step BLOCKS for owner investigation), and stashes the
    evaluated pipeline-run links on the step so its Details point at every artifact behind
    the verdict.

    This is the follow-up the rc_report NeedsSkill names (`payload.followup_command`), so
    the skill runs it instead of a blind `record-step --status pass`."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    try:
        model = K.rc_report_model(orch.state)
    except Exception as e:                       # pragma: no cover - defensive
        print(_json.dumps({"error": f"could not build the RC model ({e})."}))
        return 1

    gate = K.rc_ui_gate(model)
    auth = K.auth_report_gate(model)
    links = K.rc_run_links(model)
    # The consolidation decision: the release auto-advances only when BOTH the MRWP UI gate
    # AND the Authenticator-ECS gate clear. Either one holding -> the step blocks (the
    # release WAITS for human attestation). The two remain SEPARATE evaluations.
    blocking = gate["blocking"] or auth["blocking"]
    status = "attention" if blocking else "pass"
    detail = gate["detail"]
    if auth["present"]:
        detail = f"{detail}\n\n{auth['detail']}"
    orch.record_scout_step("build_verify", "rc_report", status, detail)

    # record_scout_step doesn't carry links — attach the evaluated-run refs (and stamp
    # the recorder as scout) on the resulting step, preserving its status/note.
    step = orch.state.get_step("build_verify", "rc_report")
    step.links = links
    step.by = "scout"
    orch.state.set_step("build_verify", "rc_report", step)
    C.save_state(orch.state, args.runs_root, args.release)

    C.emit(args.runs_root, args.release,
           f"[{'ok' if status == 'pass' else 'attention'}] rc_report: "
           f"{detail.splitlines()[0]}", kind="step")
    print(_json.dumps({"verdict": gate["verdict"], "auth_verdict": auth["verdict"],
                       "status": status, "blocking": blocking,
                       "pass_pct": gate["pass_pct"], "ui_total": gate["ui_total"],
                       "ui_failed": gate["ui_failed"], "threshold": gate["threshold"],
                       "detail": detail, "links": links}))
    return 0 if status == "pass" else 2


def _format(m) -> str:
    L = [f"## RC Pipeline Status — Release {m['release']}", ""]

    ch = m.get("checker") or {}
    if ch.get("fired"):
        L.append(f"✅ **Code Complete Checker** fired the release — run {ch['run_id']} ({ch['when']}).")
    elif "error" in ch:
        L.append(f"⚠ **Code Complete Checker** — couldn't read ({ch['error']}).")
    else:
        L.append("⏳ **Code Complete Checker** — no triggering run yet (before Code Complete Day, or not fired).")

    o = m.get("orchestrator") or {}
    if not o.get("found"):
        err = f" ({o['error']})" if "error" in o else ""
        L.append(f"⛔ **Release Orchestrator** — no run found{err}.")
    else:
        vstr = K.format_versions(o.get("versions"), fallback="versions n/a")
        if o.get("healthy"):
            park = "parked at 'Remove RC Tags' (awaiting owner approval)" if o.get("parked") \
                else f"'{o.get('park_stage')}' already cleared"
            L.append(f"✅ **Release Orchestrator** run {o['run_id']} healthy — pre-gate stages green, {park}. {vstr}.")
        else:
            L.append(f"⛔ **Release Orchestrator** run {o['run_id']} — stage(s) not green: "
                     f"{', '.join(o.get('failed_stages') or [])}. {vstr}.")
        L.append(f"   {_u(o.get('run_id'))}")

    for provider in ("ECS", "Local"):
        r = (m.get("mrwp") or {}).get(provider)
        if not r:
            continue
        if "error" in r:
            L.append(f"⚠ **MRWP {provider}** run {r.get('run_id')} — couldn't read stages ({r['error']}).")
            continue
        icon = "✅" if r.get("complete") else "⛔"
        verdict = "ran to completion" if r.get("complete") else "did NOT run to completion"
        extras = []
        if r.get("failed_stages"):
            extras.append(f"{len(r['failed_stages'])} red")
        if r.get("yellow_stages"):
            extras.append(f"{len(r['yellow_stages'])} yellow")
        ex = f" ({', '.join(extras)})" if extras else ""
        L.append(f"{icon} **MRWP {provider}** run {r['run_id']} — {verdict}: {r.get('ran')}/{r.get('total')} stages{ex}.")
        if not r.get("complete") and r.get("never_ran"):
            L.append(f"   never ran: {', '.join(n for n in r['never_ran'] if n)}")
        t = r.get("tests") or {}
        cats = t.get("categories") or {}
        _lbl = {"unit": "Unit", "instrumented": "Instrumented", "ui": "UI automation"}
        for cat in ("unit", "instrumented", "ui"):
            c = cats.get(cat) or {}
            if not c.get("total"):
                continue
            fr = round((c.get("failed", 0)) * 100.0 / c["total"], 1)
            gate = "  ← RC gate" if cat == "ui" else ""
            L.append(f"   {_lbl[cat]:13} {c.get('passed')}/{c.get('total')} passed · "
                     f"{c.get('failed')} failed · {fr}%{gate}")
        # Failing tests, grouped by suite (UI first), each tagged by category.
        suites = r.get("failed_suites")
        if suites:
            for s in K.sort_failed_suites(suites):
                cat = _lbl.get(s.get("category", "ui"), "UI automation")
                fr = round(s["failed"] * 100.0 / s["total"], 1) if s["total"] else 0.0
                L.append(f"   • [{cat}] {s['name']}: {s['failed']}/{s['total']} failed ({fr}%)")
                for tname in s.get("tests", []):
                    L.append(f"       - {tname}")
                shown = len(s.get("tests", []))
                if shown < s["failed"]:
                    L.append(f"       … and {s['failed'] - shown} more (see the run)")
        elif t and t.get("failed"):
            L.append(f"   (failing test names unavailable — open the run)")
        L.append(f"   {_u(r.get('run_id'))}")

    probs = m.get("problems") or []
    if probs:
        L += ["", "**Issues:**"]
        for p in probs:
            L.append(f"  - {p}")
    # Unit retry warning — failed-then-passed on retry (counted as passed).
    recovered = K.recovered_unit_tests(m)
    if recovered:
        L += ["", f"⚠ **Retry warning** — {len(recovered)} unit test(s) failed then passed "
                  f"on retry (counted as passed):"]
        for t in recovered[:20]:
            L.append(f"  - {t}")
        if len(recovered) > 20:
            L.append(f"  … and {len(recovered) - 20} more")
    return "\n".join(L)


def register(sub):
    rp = sub.add_parser("rc-report", help="Phase 2 RC-pipeline + test status report (read-only)")
    rp.add_argument("--release", required=True)
    rp.add_argument("--json", action="store_true", help="Emit the raw report model")
    rp.set_defaults(func=cmd_rc_report)

    rr = sub.add_parser(
        "record-rc-report",
        help="Record the rc_report step after emailing: apply the 90%% UI gate "
             "(pass|attention/block) + stash the evaluated run links")
    rr.add_argument("--release", required=True)
    rr.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    rr.set_defaults(func=cmd_record_rc_report)
