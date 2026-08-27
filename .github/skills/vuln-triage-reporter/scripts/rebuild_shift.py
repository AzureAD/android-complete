#!/usr/bin/env python3
"""Rebuild ALL artifacts for an on-call shift — lint-gated, in the right order.

Why this exists
---------------
On-call receives findings **one at a time** across a Wed->Wed shift. Every time a new one is triaged,
the whole shift's outputs must be regenerated so the master report and roll-up include it — the master
report is built from *all* the finding markdowns, not appended to. Doing that by hand is four commands in
a specific order with easy-to-miss flags; skipping one silently leaves a stale report that omits the
newest finding.

It also runs `lint_finding.py` FIRST and refuses to build on a structural failure. That is deliberate:
a report that fails the structure gate produces blank stat tiles and a wrong master row, and that has
already shipped once.

Usage
-----
    python rebuild_shift.py                     # current Wed->Wed shift
    python rebuild_shift.py --date 2026-06-20   # a shift containing a given day
    python rebuild_shift.py --dir <path>        # an explicit run folder
    python rebuild_shift.py --skip-lint         # escape hatch (prints a loud warning)

Exit codes: 0 = everything built; 1 = lint failed (nothing built) or a build step failed.
"""
import argparse
import glob
import os
import subprocess
import sys

import shift

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))


def run(script, *cli_args):
    cmd = [sys.executable, os.path.join(HERE, script), *cli_args]
    print(f"\n$ {script} {' '.join(str(a) for a in cli_args)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    print("\n".join("  " + l for l in out.strip().splitlines()[-14:]))
    return proc.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="Explicit run folder (default: the current shift folder)")
    ap.add_argument("--date", help="A day inside the desired shift (YYYY-MM-DD); default today")
    ap.add_argument("--start", help="Explicit shift start (YYYY-MM-DD); use with --end")
    ap.add_argument("--end", help="Explicit shift end (YYYY-MM-DD); use with --start")
    ap.add_argument("--owner", help="On-call owner label for the shift header")
    ap.add_argument("--skip-lint", action="store_true",
                    help="Build even if the structure gate fails (NOT recommended)")
    args = ap.parse_args()

    s, e = shift.shift_window(args.date, args.start, args.end)
    run_dir = os.path.abspath(args.dir) if args.dir else shift.shift_dir(s, e)
    findings = sorted(glob.glob(os.path.join(run_dir, "findings", "*.md")))
    findings = [f for f in findings if os.path.basename(f) != "_ROLLUP.md"]

    print(f"Shift:   {shift.label_for(s, e)}")
    print(f"Folder:  {run_dir}")
    print(f"Findings: {len(findings)}")
    if not findings:
        print("\nNo finding markdown found. Scaffold one first:")
        print("  python new_finding.py --icm <id> --tag MSRC --component <repo> --title \"<title>\"")
        return 1
    for f in findings:
        print(f"  - {os.path.basename(f)}")

    # 1. Structure gate FIRST — never build on a malformed report.
    if args.skip_lint:
        print("\n!! --skip-lint: building WITHOUT the structure gate. Stat tiles may render blank.")
    else:
        if run("lint_finding.py", *findings) != 0:
            print("\nFAIL: the structure gate rejected at least one finding — nothing was rebuilt.")
            print("Fix the items above (they are what cause blank tiles / wrong master rows), then re-run.")
            return 1

    glob_arg = os.path.join(run_dir, "findings", "*.md")
    research_dir = os.path.join(run_dir, "research")
    specs_dir = os.path.join(run_dir, "agent-specs")

    rc = 0
    rc |= run("build_research_pages.py", glob_arg, "--out", research_dir,
              "--index", "--agent-dir", "../agent-specs")
    rc |= run("build_agent_spec.py", glob_arg, "--out", specs_dir)

    master = ["build_master_report.py", glob_arg, "--out", run_dir,
              "--research-dir", "research", "--agent-dir", "agent-specs",
              "--shift", shift.label_for(s, e),
              "--window", f"{s.isoformat()} -> {e.isoformat()}"]
    if args.owner:
        master += ["--owner", args.owner]
    rc |= run(*master)

    csv_path = os.path.join(run_dir, "classifications.csv")
    if os.path.isfile(csv_path):
        rc |= run("rollup.py", csv_path, "--window", shift.label_for(s, e),
                  "--out", os.path.join(run_dir, "_ROLLUP.md"))
    else:
        print(f"\n  (no classifications.csv — skipping roll-up; create it to get _ROLLUP.md)")

    print("\n" + "=" * 72)
    rc |= run("verify_outputs.py", "--dir", run_dir, "--expect", str(len(findings)))
    if rc != 0:
        print("\nFAIL: at least one step reported a problem — see above.")
        return 1
    print(f"\nOK: {len(findings)} finding(s) built for {shift.label_for(s, e)}.")
    print(f"Open: file:///{run_dir.replace(os.sep, '/')}/wbr-security-report.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
