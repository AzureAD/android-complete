"""Real Phase-0 pre-flight agents (deterministic; az CLI / HTTP).

These replace the `stub` for specific Phase-0 steps with genuine actions:

  * breaking_detect  (step `breaking`, S2) — reads the common-for-android
      changelog, finds breaking ([MAJOR]) changes in the unreleased section,
      and drafts the OneAuth comms. Read-only.
  * wiki_payload     (step `wiki`, S0)     — creates the per-release payload
      wiki subpage under the standing history page. A real ADO write.

Contract (shared with the stub):  run(phase_id, step, dry_run, state) -> StepResult

Dry-run safety: in a dry-run release every agent SIMULATES — no network, no
writes — so tests and dry-run rehearsals never touch production. Only a real
(dry_run=False) release performs the action.
"""
from __future__ import annotations
from urllib import request as _request

from phases.stub_runner import StepResult


# ---- config ----------------------------------------------------------------
def _load_cfg() -> dict:
    """Phase-0 config (config/preflight.yaml), via the shared per-phase loader."""
    from orchestrator.phase_config import load_phase_config
    return load_phase_config("preflight")


def _fetch_text(url: str, timeout: int = 20) -> str:
    req = _request.Request(url, headers={"User-Agent": "release-agent-preflight/1.0"})
    with _request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


# ---- breaking-change detection (pure, unit-tested) -------------------------
def parse_breaking(changelog_text: str, section: str = "vNext",
                   tag: str = "[MAJOR]") -> list:
    """Return the list of `tag` (breaking) entry lines inside `section`.

    The changelog is a flat text file: a section header line (e.g. "vNext"),
    an underline, then `- [SEVERITY] ... (#PR)` bullets, until the next
    "Version X.Y.Z" header. We scan only the requested section.
    """
    entries, in_section = [], False
    for raw in changelog_text.splitlines():
        s = raw.strip()
        if not in_section:
            if s == section:
                in_section = True
            continue
        if s.startswith("Version "):
            break
        if tag in raw:
            entries.append(s)
    return entries


def _draft_breaking_comms(entries: list, state=None) -> str:
    release = getattr(state, "release_id", None) or "this release"
    bullets = "\n".join(f"- {e}" for e in entries)
    return (
        f"Subject: [Action] Breaking OneAuth changes in {release}\n\n"
        f"Hi OneAuth team,\n\n"
        f"The upcoming Android common release ({release}) contains the following "
        f"breaking change(s). Please review for downstream impact before code "
        f"complete:\n\n{bullets}\n\n"
        f"Thanks,\nRelease Orchestrator"
    )


def run_breaking(phase_id: str, step: dict, dry_run: bool, state=None) -> StepResult:
    cfg = _load_cfg().get("breaking", {})
    url = cfg.get("changelog_url")
    section = cfg.get("section", "vNext")
    tag = cfg.get("breaking_tag", "[MAJOR]")
    if dry_run:
        return StepResult(
            True,
            f"[dry-run] Would scan the '{section}' changelog section for {tag} "
            f"(breaking) entries and draft OneAuth comms.",
            "agent",
        )
    if not url:
        return StepResult(False, "breaking: no changelog_url configured", "agent")
    try:
        text = _fetch_text(url)
    except Exception as e:  # noqa: BLE001 - network/parse errors -> hold for human
        return StepResult(False, f"breaking: could not fetch changelog ({e})", "agent")
    entries = parse_breaking(text, section, tag)
    if not entries:
        return StepResult(
            True, f"No breaking ({tag}) changes in '{section}' — no OneAuth comms needed.",
            "agent",
        )
    listing = "\n".join(f"  - {e}" for e in entries)
    draft = _draft_breaking_comms(entries, state)
    return StepResult(
        True,
        f"Detected {len(entries)} breaking ({tag}) change(s) in '{section}':\n{listing}\n\n"
        f"--- DRAFT COMMS (send to OneAuth) ---\n{draft}",
        "agent",
    )


# ---- payload wiki subpage --------------------------------------------------
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


def run_wiki(phase_id: str, step: dict, dry_run: bool, state=None) -> StepResult:
    cfg = _load_cfg().get("wiki", {})
    org = cfg.get("org")
    project = cfg.get("project")
    wiki = cfg.get("wiki")
    parent = (cfg.get("parent_path") or "").rstrip("/")
    base_name = _page_name(state)
    base_path = f"{parent}/{base_name}"
    if dry_run:
        return StepResult(
            True,
            f"[dry-run] Would create payload wiki subpage '{base_name}' under "
            f"'{parent}' (duplicate-safe: a second numbered page if it already exists).",
            "agent",
        )
    if not (org and project and wiki and parent):
        return StepResult(False, "wiki: incomplete configuration", "agent")
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
                    return StepResult(False, f"wiki: could not create '{cand_path}' — {res.detail}", "agent")
                return StepResult(
                    True,
                    f"⚠ A payload page already exists for this month ('{base_name}'). "
                    f"Left it untouched and created a SECOND page: '{cand_name}'. ({res.detail})",
                    "agent",
                )
            n += 1
        return StepResult(False, f"wiki: too many existing pages for '{base_name}'", "agent")

    res = create_wiki_page(org, project, wiki, base_path, _payload_template(state))
    if not res.ok:
        return StepResult(False, f"wiki: could not create '{base_path}' — {res.detail}", "agent")
    return StepResult(True, f"Payload wiki subpage ready: '{base_name}' ({res.detail})", "agent")


# ---- Component Governance alerts (report-only) -----------------------------
def _cg_summary(alerts: list, high_sev: list):
    """Return (active, high) lists from raw alerts. `high` = active alerts whose
    severity is in high_sev (critical/high)."""
    active = [a for a in alerts if (a.get("alertState") or "").lower() == "active"]
    high = [a for a in active if (a.get("severity") or "").lower() in
            [s.lower() for s in high_sev]]
    return active, high


def _cg_report(active: list, high: list) -> str:
    from collections import Counter
    by_sev = Counter((a.get("severity") or "unknown").lower() for a in active)
    counts = ", ".join(f"{n} {s}" for s, n in sorted(by_sev.items(),
                       key=lambda kv: (-kv[1], kv[0]))) or "none"
    lines = [f"Component Governance: {len(active)} active alert(s) — {counts}."]
    if high:
        lines.append(f"High/Critical ({len(high)}) — review before release:")
        for a in high[:15]:
            comp = (a.get("component") or {}).get("displayName") or ""
            ver = (a.get("component") or {}).get("displayVersion") or ""
            rec = (a.get("actionItems") or "").strip().split("\n")[0]
            title = a.get("title") or a.get("summary") or "?"
            sev = (a.get("severity") or "").capitalize()
            comp_str = f" — {comp} {ver}".rstrip() if comp else ""
            lines.append(f"  • [{sev}] {title}{comp_str}" + (f" — {rec}" if rec else ""))
    else:
        lines.append("No High/Critical active alerts.")
    return "\n".join(lines)


def run_cg_alerts(phase_id: str, step: dict, dry_run: bool, state=None) -> StepResult:
    """Report active Component Governance alerts. Passes when there are no active
    High/Critical alerts; BLOCKS (holds) when there are — the owner must fix the
    issues and RERUN this step (re-checks), or override by skipping it."""
    cfg = _load_cfg().get("cg", {})
    if dry_run:
        return StepResult(
            True,
            "[dry-run] Would query Component Governance for active alerts and "
            "report counts + High/Critical items (blocking on High/Critical).",
            "agent",
        )
    required = ("resource", "governance_host", "project_id", "governed_repo_id", "branch")
    if not all(cfg.get(k) for k in required):
        return StepResult(False, "cg: incomplete configuration", "agent")
    from tools.checks import fetch_cg_alerts
    ok, alerts, detail = fetch_cg_alerts(
        cfg["resource"], cfg["governance_host"], cfg["project_id"],
        cfg["governed_repo_id"], cfg["branch"])
    if not ok:
        return StepResult(False, f"cg: could not read alerts — {detail}", "agent")
    active, high = _cg_summary(alerts, cfg.get("high_severities", ["critical", "high"]))
    report = _cg_report(active, high)
    if high:
        # Block: the report is shown, the step holds. The owner fixes the alerts
        # and reruns this step (re-checks), or skips to override.
        return StepResult(
            False,
            report + "\n→ Fix the High/Critical alerts (or wait for remediation), then "
            "RERUN this step to re-check — or skip to override with a reason.",
            "agent",
        )
    return StepResult(True, report, "agent")


# ---- Calendar Checker schedule verification --------------------------------
def _iso_age_days(iso: str):
    """Whole days between an ISO-8601 timestamp and now (UTC), or None if unparseable."""
    from datetime import datetime, timezone
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except ValueError:
        return None


def run_cron_check(phase_id: str, step: dict, dry_run: bool, state=None) -> StepResult:
    """Verify the Calendar Checker pipeline is scheduled AND firing, by confirming a
    recent `schedule`-reason run. Passes if a scheduled run is within the staleness
    window; BLOCKS (fix + rerun, or skip) if there's none or it's stale."""
    cfg = _load_cfg().get("cron", {})
    name = cfg.get("name", "Calendar Checker")
    if dry_run:
        return StepResult(
            True,
            f"[dry-run] Would verify '{name}' (pipeline {cfg.get('pipeline_id')}) has a "
            f"recent scheduled run.",
            "agent",
        )
    if not all(cfg.get(k) for k in ("pipeline_id", "org", "project")):
        return StepResult(False, "cron: incomplete configuration", "agent")
    from tools.checks import latest_scheduled_build
    ok, run, detail = latest_scheduled_build(cfg["org"], cfg["project"], cfg["pipeline_id"])
    if not ok:
        return StepResult(False, f"cron: could not read build history — {detail}", "agent")
    if not run:
        return StepResult(
            False,
            f"{name}: no scheduled run found in recent history — the cron may be "
            f"disabled. Investigate, then rerun this step (or skip to override).",
            "agent",
        )
    age = _iso_age_days(run.get("queueTime"))
    max_stale = cfg.get("max_staleness_days", 2)
    when = (run.get("queueTime") or "")[:16]
    if age is not None and age > max_stale:
        return StepResult(
            False,
            f"{name}: last scheduled run was {when} ({age}d ago) — stale (> {max_stale}d). "
            f"The schedule may be broken. Fix + rerun this step, or skip to override.",
            "agent",
        )
    return StepResult(
        True,
        f"{name} is scheduled and firing — last scheduled run {when} ({run.get('result')}).",
        "agent",
    )


# ---- registry --------------------------------------------------------------
REGISTRY = {
    "breaking_detect": run_breaking,
    "wiki_payload": run_wiki,
    "cg_alerts": run_cg_alerts,
    "cron_check": run_cron_check,
}
