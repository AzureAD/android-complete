"""Partner-facing daily release STATUS email — composer + template.

A polished, business-day daily status update sent to the release distribution lists while the
release is in flight (Phase 2 build_verify through Phase 4 finalize). Distinct from the internal
owner digest (orchestrator/notifications + render.notification_html): different audience (the
partner DLs), a richer milestone template, and a business-day cadence.

`compose(state, phase_order, recipients)` returns a send descriptor
    {to, subject, html, skip, reason, model}
where `skip` is True (with a reason) when the release is OUTSIDE the send window
(before build_verify, or at rollout_start+). The command layer decides what to do with it;
this module is pure (no IO) so it renders identically in tests and previews.

The milestone rows map partner-facing checkpoints onto our real phase/step state, so the email
is always an honest reflection of the run — never hand-maintained.
"""
from __future__ import annotations

from steps.lib import templating as T
from orchestrator import schedule


# Window: send only while build_verify (2) <= current phase < rollout_start (5).
WINDOW_START = "build_verify"
WINDOW_END = "rollout_start"          # exclusive — Phase 5 onward: stop.

# Status vocabulary → (label, pill background, pill text color).
_PILL = {
    "complete":    ("✅ Complete",    "#d1fadf", "#027a48"),
    "in_progress": ("🟡 In Progress", "#fef0c7", "#b54708"),
    "scheduled":   ("🗓 Scheduled",   "#d1e9ff", "#175cd3"),
    "blocked":     ("🔴 Blocked",     "#fee4e2", "#b42318"),
    "not_started": ("⬜ Not Started", "#f2f4f7", "#475467"),
}


# ---- milestone model ------------------------------------------------------

def _step(state, phase, step):
    try:
        return state.get_step(phase, step)
    except Exception:  # noqa: BLE001
        return None


def _is_done(state, phase, step) -> bool:
    try:
        return state.is_done(phase, step)
    except Exception:  # noqa: BLE001
        return False


def _phase_index(phase_order, phase_id):
    try:
        return phase_order.index(phase_id)
    except (ValueError, AttributeError):
        return -1


def _step_status(state, phase, step, phase_order):
    """Derive a partner-facing status for one of OUR steps: complete | blocked | in_progress
    (it's the current phase's active work) | not_started."""
    st = _step(state, phase, step)
    if st is not None and getattr(st, "status", None) == "blocked":
        return "blocked"
    if _is_done(state, phase, step):
        return "complete"
    # In progress if we've reached this step's phase (current phase index >= its phase index).
    cur_i = _phase_index(phase_order, getattr(state, "current_phase", None))
    ph_i = _phase_index(phase_order, phase)
    if cur_i >= ph_i >= 0:
        return "in_progress"
    return "not_started"


def _roll_up(state, steps, phase_order):
    """Combine several of our steps into one milestone status: all done → complete;
    any blocked → blocked; any reached → in_progress; else not_started."""
    statuses = [_step_status(state, p, s, phase_order) for p, s in steps]
    if statuses and all(s == "complete" for s in statuses):
        return "complete"
    if any(s == "blocked" for s in statuses):
        return "blocked"
    if any(s in ("in_progress", "complete") for s in statuses):
        return "in_progress"
    return "not_started"


def _fmt_date(s):
    """'YYYY-MM-DD' or ISO -> 'M/D/YYYY'; '' on anything unparseable."""
    d = schedule.parse_date(str(s)[:10]) if s else None
    return f"{d.month}/{d.day}/{d.year}" if d else ""


def _release_branch_links(state):
    """[{text,url}] to each lib's release branch (release/<version>, per integ_prs.RELEASE_PREFIX)
    + the auth app release branch. All libs use release/<version> (verified live); the auth app
    uses release/YYYY/MM/DD."""
    from steps.finalize import integ_prs as IP
    v = getattr(state, "versions", None) or {}
    out = []
    for key, label in (("common", "Common"), ("msal", "MSAL"), ("broker", "Broker")):
        ver = v.get(key)
        gh = (IP.CONFIG.get(key) or {}).get("gh_repo")
        if not (ver and gh):
            continue
        host = ("https://" + gh) if ("." in gh.split("/")[0]) else ("https://github.com/" + gh)
        out.append({"text": label, "url": f"{host}/tree/{IP.RELEASE_PREFIX}{ver}"})
    ab = v.get("authenticator")                       # release/YYYY/MM/DD
    ado = (IP.CONFIG.get("authenticator") or {}).get("ado") or {}
    if ab and ado.get("org") and ado.get("project") and ado.get("repository"):
        from urllib.parse import quote
        org = ado["org"].rstrip("/")
        out.append({"text": "Authenticator",
                    "url": f"{org}/{ado['project']}/_git/{ado['repository']}?version=GB{quote(ab)}"})
    return out


def _rc_ui_summary(state):
    """A '{text}' item summarizing the combined RC UI-automation pass rate (ECS + Local),
    from the stored latest-RC test data. None when not available."""
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    if not rcs:
        return None
    cur = rcs[-1]
    passed = total = 0
    for slot in ("ecs", "local"):
        ui = (((cur.get(slot) or {}).get("tests") or {}).get("categories") or {}).get("ui") or {}
        passed += ui.get("passed", 0) or 0
        total += ui.get("total", 0) or 0
    if not total:
        return None
    pct = round(passed * 100.0 / total, 1)
    gate = "✅" if pct >= 90 else "🔴"
    return {"text": f"UI automation {pct}% ({passed}/{total}, gate ≥90% {gate})"}


def _auth_build_link(state):
    """A '{text,url}' link to the Authenticator app build (msazure/One), when known in state."""
    from tools.coordinates import coords
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    bid = None
    if rcs:
        bid = (((rcs[-1].get("auth") or {}).get("build") or {}).get("build_id"))
    if not bid:
        return None
    org = f"{coords.org_url('one')}/{coords.project('one')}"
    return {"text": "Authenticator build", "url": f"{org}/_build/results?buildId={bid}&view=results"}


def milestones(state, phase_order):
    """The ordered partner milestone rows, each: {label, status, date, details:[{text,url?}]}."""
    rows = []

    # 1. Code Complete — the CCD; complete once CCD has passed (Phase 1 done).
    cc_status = "complete" if _is_done(state, "ccd", "final_reminder") or _roll_up(
        state, [("ccd", "final_reminder")], phase_order) == "complete" else _step_status(
        state, "ccd", "final_reminder", phase_order)
    rows.append({"label": "Code Complete", "status": cc_status,
                 "date": _fmt_date(getattr(state, "ccd", None)), "details": []})

    # 2. Release Branches Created — orchestrator cut them (Phase-2 orchestrator_health).
    rows.append({"label": "Release Branches Created",
                 "status": _roll_up(state, [("build_verify", "orchestrator_health")], phase_order),
                 "date": "", "details": _release_branch_links(state)})

    # 3. Bug Bash Test Plan — the cloned bug-bash plans.
    plan_id = (_getdata(state, "bug_bash", "clone_plans_broker") or {}).get("plan_id")
    tp_details = []
    if plan_id:
        from tools import testplans as _TP
        tp_details = [{"text": f"Broker test plan {plan_id}", "url": _TP.plan_web_url(plan_id)}]
    rows.append({"label": "Bug Bash Test Plan",
                 "status": _roll_up(state, [("bug_bash", "clone_plans_broker"),
                                            ("bug_bash", "clone_plans_auth")], phase_order),
                 "date": "", "details": tp_details})

    # 4. RC builds + automation run — the MRWP/auth RC pipelines (Phase 2). Surface the
    #    combined UI-automation pass rate (the 90% gate signal) alongside the build links.
    rc_details = _rc_build_links(state)
    ui = _rc_ui_summary(state)
    if ui:
        rc_details = [ui] + rc_details
    rows.append({"label": "RC builds and test apks generated, automation run",
                 "status": _roll_up(state, [("build_verify", "mrwp_ecs"),
                                            ("build_verify", "mrwp_local"),
                                            ("build_verify", "auth_ecs"),
                                            ("build_verify", "rc_report")], phase_order),
                 "date": "", "details": rc_details})

    # 4b. Authenticator app — built at Phase 2 (auth_ecs), version-tagged at Phase 4
    #     (tag_authenticator). Relevant to androididentity@ (the Auth app, not just the SDKs).
    auth_details = []
    abl = _auth_build_link(state)
    if abl:
        auth_details.append(abl)
    rows.append({"label": "Authenticator app built & tagged",
                 "status": _roll_up(state, [("build_verify", "auth_ecs"),
                                            ("finalize", "tag_authenticator")], phase_order),
                 "date": "", "details": auth_details})

    # 5. Manual Test Pass Scheduled — the bug bash invite (shows as Scheduled, not Complete).
    #    The date is RECOMPUTED deterministically from when send_invite ran (its persisted
    #    completed_at) via the same scheduling rule — no extra persisted field, no guess.
    invite_done = _is_done(state, "bug_bash", "send_invite")
    rows.append({"label": "Manual Test Pass Scheduled",
                 "status": "scheduled" if invite_done else _step_status(
                     state, "bug_bash", "send_invite", phase_order),
                 "date": _bugbash_date(state), "details": []})

    # 6. Manual Test Pass Complete — bug bash signed off.
    rows.append({"label": "Manual Test Pass Complete",
                 "status": _roll_up(state, [("bug_bash", "bugbash_complete")], phase_order),
                 "date": "", "details": []})

    # 7. Final Release builds published — Maven Central + GitHub (Phase 4).
    rows.append({"label": "Final Release builds published",
                 "status": _roll_up(state, [("finalize", "verify_pub")], phase_order),
                 "date": "", "details": []})

    # 8. Notification sent out to partners — the Teams announcement.
    rows.append({"label": "Notification sent out to partners",
                 "status": _roll_up(state, [("finalize", "release_announcement")], phase_order),
                 "date": "", "details": []})

    # 9. Release Notes published — the GitHub release notes.
    rows.append({"label": "Release Notes published",
                 "status": _roll_up(state, [("finalize", "verify_release_notes")], phase_order),
                 "date": "", "details": []})
    return rows


def _getdata(state, phase, step):
    st = _step(state, phase, step)
    return getattr(st, "data", None) if st is not None else None


def _bugbash_date(state):
    """The bug-bash date, RECOMPUTED from send_invite's persisted completed_at via the same
    deterministic scheduling rule (tools.invite.schedule_bugbash). '' when not scheduled yet."""
    st = _step(state, "bug_bash", "send_invite")
    ts = getattr(st, "completed_at", None) if st is not None else None
    if not ts:
        return ""
    try:
        from datetime import datetime
        from tools import invite as I
        dt = datetime.fromisoformat(str(ts).replace("Z", "").split(".")[0])
        start, _end, _when = I.schedule_bugbash(dt)
        return f"{start.month}/{start.day}/{start.year}"
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


def current_status_line(state, phase_order, ms):
    """A one-line human 'Current Status' summary, auto-generated from the milestone model."""
    done = [m["label"] for m in ms if m["status"] == "complete"]
    in_prog = [m["label"] for m in ms if m["status"] == "in_progress"]
    blocked = [m["label"] for m in ms if m["status"] == "blocked"]
    parts = []
    if blocked:
        parts.append("Blocked on " + ", ".join(blocked).lower() + ".")
    if in_prog:
        parts.append("In progress: " + ", ".join(in_prog).lower() + ".")
    elif done and not blocked:
        parts.append("Completed " + done[-1].lower() + ".")
    return " ".join(parts) or "Release in progress."


# ---- window ---------------------------------------------------------------

def in_window(state, phase_order):
    """(bool, reason) — True while build_verify <= current phase < rollout_start."""
    cur = getattr(state, "current_phase", None)
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


def _versions_table(versions) -> str:
    order = [("msal", "MSAL"), ("common", "Common"), ("broker", "Broker")]
    rows = ""
    for key, label in order:
        val = (versions or {}).get(key) or "—"
        rows += (f"<tr><td style=\"padding:8px 14px;border-top:1px solid #eaecf0;font-weight:600;\">"
                 f"{label}</td><td style=\"padding:8px 14px;border-top:1px solid #eaecf0;"
                 f"font-family:'SFMono-Regular',Consolas,monospace;\">{T.esc(val)}</td></tr>")
    return (f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" "
            f"style=\"border-collapse:collapse;width:auto;min-width:260px;border:1px solid #eaecf0;"
            f"border-radius:8px;overflow:hidden;\">"
            f"<tr style=\"background:#f9fafb;\"><th align=\"left\" style=\"padding:8px 14px;"
            f"font-size:12px;color:#475467;text-transform:uppercase;letter-spacing:.04em;\">SDK</th>"
            f"<th align=\"left\" style=\"padding:8px 14px;font-size:12px;color:#475467;"
            f"text-transform:uppercase;letter-spacing:.04em;\">Current Build</th></tr>{rows}</table>")


def _milestones_table(ms) -> str:
    head = ("<tr style=\"background:#f9fafb;\">"
            "<th align=\"left\" style=\"padding:10px 14px;font-size:12px;color:#475467;"
            "text-transform:uppercase;letter-spacing:.04em;\">Step</th>"
            "<th align=\"left\" style=\"padding:10px 14px;font-size:12px;color:#475467;"
            "text-transform:uppercase;letter-spacing:.04em;white-space:nowrap;\">ETA / Date</th>"
            "<th align=\"left\" style=\"padding:10px 14px;font-size:12px;color:#475467;"
            "text-transform:uppercase;letter-spacing:.04em;\">Status</th>"
            "<th align=\"left\" style=\"padding:10px 14px;font-size:12px;color:#475467;"
            "text-transform:uppercase;letter-spacing:.04em;\">Details / Updates</th></tr>")
    body = ""
    for m in ms:
        body += (f"<tr>"
                 f"<td style=\"padding:11px 14px;border-top:1px solid #eaecf0;font-weight:600;"
                 f"color:#101828;\">{T.esc(m['label'])}</td>"
                 f"<td style=\"padding:11px 14px;border-top:1px solid #eaecf0;color:#475467;"
                 f"white-space:nowrap;\">{T.esc(m.get('date') or '')}</td>"
                 f"<td style=\"padding:11px 14px;border-top:1px solid #eaecf0;\">{_pill(m['status'])}</td>"
                 f"<td style=\"padding:11px 14px;border-top:1px solid #eaecf0;color:#475467;"
                 f"font-size:13px;\">{_details_html(m.get('details'))}</td></tr>")
    return (f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" "
            f"style=\"border-collapse:collapse;width:100%;border:1px solid #eaecf0;"
            f"border-radius:8px;overflow:hidden;\">{head}{body}</table>")


def _changelist_html(changes) -> str:
    """changes: [{level, text, pr?}] — level (PATCH/MINOR/MAJOR) shown as a badge only when
    present (we never fabricate one)."""
    if not changes:
        return ""
    lvl_bg = {"MAJOR": ("#fee4e2", "#b42318"), "MINOR": ("#fef0c7", "#b54708"),
              "PATCH": ("#eaecf0", "#475467")}
    items = ""
    for c in changes:
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
    return (f"<h3 style=\"margin:26px 0 8px;font-size:15px;color:#101828;\">Broker change list</h3>"
            f"<ul style=\"margin:0;padding-left:18px;color:#344054;font-size:13px;\">{items}</ul>")


def render_html(model) -> str:
    """The full status email. `model` = {month_year, status_line, versions, milestones,
    changes, owner_name}."""
    month_year = model.get("month_year", "")
    status_line = model.get("status_line", "")
    owner = model.get("owner_name") or "the release team"
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
        "<div style=\"background:#f2f7ff;border:1px solid #d1e9ff;border-radius:8px;padding:12px 14px;"
        "margin-bottom:22px;\"><span style=\"font-weight:700;color:#175cd3;\">Current status:</span> "
        f"<span style=\"color:#344054;\">{T.esc(status_line)}</span></div>"
        # versions
        "<h3 style=\"margin:0 0 8px;font-size:15px;color:#101828;\">Versions</h3>"
        f"{_versions_table(model.get('versions'))}"
        # milestones
        "<h3 style=\"margin:26px 0 8px;font-size:15px;color:#101828;\">Progress</h3>"
        f"{_milestones_table(model.get('milestones') or [])}"
        # change list
        f"{_changelist_html(model.get('changes'))}"
        # sign-off
        f"<p style=\"margin:26px 0 0;color:#344054;\">Thanks,<br>{T.esc(owner)}</p>"
        "<p style=\"margin:16px 0 0;color:#98a2b3;font-size:11px;\">Automated daily release status "
        "(business days) from the Android release orchestrator.</p>"
        "</div></div>")


def compose(state, phase_order, recipients, changes=None):
    """Build the send descriptor. Returns {to, subject, html, skip, reason, model}."""
    ok, reason = in_window(state, phase_order)
    month_year = schedule.target_month_label(state) or str(getattr(state, "release_id", ""))
    subject = f"Auth Client Android SDKs {month_year} Release — Daily Status"
    ms = milestones(state, phase_order)
    model = {
        "month_year": month_year,
        "status_line": current_status_line(state, phase_order, ms),
        "versions": getattr(state, "versions", None) or {},
        "milestones": ms,
        "changes": changes or [],
        "owner_name": getattr(state, "owner_name", None) or getattr(state, "owner_email", None),
    }
    return {
        "to": list(recipients or []),
        "subject": subject,
        "html": render_html(model),
        "skip": not ok,
        "reason": reason,
        "model": model,
    }
