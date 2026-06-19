#!/usr/bin/env python3
"""Generate a machine-readable, agent-actionable spec from a finding's README markdown.

Part of the `vuln-triage-reporter` skill. The per-finding README is the full human + agent investigation;
this script distills it into a compact `<name>.agent.md` that an AI coding agent (Copilot coding agent /
`pbi-creator`) can parse and act on to open a PR — WITHOUT scraping prose.

Output = YAML front-matter (structured fields) + a Dispatch Block (problem statement + acceptance criteria)
+ files-to-change + constraints + "do-not-proceed-until" gating pulled from the Verification Gaps section.

Repeatable: run it over the per-finding markdown after the reports are written.

Usage:
    python build_agent_spec.py <readme.md ...|glob> [--out DIR]
    # default: writes <slug>.agent.md next to each input
"""
import argparse
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

REPO_HINTS = [
    ("authenticator/", "authenticator"), ("PhoneFactor/", "authenticator"),
    ("broker/", "broker"), ("common/", "common"), ("adal/", "adal"), ("msal/", "msal"),
]


def section(md, heading):
    """Return the body text of a '## heading' section (until the next '## ')."""
    m = re.search(rf'^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)', md,
                  re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def find_section(md, *substrs):
    """Return body of the first '##' or '###' heading containing any of substrs (case-insensitive)."""
    for m in re.finditer(r'^#{2,3}\s+(.+?)\s*\n(.*?)(?=^#{2,3}\s|\Z)', md, re.MULTILINE | re.DOTALL):
        title = m.group(1).lower()
        if any(s.lower() in title for s in substrs):
            return m.group(2).strip()
    return ""


def field(md, label):
    m = re.search(rf'^\*\*{re.escape(label)}:\*\*\s*(.+)$', md, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def clean(v):
    if not v:
        return ""
    v = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', v)   # links -> text
    v = re.sub(r'`([^`]*)`', r'\1', v)                # de-code
    v = re.sub(r'\*\*([^*]+)\*\*', r'\1', v)          # de-bold
    v = re.sub(r'[*_]', '', v)
    v = re.sub(r'\s*[_(].*$', '', v).strip()          # trailing italic/paren note
    return v.split('·')[0].strip()


def light_clean(v):
    """De-markdown without truncating — for full sentences (gap questions, changes)."""
    if not v:
        return ""
    v = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', v)
    v = re.sub(r'`([^`]*)`', r'\1', v)
    v = re.sub(r'\*\*([^*]+)\*\*', r'\1', v)
    v = re.sub(r'(?<!\w)[*_](?!\w)', '', v)
    return v.strip()


def yaml_val(v):
    v = (v or "").replace('"', "'")
    return f'"{v}"' if v else '""'


def classification_cell(md, row_label, col):
    """Read a cell from the Classification table's Filed/Ours row (0-based col after the row label)."""
    for line in md.splitlines():
        if re.match(rf'\s*\|\s*\*?\*?{row_label}', line, re.IGNORECASE):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) > col:
                return clean(cells[col])
    return ""


def parse_files(md):
    """Pull (path#lines, change) from the 'Files to Change' table, else bullets from 'Fix Notes'."""
    files = []
    fc = find_section(md, "Files to Change")
    if fc:
        for line in fc.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) < 2 or set(cells[0]) <= set("-: "):
                continue
            if cells[0].lower().startswith("file"):
                continue
            path = cells[0]
            m = re.search(r'\[[^\]]+\]\(([^)]+)\)', path)        # prefer the link URL (full repo path)
            if m:
                path = re.sub(r'^\./', '', m.group(1))
            else:
                path = re.sub(r'`', '', path).strip()
            change = light_clean(cells[1])
            if path:
                files.append((path, change))
    else:  # Intern-eligible -> Fix Notes bullets that cite a file
        fn = find_section(md, "Fix Notes")
        for line in fn.splitlines():
            if re.match(r'\s*[-*]\s', line) and re.search(r'\.(java|kt|kts|xml)', line):
                m = re.search(r'\[[^\]]+\]\(([^)]+)\)', line)        # prefer link URL (full path)
                if m:
                    path = re.sub(r'^\./', '', m.group(1))
                else:
                    mc = re.search(r'`([^`]+\.(?:java|kt|kts|xml)[^`]*)`', line)
                    path = mc.group(1) if mc else light_clean(line.lstrip("-* ").split("—")[0])
                files.append((path, light_clean(line.lstrip("-* "))))
    return files


def infer_repos(files):
    repos = []
    for path, _ in files:
        for hint, repo in REPO_HINTS:
            if hint in path and repo not in repos:
                repos.append(repo)
    return repos


def parse_gaps(md):
    """Return the open-question (col 1) of each Verification Gaps table row."""
    gaps = []
    body = find_section(md, "Verification Gaps", "What We Need", "Open Questions")
    if not body:
        return gaps
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 2 or set("".join(cells[0])) <= set("-: ") or cells[0].lower() in ("#", ""):
            continue
        if len(cells) >= 2 and cells[1] and not cells[1].lower().startswith("open question"):
            gaps.append(light_clean(cells[1]))
    return gaps


def acceptance(md):
    """Pull the negative-test line(s) from the Test Plan as acceptance criteria."""
    body = find_section(md, "Test Plan")
    out = []
    for line in body.splitlines():
        s = line.strip().lstrip("-* ").strip()
        if re.match(r'\*\*?(Negative test|Unit|Regression)', s, re.IGNORECASE):
            out.append(clean(s))
    return out


def first_para(text):
    for para in re.split(r'\n\s*\n', text):
        p = para.strip()
        if p and not p.startswith(("|", ">", "-", "*", "#")):
            return light_clean(p)
    return light_clean(text.split("\n")[0]) if text else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="Finding README markdown file(s) or globs")
    ap.add_argument("--out", default=None, help="Output dir (default: next to each input)")
    args = ap.parse_args()

    files_in = []
    for inp in args.inputs:
        files_in.extend(glob.glob(inp, recursive=True) if any(c in inp for c in "*?[") else [inp])
    files_in = [f for f in files_in if f.endswith(".md") and not f.endswith(".agent.md")]

    if args.out:
        os.makedirs(args.out, exist_ok=True)

    for f in sorted(files_in):
        md = open(f, encoding="utf-8").read()
        title = next((l[2:].strip() for l in md.split("\n") if l.startswith("# ")), os.path.basename(f))

        head = md.split("##", 1)[0]
        fid = ""
        mfid = re.search(r'Linked IcM:\*\*\s*([0-9]+)', head) or re.search(r'\[(\d{6,})\]', title)
        if mfid:
            fid = mfid.group(1)
        tag = "ITD"
        if re.search(r'\bMSRC\b', title) and not re.search(r'\bITD\b', title):
            tag = "MSRC"
        fw = ""
        mfw = re.search(r'FireWatch:\*\*\s*`?([0-9a-f-]{8,})`?', head, re.IGNORECASE)
        if mfw:
            fw = mfw.group(1)
        component = clean(re.search(r'Component:\*\*\s*([^·\n]+)', head).group(1)) if re.search(
            r'Component:\*\*', head) else ""

        our_tier = classification_cell(md, "Ours", 2)
        filed_tier = classification_cell(md, "Filed", 2)
        cwe = ""
        mcwe = re.search(r'CWE-\d+', classification_cell(md, "Ours", 3) or classification_cell(md, "Filed", 3))
        if mcwe:
            cwe = mcwe.group(0)

        verdict = clean(field(md, "Verdict"))
        confidence = clean(field(md, "Confidence"))
        icm_sev = clean(field(md, "IcM Severity"))
        assignment = clean(field(md, "Assignment"))
        ext = field(md, "External validation")
        ext_yes = ext.lower().startswith("yes") if ext else bool(
            re.search(r'cannot (verify|conclude)|server-side|inferred', md, re.IGNORECASE))

        files_change = parse_files(md)
        repos = infer_repos(files_change)
        gaps = parse_gaps(md)
        accept = acceptance(md)

        root_cause = first_para(find_section(md, "Root Cause") or find_section(md, "The Vulnerability")
                                or section(md, "Description"))
        fix_approach = first_para(find_section(md, "Fix Approach") or find_section(md, "Fix Notes"))

        is_eng = "engineer" in assignment.lower()
        if not is_eng:
            status = "intern-queue"
        elif ext_yes and gaps:
            status = "ready-to-fix (severity pending external confirmation)"
        else:
            status = "ready-to-fix"

        # ---- build front-matter ----
        fm = ["---"]
        fm.append(f"finding_id: {yaml_val(fid)}")
        fm.append(f"tag: {tag}")
        fm.append(f"title: {yaml_val(title)}")
        fm.append(f"component: {yaml_val(component)}")
        fm.append(f"filed_tier: {yaml_val(filed_tier)}")
        fm.append(f"our_tier: {yaml_val(our_tier)}")
        fm.append(f"cwe: {yaml_val(cwe)}")
        fm.append(f"icm_sev: {yaml_val(icm_sev)}")
        fm.append(f"confidence: {yaml_val(confidence)}")
        fm.append(f"verdict: {yaml_val(verdict)}")
        fm.append(f"assignment: {yaml_val(assignment)}")
        fm.append(f"external_validation_needed: {'true' if ext_yes else 'false'}")
        fm.append(f"status: {yaml_val(status)}")
        fm.append("target_repos: [" + ", ".join(repos) + "]")
        if fw:
            fm.append(f"firewatch_id: {yaml_val(fw)}")
        fm.append("files_to_change:")
        for path, change in files_change:
            fm.append(f"  - path: {yaml_val(path)}")
            fm.append(f"    change: {yaml_val(change)}")
        fm.append("blocked_on:")
        for g in gaps:
            fm.append(f"  - {yaml_val(g)}")
        fm.append("---")

        # ---- build body ----
        b = [f"# Agent Dispatch Spec — {title}", ""]
        b.append("> Machine-actionable distillation of the investigation. Full human evidence + audit trail "
                 "live in the finding README. **No PoC payloads / PII** — implement the fix, do not reproduce "
                 "the exploit.")
        b.append("")
        b.append(f"**Status:** {status}  ·  **Our severity:** {our_tier} ({icm_sev})  ·  "
                 f"**Confidence:** {confidence}  ·  **Assignment:** {assignment}")
        b.append("")
        b.append("## Problem Statement")
        if root_cause:
            b.append(root_cause)
        if fix_approach:
            b.append("")
            b.append(f"**Fix approach:** {fix_approach}")
        b.append("")
        b.append("## Files to Change")
        if files_change:
            for path, change in files_change:
                b.append(f"- `{path}` — {change}")
        else:
            b.append("- _See README; no explicit file table parsed._")
        b.append("")
        b.append("## Acceptance Criteria")
        if accept:
            for a in accept:
                b.append(f"- [ ] {a}")
        b.append("- [ ] The previously-exploitable case is now blocked (add the negative test as the contract).")
        b.append("- [ ] Legitimate path still works (regression).")
        b.append("")
        if ext_yes or gaps:
            b.append("## ⚠️ Do NOT proceed past these without human/owner confirmation")
            b.append("These are unverifiable from code and may change the severity or the fix decision:")
            for g in gaps:
                b.append(f"- {g}")
            b.append("")
            b.append("> The **code fix** is generally safe to implement now (it is correct hardening); what is "
                     "gated is the **severity/priority decision**. Confirm the above before closing the IcM.")
            b.append("")
        b.append("## Constraints")
        b.append("- Public-repo-safe: no exploit detail, PoC, or PII in code, tests, or PR text.")
        b.append("- If the change touches `OneAuthSharedFunctions` or any Common/IPC surface consumed by 1P "
                 "apps, flag the breaking-change and notify the OneAuth team.")
        b.append("- Prefer reusing an existing hardened sibling control over inventing a new one (see README).")
        b.append("")
        b.append(f"_Source investigation: `{os.path.basename(f)}` (full evidence, defense-in-depth sweep, "
                 "adversarial verification, and searches-run audit)._")

        outname = re.sub(r'\.md$', '', os.path.basename(f)) + ".agent.md"
        outdir = args.out or os.path.dirname(os.path.abspath(f))
        outp = os.path.join(outdir, outname)
        open(outp, "w", encoding="utf-8").write("\n".join(fm) + "\n\n" + "\n".join(b) + "\n")
        print("  +", outp)

    print(f"\nDone. {len(files_in)} agent spec(s).")


if __name__ == "__main__":
    main()
