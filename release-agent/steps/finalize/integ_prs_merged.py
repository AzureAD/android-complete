"""Step: `integ_prs_merged` — verify the 8 Freeze & Release-Integration PRs are merged (Phase 4).

Why this step exists:
  `publish-github-release-notes.yml` in the Release Orchestrator runs:
      git diff --quiet origin/working/release/<v> origin/release/<v>
  and hard-fails if the Freeze PRs (`working/release/<v> -> release/<v>`) opened by `integ_prs`
  have not been merged yet. Splitting PR creation (`integ_prs`) from PR merge verification
  (`integ_prs_merged`) ensures `publish_notes_gate` is never surfaced until the PRs are merged,
  while `RECHECK_WHEN_BLOCKED = True` lets `release-drainer` automatically unblock and advance
  as soon as reviewers merge the PRs.

Read-only (`gh pr` / `az repos pr`) -> `agent` step.
"""
from __future__ import annotations

from collections.abc import Mapping

from orchestrator.authority import OwnStepData
from orchestrator.evidence import StepData
from orchestrator.outcomes import Blocked, Done
from orchestrator.step_context import StepContext, thaw
from steps.finalize import integ_prs as IP
from steps.lib.mockctx import MISSING
from tools import prs as PR

ID = "integ_prs_merged"
KIND = "agent"
NAME = "Verify freeze & release-integration PRs merged"
REQUIRED_TOOLS = ("gh", "az")
EFFECT_MODE = "read_only"
RECHECK_WHEN_BLOCKED = True
EVIDENCE = (OwnStepData(),)

MOCKABLE = {
    "versions": {"kind": "input", "desc": "dict repo->version (override state.versions for testing)."},
    "branches": {"kind": "input", "desc": "dict repo->{wr,r,ri,target} to override branch names."},
    "repos": {"kind": "input", "desc": "list of repo keys to include (default all)."},
    "prs": {
        "kind": "input",
        "desc": (
            "dict '<repo>:<freeze|integration>' -> 'merged'|'open'|'closed'|'missing'|'error' "
            "or {state, number, url} to bypass live gh/az reads in tests."
        ),
    },
}


def _recorded_receipts(context: StepContext) -> dict[tuple[str, str, str], dict]:
    """Extract acknowledged PR receipts keyed by (repo_key, head, base) from `finalize.integ_prs`."""
    try:
        integ_step = context.evidence.step("finalize", "integ_prs")
        data = thaw(integ_step.data) if integ_step and integ_step.data else {}
    except Exception:  # noqa: BLE001
        data = {}
    ops = ((data.get("last_write_progress") or {}).get("operations") or {})
    if not isinstance(ops, Mapping):
        return {}
    receipts: dict[tuple[str, str, str], dict] = {}
    for op in ops.values():
        if not isinstance(op, Mapping):
            continue
        if op.get("kind") not in ("integration.create", "integration.reuse"):
            continue
        target = op.get("target") or {}
        receipt = op.get("receipt") or {}
        repo_key = str(target.get("repo") or "")
        head = str(target.get("head") or "")
        base = str(target.get("base") or "")
        if repo_key and head and base and isinstance(receipt, Mapping):
            receipts[(repo_key, head, base)] = {
                "number": receipt.get("id"),
                "url": receipt.get("url"),
            }
    return receipts


def _check_pr(context: StepContext, repo_key: str, pr_kind: str, cfg: dict, head: str, base: str, receipt: dict | None):
    """Return (ok, info|None, detail) where info is {number, url, state}."""
    inj = context.input("prs", MISSING)
    if isinstance(inj, Mapping):
        slot = f"{repo_key}:{pr_kind}"
        val = inj.get(slot, inj.get(repo_key, MISSING))
        if val is not MISSING:
            if isinstance(val, Mapping):
                state = str(val.get("state") or "open").lower()
                if state == "error":
                    return (False, None, str(val.get("detail") or "injected error"))
                if state == "missing":
                    return (True, None, "no PR found")
                num = val.get("number") or 100
                url = val.get("url") or f"https://example.invalid/{repo_key}/pull/{num}"
                return (True, {"number": num, "url": url, "state": state}, "")
            s = str(val).lower()
            if s == "error":
                return (False, None, "injected error")
            if s == "missing":
                return (True, None, "no PR found")
            num = (receipt or {}).get("number") or 100
            url = (receipt or {}).get("url") or f"https://example.invalid/{repo_key}/pull/{num}"
            return (True, {"number": num, "url": url, "state": s}, "")

    rec_num = (receipt or {}).get("number")
    if cfg["tool"] == "gh":
        fn = context.services.repositories.gh_pr_merge_state or PR.gh_pr_merge_state
        return fn(cfg["gh_repo"], head, base, number=rec_num)
    ado = cfg["ado"]
    fn = context.services.repositories.az_pr_merge_state or PR.az_pr_merge_state
    return fn(ado["org"], ado["project"], ado["repository"], head, base, number=rec_num)


def build(context: StepContext):
    repos = IP._selected_repos(context)
    versions = IP._versions(context)
    missing_ver = [k for k in repos if not versions.get(k)]
    if missing_ver:
        return Blocked(
            f"integ_prs_merged: missing release version(s) for {', '.join(missing_ver)} "
            "(expected in state.versions from Phase 2).")

    receipts = _recorded_receipts(context)
    merged_items: list[dict] = []
    unmerged_labels: list[str] = []
    unmerged_links: list[dict] = []
    all_links: list[dict] = []
    errors: list[str] = []

    for repo_key in repos:
        cfg = IP.CONFIG[repo_key]
        br = IP._branches(context, repo_key, cfg, versions[repo_key])
        pairs = (
            ("freeze", br["wr"], br["r"]),
            ("integration", br["ri"], br["target"]),
        )
        for pr_kind, head, base in pairs:
            receipt = receipts.get((repo_key, head, base))
            ok, info, detail = _check_pr(context, repo_key, pr_kind, cfg, head, base, receipt)
            label_prefix = f"{repo_key} {pr_kind}"
            if not ok:
                errors.append(f"{label_prefix} ({detail})")
                continue
            if not info:
                unmerged_labels.append(f"{label_prefix} ({head} -> {base}: no PR found)")
                continue
            state = str(info.get("state") or "open").lower()
            num = info.get("number")
            url = info.get("url") or (receipt or {}).get("url")
            pr_tag = f"#{num}" if num else f"{head}->{base}"
            entry = {
                "repo": repo_key,
                "kind": pr_kind,
                "number": num,
                "url": url,
                "state": state,
                "head": head,
                "base": base,
            }
            link_obj = {"name": f"{repo_key} {pr_kind} PR {pr_tag} ({state})", "url": url} if url else None
            if link_obj:
                all_links.append(link_obj)
            if state == "merged":
                merged_items.append(entry)
            else:
                unmerged_labels.append(f"{label_prefix} PR {pr_tag} ({state})")
                if link_obj:
                    unmerged_links.append(link_obj)

    total_expected = len(repos) * 2
    step_update = StepData({
        "checked_at": context.clock.iso(),
        "expected_count": total_expected,
        "merged_count": len(merged_items),
        "merged_prs": merged_items,
        "unmerged": unmerged_labels,
    })

    if errors:
        return Blocked(
            f"integ_prs_merged: could not verify PR merge status — {'; '.join(errors)}.",
            links=unmerged_links or all_links,
            updates=(step_update,),
        )

    if unmerged_labels:
        return Blocked(
            f"integ_prs_merged: {len(unmerged_labels)} of {total_expected} release PR(s) not yet "
            f"merged — {', '.join(unmerged_labels)}. Merge them before approving "
            "'Publish GitHub Release Notes' (release-drainer re-checks automatically).",
            links=unmerged_links or all_links,
            updates=(step_update,),
        )

    done_links = [
        {"name": f"{m['repo']} {m['kind']} PR #{m['number']}", "url": m["url"]}
        for m in merged_items if m.get("url")
    ]
    return Done(
        f"All {len(merged_items)}/{total_expected} freeze and release-integration PRs are merged "
        f"({', '.join(repos)}).",
        links=done_links,
        updates=(step_update,),
    )


KNOWLEDGE = {
    "summary": "Verify all 8 freeze and release-integration PRs are merged before publishing release notes.",
    "what": (
        "Checks via `gh pr` and `az repos pr` that both the Freeze PR (`working/release/<v> -> release/<v>`) "
        "and the Release-Integration PR (`release-integration/<v> -> dev|working`) are merged across all "
        "four release repositories (`common`, `msal`, `broker`, `authenticator`). The Release Orchestrator's "
        "`Publish GitHub Release Notes` stage runs `git diff --quiet origin/working/release/<v> origin/release/<v>` "
        "and fails if Freeze PRs are still open.\n"
    ),
    "who": (
        "Automated read-only verification (`agent` step). When PRs are still awaiting review/merge, "
        "the step blocks with direct links to the remaining unmerged PRs and `release-drainer` "
        "automatically re-evaluates it on subsequent ticks (`RECHECK_WHEN_BLOCKED = True`).\n"
    ),
    "how": (
        "Reads the acknowledged PR receipts recorded by `finalize.integ_prs` (falling back to branch-based "
        "`head -> base` lookup when `integ_prs` was completed manually) and checks merge status on GitHub.com, "
        "GitHub Enterprise (`msft.ghe.com`), and Azure DevOps (`msazure/One`). Once all 8 PRs are merged, "
        "transitions to `done` and unlocks `publish_notes_gate`.\n"
    ),
}
