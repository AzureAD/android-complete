"""Shared config + helpers for the Phase 2 (build_verify) release-verification steps.

Underscore-prefixed so steps.discover() skips it (it's not a step). Holds the ADO
coordinates for the three Engineering release pipelines and the recovery / escalation
links surfaced when a step blocks, plus small resolvers the step modules reuse.
"""
from __future__ import annotations

ORG = "https://identitydivision.visualstudio.com"
PROJECT = "Engineering"

CHECKER_DEF = 3038          # Code Complete Calendar Checker (fires the release on the CCD)
ORCHESTRATOR_DEF = 2828     # Release Orchestrator (the spine)
MRWP_DEF = 2519             # Monthly Release Work Pipeline (RC testing; runs ECS + Local)

# The orchestrator stages that must be green before RC testing is trustworthy, and the
# stage it should be PARKED at (a human approval gate the owner clears in a later phase).
ORCH_REQUIRED_STAGES = [
    "Validate Branch and Versions availability",
    "Create Release Branches",
    "Trigger RC Testing",
]
ORCH_PARK_STAGE = "Remove RC Tags"

# Surfaced in every block reason so the engineer knows how to recover / escalate.
RECOVERY_TSG = ("https://eng.ms/docs/microsoft-security/identity/"
                "entra-developer-application-platform/auth-client/"
                "authn-sdk-msal-android/android-auth-libraries/releases/"
                "internal-release-checklist/release-orchestrator-recovery")
ESCALATION_CHAT = ("https://teams.microsoft.com/l/chat/"
                   "19:976a859f167f44e59c4ceca8b1d23581@thread.v2/conversations")

# Standard help tail appended to orchestrator/MRWP block reasons.
UNBLOCK_HELP = (
    "\n→ Each failed stage's output describes the root cause + corrective action. "
    "Follow it, then click Retry on the failed stage. Recovery TSG: "
    f"{RECOVERY_TSG} . If unresolved within 2h, escalate: {ESCALATION_CHAT}")


def build_url(build_id):
    return f"{ORG}/{PROJECT}/_build/results?buildId={build_id}"


def links_for(build_id, name="ADO run"):
    return [{"name": name, "url": build_url(build_id)}]


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _pipeline_runs(state) -> dict:
    """The nested pipeline_runs container on state (migrating a legacy flat shape)."""
    from orchestrator.state import migrate_pipeline_runs
    return migrate_pipeline_runs(getattr(state, "pipeline_runs", None) or {})


def stash_checker(state, run_id, when=None):
    """Record the (single) Code Complete Checker run that fired the release."""
    pr = _pipeline_runs(state)
    pr["checker"] = {"run_id": str(run_id), "when": when, "resolved_at": _now_iso()}
    state.pipeline_runs = pr


def stash_orchestrator(state, run_id, versions=None, parked=None):
    """Record the (single) Release Orchestrator run + its RC versions and parked flag."""
    pr = _pipeline_runs(state)
    pr["orchestrator"] = {"run_id": str(run_id),
                          "versions": versions or {},
                          "parked": parked,
                          "resolved_at": _now_iso()}
    state.pipeline_runs = pr


def latest_rc(state) -> dict:
    """The current RC iteration (the last entry in rcs), or {} when none resolved yet."""
    rcs = _pipeline_runs(state).get("rcs") or []
    return rcs[-1] if rcs else {}


def stash_mrwp(state, provider, snapshot):
    """Record an MRWP provider run's FULL verification snapshot into the current RC
    iteration. `provider` is 'ECS' or 'Local'; `snapshot` carries run_id + stage/test
    results (run_id, id_source, complete, ran, total, failed_stages, yellow_stages,
    never_ran, tests, failed_suites).

    RC iterations are a list; the LATEST is rcs[-1]. When this provider's slot in the
    current RC already holds a DIFFERENT run_id, RC Testing was re-triggered → a NEW rc
    entry is appended (rc = last+1) and the snapshot lands there. Same id → idempotent
    update. So ecs/local resolving in separate steps (any order) merge into one rc, and a
    re-trigger rolls forward to the next rc."""
    key = provider.lower()                       # 'ecs' | 'local'
    pr = _pipeline_runs(state)
    rcs = pr.setdefault("rcs", [])
    cur = rcs[-1] if rcs else None
    existing = (cur or {}).get(key) or {}
    if cur is None or (existing.get("run_id") and existing["run_id"] != str(snapshot.get("run_id"))):
        cur = {"rc": (rcs[-1]["rc"] + 1) if rcs else 1}
        rcs.append(cur)
    snap = dict(snapshot)
    snap["run_id"] = str(snapshot.get("run_id"))
    snap["resolved_at"] = _now_iso()
    cur[key] = snap
    cur["resolved_at"] = _now_iso()
    state.pipeline_runs = pr


# The Phase-2 quality bar: at least this % of UI-automation tests must pass for RC to
# clear to bug bash. Below it, the rc_report step blocks for owner investigation
# (a large UI failure usually means a real regression → fix + re-run MRWP).
RC_UI_PASS_THRESHOLD = 90.0


# ---------------------------------------------------------------- RC report email
def rc_report_model(state, timeout=120):
    """The Phase-2 RC report model — assembled from the RECORD in state.pipeline_runs
    (the verification steps stored it), NOT a live re-discovery. Uses the LATEST RC
    iteration (rcs[-1]). Shape mirrors tools.pipelines.release_report so the gate + email
    builders consume it unchanged:
      {release, checker{fired,run_id,when}, orchestrator{found,healthy,parked,run_id,versions},
       mrwp{ECS{...}, Local{...}}, problems[], rc}
    """
    from orchestrator.state import migrate_pipeline_runs
    pr = migrate_pipeline_runs(getattr(state, "pipeline_runs", None) or {})
    ch = pr.get("checker") or {}
    o = pr.get("orchestrator") or {}
    rcs = pr.get("rcs") or []
    rc = rcs[-1] if rcs else {}

    model = {
        "release": state.release_id,
        "checker": {"fired": bool(ch.get("run_id")), "run_id": ch.get("run_id"),
                    "when": ch.get("when")},
        "orchestrator": {"found": bool(o.get("run_id")), "healthy": True,
                         "run_id": o.get("run_id"), "versions": o.get("versions") or {},
                         "parked": o.get("parked")},
        "mrwp": {}, "problems": [], "rc": rc.get("rc"),
    }
    for slot, prov in (("ecs", "ECS"), ("local", "Local")):
        s = rc.get(slot)
        if not s:
            continue
        model["mrwp"][prov] = {
            "run_id": s.get("run_id"), "complete": s.get("complete"),
            "ran": s.get("ran"), "total": s.get("total"),
            "failed_stages": s.get("failed_stages") or [],
            "yellow_stages": s.get("yellow_stages") or [],
            "never_ran": s.get("never_ran") or [],
            "tests": s.get("tests"), "failed_suites": s.get("failed_suites"),
        }
        if not s.get("complete") and s.get("never_ran"):
            model["problems"].append(
                f"MRWP {prov}: did NOT run to completion — never-ran: "
                f"{', '.join(n for n in s['never_ran'] if n)}.")
    return model


def rc_run_links(model) -> list:
    """Durable links to EVERY pipeline run the RC verification evaluated — the Code
    Complete Checker, the Release Orchestrator, and both MRWP (ECS + Local) runs — so the
    recorded step points at each artifact behind the verdict (surfaced in the step's
    Details). Only runs with a resolved id are included."""
    out = []
    ch = (model.get("checker") or {}).get("run_id")
    if ch:
        out.append({"name": "Code Complete Checker run", "url": build_url(ch)})
    orid = (model.get("orchestrator") or {}).get("run_id")
    if orid:
        out.append({"name": "Release Orchestrator run", "url": build_url(orid)})
    for prov in ("ECS", "Local"):
        rid = ((model.get("mrwp") or {}).get(prov) or {}).get("run_id")
        if rid:
            out.append({"name": f"MRWP {prov} run", "url": build_url(rid)})
    return out


def _ui_failing_suites_summary(model, limit=6) -> str:
    """A compact 'Top UI failures' list across both providers, or '' when none."""
    suites = []
    for prov in ("ECS", "Local"):
        for s in (((model.get("mrwp") or {}).get(prov) or {}).get("failed_suites") or []):
            if s.get("category", "ui") == "ui" and s.get("failed"):
                suites.append((prov, s))
    suites.sort(key=lambda ps: -ps[1]["failed"])
    if not suites:
        return ""
    return "\nTop UI failures:\n" + "\n".join(
        f"  \u2022 [{prov}] {s['name']}: {s['failed']}/{s['total']} failed"
        for prov, s in suites[:limit])


def rc_ui_gate(model) -> dict:
    """The Phase-2 RC quality gate — a THREE-tier decision on the combined UI-automation
    pass rate across both MRWP providers (ECS + Local). Returns
      {ui_total, ui_passed, ui_failed, pass_pct, threshold, verdict, blocking, detail}
    where `verdict` is:
      * 'clean'     — 100% UI pass (or no UI tests found): proceed, no action.
      * 'warn'      — >= RC_UI_PASS_THRESHOLD (90%) but < 100%: proceed to bug bash, but
                      the owner should investigate the failing UI tests IN PARALLEL (a
                      later step confirms the retest — bug bash is NOT blocked).
      * 'attention' — < 90%: BLOCK. A large failure the owner must investigate and rule
                      on (patch a real bug + re-trigger RC, or proceed as an automation
                      flake to re-run later).
    `blocking` is True only for 'attention'. `detail` is the note recorded on the step /
    shown to the owner."""
    ui_total = ui_pass = ui_fail = 0
    for prov in ("ECS", "Local"):
        ui = (((model.get("mrwp") or {}).get(prov) or {}).get("tests") or {}) \
            .get("categories", {}).get("ui") or {}
        ui_total += ui.get("total") or 0
        ui_pass += ui.get("passed") or 0
        ui_fail += ui.get("failed") or 0
    thr = RC_UI_PASS_THRESHOLD
    base = {"ui_total": ui_total, "ui_passed": ui_pass, "ui_failed": ui_fail, "threshold": thr}
    if not ui_total:
        return {**base, "pass_pct": None, "verdict": "clean", "blocking": False,
                "detail": ("\u26a0 No UI-automation tests were found in either MRWP run — "
                           "nothing to gate on. Proceeding, but verify RC test coverage.")}
    pass_pct = round(ui_pass * 100.0 / ui_total, 1)
    head = (f"UI-automation pass rate {pass_pct}% ({ui_pass}/{ui_total} passed, "
            f"{ui_fail} failed) across ECS + Local")
    if pass_pct >= 100.0:
        return {**base, "pass_pct": pass_pct, "verdict": "clean", "blocking": False,
                "detail": (f"UI-automation pass rate 100% ({ui_pass}/{ui_total}) — all UI "
                           f"tests passed. Proceeding to bug bash.")}
    if pass_pct >= thr:
        return {**base, "pass_pct": pass_pct, "verdict": "warn", "blocking": False,
                "detail": (f"{head} \u2014 at or above the {thr:.0f}% gate but not clean. "
                           f"Proceeding to bug bash; release owner: investigate the {ui_fail} "
                           f"failing UI test(s) in parallel (a later step confirms the retest, "
                           f"so bug bash is not blocked)." + _ui_failing_suites_summary(model))}
    return {**base, "pass_pct": pass_pct, "verdict": "attention", "blocking": True,
            "detail": (f"{head} \u2014 BELOW the {thr:.0f}% gate. Large UI failure: investigate "
                       f"the root cause and decide \u2014 patch a real bug + re-trigger RC, or "
                       f"(if it's an automation flake to re-run later) proceed to bug bash. This "
                       f"step stays BLOCKED until you `next` after a re-run, or `skip --reason` "
                       f"to override." + _ui_failing_suites_summary(model))}


def rc_email_subject(model) -> str:
    rid = model.get("release", "?")
    v = rc_ui_gate(model)["verdict"]
    action = {"clean": "approve to proceed to bug bash",
              "warn": "proceeding to bug bash — investigate failing UI tests in parallel",
              "attention": "investigate UI failures before proceeding"}[v]
    return f"Release {rid} — RC verification report (Phase 2) · action: {action}"


def _fail_rate(failed, total) -> float:
    """Failure percentage (1 decimal). 0 when total is 0/None."""
    try:
        return round((failed or 0) * 100.0 / total, 1) if total else 0.0
    except (TypeError, ZeroDivisionError):
        return 0.0


# Test-run category labels (mirrors tools.pipelines.classify_test_run).
_CAT_LABEL = {"unit": "Unit", "instrumented": "Instrumented", "ui": "UI automation"}


def _rc_email_plain(model, ctx) -> str:
    """Plain-text form of the RC report email (fallback + logging)."""
    L = []
    rid = model.get("release", "?")
    o = model.get("orchestrator") or {}
    v = o.get("versions") or {}
    vstr = ", ".join(f"{k} {v[k]}" for k in ("Common", "Msal", "Broker") if v.get(k)) or "n/a"
    L.append(f"Hi {ctx.get('owner', 'there')},")
    L.append("")
    L.append(f"The Release Candidate for {rid} has been built and RC testing has "
             f"completed. Review the results below and approve 'RC verified — proceed "
             f"to bug bash' when ready.")
    L.append("")
    ch = model.get("checker") or {}
    L.append("PIPELINE HEALTH")
    L.append(f"  - Code Complete Checker: fired the release (run {ch.get('run_id')}).")
    park = ("parked at 'Remove RC Tags' (awaiting approval, later phase)"
            if o.get("parked") else "gate already cleared")
    L.append(f"  - Release Orchestrator: healthy — pre-gate stages green, {park}.")
    L.append(f"      Versions: {vstr}")
    L.append(f"      {build_url(o.get('run_id'))}")
    L.append("")
    L.append("RC TESTING — results by category (both provider runs ran to completion):")
    for prov in ("ECS", "Local"):
        r = (model.get("mrwp") or {}).get(prov) or {}
        t = r.get("tests") or {}
        cats = t.get("categories") or {}
        ui = cats.get("ui") or {}
        L.append(f"  MRWP {prov} — run {r.get('run_id')} ({r.get('ran')}/{r.get('total')} stages)")
        for cat in ("unit", "instrumented", "ui"):
            c = cats.get(cat) or {}
            if not c.get("total"):
                continue
            tag = "  <-- RC gate" if cat == "ui" else ""
            L.append(f"      {_CAT_LABEL.get(cat, cat):14} {c.get('passed')}/{c.get('total')} passed"
                     f" · {c.get('failed')} failed · {_fail_rate(c.get('failed'), c.get('total'))}% fail{tag}")
        fs = r.get("failed_stages") or []
        if fs:
            L.append(f"      Red stages ({len(fs)}): {', '.join(fs)}")
        _ord = {"ui": 0, "instrumented": 1, "unit": 2}
        for s in sorted((r.get("failed_suites") or []),
                        key=lambda s: (_ord.get(s.get("category", "ui"), 9), -s["failed"])):
            sr = _fail_rate(s["failed"], s["total"])
            L.append(f"      [{_CAT_LABEL.get(s.get('category', 'ui'), 'UI automation')}] "
                     f"{s['name']} — {s['failed']}/{s['total']} failed ({sr}%):")
            names = s.get("tests", [])
            for tname in names[:10]:
                L.append(f"          - {tname}")
            if len(names) < s["failed"]:
                L.append(f"          … and {s['failed'] - len(names)} more (see the run)")
        L.append(f"      {build_url(r.get('run_id'))}")
        L.append("")
    probs = model.get("problems") or []
    if probs:
        L.append("BLOCKING ISSUES (a stage that never ran = the pipeline aborted):")
        L += [f"  - {p}" for p in probs]
        L.append("")
    L.append("NEXT: review the failing tests above. If they're acceptable to carry into "
             "bug bash, approve the gate (advances to Phase 3 — Test / Bug Bash). "
             "Otherwise investigate the red suites first.")
    L.append("")
    L.append("— Release Orchestrator (Scout)")
    return "\n".join(L)


def _rc_email_html(model, ctx) -> str:
    """Email-safe HTML form of the RC report — a compact visual dashboard (inline styles,
    table-based bars; Outlook-friendly)."""
    from steps.lib import templating as T
    rid = model.get("release", "?")
    o = model.get("orchestrator") or {}
    v = o.get("versions") or {}
    vstr = ", ".join(f"{k} {v[k]}" for k in ("Common", "Msal", "Broker") if v.get(k)) or "n/a"
    ch = model.get("checker") or {}
    park = ("parked at &lsquo;Remove RC Tags&rsquo;"
            if o.get("parked") else "gate cleared")

    def _split_bar(pass_pct, h=10):
        """A green(pass)/red(fail) horizontal bar as a 2-cell table (Outlook-safe)."""
        p = max(0, min(100, int(round(pass_pct))))
        f = 100 - p
        pcell = (f"<td width='{p}%' bgcolor='#12b76a' style='font-size:0;line-height:0;'>&nbsp;</td>"
                 if p > 0 else "")
        fcell = (f"<td width='{f}%' bgcolor='#f04438' style='font-size:0;line-height:0;'>&nbsp;</td>"
                 if f > 0 else "")
        return (f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
                f"style='border-collapse:separate;height:{h}px;border-radius:{h//2}px;overflow:hidden;'>"
                f"<tr>{pcell}{fcell}</tr></table>")

    def _chip(text, bg, fg):
        return (f"<span style='display:inline-block;padding:2px 8px;border-radius:10px;"
                f"background:{bg};color:{fg};font-size:12px;font-weight:600;'>{text}</span>")

    def _cat_row(cat, c):
        total = c.get("total") or 0
        if not total:
            return ""
        passed, failed = c.get("passed") or 0, c.get("failed") or 0
        fr = _fail_rate(failed, total)
        is_ui = cat == "ui"
        lbl_style = "font-weight:700;color:#101828;" if is_ui else "color:#475467;"
        gate = (" <span style='font-size:11px;color:#0b5cad;'>&larr; RC gate</span>"
                if is_ui else "")
        pct_color = "#b42318" if fr >= 1 else ("#b54708" if fr > 0 else "#067647")
        return (f"<tr>"
                f"<td style='padding:5px 0;font-size:13px;{lbl_style}'>{_CAT_LABEL.get(cat, cat)}{gate}</td>"
                f"<td style='padding:5px 10px;font-size:12px;color:#98a2b3;white-space:nowrap;'>{passed}/{total}</td>"
                f"<td width='130' style='padding:5px 0;'>{_split_bar(100 - fr, h=6)}</td>"
                f"<td align='right' style='padding:5px 0 5px 10px;font-size:13px;font-weight:700;"
                f"color:{pct_color};white-space:nowrap;'>{fr}%</td></tr>")

    def mrwp_card(prov):
        r = (model.get("mrwp") or {}).get(prov) or {}
        t = r.get("tests") or {}
        cats = t.get("categories") or {}
        ui = cats.get("ui") or {}
        ui_total, ui_pass, ui_fail = ui.get("total") or 0, ui.get("passed") or 0, ui.get("failed") or 0
        ui_rate = _fail_rate(ui_fail, ui_total)
        fs = r.get("failed_stages") or []
        red = (f"<div style='margin:8px 0 0;color:#b42318;font-size:12px;'>Red stages "
               f"({len(fs)}): {T.esc(', '.join(fs))}</div>" if fs else "")

        cat_table = "".join(_cat_row(c, cats.get(c) or {}) for c in
                            ("unit", "instrumented", "ui"))

        # Failing suites — UI first, then instrumented/unit; each tagged by category.
        _ord = {"ui": 0, "instrumented": 1, "unit": 2}
        suites = sorted((r.get("failed_suites") or []),
                        key=lambda s: (_ord.get(s.get("category", "ui"), 9), -s["failed"]))
        suite_html = ""
        for s in suites:
            sr = _fail_rate(s["failed"], s["total"])
            names = s.get("tests", [])
            items = "".join(
                f"<li style='margin:1px 0;color:#475467;'>{T.esc(n)}</li>" for n in names[:4])
            more = (f"<li style='margin:1px 0;color:#98a2b3;list-style:none;'>… and "
                    f"{s['failed'] - len(names)} more</li>" if len(names) < s["failed"] else "")
            tag = _chip(_CAT_LABEL.get(s.get("category", "ui"), "UI automation"), "#eef4ff", "#0b5cad")
            suite_html += (
                f"<div style='margin:9px 0 0;'>"
                f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
                f"<td style='font-size:13px;font-weight:600;color:#1d2939;'>{T.esc(s['name'])} &nbsp;{tag}</td>"
                f"<td align='right' style='font-size:13px;white-space:nowrap;'>"
                f"<strong style='color:#b42318;'>{s['failed']}</strong>"
                f"<span style='color:#98a2b3;'>/{s['total']}</span> "
                f"<span style='color:#b42318;font-weight:600;'>&middot; {sr}%</span></td></tr></table>"
                f"<ul style='margin:2px 0 0 18px;padding:0;font-size:12px;"
                f"font-family:Consolas,ui-monospace,monospace;'>{items}{more}</ul></div>")

        rate_color = "#b42318" if ui_rate >= 5 else ("#b54708" if ui_rate > 0 else "#067647")
        return (
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid #e4e7ec;border-radius:10px;margin:10px 0;'>"
            f"<tr><td style='padding:14px 16px;'>"
            f"<table role='presentation' width='100%'><tr>"
            f"<td style='font-size:15px;font-weight:700;color:#101828;'>MRWP {prov}"
            f"<span style='color:#98a2b3;font-weight:400;font-size:13px;'> &middot; run {r.get('run_id')} "
            f"&middot; {r.get('ran')}/{r.get('total')} stages</span></td>"
            f"<td align='right'>{_chip('completed', '#ecfdf3', '#067647')}</td></tr></table>"
            # headline = UI-automation failure rate (the RC-critical bucket)
            f"<div style='margin:10px 0 2px;'>"
            f"<span style='font-size:26px;font-weight:800;color:{rate_color};'>{ui_rate}%</span>"
            f"<span style='font-size:13px;color:#667085;'> UI-automation failure rate &nbsp;·&nbsp; "
            f"<strong style='color:#12b76a;'>{ui_pass}</strong> passed / "
            f"<strong style='color:#b42318;'>{ui_fail}</strong> failed of {ui_total} UI tests</span></div>"
            f"{_split_bar(100 - ui_rate)}"
            # per-category breakdown
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='margin:10px 0 0;border-top:1px solid #eef0f3;'>{cat_table}</table>"
            f"{red}{suite_html}"
            f"<div style='margin-top:10px;'><a href='{build_url(r.get('run_id'))}' "
            f"style='color:#0b5cad;font-size:13px;'>Open run {r.get('run_id')} &rsaquo;</a></div>"
            f"</td></tr></table>")

    # Overall headline — UI-automation failures ONLY (the RC-critical bucket), across both providers.
    def _ui_sum(field):
        return sum((((model.get("mrwp") or {}).get(p) or {}).get("tests", {})
                    .get("categories", {}).get("ui", {}).get(field, 0) or 0)
                   for p in ("ECS", "Local"))
    tot_f, tot_t = _ui_sum("failed"), _ui_sum("total")
    overall_rate = _fail_rate(tot_f, tot_t)

    probs = model.get("problems") or []
    issues = (("<div style='margin:12px 0;padding:10px 12px;background:#fef3f2;border:1px solid #fda29b;"
               "border-radius:8px;color:#b42318;'><strong>Blocking issues</strong> (a stage that never "
               "ran = pipeline aborted):<ul style='margin:6px 0 0 18px;'>"
               + "".join(f"<li>{T.esc(p)}</li>" for p in probs) + "</ul></div>")
              if probs else "")

    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;color:#101828;line-height:1.5;max-width:720px;margin:0 auto;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-radius:10px;background:#0b5cad;">
    <tr><td style="padding:18px 20px;color:#ffffff;">
      <div style="font-size:19px;font-weight:800;">RC Verification Report</div>
      <div style="font-size:13px;opacity:.92;margin-top:2px;">Release {T.esc(rid)} &middot; Phase 2 &mdash; Build &amp; RC testing</div>
    </td></tr>
  </table>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:12px 0;border:1px solid #e4e7ec;border-radius:10px;">
    <tr>
      <td style="padding:12px 16px;border-right:1px solid #eef0f3;" width="33%">
        <div style="font-size:12px;color:#667085;">UI-automation failure rate</div>
        <div style="font-size:22px;font-weight:800;color:{'#b42318' if overall_rate >= 5 else '#b54708'};">{overall_rate}%</div>
        <div style="font-size:12px;color:#98a2b3;">{tot_f} failed / {tot_t} UI tests</div>
      </td>
      <td style="padding:12px 16px;border-right:1px solid #eef0f3;" width="33%">
        <div style="font-size:12px;color:#667085;">Checker</div>
        <div style="font-size:15px;font-weight:700;color:#067647;">&#10003; Fired</div>
        <div style="font-size:12px;color:#98a2b3;">run {ch.get('run_id')}</div>
      </td>
      <td style="padding:12px 16px;" width="34%">
        <div style="font-size:12px;color:#667085;">Orchestrator</div>
        <div style="font-size:15px;font-weight:700;color:#067647;">&#10003; Healthy</div>
        <div style="font-size:12px;color:#98a2b3;">{park}</div>
      </td>
    </tr>
  </table>

  <p style="margin:6px 0;color:#475467;">Versions: <strong>{T.esc(vstr)}</strong> &middot;
     <a href="{build_url(o.get('run_id'))}" style="color:#0b5cad;">orchestrator run {o.get('run_id')}</a></p>

  <p style="margin:16px 0 2px;font-size:15px;font-weight:700;">UI-automation results</p>
  {mrwp_card('ECS')}
  {mrwp_card('Local')}
  {issues}

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:14px 0;border-radius:8px;background:#f9fafb;border:1px solid #eef0f3;">
    <tr><td style="padding:12px 16px;">
      <strong>Next:</strong> review the failing suites above. If acceptable to carry into bug bash,
      approve <strong>&ldquo;RC verified &mdash; proceed to bug bash&rdquo;</strong>
      (advances to Phase 3). Otherwise investigate the red suites first.
    </td></tr>
  </table>
  <p style="color:#98a2b3;font-size:12px;">&mdash; Release Orchestrator (Scout)</p>
</div>"""


def rc_email(state):
    """Compose the RC verification email (subject, html, plain) for this release from
    LIVE pipeline data. Returns (subject, html, plain, model)."""
    from steps.lib.context import release_ctx
    model = rc_report_model(state)
    ctx = release_ctx(state)
    return (rc_email_subject(model), _rc_email_html(model, ctx),
            _rc_email_plain(model, ctx), model)


def verify_mrwp(state, provider):
    """Shared body for the mrwp_ecs / mrwp_local steps. `provider` is 'ECS' or 'Local'.

    Resolves this release's MRWP (def 2519) run for the provider — from the orchestrator
    run's RC-<provider>=<id> tag, or a log-parse fallback — then applies the release
    stage-completion rule (every stage must have executed; skipped/canceled/pending =
    block) and attaches the Test-tab summary. Uses the step's mock knobs when present:
      mrwp_id : inject the MRWP build id (skip the orchestrator lookup)
      stages  : inject the stage list [{name,state,result}]
      tests   : inject the test summary {total,passed,failed[,runs]}
    Returns a Done/Blocked outcome.
    """
    from orchestrator.outcomes import Done, Blocked
    from steps.lib.mockctx import mock_input, MISSING
    from tools import pipelines as P

    label = f"MRWP {provider}"
    # 1) resolve the MRWP build id for this provider
    mid = mock_input("mrwp_id", MISSING)
    if mid is MISSING:
        ok, run, detail = P.find_orchestrator_run(ORG, PROJECT, ORCHESTRATOR_DEF, state.release_id)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not read orchestrator run ({detail}){hint}.")
        if not run:
            return Blocked(
                f"{label}: no orchestrator run found for {state.release_id} — can't locate "
                f"the RC-testing runs. Verify the orchestrator first.")
        ok2, ids, detail2, source = P.mrwp_run_ids(ORG, PROJECT, run)
        if not ok2:
            hint = " — run `az login`" if str(detail2).startswith("AUTH") else ""
            return Blocked(
                f"{label}: could not resolve the MRWP run id ({detail2}){hint}.",
                links=links_for(run.get("id"), "Release Orchestrator run"))
        mid = ids.get(provider)
        if not mid:
            return Blocked(f"{label}: orchestrator didn't record a {provider} RC-testing run.")

    links = links_for(mid, f"{label} run")

    # 2) stage-completion rule
    stages = mock_input("stages", MISSING)
    if stages is MISSING:
        ok, stages, detail = P.get_stages(ORG, PROJECT, mid)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not read stages for run {mid} ({detail}){hint}.", links=links)
    comp = P.stage_completion(stages)
    if not comp["complete"]:
        never = ", ".join(n for n in comp["never_ran"] if n) or "(unknown)"
        return Blocked(
            f"{label} run {mid} did NOT run to completion — {len(comp['never_ran'])} stage(s) "
            f"never ran (pending/skipped/canceled): {never}. A stage that never ran means the "
            f"pipeline aborted partway.{UNBLOCK_HELP}", links=links)

    # 3) test summary (best-effort — never blocks; red/yellow tests are triaged later)
    tests = mock_input("tests", MISSING)
    tests_injected = tests is not MISSING
    if not tests_injected:
        ok, tests, _ = P.get_test_summary(ORG, PROJECT, mid)
        if not ok:
            tests = None
    tnote = ""
    if tests:
        tnote = f" Tests: {tests['passed']}/{tests['total']} passed, {tests['failed']} failed."
    stage_note = f"{comp['ran']}/{comp['total']} stages ran"
    extras = []
    if comp["failed"]:
        extras.append(f"{len(comp['failed'])} red")
    if comp["yellow"]:
        extras.append(f"{len(comp['yellow'])} yellow")
    extra = f" ({', '.join(extras)} — triaged later)" if extras else ""

    # 4) failing suites (individual test names) — snapshot alongside the summary so the RC
    # report + gate read everything from state (no re-discovery). Mockable via `suites`.
    # Only fetch LIVE when the summary was read live (tests not injected) — an injected
    # summary means an offline/test context, so we don't make the extra network call.
    suites = mock_input("suites", MISSING)
    if suites is MISSING:
        suites = None
        if not tests_injected and tests and tests.get("failed"):
            okf, fsuites, _ = P.get_failed_tests(ORG, PROJECT, mid)
            if okf:
                suites = fsuites

    # 5) stash the FULL per-provider snapshot into the current RC iteration.
    stash_mrwp(state, provider, {
        "run_id": mid, "complete": comp["complete"], "ran": comp["ran"],
        "total": comp["total"], "failed_stages": comp["failed"],
        "yellow_stages": comp["yellow"], "never_ran": comp["never_ran"],
        "tests": tests, "failed_suites": suites,
    })
    return Done(
        f"{label} run {mid} ran to completion — {stage_note}{extra}.{tnote}", links=links)
