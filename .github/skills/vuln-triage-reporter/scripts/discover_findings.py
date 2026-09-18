#!/usr/bin/env python3
"""Summarize IcM [MSRC]/[ITD] search results into a triage inventory table.

Repeatable weekly-run helper for the `vuln-triage-reporter` skill. The IcM *query* is performed by the
agent via the IcM MCP (search_incidents with owningTeamId + dateRange). The MCP writes large results to
JSON resource files; pass those file paths here to get a clean inventory.

Usage:
    python discover_findings.py <results.json> [<results2.json> ...]
    python discover_findings.py <results.json> --window-days 7

Each input JSON is the MCP `search_incidents` payload: an object with a "value" (or "items"/"result")
array of incident records. Records are filtered to MSRC/ITD/security-risk and printed as a table.

Team IDs in scope (Android Authenticator & Broker) — routing integers, inert without corp IcM access:
    65431  Cloud Identity AuthN MSAL Android
    65436  Cloud Identity AuthN ADAL Android
    78848  Auth Client Android Shield
    148914 Android Microsoft Authenticator App
Refresh via the IcM MCP `get_teams_by_name` at run start in case routing changed. Do NOT add sensitive
data here (telemetry sampling/coverage, internal security-control logic, PII) — this is a public repo.
"""
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")


def load_records(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    for key in ("value", "items", "result"):
        if isinstance(d, dict) and isinstance(d.get(key), list):
            return d[key]
    if isinstance(d, list):
        return d
    return []


def flags(rec):
    title = (rec.get("title") or "")
    tags = rec.get("tags") or []
    blob = (title + " " + " ".join(tags)).upper()
    fl = []
    if "[MSRC]" in blob or "MSRC" in (t.upper() for t in tags) or "MSRC" in blob:
        fl.append("MSRC")
    if "ITD" in blob or "IDSEC" in blob:
        fl.append("ITD")
    if rec.get("isSecurityRisk"):
        fl.append("SECRISK")
    return fl


def within_window(rec, cutoff):
    if cutoff is None:
        return True
    created = rec.get("createdDate")
    if not created:
        return True
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return True
    return dt >= cutoff


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="MCP search_incidents JSON result file(s)")
    ap.add_argument("--window-days", type=int, default=None,
                    help="Only include incidents created within the last N days (default: no filter)")
    args = ap.parse_args()

    cutoff = None
    if args.window_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.window_days)

    seen = {}
    for path in args.files:
        for rec in load_records(path):
            fl = flags(rec)
            if not fl:
                continue
            if not within_window(rec, cutoff):
                continue
            seen[rec.get("id")] = (rec, fl)

    rows = sorted(seen.values(), key=lambda rf: rf[0].get("createdDate", ""), reverse=True)

    print(f"# MSRC/ITD triage inventory  (findings: {len(rows)}"
          + (f", window: last {args.window_days}d" if cutoff else "") + ")\n")
    print("| Created | Tag | Sev | State | IcM | Title |")
    print("|---------|-----|-----|-------|-----|-------|")
    for rec, fl in rows:
        title = (rec.get("title") or "").replace("|", "\\|")
        if len(title) > 90:
            title = title[:87] + "..."
        print(f"| {rec.get('createdDate','')[:10]} | {'/'.join(fl)} | "
              f"{rec.get('severity','?')} | {rec.get('state','?')} | {rec.get('id','?')} | {title} |")

    if not rows:
        print("\n_No MSRC/ITD/security-risk incidents in the provided results"
              + (" for the window." if cutoff else ".") + "_")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
