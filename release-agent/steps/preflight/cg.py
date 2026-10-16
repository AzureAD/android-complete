"""Step: `cg` — report critical Component Governance alerts (Phase 0, S8).

Reads active CG alerts (read-only, via `az rest`) and reports counts + High/Critical
items. Report-only in general, but BLOCKS (holds) when there are active High/Critical
alerts — the owner must fix them and RERUN, or skip to override. Deterministic → an
`agent` step the engine runs in-process.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext, thaw

from orchestrator.outcomes import Done, Blocked
from tools.coordinates import coords
from steps.lib.mockctx import MISSING

ID = "cg"
KIND = "agent"
EFFECT_MODE = "read_only"

# Step config (co-located — this module is the single home for the step).
# Component Governance alerts for the governed repo, read (read-only) from the CG
# governance host via `az rest`; reports ACTIVE alerts and blocks on High/Critical.
# Coordinates (resource id, host, repo ids, org/project) come from config/coordinates.yaml.
_AUTH_REPO = coords.repo("authenticator")
CONFIG = {
    "resource": coords.resource_id(),                      # Azure DevOps resource id (for `az rest`)
    "governance_host": coords.host("cg_governance"),
    "project_id": _AUTH_REPO["project_id"],                # msazure/One
    "governed_repo_id": _AUTH_REPO["cg_repo_id"],          # AD-MFA-phonefactor-phoneApp-android
    "branch": "working",
    "high_severities": ["critical", "high"],               # surfaced/flagged as high-priority
    # Portal link to the repo's CG alerts page — stored as a link so the owner can
    # jump straight to the alerts.
    "alerts_url": (f"{_AUTH_REPO['org']}/{_AUTH_REPO['project']}"
                   f"/_componentGovernance/{_AUTH_REPO['name']}"),
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
    if not isinstance(alerts, list):
        raise ValueError("alerts must be an array")
    severities = {"critical", "high", "medium", "moderate", "low", "informational", "none"}
    for index, alert in enumerate(alerts, 1):
        if not isinstance(alert, dict):
            raise ValueError(f"alert {index} must be an object")
        state = alert.get("alertState")
        if not isinstance(state, str) or state.strip().lower() not in {
                "active", "fixed", "dismissed", "autodismissed", "resolved", "closed"}:
            raise ValueError(f"alert {index} has missing or unknown state")
        if state.strip().lower() == "active":
            severity = alert.get("severity")
            if not isinstance(severity, str) or severity.strip().lower() not in severities:
                raise ValueError(f"active alert {index} has missing or unknown severity")
            if alert.get("component") is not None and not isinstance(alert["component"], dict):
                raise ValueError(f"active alert {index} has invalid component data")
            if alert.get("actionItems") is not None and not isinstance(alert["actionItems"], str):
                raise ValueError(f"active alert {index} has invalid action items")
            if alert.get("url") is not None and not isinstance(alert["url"], str):
                raise ValueError(f"active alert {index} has an invalid alert URL")
            links = alert.get("_links")
            if links is not None:
                if not isinstance(links, dict):
                    raise ValueError(f"active alert {index} has invalid links")
                web = links.get("web")
                if web is not None and not isinstance(web, dict):
                    raise ValueError(f"active alert {index} has an invalid web link")
    active = [a for a in alerts if a["alertState"].strip().lower() == "active"]
    high = [a for a in active if a["severity"].strip().lower() in
            [s.lower() for s in high_sev]]
    return active, high


def _cg_report(active: list, high: list) -> str:
    from collections import Counter
    by_sev = Counter((a.get("severity") or "unknown").strip().lower() for a in active)
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
            sev = (a.get("severity") or "").strip().capitalize()
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


def build(context: StepContext):
    cfg = CONFIG
    # Injected `alerts` (mocks.local.yaml) → run the REAL report/block logic on your
    # data, skipping the live az call.
    injected = context.input("alerts", MISSING)
    if injected is not MISSING:
        alerts = injected
    else:
        required = ("resource", "governance_host", "project_id", "governed_repo_id", "branch")
        if not all(cfg.get(k) for k in required):
            return Blocked("cg: incomplete configuration")
        ok, alerts, detail = context.services.repositories.fetch_cg_alerts(
            cfg["resource"], cfg["governance_host"], cfg["project_id"],
            cfg["governed_repo_id"], cfg["branch"])
        if not ok:
            return Blocked(f"cg: could not read alerts — {detail}")
    try:
        active, high = _cg_summary(alerts, cfg.get("high_severities", ["critical", "high"]))
    except ValueError as exc:
        return Blocked(f"cg: invalid alerts — {exc}", links=_cg_links(cfg, []))
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
