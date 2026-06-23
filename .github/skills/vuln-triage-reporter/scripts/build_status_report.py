#!/usr/bin/env python3
"""Build a concise, email-ready WEEKLY STATUS report from the triage classifications.

Part of the `vuln-triage-reporter` skill. This is the manager-tracking artifact — a single compact table
(IcM · Bug · Severity · Status · Work Item · Updated). It is NOT the research report: no evidence,
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
  --auto-token          — instead of a token file, acquire the ADO token automatically via `az`
                          (must be logged in). Makes the weekly run a SINGLE command.
  --out <file>          — write HTML here as UTF-8 (recommended). Else prints to stdout.
  --window "<label>"    — header window label, e.g. "2026-06-18 -> 2026-06-25".

Map auto-discovery: if --map is omitted, the script looks for `work-item-map.json` (then
`work-item-map.csv`) NEXT TO the CSV. Persist that map once in the workspace and the weekly run needs
no --map. (The map pairs IcM ids with work-item ids, so it lives in the private workspace, NOT the repo.)

Usage:
  # one-command weekly run (auto-discovered map + auto token):
  python build_status_report.py classifications.csv --auto-token \
      --out weekly-status.html --window "2026-06-18 -> 2026-06-25"

  # explicit map + token file:
  python build_status_report.py classifications.csv --map map.json --token tok.txt \
      --out weekly-status.html --window "2026-06-18 -> 2026-06-25"
"""
import argparse
import csv
import datetime
import html as htmllib
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ORG = "https://identitydivision.visualstudio.com"
PROJECT = "Engineering"

# report status -> sort order (needs-attention first) + chip colour (bg, fg)
STATUS_ORDER = {"Blocked": 0, "In progress": 1, "In review": 2, "Not started": 3,
                "Complete": 4, "Out of scope": 5}
STATUS_COLOR = {
    "Blocked": ("#fde8e8", "#b91c1c"),
    "In progress": ("#e0f2fe", "#075985"),
    "In review": ("#ede9fe", "#5b21b6"),
    "Not started": ("#f1f5f9", "#475569"),
    "Complete": ("#dcfce7", "#15803d"),
    "Out of scope": ("#f1f5f9", "#94a3b8"),
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


def map_tracker_status(text):
    """EXECUTION-TRACKER.md exec-status vocabulary -> the report's small status set, or None if the text
    is not a recognized status (so non-status table cells — e.g. a spec path — are ignored).
    Tracker statuses: NOT STARTED · IN PROGRESS · IMPLEMENTED (local) · PUSHED (no PR) · PR OPEN ·
    MERGED · BLOCKED · OUT OF SCOPE (intern)."""
    s = (text or "").strip().lower()
    if "out of scope" in s:
        return "Out of scope"
    if "blocked" in s:
        return "Blocked"
    if "merged" in s:
        return "Complete"
    if "pr open" in s:
        return "In review"
    if "in progress" in s or "implemented" in s or "pushed" in s:
        return "In progress"
    if "not started" in s:
        return "Not started"
    return None


def load_tracker(path):
    """Parse EXECUTION-TRACKER.md's 'Status at a glance' table -> {icm_id: report_status}.
    Reads each markdown table row, takes the long IcM number from any cell and the exec status from the
    LAST cell (strips **bold**/`code`/parenthetical detail). Rows whose last cell is NOT a recognized
    status (e.g. the bottom 'Out of scope' table whose last cell is a spec path) are skipped."""
    if not path or not os.path.isfile(path):
        return {}
    out = {}
    for line in open(path, encoding="utf-8"):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        icm = next((re.search(r"\b(\d{9,})\b", c).group(1) for c in cells
                    if re.search(r"\b\d{9,}\b", c)), None)
        if not icm:
            continue
        raw = re.sub(r"[`*]", "", cells[-1])      # drop bold/code markers
        status = map_tracker_status(raw)
        if status:                                 # ignore non-status cells (spec paths, etc.)
            out[icm] = status
    return out


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


def acquire_token():
    """Fetch an ADO bearer token via the Azure CLI. Returns the token string or None.

    `az` is a .cmd shim on Windows, so it must be invoked through the shell. The resource is the
    well-known Azure DevOps application id (constant across tenants)."""
    import subprocess
    ado_app_id = "499b84ac-1321-427f-aa17-267ca6975798"  # public Azure DevOps resource id (not a secret)
    try:
        r = subprocess.run(
            f'az account get-access-token --resource {ado_app_id} --query accessToken -o tsv',
            shell=True, capture_output=True, text=True)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"  ! token acquisition error: {e}\n")
        return None
    tok = (r.stdout or "").strip()
    return tok if r.returncode == 0 and tok else None


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
    ap.add_argument("--map", default=None,
                    help="JSON/CSV mapping IcM id -> ADO work-item id. If omitted, auto-discovers "
                         "'work-item-map.json' (then '.csv') next to the CSV.")
    ap.add_argument("--token", default=None, help="File with an ADO bearer token (enables live status)")
    ap.add_argument("--tracker", default=None,
                    help="EXECUTION-TRACKER.md to read exec status from (branch/PR/merge state + intern "
                         "out-of-scope). Takes precedence over live ADO state. If omitted, auto-discovers "
                         "'EXECUTION-TRACKER.md' next to the CSV.")
    ap.add_argument("--auto-token", action="store_true",
                    help="Acquire an ADO bearer token automatically via the Azure CLI "
                         "(az account get-access-token) so no token file is needed. Requires `az` "
                         "logged in. This makes the weekly run a single command.")
    ap.add_argument("--out", default=None, help="Write HTML here as UTF-8 (recommended)")
    ap.add_argument("--window", default="", help="Header window label")
    ap.add_argument("--title", default="Security Triage — Weekly Status")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.csv_file, encoding="utf-8")))

    # Map: explicit --map, else auto-discover a persisted map next to the CSV.
    map_path = args.map
    if not map_path:
        here = os.path.dirname(os.path.abspath(args.csv_file))
        for cand in ("work-item-map.json", "work-item-map.csv"):
            if os.path.isfile(os.path.join(here, cand)):
                map_path = os.path.join(here, cand)
                print(f"  (using discovered map: {cand})")
                break
    wmap = load_map(map_path)
    # also accept a work_item column directly on the CSV
    for r in rows:
        wid = (r.get("work_item") or "").strip().lstrip("AB#").strip()
        if r.get("id") and wid and str(r["id"]).strip() not in wmap:
            wmap[str(r["id"]).strip()] = int(wid)

    # Execution tracker: explicit --tracker, else auto-discover EXECUTION-TRACKER.md next to the CSV.
    # The tracker reflects what has ACTUALLY been done (branch/PR/merge) and marks intern items out of
    # scope — it takes precedence over live ADO state for the Status column.
    tracker_path = args.tracker
    if not tracker_path:
        here = os.path.dirname(os.path.abspath(args.csv_file))
        cand = os.path.join(here, "EXECUTION-TRACKER.md")
        if os.path.isfile(cand):
            tracker_path = cand
            print("  (using discovered execution tracker: EXECUTION-TRACKER.md)")
    tracker = load_tracker(tracker_path)

    token = None
    if args.token and os.path.isfile(args.token):
        token = open(args.token, encoding="utf-8").read().strip()
    elif args.auto_token:
        token = acquire_token()
        if not token:
            sys.stderr.write("  ! --auto-token failed (is `az` logged in?). Falling back to no live status.\n")

    items = []
    for r in rows:
        icm = (r.get("id") or "").strip()
        tier = (r.get("our_tier") or "").strip()
        tier_key = next((k for k in SEV_ORDER if k in tier.lower()), "moderate")
        work_id = wmap.get(icm)
        status, updated = "Not started", ""
        # Precedence: execution tracker (source of truth for done-ness) > live ADO state > csv > default.
        if icm in tracker:
            status = tracker[icm]
            if work_id and token:                       # still fetch the changed-date for the Updated col
                _state, updated, _tags = fetch_ado(work_id, token)
        elif work_id and token:
            state, updated, tags = fetch_ado(work_id, token)
            status = map_ado_state(state, tags)
        elif (r.get("status") or "").strip():
            status = r["status"].strip()
        items.append({
            "icm": icm,
            "bug": (r.get("title") or "").strip(),
            "tier": tier or "—",
            "tier_key": tier_key,
            "status": status,
            "work_id": work_id,
            "updated": updated,
        })

    items.sort(key=lambda x: (STATUS_ORDER.get(x["status"], 9), SEV_ORDER.get(x["tier_key"], 9)))

    from collections import Counter
    sc = Counter(i["status"] for i in items)
    count_line = f'{len(items)} findings · ' + ' · '.join(
        f'{sc[s]} {s.lower()}' for s in sorted(sc, key=lambda s: STATUS_ORDER.get(s, 9)))
    oos = sc.get("Out of scope", 0)
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
<th {th}>IcM</th><th {th}>Bug</th><th {th}>Sev</th>
<th {th}>Status</th><th {th}>Work Item</th><th {th}>Updated</th>
</tr></thead><tbody>{''.join(trs)}</tbody></table>
{f'<div style="font-size:11px;color:#5b6470;margin-top:8px"><strong>Out of scope</strong> ({oos}): intern-eligible items are out of scope for now — assigned to an intern who has not started yet. They are tracked for completeness and will move to In progress once the intern picks them up.</div>' if oos else ''}
<div style="font-size:11px;color:#94a3b8;margin-top:8px">Generated {generated} · high-level status only (owner &amp; details live on the work item; evidence in the research report).</div>
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
