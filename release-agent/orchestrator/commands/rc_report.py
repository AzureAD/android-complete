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
from steps.build_verify import rc_report as R, _rc_report_rendering as rendering
from tools import pipelines as P


def cmd_rc_report(args):
    st = C.load_state(args.runs_root, args.release)
    month = getattr(st, "release_id", None) or args.release
    model = P.release_report(K.ORG, K.PROJECT, month,
                             checker_def=P.CHECKER_DEF, orch_def=P.ORCHESTRATOR_DEF)
    _persist(st, model, args)
    # Authenticator is the Phase-2 frozen capture, never a diagnostic refetch.
    current = K.latest_rc(st) if st else {}
    if current.get("rc") == model.get("rc"):
        model["auth"] = current.get("auth")
    R.prepare_report_evidence(model)
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
                                         "yellow_stages", "never_ran", "tests", "failed_suites",
                                         "failed_suites_error", "tests_error")},
                             rc=model.get("rc"))
        C.save_state(st, args.runs_root, args.release)
    except Exception:
        pass


def _u(build_id):
    return K.build_url(build_id) if build_id else ""


def cmd_record_rc_report(args):
    """Reject legacy send acknowledgements; completed work stays an idempotent no-op."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    completed = orch.completed_step_outcome("build_verify", "rc_report")
    if completed is not None:
        print(_json.dumps({"status": "unchanged", "note": completed.note}))
        return 0
    print(_json.dumps({"error": "Use notification claim/result; the approved report snapshot carries its gate verdict"}))
    return 1


def _format(m) -> str:
    L = [f"## RC Pipeline Status — Release {m['release']}", "", rendering.COUNT_NOTE, ""]

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
        vstr = P.format_versions(o.get("versions"), fallback="versions n/a")
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
        current = t.get("count_basis") == P.MRWP_COUNT_BASIS
        cats = (t.get("categories") or {}) if current else {}
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
        suites = r.get("failed_suites") if current else []
        if suites:
            for s in rendering.sort_failed_suites(suites):
                cat = _lbl.get(s.get("category", "ui"), "UI automation")
                fr = round(s["failed"] * 100.0 / s["total"], 1) if s["total"] else 0.0
                L.append(f"   • [{cat}] {s['name']}: {s['failed']}/{s['total']} failed "
                         f"{rendering.suite_count_label(s)} ({fr}%)")
                L.append("       " + rendering.suite_failure_note(s))
                for tname in rendering.failure_test_names(s):
                    L.append(f"       - {tname}")
        if rendering.failure_evidence_error(r):
            L.append("   " + rendering.failure_evidence_error(r))
        L.append(f"   {_u(r.get('run_id'))}")

    L += ["", "Source evidence / release-owner investigation",
          *rendering.source_evidence_lines(m)]
    probs = m.get("problems") or []
    if probs:
        L += ["", "**Issues:**"]
        for p in probs:
            L.append(f"  - {p}")
    recovered = rendering.recovered_tests(m)
    if recovered:
        L += ["", f"⚠ **Retry warning** — {len(recovered)} recovered test(s): SUCCESS "
                  f"(Passed and Failed attempts in any order; counted once as passed):"]
        for t in recovered:
            L.append(f"  - {t}")
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
