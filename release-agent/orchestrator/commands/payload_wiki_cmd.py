"""`create-payload-wiki` — create/update the monthly release PAYLOAD wiki subpage (Phase-4).

Preview-first, mirroring `create-oneauth-common-pr`: with `--dry-run` (the default the step's
follow-up names) it re-composes the page from live data and PRINTS the full markdown, writing
NOTHING. With `--execute` it create-or-updates the page (ETag-guarded update if it already
exists, else create) and records the `finalize.wiki_payload` step (pass, or attention on a
write failure).

Honors the release's `finalize.wiki_payload` mocks (version / prs / page_name) for offline tests.
"""
from __future__ import annotations

import json

from orchestrator import cli_common as C, write_review as W
from steps.finalize import wiki_payload as S
from tools import checks


def _step_mocks(orch):
    return (getattr(orch, "mocks", {}) or {}).get("finalize.wiki_payload", {}) or {}


def _record(orch, args, status, summary, url=None):
    orch.record_scout_step(
        "finalize", "wiki_payload", status, summary,
        execution_id=args.execution_id)
    if url:
        orch.annotate_step(
            "finalize", "wiki_payload",
            links=[{"name": "Release payload page", "url": url}],
            by="scout")
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[wiki_payload] {summary}", kind="step", log_text=summary)


def plan_payload_wiki(orch):
    ok, plan, detail = S.compose_payload(
        orch.context("finalize", S.ID, inputs=_step_mocks(orch)))
    if not ok:
        raise ValueError(f"Could not compose the payload page: {detail}")
    org, project, wiki = S.CONFIG["org"], S.CONFIG["project"], S.CONFIG["wiki"]
    path = plan["page_path"]
    exists = checks.wiki_page_exists(org, project, wiki, path)
    if type(exists) is not bool:
        raise ValueError("Payload page existence is unknown; no write can be reviewed.")
    before = {"exists": exists}
    if exists:
        ok, content, etag, detail = checks.get_wiki_page(org, project, wiki, path)
        if (not ok or not isinstance(content, str) or not isinstance(etag, str)
                or not etag.strip().strip('"') or etag.strip().strip('"') == "*"):
            raise ValueError(f"Payload update needs readable content and a nonempty exact ETag: {detail}")
        before.update(content=content, etag=etag)
    return W.WritePlan(
        S.WRITE_COMMAND, {k: v for k, v in plan.items() if k != "content"},
        (W.WriteOperation(
            "update_wiki_page" if exists else "create_wiki_page",
            {"org": org, "project": project, "wiki": wiki, "path": path},
            {"content": plan["content"]}, before),))


def cmd_create_payload_wiki(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    try:
        if args.dry_run and (args.execute or args.reserve):
            raise ValueError("--dry-run cannot be combined with --execute/--reserve.")
        if not (args.execute or args.reserve):
            print(json.dumps(W.preview(orch, "finalize", S.ID, plan_payload_wiki(orch)), indent=2))
            return 0
        authorization = W.authorize(args, orch, "finalize", S.ID, lambda: plan_payload_wiki(orch))
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "permission_to_execute": False}))
        return 1
    if authorization.reserved_only:
        W.print_reservation(authorization)
        return 0
    operation = authorization.plan.operations[0]
    target = operation.target
    args.execution_id = authorization.execution_id
    try:
        authorization.validate()
        if operation.kind == "update_wiki_page":
            result = checks.update_wiki_page(
                **target, content=operation.content["content"], etag=operation.preconditions["etag"])
        else:
            result = checks.create_wiki_page(
                **target, content=operation.content["content"], require_absent=True)
        if not result.ok:
            raise ValueError(result.detail)
        ok, content, _, detail = checks.get_wiki_page(**target)
        if not ok or content != operation.content["content"]:
            raise ValueError(f"Payload readback differs or is unavailable: {detail}")
        authorization.validate()
    except Exception as exc:
        _record(orch, args, "attention",
                f"wiki_payload: write result uncertain — {exc}; inspect the page before owner resolution.")
        print(json.dumps({"error": str(exc)}))
        return 2
    info = authorization.plan.parameters
    summary = f"wiki_payload: verified '{info['page_name']}' — App Version {info['version']}."
    _record(orch, args, "pass", summary, url=info["url"])
    print(summary)
    return 0


def register(sub):
    p = sub.add_parser(
        "create-payload-wiki",
        help="Create/update the monthly release payload wiki page (preview by default; --execute to write)")
    p.add_argument("--release", required=True)
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.add_argument("--dry-run", action="store_true", help="Preview only (default behavior)")
    p.add_argument("--execute", action="store_true",
                   help="Perform the create-or-update write. Default is dry-run.")
    p.add_argument("--execution-id", help="Active reserve-step execution id")
    W.add_arguments(p)
    p.set_defaults(func=cmd_create_payload_wiki)
