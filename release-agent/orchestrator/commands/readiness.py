"""Readiness entry-gate commands: checklist, verify, sign, decline."""
from __future__ import annotations
import json as _json

from orchestrator import render
from orchestrator import cli_common as C


def cmd_checklist(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    if getattr(args, "verify", False):
        orch.gate.verify()
        C.save_state(st, args.runs_root, args.release)
    chk = orch.gate.checklist()
    from orchestrator.readiness import dispatch_plan
    from orchestrator.revision import mismatch_reason
    revision_problem = mismatch_reason(orch)
    if revision_problem:
        chk["dispatch"] = dispatch_plan(chk, args.release, suspension=revision_problem)
    runtime_report = orch.status_report()
    chk = runtime_report.get("readiness", chk)
    attest = getattr(args, "attest_prompt", False)
    code = 2 if getattr(args, "handoff", False) and not chk["dispatch"]["may_return_to_user"] else 0
    if getattr(args, "json", False):
        # Keep the full checklist and deterministic card on the same snapshot.
        # Tool output alone is not a user-visible table: the skill must paste it.
        from orchestrator.commands.automation import drainer_setup_hint
        hint = drainer_setup_hint(args.runs_root, args.release, args.config, st)
        if attest:
            payload = render.attest_prompt_payload(chk, args.release)
            payload["progression_policy"] = runtime_report["progression_policy"]
            payload["display_before_prompt"] = runtime_report["handoff_ready"]
            if hint is not None:
                payload["drainer_setup"] = hint
            print(_json.dumps(payload))
        else:
            if hint is not None:
                chk = {**chk, "drainer_setup": hint}
            print(_json.dumps(chk))
        return code
    if attest:
        C.emit(args.runs_root, args.release,
               render.readiness_table(chk, args.release) + "\n\n" + render.attest_prompt(chk),
               kind="readiness_attest_prompt")
        return code
    # canonical, consistent display block (the template) — same for every engineer.
    # This records emitted output, not proof that Scout pasted it into the conversation.
    C.emit(args.runs_root, args.release, render.readiness_table(chk, args.release), kind="readiness_checklist")
    return code


def cmd_verify(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    chk = orch.gate.verify(pending_only=getattr(args, "pending_only", False))
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "readiness_verified",
        results=[{"id": it["id"], "status": it["status"]} for it in chk["auto_items"]])
    for it in chk["auto_items"]:
        status = ("pending" if not it["satisfied"] and it["status"] in ("pass", "degraded")
                  else it["status"])
        mark = {"pass": "OK", "degraded": "WARN", "pending": "PENDING",
                "needs_input": "INPUT"}.get(status, "FAIL")
        print(f"  [{mark}] {it['id']}: {status} — {it.get('message','')}")
    print(render.readiness_followup(chk, args.release))
    return 0


def cmd_sign(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    ids = args.item or []
    if not ids:
        # No blanket sign: the engineer must name the item(s) they confirmed.
        # This closes the integrity hole where `sign --all` attested every human
        # item in one blind call with no evidence and no per-item confirmation.
        print("Refusing to sign: name the item(s) you confirmed with --item "
              "(repeatable). Attest only what the engineer explicitly confirmed.")
        return 2
    # Validate the ids are real attest items before recording anything.
    chk_before = orch.gate.checklist()
    attest_ids = {i["id"] for i in chk_before["attest_items"]}
    unknown = [i for i in ids if i not in attest_ids]
    if unknown:
        print(f"Not attestable (unknown or not an attest item): {', '.join(unknown)}")
        return 2
    st.touch_owner_interaction()
    chk = orch.gate.sign(ids, note=args.note or None)
    C.save_state(st, args.runs_root, args.release)
    el = C.elog(args.runs_root, args.release)
    # Log EACH attestation individually with its evidence note, so the trail shows
    # exactly what was confirmed (not one opaque items:"all").
    for iid in ids:
        el.log("readiness_attested", item=iid, driver=args.note or None)
    el.log("readiness_signed" if chk["signed"] else "readiness_partial",
           items=ids, signed=chk["signed"])
    if chk["signed"]:
        print(f"Readiness signed at {chk['signed_at']}. Entry gate cleared — you can now start Phase 0.")
        from orchestrator.commands.automation import drainer_setup_hint
        hint = drainer_setup_hint(args.runs_root, args.release, args.config, st)
        if hint is not None:
            print(
                f"  Optional 7 AM–7 PM release drainer setup: run `{hint['plan_command']}`, create "
                f"`{hint['name']}` ({hint['schedule']}) via `m_create_automation` (or reuse if present), "
                f"and record with `{hint['record_command']}` (or `{hint['skip_command']}` if unavailable), "
                "then proceed immediately to `next --deliver`."
            )
    else:
        pending = [i["id"] for i in chk["items"] if not i["satisfied"]]
        print(f"Recorded {', '.join(ids)}. Still pending: {', '.join(pending)}")
        print(render.readiness_followup(chk, args.release))
    return 0


def cmd_decline(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    chk = orch.gate.decline(args.item or [])
    if "error" in chk:
        print(chk["error"])
        return 2
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "readiness_declined", items=args.item or [], blocked=chk["blocked"], driver=args.reason or None)
    if chk["blocked"]:
        labels = [next((i["label"] for i in chk["items"] if i["id"] == b), b)
                  for b in chk["blocked_items"]]
        msg = ("⛔ BLOCKED — cannot start: " + ", ".join(labels) + ".\n"
               "   " + chk.get("blocked_message", "").strip())
    else:
        msg = f"Recorded as unable: {', '.join(args.item or [])}"
    C.emit(args.runs_root, args.release,
           msg + "\n" + render.readiness_followup(chk, args.release), kind="decline_result")
    return 0


def cmd_record_check(args):
    """Record the result of a scout-assisted auto readiness check (source: scout),
    e.g. the ICM on-call lookup. The skill runs the check via its MCP tools and
    calls this to store the pass/fail result in the engine."""
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    evidence = None
    if getattr(args, "evidence_file", None):
        try:
            with open(args.evidence_file, encoding="utf-8-sig") as stream:
                evidence = _json.load(stream)
        except (OSError, ValueError) as exc:
            print(f"Cannot read readiness evidence: {exc}")
            return 1
    res = orch.gate.record_check(args.item, args.status, args.detail or "", evidence=evidence)
    if "error" in res:
        print(res["error"])
        return 1
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "readiness_check_recorded", item=args.item, status=args.status, driver=args.detail or None)
    item = next((i for i in res["items"] if i["id"] == args.item), None)
    mark = {"pass": "OK", "degraded": "WARN", "needs_input": "INPUT"}.get(args.status, "FAIL")
    tail = " — entry gate cleared." if res.get("signed") else ""
    C.emit(args.runs_root, args.release,
           f"[{mark}] {(item or {}).get('label', args.item)}: {args.status}"
           f"{(' — ' + args.detail) if args.detail else ''}{tail}\n"
           + render.readiness_followup(res, args.release), kind="record_check")
    return 0


def cmd_override_oncall(args):
    """Record an audited continuation-only override when the release owner rotates
    onto primary on-call after a release has already started."""
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    res = orch.gate.override_oncall(args.confirmed_by or st.owner_email or "", args.reason or "")
    if "error" in res:
        print(res["error"])
        return 1
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "readiness_oncall_overridden", item="oncall_now", reason=args.reason)
    C.emit(args.runs_root, args.release,
           "Recorded continuation-only on-call override; readiness entry gate remains satisfied.\n"
           + render.readiness_followup(res, args.release), kind="oncall_override")
    return 0


def cmd_resolve_readiness_manual(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    mode = "waived" if args._mode == "waived" else "manual"
    reason = getattr(args, "reason", None) or getattr(args, "note", None) or ""
    res = orch.gate.resolve_failed_item(
        args.item, mode=mode,
        confirmed_by=getattr(args, "confirmed_by", None) or st.owner_email or "",
        reason=reason,
    )
    if "error" in res:
        print(res["error"])
        return 1
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "readiness_owner_resolved", item=args.item, mode=mode, reason=reason)
    C.emit(args.runs_root, args.release,
           f"[{mode.upper()}] {args.item}: {reason}\n"
           + render.readiness_followup(res, args.release), kind="readiness_owner_resolved")
    return 0


def cmd_m365_capabilities(args):
    from orchestrator.m365_capabilities import plan
    st, _ = C.load_orch(args.runs_root, args.release, args.config)
    if not st.owner_email:
        print(_json.dumps({"error": "Set the confirmed release owner before probing connector capabilities"}))
        return 1
    try:
        evidence = None
        if args.evidence_file:
            with open(args.evidence_file, encoding="utf-8-sig") as stream:
                evidence = _json.load(stream)
        print(_json.dumps(plan(st, evidence=evidence), indent=2))
    except (ValueError, OSError) as exc:
        print(_json.dumps({"error": str(exc), "permission_to_send": False}))
        return 1
    return 0


def register(sub):
    c = sub.add_parser("checklist", help="Show the readiness entry-gate checklist")
    c.add_argument("--release", required=True)
    c.add_argument("--verify", action="store_true", help="Run Python verifiers only; Scout probes remain host-executed")
    c.add_argument("--attest-prompt", dest="attest_prompt", action="store_true",
                   help="Emit the full checklist and readiness handoff/question from the same snapshot")
    c.add_argument("--json", action="store_true")
    c.add_argument("--handoff", action="store_true",
                   help="Refuse a final handoff (exit 2) while automatic probes remain executable")
    c.set_defaults(func=cmd_checklist)

    v = sub.add_parser("verify", help="Run Python readiness verifiers and report the remaining Scout queue")
    v.add_argument("--release", required=True)
    v.add_argument("--pending-only", action="store_true",
                   help="Dispatch only unperformed/invalidated checks; do not retry recorded failures")
    v.set_defaults(func=cmd_verify)

    rc = sub.add_parser("record-check",
                        help="Record a scout-assisted auto check result (e.g. ICM on-call)")
    rc.add_argument("--release", required=True)
    rc.add_argument("--item", required=True, help="Readiness item id (must be a source:scout auto item)")
    rc.add_argument("--status", required=True, choices=["pass", "fail", "degraded", "needs_input"],
                    help="Actual result, consent/input hold, or explicit opt-out (degraded)")
    rc.add_argument("--detail", default="", help="Short evidence/summary (e.g. 'not in roster')")
    rc.add_argument("--evidence-file", help="Structured fresh M365 capability inventory/probe results")
    rc.set_defaults(func=cmd_record_check)

    cp = sub.add_parser("m365-capabilities", help="Plan actual read-only M365 capability probes before release entry")
    cp.add_argument("--release", required=True)
    cp.add_argument("--evidence-file", help="Partial observations to request exact next group/member/library reads")
    cp.set_defaults(func=cmd_m365_capabilities)

    sg = sub.add_parser("sign", help="Attest human readiness items (also runs auto verify)")
    sg.add_argument("--release", required=True)
    sg.add_argument("--item", action="append",
                    help="Attest a specific item id the engineer confirmed (repeatable, required)")
    sg.add_argument("--note", default="",
                    help="Evidence: what the engineer confirmed (recorded per item)")
    sg.set_defaults(func=cmd_sign)

    dc = sub.add_parser("decline", help="Declare you CANNOT satisfy an item (may block ownership)")
    dc.add_argument("--release", required=True)
    dc.add_argument("--item", action="append", required=True, help="Item id you cannot satisfy (repeatable)")
    dc.add_argument("--reason", default="", help="Why (recorded in the event log)")
    dc.set_defaults(func=cmd_decline)

    oo = sub.add_parser("override-oncall",
                        help="Audited continuation-only override if owner becomes primary on-call mid-release")
    oo.add_argument("--release", required=True)
    oo.add_argument("--reason", required=True, help="Explicit owner reason for continuing")
    oo.add_argument("--confirmed-by", default=None, help="Release owner email (defaults to state owner)")
    oo.set_defaults(func=cmd_override_oncall)

    sr = sub.add_parser("skip-readiness",
                        help="Waive a readiness item manually with an audited owner reason")
    sr.add_argument("--release", required=True)
    sr.add_argument("--item", required=True)
    sr.add_argument("--confirmed-by", default="")
    sr.add_argument("--reason", required=True)
    sr.set_defaults(func=cmd_resolve_readiness_manual, _mode="waived")

    cmr = sub.add_parser("complete-readiness-manually",
                         help="Record owner-attested manual completion of a readiness item")
    cmr.add_argument("--release", required=True)
    cmr.add_argument("--item", required=True)
    cmr.add_argument("--confirmed-by", default="")
    cmr.add_argument("--note", required=True)
    cmr.set_defaults(func=cmd_resolve_readiness_manual, _mode="manual")
