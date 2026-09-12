"""Step: `wiki_payload` — create/update the monthly release PAYLOAD wiki subpage (Phase 4,
finalize, after `tag_authenticator`; checklist Phase 2.2 Step 6, relocated to the end of the
release once every version/commit/tag is final).

This REPLACES the old Phase-0 `wiki` step (which only seeded an empty placeholder page). By the
time we reach here the Authenticator build + all SDK versions are final, so we fill the page in
one shot rather than creating an empty note early and updating it later.

The page mirrors the real payload pages under IdentityWiki / "Monthly Releases Payloads History":
  #App Version            → the bug-bash/Authenticator version + its release-app build run link
                            (the NEW AndroidBuild-1ES pipeline — find_auth_release_build)
  Authenticator + DID     → the merged-PR list for this release (auto-derived, tools.merged_release_prs)
  Auth Client Android SDKs→ Broker / Common / MSAL versions (state.versions)
  Email / Sign-offs /     → clearly-marked placeholders the owner fills (hand-curated, not derivable)
  Feature-flag rollouts

PREVIEW-FIRST (like oneauth_common_pr): `build()` only READS — it resolves the version + build
link, derives the PR list, and renders the markdown — then returns a NeedsSkill so the skill
PREVIEWS the composed page before the `create-payload-wiki` command does the create-or-update
write (`--dry-run` / `--execute`).

Mock knobs (mocks.local.yaml / tests):
  version   : inject the Authenticator version + build link (skip the ADO build lookup).
  prs       : inject the merged-PR list [{id,title}] (skip the live PR derivation).
  page_name : write to this exact page name instead of '<Month> <Year> Release' (a REAL page).
  fail      : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools.coordinates import coords

ID = "wiki_payload"
KIND = "agent"

# The payload page lives in a DIFFERENT project (IdentityWiki) from the release chain — only the
# org host is shared. Same parent as the standing payloads-history tree.
CONFIG = {
    "org": coords.org_url("identity_wiki"),
    "project": coords.project("identity_wiki"),
    "wiki": "IdentityWiki.wiki",
    "parent_path": "/IdentityWiki/Services/Microsoft Authenticator/Release/Android/Monthly Releases Payloads History",
    "sdks": [("broker", "Broker"), ("common", "Common"), ("msal", "Msal")],
}

# Pure localization auto-checkins — noise the real payload pages curate out of the PR list.
_NOISE_PREFIXES = ("LEGO: check in to working",)

MOCKABLE = {
    "version": {"kind": "input",
                "desc": "Inject {version, build_number, build_url} (skip the ADO build lookup)."},
    "prs": {"kind": "input",
            "desc": "Inject the merged-PR list [{id,title}] (skip the live PR derivation)."},
    "page_name": {"kind": "input",
                  "desc": "Write to this exact page name instead of '<Month> <Year> Release'."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _month_year(state) -> str:
    from orchestrator import schedule
    return schedule.target_month_label(state) or str(state.release_id)


def page_name(state) -> str:
    """Payload page name: '<Ship Month> <Year> Release' (the release's display month, e.g.
    'September 2026 Release')."""
    ov = mock_input("page_name", MISSING)
    if ov is not MISSING and ov:
        return str(ov)
    from orchestrator import schedule
    label = schedule.target_month_label(state)
    return f"{label} Release" if label else f"{state.release_id} Release"


def page_path(state) -> str:
    return f"{CONFIG['parent_path'].rstrip('/')}/{page_name(state)}"


def wiki_url(path: str) -> str:
    from urllib.parse import quote
    return (f"{CONFIG['org'].rstrip('/')}/{CONFIG['project']}/_wiki/wikis/"
            f"{CONFIG['wiki']}?pagePath={quote(path or '')}")


def _auth_build(state):
    """(version, build_number, build_url, detail) — from a `version` mock, else the live
    Authenticator release-app build. version is None on failure."""
    ov = mock_input("version", MISSING)
    if ov is not MISSING and ov:
        d = ov if isinstance(ov, dict) else {"version": ov}
        return (d.get("version"), d.get("build_number"), d.get("build_url"), "")
    branch = (getattr(state, "versions", None) or {}).get("authenticator")
    if not branch:
        return (None, None, None, "no Authenticator release branch on record (state.versions.authenticator)")
    from tools.pipelines import find_auth_release_build, auth_build_url
    ok, info, detail = find_auth_release_build(branch)
    if not ok or not info:
        return (None, None, None, detail or "no succeeded Authenticator release-app build yet")
    return (info.get("version"), info.get("build_number"), auth_build_url(info.get("build_id")), "")


def _prs(state):
    """(prs, detail) — merged-PR list [{id,title}], noise-filtered. From a `prs` mock, else live."""
    ov = mock_input("prs", MISSING)
    if ov is not MISSING and ov is not None:
        rows = list(ov)
    else:
        branch = (getattr(state, "versions", None) or {}).get("authenticator")
        if not branch:
            return (None, "no Authenticator release branch to derive PRs from")
        from tools.pipelines import merged_release_prs
        ok, rows, detail = merged_release_prs(branch)
        if not ok:
            return (None, detail)
    clean = [p for p in rows
             if not any(str(p.get("title", "")).startswith(pfx) for pfx in _NOISE_PREFIXES)]
    return (clean, "")


def _render(state, version, build_number, build_url, prs) -> str:
    run_link = ""
    if build_url:
        label = f"Pipelines - Run {build_number}" if build_number else "Pipelines - Run"
        run_link = f" [{label}]({build_url})"
    pr_lines = "\n".join(f"PR {p.get('id')}: {p.get('title')}" for p in prs) or "_No merged PRs derived._"
    sv = getattr(state, "versions", None) or {}
    sdk_lines = "\n".join(f"*   {label}: {sv.get(key) or '_TBD_'}" for key, label in CONFIG["sdks"])
    return (
        f"#App Version\n{version}{run_link}\n\n\n"
        f"Release Payload\n===============\n\n"
        f"Authenticator + DID\n-------------\n{pr_lines}\n\n"
        f"* * *\n\n"
        f"Auth Client Android SDKs\n------------------------\n\n"
        f"### Release: {_month_year(state)}\n\n"
        f"Email with release notes: _Add the Broker release-announcement email title._\n\n"
        f"{sdk_lines}\n\n"
        f"* * *\n\n"
        f"DID\n---\n\n"
        f"* * *\n\n"
        f"### Sign-offs\n\n"
        f"*   Broker Library / Authenticator: _Add sign-off._\n"
        f"*   DID: _Add sign-off._\n\n"
        f"Expected Feature flags rollouts\n-------------------------------\n\n"
        f"_Feature owners: list the flags rolling out this release and their default states._\n"
    )


def compose_payload(state):
    """Resolve everything and render the payload markdown. Returns (ok, plan, detail) where
    plan = {content, page_name, page_path, url, version, build_url, pr_count}. Read-only."""
    version, build_number, build_url, d = _auth_build(state)
    if not version:
        return (False, None, f"couldn't resolve the Authenticator version ({d})")
    prs, dp = _prs(state)
    if prs is None:
        return (False, None, f"couldn't derive the merged-PR list ({dp})")
    content = _render(state, version, build_number, build_url, prs)
    path = page_path(state)
    return (True, {"content": content, "page_name": page_name(state), "page_path": path,
                   "url": wiki_url(path), "version": version, "build_url": build_url,
                   "pr_count": len(prs)}, "")


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"wiki_payload: {fail}")

    ok, plan, detail = compose_payload(state)
    if not ok:
        hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
        return Blocked(f"wiki_payload: {detail}{hint}.")

    from tools.checks import wiki_page_exists
    exists = wiki_page_exists(CONFIG["org"], CONFIG["project"], CONFIG["wiki"], plan["page_path"])
    verb = "update" if exists else "create"      # None (unknown) -> treated as create (create is exist-safe)
    summary = (f"{verb.capitalize()} the '{plan['page_name']}' payload wiki page — App Version "
               f"{plan['version']} + {plan['pr_count']} merged PR(s) + SDK versions "
               f"(email/sign-offs/feature-flags left as placeholders).")
    return NeedsSkill(
        tool="create-payload-wiki",
        payload={
            "release": state.release_id,
            "plan": {"page_name": plan["page_name"], "page_path": plan["page_path"],
                     "url": plan["url"], "version": plan["version"], "pr_count": plan["pr_count"],
                     "action": verb, "content": plan["content"]},
            "followup_command": f"create-payload-wiki --release {state.release_id} --dry-run",
        },
        record_as=ID,
        summary=summary,
        note=f"{verb} payload page '{plan['page_name']}' ({plan['pr_count']} PRs)",
        outbound=True,
    )


run = legacy_run(build)
