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

# Nested submodules: a parent repo can build against its OWN copy of a library rather than the
# top-level one. `broker/settings.gradle` maps :common/:common4j to broker/common. Analysing the
# top-level checkout while the build uses a different (stale) commit yields conclusions about code
# that never ships.
NESTED_SUBMODULES = [
    ("broker", "common"),
]

# How many days of drift before we consider a checkout stale enough to distort a Gate-0 answer.
STALE_DAYS = 3

# Commits behind the module's DEFAULT branch before a non-default branch is treated as abandoned.
# Small non-zero tolerance so a legitimate short-lived feature branch does not trip the gate.
MAX_BRANCH_DRIFT = 50

# ESTS is a hyper-active monorepo: it lands commits continuously, so "behind == 0" is never
# durably true and a strict check makes the gate unsatisfiable (observed: 323 -> 4 -> 1 across
# consecutive pulls in one session). What matters is that it is recent enough to answer questions.
ESTS_MAX_BEHIND = 250


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
        # A module parked on an abandoned branch is the most dangerous state of all: `git pull` says
        # "Already up to date" forever because the BRANCH is dead, not the remote. A real run had
        # `common` sitting on master @ a 4-year-old commit, 4200+ commits behind dev — greps against it
        # returned nothing, and "no results" reads as "the sink isn't there". This must FAIL, not WARN.
        default_branch = _default_branch(mod)
        if default_branch and branch != default_branch:
            rc_d, drift, _ = run(
                ["git", "-C", str(mod), "rev-list", "--count", f"HEAD..origin/{default_branch}"]
            )
            drift_n = int(drift) if drift.isdigit() else -1
            if drift_n > MAX_BRANCH_DRIFT:
                rep.add(
                    "FAIL",
                    f"{module} is on '{branch}', {drift_n} commit(s) behind origin/{default_branch}",
                    detail
                    + f"\nThis branch looks ABANDONED. A grep here returns nothing for code that exists on"
                    f"\n'{default_branch}', and an empty grep reads as 'the sink is not there'.",
                    f'git -C "{mod}" checkout {default_branch} && git -C "{mod}" pull --ff-only',
                )
                continue

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


def check_nested_submodules(root: Path, rep: Report):
    """Verify nested submodules match the gitlink their parent actually builds against.

    `broker/settings.gradle` wires `:common`/`:common4j` to `broker/common` — a SEPARATE checkout from
    the top-level `common`. In a real run that nested checkout was parked on an unrelated branch and
    lacked the security controls entirely, which sent an adversarial reviewer down a false path and
    capped confidence until it was manually resolved. The gitlink is the source of truth.
    """
    for parent, nested in NESTED_SUBMODULES:
        pdir = root / parent
        ndir = root / parent / nested
        if not (pdir / ".git").exists() or not ndir.exists():
            continue
        rc, out, _ = run(["git", "-C", str(pdir), "ls-tree", "HEAD", nested])
        if rc != 0 or not out:
            continue
        parts = out.split()
        pinned = parts[2] if len(parts) >= 3 else ""
        rc, head, _ = run(["git", "-C", str(ndir), "rev-parse", "HEAD"])
        if not pinned or not head:
            continue
        if head.strip() != pinned.strip():
            rep.add(
                "FAIL",
                f"{parent}/{nested} does not match the gitlink {parent} builds against",
                f"pinned={pinned[:9]} checked_out={head.strip()[:9]}\n"
                f"'{parent}/settings.gradle' builds against this nested checkout, so analysing it gives\n"
                f"conclusions about code that never ships.",
                f'git -C "{pdir}" submodule update --init --recursive {nested}',
            )
        else:
            rep.add("PASS", f"{parent}/{nested} matches its gitlink ({pinned[:9]})")


def _default_branch(mod: Path) -> str:
    """Resolve the module's default branch from origin/HEAD, falling back to dev/main/master."""
    rc, out, _ = run(["git", "-C", str(mod), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
    if rc == 0 and out:
        return out.rsplit("/", 1)[-1]
    for cand in ("dev", "main", "master"):
        rc, _, _ = run(["git", "-C", str(mod), "rev-parse", "--verify", f"origin/{cand}"])
        if rc == 0:
            return cand
    return ""


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
    if behind_n > ESTS_MAX_BEHIND:
        rep.add(
            "FAIL",
            f"ESTS behind origin/{branch} by {behind_n}",
            f"More than {ESTS_MAX_BEHIND} commits behind — token-service answers may be out of date.",
            f'git -C "{ests}" pull --ff-only',
        )
    elif behind_n > 0:
        # Normal churn on a monorepo. Report it, but do not block the run.
        rep.add(
            "PASS",
            f"ESTS present and current enough ({ests}, branch={branch}, behind={behind_n})",
            f"Within the {ESTS_MAX_BEHIND}-commit tolerance for this monorepo's normal churn.",
        )
    else:
        rep.add("PASS", f"ESTS present and current ({ests}, branch={branch})")


def check_app_freshness(root: Path, rep: Report):
    """The Authenticator app checkout is only verified as 'populated' elsewhere — but MSRC cases are
    filed against the APP, and its gradle.properties is what pins the shipping broker/common versions.
    A stale app checkout silently answers the release-exposure question wrong."""
    app = root / "authenticator" / "PhoneFactor"
    if not app.exists():
        return
    # The git repo root is the parent (`authenticator/`), not PhoneFactor itself — resolve it rather
    # than assuming, or this check silently no-ops.
    rc, top, _ = run(["git", "-C", str(app), "rev-parse", "--show-toplevel"])
    if rc != 0 or not top:
        return
    app = Path(top.strip())
    run(["git", "-C", str(app), "fetch", "origin", "--prune"])
    rc, branch, _ = run(["git", "-C", str(app), "rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0 or not branch:
        return
    rc, behind, _ = run(
        ["git", "-C", str(app), "rev-list", "--count", f"HEAD..origin/{branch}"]
    )
    behind_n = int(behind) if behind.isdigit() else -1
    if behind_n > 0:
        rep.add(
            "FAIL",
            f"authenticator app is BEHIND origin/{branch} by {behind_n} commit(s)",
            f"branch={branch} behind={behind_n}\n"
            "The app pins the shipping broker/common versions - a stale checkout answers the\n"
            "'was a shipped release vulnerable?' question incorrectly.",
            f'git -C "{app}" pull --ff-only',
        )
    else:
        rep.add("PASS", f"authenticator app current (branch={branch})")


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
    check_nested_submodules(root, rep)
    if not args.skip_fetch:
        check_freshness(root, rep)
        check_app_freshness(root, rep)
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
