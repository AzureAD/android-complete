"""Step: `wiki` — create the per-release payload wiki subpage (Phase 0, S0).

Creates '<Month> <Year> Release' under the standing history page (a real ADO write;
Phase 2 later fills in built versions). Duplicate-safe: if the month's page already
exists it is left untouched and the next free numbered page is created instead.
Deterministic → an `agent` step the engine runs in-process.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input

ID = "wiki"
KIND = "agent"

# Step config (co-located). Creates the per-release payload page under the standing
# history parent page (Phase 2 later writes the built versions into it).
CONFIG = {
    "org": "https://identitydivision.visualstudio.com",
    "project": "IdentityWiki",
    "wiki": "IdentityWiki.wiki",
    "parent_path": "/IdentityWiki/Services/Microsoft Authenticator/Release/Android/Monthly Releases Payloads History",
}

# Properties this step exposes to mocks.local.yaml (see `mock-spec`).
MOCKABLE = {
    "page_name": {
        "kind": "input",
        "desc": ("Use this exact payload page name instead of the default "
                 "'<Month> <Year> Release' (a REAL create on your named page)."),
    },
    "name_suffix": {
        "kind": "input",
        "desc": ("Append to the default payload page name (e.g. ' (TEST)') so a REAL "
                 "create lands on a safe, non-colliding page."),
    },
}


def _payload_template(state=None) -> str:
    release = getattr(state, "release_id", None) or "unknown"
    ccd = getattr(state, "ccd", None) or "TBD"
    owner = getattr(state, "owner_name", None) or getattr(state, "owner_email", None) or "TBD"
    return (
        f"# {release} — Release Payload\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Release | {release} |\n| Code Complete Date | {ccd} |\n| Release owner | {owner} |\n\n"
        f"## Built versions\n\n"
        f"_Filled during Build & Lib Verification (Phase 2)._\n\n"
        f"| Artifact | Version |\n|---|---|\n|  |  |\n"
    )


def _page_name(state, n: int = 1) -> str:
    """Payload page name: '<Month> <Year> Release', e.g. 'August 2026 Release'.
    A numbered variant ('August 2026 2 Release') is used when a page already
    exists for the month (n >= 2)."""
    import calendar
    release = getattr(state, "release_id", None) or "unknown"
    try:
        year, month = release.split("-")[:2]
        base = f"{calendar.month_name[int(month)]} {int(year)}"
    except Exception:  # noqa: BLE001 - fall back to the raw id
        base = release
    return f"{base} {n} Release" if n and n >= 2 else f"{base} Release"


def _wiki_url(org: str, project: str, wiki: str, path: str) -> str:
    """Browser URL for an ADO wiki page (so the result links to the created page)."""
    from urllib.parse import quote
    return f"{(org or '').rstrip('/')}/{project}/_wiki/wikis/{wiki}?pagePath={quote(path or '')}"


def build(state):
    cfg = CONFIG
    org = cfg.get("org")
    project = cfg.get("project")
    wiki = cfg.get("wiki")
    parent = (cfg.get("parent_path") or "").rstrip("/")
    # page_name overrides the whole name; name_suffix appends to the default.
    base_name = (mock_input("page_name") or _page_name(state)) + (mock_input("name_suffix") or "")
    base_path = f"{parent}/{base_name}"
    if not (org and project and wiki and parent):
        return Blocked("wiki: incomplete configuration")
    from tools.checks import create_wiki_page, wiki_page_exists

    # Duplicate handling: if the month's page already exists, DON'T overwrite —
    # notify and create the next free "<Month> <Year> N Release" page instead.
    if wiki_page_exists(org, project, wiki, base_path):
        n = 2
        while n <= 50:
            cand_name = _page_name(state, n)
            cand_path = f"{parent}/{cand_name}"
            if not wiki_page_exists(org, project, wiki, cand_path):
                res = create_wiki_page(org, project, wiki, cand_path, _payload_template(state))
                if not res.ok:
                    return Blocked(f"wiki: could not create '{cand_path}' — {res.detail}")
                url = _wiki_url(org, project, wiki, cand_path)
                return Done(
                    f"⚠ A payload page already exists for this month ('{base_name}'). "
                    f"Left it untouched and created a SECOND page: '{cand_name}'.\n"
                    f"Link: {url}",
                    links=[{"name": cand_name, "url": url}])
            n += 1
        return Blocked(f"wiki: too many existing pages for '{base_name}'")

    res = create_wiki_page(org, project, wiki, base_path, _payload_template(state))
    if not res.ok:
        return Blocked(f"wiki: could not create '{base_path}' — {res.detail}")
    url = _wiki_url(org, project, wiki, base_path)
    return Done(
        f"Payload wiki subpage ready: '{base_name}'.\nLink: {url}",
        links=[{"name": base_name, "url": url}])


run = legacy_run(build)
