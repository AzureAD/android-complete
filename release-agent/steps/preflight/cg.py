"""Step: `cg` — report critical Component Governance alerts (Phase 0, S8).

Reads active CG alerts (read-only, via `az rest`) and reports counts + High/Critical
items. Report-only in general, but BLOCKS (holds) when there are active High/Critical
alerts — the owner must fix them and RERUN, or skip to override. Deterministic → an
`agent` step the engine runs in-process; a dry-run simulates.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from orchestrator.phase_config import load_phase_config
from steps.lib.agent import legacy_run

ID = "cg"
KIND = "agent"


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


def build(state):
    cfg = load_phase_config("preflight").get("cg", {})
    if state.dry_run:
        return Done("[dry-run] Would query Component Governance for active alerts and "
                    "report counts + High/Critical items (blocking on High/Critical).")
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
    if high:
        # Block: the report is shown, the step holds. The owner fixes the alerts
        # and reruns this step (re-checks), or skips to override.
        return Blocked(
            report + "\n→ Fix the High/Critical alerts (or wait for remediation), then "
            "RERUN this step to re-check — or skip to override with a reason.")
    return Done(report)


run = legacy_run(build)
