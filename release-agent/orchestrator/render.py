"""Presentation layer — turns structured data into text/markdown.

Kept separate from the logic (engine.py / readiness.py) so a different interface
(web UI, TUI, another frontend) can consume the same structured data and present
it its own way. These are pure functions: data in, string out. No state, no IO.
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from orchestrator import schedule

# First URL anywhere in a note (e.g. the wiki page link) — surfaced as a compact
# [link](…) in the Details column instead of a long raw URL.
_URL_RE = re.compile(r"https?://[^\s)>\]]+")


def _cell(text: str) -> str:
    """Make arbitrary text safe for a single markdown table cell: collapse all
    whitespace/newlines to one line and escape pipes."""
    return re.sub(r"\s+", " ", str(text)).replace("|", "\\|").strip()


def _markdown_url(value: str) -> str:
    """Normalize URL destinations so markdown links stay clickable.

    SharePoint web URLs may contain raw spaces (for example "Shared Documents"),
    which break markdown link destinations. Encode path/query/fragment safely while
    preserving already-encoded bytes.
    """
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return text
    path = quote(unquote(parts.path), safe="/-._~!$&'()*+,;=:@")
    query = quote(unquote(parts.query), safe="=&-._~!$'()*+,;:@/?")
    fragment = quote(unquote(parts.fragment), safe="-._~!$&'()*+,;=:@/?")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def _execution_guidance(execution: dict, status=None) -> str:
    if execution.get("notification_id"):
        return ("Do not repeat automatically. Check the previous notification's delivery. "
                "A new attempt or manual closure requires the owner's stopped-runner confirmation.")
    if execution.get("effect_mode"):
        return ("Do not repeat automatically. Reconcile the previous action, or explicitly request "
                "a fresh attempt/manual closure after confirming the prior runner stopped.")
    if execution.get("approval"):
        return ("Scout needs to check the existing pipeline approval. "
                "It will recover that decision rather than submit another approval.")
    if execution.get("write_progress"):
        return ("Continue with the same checked command. Scout verifies saved receipts and "
                "reviews only remaining actions. Owner-confirmed manual results/completion "
                "are supported; never clear ownership or blindly retry unknown writes.")
    if execution.get("write_review"):
        return ("Scout is checking the previous write's outcome before another attempt. "
                "An unknown result will not be repeated.")
    return ("Another release session owns this action. Scout needs to check its outcome "
            "before starting another attempt.")


def step_detail(s: dict, limit: int | None = None) -> str:
    """One-line 'Details' summary of a step's execution, from its stored note.

    Generic — works for ANY step (current or future phases) because every step
    records its outcome as `note` (agent report, block reason, …) and may attach
    structured `links` ([{name,url}], e.g. the wiki page or CG alerts page).
    Prefers the stored `links` for the reference; falls back to the first URL found
    in the note. Empty when nothing ran yet; an em dash for outstanding items.
    Blocked/in-flight steps default to a generous 500-char limit so actionable
    block reasons are never cut off mid-sentence.
    """
    state = s.get("state") or s.get("status")
    if limit is None:
        limit = 320 if state in ("blocked", "in_flight", "fail", "unable") else 160
    note = s.get("note")
    if s.get("execution"):
        note = _execution_guidance(s["execution"], state)
    links = s.get("links") or []
    if not note and not links:
        return "—" if state in ("pending", "scheduled", "reminder", "gate", "auto") else ""
    text = str(note or "").strip()
    lead = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    # Prefer STRUCTURED links (first-class, stored on state) over URL-in-prose.
    if links:
        link_md = "  ·  ".join(f"[{l.get('name', 'link')}]({_markdown_url(l['url'])})"
                               for l in links if l.get("url"))
        # Avoid showing a truncated raw URL when we already have structured links.
        lead = _URL_RE.sub("", lead).strip()
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
        link = f"[link]({_markdown_url(url.group(0))})"
        return f"{lead_c} · {link}" if lead_c else link
    return lead_c


# ---- readiness entry gate (the frozen table) ----
_ICON = {"pass": "✅", "attested": "✅", "skipped": "⏭️", "fail": "❌", "unable": "⛔", "needs_input": "⏸",
         "degraded": "⚠️", "pending": "⬜"}
_STATUS_WORD = {"pass": "PASS", "attested": "Confirmed", "skipped": "Skipped (owner)", "fail": "FAIL",
                "unable": "Unable", "degraded": "Proceeding (not silent)",
                "pending": "Outstanding", "needs_input": "Input needed"}


def readiness_followup(chk: dict, release_id: str) -> str:
    from orchestrator.readiness import dispatch_plan
    plan = chk.get("dispatch") or dispatch_plan(chk, release_id)
    lines = [plan["instruction"]]
    if plan["pending_auto_checks"]:
        lines.append("**Scout queue (not owner tasks):**")
        for item in plan["pending_auto_checks"]:
            lines.append(f"- `{item['id']}`: queued for Scout verification")
    if plan["next_action"] != "complete":
        lines.append("Readiness continues through the AI agent until all Scout checks are exhausted.")
        lines.append("Scout may still need to finish executable checks before control can return to you.")
    return "\n".join(lines)


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
    if chk.get("owner_email"):
        who = f"{chk.get('owner_name') or ''} {chk['owner_email']}".strip()
        lines[2:2] = [f"**Owner:** {_cell(who)}", ""]
    for it in chk["items"]:
        stale = it["status"] in ("pass", "degraded") and not it["satisfied"]
        status = "pending" if stale else it["status"]
        box = _ICON.get(status, "⬜")
        typ = "`[auto]`" if it["verify"] == "auto" else "`[attest]`"
        label = it.get("label") or it["id"]
        if it.get("checks") and not stale:
            parts = [f"[{c['name']}]({c['url']}) {'✓' if c.get('ok') else '✗'}"
                     if c.get("url") else f"{c['name']} {'✓' if c.get('ok') else '✗'}"
                     for c in it["checks"]]
            detail = " · ".join(parts)
        else:
            detail = it.get("detail") or it.get("text") or ""
        win = it.get("window")
        if win and win.get("start") and win.get("end"):
            detail = f"{detail} (window: {win['start']} → {win['end']})".strip()
        if stale:
            detail += " — prior evidence no longer matches this release; Scout must recheck."
        elif status in ("fail", "unable", "degraded", "needs_input") and it.get("message"):
            detail = f"{detail} — {it['message']}".strip(" —")
        item_cell = f"**{_cell(label)}** — {_cell(detail)}"
        lines.append(f"| {box} | {typ} | {item_cell} | {_STATUS_WORD.get(status, status)} |")
    lines.append("")

    if chk["blocked"]:
        labels = [next((i["label"] for i in chk["items"] if i["id"] == b), b)
                  for b in chk["blocked_items"]]
        lines.append("⛔ **Blocked** — cannot start: " + ", ".join(labels) + ".")
        lines.append(_cell(chk["blocked_message"]))
    elif chk["signed"]:
        lines.append("✅ **All readiness items satisfied — entry gate cleared.**")
    else:
        failed_items = [i for i in chk["items"] if i["status"] == "fail" and not i["satisfied"]]
        if failed_items:
            from .remediation import readiness_remediation
            lines.append("❌ **Failed checks:**")
            for fi in failed_items:
                reason = fi.get("message") or "check failed"
                rem = readiness_remediation(release_id or "<YYYY-MM>", fi["id"], reason)
                lines.append(
                    f"- **{_cell(fi['label'])}**: {_cell(reason)}\n"
                    f"  - *System:* {rem['system']} · *Fix:* {rem['how_to_fix']}\n"
                    f"  - *Escape hatch:* `{rem['complete_manually_command']}` or `{rem['skip_command']}`"
                )
        pending = [i["label"] for i in chk["items"] if not i["satisfied"]]
        lines.append("**Outstanding:** " + ", ".join(pending))
    if not chk["signed"]:
        lines.extend(["", readiness_followup(chk, release_id)])
    if not (chk.get("dispatch") or {}).get("continue_automatically"):
        lines.extend([
            "",
            "---",
            '💡 **Quick commands:** `"status"` (where am I) · `"continue"` (advance ready checks) '
            '· `"explain <check>"` · `"help"` (all commands)',
        ])
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
    if chk.get("blocked"):
        return "Readiness is blocked; resolve the recorded decline before attesting."
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
    if chk.get("blocked"):
        return ""
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
# m_ask_user card is self-describing alongside the mandatory full checklist.
_ATTEST_CARD = {
    "play_console_access": ("I can't open Play Console", "Play Console dashboard doesn't load"),
    "oncall_window":       ("I'm on-call during the window", "Scheduled Android on-call during the release window"),
    "saw_ame":             ("I don't have a SAW machine", "Can't access SAW / AME"),
    "yubikey":             ("I don't have a YubiKey", "No YubiKey in hand"),
}


def attest_prompt_payload(chk: dict, release_id: str) -> dict:
    """Build the mandatory checklist display and exact readiness question together.

    Returns a dict:
      { ready: bool, release, question, answers:[{title,description,action,item?}],
        confirm_items:[ids], recommendedIndex, reason, display_markdown,
        next_action, continue_automatically, pending_auto_checks, failed_auto_checks }
    The caller displays display_markdown before prompting or ending the iteration.
    `ready` is False (with `reason`) when it's not time to attest yet (auto checks
    pending) or nothing is outstanding — the skill should not prompt in that case."""
    from orchestrator.readiness import dispatch_plan
    plan = chk.get("dispatch") or dispatch_plan(chk, release_id)
    display = {
        **plan,
        "release": release_id,
        "display_markdown": readiness_table(chk, release_id),
        "render_instruction": (
            "Emit display_markdown directly as raw live Markdown in your reply "
            "(starting directly with ### Readiness Entry Gate) — NEVER wrap it in ```markdown or ``` code fences!"
        ),
    }
    if plan["next_action"] not in ("attest", "complete"):
        reason = ("auto checks pending: " + ", ".join(i["id"] for i in plan["pending_auto_checks"])
                  if plan["continue_automatically"] else plan["instruction"])
        return {**display, "ready": False, "reason": reason}
    outstanding = [i for i in chk["items"]
                   if i["verify"] == "attest" and not i["satisfied"]]
    if not outstanding:
        return {**display, "ready": False, "next_action": "complete",
                "reason": "no attestations outstanding"}
    # Reserve one of the host's five answer slots for confirmation.
    remaining = outstanding[4:]
    outstanding = outstanding[:4]

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
        **display,
        "ready": True,
        "next_action": "attest",
        "question": (
            f"Automated readiness checks passed - confirm these {len(outstanding)} "
            f"item(s) for release {release_id}? {len(remaining)} more will follow."
            if remaining else
            f"Automated readiness checks passed - confirm all "
            f"{len(outstanding)} remaining item(s) to start release {release_id}?"),
        "answers": answers,
        "confirm_items": [it["id"] for it in outstanding],
        "remaining_items": [it["id"] for it in remaining],
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
    "cancelled": "Cancelled",
    "complete": "Complete",
}
_PHASE_ICON = {"done": "✅", "current": "⏸", "pending": "⬜", "scheduled": "📅"}
_STEP_ICON = {"done": "✅", "gate": "⏸", "reminder": "📌", "scheduled": "📅",
              "pending": "⬜", "skipped": "⏭️", "scout": "🤖", "auto": "🤖", "blocked": "⛔",
              "in_flight": "⏳", "running": "⏳"}
_STEP_STATE_WORD = {"done": "Done", "gate": "Awaiting your approval",
                    "reminder": "Do this — then mark done", "scheduled": "Not open yet",
                    "pending": "Pending", "skipped": "Skipped",
                    # 'auto' (engine-run agent step) and 'scout' (skill-run MCP step) are BOTH
                    # Scout's automatic work — shown identically so the human sees one message
                    # (no action needed), never a confusing "Pending" next to "automatic".
                    "auto": "Scout runs this — automatic",
                    "scout": "Scout runs this — automatic", "blocked": "Blocked — needs you",
                    "in_flight": "Work in progress — Scout is polling",
                    "running": "Execution reserved — do not repeat"}


def _pipelines_line(r: dict) -> str:
    """Compact one-line summary of the Phase-2 release-pipeline runs recorded on state
    (checker → orchestrator → the LATEST RC's two MRWP runs). Empty string when none
    resolved yet. Reads the nested pipeline_runs schema; no live az call in the render path."""
    from tools.pipelines import format_release_versions
    pr = r.get("pipeline_runs") or {}
    parts = []
    ch = pr.get("checker") or {}
    if ch.get("run_id"):
        parts.append(f"checker {ch['run_id']}")
    o = pr.get("orchestrator") or {}
    if o.get("run_id"):
        vstr = format_release_versions(r.get("versions"))
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


def work_followup(plan: dict) -> str:
    if not plan:
        return ""
    lines = [plan["instruction"]]
    if plan.get("pending_work"):
        lines.append("**Next actions before owner handoff:**")
        for item in plan["pending_work"]:
            label = item.get("name") or item.get("key", "task")
            command = item.get("command")
            if command:
                lines.append(f"- **{_cell(label)}** — run `{command}`.")
            else:
                lines.append(f"- **{_cell(label)}** — queued for Scout execution.")
    for item in plan.get("deferred_work", []):
        lines.append(f"- `{item['key']}` remains incomplete: {_cell(item['reason'])} (host-reported hold).")
    held = plan.get("held_work") or []
    if held:
        lines.append("**Held executions (recover exactly, do not replay):**")
        for item in held:
            key = item.get("key") or f"{item.get('phase')}.{item.get('step')}"
            status = item.get("status") or "held"
            execution = item.get("execution_id")
            command = item.get("resume_command")
            tail = f" (execution `{execution}`)" if execution else ""
            if command:
                lines.append(f"- `{key}` is `{status}`{tail}: run `{command}`.")
            else:
                lines.append(f"- `{key}` is `{status}`{tail}.")
    if plan.get("pending_work") or plan.get("deferred_work"):
        lines.append("Scout must finish these actions before control can return to you.")
    return "\n".join(lines)


def status_brief(r: dict) -> str:
    """Local inspection result; the full checklist is an explicit detail view."""
    focus = r.get("gate") or r.get("action") or {}
    next_step = focus.get("step_name") or r.get("current_step_name")
    label = _STATE_LABEL.get(r["status"], r["status"])
    lines = [
        f"Release {r['release_id']}",
        f"Checklist: {r['done']}/{r['total']} ({r['percent']}%). "
        + label,
    ]
    if r.get("current_phase_name"):
        lines.append(f"Phase: {r['current_phase_name']}")
    if r.get("readiness") and not r["readiness_signed"]:
        pending = [item["label"] for item in r["readiness"]["items"]
                   if not item["satisfied"]]
        if pending:
            lines.append("Required: " + ", ".join(pending))
    elif next_step:
        lines.append(f"Next: {next_step}")
    if r.get("halt_reason"):
        lines.append(f"Hold: {r['halt_reason']}")
    counts = r.get("completion_provenance") or {}
    if counts.get("waived") or counts.get("owner_attested") or counts.get("manual_only"):
        lines.append(
            f"Provenance: {counts.get('verified', 0)} verified, "
            f"{counts.get('owner_attested', 0)} owner-attested, "
            f"{counts.get('waived', 0)} waived; {counts.get('manual_only', 0)} manual-only tasks."
        )
    return "\n".join(lines)


def status_view(r: dict) -> str:
    """Human-readable status: next-action headline → phase map → current-phase steps.
    `r` is Orchestrator.status_report()."""
    if (r.get("readiness") and not r["readiness_signed"]
            and not r.get("halted") and not r.get("skip_release")
            and not any(issue.get("severity") == "error" for issue in r.get("invariant_violations", []))):
        return readiness_table(r["readiness"], r["release_id"])
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
    try:
        from orchestrator import environment as _env
        prof = _env.active_profile_name()
        if prof != "prod":
            lines.append(
                f"**Profile:** 🧪 **{prof}** *(safe targets: emails → {r.get('owner_email') or 'owner'} · Teams → self-chat)*"
            )
    except Exception:  # noqa: BLE001
        pass
    lines.append("")

    # 1) Next-action headline — the single most important thing.
    invariant_errors = [
        issue["message"] for issue in r.get("invariant_violations", [])
        if issue.get("severity") == "error"
    ]
    if invariant_errors:
        lines.append("⛔ **State integrity requires owner review.** No new work is authorized.")
        lines.extend(f"- {_cell(message)}" for message in invariant_errors)
        lines.append("Preserve execution ownership; use verified recovery rather than guessing success or resending.")
    elif r.get("status") == "cancelled":
        lines.append("⛔ **Release cancelled.** Clear `skip-release` before continuing.")
    elif r.get("halted"):
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
        lines.append(f"📅 **Scheduled** — **{sc['phase_name']}** opens **{sc.get('opens','')}** "
                     f"({when}). Nothing to do yet.")
    elif (r.get("work_dispatch") or {}).get("continue_automatically"):
        lines.append("🤖 **Scout work remains — continue before an owner decision.** "
                     "Independent tasks are still eligible; the owner-facing hold does not stop them.")
    elif r.get("action"):
        a = r["action"]
        if a.get("execution"):
            lines.append(f"**Execution recovery needed — {a['step_name']}.** {_execution_guidance(a['execution'])}")
        elif a.get("reason") == "blocked":
            lines.append(f"⛔ **Blocked check — {a['step_name']}.** "
                         "Fix and rerun the owning check, or review an explicit override; "
                         "a generic completion is not a passing result.")
        else:
            lines.append(f"📌 **Action needed** — you need to: **{a['step_name']}** "
                         f"(Phase {_phase_num(r, a['phase'])} · {a['phase_name']}). "
                         "Tell the AI agent when complete so it can record the result.")
    elif r["gate"]:
        g = r["gate"]
        if g.get("kind") == "denied":
            lines.append(f"⛔ **Gate denied — {g['step_name']}.** "
                         f"{g.get('reason') or 'Owner review is required before reconsidering.'}")
        else:
            lines.append(f"⏸ **Next: your decision** — approve or deny **{g['step_name']}** "
                         f"(Phase {_phase_num(r, g['phase'])} · {g['phase_name']}) through the AI agent.")
    elif r.get("scout_pending"):
        pending = set(r.get("scout_pending") or [])
        step = next((s for s in r.get("current_steps", []) if s.get("id") in pending), None)
        name = step["name"] if step else next(iter(pending), "Scout work")
        lines.append(f"🤖 **Scout action pending** — Scout needs to run: **{name}** "
                     f"({r.get('current_phase_name') or 'current phase'}).")
    elif r["status"] == "complete":
        lines.append("✔ **Release complete.** All phases done.")
    elif r["status"] == "not_started":
        lines.append("▶ **Not started yet** — ask the AI agent to continue when you want to begin.")
    else:
        # in progress, between gates
        nxt = _next_pending_step(r)
        if nxt:
            lines.append(f"▶ **In progress** — next up: **{nxt}** "
                         f"({r.get('current_phase_name') or ''}).")
        else:
            lines.append(f"▶ **{label}.**")

    failed_steps = [
        s for s in r.get("current_steps", [])
        if s.get("state") == "blocked"
    ]
    if failed_steps and not (r.get("work_dispatch") or {}).get("continue_automatically"):
        from .remediation import step_remediation
        lines.append("")
        lines.append("⛔ **Actionable fixes & manual escape hatches:**")
        cur_phase = r.get("current_phase") or "<phase>"
        rid = r.get("release_id") or "<YYYY-MM>"
        for fs in failed_steps:
            sid = fs.get("id", "<step>")
            sname = fs.get("name") or f"{cur_phase}.{sid}"
            detail = step_detail(fs, limit=240) or "Step failed; see details."
            rem = step_remediation(rid, cur_phase, sid, fs.get("note") or detail)
            links_md = " · ".join(
                f"[{lnk.get('name', 'link')}]({_markdown_url(lnk['url'])})"
                for lnk in (fs.get("links") or [])
                if lnk.get("url")
            )
            links_suffix = f" · {links_md}" if links_md else ""
            lines.append(
                f"- **{sname}** — *System:* {rem['system']} · *Fix:* {rem['how_to_fix']}{links_suffix}\n"
                f"  - *Escape hatch:* `{rem['complete_manually_command']}` or `{rem['skip_command']}`"
            )
            if isinstance(fs.get("ai_diagnosis"), dict) and fs["ai_diagnosis"].get("headline"):
                from . import diagnosis as DIAG
                lines.extend(DIAG.format_diagnosis_markdown(fs["ai_diagnosis"], indent="  "))

    if r.get("gate") and not (r.get("work_dispatch") or {}).get("continue_automatically"):
        gate_step_row = next(
            (s for s in r.get("current_steps", []) if s.get("id") == r["gate"].get("step")),
            None,
        )
        if gate_step_row and isinstance(gate_step_row.get("ai_diagnosis"), dict) and gate_step_row["ai_diagnosis"].get("headline"):
            from . import diagnosis as DIAG
            lines.append("")
            lines.append(f"🔍 **Failure Triage for `{r['gate'].get('step_name')}`:**")
            lines.extend(DIAG.format_diagnosis_markdown(gate_step_row["ai_diagnosis"], indent=""))

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
        lines += ["", "_✅ done · ⏸ in progress · 📅 scheduled · ⬜ not started_"]

    # 3) Current-phase detail (drill-down). Icon prefixed onto the Step name.
    # A third "Details" column captures each step's execution outcome (from its
    # stored note): where a lockdown clashed, the breaking change, CG alerts found,
    # the created wiki link, etc. Generic — any step that records a note shows it.
    if r.get("current_steps"):
        lines += ["", f"### ▶ Current phase — {r.get('current_phase_name','')}",
                  "| Step | State | Details |", "|---|---|---|"]
        for s in r["current_steps"]:
            icon = _STEP_ICON.get(s["state"], "⬜")
            word = (
                "Scout — approval needed"
                if s["state"] == "scout" and s.get("requires_approval")
                else _STEP_STATE_WORD.get(s["state"], s["state"])
            )
            tag = " 🚦" if s["gate"] or (s["state"] == "scout" and s.get("requires_approval")) else (" 📌" if s.get("reminder") else "")
            detail = step_detail(s)
            lines.append(f"| {icon} {s['name']}{tag} | {word} | {detail} |")

        # Phase-2 pipeline run ids (checker/orchestrator/MRWP) — recorded on state as
        # the verification steps resolve the chain, so they show without a live read.
        pl = _pipelines_line(r)
        if pl:
            lines += ["", f"**Pipelines:** {pl}"]

        # 3b) Expanded detail is only for unresolved work. Completed/skipped rows
        # already have their summary in the table and must not compete with the
        # actual actions below it, even when they retain links or multiline evidence.
        def _multiline(note):
            return len([ln for ln in str(note).splitlines() if ln.strip()]) > 1

        rich = [
            s for s in r["current_steps"]
            if s.get("state") not in ("done", "skipped")
            and ((s.get("note") and _multiline(s["note"])) or s.get("links"))
        ]
        if rich:
            lines += ["", "### Step details"]
            for s in rich:
                mark = _STEP_ICON.get(s["state"], "•")
                note_lines = [ln.rstrip() for ln in str(s.get("note") or "").strip().split("\n") if ln.strip()]
                if len(note_lines) > 12:
                    note_lines = note_lines[:12] + [f"… ({len(note_lines) - 12} more line(s) truncated)"]
                body = "  \n".join(note_lines)
                if len(body) > 900:
                    body = body[:897].rstrip() + "..."
                lines += ["", f"{mark} **{s['name']}**  ", body]
                for l in (s.get("links") or []):
                    if l.get("url"):
                        lines.append(f"🔗 [{l.get('name', 'link')}]({l['url']})  ")

    plan = r.get("work_dispatch") or {}
    if plan.get("pending_work") or plan.get("deferred_work"):
        lines.extend(["", work_followup(plan)])
    if not plan.get("continue_automatically"):
        lines.extend(_quick_commands_footer(r))
    return "\n".join(lines)


def _short_step_label(name: str | None, fallback: str = "<step>") -> str:
    if not name:
        return fallback
    cleaned = re.sub(r"\s*\([^)]*\)", "", str(name)).strip()
    cleaned = _cell(cleaned)
    if len(cleaned) > 42:
        cleaned = cleaned[:41].rstrip() + "…"
    return cleaned or fallback


def _example_step_name(r: dict, *, prefer_done: bool = False) -> str:
    if not prefer_done:
        focus = (r.get("action") or {}).get("step_name") or (r.get("gate") or {}).get("step_name")
        if focus:
            return _short_step_label(focus)
    steps = r.get("current_steps") or []
    if prefer_done:
        for s in steps:
            if s.get("state") == "done" and not s.get("gate"):
                return _short_step_label(s.get("name"))
    for s in steps:
        if s.get("state") not in ("done", "skipped") and not s.get("gate"):
            return _short_step_label(s.get("name"))
    if steps:
        return _short_step_label(steps[0].get("name"))
    return "<step>"


def _quick_commands_footer(r: dict) -> list[str]:
    """Compact, state-aware 2-line command hint appended to human-facing status views."""
    invariant_errors = any(
        issue.get("severity") == "error" for issue in (r.get("invariant_violations") or [])
    )
    has_uncertain_exec = bool((r.get("action") or {}).get("execution"))
    if (invariant_errors or has_uncertain_exec or r.get("halted")
            or r.get("status") == "cancelled"):
        return [
            "",
            "---",
            '💡 **Quick commands:** `"status"` (where am I) · `"task history"` · `"help"` (all commands)',
        ]
    if r.get("status") == "complete":
        return [
            "",
            "---",
            '💡 **Quick commands:** `"status"` · `"list releases"` · `"start a release"` · `"help"`',
        ]
    if r.get("scheduled"):
        opens = (r["scheduled"] or {}).get("opens") or "the window"
        return [
            "",
            "---",
            f'💡 **Quick commands:** `"status"` (where am I) · `"continue"` *(once {opens} opens)* '
            '· `"rerun <step>"` · `"help"`',
        ]
    phase = r.get("current_phase") or ""
    extra = ""
    if phase == "build_verify":
        extra = ' · `"rc report"`'
    elif phase == "bug_bash":
        extra = ' · `"bugbash progress"`'
    return [
        "",
        "---",
        f'💡 **Quick commands:** `"status"` (where am I) · `"continue"` (advance ready steps){extra} '
        '· `"explain <step>"` · `"rerun <step>"` · `"skip <step>"` · `"completed <step> manually"` · `"help"`',
    ]


def commands_card(r: dict | None = None) -> str:
    """Deterministic Markdown command guide, filled with live active-release examples when available."""
    if not r:
        return "\n".join([
            "### 🧭 Release Agent — Command Guide",
            "",
            "No active release is initialized yet. You can drive everything in plain English or via `/release-agent`:",
            "",
            "| Goal | What to type |",
            "|---|---|",
            '| **Start a release** | `"start a release"` (confirms clone folder + release date, then runs Readiness Gate) |',
            '| **Open launcher menu** | `/release-agent` (interactive menu for status, start, profile, or help) |',
            '| **Target profile** | `"environment"` or `"set profile to test\\|prod"` (inspect or switch `config/environment.yaml`) |',
            '| **List releases** | `"list releases"` (shows existing or past release runs on this machine) |',
            '| **Where am I?** | `"status"` (renders the full phase & step checklist once a release starts) |',
            '| **Advance** | `"continue"` (runs all eligible automatic steps until a human checkpoint) |',
        ])

    rid = r.get("release_id") or "<YYYY-MM>"
    phase_name = r.get("current_phase_name") or ("Entry Gate" if not r.get("readiness_signed") else "Complete")
    progress = f"{r.get('done', 0)}/{r.get('total', 0)} ({r.get('percent', 0)}%)"
    ex = _example_step_name(r)
    rerun_ex = _example_step_name(r, prefer_done=True)
    phase = r.get("current_phase") or ""
    inspect_extra = (
        '`"rc report"` (Phase 2 pipeline chain) or '
        if phase == "build_verify"
        else ('`"bugbash progress"` or ' if phase == "bug_bash" else "")
    )
    return "\n".join([
        f"### 🧭 Release Agent — Quick Guide (Release {rid} · {_cell(phase_name)} · {progress})",
        "",
        "| Goal | What to type |",
        "|---|---|",
        f'| **Where am I?** | `"status"` (full phase & step table) · {inspect_extra}`"/release-agent"` (interactive menu) |',
        '| **Continue / advance** | `"continue"` (runs all ready automatic steps across phases until a human checkpoint) |',
        f'| **Confirm or unblock** | `"{ex} is done"` · `"completed {ex} manually"` · `"skip {ex}"` |',
        f'| **Re-run (or new inputs)** | `"rerun {rerun_ex}"` · `"rerun Broker test plan with name=..."` · `"task history {rerun_ex}"` |',
        '| **Schedule / dates** | `"set CCD to YYYY-MM-DD"` (updates Code Complete Date and recalculates schedule) |',
        '| **Environment / targets** | `"environment"` (inspect active `test`/`prod` profile & targets) |',
        f'| **Ask why / how** | `"explain {ex}"` (shows runbook knowledge, prerequisites, and parameters) |',
        "",
        "_Tip: When a phase pauses at a checkpoint card, picking an option (**Completed**, **Approve**, **Skip**, **Retry**) automatically resumes the next eligible steps — no need to type `\"continue\"` afterward._",
    ])


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
        if s["state"] in ("pending", "gate", "auto"):
            return s["name"]
    return None
