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
    if getattr(args, "json", False):
        print(_json.dumps(chk, indent=2))
        return 0
    if getattr(args, "attest_prompt", False):
        # ONLY the '✋ Your confirmation needed' block (no table). Used as the second
        # readiness render, after the full table + silent auto checks — so the table
        # shows exactly once but the attestation ask still comes from the engine.
        C.emit(args.runs_root, args.release, render.attest_prompt(chk), kind="readiness_attest_prompt")
        return 0
    # canonical, consistent display block (the template) — same for every engineer.
    # auto-logged as scout output so the log always records what was shown.
    C.emit(args.runs_root, args.release, render.readiness_table(chk, args.release), kind="readiness_checklist")
    return 0


def cmd_verify(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    chk = orch.gate.verify()
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "readiness_verified",
        results=[{"id": it["id"], "status": it["status"]} for it in chk["auto_items"]])
    for it in chk["auto_items"]:
        mark = "OK" if it["status"] == "pass" else "FAIL"
        print(f"  [{mark}] {it['id']}: {it['status']} — {it.get('message','')}")
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
    else:
        pending = [i["id"] for i in chk["items"] if not i["satisfied"]]
        print(f"Recorded {', '.join(ids)}. Still pending: {', '.join(pending)}")
    return 0


def cmd_decline(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    chk = orch.gate.decline(args.item or [])
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
    C.emit(args.runs_root, args.release, msg, kind="decline_result")
    return 0


def cmd_record_check(args):
    """Record the result of a scout-assisted auto readiness check (source: scout),
    e.g. the ICM on-call lookup. The skill runs the check via its MCP tools and
    calls this to store the pass/fail result in the engine."""
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    res = orch.gate.record_check(args.item, args.status, args.detail or "")
    if "error" in res:
        print(res["error"])
        return 1
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "readiness_check_recorded", item=args.item, status=args.status, driver=args.detail or None)
    item = next((i for i in res["items"] if i["id"] == args.item), None)
    mark = {"pass": "OK", "degraded": "WARN"}.get(args.status, "FAIL")
    tail = " — entry gate cleared." if res.get("signed") else ""
    C.emit(args.runs_root, args.release,
           f"[{mark}] {(item or {}).get('label', args.item)}: {args.status}"
           f"{(' — ' + args.detail) if args.detail else ''}{tail}", kind="record_check")
    return 0


def register(sub):
    c = sub.add_parser("checklist", help="Show the readiness entry-gate checklist")
    c.add_argument("--release", required=True)
    c.add_argument("--verify", action="store_true", help="Run auto verifiers before showing")
    c.add_argument("--attest-prompt", dest="attest_prompt", action="store_true",
                   help="Emit ONLY the '✋ Your confirmation needed' block (no table) — the second readiness render")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_checklist)

    v = sub.add_parser("verify", help="Run the auto readiness verifiers")
    v.add_argument("--release", required=True)
    v.set_defaults(func=cmd_verify)

    rc = sub.add_parser("record-check",
                        help="Record a scout-assisted auto check result (e.g. ICM on-call)")
    rc.add_argument("--release", required=True)
    rc.add_argument("--item", required=True, help="Readiness item id (must be a source:scout auto item)")
    rc.add_argument("--status", required=True, choices=["pass", "fail", "degraded"],
                    help="pass | fail (or 'degraded' for opt-out items the user proceeds without)")
    rc.add_argument("--detail", default="", help="Short evidence/summary (e.g. 'not in roster')")
    rc.set_defaults(func=cmd_record_check)

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
