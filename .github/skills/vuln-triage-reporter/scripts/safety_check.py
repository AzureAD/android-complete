#!/usr/bin/env python3
"""Public-repo safety scanner for the `vuln-triage-reporter` skill.

This skill lives in a PUBLIC GitHub-mirrored repo. Run this BEFORE every commit that touches the skill.
It scans the skill's tracked/modified files for genuinely-sensitive content that must never be published,
while NOT flagging opaque routing identifiers (team IDs, service-tree GUIDs, codenames) that are inert
without corp access.

Exit code 0 = clean (safe to commit). Non-zero = findings present; DO NOT COMMIT until resolved.

Usage:
    python safety_check.py                 # scan the skill dir (default)
    python safety_check.py <path...>       # scan specific files/dirs
    python safety_check.py --all-tracked   # also warn if investigation outputs are tracked by git
"""
import argparse
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

SKILL_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# (label, compiled regex, why). These are GENUINELY sensitive — actionable by an outsider with no access.
RULES = [
    ("telemetry-sampling",
     re.compile(r"traceIdRatioBased|DefaultAndroidBrokerTelemetry|head[- ]?sampl\w*\s*(rate|at)\s*\d|"
                r"\b\d{1,3}\s*%\s*(of\s+)?(events|auth|traffic|coverage|sampled)|"
                r"coverage\s*[:=]\s*~?\d{1,3}\s*%", re.IGNORECASE),
     "telemetry sampling rate / coverage percentage (evasion map)"),
    ("security-control-logic",
     re.compile(r"SKIP_SILENT_IN_INTERACTIVE|"
                r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}\b(?=.*\b(flight|bypass|skip|nonce|silent|"
                r"interactive|validate\w*\s+when)\b)"),
     "possible internal security-control flight/constant + bypass logic"),
    ("private-file-citation",
     re.compile(r"\b\w+\.(java|kt)#L\d+"),
     "real file:line citation into private code (use placeholders in skill docs)"),
    ("internal-host",
     re.compile(r"azurefd\.net|firewatch-pilot|\bame\.gbl\b|msftcloudes|vnext\.s360", re.IGNORECASE),
     "internal hostname / portal URL"),
    ("pii-identity",
     re.compile(r"@microsoft\.com|@ame\.gbl|[a-z0-9]+@[a-z0-9.\-]+\.(onmicrosoft|com)\b", re.IGNORECASE),
     "email / alias / UPN (possible PII)"),
    ("tenant-or-finding-guid-with-content",
     re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
     "raw GUID (tenant/finding) — confirm it is not a finding GUID paired with content"),
    ("long-icm-number",
     re.compile(r"\b\d{11,}\b"),
     "long numeric ID resembling a real IcM number (use NNNNNN in examples)"),
]

# Lines that legitimately mention forbidden terms because they are the RULES/banner themselves.
ALLOW_CONTEXT = re.compile(
    r"sampling rate|coverage percentage|evasion map|forbidden|NEVER put here|do\s+not\s+commit|"
    r"bypass/skip|placeholder|safety[_ ]check|these are an|exact flight names|internal hostnames|"
    r"tenant GUIDs|UPNs|aliases|flight constant|finding content paired|use placeholders|"
    r"flight constant names", re.IGNORECASE)

SCAN_EXT = (".md", ".py", ".txt", ".json", ".yml", ".yaml", ".html")


def iter_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    if f.endswith(SCAN_EXT):
                        yield os.path.join(root, f)


def scan_file(path):
    hits = []
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return hits
    for n, line in enumerate(lines, 1):
        if ALLOW_CONTEXT.search(line):
            continue
        for label, rx, why in RULES:
            if rx.search(line):
                hits.append((n, label, why, line.strip()[:160]))
    return hits


def check_tracked_outputs():
    """Warn if any investigation output is tracked by git (it must live outside the repo)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", ".github/skills/vuln-triage-reporter/", "**/local-context/**"],
            capture_output=True, text=True, cwd=os.path.join(SKILL_DIR, "..", "..", ".."))
        tracked = [l for l in out.stdout.splitlines()
                   if "local-context" in l or "itd-investigations" in l or "wbr-security-report" in l]
        return tracked
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=None, help="Files/dirs to scan (default: the skill dir)")
    ap.add_argument("--all-tracked", action="store_true",
                    help="Also warn if investigation outputs are tracked by git")
    args = ap.parse_args()

    paths = args.paths or [SKILL_DIR]
    total = 0
    for f in sorted(set(iter_files(paths))):
        # never scan this scanner's own rule table
        if os.path.basename(f) == "safety_check.py":
            continue
        for n, label, why, snippet in scan_file(f):
            total += 1
            rel = os.path.relpath(f)
            print(f"  [{label}] {rel}:{n}\n      {why}\n      > {snippet}")

    if args.all_tracked:
        tracked = check_tracked_outputs()
        if tracked:
            print("\n  [tracked-output] investigation outputs are tracked by git (must be OUT of repo):")
            for t in tracked:
                print(f"      {t}")
                total += 1

    print()
    if total:
        print(f"FAIL: {total} potential issue(s). DO NOT COMMIT until each is removed or genericized.")
        print("(Opaque team IDs / service-tree GUIDs / codenames are allowed and are not flagged.)")
        return 1
    print("PASS: no sensitive content detected in scanned skill files. Safe to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
