#!/usr/bin/env python3
"""Scaffold one investigation folder per FireWatch/ITD finding.

Repeatable helper for the `vuln-triage-reporter` skill. Creates `<n>-<vulntype>-<component>/README.md`
placeholders under the itd-investigations root, ready for the user to drop saved FireWatch HTML into.

Usage:
    # From a simple spec file (one finding per line: "vulntype|component")
    python scaffold_itd.py --spec findings.txt

    # Or inline
    python scaffold_itd.py --finding "Authorization|Broker" --finding "CSRF|Auth App"

    # Custom root (default: $VULN_TRIAGE_WORKSPACE/msrc/itd-investigations, an OUT-OF-REPO dir)
    python scaffold_itd.py --spec findings.txt --root path/to/itd-investigations

Folder names are slugified: "Authorization|Broker" -> "1-authorization-broker".
Existing folders are left untouched (idempotent).
"""
import argparse
import os
import re
import sys

# Sensitive artifacts MUST live OUTSIDE the repo (this repo is mirrored to public GitHub).
# Default to the private workspace: $VULN_TRIAGE_WORKSPACE, else ~/vuln-triage-workspace.
WORKSPACE = os.environ.get("VULN_TRIAGE_WORKSPACE") or os.path.join(os.path.expanduser("~"), "vuln-triage-workspace")
DEFAULT_ROOT = os.path.join(WORKSPACE, "msrc", "itd-investigations")

README_TEMPLATE = """\
# ITD — {vulntype} ({component})

**FireWatch source:** Glasswing
**Component:** {component}
**Vulnerability type:** {vulntype}

> Save the FireWatch finding page here via **Save Page As -> "Web Page, Complete"**.
> Required: `Finding Detail - FireWatch Partner Portal_files/report-content.html` (the full report).
> The agent transcribes it via `scripts/transcribe_finding.py`.

## Classification
_pending — awaiting saved HTML_

## Linked IcM
_to be confirmed_
"""


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def parse_specs(args):
    specs = []
    if args.spec:
        with open(args.spec, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                specs.append(line)
    specs.extend(args.finding or [])
    out = []
    for s in specs:
        if "|" not in s:
            print(f"  ! skipping malformed spec (need 'vulntype|component'): {s!r}", file=sys.stderr)
            continue
        vt, comp = (p.strip() for p in s.split("|", 1))
        out.append((vt, comp))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", help="File with one 'vulntype|component' per line")
    ap.add_argument("--finding", action="append", help="Inline 'vulntype|component' (repeatable)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help=f"Root dir (default: {DEFAULT_ROOT})")
    ap.add_argument("--start-index", type=int, default=1, help="Starting folder number (default: 1)")
    args = ap.parse_args()

    specs = parse_specs(args)
    if not specs:
        ap.error("no findings provided (use --spec or --finding)")

    os.makedirs(args.root, exist_ok=True)
    created, skipped = 0, 0
    for i, (vt, comp) in enumerate(specs, start=args.start_index):
        folder = os.path.join(args.root, f"{i}-{slug(vt)}-{slug(comp)}")
        readme = os.path.join(folder, "README.md")
        if os.path.exists(readme):
            print(f"  = exists: {folder}")
            skipped += 1
            continue
        os.makedirs(folder, exist_ok=True)
        with open(readme, "w", encoding="utf-8") as f:
            f.write(README_TEMPLATE.format(vulntype=vt, component=comp))
        print(f"  + created: {folder}")
        created += 1

    print(f"\nDone. {created} created, {skipped} existing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
