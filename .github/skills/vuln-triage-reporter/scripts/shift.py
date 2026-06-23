#!/usr/bin/env python3
"""On-call shift windowing + finding manifest for the `vuln-triage-reporter` skill.

On-call runs **Wednesday -> Wednesday**. This helper computes the current (or a given) shift window,
names the shift folder deterministically, and maintains a per-shift `manifest.json` so re-runs **append**
new findings instead of overwriting — and already-triaged IcMs are **skipped** (dedup).

Everything lives in the PRIVATE workspace ($VULN_TRIAGE_WORKSPACE, default ~/vuln-triage-workspace),
OUT OF the repo, because findings are sensitive.

Folder layout this enforces:
    <workspace>/msrc/<YYYY-MM-DD_to_YYYY-MM-DD>/      <- one folder per Wed->Wed shift
        manifest.json                                 <- {icm: {first_seen, slug, tag}}
        itd-investigations/  findings/  research/  agent-specs/  wbr-security-report.html  _ROLLUP.md

Subcommands:
    window     Print the shift window (start/end/slug/label/dir) as JSON. Use --date to pick the shift
               containing a given day, or --start/--end for an explicit window.
    ensure     Create the shift dir (+ empty manifest) if missing; print its path.
    check      Given an IcM id, print NEW or SEEN (and exit 0=NEW, 3=SEEN) — drives append/dedup.
    add        Record an IcM in the manifest (first_seen=now) with its slug/tag.
    list       Print the manifest entries.

Examples:
    python shift.py window                       # current Wed->Wed shift
    python shift.py window --date 2026-06-20
    python shift.py ensure                       # make the folder + manifest
    python shift.py check 31000000XXXXXX         # already triaged this shift?
    python shift.py add 31000000XXXXXX --slug 9-data-exposure-auth-app --tag MSRC
    python shift.py list
"""
import argparse
import json
import os
import sys
from datetime import datetime, date, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

WORKSPACE = os.environ.get("VULN_TRIAGE_WORKSPACE") or os.path.join(
    os.path.expanduser("~"), "vuln-triage-workspace")
MSRC_ROOT = os.path.join(WORKSPACE, "msrc")
WEDNESDAY = 2  # Monday=0 .. Wednesday=2


def shift_window(today=None, start=None, end=None):
    """Return (start_date, end_date) for the Wed->Wed shift. Explicit start/end win; else the shift that
    CONTAINS `today` (most recent Wednesday on/before today .. +7 days)."""
    if start and end:
        return date.fromisoformat(start), date.fromisoformat(end)
    d = date.fromisoformat(today) if today else date.today()
    back = (d.weekday() - WEDNESDAY) % 7        # days since the most recent Wednesday
    s = d - timedelta(days=back)
    return s, s + timedelta(days=7)


def slug_for(s, e):
    return f"{s.isoformat()}_to_{e.isoformat()}"


def label_for(s, e):
    return f"Wed {s.isoformat()} -> Wed {e.isoformat()}"


def shift_dir(s, e):
    return os.path.join(MSRC_ROOT, slug_for(s, e))


def manifest_path(s, e):
    return os.path.join(shift_dir(s, e), "manifest.json")


def load_manifest(s, e):
    p = manifest_path(s, e)
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_manifest(s, e, data):
    os.makedirs(shift_dir(s, e), exist_ok=True)
    with open(manifest_path(s, e), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def window_info(s, e):
    return {
        "start": s.isoformat(),
        "end": e.isoformat(),
        "slug": slug_for(s, e),
        "label": label_for(s, e),
        "dir": shift_dir(s, e),
        "manifest": manifest_path(s, e),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_window_args(p):
        p.add_argument("--date", help="A day inside the desired shift (YYYY-MM-DD); default today")
        p.add_argument("--start", help="Explicit shift start (YYYY-MM-DD); use with --end")
        p.add_argument("--end", help="Explicit shift end (YYYY-MM-DD); use with --start")

    p_win = sub.add_parser("window", help="Print shift window as JSON")
    add_window_args(p_win)

    p_ens = sub.add_parser("ensure", help="Create the shift dir (+ empty manifest) if missing")
    add_window_args(p_ens)

    p_chk = sub.add_parser("check", help="Is this IcM already in the shift manifest?")
    p_chk.add_argument("icm")
    add_window_args(p_chk)

    p_add = sub.add_parser("add", help="Record an IcM in the manifest (first_seen=now)")
    p_add.add_argument("icm")
    p_add.add_argument("--slug", default="", help="Finding slug, e.g. 9-data-exposure-auth-app")
    p_add.add_argument("--tag", default="", help="MSRC or ITD")
    add_window_args(p_add)

    p_lst = sub.add_parser("list", help="Print the manifest entries")
    add_window_args(p_lst)

    args = ap.parse_args()
    s, e = shift_window(getattr(args, "date", None), getattr(args, "start", None), getattr(args, "end", None))

    if args.cmd == "window":
        print(json.dumps(window_info(s, e), indent=2))
        return 0

    if args.cmd == "ensure":
        os.makedirs(shift_dir(s, e), exist_ok=True)
        if not os.path.isfile(manifest_path(s, e)):
            save_manifest(s, e, {})
        print(shift_dir(s, e))
        return 0

    if args.cmd == "check":
        m = load_manifest(s, e)
        entry = m.get(str(args.icm))
        if entry:
            print(f"SEEN: {args.icm} first_seen={entry.get('first_seen','?')} "
                  f"slug={entry.get('slug','?')}")
            return 3
        print(f"NEW: {args.icm} (not in shift {slug_for(s, e)})")
        return 0

    if args.cmd == "add":
        m = load_manifest(s, e)
        if str(args.icm) in m:
            print(f"= already present: {args.icm}")
            return 0
        m[str(args.icm)] = {
            "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "slug": args.slug,
            "tag": args.tag,
        }
        save_manifest(s, e, m)
        print(f"+ added {args.icm} -> {manifest_path(s, e)}")
        return 0

    if args.cmd == "list":
        m = load_manifest(s, e)
        print(f"# Shift {slug_for(s, e)} — {len(m)} finding(s)")
        for icm, meta in sorted(m.items(), key=lambda kv: kv[1].get("first_seen", "")):
            print(f"  {icm} | {meta.get('tag','?'):4} | {meta.get('first_seen','?')} | {meta.get('slug','')}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
