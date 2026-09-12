"""`create-payload-wiki` — create/update the monthly release PAYLOAD wiki subpage (Phase-4).

Preview-first, mirroring `create-oneauth-common-pr`: with `--dry-run` (the default the step's
follow-up names) it re-composes the page from live data and PRINTS the full markdown, writing
NOTHING. With `--execute` it create-or-updates the page (ETag-guarded update if it already
exists, else create) and records the `finalize.wiki_payload` step (pass, or attention on a
write failure).

Honors the release's `finalize.wiki_payload` mocks (version / prs / page_name) for offline tests.
"""
from __future__ import annotations

from orchestrator import cli_common as C
from steps.lib import mockctx
from steps.finalize import wiki_payload as S
from tools import checks


def _step_mocks(orch):
    return (getattr(orch, "mocks", {}) or {}).get("finalize.wiki_payload", {}) or {}


def _record(orch, args, status, summary, url=None):
    orch.record_scout_step("finalize", "wiki_payload", status, summary)
    if url:
        step = orch.state.get_step("finalize", "wiki_payload")
        step.links = [{"name": "Release payload page", "url": url}]
        step.by = "scout"
        orch.state.set_step("finalize", "wiki_payload", step)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[wiki_payload] {summary}", kind="step", log_text=summary)


def cmd_create_payload_wiki(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    with mockctx.active(_step_mocks(orch)):
        ok, plan, detail = S.compose_payload(st)
    if not ok:
        print(f"Could not compose the payload page: {detail}")
        return 1

    org, project, wiki = S.CONFIG["org"], S.CONFIG["project"], S.CONFIG["wiki"]
    path = plan["page_path"]
    exists = checks.wiki_page_exists(org, project, wiki, path)
    action = "update" if exists else "create"     # None (unknown) -> create (create is exist-safe)

    print(f"Release payload page — {plan['page_name']}")
    print(f"  wiki:    {wiki}  ({org}/{project})")
    print(f"  path:    {path}")
    print(f"  action:  {action.upper()}")
    print(f"  version: {plan['version']}   ·   merged PRs: {plan['pr_count']}")
    print(f"  link:    {plan['url']}")

    if not args.execute:
        print("\n----- PAGE CONTENT (preview) -----")
        print(plan["content"])
        print("----- end preview -----")
        print("\n(dry-run — nothing written. Re-run with --execute to create/update the page.)")
        return 0

    if exists:
        okg, _content, etag, dg = checks.get_wiki_page(org, project, wiki, path)
        if not okg:
            _record(orch, args, "attention", f"wiki_payload: couldn't read the page to update ({dg})")
            print(f"\nRead-for-update failed: {dg}")
            return 2
        res = checks.update_wiki_page(org, project, wiki, path, plan["content"], etag)
    else:
        res = checks.create_wiki_page(org, project, wiki, path, plan["content"])

    if not res.ok:
        _record(orch, args, "attention", f"wiki_payload: {action} failed — {res.detail}")
        print(f"\n{action.capitalize()} failed: {res.detail}")
        return 2

    summary = (f"wiki_payload: {action}d '{plan['page_name']}' — App Version {plan['version']}, "
               f"{plan['pr_count']} merged PR(s). Link: {plan['url']}")
    _record(orch, args, "pass", summary, url=plan["url"])
    print(f"\n{action.capitalize()}d payload page. {plan['url']}")
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
    p.set_defaults(func=cmd_create_payload_wiki)
