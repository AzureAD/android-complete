#!/usr/bin/env python3
"""Environment preflight for the `vuln-triage-reporter` skill — RUN THIS FIRST.

Why this exists
---------------
Three real failures, each of which produced a *confidently wrong* verdict:

1. **Missing submodule.** The app/broker code lives in git-ignored submodules. When they are absent, a
   grep for a vulnerable sink returns nothing and the finding gets down-classified as "not present."

2. **Stale remote (the worst one).** The broker repo migrated hosts. The old mirror kept serving a
   *frozen snapshot*, so `git fetch` still exited 0 and still printed "Already up to date." Every
   `git log --all` / `git branch --contains` Gate-0 check silently covered only history up to the
   migration date. A fix that had already shipped was reported as "no fix exists." **A fetch that
   succeeds is not evidence that you are looking at the current repository.**

3. **Missing server-side source.** Two findings hinged on what the token service does with a request.
   Without the identity-service source checked out, that question is unanswerable and the finding stalls
   at "unverifiable boundary" — or worse, gets guessed.

All three are cheap to detect and expensive to miss, so they are a hard gate rather than advice.

Usage
-----
    python preflight.py                      # check everything, human-readable
    python preflight.py --json               # machine-readable
    python preflight.py --fix-remotes        # offer the exact commands to repair drift
    python preflight.py --root C:\\src\\android-complete --ests C:\\src\\ESTS-Main

Exit codes: 0 = all PASS · 1 = at least one FAIL (do not begin the investigation).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Modules that must be populated for a source-grep investigation to mean anything.
REQUIRED_SUBMODULES = [
    ("authenticator/PhoneFactor", "Microsoft Authenticator app + MSA SDK"),
    ("broker/AADAuthenticator", "broker app code"),
    ("broker/broker4j", "broker library"),
    ("common", "common / common4j shared library"),
    ("msal", "MSAL library"),
    ("adal", "ADAL library"),
]

# Expected remote host fragments per module. A module whose remote does not contain the expected
# fragment is treated as DRIFTED — its history cannot be trusted for a Gate-0 "already fixed?" call.
#
# NOTE: the broker moved to GitHub Enterprise. The retired location still resolves and still serves a
# frozen snapshot, which is exactly why this check exists. See docs/broker-remote-migration.md.
EXPECTED_REMOTES = {
    "broker": ("msft.ghe.com", "security/ad-accounts-for-android"),
    "common": ("github.com", "microsoft-authentication-library-common-for-android"),
    "msal": ("github.com", "microsoft-authentication-library-for-android"),
    "adal": ("github.com", "azure-activedirectory-library-for-android"),
}

# How many days of drift before we consider a checkout stale enough to distort a Gate-0 answer.
STALE_DAYS = 3


def run(args, cwd=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=180)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as exc:  # pragma: no cover
        return 1, "", str(exc)


class Report:
    def __init__(self):
        self.items = []

    def add(self, status, name, detail="", fix=""):
        self.items.append(
            {"status": status, "name": name, "detail": detail, "fix": fix}
        )

    @property
    def failed(self):
        return [i for i in self.items if i["status"] == "FAIL"]

    def render(self):
        for i in self.items:
            icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]"}[i["status"]]
            print(f"  {icon} {i['name']}")
            if i["detail"]:
                for line in i["detail"].splitlines():
                    print(f"         {line}")
            if i["fix"] and i["status"] != "PASS":
                for line in i["fix"].splitlines():
                    print(f"         fix: {line}")


def check_submodules(root: Path, rep: Report):
    for rel, desc in REQUIRED_SUBMODULES:
        p = root / rel
        populated = p.is_dir() and any(p.iterdir()) if p.is_dir() else False
        if populated:
            rep.add("PASS", f"submodule populated: {rel}")
        else:
            rep.add(
                "FAIL",
                f"submodule MISSING/EMPTY: {rel}  ({desc})",
                "A grep against a missing module returns nothing, which reads as 'sink not present'\n"
                "and silently down-classifies a real finding.",
                "git droidSetup    (or: git submodule update --init --recursive)",
            )


def check_worktree(root: Path, rep: Report):
    rc, out, _ = run(["git", "-C", str(root), "rev-parse", "--git-dir"])
    if rc != 0:
        rep.add("FAIL", "android-complete is not a git repo", out)
        return
    if out.strip() != ".git":
        rep.add(
            "FAIL",
            "running from a git WORKTREE, not the main checkout",
            f"git-dir = {out}\nWorktrees typically lack the submodules.",
            "cd to the main checkout (e.g. C:\\src\\android-complete)",
        )
    else:
        rep.add("PASS", "on the main checkout (not a worktree)")


def check_remotes(root: Path, rep: Report, fix_mode: bool):
    for module, (host, slug) in EXPECTED_REMOTES.items():
        mod = root / module
        if not (mod / ".git").exists():
            continue  # covered by the submodule check
        rc, url, _ = run(["git", "-C", str(mod), "remote", "get-url", "origin"])
        if rc != 0:
            rep.add("FAIL", f"{module}: no origin remote", url)
            continue
        if host in url and slug in url:
            rep.add("PASS", f"{module} remote OK ({host})")
        else:
            rep.add(
                "FAIL",
                f"{module} remote DRIFTED -> {url}",
                "The retired remote may still fetch successfully while serving a FROZEN snapshot.\n"
                "Any 'already fixed?' answer from this checkout is unreliable.",
                f'git -C "{mod}" remote set-url origin https://{host}/{slug}.git',
            )


def check_freshness(root: Path, rep: Report):
    """Fetch and report how far behind each module is.

    A successful fetch is NOT proof of currency — see the module docstring. We therefore also report
    the age of the newest commit, which is what actually exposes a frozen mirror.
    """
    import datetime as _dt

    for module, _ in EXPECTED_REMOTES.items():
        mod = root / module
        if not (mod / ".git").exists():
            continue
        run(["git", "-C", str(mod), "fetch", "origin", "--prune"])
        rc, branch, _ = run(["git", "-C", str(mod), "rev-parse", "--abbrev-ref", "HEAD"])
        if rc != 0:
            continue
        rc, behind, _ = run(
            ["git", "-C", str(mod), "rev-list", "--count", f"HEAD..origin/{branch}"]
        )
        behind_n = int(behind) if behind.isdigit() else -1
        rc, iso, _ = run(
            ["git", "-C", str(mod), "log", "-1", "--format=%cI", f"origin/{branch}"]
        )
        age_days = None
        if iso:
            try:
                when = _dt.datetime.fromisoformat(iso)
                age_days = (
                    _dt.datetime.now(_dt.timezone.utc) - when.astimezone(_dt.timezone.utc)
                ).days
            except ValueError:
                pass

        detail = f"branch={branch} behind={behind_n} newest_remote_commit_age={age_days}d"
        if behind_n > 0:
            rep.add(
                "FAIL",
                f"{module} is BEHIND origin/{branch} by {behind_n} commit(s)",
                detail,
                f'git -C "{mod}" pull --ff-only',
            )
        elif age_days is not None and age_days > STALE_DAYS * 10:
            # Up to date with a remote whose newest commit is ancient => probably a frozen mirror.
            rep.add(
                "WARN",
                f"{module}: up to date, but the remote itself looks FROZEN",
                detail
                + "\nAn abandoned mirror reports 'Already up to date' forever. Verify the remote URL.",
                "confirm the module has not migrated hosts",
            )
        else:
            rep.add("PASS", f"{module} current ({detail})")


def check_ests(ests: Path | None, rep: Report):
    if ests is None or not ests.exists():
        rep.add(
            "FAIL",
            "identity-service (ESTS) source NOT available",
            "Findings that turn on what the token service validates (grant handling, caller/app-id\n"
            "checks, redirect handling) cannot be resolved without it. Without this the verdict stalls\n"
            "at 'unverifiable server-side boundary' — or gets guessed, which is worse.",
            "Clone the ESTS repo locally and re-run with --ests <path>\n"
            "(ask the on-call lead for the clone URL / access package if you do not have it).",
        )
        return
    rc, out, _ = run(["git", "-C", str(ests), "rev-parse", "--git-dir"])
    if rc != 0:
        rep.add("FAIL", f"ESTS path is not a git repo: {ests}")
        return
    run(["git", "-C", str(ests), "fetch", "origin", "--prune"])
    rc, branch, _ = run(["git", "-C", str(ests), "rev-parse", "--abbrev-ref", "HEAD"])
    rc, behind, _ = run(
        ["git", "-C", str(ests), "rev-list", "--count", f"HEAD..origin/{branch}"]
    )
    behind_n = int(behind) if behind.isdigit() else -1
    if behind_n > 0:
        rep.add(
            "FAIL",
            f"ESTS behind origin/{branch} by {behind_n}",
            "",
            f'git -C "{ests}" pull --ff-only',
        )
    else:
        rep.add("PASS", f"ESTS present and current ({ests}, branch={branch})")


def check_workspace(rep: Report):
    ws = os.environ.get("VULN_TRIAGE_WORKSPACE") or str(Path.home() / "vuln-triage-workspace")
    try:
        Path(ws).mkdir(parents=True, exist_ok=True)
        rep.add("PASS", f"private workspace writable: {ws}")
    except Exception as exc:
        rep.add("FAIL", f"workspace not writable: {ws}", str(exc))


def main():
    ap = argparse.ArgumentParser(description="Preflight gate for vuln-triage-reporter.")
    ap.add_argument("--root", default=r"C:\src\android-complete")
    ap.add_argument(
        "--ests",
        default=os.environ.get("ESTS_ROOT", r"C:\src\ESTS-Main"),
        help="Path to the identity-service (ESTS) checkout.",
    )
    ap.add_argument("--skip-fetch", action="store_true", help="Skip network freshness checks.")
    ap.add_argument("--fix-remotes", action="store_true", help="Print repair commands for drift.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    ests = Path(args.ests) if args.ests else None
    rep = Report()

    if not root.exists():
        print(f"FAIL: android-complete not found at {root}")
        return 1

    check_worktree(root, rep)
    check_submodules(root, rep)
    check_remotes(root, rep, args.fix_remotes)
    if not args.skip_fetch:
        check_freshness(root, rep)
    check_ests(ests, rep)
    check_workspace(rep)

    if args.json:
        print(json.dumps(rep.items, indent=2))
    else:
        print("\nPreflight - vuln-triage-reporter\n" + "=" * 60)
        rep.render()
        print("=" * 60)
        if rep.failed:
            print(
                f"\nFAIL: {len(rep.failed)} blocking item(s).\n"
                "DO NOT begin the investigation. A partial or stale environment produces\n"
                "confidently wrong verdicts - that is the failure this gate exists to prevent.\n"
                "Report the failures to the user with the fix commands above."
            )
        else:
            print("\nPASS: environment is complete and current. Safe to begin.")

    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
