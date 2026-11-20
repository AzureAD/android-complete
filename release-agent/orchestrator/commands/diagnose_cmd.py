# Copyright (c) Microsoft Corporation. All rights reserved.
"""CLI commands for deterministic failure evidence extraction (`diagnose`) and AI triage storage (`record-diagnosis`)."""
from __future__ import annotations

import json as _json

from orchestrator import cli_common as C
from orchestrator import diagnosis as DIAG


def cmd_diagnose(args) -> int:
    rid = C.resolve_release_id(args.runs_root, args.release)
    if not rid:
        print(_json.dumps({"error": "No active release found."}) if args.json else "No active release found.")
        return 1
    _, orch = C.load_orch(args.runs_root, rid, args.config)
    fetch_live = not bool(getattr(args, "no_live_logs", False))

    if args.phase and args.step:
        if orch.workflow.step(args.phase, args.step) is None:
            raise ValueError(f"Unknown workflow step: {args.phase}.{args.step}")
        ev = DIAG.collect_step_evidence(
            orch, args.runs_root, args.config, args.phase, args.step, fetch_live_logs=fetch_live
        )
        if args.json:
            print(_json.dumps(ev, indent=2))
            return 0
        brief = ev.get("existing_diagnosis") or ev.get("deterministic_brief") or {}
        print(f"Diagnosis for {rid} · {ev['step_name']} ({ev['step']}):")
        for ln in DIAG.format_diagnosis_markdown(brief, indent=""):
            print(ln)
        return 0

    targets = DIAG.pending_diagnosis_targets(orch, args.runs_root, args.config)
    items = [
        DIAG.collect_step_evidence(
            orch, args.runs_root, args.config, t["phase"], t["step_id"], fetch_live_logs=fetch_live
        )
        for t in targets
    ]
    payload = {"release": rid, "count": len(items), "items": items}
    if args.json:
        print(_json.dumps(payload, indent=2))
        return 0
    if not items:
        print(f"No pending failure diagnoses for release {rid}.")
        return 0
    for ev in items:
        brief = ev.get("existing_diagnosis") or ev.get("deterministic_brief") or {}
        print(f"• {ev['step_name']} ({ev['step']}):")
        for ln in DIAG.format_diagnosis_markdown(brief, indent="  "):
            print(ln)
    return 0


def cmd_record_diagnosis(args) -> int:
    rid = C.resolve_release_id(args.runs_root, args.release)
    if not rid:
        print(_json.dumps({"error": "No active release found."}))
        return 1
    links = []
    for raw in getattr(args, "link", None) or []:
        name, sep, url = str(raw).partition("=")
        if sep and name.strip() and url.strip():
            links.append({"name": name.strip(), "url": url.strip()})
    res = DIAG.record_step_diagnosis(
        args.runs_root,
        rid,
        args.config,
        phase_id=args.phase,
        step_id=args.step,
        signature=getattr(args, "signature", None),
        headline=args.headline,
        root_cause=args.root_cause,
        confidence=getattr(args, "confidence", "medium") or "medium",
        evidence_bullets=list(getattr(args, "evidence", None) or []),
        recommended_actions=list(getattr(args, "action", None) or []),
        skills_used=list(getattr(args, "skill", None) or []),
        links=links,
        source=getattr(args, "source", "ai") or "ai",
    )
    if getattr(args, "json", False):
        print(_json.dumps(res))
    else:
        print(f"Recorded {res['diagnosis']['source']} diagnosis for {rid} {args.phase}.{args.step}: {args.headline}")
    return 0


def register(subparsers) -> None:
    p_diag = subparsers.add_parser(
        "diagnose",
        help="Extract deterministic ADO task-log / test-cluster failure evidence for a blocked or failing step",
    )
    p_diag.add_argument("--release", default=None)
    p_diag.add_argument("--phase", default=None)
    p_diag.add_argument("--step", default=None)
    p_diag.add_argument("--no-live-logs", action="store_true", help="Skip live ADO timeline/log HTTP fetches")
    p_diag.add_argument("--json", action="store_true")
    p_diag.set_defaults(func=cmd_diagnose)

    p_rec = subparsers.add_parser(
        "record-diagnosis",
        help="Persist an AI/skill root-cause diagnosis for a blocked step or RC test-failure gate",
    )
    p_rec.add_argument("--release", required=True)
    p_rec.add_argument("--phase", required=True)
    p_rec.add_argument("--step", required=True)
    p_rec.add_argument("--signature", default=None, help="Failure signature from `diagnose`")
    p_rec.add_argument("--headline", required=True, help="One-line summary of what failed")
    p_rec.add_argument("--root-cause", required=True, help="Evidence-backed explanation of why it failed")
    p_rec.add_argument("--confidence", choices=["high", "medium", "low"], default="medium")
    p_rec.add_argument("--evidence", action="append", default=[], help="Supporting log line or test cluster fact (repeatable)")
    p_rec.add_argument("--action", action="append", default=[], help="Concrete investigation or fix action (repeatable)")
    p_rec.add_argument("--skill", action="append", default=[], help="Workspace skill consulted (e.g. release-helper)")
    p_rec.add_argument("--link", action="append", default=[], help="Optional label=url deep link (repeatable)")
    p_rec.add_argument("--source", choices=["ai", "deterministic"], default="ai")
    p_rec.add_argument("--json", action="store_true")
    p_rec.set_defaults(func=cmd_record_diagnosis)
