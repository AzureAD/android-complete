#!/usr/bin/env python3
"""Transcribe a saved FireWatch finding into a triage-ready Markdown summary.

Repeatable helper for the `vuln-triage-reporter` skill. Parses the saved-page HTML (stdlib only, no deps)
and emits a structured summary: classification metadata, affected code locations, and the suggested fix.

PoC-bearing sections (Exploitation Scenario, Sample Exploit, Step-by-Step) are **excluded** from output by
design — we keep engineering-triage detail only.

Usage:
    # Point at a finding folder (auto-finds the saved files)
    python transcribe_finding.py <finding_folder>

    # Or at the report-content.html directly
    python transcribe_finding.py "<folder>/Finding Detail - FireWatch Partner Portal_files/report-content.html"

Writes the summary to stdout. Redirect/append into the folder README.md as needed.
"""
import argparse
import glob
import os
import re
import sys
from html.parser import HTMLParser

# Force UTF-8 stdout so Unicode in reports (arrows, en-dashes) doesn't crash on Windows cp1252 consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

EXCLUDE_SECTIONS = {  # section headings whose bodies we drop (PoC / exploit detail)
    "exploitation scenario", "step-by-step attack", "sample exploit", "variants", "prerequisites",
    "source-to-sink call trace",
}


class TextExtractor(HTMLParser):
    """Collect (tag, text) and table rows in document order."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self._buf = []
        self._tag = None
        self._row = []
        self._cell = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3"):
            self._flush_text()
            self._tag = tag
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3"):
            text = "".join(self._buf).strip()
            if text:
                self.parts.append(("heading", tag, text))
            self._buf = []
            self._tag = None
        elif tag in ("td", "th"):
            self._row.append("".join(self._cell).strip())
            self._in_cell = False
        elif tag == "tr":
            if self._row:
                self.parts.append(("row", None, list(self._row)))
            self._row = []

    def handle_data(self, data):
        if self._tag:
            self._buf.append(data)
        elif self._in_cell:
            self._cell.append(data)

    def _flush_text(self):
        self._buf = []


def _find_files(target):
    """Return (wrapper_html, report_html) given a folder or a direct file path."""
    if os.path.isfile(target):
        if target.endswith("report-content.html"):
            return None, target
        return target, None
    # folder: locate saved files
    wrapper = None
    report = None
    for p in glob.glob(os.path.join(target, "**", "*.html"), recursive=True):
        base = os.path.basename(p).lower()
        if base == "report-content.html":
            report = p
        elif base.startswith("finding detail") and "_files" not in p.lower().replace(target.lower(), ""):
            wrapper = p
    # fallback: any html that isn't report-content as wrapper
    if wrapper is None:
        for p in glob.glob(os.path.join(target, "*.html")):
            if not p.endswith("report-content.html"):
                wrapper = p
                break
    return wrapper, report


def parse_kv_table(parts):
    """Extract metadata rows that look like Field|Value pairs."""
    kv = {}
    for kind, _, payload in parts:
        if kind == "row" and len(payload) == 2:
            k, v = payload
            if k and v and len(k) < 40:
                kv[k.rstrip(":")] = v
    return kv


def parse_code_locations(parts):
    """Extract the 'Affected Code Locations' style table (#, File, Line(s), Role)."""
    rows = []
    for kind, _, payload in parts:
        if kind == "row" and len(payload) >= 3:
            # heuristic: a cell containing a path
            if any("/" in c and ("." in c) for c in payload):
                rows.append(payload)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="Finding folder or report-content.html path")
    args = ap.parse_args()

    wrapper, report = _find_files(args.target)
    if not report:
        print(f"ERROR: could not find report-content.html under {args.target!r}", file=sys.stderr)
        print("Ensure the page was saved as 'Web Page, Complete' (the _files/ folder is required).",
              file=sys.stderr)
        return 2

    with open(report, encoding="utf-8", errors="replace") as f:
        rp = TextExtractor()
        rp.feed(f.read())

    title = next((t for k, _, t in ((p[0], p[1], p[2]) for p in rp.parts)
                  if k == "heading" and _ == "h1"), "(title not found)")
    meta = parse_kv_table(rp.parts)
    locs = parse_code_locations(rp.parts)

    wrap_kv = {}
    if wrapper and os.path.isfile(wrapper):
        with open(wrapper, encoding="utf-8", errors="replace") as f:
            wp = TextExtractor()
            wp.feed(f.read())
        wrap_kv = parse_kv_table(wp.parts)

    def g(*keys):
        for src in (meta, wrap_kv):
            for k in keys:
                for kk, vv in src.items():
                    if kk.lower() == k.lower():
                        return vv
        return "—"

    print(f"# {title}\n")
    print("## Filed Classification (from FireWatch)\n")
    print("| Field | Value |")
    print("|-------|-------|")
    for label, keys in [
        ("Finding ID", ("Finding ID",)),
        ("Source", ("Source", "Pipeline source")),
        ("Severity", ("Severity",)),
        ("Exploitability Tier", ("Exploitability Tier",)),
        ("Exploitable", ("Exploitable",)),
        ("Vulnerability Class", ("Vulnerability Class", "Vulnerability Type")),
        ("CWE", ("CWE",)),
        ("CVSS 3.1 (Estimated)", ("CVSS 3.1 (Estimated)", "CVSS")),
        ("Attack Vector", ("Attack Vector",)),
        ("Repository", ("Repository",)),
        ("Validation", ("Validated By", "Verdict")),
    ]:
        val = g(*keys).replace("|", "\\|")
        if val and val != "—":
            print(f"| {label} | {val} |")

    if locs:
        print("\n## Affected Code Locations\n")
        print("| File | Line(s) | Role |")
        print("|------|---------|------|")
        for row in locs:
            # try to find the path cell + a line-range cell
            path = next((c for c in row if "/" in c), row[0])
            lines = next((c for c in row if re.search(r"\d+\D+\d+|\d{2,}", c) and c != path), "")
            role = row[-1] if row[-1] not in (path, lines) else ""
            print(f"| `{path}` | {lines} | {role.replace('|', ' ')[:120]} |")

    # Suggested fix paragraph, if present
    fix = None
    capture = False
    for kind, lvl, payload in rp.parts:
        if kind == "heading":
            capture = "fix" in payload.lower() or "suggested fix" in payload.lower()
    # The fix often lives in a <p> with bold 'Suggested Fix'; the extractor merged it into headings/rows
    # only if structured. Leave a prompt for the agent to fill from the report if not auto-found.
    print("\n## Suggested Fix (verbatim from report)\n")
    print("_Copy the 'Suggested Fix' paragraph from report-content.html here._\n")

    print("\n---\n")
    print("## OUR Classification (to be completed by codebase-researcher)\n")
    print("- **Verdict:** AGREE | DOWN-CLASSIFY | UP-CLASSIFY")
    print("- **Our tier:** <CRITICAL | Important | Moderate | Low>")
    print("- **Defense-in-depth sweep:** _run codebase-researcher; cite file:line for every control_")
    print("- **Justification:** _anchored to cited evidence_")
    return 0


if __name__ == "__main__":
    sys.exit(main())
