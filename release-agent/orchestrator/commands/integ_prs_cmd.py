"""`create-integration-prs` — open the Phase-4 release integration/freeze PRs.

Preview-first, mirroring `distribute-tests`: with no flag it prints the full plan and
writes NOTHING. With `--execute` it performs the writes:

  1. create ONE shared PBI (unless --pbi <id> is given or the plan says skip),
  2. for each repo/PR head->base:
       - reuse an existing open PR (just ensure labels) — never duplicate,
       - for INTEGRATION PRs, first bring the RI branch up to date + revert build.gradle
         via tools.prs.prepare_ri_branch (isolated worktree; HELD if a human conflict
         remains — the PR is NOT opened),
       - otherwise open the PR (gh for github/GHE, az for the ADO authenticator repo)
         with labels + the AB#<pbi> body,
  3. record the `finalize.integ_prs` step (pass if everything opened/reused, else attention).

Honors the release's `finalize.integ_prs` mocks (versions / branches / repos / pbi) so it
can be exercised against fake branches without touching production.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from steps.lib import mockctx
from steps.finalize import integ_prs as S
from tools import prs as PR


def _step_mocks(orch):
    return (getattr(orch, "mocks", {}) or {}).get("finalize.integ_prs", {}) or {}


def _resolve_pbi(plan, args):
    """(pbi_id, note) — reuse --pbi / plan id, create one, or skip."""
    if args.pbi:
        return (args.pbi, f"using PBI AB#{args.pbi}")
    if plan["pbi_mode"] == "existing":
        return (plan["pbi_id"], f"using PBI AB#{plan['pbi_id']}")
    if plan["pbi_mode"] == "skip":
        return (None, "no PBI (skipped)")
    title = args.pbi_title or f"Android {args.release} release — integration PRs"
    ok, wid, url, detail = PR.create_pbi(S.PL.ENGINEERING_ORG, S.PL.ENGINEERING_PROJECT, title)
    if not ok:
        return (None, f"PBI creation FAILED ({detail}) — opening PRs without an AB# link")
    return (str(wid), f"created PBI AB#{wid} ({url})")


def _open_one(repo, pr, pbi_id, results):
    """Execute a single PR (RI edit if needed → create/reuse → labels). Appends to results."""
    key = repo["key"]
    cfg = S.CONFIG[key]
    head, base, kind = pr["head"], pr["base"], pr["kind"]
    tag = f"{key} {kind} {head}->{base}"

    if pr.get("existing"):
        num = pr["existing"]["number"]
        if cfg["tool"] == "gh" and cfg.get("labels"):
            PR.gh_ensure_labels(cfg["gh_repo"], num, cfg["labels"])
        results.append(("reuse", tag, pr["existing"].get("url") or f"#{num}"))
        return
    if pr.get("head_exists") is False or pr.get("base_exists") is False:
        results.append(("skip", tag, "head/base branch missing"))
        return

    if kind == "integration":
        ok, ri, detail = PR.prepare_ri_branch(cfg["dir"], head, base, dry_run=False)
        if not ok:
            results.append(("fail", tag, f"RI edit failed: {detail}"))
            return
        if ri.get("human_conflicts"):
            results.append(("held", tag,
                            f"{len(ri['human_conflicts'])} human conflict(s) — resolve then rerun"))
            return

    body = S.pr_body(key, kind, repo["branches"], pbi_id)
    if cfg["tool"] == "gh":
        ok, url, detail = PR.gh_create_pr(cfg["gh_repo"], head, base, pr["title"], body,
                                          labels=cfg.get("labels", []))
    else:
        a = cfg["ado"]
        ok, url, detail = PR.az_create_pr(a["org"], a["project"], a["repository"],
                                          head, base, pr["title"], body, work_items=pbi_id)
    results.append((("created" if ok else "fail"), tag, url or detail))


def cmd_create_integration_prs(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    with mockctx.active(_step_mocks(orch)):
        plan = S.plan(st)

    preview = S.render_preview(plan)
    print(preview)

    if not plan["ready"]:
        print(f"\nNOT READY — missing: {', '.join(plan['missing'])}. Nothing opened.")
        return 1
    if not args.execute:
        print("\n(dry-run -- no PBI created, no branches touched, no PRs opened. "
              "Re-run with --execute to write.)")
        return 0

    pbi_id, pbi_note = _resolve_pbi(plan, args)
    print(f"\nPBI: {pbi_note}")

    results = []
    only = set(args.repos or [])
    for repo in plan["repos"]:
        if only and repo["key"] not in only:
            continue
        for pr in repo.get("prs", []):
            _open_one(repo, pr, pbi_id, results)

    print("\nResults:")
    for status, tag, info in results:
        print(f"  [{status:<7}] {tag}  {info}")

    created = [r for r in results if r[0] == "created"]
    problems = [r for r in results if r[0] in ("fail", "held", "skip")]
    status = "pass" if not problems else "attention"
    summary = (f"integ_prs: {len(created)} PR(s) opened, "
               f"{len([r for r in results if r[0]=='reuse'])} reused"
               + (f", {len(problems)} need attention" if problems else "")
               + f". {pbi_note}.")
    orch.record_scout_step("finalize", "integ_prs", status, summary)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[integ_prs] {summary}", kind="step",
           log_text=summary)
    return 0 if not problems else 2


def register(sub):
    p = sub.add_parser(
        "create-integration-prs",
        help="Open the Phase-4 release integration/freeze PRs (preview by default; --execute to write)")
    p.add_argument("--release", required=True)
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.add_argument("--execute", action="store_true",
                   help="Perform the writes (create PBI, edit RI, open PRs). Default is dry-run.")
    p.add_argument("--repos", nargs="*", default=None,
                   help="Limit to these repo keys (common msal broker authenticator)")
    p.add_argument("--pbi", default=None, help="Reuse this PBI id instead of creating one")
    p.add_argument("--pbi-title", default=None, help="Title for the created PBI")
    p.set_defaults(func=cmd_create_integration_prs)
