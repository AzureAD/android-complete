#!/usr/bin/env python3
"""Structural linter for a finding report — catches missing research-discipline artifacts.

Why this exists
---------------
A real run produced a confident, well-cited, WRONG verdict: the adversarial pass challenged a reworded
version of the claim, framed around a co-resident subsystem that had no data path to the sink, and the
"refutation" retired a higher-severity argument that was never actually tested.

Nothing mechanical can judge whether reasoning is sound. But a great deal of that failure was
*structurally* visible — no scope contract, claims not carried verbatim, no channel tags — and this
linter makes those omissions impossible to ship silently.

What it checks (per finding markdown):
    [required] ## Scope Contract       with IN SCOPE / OUT OF SCOPE and an entry point
    [required] ## Claim Ledger         with >=1 claim row, each tagged with a channel/subsystem
    [required] a verdict/severity line and the Searches Run audit trail
    [warn]     SDL/MSRC bug bar factor table (the reviewer-favorite artifact)
    [warn]     VOID refutations noted, scope amendments re-evaluated
    [cond]     ## Fix Verification      4-cell flag ON/OFF matrix — required once a fix is claimed

Usage:
    python lint_finding.py <file.md> [more.md ...]
    python lint_finding.py --dir <run_dir>        # lint every finding md in a run folder
    python lint_finding.py --dir <run_dir> --strict   # warnings also fail
"""
import argparse
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

REQ = "REQUIRED"
WARN = "WARN"

# Words that indicate a claim row names which subsystem/channel it belongs to.
CHANNEL_HINT = re.compile(
    r"channel|subsystem|module|ipc|sso|broker|common|msal|adal|authenticator|app|library|"
    r"provider|service|domain",
    re.IGNORECASE)

FIX_CLAIMED = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:fix approach|chosen option|implemented|"
    r"remediation (?:executed|complete)|pr (?:opened|link))\b", re.IGNORECASE | re.MULTILINE)

MATRIX_CELLS = re.compile(r"flight\s*(?:=|:)?\s*(on|off)|\|\s*[ABCD]\s*\|", re.IGNORECASE)


def has_heading(text, *names):
    for n in names:
        if re.search(rf"^#{{1,4}}\s*.*{re.escape(n)}", text, re.IGNORECASE | re.MULTILINE):
            return True
    return False


def section(text, name):
    """Return the body of the first heading matching `name`, up to the next heading of same/higher level."""
    m = re.search(rf"^(#{{1,4}})\s*.*{re.escape(name)}.*$", text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return ""
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s", rest, re.MULTILINE)
    return rest[:nxt.start()] if nxt else rest


def table_rows(body):
    """Data rows of a markdown table (skip header + separator)."""
    rows = []
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|") or s.count("|") < 3:
            continue
        if re.match(r"^\|[\s:\-|]+\|$", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and re.match(r"^(id|#|cell|factor)$", cells[0], re.IGNORECASE):
            continue
        rows.append(cells)
    return rows


def lint(path):
    """Return list of (severity, message)."""
    issues = []
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        return [(REQ, f"cannot read file: {exc}")]

    # --- Scope Contract -----------------------------------------------------
    if not has_heading(text, "Scope Contract"):
        issues.append((REQ, "missing '## Scope Contract' — the trust boundary must be written down "
                            "BEFORE analysis (see references/research-discipline.md)"))
    else:
        body = section(text, "Scope Contract")
        if not re.search(r"IN SCOPE", body, re.IGNORECASE):
            issues.append((REQ, "Scope Contract has no 'IN SCOPE' list"))
        if not re.search(r"OUT OF SCOPE", body, re.IGNORECASE):
            issues.append((REQ, "Scope Contract has no 'OUT OF SCOPE' list — naming the co-resident "
                                "subsystem you excluded is the whole point"))
        if not re.search(r"entry point", body, re.IGNORECASE):
            issues.append((WARN, "Scope Contract does not name an entry point"))
        if not re.search(r"asset", body, re.IGNORECASE):
            issues.append((WARN, "Scope Contract does not name the asset at risk"))

    # --- Claim Ledger -------------------------------------------------------
    if not has_heading(text, "Claim Ledger"):
        issues.append((REQ, "missing '## Claim Ledger' — claims must be carried across passes verbatim"))
    else:
        body = section(text, "Claim Ledger")
        rows = table_rows(body)
        if not rows:
            issues.append((REQ, "Claim Ledger has no claim rows"))
        for r in rows:
            cid = r[0] if r else "?"
            if len(r) < 3:
                issues.append((REQ, f"claim {cid}: row is missing columns "
                                    f"(need ID | claim | channel | evidence | pass2 | status)"))
                continue
            if not CHANNEL_HINT.search(r[2]):
                issues.append((REQ, f"claim {cid}: no channel/subsystem tag — an untagged claim cannot "
                                    f"be safely challenged (this is the exact real-run failure)"))
            if len(r) >= 2 and not re.search(r"[\"'\u201c\u201d]", r[1]):
                issues.append((WARN, f"claim {cid}: claim text is not quoted — quote it verbatim so "
                                     f"drift between passes is visible"))
        if re.search(r"\bVOID\b", body):
            issues.append((WARN, "ledger notes a VOID refutation — confirm the challenge was re-issued "
                                 "against the verbatim claim"))

    # --- Verdict + audit trail ---------------------------------------------
    if not re.search(r"our (tier|severity)|verdict|classification|won'?t[- ]fix|sev\s*[234]",
                     text, re.IGNORECASE):
        issues.append((REQ, "no verdict / severity call found"))
    if not has_heading(text, "Searches Run"):
        issues.append((REQ, "missing '## Searches Run (audit trail)' — the absence proofs behind every "
                            "'no mitigation found' claim"))

    # --- Reviewer-favorite artifacts ---------------------------------------
    if not re.search(r"bug bar", text, re.IGNORECASE):
        issues.append((WARN, "no SDL/MSRC bug bar factor table — reviewers specifically value the "
                             "factor-by-factor breakdown (vuln class, attack vector, privileges, "
                             "prerequisites, blast radius, CIA)"))
    if re.search(r"amend", text, re.IGNORECASE) and not re.search(
            r"re-?evaluat", text, re.IGNORECASE):
        issues.append((WARN, "a scope amendment is mentioned but no re-evaluation of affected claims"))

    # --- Fix verification (conditional) ------------------------------------
    if FIX_CLAIMED.search(text):
        if not has_heading(text, "Fix Verification"):
            issues.append((REQ, "a fix is described but there is no '## Fix Verification' section — "
                                "a fix needs the 4-cell flag ON/OFF matrix "
                                "(see references/flight-verification.md)"))
        else:
            body = section(text, "Fix Verification")
            rows = table_rows(body)
            if len(rows) < 4:
                issues.append((REQ, f"Fix Verification matrix has {len(rows)} row(s), needs 4 "
                                    f"(A: exploit/OFF, B: legit/OFF, C: exploit/ON, D: legit/ON)"))
            if not MATRIX_CELLS.search(body):
                issues.append((WARN, "Fix Verification does not clearly show both flight states"))
            if not re.search(r"not covered|gap", body, re.IGNORECASE):
                issues.append((WARN, "Fix Verification has no 'Not covered' line — name what remains "
                                     "unverified (OEMs, API levels, server state)"))
    return issues


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="Finding markdown file(s)")
    ap.add_argument("--dir", help="Lint every finding markdown in a run folder")
    ap.add_argument("--strict", action="store_true", help="Warnings fail too")
    args = ap.parse_args()

    targets = list(args.files)
    if args.dir:
        for pat in ("findings/*.md", "msrc-investigations/*.md", "*.md"):
            targets.extend(glob.glob(os.path.join(args.dir, pat)))
    targets = sorted({os.path.abspath(t) for t in targets
                      if os.path.basename(t) not in ("_ROLLUP.md",)})

    if not targets:
        print("No finding markdown files found to lint.")
        print("Usage: python lint_finding.py <file.md> | --dir <run_dir>")
        return 1

    total_req = total_warn = 0
    for t in targets:
        issues = lint(t)
        reqs = [i for i in issues if i[0] == REQ]
        warns = [i for i in issues if i[0] == WARN]
        total_req += len(reqs)
        total_warn += len(warns)
        status = "FAIL" if reqs else ("WARN" if warns else "PASS")
        print(f"\n[{status}] {t}")
        for sev, msg in reqs + warns:
            print(f"    [{sev}] {msg}")

    print(f"\n{len(targets)} file(s): {total_req} required issue(s), {total_warn} warning(s).")
    if total_req or (args.strict and total_warn):
        print("Report is not ready. Fix the items above — see references/research-discipline.md")
        print("and references/flight-verification.md.")
        return 1
    print("Structure OK. (This checks structure only — it cannot judge whether the reasoning is right.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
