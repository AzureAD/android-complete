#!/usr/bin/env python3
"""Roll up per-finding classifications into an aggregate WBR/on-call summary.

Repeatable helper for the `vuln-triage-reporter` skill. Reads a simple CSV/TSV of finding classifications
and emits: counts, filed-vs-ours severity breakdown, confidence breakdown, the Gate 0 disposition split
(Already-Covered / Fixed-Since-Filed / Not-Fixable / Kept), total estimated eng-days, and a compact table
suitable for a shared WBR section.

`build_master_report.py` writes `classifications.csv` next to the HTML report, so this normally runs with
no manual step. `confidence` is optional; the disposition is taken from the `assignment`/`disposition`
column when present and otherwise derived from `our_tier`:
    id,tag,component,filed_tier,our_tier,icm_sev,verdict,confidence,assignment,eng_days,title
    NNNNNN,ITD,Authenticator,IMPORTANT,Moderate,Sev3,DOWN-CLASSIFY,High,Keep,2,<short vuln class>
    ...

Usage:
    python rollup.py classifications.csv --out _ROLLUP.md
    python rollup.py classifications.csv --window "2026-06-02 -> 2026-06-09" --out _ROLLUP.md

ALWAYS pass --out (writes UTF-8 directly). Do NOT use PowerShell `>` redirection — it re-encodes
stdout through the console code page and corrupts the Unicode (· → ┬╖, — → ΓÇö).
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


def derive_assignment(our_tier, icm_sev="", component="", assignment=""):
    """Gate 0 disposition. Prefers an explicit `assignment`/`disposition` column when the CSV carries one
    (build_master_report.py emits it), falling back to the tier text for hand-written CSVs. Returns:
    'Won't-Fix (Already-Covered)' | "Won't-Fix (Fixed-Since-Filed)" | 'Not-Fixable (By-Design)' | 'Keep'."""
    hay = ((assignment or "") + " " + (our_tier or "")).strip().lower()
    if ("fixed-since-filed" in hay) or ("fixed since filed" in hay):
        return "Won't-Fix (Fixed-Since-Filed)"
    if ("not-fixable" in hay) or ("not fixable" in hay) or ("by-design" in hay) or ("by design" in hay):
        return "Not-Fixable (By-Design)"
    if ("already-covered" in hay) or ("already covered" in hay) or ("won't" in hay) or ("wont" in hay):
        return "Won't-Fix (Already-Covered)"
    return "Keep"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_file", help="CSV of classifications (see module docstring)")
    ap.add_argument("--window", default=None, help="Window label for the header, e.g. '2026-06-02 -> 2026-06-09'")
    ap.add_argument("--out", default=None,
                    help="Write the roll-up markdown to this path as UTF-8 (recommended). "
                         "Avoids PowerShell '>' redirection mojibake. If omitted, prints to stdout.")
    args = ap.parse_args()

    buf = []

    def emit(line=""):
        buf.append(line)

    rows = []
    with open(args.csv_file, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    if not rows:
        emit("_No classifications to roll up._")
        _flush(buf, args.out)
        return 0

    n = len(rows)
    # Normalize the Gate 0 disposition so the roll-up is the single source of truth, preferring an
    # explicit column when present and falling back to the tier text otherwise.
    for r in rows:
        r["assignment"] = derive_assignment(
            r.get("our_tier", ""), r.get("icm_sev", ""), r.get("component", ""),
            r.get("assignment", "") or r.get("disposition", ""))

    by_tag = Counter(r.get("tag", "?").strip().upper() for r in rows)
    filed = Counter(r.get("filed_tier", "?").strip() for r in rows)
    ours = Counter(r.get("our_tier", "?").strip() for r in rows)
    verdicts = Counter(r.get("verdict", "?").strip().upper() for r in rows)
    confidence = Counter((r.get("confidence", "") or "").strip().upper() for r in rows if (r.get("confidence", "") or "").strip())
    assignment = Counter(r.get("assignment", "?").strip() for r in rows)
    icm_sev = Counter((r.get("icm_sev", "") or "").strip() for r in rows if (r.get("icm_sev", "") or "").strip())
    ext_needed = [r for r in rows
                  if (r.get("external_validation", r.get("ext_validation", "")) or "").strip().lower()
                  in ("1", "true", "yes", "y")]
    total_days = 0.0
    for r in rows:
        try:
            total_days += float(r.get("eng_days", 0) or 0)
        except ValueError:
            pass

    hdr = "# MSRC/ITD Triage Roll-Up"
    if args.window:
        hdr += f"  ({args.window})"
    emit(hdr + "\n")

    emit(f"- **Findings:** {n}  "
         + " · ".join(f"{k}: {v}" for k, v in by_tag.most_common()))
    emit("- **Verdicts:** " + " · ".join(f"{k}: {v}" for k, v in verdicts.most_common()))
    if confidence:
        conf_str = " · ".join(f"{k.title()}: {v}" for k, v in
                              sorted(confidence.items(), key=lambda kv: CONF_ORDER.get(kv[0], 9)))
        emit(f"- **Confidence:** {conf_str}")
    emit("- **Disposition:** " + " · ".join(f"{k}: {v}" for k, v in assignment.most_common()))
    if ext_needed:
        emit(f"- **Needs external validation:** {len(ext_needed)} "
             f"_(verdict leans on a server/downstream control we can't statically prove — confirm before closing)_")
    emit(f"- **Estimated eng-days (sum):** {total_days:g}  _(ESTIMATE — adjust)_\n")

    def fmt(counter):
        return " · ".join(
            f"{k}: {v}" for k, v in sorted(counter.items(), key=lambda kv: TIER_ORDER.get(kv[0], 9))
        )

    emit(f"- **Severity (filed):** {fmt(filed)}")
    emit(f"- **Severity (ours):**  {fmt(ours)}")
    if icm_sev:
        sev_order = {"Sev2": 0, "Sev2.5": 1, "Sev3": 2, "Sev4": 3}
        sev_str = " · ".join(f"{k}: {v}" for k, v in
                             sorted(icm_sev.items(), key=lambda kv: sev_order.get(kv[0], 9)))
        emit(f"- **IcM Sev (urgency):** {sev_str}")
        high = sum(v for k, v in icm_sev.items() if k in ("Sev2", "Sev2.5"))
        if high:
            emit(f"\n> ⚠️ {high} finding(s) at **Sev2.5+** — confirm each meets the high bar "
                 f"(High confidence · proven reachable · no safeguard · not boundary-dependent).")

    downs = verdicts.get("DOWN-CLASSIFY", 0)
    ups = verdicts.get("UP-CLASSIFY", 0)
    if downs or ups:
        emit(f"\n> Net re-classification: {downs} down, {ups} up vs. filed — "
             f"each backed by cited code evidence.\n")

    low_conf = [r for r in rows if (r.get("confidence", "") or "").strip().upper() == "LOW"]
    if low_conf:
        emit(f"> ⚠️ **{len(low_conf)} Low-confidence finding(s)** need a human review before action: "
             + ", ".join(str(r.get("id", "?")) for r in low_conf) + "\n")

    def print_table(subset):
        emit("| IcM | Tag | Component | Filed | Ours | Sev | Verdict | Conf | Eng-days | Title |")
        emit("|-----|-----|-----------|-------|------|-----|---------|------|----------|-------|")
        for r in sorted(subset, key=lambda x: TIER_ORDER.get(x.get("our_tier", "").strip(), 9)):
            title = (r.get("title", "") or "").replace("|", "\\|")
            if len(title) > 55:
                title = title[:52] + "..."
            emit(f"| {r.get('id','')} | {r.get('tag','')} | {r.get('component','')} | "
                 f"{r.get('filed_tier','')} | {r.get('our_tier','')} | "
                 f"{(r.get('icm_sev','') or '').strip() or '—'} | {r.get('verdict','')} | "
                 f"{(r.get('confidence','') or '').strip() or '—'} | "
                 f"{r.get('eng_days','')} | {title} |")

    def _asn(r):
        return r.get("assignment", "").strip().lower()

    covered = [r for r in rows if "already-covered" in _asn(r)]
    fixed_since = [r for r in rows if "fixed-since-filed" in _asn(r)]
    not_fixable = [r for r in rows if "not-fixable" in _asn(r)]
    keep = [r for r in rows if _asn(r) == "keep"]

    eng_days_keep = sum(float(r.get("eng_days", 0) or 0) for r in keep if (r.get("eng_days") or "").strip())

    emit(f"\n## ✅ Already Covered / Won't-Fix  ·  {len(covered)} finding(s)  ·  0 eng-days\n")
    emit("> Already neutralized by existing defense-in-depth (an upstream allow-list/validator, flight "
         "default, signature/package check, non-exported component, server-side number-match, …) — **we ship "
         "nothing.** Each row must cite the covering control. This is the safest outcome: the change we don't "
         "make can't regress a >1B-user library. Recommend these to their IcMs as Won't-Fix / down-classify.\n")
    if covered:
        print_table(covered)
    else:
        emit("_None this period._")

    emit(f"\n## 🕒 Fixed Since Filed  ·  {len(fixed_since)} finding(s)  ·  0 eng-days\n")
    emit("> Accurate when filed; the control shipped **after** the filing date. Close these, but answer the "
         "release-exposure question first: if a **shipped** release lacked the control, a customer/SIR "
         "response may be owed. Do not report these as 'never a bug'.\n")
    if fixed_since:
        print_table(fixed_since)
    else:
        emit("_None this period._")

    emit(f"\n## 🚫 Not-Fixable (By-Design)  ·  {len(not_fixable)} finding(s)  ·  0 eng-days\n")
    emit("> A real weakness that **no client-side change can close** — normally an OAuth public-client or "
         "platform constraint. Each row must cite the standard (RFC) and the compensating control we "
         "implement instead. Ask the security team to withdraw the sub-claim.\n")
    if not_fixable:
        print_table(not_fixable)
    else:
        emit("_None this period._")

    emit(f"\n## Kept — needs remediation  ·  {len(keep)} finding(s), ~{eng_days_keep:g} eng-days\n")
    if keep:
        print_table(keep)
        low_in_queue = [r for r in keep if (r.get("confidence", "") or "").strip().upper() == "LOW"]
        if low_in_queue:
            emit(f"\n> Note: {len(low_in_queue)} kept finding(s) are Low confidence — "
                 f"human review before action: "
                 + ", ".join(str(r.get("id", "?")) for r in low_in_queue))
    else:
        emit("_None._")

    emit("\n> The four sections above ARE the action split: **Already Covered**, **Fixed Since Filed** and "
         "**Not-Fixable** all close out (no change shipped, but each needs a *different* reply to the "
         "security team), while **Kept** = we own it and solution it. External-validation "
         "gating (server/downstream we can't statically verify) is flagged per-finding in the HTML report's "
         "⚗ signal.")

    _flush(buf, args.out)
    return 0


def _flush(buf, out_path):
    text = "\n".join(buf) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  + {out_path}")
    else:
        print(text)


if __name__ == "__main__":
    sys.exit(main())
