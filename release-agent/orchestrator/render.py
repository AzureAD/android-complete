"""Presentation layer — turns structured data into text/markdown.

Kept separate from the logic (engine.py / readiness.py) so a different interface
(web UI, TUI, another frontend) can consume the same structured data and present
it its own way. These are pure functions: data in, string out. No state, no IO.
"""
from __future__ import annotations

import re

# First URL anywhere in a note (e.g. the wiki page link) — surfaced as a compact
# [link](…) in the Details column instead of a long raw URL.
_URL_RE = re.compile(r"https?://[^\s)>\]]+")


def _cell(text: str) -> str:
    """Make arbitrary text safe for a single markdown table cell: collapse all
    whitespace/newlines to one line and escape pipes."""
    return re.sub(r"\s+", " ", str(text)).replace("|", "\\|").strip()


def step_detail(s: dict, limit: int = 160) -> str:
    """One-line 'Details' summary of a step's execution, from its stored note.

    Generic — works for ANY step (current or future phases) because every step
    records its outcome as `note` (agent report, block reason, …) and may attach
    structured `links` ([{name,url}], e.g. the wiki page or CG alerts page).
    Prefers the stored `links` for the reference; falls back to the first URL found
    in the note. Empty when nothing ran yet; an em dash for outstanding items.
    """
    note = s.get("note")
    links = s.get("links") or []
    if not note and not links:
        state = s.get("state")
        return "—" if state in ("pending", "scheduled", "reminder", "gate") else ""
    text = str(note or "").strip()
    lead = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    # Prefer STRUCTURED links (first-class, stored on state) over URL-in-prose.
    if links:
        link_md = "  ·  ".join(f"[{l.get('name', 'link')}]({l['url']})"
                               for l in links if l.get("url"))
        lead_c = _cell(lead).rstrip(" :.—-")
        if len(lead_c) > limit:
            lead_c = lead_c[: limit - 1].rstrip() + "…"
        return f"{lead_c} · {link_md}" if lead_c else link_md
    url = _URL_RE.search(text)
    if url and url.group(0) in lead:
        # lead itself carries the URL — strip it, we re-add it as a clean link
        lead = lead.replace(url.group(0), "")
    lead_c = _cell(lead).rstrip(" :.—-")
    if len(lead_c) > limit:
        lead_c = lead_c[: limit - 1].rstrip() + "…"
    if url:
        link = f"[link]({url.group(0)})"
        return f"{lead_c} · {link}" if lead_c else link
    return lead_c


# ---- readiness entry gate (the frozen table) ----
_ICON = {"pass": "✅", "attested": "✅", "fail": "❌", "unable": "⛔",
         "degraded": "⚠️", "pending": "⬜"}
_STATUS_WORD = {"pass": "PASS", "attested": "Confirmed", "fail": "FAIL",
                "unable": "Unable", "degraded": "Proceeding (not silent)",
                "pending": "Outstanding"}


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


def attest_prompt(chk: dict) -> str:
    """Public: the '✋ Your confirmation needed' block on its own (no table), or a
    short status line when it's not time yet / nothing to attest. Rendered as the
    SECOND readiness output — after the full table (render #1) and the silent auto
    checks — so the table appears exactly ONCE while the attestation ask still comes
    deterministically from the engine (the item list is not hand-built by the skill)."""
    block = _attest_prompt_block(chk)
    if block:
        return block
    if chk.get("signed"):
        return "✅ All items satisfied — entry gate cleared. Ready to start Phase 0."
    # auto checks not yet complete
    auto_pending = [i["label"] for i in chk["items"]
                    if i["verify"] == "auto" and not i["satisfied"]]
    if auto_pending:
        return ("Automated checks still pending: " + ", ".join(auto_pending)
                + " — resolve these before the attestations.")
    return "No attestations outstanding."


def _attest_prompt_block(chk: dict) -> str:
    """The 'your confirmation needed' section, or '' when it's not time yet.
    Only returned when: gate unsigned, not blocked, ALL auto items satisfied, and
    at least one attest item is still outstanding."""
    auto = [i for i in chk["items"] if i["verify"] == "auto"]
    if auto and not all(i["satisfied"] for i in auto):
        return ""  # auto checks not finished — don't prompt for attestations yet
    outstanding = [i for i in chk["items"]
                   if i["verify"] == "attest" and not i["satisfied"]]
    if not outstanding:
        return ""
    out = ["---", "### ✋ Your confirmation needed",
           "The automated checks are done. I'll sign **only** what you explicitly "
           "confirm — please verify each:", ""]
    for it in outstanding:
        detail = it.get("detail") or it.get("text") or ""
        win = it.get("window")
        if win and win.get("start") and win.get("end"):
            detail = f"{detail} (window: {win['start']} → {win['end']})".strip()
        out.append(f"- **{_cell(it.get('label') or it['id'])}** — {_cell(detail)}")
    return "\n".join(out)


# Short "can't satisfy" card title + one-line description per attest item, so the
# m_ask_user card is self-describing even when no markdown table renders.
_ATTEST_CARD = {
    "play_console_access": ("I can't open Play Console", "Play Console dashboard doesn't load"),
    "oncall_window":       ("I'm on-call during the window", "Scheduled Android on-call during the release window"),
    "saw_ame":             ("I don't have a SAW machine", "Can't access SAW / AME"),
    "yubikey":             ("I don't have a YubiKey", "No YubiKey in hand"),
}


def attest_prompt_payload(chk: dict, release_id: str) -> dict:
    """DETERMINISTIC m_ask_user payload for the readiness attestations — the engine
    owns the exact question + answer cards so the (always-rendered) Scout card is the
    source of truth, independent of whether the markdown table rendered.

    Returns a dict:
      { ready: bool, release, question, answers:[{title,description,action,item?}],
        confirm_items:[ids], recommendedIndex, reason }
    `ready` is False (with `reason`) when it's not time to attest yet (auto checks
    pending) or nothing is outstanding — the skill should not prompt in that case."""
    auto = [i for i in chk["items"] if i["verify"] == "auto"]
    if auto and not all(i["satisfied"] for i in auto):
        pend = ", ".join(i["label"] for i in auto if not i["satisfied"])
        return {"ready": False, "release": release_id,
                "reason": f"auto checks pending: {pend}"}
    outstanding = [i for i in chk["items"]
                   if i["verify"] == "attest" and not i["satisfied"]]
    if not outstanding:
        return {"ready": False, "release": release_id,
                "reason": "no attestations outstanding"}

    # "All confirmed" description: concise per-item summary (with the on-call dates).
    bits = []
    for it in outstanding:
        lbl = it.get("label") or it["id"]
        win = it.get("window")
        if it["id"] == "oncall_window" and win and win.get("start") and win.get("end"):
            bits.append(f"free of on-call {win['start']}–{win['end']}")
        else:
            bits.append(lbl)
    confirm_desc = " · ".join(bits)

    answers = [{"title": "All confirmed", "description": _cell(confirm_desc),
                "action": "confirm_all"}]
    for it in outstanding:
        title, desc = _ATTEST_CARD.get(
            it["id"], (f"Can't satisfy {it.get('label') or it['id']}", ""))
        answers.append({"title": title, "description": desc,
                        "action": "decline", "item": it["id"]})

    return {
        "ready": True,
        "release": release_id,
        "question": (f"Automated readiness checks passed — confirm all "
                     f"{len(outstanding)} remaining item(s) to start release {release_id}?"),
        "answers": answers,
        "confirm_items": [it["id"] for it in outstanding],
        "recommendedIndex": 0,
    }



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
              "pending": "⬜", "skipped": "⏭️", "scout": "🤖", "blocked": "⛔"}
_STEP_STATE_WORD = {"done": "Done", "gate": "Awaiting your approval",
                    "reminder": "Do this — then mark done", "scheduled": "Not open yet",
                    "pending": "Pending", "skipped": "Skipped",
                    "scout": "Scout runs this — automatic", "blocked": "Blocked — needs you"}


def _pipelines_line(r: dict) -> str:
    """Compact one-line summary of the Phase-2 release-pipeline runs recorded on state
    (checker → orchestrator → the LATEST RC's two MRWP runs). Empty string when none
    resolved yet. Reads the nested pipeline_runs schema (migrating a legacy flat shape);
    no live az call in the render path."""
    from orchestrator.state import migrate_pipeline_runs
    pr = migrate_pipeline_runs(r.get("pipeline_runs") or {})
    parts = []
    ch = pr.get("checker") or {}
    if ch.get("run_id"):
        parts.append(f"checker {ch['run_id']}")
    o = pr.get("orchestrator") or {}
    if o.get("run_id"):
        v = o.get("versions") or {}
        vstr = ", ".join(f"{k} {v[k]}" for k in ("Common", "Msal", "Broker") if v.get(k))
        parts.append(f"orchestrator {o['run_id']}" + (f" ({vstr})" if vstr else ""))
    rcs = pr.get("rcs") or []
    rc = rcs[-1] if rcs else {}
    mr = []
    if (rc.get("ecs") or {}).get("run_id"):
        mr.append(f"ECS {rc['ecs']['run_id']}")
    if (rc.get("local") or {}).get("run_id"):
        mr.append(f"Local {rc['local']['run_id']}")
    if mr:
        tag = f" (RC{rc['rc']})" if rc.get("rc") and len(rcs) > 1 else ""
        parts.append("MRWP" + tag + " " + " / ".join(mr))
    return " · ".join(parts)


def status_view(r: dict) -> str:
    """Human-readable status: next-action headline → phase map → current-phase steps.
    `r` is Orchestrator.status_report()."""
    bars = 20
    filled = round(bars * r["done"] / r["total"]) if r["total"] else 0
    bar = "█" * filled + "░" * (bars - filled)
    label = _STATE_LABEL.get(r["status"], r["status"])

    lines = [
        f"## Release {r['release_id']} · {r['done']}/{r['total']} ({r['percent']}%)",
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

    # 2) Phase map (overview). Icon is prefixed onto the Phase name (no separate
    # empty-header icon column — that renders with a huge gap in Scout's tables).
    if r.get("phases"):
        lines += ["", "### Phases", "| # | Phase | Done |", "|---|---|---|"]
        for p in r["phases"]:
            icon = _PHASE_ICON.get(p["state"], "⬜")
            note = ""
            if p["current"]:
                note = "  ← you are here"
            elif p["state"] == "scheduled" and p.get("opens"):
                note = f"  · opens {p['opens']} ({_delta_phrase(p.get('opens_in_days'))})"
            lines.append(f"| {p['num']} | {icon} {p['name']}{note} | {p['done']}/{p['total']} |")
        # Blank line BEFORE the legend so markdown ends the table and renders the
        # legend as its own caption paragraph — otherwise it's absorbed as a row
        # (all glued into one column).
        lines += ["", "_✅ done · ⏸ in progress · 🗓 scheduled · ⬜ not started_"]

    # 3) Current-phase detail (drill-down). Icon prefixed onto the Step name.
    # A third "Details" column captures each step's execution outcome (from its
    # stored note): where a lockdown clashed, the breaking change, CG alerts found,
    # the created wiki link, etc. Generic — any step that records a note shows it.
    if r.get("current_steps"):
        lines += ["", f"### ▶ Current phase — {r.get('current_phase_name','')}",
                  "| Step | State | Details |", "|---|---|---|"]
        for s in r["current_steps"]:
            icon = _STEP_ICON.get(s["state"], "⬜")
            word = _STEP_STATE_WORD.get(s["state"], s["state"])
            tag = " 🚦" if s["gate"] else (" 📌" if s.get("reminder") else "")
            detail = step_detail(s)
            lines.append(f"| {icon} {s['name']}{tag} | {word} | {detail} |")

        # Phase-2 pipeline run ids (checker/orchestrator/MRWP) — recorded on state as
        # the verification steps resolve the chain, so they show without a live read.
        pl = _pipelines_line(r)
        if pl:
            lines += ["", f"**Pipelines:** {pl}"]

        # 3b) Expanded detail — only for steps whose note has MORE than the one-line
        # summary the column shows (e.g. the CG report's full High/Critical CVE list,
        # or the breaking-change draft comms). Simple one-line notes (lockdown "no
        # overlap", cron "firing") are already fully shown in the column, so they're
        # not repeated here. Generic: any step with a multi-line note expands.
        def _multiline(note):
            return len([ln for ln in str(note).splitlines() if ln.strip()]) > 1

        rich = [s for s in r["current_steps"]
                if (s.get("note") and _multiline(s["note"])) or s.get("links")]
        if rich:
            lines += ["", "### Step details"]
            for s in rich:
                mark = _STEP_ICON.get(s["state"], "•")
                note_lines = [ln.rstrip() for ln in str(s.get("note") or "").strip().split("\n")]
                body = "  \n".join(ln for ln in note_lines if ln)  # markdown hard breaks
                lines += ["", f"{mark} **{s['name']}**  ", body]
                for l in (s.get("links") or []):
                    if l.get("url"):
                        lines.append(f"🔗 [{l.get('name', 'link')}]({l['url']})  ")

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


def _digest_model(r: dict):
    """The digest's content model, or None to STAY SILENT — the single source of
    truth shared by the plain-text and markdown renderers (the HTML email uses a
    fuller step table but reuses this as its silence gate).

    Silence rules (established with the user):
      * Setup (readiness + CCD) is interactive in Scout — NO push. An unsigned
        release, a blocked entry gate, or a halted/complete release stay silent.
      * The FIRST push is a phase opening (Phase 0 at CCD-7). Nothing before it.
      * While a phase is open (due) with outstanding steps, report status daily.
    """
    if (r.get("halted") or r.get("blocked") or r.get("status") == "complete"
            or not r.get("readiness_signed")):
        return None                            # setup / paused — no push
    ap = r.get("active_phase")
    if not ap or not ap.get("due"):
        return None                            # nothing open yet (scheduled) — no push
    # Scout still owes automatic steps on the open phase (notice / reminders / lockdown
    # not yet run). The digest reports the *settled* "here's what needs YOU" picture, so
    # sending it now would be premature and half-run — stay silent until Scout drains its
    # own steps (a blocked scout step is status 'blocked', not 'scout', so it still pushes).
    if r.get("scout_pending"):
        return None

    hold = None
    if r.get("gate"):
        hold = ("gate", r["gate"]["step_name"])
    elif r.get("action"):
        hold = ("action", r["action"]["step_name"])
    human_all = [o for o in ap.get("outstanding", []) if o["gate"] or o["reminder"]]
    completed_all = ap.get("completed") or []
    # Phase-2 RC one-liner — only while a phase that opts in (show_pipeline_runs) is
    # active, best-effort (reads state.pipeline_runs; never a live call). Empty until the
    # chain resolves.
    rc_line = _pipelines_line(r) if ap.get("show_pipeline_runs") else ""
    return {
        "rid": r.get("release_id", "?"),
        "ap": ap,
        "started": bool(ap.get("started")),
        "completed": completed_all[:8],
        "completed_total": len(completed_all),
        "hold": hold,                          # (kind, step_name) or None
        "human": human_all[:6],
        "human_total": len(human_all),
        "pipelines": rc_line,                  # RC id one-liner (build_verify only)
    }


def _progress_line(m: dict, bold: bool = False) -> str:
    """The opened/progress line, shared by both text renderers."""
    ap = m["ap"]
    if not m["started"]:
        opened = f" (opened {ap['opens']})" if ap.get("opens") else ""
        total = f"**{ap['total']}**" if bold else str(ap["total"])
        return f"Phase {ap['num']} has opened{opened} — {total} steps to work through, none done yet."
    prog = f"**{ap['done']} of {ap['total']}**" if bold else f"{ap['done']} of {ap['total']}"
    return f"Progress: {prog} steps done."


def notification(r: dict) -> str:
    """The DAILY PHASE DIGEST emailed to the release owner, or "" to stay silent.
    `r` is Orchestrator.status_report(). Plain-text form (email fallback / logs);
    the markdown and HTML forms render the same model differently. The once-per-day
    cadence is enforced by the CLI (last_notified_date)."""
    m = _digest_model(r)
    if m is None:
        return ""
    ap = m["ap"]
    lines = [f"Release {m['rid']} — Phase {ap['num']}: {ap['name']}", _progress_line(m)]
    if m.get("pipelines"):
        lines.append(f"RC pipelines: {m['pipelines']}")
    if m["completed"]:
        lines.append(f"Completed ({m['completed_total']}):")
        lines += [f"  ✓ {name}" for name in m["completed"]]
    if m["hold"]:
        kind, name = m["hold"]
        lines.append(f"Waiting on your decision: {name} (approve or deny)." if kind == "gate"
                     else f"Action needed now: {name} (do it, then mark done).")
    if m["human"]:
        lines.append(f"Still needs you ({m['human_total']}):")
        lines += [f"  • {o['name']} — {'your approval' if o['gate'] else 'your action'}"
                  for o in m["human"]]
    lines.append("Open Scout to continue the release.")
    return "\n".join(lines)


def notification_markdown(r: dict) -> str:
    """Teams-bot-friendly MARKDOWN digest — same content as notification() but with
    blank-line paragraph breaks, `-` bullets and `**bold**`. The Scout bot renders
    markdown and COLLAPSES single newlines, so the plain-text form would arrive as
    one run-on paragraph. Empty under the same silence rules."""
    m = _digest_model(r)
    if m is None:
        return ""
    ap = m["ap"]
    blocks = [f"**Release {m['rid']} — Phase {ap['num']}: {ap['name']}**", _progress_line(m, bold=True)]
    if m.get("pipelines"):
        blocks.append(f"**RC pipelines:** {m['pipelines']}")
    if m["completed"]:
        blocks.append("\n".join([f"**Completed ({m['completed_total']}):**"]
                                + [f"- ✓ {n}" for n in m["completed"]]))
    if m["hold"]:
        kind, name = m["hold"]
        blocks.append(f"**Waiting on your decision:** {name} (approve or deny)." if kind == "gate"
                      else f"**Action needed now:** {name} (do it, then mark done).")
    if m["human"]:
        rows = [f"**Still needs you ({m['human_total']}):**"]
        rows += [f"- {o['name']} — {'your approval' if o['gate'] else 'your action'}"
                 for o in m["human"]]
        blocks.append("\n".join(rows))
    blocks.append("_Open Scout to continue the release._")
    return "\n\n".join(blocks)      # blank line between blocks survives markdown collapse


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
    "scout":     ("Scout runs this",       "#475467", "#f2f4f7"),
    "auto":      ("Automatic — pending",  "#475467", "#f2f4f7"),
}


def _pill(status: str) -> str:
    label, fg, bg = _PILL.get(status, _PILL["auto"])
    return (f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;'
            f'font-size:12px;font-weight:600;color:{fg};background:{bg};'
            f'white-space:nowrap;">{label}</span>')


def notification_html(r: dict) -> str:
    """HTML version of the daily phase digest. Returns "" under the exact same
    silence rules as the other renderers (shares `_digest_model`). Presents EVERY
    step in the active phase with a status pill, and flags what needs the owner now."""
    if _digest_model(r) is None:                  # same silence rules / dedup gate
        return ""
    rid = _esc(r.get("release_id", "?"))
    ap = r.get("active_phase") or {}
    phase_title = _esc(f"Phase {ap.get('num')}: {ap.get('name','')}")
    done, total = ap.get("done", 0), ap.get("total", 0)
    pct = round(100 * done / total) if total else 0
    steps = ap.get("steps", [])

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

    return f"""\
<div style="margin:0;padding:24px 0;background:#f5f6f8;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(16,24,40,0.08);">
  <tr><td style="background:#1f2a44;padding:20px 24px;">
    <div style="color:#ffffff;font-size:18px;font-weight:700;">Release {rid}</div>
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

