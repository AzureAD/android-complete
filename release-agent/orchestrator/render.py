"""Presentation layer — turns structured data into text/markdown.

Kept separate from the logic (engine.py / readiness.py) so a different interface
(web UI, TUI, another frontend) can consume the same structured data and present
it its own way. These are pure functions: data in, string out. No state, no IO.
"""
from __future__ import annotations


# ---- readiness entry gate (the frozen table) ----
_ICON = {"pass": "✅", "attested": "✅", "fail": "❌", "unable": "⛔",
         "degraded": "⚠️", "pending": "⬜"}
_STATUS_WORD = {"pass": "PASS", "attested": "Confirmed", "fail": "FAIL",
                "unable": "Unable", "degraded": "Proceeding (not silent)",
                "pending": "Outstanding"}


def _cell(s: str) -> str:
    """Single-line, pipe-safe text for a markdown table cell."""
    return (s or "").replace("\n", " ").replace("|", "\\|").strip()


def readiness_table(chk: dict, release_id: str) -> str:
    """Canonical markdown table for the entry gate (frozen layout).
    `chk` is ReadinessGate.checklist()."""
    if not chk.get("items"):
        return "No readiness checklist configured."
    lines = [
        f"### Readiness Entry Gate — {release_id}",
        "",
        _cell(chk["instructions"]),
        "",
        "**Type legend:** `[auto]` Scout verifies · `[attest]` you confirm",
        "",
        "| | Type | Item | Status |",
        "|---|---|---|---|",
    ]
    for it in chk["items"]:
        box = _ICON.get(it["status"], "⬜")
        typ = "`[auto]`" if it["verify"] == "auto" else "`[attest]`"
        label = it.get("label") or it["id"]
        if it.get("checks"):
            parts = [f"[{c['name']}]({c['url']}) {'✓' if c.get('ok') else '✗'}"
                     if c.get("url") else f"{c['name']} {'✓' if c.get('ok') else '✗'}"
                     for c in it["checks"]]
            detail = " · ".join(parts)
        else:
            detail = it.get("detail") or it.get("text") or ""
        win = it.get("window")
        if win and win.get("start") and win.get("end"):
            detail = f"{detail} (window: {win['start']} → {win['end']})".strip()
        item_cell = f"**{_cell(label)}** — {_cell(detail)}"
        lines.append(f"| {box} | {typ} | {item_cell} | {_STATUS_WORD.get(it['status'], it['status'])} |")
    lines.append("")

    if chk["blocked"]:
        labels = [next((i["label"] for i in chk["items"] if i["id"] == b), b)
                  for b in chk["blocked_items"]]
        lines.append("⛔ **Blocked** — cannot start: " + ", ".join(labels) + ".")
        lines.append(_cell(chk["blocked_message"]))
    elif chk["signed"]:
        lines.append("✅ **All items satisfied — entry gate cleared.** Ready to start Phase 0.")
    else:
        pending = [i["label"] for i in chk["items"] if not i["satisfied"]]
        lines.append("**Outstanding:** " + ", ".join(pending))
    return "\n".join(lines)


# ---- release status ----
# Plain-language labels for internal engine states (never show raw state names).
_STATE_LABEL = {
    "not_started": "Not started",
    "running": "In progress",
    "scheduled": "Scheduled — waiting for the window to open",
    "awaiting_action": "Action needed from you",
    "holding_gate": "Waiting for your approval",
    "readiness_gate": "Entry gate — checklist pending",
    "blocked": "Blocked",
    "halted": "Halted",
    "complete": "Complete",
}
_PHASE_ICON = {"done": "✅", "current": "⏸", "pending": "⬜", "scheduled": "🗓"}
_STEP_ICON = {"done": "✅", "gate": "⏸", "reminder": "📌", "scheduled": "🗓",
              "pending": "⬜", "skipped": "⏭️"}
_STEP_STATE_WORD = {"done": "Done", "gate": "Awaiting your approval",
                    "reminder": "Do this — then mark done", "scheduled": "Not open yet",
                    "pending": "Pending", "skipped": "Skipped"}


def status_view(r: dict) -> str:
    """Human-readable status: next-action headline → phase map → current-phase steps.
    `r` is Orchestrator.status_report()."""
    mode = "DRY-RUN" if r["dry_run"] else "LIVE"
    bars = 20
    filled = round(bars * r["done"] / r["total"]) if r["total"] else 0
    bar = "█" * filled + "░" * (bars - filled)
    label = _STATE_LABEL.get(r["status"], r["status"])

    lines = [
        f"## Release {r['release_id']} · {mode} · {r['done']}/{r['total']} ({r['percent']}%)",
        f"`{bar}`",
    ]
    # Code Complete Date anchor line (when known).
    if r.get("ccd"):
        src = {"override": "override", "manual": "confirmed",
               "default": "2nd Wednesday"}.get(r.get("ccd_source"), r.get("ccd_source") or "")
        srctag = f" ({src})" if src else ""
        skip = "  ·  ⚠ release SKIP set in pipeline" if r.get("skip_release") else ""
        lines.append(f"**Code Complete:** {r['ccd']}{srctag}  ·  **today:** {r.get('as_of','')}{skip}")
        if r.get("ccd_conflict"):
            lines.append(f"⚠ **Confirm the date** — the pipeline override is **{r['ccd_conflict']}**, "
                         f"which differs from the 2nd-Wednesday default (**{r['ccd']}**). "
                         f"Which is the real Code Complete Date — the default, or the pipeline's?")
    if r.get("owner_email"):
        who = (f"{r['owner_name']} " if r.get("owner_name") else "") + f"{r['owner_email']}"
        lines.append(f"**Owner:** {who}")
    lines.append("")

    # 1) Next-action headline — the single most important thing.
    if r.get("halted"):
        rsn = f" — {r['halt_reason']}" if r.get("halt_reason") else ""
        lines.append(f"⛔ **HALTED**{rsn}. Nothing advances until you resume.")
    elif r["blocked"]:
        lines.append(f"⛔ **Blocked** — cannot start: {', '.join(r['blocked_items'])}. "
                     "Resolve it, or hand the release to someone who can.")
    elif not r["readiness_signed"]:
        lines.append("▣ **Entry gate** — the readiness checklist isn't signed yet.")
    elif r.get("scheduled"):
        sc = r["scheduled"]
        when = _delta_phrase(sc.get("opens_in_days"))
        lines.append(f"🗓 **Scheduled** — **{sc['phase_name']}** opens **{sc.get('opens','')}** "
                     f"({when}). Nothing to do yet.")
    elif r.get("action"):
        a = r["action"]
        lines.append(f"📌 **Action needed** — you need to: **{a['step_name']}** "
                     f"(Phase {_phase_num(r, a['phase'])} · {a['phase_name']}). Mark it done when complete.")
    elif r["gate"]:
        g = r["gate"]
        lines.append(f"⏸ **Next: your decision** — approve or deny **{g['step_name']}** "
                     f"(Phase {_phase_num(r, g['phase'])} · {g['phase_name']}).")
    elif r["status"] == "complete":
        lines.append("✔ **Release complete.** All phases done.")
    elif r["status"] == "not_started":
        lines.append("▶ **Not started yet** — run the next step to begin.")
    else:
        # in progress, between gates
        nxt = _next_pending_step(r)
        if nxt:
            lines.append(f"▶ **In progress** — next up: **{nxt}** "
                         f"({r.get('current_phase_name') or ''}).")
        else:
            lines.append(f"▶ **{label}.**")

    # 2) Phase map (overview).
    if r.get("phases"):
        lines += ["", "### Phases", "| | # | Phase | Done |", "|---|---|---|---|"]
        for p in r["phases"]:
            icon = _PHASE_ICON.get(p["state"], "⬜")
            note = ""
            if p["current"]:
                note = "  ← you are here"
            elif p["state"] == "scheduled" and p.get("opens"):
                note = f"  · opens {p['opens']} ({_delta_phrase(p.get('opens_in_days'))})"
            lines.append(f"| {icon} | {p['num']} | {p['name']}{note} | {p['done']}/{p['total']} |")
        lines.append("✅ done · ⏸ in progress · 🗓 scheduled · ⬜ not started")

    # 3) Current-phase detail (drill-down).
    if r.get("current_steps"):
        lines += ["", f"### ▶ Current phase — {r.get('current_phase_name','')}",
                  "| | Step | State |", "|---|---|---|"]
        for s in r["current_steps"]:
            icon = _STEP_ICON.get(s["state"], "⬜")
            word = _STEP_STATE_WORD.get(s["state"], s["state"])
            tag = " 🚦" if s["gate"] else (" 📌" if s.get("reminder") else "")
            lines.append(f"| {icon} | {s['name']}{tag} | {word} |")

    return "\n".join(lines)


def _delta_phrase(days) -> str:
    if days is None:
        return ""
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    return f"in {days} days" if days > 0 else f"{-days} days ago"


def _phase_num(r: dict, phase_id: str):
    for p in r.get("phases", []):
        if p["id"] == phase_id:
            return p["num"]
    return "?"


def _next_pending_step(r: dict):
    for s in r.get("current_steps", []):
        if s["state"] in ("pending", "gate"):
            return s["name"]
    return None


def notification_subject(r: dict) -> str:
    """Email subject for the daily phase digest (empty if nothing to send)."""
    ap = r.get("active_phase")
    if not ap:
        return f"Release {r.get('release_id','?')} — update"
    return f"Release {r.get('release_id','?')} — Phase {ap.get('num')} status"


def notification(r: dict) -> str:
    """The DAILY PHASE DIGEST emailed to the release owner, or "" to stay silent.
    `r` is Orchestrator.status_report(). The push automation sends whatever this
    returns; the once-per-day cadence is enforced by the CLI (last_notified_date).

    Model (established with the user):
      * Setup (readiness + CCD) is interactive in Scout — NO push. So an unsigned
        release, a blocked entry gate, or a halted release stay silent here.
      * The FIRST push is a phase opening (Phase 0 at CCD-7). Nothing before it
        (no pre-open heads-up).
      * While a phase is open with outstanding steps, report its status daily.
      * Each phase gets its own digest when it opens (general pattern).
    """
    rid = r.get("release_id", "?")
    if (r.get("halted") or r.get("blocked") or r.get("status") == "complete"
            or not r.get("readiness_signed")):
        return ""                              # setup / paused — no push

    ap = r.get("active_phase")
    if not ap or not ap.get("due"):
        return ""                              # nothing open yet (scheduled) — no push

    head = f"Release {rid} — Phase {ap['num']}: {ap['name']}"
    lines = [head]
    if not ap["started"]:
        opened = f" (opened {ap['opens']})" if ap.get("opens") else ""
        lines.append(f"Phase {ap['num']} has opened{opened} — {ap['total']} steps to work through, none done yet.")
    else:
        lines.append(f"Progress: {ap['done']} of {ap['total']} steps done.")

    # What the orchestrator has already handled automatically (this phase).
    completed = ap.get("completed") or []
    if completed:
        lines.append(f"Completed ({len(completed)}):")
        for name in completed[:8]:
            lines.append(f"  ✓ {name}")

    # What needs the owner right now (a live hold), then the human touchpoints ahead.
    if r.get("gate"):
        lines.append(f"Waiting on your decision: {r['gate']['step_name']} (approve or deny).")
    elif r.get("action"):
        lines.append(f"Action needed now: {r['action']['step_name']} (do it, then mark done).")

    human = [o for o in ap.get("outstanding", []) if o["gate"] or o["reminder"]]
    if human:
        lines.append(f"Still needs you ({len(human)}):")
        for o in human[:6]:
            what = "your approval" if o["gate"] else "your action"
            lines.append(f"  • {o['name']} — {what}")

    lines.append("Open Scout to continue the release.")
    return "\n".join(lines)


# ---- HTML digest (nice email UX) -------------------------------------------
# Email-safe: inline styles + table layout (Outlook-friendly), no external CSS.

def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# Per-status pill styling (label, text color, background).
_PILL = {
    "done":      ("✓ Done",              "#1a7f37", "#e6f4ea"),
    "now":       ("⚠ Needs you now",      "#b42318", "#fef3f2"),
    "blocked":   ("⛔ Blocked — fix & rerun", "#b42318", "#fef3f2"),
    "approval":  ("Your approval",        "#b54708", "#fffaeb"),
    "confirm":   ("Your confirmation",    "#b54708", "#fffaeb"),
    "action":    ("Your action",          "#b54708", "#fffaeb"),
    "auto":      ("Automatic — pending",  "#475467", "#f2f4f7"),
}


def _pill(status: str) -> str:
    label, fg, bg = _PILL.get(status, _PILL["auto"])
    return (f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;'
            f'font-size:12px;font-weight:600;color:{fg};background:{bg};'
            f'white-space:nowrap;">{label}</span>')


def notification_html(r: dict) -> str:
    """HTML version of the daily phase digest. Returns "" under the exact same
    silence rules as notification() (reuses it as the guard). Presents EVERY step
    in the active phase with a status pill, and flags what needs the owner now."""
    if not notification(r):                       # same silence rules / dedup gate
        return ""
    rid = _esc(r.get("release_id", "?"))
    ap = r.get("active_phase") or {}
    phase_title = _esc(f"Phase {ap.get('num')}: {ap.get('name','')}")
    done, total = ap.get("done", 0), ap.get("total", 0)
    pct = round(100 * done / total) if total else 0
    steps = ap.get("steps", [])
    dry = r.get("dry_run")

    # The single item that needs the owner right now (the live hold), if any.
    hold = r.get("gate") or r.get("action")
    hold_name = _esc(hold["step_name"]) if hold else ""
    hold_kind = "approve or deny" if r.get("gate") else "do it, then mark it done"

    # Rows: every step, with the active hold promoted to the "now" pill.
    rows = []
    for s in steps:
        st = "now" if s.get("now") else s.get("status", "auto")
        name = _esc(s.get("name", ""))
        star = (' <span style="color:#b42318;font-weight:700;">&#9873;</span>'
                if s.get("needs_owner") else "")
        rows.append(
            f'<tr><td style="padding:9px 12px;border-bottom:1px solid #eaecf0;'
            f'font-size:14px;color:#101828;">{name}{star}</td>'
            f'<td style="padding:9px 12px;border-bottom:1px solid #eaecf0;'
            f'text-align:right;">{_pill(st)}</td></tr>'
        )
    rows_html = "\n".join(rows)

    attention = ""
    if hold:
        attention = (
            f'<tr><td style="padding:0 24px 8px;">'
            f'<div style="background:#fef3f2;border:1px solid #fecdca;border-radius:8px;'
            f'padding:12px 14px;color:#b42318;font-size:14px;">'
            f'<strong>&#9873; Needs your attention:</strong> {hold_name} '
            f'&mdash; {hold_kind}.</div></td></tr>'
        )

    dry_badge = (
        '<span style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:10px;'
        'font-size:11px;font-weight:600;color:#475467;background:#f2f4f7;">DRY-RUN</span>'
        if dry else "")

    return f"""\
<div style="margin:0;padding:24px 0;background:#f5f6f8;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(16,24,40,0.08);">
  <tr><td style="background:#1f2a44;padding:20px 24px;">
    <div style="color:#ffffff;font-size:18px;font-weight:700;">Release {rid}{dry_badge}</div>
    <div style="color:#c3cad9;font-size:14px;margin-top:2px;">{phase_title}</div>
  </td></tr>
  <tr><td style="padding:18px 24px 6px;">
    <div style="font-size:14px;color:#475467;">Progress: <strong style="color:#101828;">{done} of {total}</strong> steps done ({pct}%)</div>
    <div style="margin-top:8px;height:8px;background:#eaecf0;border-radius:6px;overflow:hidden;">
      <div style="width:{pct}%;height:8px;background:#1a7f37;"></div>
    </div>
  </td></tr>
  {attention}
  <tr><td style="padding:8px 24px 4px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
      <tr><th style="text-align:left;padding:6px 12px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#667085;border-bottom:2px solid #eaecf0;">Task</th>
          <th style="text-align:right;padding:6px 12px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#667085;border-bottom:2px solid #eaecf0;">Status</th></tr>
      {rows_html}
    </table>
  </td></tr>
  <tr><td style="padding:16px 24px 24px;">
    <div style="font-size:13px;color:#667085;">Items marked <span style="color:#b42318;font-weight:700;">&#9873;</span> need you. Open Scout to continue the release.</div>
  </td></tr>
</table>
</div>"""

