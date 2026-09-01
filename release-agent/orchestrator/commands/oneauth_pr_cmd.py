"""`create-oneauth-common-pr` — bump AndroidCommon in OneAuth and PR it to dev (Phase-4).

Preview-first, mirroring `create-integration-prs`: with no flag it prints the plan and writes
NOTHING. With `--execute` it performs the writes, in order:

  1. MERGE `dev` into `android/common-ingestion` server-side (transient PR; conflicts are
     surfaced and the step is held — never forced),
  2. bump the four files (libs.versions.toml, cgmanifest.json, deps/README.md, CHANGELOG.md) in
     ONE commit on `android/common-ingestion`,
  3. open (or reuse) the `android/common-ingestion -> dev` PR,
  4. record the `finalize.oneauth_common_pr` step (pass, or attention on a conflict/failure).

Honors the release's `finalize.oneauth_common_pr` mocks (common / msal) for offline testing.
"""
from __future__ import annotations

from orchestrator import cli_common as C
from steps.lib import mockctx
from steps.finalize import oneauth_common_pr as S
from tools import oneauth as OA


def _step_mocks(orch):
    return (getattr(orch, "mocks", {}) or {}).get("finalize.oneauth_common_pr", {}) or {}


def _record(orch, args, status, summary):
    orch.record_scout_step("finalize", "oneauth_common_pr", status, summary)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[oneauth_common_pr] {summary}", kind="step",
           log_text=summary)


def cmd_create_oneauth_common_pr(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    with mockctx.active(_step_mocks(orch)):
        common, msal = S._versions(st)
    if not common or not msal:
        print("No Common/MSAL versions on state.versions — nothing to ingest.")
        return 1

    # ---- read-only plan ----
    okab, ab, dab = OA.ahead_behind(OA.TARGET_BRANCH, OA.INGEST_BRANCH)
    behind = (ab or {}).get("behind") if okab else None
    oke, existing, _de = OA.find_open_pr(OA.INGEST_BRANCH, OA.TARGET_BRANCH)
    existing = existing if oke else None

    print(f"OneAuth AndroidCommon ingestion — Common {common} (MSAL {msal})")
    print(f"  repo:   {OA.REPO}  ({OA.ORG}/{OA.PROJECT})")
    print(f"  branch: {OA.INGEST_BRANCH} -> {OA.TARGET_BRANCH}")
    print(f"  merge:  {'dev is ' + str(behind) + ' commit(s) ahead → merge needed' if behind else ('up to date' if behind == 0 else 'ahead/behind unknown (' + str(dab) + ')')}")
    print(f"  files:  libs.versions.toml, cgmanifest.json, deps/README.md, CHANGELOG.md")
    print(f"  PR:     {'REUSE ' + (existing.get('url') or '') if existing else 'open new'}")

    if not args.execute:
        print("\n(dry-run — no merge, no commit, no PR. Re-run with --execute to write.)")
        return 0

    # ---- 1) merge dev into the ingestion branch (server-side) ----
    if behind:
        okm, minfo, dm = OA.merge_dev_into_ingestion(dry_run=False)
        if not okm:
            conflict = bool((minfo or {}).get("conflict"))
            detail = f"oneauth_common_pr: {'MERGE CONFLICT — ' if conflict else ''}{dm}"
            _record(orch, args, "attention", detail)
            print("\n" + detail)
            return 2
        print(f"  merged dev into {OA.INGEST_BRANCH}" if (minfo or {}).get("merged") else "  (already current)")

    # ---- 2) bump the four files in one commit ----
    files = {}
    for key, path in OA.FILES.items():
        okr, txt, dr = OA.read_text(path, OA.INGEST_BRANCH)
        if not okr:
            _record(orch, args, "attention", f"oneauth_common_pr: read {path} failed ({dr})")
            return 2
        files[key] = txt
    try:
        changed = OA.apply_edits(files, common, msal)
    except ValueError as e:
        _record(orch, args, "attention", f"oneauth_common_pr: bump anchors not found ({e})")
        return 2
    if changed:
        oktip, tip, dt = OA.branch_object_id(OA.INGEST_BRANCH)
        if not oktip:
            _record(orch, args, "attention", f"oneauth_common_pr: could not resolve branch tip ({dt})")
            return 2
        comment = f"Ingest AndroidCommon {common} (bump libs.versions.toml, cgmanifest.json, README, CHANGELOG)"
        okp, commit, dp = OA.push_edits(OA.INGEST_BRANCH, tip, changed, comment)
        if not okp:
            _record(orch, args, "attention", f"oneauth_common_pr: push bump failed ({dp})")
            return 2
        print(f"  pushed bump commit {str(commit)[:8]} ({len(changed)} file(s))")
    else:
        print("  no file changes needed (already at this version)")

    # ---- 3) open / reuse the ingestion -> dev PR ----
    if existing:
        url = existing.get("url")
        print(f"  reused PR {url}")
    else:
        title = f"Merge latest common {common} to dev"
        body = (f"Automated (release-agent · oneauth_common_pr): ingest AndroidCommon {common} "
                f"into `{OA.TARGET_BRANCH}`.\nAny apps that still use MSAL.Android must update to {msal}.")
        okc, pr, dc = OA.create_pr(OA.INGEST_BRANCH, OA.TARGET_BRANCH, title, body)
        if not okc:
            _record(orch, args, "attention", f"oneauth_common_pr: opening the PR failed ({dc})")
            return 2
        url = pr.get("url")
        print(f"  opened PR {url}")

    summary = f"oneauth_common_pr: AndroidCommon {common} ingested — PR {url}"
    _record(orch, args, "pass", summary)
    return 0


def register(sub):
    p = sub.add_parser(
        "create-oneauth-common-pr",
        help="Bump AndroidCommon in OneAuth and PR it to dev (preview by default; --execute to write)")
    p.add_argument("--release", required=True)
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.add_argument("--execute", action="store_true",
                   help="Perform the writes (merge dev, bump 4 files, open PR). Default is dry-run.")
    p.set_defaults(func=cmd_create_oneauth_common_pr)
