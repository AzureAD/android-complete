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
    if getattr(args, "json", False):
        print(_json.dumps(model, indent=2))
        return 0
    print(_format(model))
    return 1 if model.get("problems") else 0


def _u(build_id):
    return K.build_url(build_id) if build_id else ""


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
        v = o.get("versions") or {}
        vstr = ", ".join(f"{k} {v[k]}" for k in ("Common", "Msal", "Broker") if v.get(k)) or "versions n/a"
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
        t = r.get("tests")
        if t:
            L.append(f"   tests: {t['passed']}/{t['total']} passed, {t['failed']} failed")
            failing = [ru for ru in (t.get("runs") or []) if ru.get("failed")]
            for ru in sorted(failing, key=lambda x: -x["failed"])[:6]:
                L.append(f"     • {ru['name']}: {ru['failed']} failed / {ru['total']}")
        L.append(f"   {_u(r.get('run_id'))}")

    probs = m.get("problems") or []
    L += ["", ("**Issues:**" if probs else "**No blocking issues** — red/yellow stages and failed tests are triaged in bug bash.")]
    for p in probs:
        L.append(f"  - {p}")
    return "\n".join(L)


def register(sub):
    rp = sub.add_parser("rc-report", help="Phase 2 RC-pipeline + test status report (read-only)")
    rp.add_argument("--release", required=True)
    rp.add_argument("--json", action="store_true", help="Emit the raw report model")
    rp.set_defaults(func=cmd_rc_report)
