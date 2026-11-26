"""Unified external partner email composer, renderer, and 24h status monitor.

Powers all 5 Broker / Auth Client Android SDK external email touchpoints using ONE
consistent HTML card template and ONE consolidated distribution list (`status_email.recipients`):
  1. Phase 0 (`preflight.notice`, stage='preflight')   — approaching release & Code Complete ETA (no versions/changelogs)
  2. Phase 1 (`ccd.final_reminder`, stage='ccd')       — Code Complete In Progress Today (no versions/changelogs)
  3. Phase 2 (`build_verify`, stage='daily')           — Automated Test Pass In Progress / Blocked (+ SDK versions)
  4. Phase 3 (`bug_bash`, stage='daily')               — Manual Test Pass In Progress / Blocked (+ SDK versions)
  5. Phase 4 (`finalize`, stage='daily' & 'final')     — Final Release builds published; `final_status_email` marks all
                                                         4 milestones Complete and includes Common, MSAL, & Broker changelogs.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from orchestrator import schedule
from steps.lib import templating as T


# Window for automated P2-P4 status monitoring: build_verify (2) <= current phase < rollout_start (5).
WINDOW_START = "build_verify"
WINDOW_END = "rollout_start"          # exclusive — Phase 5 onward: stop.

# Status vocabulary → (label, pill background, pill text color).
_PILL = {
    "complete":    ("✅ Complete",    "#d1fadf", "#027a48"),
    "in_progress": ("🟡 In Progress", "#fef0c7", "#b54708"),
    "scheduled":   ("📅 Scheduled",   "#d1e9ff", "#175cd3"),
    "blocked":     ("🔴 Blocked",     "#fee4e2", "#b42318"),
    "not_started": ("⬜ Not Started", "#f2f4f7", "#475467"),
}

SDK_ORDER = (
    ("common", "Common"),
    ("msal", "MSAL"),
    ("broker", "Broker"),
)

HOTFIX_POLICY_NOTE = (
    "Any check-ins made after code complete will require following "
    "the hotfix cherry-pick process and EM approval."
)


# ---- milestone model ------------------------------------------------------

def _step(state, phase, step):
    try:
        return state.get_step(phase, step)
    except Exception:  # noqa: BLE001
        return None


def _is_done(state, phase, step, selection=None) -> bool:
    try:
        ready = next((item for item in selection.steps
                      if item.definition.key == f"{phase}.{step}"), None) if selection else None
        return ready.complete if ready else state.is_done(phase, step)
    except Exception:  # noqa: BLE001
        return False


def _phase_index(phase_order, phase_id):
    try:
        return phase_order.index(phase_id)
    except (ValueError, AttributeError):
        return -1


def _step_status(state, phase, step, phase_order, selection=None):
    """Derive a partner-facing status for one of OUR steps: complete | blocked | in_progress | not_started."""
    st = _step(state, phase, step)
    if st is not None and getattr(st, "status", None) == "blocked":
        return "blocked"
    if _is_done(state, phase, step, selection):
        return "complete"
    frontier = selection.frontier if selection else None
    current_phase = frontier.id if frontier else None
    cur_i = _phase_index(phase_order, current_phase)
    ph_i = _phase_index(phase_order, phase)
    if cur_i >= ph_i >= 0:
        return "in_progress"
    return "not_started"


def _roll_up(state, steps, phase_order, selection=None):
    """Combine several of our steps into one milestone status."""
    statuses = [_step_status(state, p, s, phase_order, selection) for p, s in steps]
    if statuses and all(s == "complete" for s in statuses):
        return "complete"
    if any(s == "blocked" for s in statuses):
        return "blocked"
    if any(s in ("in_progress", "complete") for s in statuses):
        return "in_progress"
    return "not_started"


def _fmt_date(s):
    """'YYYY-MM-DD' or ISO -> 'MM/DD/YYYY'; '' on anything unparseable."""
    d = schedule.parse_date(str(s)[:10]) if s else None
    return d.strftime("%m/%d/%Y") if d else ""


def _release_branch_links(state):
    """[{text,url}] to each SDK library's release branch (`release/<version>`)."""
    from steps.finalize import integ_prs as IP
    v = getattr(state, "versions", None) or {}
    out = []
    for key, label in SDK_ORDER:
        ver = v.get(key)
        gh = (IP.CONFIG.get(key) or {}).get("gh_repo")
        if not (ver and gh):
            continue
        host = ("https://" + gh) if ("." in gh.split("/")[0]) else ("https://github.com/" + gh)
        out.append({"text": f"{label} {ver}", "url": f"{host}/tree/{IP.RELEASE_PREFIX}{ver}"})
    return out


def _github_release_links(state):
    """[{text,url}] to each SDK library's published GitHub release (`v<version>`)."""
    from steps.finalize import integ_prs as IP
    v = getattr(state, "versions", None) or {}
    out = []
    for key, label in SDK_ORDER:
        ver = v.get(key)
        gh = (IP.CONFIG.get(key) or {}).get("gh_repo")
        if not (ver and gh):
            continue
        host = ("https://" + gh) if ("." in gh.split("/")[0]) else ("https://github.com/" + gh)
        out.append({"text": f"{label} v{ver}", "url": f"{host}/releases/tag/v{ver}"})
    return out


def _rc_ui_summary(state):
    """A '{text}' item summarizing the combined RC UI+Stress pass rate (ECS + Local),
    from the stored latest-RC test data. None when not available."""
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    if not rcs:
        return None
    cur = rcs[-1]
    passed = total = 0
    from tools.pipelines import ui_stress_counts
    for slot in ("ecs", "local"):
        try:
            ui = ui_stress_counts((cur.get(slot) or {}).get("tests"))
        except ValueError:
            return {"text": "UI+Stress evidence unavailable; refresh RC verification"}
        passed += ui.get("passed", 0) or 0
        total += ui.get("total", 0) or 0
    if not total:
        return None
    pct = round(passed * 100.0 / total, 1)
    gate = "✅" if pct >= 90 else "🔴"
    return {"text": f"UI+Stress {pct}% ({passed}/{total}, gate ≥90% {gate})"}


def _getdata(state, phase, step):
    st = _step(state, phase, step)
    return getattr(st, "data", None) if st is not None else None


def _bugbash_date(state):
    """The bug-bash date, recomputed from send_invite's persisted completed_at."""
    st = _step(state, "bug_bash", "send_invite")
    ts = getattr(st, "completed_at", None) if st is not None else None
    if not ts:
        return ""
    try:
        from tools import invite as I
        dt = datetime.fromisoformat(str(ts).replace("Z", "").split(".")[0])
        start, _end, _when = I.schedule_bugbash(dt)
        return start.strftime("%m/%d/%Y")
    except Exception:  # noqa: BLE001
        return ""


def _rc_build_links(state):
    """[{text,url}] for the ECS + Local RC build runs (from the latest RC record)."""
    from tools.coordinates import coords
    eng = f"{coords.org_url('engineering')}/{coords.project('engineering')}"
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    if not rcs:
        return []
    cur = rcs[-1]
    out = []
    for slot, label in (("ecs", "ECS"), ("local", "Local Flights")):
        rid = (cur.get(slot) or {}).get("run_id")
        if rid:
            out.append({"text": label, "url": f"{eng}/_build/results?buildId={rid}&view=results"})
    return out


def milestones(
    state,
    phase_order,
    selection=None,
    *,
    stage: str = "daily",
    ccd_today: bool = False,
    stalled_phase: Optional[str] = None,
):
    """Build the 4 unified partner milestone rows:
      1. Code Complete
      2. Automated Test Pass
      3. Manual Test Pass
      4. Final Release builds published
    """
    ccd_str = _fmt_date(getattr(state, "ccd", None))
    owner_email = getattr(state, "owner_email", None) or ""
    owner_at = owner_email.split("@")[0] if owner_email else "release-owner"

    if stage == "preflight":
        return [
            {
                "id": "code_complete",
                "label": "Code Complete",
                "status": "scheduled",
                "date": f"ETA {ccd_str}" if ccd_str else "",
                "details": [{"text": f"Release Owner: @{owner_at}"}],
            },
            {"id": "automated_test_pass", "label": "Automated Test Pass", "status": "not_started", "date": "", "details": []},
            {"id": "manual_test_pass", "label": "Manual Test Pass", "status": "not_started", "date": "", "details": []},
            {"id": "final_published", "label": "Final Release builds published", "status": "not_started", "date": "", "details": []},
        ]

    if stage == "ccd":
        return [
            {
                "id": "code_complete",
                "label": "Code Complete",
                "status": "in_progress",
                "date": ccd_str,
                "today_highlight": bool(ccd_today),
                "details": [{"text": f"Release Owner: @{owner_at}"}],
            },
            {"id": "automated_test_pass", "label": "Automated Test Pass", "status": "not_started", "date": "", "details": []},
            {"id": "manual_test_pass", "label": "Manual Test Pass", "status": "not_started", "date": "", "details": []},
            {"id": "final_published", "label": "Final Release builds published", "status": "not_started", "date": "", "details": []},
        ]

    if stage == "final":
        rc_details = []
        ui = _rc_ui_summary(state)
        if ui:
            rc_details.append(ui)
        pub_details = _github_release_links(state) or _release_branch_links(state)
        return [
            {"id": "code_complete", "label": "Code Complete", "status": "complete", "date": ccd_str, "details": []},
            {"id": "automated_test_pass", "label": "Automated Test Pass", "status": "complete", "date": "", "details": rc_details},
            {"id": "manual_test_pass", "label": "Manual Test Pass", "status": "complete", "date": _bugbash_date(state), "details": []},
            {"id": "final_published", "label": "Final Release builds published", "status": "complete", "date": "", "details": pub_details},
        ]

    # `stage == "daily"` (live projection across Phases 2, 3, and 4)
    frontier = selection.frontier if selection else None
    cur_phase = frontier.id if frontier else None
    cur_idx = _phase_index(phase_order, cur_phase)

    # 1. Code Complete
    cc_done = (
        cur_idx >= _phase_index(phase_order, "build_verify") >= 0
        or _is_done(state, "ccd", "final_reminder", selection)
    )
    cc_status = "complete" if cc_done else _step_status(state, "ccd", "final_reminder", phase_order, selection)

    # 2. Automated Test Pass (Phase 2 `build_verify`)
    auto_steps = [
        ("build_verify", "orchestrator_health"),
        ("build_verify", "mrwp_ecs"),
        ("build_verify", "mrwp_local"),
        ("build_verify", "auth_ecs"),
        ("build_verify", "rc_report_notify"),
        ("build_verify", "rc_report_gate"),
    ]
    auto_status = _roll_up(state, auto_steps, phase_order, selection)
    if cur_phase == "build_verify" and stalled_phase == "build_verify" and auto_status != "complete":
        auto_status = "blocked"
    rc_details = _rc_build_links(state) + _release_branch_links(state)
    ui = _rc_ui_summary(state)
    if ui:
        rc_details = [ui] + rc_details
    if auto_status == "blocked":
        rc_details = [{"text": "Investigating RC build/test validation; release team actively triaging."}] + rc_details[:2]

    # 3. Manual Test Pass (Phase 3 `bug_bash`)
    bugbash_done = _roll_up(state, [("bug_bash", "bugbash_complete")], phase_order, selection) == "complete"
    if bugbash_done:
        manual_status = "complete"
    else:
        p3_steps = [
            ("bug_bash", "clone_plans_broker"),
            ("bug_bash", "clone_plans_auth"),
            ("bug_bash", "ui_test_status"),
            ("bug_bash", "distribute_tests"),
            ("bug_bash", "send_invite"),
            ("bug_bash", "activate_chat"),
            ("bug_bash", "bugbash_updates"),
            ("bug_bash", "notify_native_auth"),
            ("bug_bash", "native_auth_signoff"),
            ("bug_bash", "bugbash_complete"),
        ]
        manual_status = _roll_up(state, p3_steps, phase_order, selection)
        if cur_phase == "bug_bash" and stalled_phase == "bug_bash" and manual_status != "complete":
            manual_status = "blocked"
        elif manual_status == "not_started" and _is_done(state, "bug_bash", "send_invite", selection):
            manual_status = "scheduled"
    manual_details = []
    plan_id = (_getdata(state, "bug_bash", "clone_plans_broker") or {}).get("plan_id")
    if plan_id:
        from tools import testplans as _TP
        manual_details.append({"text": f"Broker test plan {plan_id}", "url": _TP.plan_web_url(plan_id)})
    if manual_status == "blocked":
        manual_details.insert(0, {"text": "Awaiting remaining Bug Bash sign-offs / issue resolution."})

    # 4. Final Release builds published (Phase 4 `finalize`)
    fin_steps = [
        ("finalize", "remove_rc_tags_gate"),
        ("finalize", "orchestrator_finalization"),
        ("finalize", "integ_prs"),
        ("finalize", "integ_prs_merged"),
        ("finalize", "publish_notes_gate"),
        ("finalize", "verify_pub"),
        ("finalize", "verify_release_notes"),
        ("finalize", "oneauth_common_pr"),
    ]
    fin_status = _roll_up(state, fin_steps, phase_order, selection)
    if cur_phase == "finalize" and stalled_phase == "finalize" and fin_status != "complete":
        fin_status = "blocked"
    fin_details = _github_release_links(state) if fin_status == "complete" else []
    if fin_status == "blocked":
        fin_details.insert(0, {"text": "Awaiting release PR merges or publication verification."})

    return [
        {"id": "code_complete", "label": "Code Complete", "status": cc_status, "date": ccd_str, "details": []},
        {"id": "automated_test_pass", "label": "Automated Test Pass", "status": auto_status, "date": "", "details": rc_details},
        {"id": "manual_test_pass", "label": "Manual Test Pass", "status": manual_status, "date": _bugbash_date(state), "details": manual_details},
        {"id": "final_published", "label": "Final Release builds published", "status": fin_status, "date": "", "details": fin_details},
    ]


def current_status_line(state, phase_order, ms, *, stage: str = "daily", ccd_long: str = "", ccd_today: bool = False):
    """A concise one-line status banner tailored to the stage and 4-milestone model."""
    if stage == "preflight":
        return f"Upcoming release reminder — Code Complete is scheduled for {ccd_long or _fmt_date(getattr(state, 'ccd', None))}."
    if stage == "ccd":
        when = "Today" if ccd_today else (ccd_long or _fmt_date(getattr(state, "ccd", None)))
        return f"Code Complete for this release is {when}."
    if stage == "final":
        return "All release milestones are complete — Common, MSAL, and Broker builds and release notes are published."

    done = [m["label"] for m in ms if m["status"] == "complete"]
    in_prog = [m["label"] for m in ms if m["status"] == "in_progress"]
    blocked = [m["label"] for m in ms if m["status"] == "blocked"]
    parts = []
    if blocked:
        parts.append("Blocked on " + ", ".join(blocked) + ".")
    if in_prog:
        parts.append("In progress: " + ", ".join(in_prog) + ".")
    elif done and not blocked:
        parts.append("Completed " + done[-1] + ".")
    return " ".join(parts) or "Release in progress."


# ---- window ---------------------------------------------------------------

def in_window(state, phase_order, selection=None):
    """(bool, reason) — True while build_verify <= current phase < rollout_start."""
    frontier = selection.frontier if selection else None
    cur = frontier.id if frontier else None
    if not cur:
        return (False, "release hasn't entered a phase yet")
    ci = _phase_index(phase_order, cur)
    si = _phase_index(phase_order, WINDOW_START)
    ei = _phase_index(phase_order, WINDOW_END)
    if ci < si:
        return (False, f"before Phase 2 (current: {cur})")
    if ei >= 0 and ci >= ei:
        return (False, f"at/after Phase 5 (current: {cur}) — status emails are done")
    return (True, "")


# ---- render ---------------------------------------------------------------

def _pill(status_key: str) -> str:
    label, bg, fg = _PILL.get(status_key, _PILL["not_started"])
    return (f"<span style=\"display:inline-block;padding:3px 10px;border-radius:12px;"
            f"background:{bg};color:{fg};font-size:12px;font-weight:600;white-space:nowrap;\">"
            f"{label}</span>")


def _details_html(details) -> str:
    if not details:
        return "<span style=\"color:#98a2b3;\">—</span>"
    bits = []
    for d in details:
        if d.get("url"):
            bits.append(f"<a href=\"{T.esc(d['url'])}\" style=\"color:#175cd3;text-decoration:none;\">"
                        f"{T.esc(d['text'])}</a>")
        else:
            bits.append(T.esc(d.get("text", "")))
    return "<br>".join(bits)


def _versions_section(versions, *, show_versions: bool) -> str:
    if not show_versions:
        return ""
    v = versions or {}
    if not any(v.get(k) for k, _ in SDK_ORDER):
        return ""
    order = [("msal", "MSAL"), ("common", "Common"), ("broker", "Broker")]
    rows = ""
    for key, label in order:
        val = v.get(key) or "—"
        rows += (f"<tr><td style=\"padding:8px 14px;border-top:1px solid #eaecf0;font-weight:600;\">"
                 f"{label}</td><td style=\"padding:8px 14px;border-top:1px solid #eaecf0;"
                 f"font-family:'SFMono-Regular',Consolas,monospace;\">{T.esc(val)}</td></tr>")
    return (
        "<h3 style=\"margin:0 0 8px;font-size:15px;color:#101828;\">SDK Versions</h3>"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" "
        f"style=\"border-collapse:collapse;width:auto;min-width:260px;margin-bottom:22px;"
        f"border:1px solid #eaecf0;border-radius:8px;overflow:hidden;\">"
        f"<tr style=\"background:#f9fafb;\"><th align=\"left\" style=\"padding:8px 14px;"
        f"font-size:12px;color:#475467;text-transform:uppercase;letter-spacing:.04em;\">SDK</th>"
        f"<th align=\"left\" style=\"padding:8px 14px;font-size:12px;color:#475467;"
        f"text-transform:uppercase;letter-spacing:.04em;\">Version</th></tr>{rows}</table>"
    )


def _milestones_table(ms) -> str:
    head = ("<tr style=\"background:#f9fafb;\">"
            "<th align=\"left\" style=\"padding:10px 14px;font-size:12px;color:#475467;"
            "text-transform:uppercase;letter-spacing:.04em;\">Milestone</th>"
            "<th align=\"left\" style=\"padding:10px 14px;font-size:12px;color:#475467;"
            "text-transform:uppercase;letter-spacing:.04em;white-space:nowrap;\">ETA / Date</th>"
            "<th align=\"left\" style=\"padding:10px 14px;font-size:12px;color:#475467;"
            "text-transform:uppercase;letter-spacing:.04em;\">Status</th>"
            "<th align=\"left\" style=\"padding:10px 14px;font-size:12px;color:#475467;"
            "text-transform:uppercase;letter-spacing:.04em;\">Details / Updates</th></tr>")
    body = ""
    for m in ms:
        raw_date = m.get("date") or ""
        if m.get("today_highlight"):
            date_cell = f"<strong>Today</strong> ({T.esc(raw_date)})" if raw_date else "<strong>Today</strong>"
        else:
            date_cell = T.esc(raw_date)
        body += (f"<tr>"
                 f"<td style=\"padding:11px 14px;border-top:1px solid #eaecf0;font-weight:600;"
                 f"color:#101828;\">{T.esc(m['label'])}</td>"
                 f"<td style=\"padding:11px 14px;border-top:1px solid #eaecf0;color:#475467;"
                 f"white-space:nowrap;\">{date_cell}</td>"
                 f"<td style=\"padding:11px 14px;border-top:1px solid #eaecf0;\">{_pill(m['status'])}</td>"
                 f"<td style=\"padding:11px 14px;border-top:1px solid #eaecf0;color:#475467;"
                 f"font-size:13px;\">{_details_html(m.get('details'))}</td></tr>")
    return (f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" "
            f"style=\"border-collapse:collapse;width:100%;border:1px solid #eaecf0;"
            f"border-radius:8px;overflow:hidden;\">{head}{body}</table>")


def _single_changelist_html(title: str, entries: list[dict]) -> str:
    if not entries:
        return ""
    lvl_bg = {"MAJOR": ("#fee4e2", "#b42318"), "MINOR": ("#fef0c7", "#b54708"),
              "PATCH": ("#eaecf0", "#475467")}
    items = ""
    for c in entries:
        lvl = (c.get("level") or "").upper()
        badge = ""
        if lvl in lvl_bg:
            bg, fg = lvl_bg[lvl]
            badge = (f"<span style=\"display:inline-block;padding:1px 7px;border-radius:9px;"
                     f"background:{bg};color:{fg};font-size:11px;font-weight:700;"
                     f"letter-spacing:.03em;\">{lvl}</span> ")
        pr = f" <span style=\"color:#98a2b3;\">#{T.esc(str(c['pr']))}</span>" if c.get("pr") else ""
        items += (f"<li style=\"margin:6px 0;line-height:1.4;\">{badge}"
                  f"{T.esc(c.get('text', ''))}{pr}</li>")
    return (f"<h4 style=\"margin:16px 0 6px;font-size:14px;color:#101828;\">{T.esc(title)}</h4>"
            f"<ul style=\"margin:0;padding-left:18px;color:#344054;font-size:13px;\">{items}</ul>")


def _changelogs_section(model: dict) -> str:
    """Render per-library changelogs (`Common`, `MSAL`, `Broker`). Accepts either `changelogs`
    dict `{common:[...], msal:[...], broker:[...]}` or legacy `changes` list (Broker-only)."""
    changelogs = model.get("changelogs")
    versions = model.get("versions") or {}
    blocks = []
    if isinstance(changelogs, dict):
        for key, label in SDK_ORDER:
            entries = changelogs.get(key) or []
            if entries:
                ver = versions.get(key)
                heading = f"{label} ({ver})" if ver else label
                blocks.append(_single_changelist_html(heading, entries))
    if not blocks and model.get("changes"):
        ver = versions.get("broker")
        heading = f"Broker ({ver})" if ver else "Broker"
        blocks.append(_single_changelist_html(heading, model["changes"]))
    if not blocks:
        return ""
    return (
        "<h3 style=\"margin:26px 0 8px;font-size:15px;color:#101828;\">Changelogs (Common · MSAL · Broker)</h3>"
        + "".join(blocks)
    )


def render_html(model) -> str:
    """Render the unified HTML card across all stages (`preflight`, `ccd`, `daily`, `final`)."""
    stage = model.get("stage") or "daily"
    month_year = model.get("month_year", "")
    status_line = model.get("status_line", "")
    owner = model.get("owner_name") or "the release team"
    show_versions = bool(model.get("show_versions", stage in ("daily", "final")))

    if stage == "final":
        banner_bg, banner_border, banner_fg, banner_label = "#ecfdf3", "#abefc6", "#027a48", "Release Published:"
    elif any(m.get("status") == "blocked" for m in (model.get("milestones") or [])):
        banner_bg, banner_border, banner_fg, banner_label = "#fef3f2", "#fecdca", "#b42318", "Attention:"
    else:
        banner_bg, banner_border, banner_fg, banner_label = "#f2f7ff", "#d1e9ff", "#175cd3", "Current status:"

    if stage == "ccd" and model.get("ccd_today"):
        status_html = (
            "Code Complete for this release is <strong>Today</strong>"
            + (f" ({T.esc(model.get('ccd_date', ''))})." if model.get("ccd_date") else ".")
        )
    else:
        status_html = T.esc(status_line)

    policy_callout = ""
    if stage in ("preflight", "ccd"):
        policy_callout = (
            "<p style=\"margin:0 0 18px;color:#344054;\">"
            f"{T.esc(HOTFIX_POLICY_NOTE)}</p>"
        )

    return (
        "<div style=\"font-family:'Segoe UI',-apple-system,Arial,sans-serif;color:#101828;"
        "max-width:760px;margin:0 auto;font-size:14px;line-height:1.5;\">"
        # header
        "<div style=\"padding:20px 22px;background:#0b3a6f;border-radius:12px 12px 0 0;color:#fff;\">"
        "<div style=\"font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.8;\">"
        "Auth Client Android SDKs — Release Status</div>"
        f"<div style=\"font-size:22px;font-weight:700;margin-top:4px;\">{T.esc(month_year)} Release</div>"
        "</div>"
        # body card
        "<div style=\"border:1px solid #eaecf0;border-top:none;border-radius:0 0 12px 12px;"
        "padding:22px;\">"
        # current status banner
        f"<div style=\"background:{banner_bg};border:1px solid {banner_border};border-radius:8px;"
        f"padding:12px 14px;margin-bottom:18px;\"><span style=\"font-weight:700;color:{banner_fg};\">"
        f"{banner_label}</span> <span style=\"color:#344054;\">{status_html}</span></div>"
        f"{policy_callout}"
        # versions (P2-P4 only)
        f"{_versions_section(model.get('versions'), show_versions=show_versions)}"
        # 4 milestones
        "<h3 style=\"margin:0 0 8px;font-size:15px;color:#101828;\">Milestones</h3>"
        f"{_milestones_table(model.get('milestones') or [])}"
        # changelogs (P4 final, or explicit changes injection)
        f"{_changelogs_section(model)}"
        # sign-off
        f"<p style=\"margin:26px 0 0;color:#344054;\">Thank you,<br>{T.esc(owner)}</p>"
        "<p style=\"margin:16px 0 0;color:#98a2b3;font-size:11px;\">"
        "Automated release communication from the Android SDK release orchestrator.</p>"
        "</div></div>"
    )


def render_plain(model: dict) -> str:
    """Plain-text summary preserved in `_plain_body` for previews and diagnostics."""
    stage = model.get("stage") or "daily"
    lines = [
        f"Auth Client Android SDKs — {model.get('month_year', '')} Release",
        "",
    ]
    if stage == "ccd" and model.get("ccd_today"):
        lines.append(f"Code Complete for this release is **Today** ({model.get('ccd_date', '')}).")
    else:
        lines.append(str(model.get("status_line") or ""))
    if stage in ("preflight", "ccd"):
        lines.extend(["", HOTFIX_POLICY_NOTE])
    lines.append("")
    for m in model.get("milestones") or []:
        pill_txt = _PILL.get(m["status"], _PILL["not_started"])[0]
        date_part = f" ({m['date']})" if m.get("date") else ""
        lines.append(f"- {m['label']}: {pill_txt}{date_part}")
    lines.extend(["", f"Thank you,\n{model.get('owner_name') or 'the release team'}"])
    return "\n".join(lines)


def compose(
    state,
    phase_order,
    recipients,
    changes=None,
    selection=None,
    *,
    stage: str = "daily",
    changelogs: Optional[dict[str, list[dict]]] = None,
    ccd_today: bool = False,
    ccd_long: str = "",
    stalled_phase: Optional[str] = None,
):
    """Build the unified send descriptor `{to, subject, html, plain, skip, reason, model}`."""
    if stage == "daily":
        ok, reason = in_window(state, phase_order, selection)
    else:
        ok, reason = True, ""
    month_year = schedule.target_month_label(state) or str(getattr(state, "release_id", ""))
    ccd_date = _fmt_date(getattr(state, "ccd", None))

    if stage == "preflight":
        subject = f"Auth Client Android SDKs {month_year} Release — Code Complete Reminder ({ccd_date})"
    elif stage == "ccd":
        suffix = " (Today)" if ccd_today else f" ({ccd_date})"
        subject = f"Auth Client Android SDKs {month_year} Release — Code Complete{suffix}"
    elif stage == "final":
        subject = f"Auth Client Android SDKs {month_year} Release — Final Status"
    else:
        subject = f"Auth Client Android SDKs {month_year} Release — Daily Status"

    ms = milestones(
        state,
        phase_order,
        selection,
        stage=stage,
        ccd_today=ccd_today,
        stalled_phase=stalled_phase,
    )
    show_versions = stage in ("daily", "final")
    model = {
        "stage": stage,
        "month_year": month_year,
        "ccd_date": ccd_date,
        "ccd_today": bool(ccd_today),
        "show_versions": show_versions,
        "status_line": current_status_line(
            state, phase_order, ms, stage=stage, ccd_long=ccd_long, ccd_today=ccd_today
        ),
        "versions": (getattr(state, "versions", None) or {}) if show_versions else {},
        "milestones": ms,
        "changes": changes or [],
        "changelogs": changelogs,
        "owner_name": getattr(state, "owner_name", None) or getattr(state, "owner_email", None),
    }
    return {
        "to": list(recipients or []),
        "subject": subject,
        "html": render_html(model),
        "plain": render_plain(model),
        "skip": not ok,
        "reason": reason,
        "model": model,
    }


def milestone_signature(ms: list[dict[str, Any]]) -> str:
    canonical = [(m.get("id") or m.get("label"), m.get("status")) for m in (ms or [])]
    digest = hashlib.sha256(
        json.dumps(canonical, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest[:16]}"


def evaluate_and_deliver_partner_status(
    orch,
    *,
    waiting: dict[str, Any],
    risk: dict[str, Any],
    runs_root: str,
    config_path: str,
    now_utc: datetime,
    deliver: bool = False,
    mcp_caller=None,
) -> dict[str, Any]:
    """24-hour + state-change external partner status monitor for Phases 2, 3, and 4.

    Mirrors the release-owner `evaluate_risk_and_cta` pattern:
      - Only active during Phases 2 (`build_verify`), 3 (`bug_bash`), and 4 (`finalize`)
        before `finalize.final_status_email` is complete.
      - Marks the active milestone `blocked` if any step is blocked OR no progress was made
        across the business day (`risk['level'] in ('attention', 'stalled', 'cutoff_at_risk')`).
      - Triggers when:
          (a) End-of-day (`>= 17:00` local on business days) if no update was sent today;
          (b) Offline / 24h recovery (`>= 20h` since last send on a new business day during
              working hours `07:00-20:00`, or `>= 8` business hours in-phase with zero prior send);
          (c) Meaningful milestone transition (newly `blocked`, recovered from `blocked`, or
              completed a phase milestone) with `>= 4h` cooldown since the previous send.
      - Honors `config/environment.yaml` profile recipients and `step_defaults.status_email`.
    """
    from orchestrator import cli_common as C, delivery as D, environment as env, notifications as notif
    from tools import bugbash as BB

    state = orch.state
    selection = orch.scheduling()
    phase_order = [p.id for p in orch.workflow.phases]
    frontier_id = selection.frontier.id if selection.frontier else None

    if (
        not orch.gate.signed
        or state.halt
        or state.cancellation
        or frontier_id not in ("build_verify", "bug_bash", "finalize")
        or state.is_done("finalize", "final_status_email")
    ):
        return {"sent": False, "due": False, "reason": "outside_active_sdk_window"}

    active_overrides = getattr(orch, "step_defaults", None) or env.load_step_defaults(config_path)
    status_override = active_overrides.get("status_email") if isinstance(active_overrides, dict) else None
    if isinstance(status_override, dict) and str(status_override.get("outcome", "")).strip().lower() in {
        "done", "pass", "ok", "complete"
    }:
        return {"sent": False, "due": False, "reason": "configured_done"}

    stalled_phase = (
        frontier_id
        if (
            waiting.get("blocked_count", 0) > 0
            or risk.get("level") in ("attention", "stalled", "cutoff_at_risk")
        )
        else None
    )

    tracker = dict((state.drain_tracker or {}).get("partner_status") or {})
    phase_step_vec = [
        (s.definition.id, s.status, str(state.get_step(frontier_id, s.definition.id).note or ""))
        for s in selection.steps
        if s.definition.phase_id == frontier_id
    ]
    cur_progress_sig = hashlib.sha256(
        json.dumps(phase_step_vec, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:16]
    if tracker.get("phase_progress_sig") != cur_progress_sig or tracker.get("active_phase") != frontier_id:
        progress_since_dt = now_utc
    else:
        progress_since_dt = schedule.parse_iso_utc(tracker.get("phase_progress_since")) or now_utc

    no_progress_bh = schedule.business_hours_between(
        progress_since_dt,
        orch.now_local,
        tz=orch.tz,
        day_start_hour=7,
        day_end_hour=19,
    )
    if no_progress_bh >= 8.0:
        stalled_phase = frontier_id

    def _persist_tracker(extra: Optional[dict[str, Any]] = None) -> None:
        dt_all = dict(state.drain_tracker or {})
        merged = {
            **tracker,
            "active_phase": frontier_id,
            "phase_progress_sig": cur_progress_sig,
            "phase_progress_since": progress_since_dt.isoformat(),
            **(extra or {}),
        }
        if dt_all.get("partner_status") != merged:
            dt_all["partner_status"] = merged
            state.drain_tracker = dt_all
            if runs_root:
                C.save_state(state, runs_root, state.release_id)

    explicit_send_to = status_override.get("send_to") if isinstance(status_override, dict) else None
    try:
        if explicit_send_to:
            recipients, subject_prefix, _ = env.resolve_email_targets(
                "partner_status",
                owner_email=state.owner_email,
                override=explicit_send_to,
                config_path=config_path,
            )
        elif env.active_profile_name(config_path) != "prod":
            recipients, subject_prefix, _ = env.resolve_email_targets(
                "partner_status",
                owner_email=state.owner_email,
                config_path=config_path,
            )
        else:
            recipients = notif.partner_recipients(config_path)
            subject_prefix = env.email_subject_prefix(config_path)
    except ValueError:
        return {"sent": False, "due": False, "reason": "no_partner_recipients_configured"}

    composed = compose(
        state,
        phase_order,
        recipients,
        selection=selection,
        stage="daily",
        stalled_phase=stalled_phase,
    )
    if composed["skip"]:
        return {"sent": False, "due": False, "reason": composed["reason"]}

    now_local = orch.now_local
    today = now_local.date()
    if not BB.is_business_day(today) or not (7 <= now_local.hour <= 20):
        _persist_tracker()
        return {"sent": False, "due": False, "reason": "outside_business_hours"}

    ms_sig = milestone_signature(composed["model"]["milestones"])
    prev_sig = tracker.get("last_milestone_sig")
    last_sent_dt = schedule.parse_iso_utc(tracker.get("last_sent_at"))
    hours_since_last = (
        (now_utc - last_sent_dt.astimezone(timezone.utc)).total_seconds() / 3600.0
        if last_sent_dt is not None
        else None
    )
    last_sent_date = (
        last_sent_dt.astimezone(orch.tz).date()
        if (last_sent_dt is not None and orch.tz is not None)
        else (last_sent_dt.date() if last_sent_dt is not None else None)
    )

    due = False
    trigger_reason = "not_due"
    sig_changed = bool(prev_sig and prev_sig != ms_sig)

    if sig_changed and (hours_since_last is None or hours_since_last >= 4.0):
        due = True
        trigger_reason = "milestone_state_changed"
    elif now_local.hour >= 17 and last_sent_date != today and (hours_since_last is None or hours_since_last >= 12.0):
        due = True
        trigger_reason = "eod_daily_heartbeat"
    elif last_sent_date != today and hours_since_last is not None and hours_since_last >= 20.0:
        due = True
        trigger_reason = "offline_catchup_24h"
    elif last_sent_dt is None and (no_progress_bh >= 8.0 or risk.get("unattended_business_hours", 0.0) >= 8.0 or stalled_phase is not None):
        due = True
        trigger_reason = "initial_phase_alert"

    if not due:
        _persist_tracker()
        return {"sent": False, "due": False, "reason": trigger_reason, "milestone_sig": ms_sig}

    if not deliver:
        _persist_tracker()
        return {"sent": False, "due": True, "reason": trigger_reason, "milestone_sig": ms_sig}

    logical_id = f"partner-status:{today.isoformat()}:{ms_sig[-12:]}"
    scope = {
        "kind": "phase",
        "phase": frontier_id,
        "date": today.isoformat(),
        "release_matches": {"ccd": state.ccd},
    }
    payload = {
        "to": recipients,
        "subject": f"{subject_prefix}{composed['subject']}",
        "body": composed["html"],
        "isHtml": True,
    }
    desc = D.descriptor(state, logical_id, scope, "workiq_send_email", payload)
    if not D.available(orch, desc):
        return {"sent": False, "due": False, "reason": "already_delivered_or_claimed", "milestone_sig": ms_sig}

    from orchestrator.commands.deliver_cmd import deliver_descriptor_items

    C.save_state(state, runs_root, state.release_id)
    results = deliver_descriptor_items(
        runs_root,
        state.release_id,
        config_path,
        [desc],
        executor="release-drainer-partner-status",
        mcp_caller=mcp_caller,
        as_of=orch.now_local,
    )
    reloaded = C.load_state(runs_root, state.release_id)
    state.notification_deliveries = reloaded.notification_deliveries
    res0 = results[0] if results else {}
    if res0.get("status") != "done":
        _persist_tracker()
        return {
            "sent": False,
            "due": True,
            "reason": f"delivery_{res0.get('status', 'failed')}:{res0.get('error', '')}",
            "milestone_sig": ms_sig,
        }

    _persist_tracker({
        "last_sent_at": now_utc.isoformat(),
        "last_milestone_sig": ms_sig,
        "last_phase": frontier_id,
        "last_reason": trigger_reason,
    })
    return {
        "sent": True,
        "due": True,
        "reason": trigger_reason,
        "to": recipients,
        "milestone_sig": ms_sig,
    }

