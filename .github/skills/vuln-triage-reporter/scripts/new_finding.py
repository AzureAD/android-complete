#!/usr/bin/env python3
"""Scaffold a NEW per-finding report into the current on-call shift folder — and APPEND it to the
existing shift report.

Why this exists
---------------
Two real failures this prevents:

1. **Free-handed reports.** An agent wrote a finding report from memory instead of from
   `references/report-template.md`. It read fine as prose, but the `**Label:**` fields and the
   `| **Filed** |` / `| **Ours** |` rows are a **parser contract** that drives the HTML stat tiles and the
   master-report row. The result: a published report whose Severity / Confidence / Verdict tiles were all
   blank, and a master table that mislabelled an MSRC as an ITD. Scaffolding from a known-good skeleton
   makes that class of error impossible — the agent fills blanks instead of inventing structure.

2. **Second finding of the week overwriting / forking the first.** On-call gets findings one at a time
   across a Wed->Wed shift. This script always resolves the CURRENT shift folder, dedups against
   `manifest.json`, and writes alongside the existing findings so the master report simply grows.

Usage
-----
    # New MSRC lands mid-shift -> scaffold it, then fill in the TODOs
    python new_finding.py --icm 31000000NNNNNN --tag MSRC --component Broker \
        --title "AccountAuthenticator caller identity"

    # An older shift / explicit window
    python new_finding.py --icm NNNNNN --tag ITD --component Authenticator --title "x" --date 2026-06-20

    # Already triaged this shift? -> exits 3 unless you pass --force
    python new_finding.py --icm 31000000NNNNNN ... --force

After filling in the report, run the pipeline (see `rebuild_shift.py`, which does all of it):
    python lint_finding.py <the .md>              # structure gate — must PASS
    python rebuild_shift.py                       # regenerates research + master + rollup for the shift
    python verify_outputs.py                      # closing gate
"""
import argparse
import os
import re
import sys

import shift  # sibling module: shift-window + manifest helpers

sys.stdout.reconfigure(encoding="utf-8")

SKELETON = """\
# [{tag}] [{icm}] — {title}

**Component:** {component}
**Linked IcM:** {icm}  ·  **Filed by:** TODO (MSRC researcher / FireWatch / Glasswing)

## Classification

| | Source | Tier | Class / CWE |
|---|--------|------|-------------|
| **Filed** | TODO | TODO | TODO |
| **Ours** | this investigation | TODO | TODO |

**Verdict:** TODO — AGREE | DOWN-CLASSIFY | UP-CLASSIFY | RE-ROOTED
**Confidence:** TODO — High | Medium | Low
**IcM Severity:** TODO — Sev2 | Sev2.5 | Sev3 | Sev4
**Assignment:** TODO — Won't-Fix (Already-Covered) | Intern-eligible | Engineer-owned
**External validation:** TODO — Yes | No, and one line on why
**Prior incidents:** TODO — None found, or "IcM NNN — outcome". Run the prior-art sweep before writing this.
**Bottom line:** TODO — ONE plain-English sentence: what it is, what to do now, what is still open.
**Justification:** TODO — 1-3 sentences anchored to the cited evidence below.

> The `**Label:**` lines above and the `**Filed**`/`**Ours**` rows are a PARSER CONTRACT — they populate
> the HTML stat tiles and the master-report row. Keep each on its own line. After generating, confirm no
> tile renders as "—".

## Scope Contract

- **IN SCOPE:** TODO — the subsystem/channel the sink lives in
- **Entry point:** TODO — how an attacker reaches it
- **Asset at risk:** TODO — credential / PII / device registration / ...
- **Trust decision under attack:** TODO
- **Consumers:** TODO — who legitimately calls this
- **OUT OF SCOPE (inadmissible either way):** TODO — the co-resident subsystems you excluded. A control
  there counts only with a named hop-by-hop path from this entry point; "same app" is not a path.

## Description
TODO — 2-4 sentences, plain English.

## How It Can Be Exploited
TODO — numbered preconditions -> steps -> outcome. No PoC payloads, no PII. If refuted, say
"Not exploitable as filed" and why.

## Sink (cited)
- **`<File>.kt#L<start>-<end>`** — TODO

## Reachability
- Reachable in shipping config? TODO — YES / NO / CONDITIONAL, and the conditions.
- Entry point -> sink call path, citing `file#Lxx` at each hop.

## Defense-in-Depth Sweep (look beyond)

| Layer | Finding | Evidence |
|-------|---------|----------|
| Component export | TODO | TODO |
| IPC boundary | TODO | TODO |
| Sibling handlers | TODO | TODO |
| Flight gates | TODO | TODO |
| Upstream validation | TODO | TODO |
| Build/config gating | TODO | TODO |

## Aggravating Factors
- TODO

## Severity Rationale (SDL/MSRC bug bar)

| Factor | Reading |
|---|---|
| **Vulnerability class** | TODO |
| **Attack vector** | TODO |
| **Privileges / UI** | TODO |
| **Prerequisites** | TODO |
| **Blast radius** | TODO |
| **CIA** | TODO |

> **-> TODO tier, IcM SevN** — name what stops it going higher AND what stops it going lower.

## Scope & Verification Boundary
TODO — what we own and verified vs. what we cannot confirm (downstream consumers, server-side).

## Adversarial Verification
- **What the Challenger attempted:** TODO
- **Result:** TODO — HELD | WEAKENED | OVERTURNED
- **What changed:** TODO
- **Confidence set:** TODO — and why

## Claim Ledger

| # | Claim (verbatim from the filed report) | Channel | Status | Evidence |
|---|---|---|---|---|
| 1 | "TODO quote the filed claim verbatim" | TODO subsystem | TODO | TODO |

## Verification Gaps & What We Need to Confirm

| # | Open question (unverified) | Why not statically verifiable | What we checked instead | What we need | If confirmed -> effect |
|---|---|---|---|---|---|
| 1 | TODO | TODO | TODO | TODO | TODO |

> **Can proceed now vs. blocked:** TODO

## Decisions Needed
- TODO — with a recommendation.

## Remediation
TODO — 2-3 options with tradeoffs and a recommendation. Do NOT write code before the engineer picks.

## Estimated Eng-Days
TODO (ESTIMATE — on-call to adjust). Basis: TODO.

## Searches Run (audit trail)
Verbatim, BOTH passes. Especially the searches that returned NOTHING — they are the absence proofs.
- `<pattern>` in `<scope>` -> TODO
- `[challenger] <pattern>` in `<scope>` -> TODO
"""


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")[:60]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--icm", required=True, help="IcM id")
    ap.add_argument("--tag", default="MSRC", choices=["MSRC", "ITD"], help="MSRC or ITD (default MSRC)")
    ap.add_argument("--title", required=True, help="Short vulnerability title")
    ap.add_argument("--component", required=True,
                    help="Authenticator | Broker | Common | MSAL | ADAL (drives the repo tile + assignment)")
    ap.add_argument("--slug", help="Filename slug (default: derived from the title)")
    ap.add_argument("--force", action="store_true", help="Rewrite even if this IcM is already in the manifest")
    ap.add_argument("--date", help="A day inside the desired shift (YYYY-MM-DD); default today")
    ap.add_argument("--start", help="Explicit shift start (YYYY-MM-DD); use with --end")
    ap.add_argument("--end", help="Explicit shift end (YYYY-MM-DD); use with --start")
    args = ap.parse_args()

    s, e = shift.shift_window(args.date, args.start, args.end)
    run_dir = shift.shift_dir(s, e)
    findings_dir = os.path.join(run_dir, "findings")
    os.makedirs(findings_dir, exist_ok=True)

    manifest = shift.load_manifest(s, e)
    existing = manifest.get(str(args.icm))
    if existing and not args.force:
        hits = [p for p in os.listdir(findings_dir)
                if p.endswith(".md") and str(args.icm) in p] if os.path.isdir(findings_dir) else []
        where = os.path.join(findings_dir, hits[0]) if hits else f"{findings_dir} (no file matched)"
        print(f"SEEN: {args.icm} is already in this shift "
              f"(first_seen={existing.get('first_seen', '?')}, slug={existing.get('slug', '')}).")
        print(f"      Report:  {where}")
        print("      Append to the existing report, or pass --force to re-scaffold it.")
        return 3

    slug = args.slug or f"icm-{args.icm}-{slugify(args.title)}"
    path = os.path.join(findings_dir, f"{slug}.md")
    if os.path.exists(path) and not args.force:
        print(f"EXISTS: {path}\n        Pass --force to overwrite.")
        return 3

    with open(path, "w", encoding="utf-8") as f:
        f.write(SKELETON.format(tag=args.tag, icm=args.icm, title=args.title, component=args.component))

    manifest[str(args.icm)] = {
        "first_seen": (existing or {}).get("first_seen") or
                      __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                      .isoformat(timespec="seconds"),
        "slug": slug,
        "tag": args.tag,
    }
    shift.save_manifest(s, e, manifest)

    n = len([f for f in os.listdir(findings_dir) if f.endswith(".md")])
    print(f"+ scaffolded: {path}")
    print(f"+ manifest:   {shift.manifest_path(s, e)}  ({len(manifest)} finding(s) this shift)")
    print(f"\nShift {shift.label_for(s, e)} now has {n} finding report(s).")
    print("\nNext:")
    print(f"  1. Fill in every TODO in {os.path.basename(path)}")
    print(f"  2. python lint_finding.py \"{path}\"")
    print("  3. python rebuild_shift.py        # regenerates research + master + rollup for ALL findings")
    print("  4. python verify_outputs.py       # closing gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
