"""RC report presentation. Gate decisions are supplied by the owning rc_report step."""
from __future__ import annotations

from steps.build_verify._common import build_url, valid_id, valid_counts
from steps.build_verify.auth_ecs import auth_build_url, auth_pass_pct
from steps.lib import templating as T
from tools.pipelines import format_versions, AUTH_UI_SUITES, AUTH_UI_PASS_THRESHOLD


def recovered_unit_tests(model) -> list:
    """Unit tests that FAILED then PASSED on retry (the unit retry rule) across both MRWP
    providers — counted as passed, but surfaced as a warning in the report. De-duplicated,
    sorted."""
    out = set()
    for prov in ("ECS", "Local"):
        cats = (((model.get("mrwp") or {}).get(prov) or {}).get("tests") or {}) \
            .get("categories", {})
        for t in ((cats.get("unit") or {}).get("recovered") or []):
            out.add(t)
    return sorted(out)


def auth_leg_summary(model, auth) -> dict:
    """Compact Authenticator-ECS verdict for the RC report's at-a-glance surfaces (email
    subject + the gates banner). Reads the stored auth section (model['auth']) and the
    report's evaluated auth gate. Returns {present, verdict, blocking, headline, worst}."""
    a = model.get("auth") or {}
    if not a:
        return {"present": False, "verdict": "unavailable", "blocking": True,
                "headline": "not evaluated", "worst": None}
    suites = (a.get("test") or {}).get("suites") or {}
    verdict = auth["verdict"]
    worst = None                                  # (short_name, pct|None) — missing or lowest
    for name in AUTH_UI_SUITES:
        s = suites.get(name) or {}
        short = name.split(" - ")[-1]
        if not s.get("present"):
            worst = (short, None)
            break
        pct = auth_pass_pct(s)
        if worst is None or (pct is not None and (worst[1] is None or pct < worst[1])):
            worst = (short, pct)
    if verdict == "clean":
        headline = f"both suites >= {AUTH_UI_PASS_THRESHOLD:.0f}%"
    elif worst and worst[1] is None:
        headline = f"{worst[0]}: no result"
    elif worst:
        headline = f"{worst[0]} {worst[1]}%"
    else:
        headline = f"< {AUTH_UI_PASS_THRESHOLD:.0f}%"
    return {"present": True, "verdict": verdict, "blocking": verdict != "clean",
            "headline": headline, "worst": worst}


def rc_email_subject(model, gate, auth) -> str:
    rid = model.get("release", "?")
    v = gate["verdict"]
    action = {"clean": "clean",
              "warn": "pass with warning — investigate failing UI tests in parallel",
              "attention": "investigate UI failures before proceeding",
              "unavailable": "evidence unavailable — hold"}[v]
    subject = f"Release {rid} — RC verification report (Phase 2) · MRWP UI: {action}"
    auth = auth_leg_summary(model, auth)
    if auth["present"]:
        subject += f" · Auth ECS: {'pass' if auth['verdict'] == 'clean' else 'HOLD'}"
    return subject


def _fail_rate(failed, total) -> float:
    """Failure percentage (1 decimal). 0 when total is 0/None."""
    try:
        return round((failed or 0) * 100.0 / total, 1) if total else 0.0
    except (TypeError, ZeroDivisionError):
        return 0.0


# Test-run category labels (mirrors tools.pipelines.classify_test_run).
_CAT_LABEL = {"unit": "Unit", "instrumented": "Instrumented", "ui": "UI automation"}

# Failing-suite display order: UI first (the RC-critical bucket), then instrumented, unit.
_SUITE_ORDER = {"ui": 0, "instrumented": 1, "unit": 2}


def sort_failed_suites(suites):
    """Failing suites ordered UI-first then instrumented/unit, each by descending failure
    count. One helper so every RC renderer (plain email, HTML email, CLI report) lists
    them identically."""
    return sorted(suites or [],
                  key=lambda s: (_SUITE_ORDER.get(s.get("category", "ui"), 9),
                                 -(s.get("failed") or 0)))


def rc_email_plain(model, ctx, gate, auth, next_action) -> str:
    """Plain-text form of the RC report email (fallback + logging)."""
    L = []
    rid = model.get("release", "?")
    o = model.get("orchestrator") or {}
    vstr = format_versions(o.get("versions"), fallback="n/a")
    L.append(f"Hi {ctx.get('owner', 'there')},")
    L.append("")
    L.append(f"Captured RC verification results for {rid}. {next_action}")
    L.append("")
    _g = gate
    _mpct = _g.get("pass_pct")
    L.append("GATES (evaluated independently)")
    L.append(f"  - MRWP UI: {_g['verdict']}"
             + (f" ({_mpct}% pass across ECS + Local)" if _mpct is not None else ""))
    _auth = auth_leg_summary(model, auth)
    if _auth["present"]:
        L.append(f"  - Authenticator ECS: "
                 f"{'pass' if _auth['verdict'] == 'clean' else 'HOLD'} "
                 f"({_auth['headline']})")
    L.append("")
    ch = model.get("checker") or {}
    L.append("PIPELINE HEALTH")
    L.append(f"  - Code Complete Checker: fired the release (run {ch.get('run_id')}).")
    park = ("parked at 'Remove RC Tags' (awaiting approval, later phase)"
            if o.get("parked") else "gate already cleared")
    L.append(f"  - Release Orchestrator: healthy — pre-gate stages green, {park}.")
    L.append(f"      Versions: {vstr}")
    L.append(f"      Run: {build_url(o.get('run_id'))}")
    L.append("")
    L.append("RC TESTING — captured results by category:")
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
        for s in sort_failed_suites(r.get("failed_suites")):
            sr = _fail_rate(s["failed"], s["total"])
            L.append(f"      [{_CAT_LABEL.get(s.get('category', 'ui'), 'UI automation')}] "
                     f"{s['name']} — {s['failed']}/{s['total']} failed ({sr}%):")
            names = s.get("tests", [])
            for tname in names[:10]:
                L.append(f"          - {tname}")
            if len(names) < s["failed"]:
                L.append(f"          … and {s['failed'] - len(names)} more (see the run)")
        L.append(f"      Run: {build_url(r.get('run_id'))}")
        L.append("")
    a = model.get("auth") or {}
    if a:
        b, t = a.get("build") or {}, a.get("test") or {}
        suites = t.get("suites") or {}
        verdict = ("PASS (both suites >= %.0f%%)" % AUTH_UI_PASS_THRESHOLD
                   if auth["verdict"] == "clean" else "HOLD")
        L.append(f"AUTHENTICATOR ECS — separate gate, does NOT affect the UI rate above: {verdict}")
        L.append(f"  build {b.get('run_id')} ({b.get('version')}), result: {b.get('result')}, "
                 f"UI tests: {t.get('run_id') or 'not available'}")
        for name in AUTH_UI_SUITES:
            s = suites.get(name) or {}
            if not s.get("present"):
                L.append(f"      {name}: no result")
                continue
            passed, failed = s.get("passed", 0) or 0, s.get("failed", 0) or 0
            pct = auth_pass_pct(s)
            L.append(f"      {name}: {passed}/{passed + failed} passed "
                     f"({'n/a' if pct is None else str(pct) + '%'})")
        L.append(f"      Run: {auth_build_url(b.get('run_id'))}")
        L.append("")
    probs = model.get("problems") or []
    if probs:
        L.append("BLOCKING ISSUES (a stage that never ran = the pipeline aborted):")
        L += [f"  - {p}" for p in probs]
        L.append("")
    recovered = recovered_unit_tests(model)
    if recovered:
        L.append(f"\u26a0 RETRY WARNING — {len(recovered)} unit test(s) FAILED then PASSED "
                 f"on retry (counted as passed; verify they aren't genuinely flaky):")
        L += [f"  - {t}" for t in recovered[:20]]
        if len(recovered) > 20:
            L.append(f"  … and {len(recovered) - 20} more")
        L.append("")
    L.append("NEXT: " + next_action)
    L.append("")
    L.append("— Release Orchestrator (Scout)")
    return "\n".join(L)


def rc_email_html(model, ctx, gate, auth, next_action) -> str:
    """Email-safe HTML form of the RC report — a compact visual dashboard (inline styles,
    table-based bars; Outlook-friendly)."""
    rid = model.get("release", "?")
    o = model.get("orchestrator") or {}
    vstr = format_versions(o.get("versions"), fallback="n/a")
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
                f"<td width='130' style='padding:5px 0;'>{_split_bar(passed * 100 / total, h=6)}</td>"
                f"<td align='right' style='padding:5px 0 5px 10px;font-size:13px;font-weight:700;"
                f"color:{pct_color};white-space:nowrap;'>{fr}%</td></tr>")

    def mrwp_card(prov):
        r = (model.get("mrwp") or {}).get(prov) or {}
        if not valid_id(r.get("run_id")):
            return f"<p>MRWP {prov}: evidence unavailable — no run captured.</p>"
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
        suites = sort_failed_suites(r.get("failed_suites"))
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
            f"<td align='right'>{_chip('completed' if r.get('complete') is True else 'incomplete', '#eef0f3', '#475467')}</td></tr></table>"
            # headline = UI-automation failure rate (the RC-critical bucket)
            f"<div style='margin:10px 0 2px;'>"
            f"<span style='font-size:26px;font-weight:800;color:{rate_color};'>{str(ui_rate) + '%' if valid_counts(ui) else 'unavailable'}</span>"
            f"<span style='font-size:13px;color:#667085;'> UI-automation failure rate &nbsp;·&nbsp; "
            f"<strong style='color:#12b76a;'>{ui_pass}</strong> passed / "
            f"<strong style='color:#b42318;'>{ui_fail}</strong> failed of {ui_total} UI tests</span></div>"
            f"{_split_bar(ui_pass * 100 / ui_total) if valid_counts(ui) else ''}"
            # per-category breakdown
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='margin:10px 0 0;border-top:1px solid #eef0f3;'>{cat_table}</table>"
            f"{red}{suite_html}"
            f"<div style='margin-top:10px;'><a href='{build_url(r.get('run_id'))}' "
            f"style='color:#0b5cad;font-size:13px;'>Open run {r.get('run_id')} &rsaquo;</a></div>"
            f"</td></tr></table>")

    def _auth_card():
        """Separate 'Authenticator ECS' section — its OWN gate (both Firebase suites >=90%),
        rendered only when the auth leg resolved. Does NOT feed the MRWP UI headline above."""
        a = model.get("auth") or {}
        if not a:
            return ""
        b = a.get("build") or {}
        t = a.get("test") or {}
        suites = t.get("suites") or {}
        clean = auth["verdict"] == "clean"
        chip = (_chip("PASS &ge;90%", "#ecfdf3", "#067647") if clean
                else _chip("HOLD", "#fef3f2", "#b42318"))
        rows = ""
        for name in AUTH_UI_SUITES:
            s = suites.get(name) or {}
            present = s.get("present")
            pct = auth_pass_pct(s)
            ok = present and pct is not None and pct >= AUTH_UI_PASS_THRESHOLD
            pcol = "#067647" if ok else "#b42318"
            passed, failed = s.get("passed", 0) or 0, s.get("failed", 0) or 0
            denom = passed + failed
            disp = "no result" if not present else (f"{pct}%" if pct is not None else "n/a")
            rows += (
                f"<tr><td style='padding:5px 0;font-size:13px;color:#1d2939;'>{T.esc(name)}</td>"
                f"<td style='padding:5px 10px;font-size:12px;color:#98a2b3;white-space:nowrap;'>"
                f"{passed}/{denom}</td>"
                f"<td width='130' style='padding:5px 0;'>"
                f"{_split_bar((pct if pct is not None else 0), h=6)}</td>"
                f"<td align='right' style='padding:5px 0 5px 10px;font-size:13px;font-weight:700;"
                f"color:{pcol};white-space:nowrap;'>{disp}</td></tr>")
        bid, tid = b.get("run_id"), t.get("run_id")
        ver_span = (f"<span style='color:#98a2b3;font-weight:400;font-size:13px;'> &middot; "
                    f"build {bid} &middot; {T.esc(b.get('version') or '')}"
                    f" &middot; {T.esc(b.get('result') or 'unknown result')}</span>")
        link = (f"<div style='margin-top:10px;'><a href='{auth_build_url(bid)}' "
                f"style='color:#0b5cad;font-size:13px;'>Open auth build {bid} &rsaquo;</a>"
                + (f" &nbsp; <a href='{auth_build_url(tid)}' style='color:#0b5cad;font-size:13px;'>"
                   f"UI tests {tid} &rsaquo;</a>" if tid else "") + "</div>")
        return (
            f"<p style='margin:16px 0 2px;font-size:15px;font-weight:700;'>Authenticator ECS "
            f"<span style='color:#98a2b3;font-weight:400;font-size:13px;'>&middot; separate gate "
            f"(does not affect the UI-automation rate above)</span></p>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid #e4e7ec;border-radius:10px;margin:6px 0;'>"
            f"<tr><td style='padding:14px 16px;'>"
            f"<table role='presentation' width='100%'><tr>"
            f"<td style='font-size:14px;font-weight:700;color:#101828;'>Firebase device suites"
            f"{ver_span}</td>"
            f"<td align='right'>{chip}</td></tr></table>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='margin:8px 0 0;border-top:1px solid #eef0f3;'>{rows}</table>"
            f"{link}</td></tr></table>")

    # Overall headline — UI-automation failures ONLY (the RC-critical bucket), across both providers.
    def _ui_sum(field):
        return sum(((((model.get("mrwp") or {}).get(p) or {}).get("tests") or {})
                    .get("categories", {}).get("ui", {}).get(field, 0) or 0)
                   for p in ("ECS", "Local"))
    tot_f, tot_t = _ui_sum("failed"), _ui_sum("total")
    overall_rate = _fail_rate(tot_f, tot_t)

    def _gates_banner():
        """Two chips at the top so the report contemplates BOTH gates at a glance — the MRWP
        combined UI gate and the SEPARATE Authenticator-ECS gate (they stay independent)."""
        g = gate
        mcol = {"clean": ("#ecfdf3", "#067647"), "warn": ("#fffaeb", "#b54708"),
                "attention": ("#fef3f2", "#b42318")}.get(g["verdict"], ("#eef0f3", "#475467"))
        mpct = g.get("pass_pct")
        mtxt = f"MRWP UI: {g['verdict']}" + (f" &middot; {mpct}%" if mpct is not None else "")
        cells = f"<td style='padding:0 8px 0 0;'>{_chip(mtxt, *mcol)}</td>"
        a = auth_leg_summary(model, auth)
        if a["present"]:
            acol = ("#ecfdf3", "#067647") if a["verdict"] == "clean" else ("#fef3f2", "#b42318")
            atxt = "Auth ECS: " + ("pass" if a["verdict"] == "clean" else "HOLD")
            cells += f"<td style='padding:0 8px;'>{_chip(atxt, *acol)}</td>"
        return ("<table role='presentation' cellpadding='0' cellspacing='0' style='margin:12px 0 0;'>"
                f"<tr>{cells}</tr></table>")

    probs = model.get("problems") or []
    issues = (("<div style='margin:12px 0;padding:10px 12px;background:#fef3f2;border:1px solid #fda29b;"
               "border-radius:8px;color:#b42318;'><strong>Blocking issues</strong> (a stage that never "
               "ran = pipeline aborted):<ul style='margin:6px 0 0 18px;'>"
               + "".join(f"<li>{T.esc(p)}</li>" for p in probs) + "</ul></div>")
              if probs else "")

    recovered = recovered_unit_tests(model)
    retry_warn = ""
    if recovered:
        shown = recovered[:15]
        more = (f"<li style='list-style:none;color:#b54708;'>&hellip; and "
                f"{len(recovered) - len(shown)} more</li>" if len(recovered) > len(shown) else "")
        retry_warn = (
            "<div style='margin:12px 0;padding:10px 12px;background:#fffaeb;border:1px solid #fedf89;"
            "border-radius:8px;color:#b54708;'><strong>&#9888; Retry warning</strong> &mdash; "
            f"{len(recovered)} unit test(s) failed then <strong>passed on retry</strong> (counted as "
            "passed; verify they aren&rsquo;t genuinely flaky):"
            "<ul style='margin:6px 0 0 18px;font-family:Consolas,ui-monospace,monospace;font-size:12px;'>"
            + "".join(f"<li>{T.esc(t)}</li>" for t in shown) + more + "</ul></div>")

    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;color:#101828;line-height:1.5;max-width:720px;margin:0 auto;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-radius:10px;background:#0b5cad;">
    <tr><td style="padding:18px 20px;color:#ffffff;">
      <div style="font-size:19px;font-weight:800;">RC Verification Report</div>
      <div style="font-size:13px;opacity:.92;margin-top:2px;">Release {T.esc(rid)} &middot; Phase 2 &mdash; Build &amp; RC testing</div>
    </td></tr>
  </table>
  {_gates_banner()}

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:12px 0;border:1px solid #e4e7ec;border-radius:10px;">
    <tr>
      <td style="padding:12px 16px;border-right:1px solid #eef0f3;" width="33%">
        <div style="font-size:12px;color:#667085;">UI-automation failure rate</div>
        <div style="font-size:22px;font-weight:800;color:{'#b42318' if overall_rate >= 5 else '#b54708'};">{str(overall_rate) + '%' if gate['verdict'] != 'unavailable' else 'unavailable'}</div>
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
  {_auth_card()}
  {issues}
  {retry_warn}

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:14px 0;border-radius:8px;background:#f9fafb;border:1px solid #eef0f3;">
    <tr><td style="padding:12px 16px;">
      <strong>Next:</strong> {T.esc(next_action)}
    </td></tr>
  </table>
  <p style="color:#98a2b3;font-size:12px;">&mdash; Release Orchestrator (Scout)</p>
</div>"""
