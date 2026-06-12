#!/usr/bin/env python3
"""Roll up per-finding classifications into an aggregate WBR/on-call summary.

Repeatable helper for the `vuln-triage-reporter` skill. Reads a simple CSV/TSV of finding classifications
and emits: counts, filed-vs-ours severity breakdown, total estimated eng-days, and a compact table
suitable for a shared WBR section.

Input format (CSV, header row required):
    id,tag,component,filed_tier,our_tier,verdict,eng_days,title
    635989,ITD,Broker,IMPORTANT,Moderate,DOWN-CLASSIFY,2,app_link intent launch
    ...

Usage:
    python rollup.py classifications.csv
    python rollup.py classifications.csv --window "2026-06-02 -> 2026-06-09"
"""
import argparse
import csv
import sys
from collections import Counter

TIER_ORDER = {"CRITICAL": 0, "Important": 1, "Moderate": 2, "Low": 3, "Won't-Fix": 4}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_file", help="CSV of classifications (see module docstring)")
    ap.add_argument("--window", default=None, help="Window label for the header, e.g. '2026-06-02 -> 2026-06-09'")
    args = ap.parse_args()

    rows = []
    with open(args.csv_file, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    if not rows:
        print("_No classifications to roll up._")
        return 0

    n = len(rows)
    by_tag = Counter(r.get("tag", "?").strip().upper() for r in rows)
    filed = Counter(r.get("filed_tier", "?").strip() for r in rows)
    ours = Counter(r.get("our_tier", "?").strip() for r in rows)
    verdicts = Counter(r.get("verdict", "?").strip().upper() for r in rows)
    total_days = 0.0
    for r in rows:
        try:
            total_days += float(r.get("eng_days", 0) or 0)
        except ValueError:
            pass

    hdr = "# MSRC/ITD Triage Roll-Up"
    if args.window:
        hdr += f"  ({args.window})"
    print(hdr + "\n")

    print(f"- **Findings:** {n}  "
          + " · ".join(f"{k}: {v}" for k, v in by_tag.most_common()))
    print(f"- **Verdicts:** " + " · ".join(f"{k}: {v}" for k, v in verdicts.most_common()))
    print(f"- **Estimated eng-days (sum):** {total_days:g}  _(ESTIMATE — adjust)_\n")

    def fmt(counter):
        return " · ".join(
            f"{k}: {v}" for k, v in sorted(counter.items(), key=lambda kv: TIER_ORDER.get(kv[0], 9))
        )

    print(f"- **Severity (filed):** {fmt(filed)}")
    print(f"- **Severity (ours):**  {fmt(ours)}")

    downs = verdicts.get("DOWN-CLASSIFY", 0)
    ups = verdicts.get("UP-CLASSIFY", 0)
    if downs or ups:
        print(f"\n> Net re-classification: {downs} down, {ups} up vs. filed — "
              f"each backed by cited code evidence.\n")

    print("\n## Findings\n")
    print("| IcM | Tag | Component | Filed | Ours | Verdict | Eng-days | Title |")
    print("|-----|-----|-----------|-------|------|---------|----------|-------|")
    for r in sorted(rows, key=lambda x: TIER_ORDER.get(x.get("our_tier", "").strip(), 9)):
        title = (r.get("title", "") or "").replace("|", "\\|")
        if len(title) > 70:
            title = title[:67] + "..."
        print(f"| {r.get('id','')} | {r.get('tag','')} | {r.get('component','')} | "
              f"{r.get('filed_tier','')} | {r.get('our_tier','')} | {r.get('verdict','')} | "
              f"{r.get('eng_days','')} | {title} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
