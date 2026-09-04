#!/usr/bin/env python3
"""Verify that a triage run actually WROTE its artifacts — and print where they are.

Why this exists
---------------
Real user feedback: a full triage session ran to a verdict, told the engineer the answer in chat,
and produced **no files at all** — the engineer went looking in `~/vuln-triage-workspace` and found
nothing. That is a silent failure: the chat transcript is not a deliverable, and it disappears.

This script is the closing gate of every run. It checks the shift folder for the artifacts the
workflow promises, prints their **absolute paths** (plus a clickable file:// URL for the folder),
and exits non-zero if the required ones are missing so the agent cannot claim "done" without them.

Required (a run is NOT complete without these):
    manifest.json                      the shift's dedup/append record
    <finding markdown>                 >=1 per-finding report (findings/*.md or *.md in the run dir)
    wbr-security-report.html           the master report
    parseable stat-tile fields         every finding must yield Component / Confidence / Verdict /
                                       IcM Severity / Bottom line / Ours-tier, and no rendered tile may
                                       be blank. A free-handed report that skips the `**Label:**` parser
                                       contract still reads fine as prose but publishes empty tiles and a
                                       mislabelled master row — this check makes that impossible to ship.

Recommended (warn only — some are opt-in or depend on the mode):
    (warn) research/index.html   agent-specs/*.agent.md   _ROLLUP.md   classifications.csv
    (fail) no leftover "TODO" placeholders — a scaffolded-but-unfilled report must never publish

Usage:
    python verify_outputs.py                      # verify the current Wed->Wed shift
    python verify_outputs.py --date 2026-06-20    # a shift containing a given day
    python verify_outputs.py --dir <path>         # an explicit run folder
    python verify_outputs.py --expect 3           # also assert at least 3 finding reports
    python verify_outputs.py --warn-only          # report but always exit 0
"""
import argparse
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shift import shift_window, shift_dir, slug_for  # noqa: E402

OK = "PASS"
NO = "FAIL"
WARN = "WARN"


def _find(run_dir, *patterns):
    """Return sorted absolute paths matching any of the given globs, relative to run_dir."""
    hits = []
    for pat in patterns:
        hits.extend(glob.glob(os.path.join(run_dir, pat)))
    return sorted({os.path.abspath(h) for h in hits})


def _row(status, label, paths_or_note):
    marker = {OK: "[PASS]", NO: "[FAIL]", WARN: "[WARN]"}[status]
    print(f"  {marker} {label}")
    if isinstance(paths_or_note, str):
        print(f"         {paths_or_note}")
    else:
        for p in paths_or_note[:8]:
            print(f"         {p}")
        if len(paths_or_note) > 8:
            print(f"         ... and {len(paths_or_note) - 8} more")


def verify(run_dir, expect=0):
    """Return (required_failures, warnings). Prints a human-readable report."""
    run_dir = os.path.abspath(run_dir)
    print(f"\nRun folder: {run_dir}")
    print(f"Open it:    file:///{run_dir.replace(os.sep, '/')}\n")

    if not os.path.isdir(run_dir):
        _row(NO, "run folder exists", "the folder was never created — the run wrote nothing")
        print("\nNothing was produced. Re-run the workflow and do not skip artifact generation.")
        return 1, 0

    failures = 0
    warnings = 0

    manifest = _find(run_dir, "manifest.json")
    if manifest:
        _row(OK, "manifest.json (dedup/append record)", manifest)
    else:
        _row(NO, "manifest.json (dedup/append record)",
             "missing — run: python scripts/shift.py ensure, then shift.py add <icm> per finding")
        failures += 1

    findings = _find(run_dir, "findings/*.md", "msrc-investigations/*.md", "*.md")
    findings = [f for f in findings if os.path.basename(f) != "_ROLLUP.md"]
    if findings:
        _row(OK, f"per-finding report(s) — {len(findings)} found", findings)
    else:
        _row(NO, "per-finding report(s)",
             "missing — EVERY finding gets a written report, including Won't-Fix / out-of-scope verdicts")
        failures += 1

    master = _find(run_dir, "wbr-security-report.html")
    if master:
        _row(OK, "master report (wbr-security-report.html)", master)
    else:
        _row(NO, "master report (wbr-security-report.html)",
             "missing — run: python scripts/build_master_report.py --out <run_dir> ...")
        failures += 1

    research = _find(run_dir, "research/index.html")
    if research:
        _row(OK, "research subpages (research/index.html)", research)
    else:
        _row(WARN, "research subpages (research/index.html)",
             "not generated — run: python scripts/build_research_pages.py")
        warnings += 1

    specs = _find(run_dir, "agent-specs/*.agent.md")
    if specs:
        _row(OK, f"agent dispatch spec(s) — {len(specs)} found", specs)
    else:
        _row(WARN, "agent dispatch spec(s) (agent-specs/*.agent.md)",
             "not generated — expected for kept findings (build_agent_spec.py)")
        warnings += 1

    for label, pattern, hint in (
        ("roll-up (_ROLLUP.md)", "_ROLLUP.md", "run: python scripts/rollup.py"),
        ("classifications.csv", "classifications.csv", "emitted alongside the roll-up"),
    ):
        hit = _find(run_dir, pattern)
        if hit:
            _row(OK, label, hit)
        else:
            _row(WARN, label, f"not generated — {hint}")
            warnings += 1

    if expect and len(findings) < expect:
        _row(NO, f"expected at least {expect} finding report(s)",
             f"only {len(findings)} present — some findings were investigated but never written up")
        failures += 1

    # --- Structure gate: the parser contract that drives the tiles -----------
    # A report can exist, look fine as prose, and still render every stat tile as "—" because a
    # `**Label:**` line was missing or misspelled. That shipped once. Check it mechanically.
    if findings:
        f2, w2 = _verify_parseable(findings, run_dir)
        failures += f2
        warnings += w2

    return failures, warnings


# Fields that MUST parse out of every finding, because each one drives a stat tile and/or the
# master-report row. Keys are as normalised by build_research_pages.parse_meta().
REQUIRED_META = [
    ("component", "Component / Repo tile + intern-eligibility cutoff"),
    ("confidence", "Confidence tile"),
    ("verdict", "Verdict-vs-filed tile"),
    ("icm severity", "IcM Severity tile"),
    ("bottom line", "the one-sentence TL;DR at the top of the HTML"),
]


def _verify_parseable(findings, run_dir):
    """Fail when a finding's parser contract is broken — i.e. the HTML would render blank tiles.

    This is the mechanical guard for a real shipped failure: a free-handed report whose Severity /
    Confidence / Verdict tiles were all empty, and whose master row mislabelled an MSRC as an ITD.
    """
    failures = warnings = 0
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import build_research_pages as brp
    except ImportError:
        _row(WARN, "stat-tile parser check", "build_research_pages.py not importable — skipped")
        return 0, 1

    for path in findings:
        name = os.path.basename(path)
        try:
            md = open(path, encoding="utf-8").read()
        except OSError as exc:
            _row(NO, f"read {name}", str(exc))
            failures += 1
            continue

        meta = brp.parse_meta(md)
        missing = [f"{k} ({why})" for k, why in REQUIRED_META if not brp._clean(meta.get(k, ""))]
        if not brp._clean(meta.get("our_tier", "")):
            missing.append("Ours row in the Classification table (Our Severity tile)")

        if missing:
            _row(NO, f"stat-tile fields parse — {name}",
                 ["these would render as an empty '—' tile:"] + missing
                 + ["fix: follow references/report-template.md; then python scripts/lint_finding.py"])
            failures += 1
        else:
            _row(OK, f"stat-tile fields parse — {name}",
                 f"tier={brp._clean(meta.get('our_tier'))} · "
                 f"{brp._clean(meta.get('icm severity'))} · "
                 f"conf={brp._clean(meta.get('confidence'))} · "
                 f"verdict={brp._clean(meta.get('verdict'))}")

        # Leftover scaffold placeholders mean the report was generated but never filled in. This is a
        # REQUIRED failure, not a warning: publishing a shift report full of "TODO" is worse than
        # publishing nothing, because the master table renders it as a real, actioned finding.
        n_todo = len(re.findall(r"\bTODO\b", md))
        if n_todo:
            _row(NO, f"unfilled placeholders — {name}",
                 [f"{n_todo} 'TODO' left — this report was scaffolded but never completed",
                  "fill it in, or delete it and remove the IcM from manifest.json"])
            failures += 1

    # Rendered HTML must not contain a blank tile either (catches generator drift).
    for html in _find(run_dir, "research/*.html"):
        if os.path.basename(html) == "index.html":
            continue
        try:
            body = open(html, encoding="utf-8").read()
        except OSError:
            continue
        blanks = re.findall(r'<div class="lbl">([^<]+)</div><div class="val">\s*—\s*</div>', body)
        if blanks:
            _row(NO, f"rendered tiles — {os.path.basename(html)}",
                 [f"blank tile(s): {', '.join(blanks)}", "regenerate after fixing the markdown fields"])
            failures += 1

    return failures, warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="Explicit run folder (default: the current shift folder)")
    ap.add_argument("--date", help="A day inside the desired shift (YYYY-MM-DD); default today")
    ap.add_argument("--start", help="Explicit shift start (YYYY-MM-DD); use with --end")
    ap.add_argument("--end", help="Explicit shift end (YYYY-MM-DD); use with --start")
    ap.add_argument("--expect", type=int, default=0,
                    help="Assert at least N per-finding reports exist")
    ap.add_argument("--warn-only", action="store_true", help="Always exit 0 (report only)")
    args = ap.parse_args()

    if args.dir:
        run_dir = args.dir
        header = "explicit run folder"
    else:
        s, e = shift_window(args.date, args.start, args.end)
        run_dir = shift_dir(s, e)
        header = f"shift {slug_for(s, e)}"

    print(f"Verifying triage outputs for {header}")
    failures, warnings = verify(run_dir, args.expect)

    print()
    if failures:
        print(f"FAIL: {failures} required artifact(s) missing, {warnings} warning(s).")
        print("The run is NOT complete. Generate the missing artifacts, then tell the user the")
        print("absolute folder path above — never report a verdict that exists only in chat.")
        return 0 if args.warn_only else 1

    if warnings:
        print(f"PASS (with {warnings} warning(s)): required artifacts are on disk.")
    else:
        print("PASS: all expected artifacts are on disk.")
    print("Give the user the run-folder path above so they can open the report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
