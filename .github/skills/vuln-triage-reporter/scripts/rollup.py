#!/usr/bin/env python3
"""Roll up per-finding classifications into an aggregate WBR/on-call summary.

Repeatable helper for the `vuln-triage-reporter` skill. Reads a simple CSV/TSV of finding classifications
and emits: counts, filed-vs-ours severity breakdown, confidence breakdown, an Intern-Queue vs.
Engineer-owned split, total estimated eng-days, and a compact table suitable for a shared WBR section.

Input format (CSV, header row required). `confidence` is optional; `assignment` is always derived from the
cutoff (**Intern-eligible only when IcM Sev4 AND component is the Authenticator app; otherwise
Engineer-owned**), so `component` and `icm_sev` should be accurate:
    id,tag,component,filed_tier,our_tier,icm_sev,verdict,confidence,assignment,eng_days,title
    NNNNNN,ITD,Authenticator,IMPORTANT,Low,Sev4,DOWN-CLASSIFY,High,,2,<short vuln class>
    ...

Usage:
    python rollup.py classifications.csv
    python rollup.py classifications.csv --window "2026-06-02 -> 2026-06-09"
"""
import argparse
import csv
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

TIER_ORDER = {"CRITICAL": 0, "Important": 1, "Moderate": 2, "Low": 3, "Won't-Fix": 4}
CONF_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def canonical_repo(component):
    """Map a free-form component string to Authenticator | Common | Broker | MSAL | ADAL."""
    c = (component or "").split("·")[0].split("(")[0].strip().lower()
    if "authenticator" in c or "auth app" in c or c in ("auth", "auth-app"):
        return "Authenticator"
    for key in ("common", "broker", "msal", "adal"):
        if key in c:
            return key.upper() if key in ("msal", "adal") else key.capitalize()
    return (component or "").strip() or "—"


def derive_assignment(our_tier, icm_sev="", component=""):
    """Cutoff: Intern-eligible ONLY when IcM Sev4 AND repo is the Authenticator app. Else Engineer-owned."""
    sev = (icm_sev or "").replace(" ", "").lower()
    if sev == "sev4" and canonical_repo(component) == "Authenticator":
        return "Intern-eligible"
    return "Engineer-owned"


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
    # Always derive assignment from the cutoff (Sev4 + Authenticator → Intern; else Engineer)
    # so the roll-up is the single source of truth regardless of any stale CSV value.
    for r in rows:
        r["assignment"] = derive_assignment(
            r.get("our_tier", ""), r.get("icm_sev", ""), r.get("component", ""))

    by_tag = Counter(r.get("tag", "?").strip().upper() for r in rows)
    filed = Counter(r.get("filed_tier", "?").strip() for r in rows)
    ours = Counter(r.get("our_tier", "?").strip() for r in rows)
    verdicts = Counter(r.get("verdict", "?").strip().upper() for r in rows)
    confidence = Counter((r.get("confidence", "") or "").strip().upper() for r in rows if (r.get("confidence", "") or "").strip())
    assignment = Counter(r.get("assignment", "?").strip() for r in rows)
    icm_sev = Counter((r.get("icm_sev", "") or "").strip() for r in rows if (r.get("icm_sev", "") or "").strip())
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
    if confidence:
        conf_str = " · ".join(f"{k.title()}: {v}" for k, v in
                              sorted(confidence.items(), key=lambda kv: CONF_ORDER.get(kv[0], 9)))
        print(f"- **Confidence:** {conf_str}")
    print(f"- **Assignment:** " + " · ".join(f"{k}: {v}" for k, v in assignment.most_common()))
    print(f"- **Estimated eng-days (sum):** {total_days:g}  _(ESTIMATE — adjust)_\n")

    def fmt(counter):
        return " · ".join(
            f"{k}: {v}" for k, v in sorted(counter.items(), key=lambda kv: TIER_ORDER.get(kv[0], 9))
        )

    print(f"- **Severity (filed):** {fmt(filed)}")
    print(f"- **Severity (ours):**  {fmt(ours)}")
    if icm_sev:
        sev_order = {"Sev2": 0, "Sev2.5": 1, "Sev3": 2, "Sev4": 3}
        sev_str = " · ".join(f"{k}: {v}" for k, v in
                             sorted(icm_sev.items(), key=lambda kv: sev_order.get(kv[0], 9)))
        print(f"- **IcM Sev (urgency):** {sev_str}")
        high = sum(v for k, v in icm_sev.items() if k in ("Sev2", "Sev2.5"))
        if high:
            print(f"\n> ⚠️ {high} finding(s) at **Sev2.5+** — confirm each meets the high bar "
                  f"(High confidence · proven reachable · no safeguard · not boundary-dependent).")

    downs = verdicts.get("DOWN-CLASSIFY", 0)
    ups = verdicts.get("UP-CLASSIFY", 0)
    if downs or ups:
        print(f"\n> Net re-classification: {downs} down, {ups} up vs. filed — "
              f"each backed by cited code evidence.\n")

    low_conf = [r for r in rows if (r.get("confidence", "") or "").strip().upper() == "LOW"]
    if low_conf:
        print(f"> ⚠️ **{len(low_conf)} Low-confidence finding(s)** need a human review before action: "
              + ", ".join(str(r.get("id", "?")) for r in low_conf) + "\n")

    def print_table(subset):
        print("| IcM | Tag | Component | Filed | Ours | Sev | Verdict | Conf | Eng-days | Title |")
        print("|-----|-----|-----------|-------|------|-----|---------|------|----------|-------|")
        for r in sorted(subset, key=lambda x: TIER_ORDER.get(x.get("our_tier", "").strip(), 9)):
            title = (r.get("title", "") or "").replace("|", "\\|")
            if len(title) > 55:
                title = title[:52] + "..."
            print(f"| {r.get('id','')} | {r.get('tag','')} | {r.get('component','')} | "
                  f"{r.get('filed_tier','')} | {r.get('our_tier','')} | "
                  f"{(r.get('icm_sev','') or '').strip() or '—'} | {r.get('verdict','')} | "
                  f"{(r.get('confidence','') or '').strip() or '—'} | "
                  f"{r.get('eng_days','')} | {title} |")

    engineer = [r for r in rows if r.get("assignment", "").strip() == "Engineer-owned"]
    intern = [r for r in rows if r.get("assignment", "").strip() != "Engineer-owned"]

    eng_days_eng = sum(float(r.get("eng_days", 0) or 0) for r in engineer if (r.get("eng_days") or "").strip())
    eng_days_int = sum(float(r.get("eng_days", 0) or 0) for r in intern if (r.get("eng_days") or "").strip())

    print(f"\n## Engineer-owned (kept — needs remediation)  ·  {len(engineer)} finding(s), "
          f"~{eng_days_eng:g} eng-days\n")
    if engineer:
        print_table(engineer)
    else:
        print("_None._")

    print(f"\n## Intern Queue (Sev4 + Authenticator — delegatable)  ·  {len(intern)} finding(s), "
          f"~{eng_days_int:g} eng-days\n")
    if intern:
        print_table(intern)
        low_in_queue = [r for r in intern if (r.get("confidence", "") or "").strip().upper() == "LOW"]
        if low_in_queue:
            print(f"\n> Note: {len(low_in_queue)} intern-queue finding(s) are Low confidence — "
                  f"engineer sanity-check before handing off: "
                  + ", ".join(str(r.get("id", "?")) for r in low_in_queue))
    else:
        print("_None._")
    return 0


if __name__ == "__main__":
    sys.exit(main())
