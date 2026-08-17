"""Step: `cg` — report critical Component Governance alerts (Phase 0, S8).

Reads active CG alerts (read-only, via `az rest`) and reports counts + High/Critical
items. Report-only in general, but BLOCKS (holds) when there are active High/Critical
alerts — the owner must fix them and RERUN, or skip to override. Deterministic → an
`agent` step the engine runs in-process.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input

ID = "cg"
KIND = "agent"

# Step config (co-located — this module is the single home for the step).
# Component Governance alerts for the governed repo, read (read-only) from the CG
# governance host via `az rest`; reports ACTIVE alerts and blocks on High/Critical.
CONFIG = {
    "resource": "499b84ac-1321-427f-aa17-267ca6975798",   # Azure DevOps resource id (for `az rest`)
    "governance_host": "https://msazure.governance.visualstudio.com",
    "project_id": "b32aa71e-8ed2-41b2-9d77-5bc261222004",  # msazure/One
    "governed_repo_id": 104410,                            # AD-MFA-phonefactor-phoneApp-android
    "branch": "working",
    "high_severities": ["critical", "high"],               # surfaced/flagged as high-priority
    # Portal link to the repo's CG alerts page — stored as a link so the owner can
    # jump straight to the alerts.
    "alerts_url": "https://msazure.visualstudio.com/One/_componentGovernance/AD-MFA-phonefactor-phoneApp-android",
}

# Properties this step exposes to mocks.local.yaml (see `mock-spec`).
MOCKABLE = {
    "alerts": {
        "kind": "input",
        "desc": ("Inject a CG alerts list (each {severity, alertState, title, …}); "
                 "the REAL report/block logic runs on it — no az call. Great for "
                 "rehearsing the High/Critical block path."),
    },
}


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


def _alert_link(a: dict):
    """Best-effort per-alert portal URL from the raw CG alert, if present."""
    url = a.get("url") or (((a.get("_links") or {}).get("web") or {}).get("href"))
    if not url:
        return None
    title = a.get("title") or a.get("summary") or "alert"
    return {"name": str(title)[:60], "url": url}


def _cg_links(cfg: dict, high: list) -> list:
    """Durable refs for the CG step: the repo's alerts page (from config) + any
    per-alert deep links the API returned."""
    links = []
    page = cfg.get("alerts_url")
    if page:
        links.append({"name": "Component Governance alerts", "url": page})
    for a in high:
        lk = _alert_link(a)
        if lk:
            links.append(lk)
    return links


def build(state):
    cfg = CONFIG
    # Injected `alerts` (mocks.local.yaml) → run the REAL report/block logic on your
    # data, skipping the live az call.
    injected = mock_input("alerts")
    if injected is not None:
        alerts = injected
    else:
        required = ("resource", "governance_host", "project_id", "governed_repo_id", "branch")
        if not all(cfg.get(k) for k in required):
            return Blocked("cg: incomplete configuration")
        from tools.checks import fetch_cg_alerts
        ok, alerts, detail = fetch_cg_alerts(
            cfg["resource"], cfg["governance_host"], cfg["project_id"],
            cfg["governed_repo_id"], cfg["branch"])
        if not ok:
            return Blocked(f"cg: could not read alerts — {detail}")
    active, high = _cg_summary(alerts, cfg.get("high_severities", ["critical", "high"]))
    report = _cg_report(active, high)
    links = _cg_links(cfg, high)
    if high:
        # Block: the report is shown, the step holds. The owner fixes the alerts
        # and reruns this step (re-checks), or skips to override.
        return Blocked(
            report + "\n→ Fix the High/Critical alerts (or wait for remediation), then "
            "RERUN this step to re-check — or skip to override with a reason.",
            links=links)
    return Done(report, links=links)


run = legacy_run(build)
