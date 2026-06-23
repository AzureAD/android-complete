#!/usr/bin/env python3
"""Build a concise, email-ready WEEKLY STATUS report from the triage classifications.

Part of the `vuln-triage-reporter` skill. This is the manager-tracking artifact — a single compact table
(IcM · Bug · Severity · Owner · Status · Work Item · Updated). It is NOT the research report: no evidence,
no file:line, no audit trail. Output is self-contained HTML that pastes cleanly into Outlook.

Status vocabulary (manager-friendly): Not started · In progress · Blocked · In review · Complete.

Inputs:
  classifications.csv   — id, our_tier, component, assignment, title (the triage roll-up CSV)
  --map <file>          — OPTIONAL JSON or CSV mapping IcM id -> ADO work-item id, e.g.
                          {"NNNNNN": NNNN}  (or CSV with columns: id,work_item)
                          Alternatively, add a `work_item` column directly to classifications.csv.
  --token <file>        — OPTIONAL file containing an ADO bearer token (obtain externally, e.g.
                          `az account get-access-token --resource <azure-devops-app-id> --query accessToken`).
                          When given, the script fetches each mapped work item's live State / ChangedDate /
                          Tags and derives the Status column from them. Without it, Status falls back to a
                          `status` column in the CSV, else "Not started".
  --out <file>          — write HTML here as UTF-8 (recommended). Else prints to stdout.
  --window "<label>"    — header window label, e.g. "2026-06-18 -> 2026-06-25".

Usage:
  python build_status_report.py classifications.csv --map map.json --token tok.txt \
      --out weekly-status.html --window "2026-06-18 -> 2026-06-25"
"""
import argparse
import csv
import datetime
import html as htmllib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rollup  # noqa: E402  (reuse canonical_repo / derive_assignment for a consistent Owner split)

ORG = "https://identitydivision.visualstudio.com"
PROJECT = "Engineering"

# report status -> sort order (needs-attention first) + chip colour (bg, fg)
STATUS_ORDER = {"Blocked": 0, "In progress": 1, "In review": 2, "Not started": 3, "Complete": 4}
STATUS_COLOR = {
    "Blocked": ("#fde8e8", "#b91c1c"),
    "In progress": ("#e0f2fe", "#075985"),
    "In review": ("#ede9fe", "#5b21b6"),
    "Not started": ("#f1f5f9", "#475569"),
    "Complete": ("#dcfce7", "#15803d"),
}
SEV_ORDER = {"critical": 0, "important": 1, "moderate": 2, "low": 3}
SEV_COLOR = {
    "critical": ("#fde8e8", "#b91c1c"), "important": ("#fdebd9", "#c2410c"),
    "moderate": ("#fdf3d3", "#a16207"), "low": ("#dcfce7", "#15803d"),
}


def map_ado_state(state, tags):
    """ADO System.State (+ Tags) -> the report's small status vocabulary."""
    s = (state or "").strip().lower()
    t = (tags or "").lower()
    if "block" in t or s in ("on hold",):
        return "Blocked"
    if s in ("done", "closed", "completed", "resolved-complete"):
        return "Complete"
    if s in ("in review", "code review", "review", "resolved"):
        return "In review"
    if s in ("committed", "active", "in progress", "doing"):
        return "In progress"
    # New / Approved / Proposed / unknown
    return "Not started"


def load_map(path):
    if not path or not os.path.isfile(path):
        return {}
    if path.lower().endswith(".json"):
        raw = json.load(open(path, encoding="utf-8"))
        return {str(k): int(v) for k, v in raw.items() if str(v).strip()}
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            wid = (r.get("work_item") or r.get("ab") or "").strip().lstrip("AB#").strip()
            if r.get("id") and wid:
                out[str(r["id"]).strip()] = int(wid)
    return out


def fetch_ado(work_id, token):
    """Return (state, changed_date 'MM-DD', tags) for a work item, or (None, '', '')."""
    import urllib.request
    import urllib.error
    url = (f"{ORG}/{PROJECT}/_apis/wit/workitems/{work_id}"
           "?fields=System.State,System.ChangedDate,System.Tags&api-version=7.0")
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            f = json.loads(resp.read().decode("utf-8")).get("fields", {})
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"  ! ADO fetch failed for {work_id}: HTTP {e.code}\n")
        return None, "", ""
    changed = (f.get("System.ChangedDate", "") or "")[5:10]  # YYYY-MM-DD -> MM-DD
    return f.get("System.State"), changed, f.get("System.Tags", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_file")
    ap.add_argument("--map", default=None, help="JSON/CSV mapping IcM id -> ADO work-item id")
    ap.add_argument("--token", default=None, help="File with an ADO bearer token (enables live status)")
    ap.add_argument("--out", default=None, help="Write HTML here as UTF-8 (recommended)")
    ap.add_argument("--window", default="", help="Header window label")
    ap.add_argument("--title", default="Security Triage — Weekly Status")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csv_file, encoding="utf-8")))
    wmap = load_map(args.map)
    # also accept a work_item column directly on the CSV
    for r in rows:
        wid = (r.get("work_item") or "").strip().lstrip("AB#").strip()
        if r.get("id") and wid and str(r["id"]).strip() not in wmap:
            wmap[str(r["id"]).strip()] = int(wid)

    token = open(args.token, encoding="utf-8").read().strip() if args.token and os.path.isfile(args.token) else None

    items = []
    for r in rows:
        icm = (r.get("id") or "").strip()
        tier = (r.get("our_tier") or "").strip()
        tier_key = next((k for k in SEV_ORDER if k in tier.lower()), "moderate")
        owner = "E" if rollup.derive_assignment(tier, r.get("icm_sev", ""), r.get("component", "")) \
            == "Engineer-owned" else "I"
        work_id = wmap.get(icm)
        status, updated = "Not started", ""
        if work_id and token:
            state, updated, tags = fetch_ado(work_id, token)
            status = map_ado_state(state, tags)
        elif (r.get("status") or "").strip():
            status = r["status"].strip()
        items.append({
            "icm": icm,
            "bug": (r.get("title") or "").strip(),
            "tier": tier or "—",
            "tier_key": tier_key,
            "owner": owner,
            "status": status,
            "work_id": work_id,
            "updated": updated,
        })

    items.sort(key=lambda x: (STATUS_ORDER.get(x["status"], 9), SEV_ORDER.get(x["tier_key"], 9)))

    from collections import Counter
    sc = Counter(i["status"] for i in items)
    count_line = f'{len(items)} findings · ' + ' · '.join(
        f'{sc[s]} {s.lower()}' for s in sorted(sc, key=lambda s: STATUS_ORDER.get(s, 9)))
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    def chip(text, bg, fg):
        return (f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;'
                f'background:{bg};color:{fg};font-size:12px;font-weight:600;white-space:nowrap">'
                f'{htmllib.escape(text)}</span>')

    trs = []
    for i in items:
        sbg, sfg = STATUS_COLOR.get(i["status"], ("#f1f5f9", "#475569"))
        vbg, vfg = SEV_COLOR.get(i["tier_key"], ("#f1f5f9", "#475569"))
        icm_link = (f'<a href="https://portal.microsofticm.com/imp/v5/incidents/details/{i["icm"]}/summary"'
                    f' style="color:#0f6cbd;text-decoration:none">{htmllib.escape(i["icm"])}</a>'
                    if i["icm"] else "—")
        wi = (f'<a href="{ORG}/{PROJECT}/_workitems/edit/{i["work_id"]}"'
              f' style="color:#0f6cbd;text-decoration:none">AB#{i["work_id"]}</a>'
              if i["work_id"] else '<span style="color:#94a3b8">—</span>')
        td = 'style="padding:6px 10px;border-bottom:1px solid #e2e6ea;font-size:13px;vertical-align:top"'
        trs.append(
            "<tr>"
            f'<td {td}>{icm_link}</td>'
            f'<td {td}>{htmllib.escape(i["bug"])}</td>'
            f'<td {td}>{chip(i["tier"], vbg, vfg)}</td>'
            f'<td {td} align="center">{htmllib.escape(i["owner"])}</td>'
            f'<td {td}>{chip(i["status"], sbg, sfg)}</td>'
            f'<td {td}>{wi}</td>'
            f'<td {td} style="padding:6px 10px;border-bottom:1px solid #e2e6ea;font-size:13px;'
            f'color:#5b6470">{htmllib.escape(i["updated"]) or "—"}</td>'
            "</tr>")

    th = ('style="text-align:left;padding:6px 10px;border-bottom:2px solid #cbd5e1;font-size:11px;'
          'text-transform:uppercase;letter-spacing:.03em;color:#5b6470"')
    sub = (args.window + " · " if args.window else "") + count_line
    html = f"""<div style="font-family:'Segoe UI',Arial,sans-serif;color:#1a1a2e;max-width:920px">
<div style="font-size:16px;font-weight:700">{htmllib.escape(args.title)}</div>
<div style="font-size:12px;color:#5b6470;margin:2px 0 10px">{htmllib.escape(sub)}</div>
<table style="border-collapse:collapse;width:100%">
<thead><tr>
<th {th}>IcM</th><th {th}>Bug</th><th {th}>Sev</th><th {th} align="center">Owner</th>
<th {th}>Status</th><th {th}>Work Item</th><th {th}>Updated</th>
</tr></thead><tbody>{''.join(trs)}</tbody></table>
<div style="font-size:11px;color:#94a3b8;margin-top:8px">Generated {generated} · Owner: E = engineer · I = intern · high-level status only (see the research report for evidence).</div>
</div>"""

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  + {args.out}")
    else:
        print(html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
