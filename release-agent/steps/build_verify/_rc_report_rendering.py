"""RC report presentation. Gate decisions are supplied by the owning rc_report step."""
from __future__ import annotations
import re

from steps.build_verify._common import build_url, valid_id, valid_counts
from steps.build_verify.auth_ecs import auth_build_url, auth_pass_pct
from steps.lib import templating as T
from tools.pipelines import (
    format_versions, AUTH_UI_SUITES, AUTH_UI_PASS_THRESHOLD, MRWP_COUNT_BASIS,
    MONTHLY_REPORT_ONLY,
    TEST_CATEGORIES, _CATEGORY_LABEL, ui_stress_counts,
)


_URL_RE = re.compile(r"https://[^\s)]+")


def _link_urls_html(text: str) -> str:
    parts, last = [], 0
    for match in _URL_RE.finditer(text or ""):
        parts.append(T.esc(text[last:match.start()]))
        url = match.group(0)
        parts.append(f"<a href='{T.esc(url)}' style='color:#0b5cad;'>{T.esc(url)}</a>")
        last = match.end()
    parts.append(T.esc((text or "")[last:]))
    return "".join(parts)


def _next_detail(model):
    from steps.build_verify import rc_report as R
    return R.rc_next_action_detail(model)


def _next_html(model):
    detail = _next_detail(model)
    intro = (f"<p style='margin:6px 0;color:#475467;'>{_link_urls_html(detail.get('intro', ''))}</p>"
             if detail.get("intro") else "")
    sections = []
    for section in detail.get("sections", []):
        items = "".join(
            f"<li style='margin:4px 0;'>{_link_urls_html(item)}</li>"
            for item in section.get("items", []))
        sections.append(
            f"<div style='margin-top:10px;'><div style='font-weight:700;color:#1d2939;'>"
            f"{T.esc(section['title'])}</div>"
            f"<ol style='margin:4px 0 0 20px;padding:0;color:#475467;'>{items}</ol></div>")
    return f"<strong>Next:</strong> {T.esc(detail['headline'])}{intro}{''.join(sections)}"


def _next_plain(model):
    detail = _next_detail(model)
    lines = ["NEXT: " + detail["headline"]]
    if detail.get("intro"):
        lines.append(detail["intro"])
    for section in detail.get("sections", []):
        lines.append(section["title"] + ":")
        lines.extend(f"  {index}. {item}" for index, item in enumerate(section.get("items", []), 1))
    return lines


_NEXT_SECTION_TITLES = (
    "Evidence action",
    "MRWP UI action",
    "Authenticator ECS action",
    "Release-owner follow-up",
)


def _split_numbered_items(text: str) -> list[str]:
    matches = list(re.finditer(r"(?:^|\s)(\d+)\)\s+", text or ""))
    if not matches:
        return [text.strip()] if str(text or "").strip() else []
    items = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        item = text[start:end].strip()
        if item:
            items.append(item)
    return items


def _parse_next_action(next_action: str) -> dict:
    text = str(next_action or "").strip()
    positions = sorted(
        (text.find(title + ": "), title)
        for title in _NEXT_SECTION_TITLES
        if (title + ": ") in text
    )
    positions = [(pos, title) for pos, title in positions if pos >= 0]
    if not positions:
        return {"headline": text, "intro": "", "sections": []}
    first, _title = positions[0]
    prefix = text[:first].strip()
    headline, intro = prefix, ""
    split_at = prefix.find(". ")
    if split_at >= 0:
        headline = prefix[:split_at + 1].strip()
        intro = prefix[split_at + 2:].strip()
    sections = []
    for index, (pos, title) in enumerate(positions):
        start = pos + len(title) + 2
        end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
        sections.append({"title": title, "items": _split_numbered_items(text[start:end].strip())})
    return {"headline": headline, "intro": intro, "sections": sections}


def _next_html(next_action: str):
    detail = _parse_next_action(next_action)
    intro = f"<p style='margin:6px 0;color:#475467;'>{_link_urls_html(detail.get('intro', ''))}</p>" if detail.get("intro") else ""
    sections = []
    for section in detail.get("sections", []):
        items = "".join(
            f"<li style='margin:4px 0;'>{_link_urls_html(item)}</li>"
            for item in section.get("items", []))
        sections.append(
            f"<div style='margin-top:10px;'><div style='font-weight:700;color:#1d2939;'>"
            f"{T.esc(section['title'])}</div>"
            f"<ol style='margin:4px 0 0 20px;padding:0;color:#475467;'>{items}</ol></div>")
    return (
        f"<strong>Next:</strong> {T.esc(detail['headline'])}"
        f"{intro}{''.join(sections)}"
    )


def _next_plain(next_action: str):
    detail = _parse_next_action(next_action)
    lines = ["NEXT: " + detail["headline"]]
    if detail.get("intro"):
        lines.append(detail["intro"])
    for section in detail.get("sections", []):
        lines.append(section["title"] + ":")
        lines.extend(f"  {index}. {item}" for index, item in enumerate(section.get("items", []), 1))
    return lines


def recovered_tests(model) -> list:
    """Complete successful retry list, retaining suite and provider identity."""
    out = []
    for test in (model.get("ui_evidence") or {}).get("recovered", []):
        attempts = ", ".join(f"{count} {outcome}" for outcome, count in sorted(test["outcomes"].items()))
        out.append(f"[{test['provider']}] {test['suite']} — {test['title']} "
                   f"({attempts} historical attempts; informational)")
    return sorted(out)


def source_evidence_lines(model):
    """Render prepared facts only: no acquisition, reconciliation or target-plan projection."""
    facts = model.get("ui_evidence")
    if not isinstance(facts, dict):
        return ["UI source evidence unavailable: refresh the RC report preparation."]
    lines = list(facts["issues"])
    for provider in facts["providers"]:
        lines.append(f"Broker {provider['flight']}: {provider['distinct_tests']} distinct UI+Stress tests; "
                     f"{provider['source_executions']} source executions.")
    if facts["policy"]:
        lines.append(facts["policy"])
    if facts["auth"]:
        auth = facts["auth"]
        lines.append(f"Authenticator: {auth['source_executions']} source executions; "
                     f"{auth['distinct_tests']} distinct tests.")
    for failure in facts["failures"]:
        policy = "; intentional_report_only" if failure.get("report_only") else ""
        category = _CATEGORY_LABEL.get(failure.get("category"))
        lines.append(f"INVESTIGATE {failure['product']} {failure['provider']} "
                     + (f"{category} " if category else "") +
                     f"[{failure['suite']}{policy}] {failure['title']}")
        lines.extend(f"Source {link['run_id']}/{link['result_id']}: {link['url']}"
                     for link in failure["links"])
    return lines


def source_result_links_html(links):
    return " &middot; ".join(
        f"<a href='{T.esc(link['url'])}' style='color:#0b5cad;'>"
        f"Source {T.esc(link['run_id'])}/{T.esc(link['result_id'])}</a>"
        for link in sorted(links, key=lambda l: (l["run_id"], l["result_id"], l["url"])))


def _auth_suite_label(name: str) -> str:
    return str(name).replace("Firebase Test Lab - ", "")


def _auth_failures_by_suite(model):
    facts = model.get("ui_evidence")
    if (not isinstance(facts, dict) or not isinstance(facts.get("auth"), dict)
            or not isinstance(facts.get("failures"), list)):
        return None
    groups = {}
    for failure in facts["failures"]:
        if failure["product"] == "Authenticator":
            groups.setdefault(failure["suite"], []).append(failure)
    return groups


def auth_failure_details_plain(model):
    """Plain-text Authenticator failure details, grouped like MRWP failed suites."""
    groups = _auth_failures_by_suite(model)
    if groups is None:
        return ["  Detailed Authenticator failure evidence unavailable; refresh Phase-2 Authenticator verification."]
    suites = ((model.get("auth") or {}).get("test") or {}).get("suites") or {}
    lines = []
    names = [*AUTH_UI_SUITES, *sorted(name for name in groups if name not in AUTH_UI_SUITES)]
    for name in names:
        suite = suites.get(name) or {}
        label = _auth_suite_label(name)
        failures = sorted(groups.get(name, []), key=lambda f: f["title"])
        if not suite.get("present") and not failures:
            lines.append(f"  {label}: no result")
            continue
        passed, failed = suite.get("passed", 0) or 0, suite.get("failed", 0) or 0
        if not failed and not failures:
            continue
        denom = passed + failed
        pct = auth_pass_pct(suite)
        lines.append(
            f"  {label}: {passed}/{denom} passed"
            f" ({'n/a' if pct is None else f'{pct:.2f}%'})")
        if not failures:
            if failed:
                lines.append("      no unresolved failing titles after same-title retry reconciliation")
            continue
        suffix = " (report-only; no test-plan case map)" if name == "Firebase Test Lab - Monthly UI Tests" else ""
        lines.append(f"      {len(failures)} unresolved failing title(s){suffix}:")
        for failure in failures:
            lines.append(f"      - {failure['title']}")
            lines.extend(
                f"          Source {link['run_id']}/{link['result_id']}: {link['url']}"
                for link in sorted(
                    failure["links"], key=lambda l: (l["run_id"], l["result_id"], l["url"])))
    return lines


def auth_failure_details_html(model):
    """Render Authenticator suites with MRWP-like counts, failing titles and source links."""
    groups = _auth_failures_by_suite(model)
    if groups is None:
        return "<p>Detailed Authenticator failure evidence unavailable; refresh Phase-2 Authenticator verification.</p>"
    suites = ((model.get("auth") or {}).get("test") or {}).get("suites") or {}
    sections = []
    names = [*AUTH_UI_SUITES, *sorted(name for name in groups if name not in AUTH_UI_SUITES)]
    for name in names:
        suite = suites.get(name) or {}
        label = _auth_suite_label(name)
        failures = sorted(groups.get(name, []), key=lambda f: f["title"])
        if not suite.get("present") and not failures:
            sections.append(
                f"<div style='margin:9px 0 0;font-size:12px;color:#667085;'>"
                f"{T.esc(label)}: no result.</div>")
            continue
        passed, failed = suite.get("passed", 0) or 0, suite.get("failed", 0) or 0
        if not failed and not failures:
            continue
        denom = passed + failed
        pct = auth_pass_pct(suite)
        fail_pct = _fail_rate(failed, denom)
        items = []
        for failure in failures:
            links = source_result_links_html(failure["links"])
            items.append(
                f"<li style='margin:4px 0;color:#475467;'>"
                f"<span style='font-family:Consolas,ui-monospace,monospace;'>{T.esc(failure['title'])}</span>"
                f"<div style='font-size:12px;'>{links}</div></li>")
        report_only = name == MONTHLY_REPORT_ONLY
        tag = (" <span style='font-size:12px;color:#0b5cad;'>(report-only; no test-plan case map)</span>"
               if report_only else "")
        note_html = (
            f"All <strong>{len(failures)}</strong> unresolved failing titles. "
            "Auth gate counts use source executions, not distinct titles."
            if failures else
            (T.esc("No unresolved failing titles after same-title retry reconciliation. "
                   "The source-execution failure count and gate percentage above are unchanged.")
             if failed else "No unresolved failing titles.")
        )
        item_list = (
            f"<ul style='margin:2px 0 0 18px;padding:0;font-size:12px;'>{''.join(items)}</ul>"
            if failures else "")
        sections.append(
            f"<div style='margin:9px 0 0;'>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
            f"<td style='font-size:13px;font-weight:600;color:#1d2939;'>{T.esc(label)}{tag}</td>"
            f"<td align='right' style='font-size:13px;white-space:nowrap;'>"
            f"<strong style='color:#b42318;'>{failed}</strong>"
            f"<span style='color:#98a2b3;'>/{denom}</span> "
            f"<span style='color:#b42318;font-weight:600;'>&middot; {fail_pct}%</span>"
            f" failed source executions</td></tr></table>"
            f"<div style='font-size:12px;color:#667085;'>{note_html}</div>"
            f"{item_list}</div>")
    return "".join(sections)


def mrwp_leg_summary(model, provider) -> dict:
    """Provider-only display metrics; the owning step's release gate policy is unchanged."""
    tests = ((model.get("mrwp") or {}).get(provider) or {}).get("tests") or {}
    try:
        ui = ui_stress_counts(tests)
    except ValueError:
        ui = None
    if (tests.get("count_basis") != MRWP_COUNT_BASIS or not valid_counts(ui)
            or ui["total"] != ui["passed"] + ui["failed"]):
        return {"available": False, "pass_pct": None, "fail_pct": None,
                "headline": "evidence unavailable", "detail": "Refresh UI+Stress verification"}
    passed, failed, total = ui["passed"], ui["failed"], ui["total"]
    pct = round(passed * 100.0 / total, 1)
    return {"available": True, "passed": passed, "failed": failed, "total": total,
            "pass_pct": pct, "fail_pct": _fail_rate(failed, total),
            "headline": f"{pct}% pass (UI+Stress)",
            "detail": f"{failed} failed / {total} distinct UI+Stress tests"}


def auth_leg_summary(model, auth) -> dict:
    """Compact Authenticator-ECS verdict for the RC report's at-a-glance surfaces (email
    subject + the gates banner). Reads the stored auth section (model['auth']) and the
    report's evaluated auth gate. Returns {present, verdict, blocking, headline, worst}."""
    a = model.get("auth") or {}
    if not a:
        return {"present": False, "verdict": "unavailable", "blocking": True,
                "headline": "not evaluated", "worst": None}
    result = (a.get("build") or {}).get("result")
    if result in ("failed", "canceled") or auth["verdict"] == "unavailable":
        return {"present": True, "verdict": auth["verdict"], "blocking": True,
                "headline": f"build {result}" if result in ("failed", "canceled") else
                           "evidence unavailable", "worst": None}
    suites = (a.get("test") or {}).get("suites") or {}
    verdict = auth["verdict"]
    worst = None                                  # (short_name, pct|None) — missing or lowest
    for name in AUTH_UI_SUITES:
        s = suites.get(name) or {}
        short = name.split(" - ")[-1]
        pct = auth_pass_pct(s)
        if not s.get("present") or pct is None:
            worst = (short, None)
            break
        if worst is None or pct < worst[1]:
            worst = (short, pct)
    if verdict == "clean":
        headline = f"both suites >= {AUTH_UI_PASS_THRESHOLD:.0f}%"
    elif worst and worst[1] is None:
        headline = f"{worst[0]}: no result"
    elif worst:
        headline = f"{worst[0]} {worst[1]:.2f}%"
    else:
        headline = f"< {AUTH_UI_PASS_THRESHOLD:.0f}%"
    return {"present": True, "verdict": verdict, "blocking": verdict != "clean",
            "headline": headline, "worst": worst}


def rc_email_subject(model, gate, auth) -> str:
    rid = model.get("release", "?")
    subject = f"Release {rid} — RC verification report (Phase 2)"
    for provider in ("ECS", "Local"):
        subject += f" · MRWP {provider}: {mrwp_leg_summary(model, provider)['headline']}"
    summary = auth_leg_summary(model, auth)
    verdict = ("pass" if summary["verdict"] == "clean" else
               "unavailable" if summary["verdict"] == "unavailable" else "HOLD")
    worst = summary["worst"]
    rate = f"{worst[1]:.1f}% lowest-suite pass" if worst and worst[1] is not None else summary["headline"]
    subject += f" · Auth ECS: {verdict} ({rate})"
    return subject


def _fail_rate(failed, total) -> float:
    """Failure percentage (1 decimal). 0 when total is 0/None."""
    try:
        return round((failed or 0) * 100.0 / total, 1) if total else 0.0
    except (TypeError, ZeroDivisionError):
        return 0.0


# Test-run category labels (mirrors tools.pipelines.classify_test_run).
_CAT_LABEL = _CATEGORY_LABEL

# Failing-suite display order: UI first (the RC-critical bucket), then instrumented, unit.
_SUITE_ORDER = {"ui": 0, "stress": 1, "instrumented": 2, "unit": 3}

COUNT_NOTE = ("MRWP counts are distinct tests: one exact title per normalized suite within "
              "each current-RC build/provider; parameterizations and API/device suites stay separate. "
              "Any Passed attempt wins, even before a later failure. The denominator is "
              "passed + failed; NA-only tests are excluded. Historical attempts are informational, "
              "not additional gate failures. Stale snapshots require a fresh verification read.")


def suite_count_label(suite):
    return "distinct tests"


def suite_failure_note(suite):
    if suite.get("count_basis") != MRWP_COUNT_BASIS:
        return "Stale/unreconciled counts; refresh verification."
    names = suite.get("tests") or []
    note = f"All {len(names)} unresolved failing titles (none ever passed in this suite/build)."
    note += f" Source: {suite.get('result_entries', 0)} execution entries before reconciliation."
    ids = suite.get("run_ids") or []
    if ids:
        note += " Test runs: " + ", ".join(str(i) for i in ids) + "."
    return note


def failure_test_names(suite):
    yield from suite.get("tests") or []


def failure_evidence_error(run):
    if run.get("tests_error"):
        return "Test summary unavailable: " + run["tests_error"]
    if run.get("failed_suites_error"):
        return "Failure details unavailable: " + run["failed_suites_error"]
    if (run.get("tests") or {}).get("count_basis") != MRWP_COUNT_BASIS:
        return "Stale/unreconciled test counts unavailable; refresh verification for pass-any evidence."
    if (run.get("tests") or {}).get("failed") and run.get("failed_suites") is None:
        return "Failure details unavailable; refresh verification for a complete list."
    return ""


def sort_failed_suites(suites):
    """Failing suites ordered UI-first then instrumented/unit, each by descending failure
    count. One helper so every RC renderer (plain email, HTML email, CLI report) lists
    them identically."""
    return sorted(suites or [],
                  key=lambda s: (_SUITE_ORDER.get(s.get("category", "ui"), 9),
                                 -(s.get("failed") or 0), s["name"]))


def rc_email_plain(model, ctx, gate, auth, next_action) -> str:
    """Plain-text form of the RC report email (fallback + logging)."""
    L = []
    rid = model.get("release", "?")
    o = model.get("orchestrator") or {}
    vstr = format_versions(o.get("versions"), fallback="n/a")
    L.append(f"Hi {ctx.get('owner', 'there')},")
    L.append("")
    L.append(f"Captured RC verification results for {rid}.")
    L.append("RESULTS (shown separately)")
    for provider in ("ECS", "Local"):
        summary = mrwp_leg_summary(model, provider)
        rate = f"; {summary['fail_pct']}% fail" if summary["available"] else ""
        L.append(f"  - MRWP {provider}: {summary['headline']}{rate}; {summary['detail']}")
    _auth = auth_leg_summary(model, auth)
    label = ("pass" if _auth["verdict"] == "clean" else
             "unavailable" if _auth["verdict"] == "unavailable" else "HOLD")
    L.append(f"  - Authenticator ECS: {label} ({_auth['headline']})")
    L.append("")
    L.extend(_next_plain(next_action))
    L.append("")
    ch = model.get("checker") or {}
    L.append("PIPELINE HEALTH")
    L.append(f"  - Checker: fired the release (run {ch.get('run_id')}) — {build_url(ch.get('run_id'))}")
    park = ("parked at 'Remove RC Tags' (awaiting approval, later phase)"
            if o.get("parked") else "gate already cleared")
    L.append(f"  - Orchestrator: healthy — pre-gate stages green, {park} — {build_url(o.get('run_id'))}")
    L.append(f"  - Versions: {vstr}")
    L.append("")
    L.append("RC TESTING — captured results by category:")
    L.append("  " + COUNT_NOTE)
    for prov in ("ECS", "Local"):
        r = (model.get("mrwp") or {}).get(prov) or {}
        t = r.get("tests") or {}
        current = t.get("count_basis") == MRWP_COUNT_BASIS
        cats = (t.get("categories") or {}) if current else {}
        L.append(f"  MRWP {prov} — run {r.get('run_id')} ({r.get('ran')}/{r.get('total')} stages)")
        for cat in TEST_CATEGORIES:
            c = cats.get(cat) or {}
            if not c.get("total"):
                if c.get("na"):
                    L.append(f"      {_CAT_LABEL[cat]:14} 0 evaluated · {c['na']} NA · rate unavailable")
                continue
            L.append(f"      {_CAT_LABEL.get(cat, cat):14} {c.get('passed')}/{c.get('total')} passed"
                     f" · {c.get('failed')} failed · {c['passed'] * 100 / c['total']:.2f}% pass"
                     f" · {_fail_rate(c.get('failed'), c.get('total'))}% fail"
                     + (f" · {c['na']} NA" if c.get("na") else ""))
        summary = mrwp_leg_summary(model, prov)
        L.append(f"      UI+Stress RC gate coverage: {summary['headline']}; {summary['detail']}")
        fs = r.get("failed_stages") or []
        if fs:
            L.append(f"      Red stages ({len(fs)}): {', '.join(fs)}")
        for s in sort_failed_suites(r.get("failed_suites") if current else []):
            sr = _fail_rate(s["failed"], s["total"])
            L.append(f"      [{_CAT_LABEL.get(s.get('category', 'ui'), 'UI automation')}] "
                     f"{s['name']} — {s['failed']}/{s['total']} failed "
                     f"{suite_count_label(s)} ({sr}%):")
            L.append("          " + suite_failure_note(s))
            for tname in failure_test_names(s):
                L.append(f"          - {tname}")
        if failure_evidence_error(r):
            L.append("      " + failure_evidence_error(r))
        L.append(f"      Run: {build_url(r.get('run_id'))}")
        L.append("")
    a = model.get("auth") or {}
    if a:
        b, t = a.get("build") or {}, a.get("test") or {}
        suites = t.get("suites") or {}
        verdict = ("PASS (both suites >= %.0f%%)" % AUTH_UI_PASS_THRESHOLD
                   if auth["verdict"] == "clean" else "HOLD")
        L.append(f"AUTHENTICATOR ECS — each Firebase suite evaluated separately: {verdict}")
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
                     f"({'n/a' if pct is None else f'{pct:.2f}% pass; {_fail_rate(failed, passed + failed)}% fail'})")
        L.extend(auth_failure_details_plain(model))
        L.append(f"      Run: {auth_build_url(b.get('run_id'))}")
        L.append("")
    L.append("SOURCE EVIDENCE / RELEASE-OWNER INVESTIGATION")
    L.extend(source_evidence_lines(model))
    probs = model.get("problems") or []
    if probs:
        L.append("PIPELINE ISSUES / INCOMPLETE EVIDENCE:")
        L += [f"  - {p}" for p in probs]
        L.append("")
    recovered = recovered_tests(model)
    if recovered:
        L.append(f"\u26a0 RETRY WARNING — {len(recovered)} recovered test(s): SUCCESS "
                 f"(at least one Passed and Failed attempt, in any order; counted once as passed):")
        L += [f"  - {t}" for t in recovered]
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
    auth_summary = auth_leg_summary(model, auth)
    broker_failure_links = {
        (f["provider"], f["suite"], f["title"]): f["links"]
        for f in (model.get("ui_evidence") or {}).get("failures", [])
        if f["product"] == "Broker"}
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

    def _rate_color(rate):
        if rate is None:
            return "#667085"
        return "#b42318" if rate >= 5 else "#b54708" if rate > 0 else "#067647"

    def _cat_row(cat, c):
        total = c.get("total") or 0
        if not total:
            if c.get("na"):
                return (f"<tr><td style='padding:5px 0;font-size:13px;'>{_CAT_LABEL[cat]}</td>"
                        f"<td colspan='3' style='font-size:12px;color:#667085;'>"
                        f"0 evaluated · {c['na']} NA · rate unavailable</td></tr>")
            return ""
        passed, failed = c.get("passed") or 0, c.get("failed") or 0
        fr = _fail_rate(failed, total)
        is_ui = cat == "ui"
        lbl_style = "font-weight:700;color:#101828;" if is_ui else "color:#475467;"
        na = f" · {c['na']} NA" if c.get("na") else ""
        pct_color = "#b42318" if fr >= 1 else ("#b54708" if fr > 0 else "#067647")
        return (f"<tr>"
                f"<td style='padding:5px 0;font-size:13px;{lbl_style}'>{_CAT_LABEL.get(cat, cat)}{na}</td>"
                f"<td style='padding:5px 10px;font-size:12px;color:#98a2b3;white-space:nowrap;'>{passed}/{total}</td>"
                f"<td width='130' style='padding:5px 0;'>{_split_bar(passed * 100 / total, h=6)}</td>"
                f"<td align='right' style='padding:5px 0 5px 10px;font-size:13px;font-weight:700;"
                f"color:{pct_color};white-space:nowrap;'>{passed * 100 / total:.2f}% pass"
                f" &middot; {fr}% fail</td></tr>")

    def mrwp_card(prov):
        r = (model.get("mrwp") or {}).get(prov) or {}
        if not valid_id(r.get("run_id")):
            return f"<p>MRWP {prov}: evidence unavailable — no run captured.</p>"
        t = r.get("tests") or {}
        if t.get("count_basis") != MRWP_COUNT_BASIS:
            return f"<p>MRWP {prov} run {r.get('run_id')}: {T.esc(failure_evidence_error(r))}</p>"
        cats = t.get("categories") or {}
        try:
            ui = ui_stress_counts(t)
        except ValueError:
            ui = {}
        ui_total, ui_pass, ui_fail = ui.get("total") or 0, ui.get("passed") or 0, ui.get("failed") or 0
        summary = mrwp_leg_summary(model, prov)
        fs = r.get("failed_stages") or []
        red = (f"<div style='margin:8px 0 0;color:#b42318;font-size:12px;'>Red stages "
               f"({len(fs)}): {T.esc(', '.join(fs))}</div>" if fs else "")

        cat_table = "".join(_cat_row(c, cats.get(c) or {}) for c in
                            TEST_CATEGORIES)

        # Failing suites — UI first, then instrumented/unit; each tagged by category.
        suites = sort_failed_suites(r.get("failed_suites"))
        suite_html = ""
        for s in suites:
            sr = _fail_rate(s["failed"], s["total"])
            items = []
            for name in failure_test_names(s):
                links = source_result_links_html(broker_failure_links.get((prov, s["name"], name), []))
                detail = f"<div style='font-size:12px;'>{links}</div>" if links else ""
                items.append(f"<li style='margin:1px 0;color:#475467;'>{T.esc(name)}{detail}</li>")
            tag = _chip(_CAT_LABEL.get(s.get("category", "ui"), "UI automation"), "#eef4ff", "#0b5cad")
            suite_html += (
                f"<div style='margin:9px 0 0;'>"
                f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
                f"<td style='font-size:13px;font-weight:600;color:#1d2939;'>{T.esc(s['name'])} &nbsp;{tag}</td>"
                f"<td align='right' style='font-size:13px;white-space:nowrap;'>"
                f"<strong style='color:#b42318;'>{s['failed']}</strong>"
                f"<span style='color:#98a2b3;'>/{s['total']}</span> "
                f"<span style='color:#b42318;font-weight:600;'>&middot; {sr}%</span>"
                f" failed {suite_count_label(s)}</td></tr></table>"
                f"<div style='font-size:12px;color:#667085;'>{T.esc(suite_failure_note(s))}</div>"
                f"<ul style='margin:2px 0 0 18px;padding:0;font-size:12px;"
                f"font-family:Consolas,ui-monospace,monospace;'>{''.join(items)}</ul></div>")
        if failure_evidence_error(r):
            suite_html += f"<p>{T.esc(failure_evidence_error(r))}</p>"

        rate_color = _rate_color(summary["fail_pct"])
        return (
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid #e4e7ec;border-radius:10px;margin:10px 0;'>"
            f"<tr><td style='padding:14px 16px;'>"
            f"<table role='presentation' width='100%'><tr>"
            f"<td style='font-size:15px;font-weight:700;color:#101828;'>MRWP {prov}"
            f"<span style='color:#98a2b3;font-weight:400;font-size:13px;'> &middot; run {r.get('run_id')} "
            f"&middot; {r.get('ran')}/{r.get('total')} stages</span></td>"
            f"<td align='right'>{_chip('completed' if r.get('complete') is True else 'incomplete', '#eef0f3', '#475467')}</td></tr></table>"
            # Headline uses success; failure counts remain explicit alongside it.
            f"<div style='margin:10px 0 2px;'>"
            f"<span style='font-size:26px;font-weight:800;color:{rate_color};'>{str(summary['pass_pct']) + '%' if summary['available'] else 'unavailable'}</span>"
            f"<span style='font-size:13px;color:#667085;'> UI+Stress success rate (RC gate coverage) &nbsp;·&nbsp; "
            f"<strong style='color:#12b76a;'>{ui_pass}</strong> passed / "
            f"<strong style='color:#b42318;'>{ui_fail}</strong> failed of {ui_total} distinct UI+Stress tests</span></div>"
            f"{_split_bar(ui_pass * 100 / ui_total) if summary['available'] else ''}"
            # per-category breakdown
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='margin:10px 0 0;border-top:1px solid #eef0f3;'>{cat_table}</table>"
            f"{red}{suite_html}"
            f"<div style='margin-top:10px;'><a href='{build_url(r.get('run_id'))}' "
            f"style='color:#0b5cad;font-size:13px;'>Open run {r.get('run_id')} &rsaquo;</a></div>"
            f"</td></tr></table>")

    def _auth_card():
        """Both Firebase suites retain their own rates; never average away a failing suite."""
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
            disp = ("no result" if not present else
                    f"{pct:.2f}% pass &middot; {_fail_rate(failed, denom)}% fail"
                    if pct is not None else "n/a")
            rows += (
                f"<tr><td style='padding:5px 0;font-size:13px;color:#1d2939;'>{T.esc(name)}</td>"
                f"<td style='padding:5px 10px;font-size:12px;color:#98a2b3;white-space:nowrap;'>"
                f"{passed}/{denom}</td>"
                f"<td width='130' style='padding:5px 0;'>"
                f"{_split_bar(pct, h=6) if pct is not None else ''}</td>"
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
        worst = auth_summary["worst"]
        worst_rate = round(worst[1], 1) if worst and worst[1] is not None else None
        headline = (
            f"<div data-auth-success-rate style='margin:10px 0 2px;'>"
            f"<span style='font-size:26px;font-weight:800;color:{_rate_color(100 - worst[1])};'>{worst_rate}%</span>"
            f"<span style='font-size:13px;color:#667085;'> UI-automation success rate "
            f"(worst gated suite: {T.esc(worst[0])})</span></div>"
            if worst and worst[1] is not None else
            f"<p style='color:#667085;'>UI-automation success rate unavailable: "
            f"{T.esc(auth_summary['headline'])}</p>")
        return (
            f"<p style='margin:16px 0 2px;font-size:15px;font-weight:700;'>Authenticator ECS "
            f"<span style='color:#98a2b3;font-weight:400;font-size:13px;'>&middot; separate gate "
            f"(each Firebase suite must pass independently)</span></p>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='border:1px solid #e4e7ec;border-radius:10px;margin:6px 0;'>"
            f"<tr><td style='padding:14px 16px;'>"
            f"<table role='presentation' width='100%'><tr>"
            f"<td style='font-size:14px;font-weight:700;color:#101828;'>Firebase device suites"
            f"{ver_span}</td>"
            f"<td align='right'>{chip}</td></tr></table>"
            f"{headline}"
            f"<p style='margin:4px 0;font-size:12px;color:#667085;'>"
            f"Source-execution rates per suite; not combined with MRWP or averaged across suites.</p>"
            f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
            f"style='margin:8px 0 0;border-top:1px solid #eef0f3;'>{rows}</table>"
            f"{auth_failure_details_html(model)}"
            f"{link}</td></tr></table>")

    def _gates_banner():
        cells = ""
        for provider in ("ECS", "Local"):
            summary = mrwp_leg_summary(model, provider)
            colors = (("#ecfdf3", "#067647") if summary.get("fail_pct") == 0 else
                     ("#fffaeb", "#b54708") if summary["available"] else ("#eef0f3", "#475467"))
            text = T.esc(f"MRWP {provider}: {summary['headline']}")
            cells += f"<td style='padding:0 8px 0 0;'>{_chip(text, *colors)}</td>"
        acol = (("#ecfdf3", "#067647") if auth_summary["verdict"] == "clean" else
                ("#eef0f3", "#475467") if auth_summary["verdict"] == "unavailable" else ("#fef3f2", "#b42318"))
        atxt = "Auth ECS: " + ("pass" if auth_summary["verdict"] == "clean" else
                              "unavailable" if auth_summary["verdict"] == "unavailable" else "HOLD")
        cells += f"<td style='padding:0 8px;'>{_chip(atxt, *acol)}</td>"
        return ("<table role='presentation' cellpadding='0' cellspacing='0' style='margin:12px 0 0;'>"
                f"<tr>{cells}</tr></table>")

    def _results_header():
        cells = []
        for provider in ("ECS", "Local"):
            summary = mrwp_leg_summary(model, provider)
            rate = f"{summary['pass_pct']}%" if summary["available"] else "unavailable"
            color = _rate_color(summary["fail_pct"])
            cells.append(
                f"<td data-result='mrwp-{provider.lower()}' width='33%' "
                f"style='padding:12px 16px;border-right:1px solid #eef0f3;vertical-align:top;'>"
                f"<div style='font-size:13px;font-weight:700;'>MRWP {provider}</div>"
                f"<div style='font-size:22px;font-weight:800;color:{color};'>{rate}</div>"
                f"<div style='font-size:12px;color:#667085;'>UI+Stress success rate</div>"
                f"<div style='font-size:12px;color:#98a2b3;'>{summary['detail']}</div></td>")
        worst = auth_summary["worst"]
        failure_pct = round(100 - worst[1], 1) if worst and worst[1] is not None else None
        rate = f"{worst[1]:.1f}%" if worst and worst[1] is not None else "unavailable"
        detail = f"Worst suite: {worst[0]}" if worst and worst[1] is not None else auth_summary["headline"]
        cells.append(
            f"<td data-result='auth-ecs' width='34%' style='padding:12px 16px;vertical-align:top;'>"
            f"<div style='font-size:13px;font-weight:700;'>Authenticator ECS</div>"
            f"<div style='font-size:22px;font-weight:800;color:{_rate_color(failure_pct)};'>{rate}</div>"
            f"<div style='font-size:12px;color:#667085;'>UI-automation success rate</div>"
            f"<div style='font-size:12px;color:#98a2b3;'>{T.esc(detail)}</div></td>")
        return "".join(cells)

    probs = model.get("problems") or []
    issues = (("<div style='margin:12px 0;padding:10px 12px;background:#fef3f2;border:1px solid #fda29b;"
               "border-radius:8px;color:#b42318;'><strong>Pipeline issues / incomplete evidence</strong>"
               "<ul style='margin:6px 0 0 18px;'>"
               + "".join(f"<li>{T.esc(p)}</li>" for p in probs) + "</ul></div>")
              if probs else "")

    recovered = recovered_tests(model)
    retry_warn = ""
    if recovered:
        retry_warn = (
            "<div style='margin:12px 0;padding:10px 12px;background:#fffaeb;border:1px solid #fedf89;"
            "border-radius:8px;color:#b54708;'><strong>&#9888; Retry warning</strong> &mdash; "
            f"{len(recovered)} recovered test(s): <strong>SUCCESS</strong> (at least one Passed "
            "and Failed attempt, in any order; counted once as passed):"
            "<ul style='margin:6px 0 0 18px;font-family:Consolas,ui-monospace,monospace;font-size:12px;'>"
            + "".join(f"<li>{T.esc(t)}</li>" for t in recovered) + "</ul></div>")

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
    <tr>{_results_header()}</tr>
  </table>

  <table data-report-metadata role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0;font-size:12px;color:#667085;">
    <tr data-metadata="checker">
      <td style="padding:4px 12px 4px 0;font-weight:700;white-space:nowrap;vertical-align:top;">Checker</td>
      <td style="padding:4px 0;">&#10003; Fired
        (<a href="{build_url(ch.get('run_id'))}" style="color:#0b5cad;">run {ch.get('run_id')}</a>)</td>
    </tr>
    <tr data-metadata="orchestrator">
      <td style="padding:4px 12px 4px 0;font-weight:700;white-space:nowrap;vertical-align:top;">Orchestrator</td>
      <td style="padding:4px 0;">&#10003; Healthy &mdash; {park}
        (<a href="{build_url(o.get('run_id'))}" style="color:#0b5cad;">run {o.get('run_id')}</a>)</td>
    </tr>
    <tr data-metadata="versions">
      <td style="padding:4px 12px 4px 0;font-weight:700;white-space:nowrap;vertical-align:top;">Versions</td>
      <td style="padding:4px 0;"><strong>{T.esc(vstr)}</strong></td>
    </tr>
  </table>

  <p style="margin:16px 0 2px;font-size:15px;font-weight:700;">Broker UI and Stress results (separate categories; combined RC gate)</p>
  <p style="font-size:12px;color:#667085;">{T.esc(COUNT_NOTE)}</p>
  {mrwp_card('ECS')}
  {mrwp_card('Local')}
  {_auth_card()}
  {issues}
  {retry_warn}

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:14px 0;border-radius:8px;background:#f9fafb;border:1px solid #eef0f3;">
    <tr><td style="padding:12px 16px;">
      {_next_html(next_action)}
    </td></tr>
  </table>
  <p style="color:#98a2b3;font-size:12px;">&mdash; Release Orchestrator (Scout)</p>
</div>"""
