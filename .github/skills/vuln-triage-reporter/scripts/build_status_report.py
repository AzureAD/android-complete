#!/usr/bin/env python3
"""Build a concise, email-ready WEEKLY STATUS report from the triage classifications.

Part of the `vuln-triage-reporter` skill. This is the manager-tracking artifact — a single compact table
(IcM · Bug · Severity · Status · Code-complete ETA · Prod · Work Item · Updated). It is NOT the research report: no evidence,
no file:line, no audit trail. Output is self-contained HTML that pastes cleanly into Outlook.

Status vocabulary (manager-friendly): Not started · In progress · In testing · Blocked · In review · Complete.

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
  # one-command weekly run (auto-discovered map + auto token + fixed milestone dates):
  python build_status_report.py classifications.csv --auto-token \
      --out weekly-status.html --window "2026-06-18 -> 2026-06-25" \
      --code-complete-date 7/8/26 --prod-date-app 8/10/26 --prod-date-lib 7/13/26

  # explicit map + token file:
  python build_status_report.py classifications.csv --map map.json --token tok.txt \
      --out weekly-status.html --window "2026-06-18 -> 2026-06-25"

Milestone dates: pass --code-complete-date (one date, same for all — shown once in the header, no per-row
column), and --prod-date-app / --prod-date-lib for the per-component Prod (100%) milestone. When omitted, the
script falls back to estimating Prod from rollout days.
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
STATUS_ORDER = {"Blocked": 0, "In progress": 1, "In testing": 2, "In review": 3, "Not started": 4,
                "Complete": 5, "Out of scope": 6}
STATUS_COLOR = {
    "Blocked": ("#fde8e8", "#b91c1c"),
    "In progress": ("#e0f2fe", "#075985"),
    "In testing": ("#fef3c7", "#92400e"),
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
    Tracker statuses: NOT STARTED · IN PROGRESS · IMPLEMENTED (local) · PUSHED (no PR) · PR DRAFT ·
    PR OPEN · MERGED · BLOCKED · DROPPED / ALREADY-COVERED / WON'T-FIX · OUT OF SCOPE (intern)."""
    s = (text or "").strip().lower()
    if "out of scope" in s:
        return "Out of scope"
    # Closed-out with no shipping change: dropped because already covered by defense-in-depth / won't-fix.
    if "dropped" in s or "already" in s or "won't" in s or "wont" in s:
        return "Out of scope"
    if "blocked" in s:
        return "Blocked"
    if "merged" in s:
        return "Complete"
    # A DRAFT PR is code-complete + locally tested but NOT verified/merged and NOT yet in formal review —
    # surface it as "In testing". Checked before "pr open" so "PR OPEN (draft)" / "PR DRAFT" land here.
    if "draft" in s or "in testing" in s or "verifying" in s:
        return "In testing"
    if "pr open" in s:
        return "In review"
    if "in progress" in s or "implemented" in s or "pushed" in s:
        return "In progress"
    if "not started" in s:
        return "Not started"
    return None


def parse_tracker_detail(raw):
    """From a tracker 'Exec status' cell, extract (pr_label, pr_url, note).
    - pr_label/pr_url: a GitHub PR (#NNNN) or ADO PR (!NNNN) reference, linked when a URL is derivable.
    - note: a short reason phrase for non-shipping outcomes (dropped / already-covered / won't-fix),
      else "".
    Returns ("", "", "") when nothing notable is present."""
    text = re.sub(r"[`*]", "", raw or "").strip()
    pr_label, pr_url, note = "", "", ""
    # GitHub PR: "PR #3170"  (common/msal repos — link resolved by repo hint if present, else left unlinked)
    m_gh = re.search(r"\bPR\s*#(\d+)\b", text)
    m_ado = re.search(r"!(\d{6,})\b", text)  # ADO PR like "!16213879"
    if m_gh:
        pr_label = f"PR #{m_gh.group(1)}"
    elif m_ado:
        pr_label = f"PR !{m_ado.group(1)}"
        pr_url = ("https://msazure.visualstudio.com/DefaultCollection/One/_git/"
                  "AD-MFA-phonefactor-phoneApp-android/pullrequest/" + m_ado.group(1))
    low = text.lower()
    if "dropped" in low or "already" in low or "won't" in low or "wont" in low:
        # Keep the human phrase after a dash if present, else a default.
        after = re.split(r"[—-]", text, maxsplit=1)
        note = (after[1].strip() if len(after) > 1 and after[1].strip()
                else "Dropped — already covered by defense-in-depth")
    return pr_label, pr_url, note


def load_tracker(path):
    """Parse EXECUTION-TRACKER.md's 'Status at a glance' table -> {icm_id: {...}}.
    Each value is a dict: {status, pr_label, pr_url, note}. Reads each markdown table row, takes the long
    IcM number from any cell and the exec status from the LAST cell (strips **bold**/`code`/parenthetical
    detail). Rows whose last cell is NOT a recognized status (e.g. the bottom 'Out of scope' table whose
    last cell is a spec path) are skipped."""
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
            pr_label, pr_url, note = parse_tracker_detail(cells[-1])
            out[icm] = {"status": status, "pr_label": pr_label, "pr_url": pr_url, "note": note}
    return out


def github_pr_url(component, pr_number):
    """Map a finding's component to its public GitHub repo and build a PR URL. Returns "" if unknown
    (e.g. broker is GHE / authenticator is ADO — those PRs come through as ADO '!NNNN' already)."""
    c = (component or "").lower()
    repo = None
    if "common" in c:
        repo = "AzureAD/microsoft-authentication-library-common-for-android"
    elif "msal" in c:
        repo = "AzureAD/microsoft-authentication-library-for-android"
    elif "adal" in c:
        repo = "AzureAD/azure-activedirectory-library-for-android"
    if not repo:
        return ""
    return f"https://github.com/{repo}/pull/{pr_number}"


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


# --- ETA / Prod-rollout math -------------------------------------------------
# Rollout days = calendar days from CODE COMPLETE to full production (flag-on), derived from the
# Combined Android Release Checklist. Libraries (broker/common/msal/adal) publish to Maven Central in
# Phase 4 — they reach prod EARLIER. The Authenticator app runs Phase 5's gradual Prod ramp
# (5%->10%->25%->50%->100%, 2 days bake each, Mon-Wed approvals) + Phase 7 partner stores, and a feature
# flag only flips after 100% — so the app takes much longer. These are ESTIMATES (monthly train +
# ramp); override with --rollout-lib-days / --rollout-app-days.
ROLLOUT_APP_DAYS = 35   # Authenticator app: monthly CCD train + full gradual Prod ramp to 100% + flag-on
ROLLOUT_LIB_DAYS = 14   # broker/common/msal/adal libraries: Phase 4 Maven Central publish (earlier)

# How far the per-finding work is from code-complete, by status (fraction of remaining effort).
REMAINING_BY_STATUS = {
    "Not started": 1.0, "Blocked": 1.0, "In progress": 0.5, "In testing": 0.15,
    "In review": 0.0, "Complete": 0.0, "Out of scope": None,  # None => not applicable
}


def is_app_component(component):
    """Authenticator app -> True (slow rollout); broker/common/msal/adal libraries -> False (faster)."""
    c = (component or "").lower()
    return "authenticator" in c or "auth app" in c or c.strip() in ("auth", "auth-app", "app")


def add_business_days(start, n):
    """Add n business days (Mon-Fri) to a date."""
    d = start
    n = int(n)
    while n > 0:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def eta_dates(asof, eng_days, status, component, buffer_frac, rollout_app, rollout_lib):
    """Return (code_complete_str, prod_str). Dates are 'MM-DD'; '✓' = already there; '—' = N/A."""
    remaining = REMAINING_BY_STATUS.get(status, 1.0)
    if remaining is None:                          # Out of scope
        return "—", "—"
    rollout = rollout_app if is_app_component(component) else rollout_lib
    if status == "Complete":                       # merged → assume rolling/rolled out
        return "✓", "✓"
    try:
        ed = float(eng_days or 0)
    except (TypeError, ValueError):
        ed = 0.0
    # code-complete = today + (remaining effort × (1 + testing buffer)), in BUSINESS days
    work_days = ed * (1.0 + buffer_frac) * remaining
    import math
    cc_date = add_business_days(asof, math.ceil(work_days)) if work_days > 0 else asof
    cc_str = "✓" if remaining == 0.0 else cc_date.strftime("%m-%d")
    # prod = code-complete + rollout (CALENDAR days; bake time runs over weekends)
    prod_date = cc_date + datetime.timedelta(days=rollout)
    return cc_str, prod_date.strftime("%m-%d")


def parse_flex_date(s):
    """Parse a user-supplied date in ISO or US format (e.g. '2026-07-08', '7/8/26', '07/08/2026').
    Returns a date or None."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    sys.stderr.write(f"  ! could not parse date '{s}' — expected YYYY-MM-DD or M/D/YY\n")
    return None


def fmt_milestone(d):
    """Friendly milestone format, e.g. 'Jul 8, 2026'."""
    return f"{d:%b} {d.day}, {d.year}" if d else "—"


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
    ap.add_argument("--asof", default=None,
                    help="Anchor date for ETA math (YYYY-MM-DD); default today.")
    ap.add_argument("--test-buffer", type=float, default=0.5,
                    help="Testing buffer added to eng-days for the Code-complete ETA (fraction; "
                         "default 0.5 = +50%% for tests/flighting/review).")
    ap.add_argument("--rollout-app-days", type=int, default=ROLLOUT_APP_DAYS,
                    help=f"Calendar days from code-complete to Prod 100%% for the Authenticator app "
                         f"(default {ROLLOUT_APP_DAYS}: monthly train + gradual ramp + flag-on).")
    ap.add_argument("--rollout-lib-days", type=int, default=ROLLOUT_LIB_DAYS,
                    help=f"Calendar days from code-complete to Prod for broker/common/msal/adal libraries "
                         f"(default {ROLLOUT_LIB_DAYS}: Phase 4 Maven Central publish).")
    ap.add_argument("--code-complete-date", default=None,
                    help="Fixed code-complete milestone date (e.g. 2026-07-08 or 7/8/26). Same for all "
                         "findings — shown once in the header. When set, the per-row Code-complete column "
                         "is not computed.")
    ap.add_argument("--prod-date-app", default=None,
                    help="Fixed Prod (100%%) date for the Authenticator app (e.g. 8/10/26). Overrides the "
                         "computed app rollout estimate.")
    ap.add_argument("--prod-date-lib", default=None,
                    help="Fixed Prod date for broker/common/msal/adal libraries (e.g. 7/13/26). Overrides "
                         "the computed library rollout estimate.")
    args = ap.parse_args()

    asof = datetime.date.fromisoformat(args.asof) if args.asof else datetime.date.today()

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

    # Fixed milestone dates (user-supplied) override the computed ETA estimates.
    fixed_cc = parse_flex_date(args.code_complete_date)
    fixed_prod_app = parse_flex_date(args.prod_date_app)
    fixed_prod_lib = parse_flex_date(args.prod_date_lib)
    use_fixed_prod = bool(fixed_prod_app or fixed_prod_lib)

    items = []
    for r in rows:
        icm = (r.get("id") or "").strip()
        tier = (r.get("our_tier") or "").strip()
        tier_key = next((k for k in SEV_ORDER if k in tier.lower()), "moderate")
        work_id = wmap.get(icm)
        status, updated = "Not started", ""
        pr_label, pr_url, note = "", "", ""
        # Precedence: execution tracker (source of truth for done-ness) > live ADO state > csv > default.
        if icm in tracker:
            t = tracker[icm]
            status = t["status"]
            pr_label, pr_url, note = t["pr_label"], t["pr_url"], t["note"]
            # Resolve a GitHub PR link from the finding's component/repo when the tracker gave a "#NNNN".
            if pr_label.startswith("PR #") and not pr_url:
                pr_url = github_pr_url(r.get("component", ""), pr_label.split("#", 1)[1])
            if work_id and token:                       # still fetch the changed-date for the Updated col
                _state, updated, _tags = fetch_ado(work_id, token)
        elif work_id and token:
            state, updated, tags = fetch_ado(work_id, token)
            status = map_ado_state(state, tags)
        elif (r.get("status") or "").strip():
            status = r["status"].strip()
        # Prod date: fixed per-component milestone when supplied, else the computed rollout estimate.
        component = r.get("component", "")
        if status == "Out of scope":
            prod_eta = "—"
        elif status == "Complete":
            prod_eta = "✓"
        elif use_fixed_prod:
            fp = fixed_prod_app if is_app_component(component) else fixed_prod_lib
            prod_eta = fmt_milestone(fp) if fp else "—"
        else:
            _cc, prod_eta = eta_dates(
                asof, r.get("eng_days"), status, component,
                args.test_buffer, args.rollout_app_days, args.rollout_lib_days)
        items.append({
            "icm": icm,
            "bug": (r.get("title") or "").strip(),
            "tier": tier or "—",
            "tier_key": tier_key,
            "status": status,
            "work_id": work_id,
            "updated": updated,
            "prod_eta": prod_eta,
            "pr_label": pr_label,
            "pr_url": pr_url,
            "note": note,
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
        # PR cell: linked when we have a URL, plain label when not, dash otherwise.
        if i["pr_label"] and i["pr_url"]:
            pr_cell = (f'<a href="{htmllib.escape(i["pr_url"])}" style="color:#0f6cbd;text-decoration:none">'
                       f'{htmllib.escape(i["pr_label"])}</a>')
        elif i["pr_label"]:
            pr_cell = htmllib.escape(i["pr_label"])
        else:
            pr_cell = '<span style="color:#94a3b8">—</span>'
        bug_cell = htmllib.escape(i["bug"])
        td = 'style="padding:6px 10px;border-bottom:1px solid #e2e6ea;font-size:13px;vertical-align:top"'
        tdc = ('style="padding:6px 10px;border-bottom:1px solid #e2e6ea;font-size:13px;vertical-align:top;'
               'white-space:nowrap;color:#1a1a2e"')
        trs.append(
            "<tr>"
            f'<td {td}>{icm_link}</td>'
            f'<td {td}>{bug_cell}</td>'
            f'<td {td}>{chip(i["tier"], vbg, vfg)}</td>'
            f'<td {td}>{chip(i["status"], sbg, sfg)}</td>'
            f'<td {td}>{pr_cell}</td>'
            f'<td {tdc}>{htmllib.escape(i["prod_eta"])}</td>'
            f'<td {td}>{wi}</td>'
            f'<td {td} style="padding:6px 10px;border-bottom:1px solid #e2e6ea;font-size:13px;'
            f'color:#5b6470">{htmllib.escape(i["updated"]) or "—"}</td>'
            "</tr>")

    th = ('style="text-align:left;padding:6px 10px;border-bottom:2px solid #cbd5e1;font-size:11px;'
          'text-transform:uppercase;letter-spacing:.03em;color:#5b6470"')
    sub = (args.window + " · " if args.window else "") + count_line
    cc_header = (f'<div style="font-size:12px;color:#1a1a2e;margin:0 0 10px">'
                 f'<strong>Code complete (all):</strong> {htmllib.escape(fmt_milestone(fixed_cc))}</div>'
                 if fixed_cc else "")
    if use_fixed_prod:
        bits = []
        if fixed_prod_lib:
            bits.append(f"<strong>{htmllib.escape(fmt_milestone(fixed_prod_lib))}</strong> for "
                        f"broker/common/MSAL/ADAL libraries (Maven Central publish)")
        if fixed_prod_app:
            bits.append(f"<strong>{htmllib.escape(fmt_milestone(fixed_prod_app))}</strong> for the "
                        f"Authenticator app (gradual ramp, then feature-flag on)")
        prod_note = ("<strong>Prod (100%)</strong> = planned full production rollout milestone: "
                     + "; ".join(bits) + ".")
    else:
        prod_note = (f"<strong>Prod (100%)</strong> = projected full production rollout: "
                     f"<strong>~{args.rollout_lib_days}d</strong> after code-complete for "
                     f"broker/common/MSAL/ADAL libraries (publish to Maven Central), "
                     f"<strong>~{args.rollout_app_days}d</strong> for the Authenticator app (gradual ramp "
                     f"5%→10%→25%→50%→100% with 2-day bakes, then feature-flag on). Estimates — per the "
                     f"Combined Android Release Checklist.")
    html = f"""<div style="font-family:'Segoe UI',Arial,sans-serif;color:#1a1a2e;max-width:1120px">
<div style="font-size:16px;font-weight:700">{htmllib.escape(args.title)}</div>
<div style="font-size:12px;color:#5b6470;margin:2px 0 6px">{htmllib.escape(sub)}</div>
{cc_header}
<table style="border-collapse:collapse;width:100%">
<thead><tr>
<th {th}>IcM</th><th {th}>Bug</th><th {th}>Sev</th>
<th {th}>Status</th><th {th}>PR</th><th {th}>Prod&nbsp;(100%)</th>
<th {th}>Work Item</th><th {th}>Updated</th>
</tr></thead><tbody>{''.join(trs)}</tbody></table>
{f'<div style="font-size:11px;color:#5b6470;margin-top:8px"><strong>Out of scope</strong> ({oos}): intern-eligible items are out of scope for now — assigned to an intern who has not started yet. They are tracked for completeness and will move to In progress once the intern picks them up.</div>' if oos else ''}
<div style="font-size:11px;color:#5b6470;margin-top:8px">{prod_note}</div>
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
