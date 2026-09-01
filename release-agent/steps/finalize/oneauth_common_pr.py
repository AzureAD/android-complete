"""Step: `oneauth_common_pr` — bump AndroidCommon in OneAuth and PR it to dev (Phase 4, finalize).

After the release publishes and `verify_pub` confirms Common is live on Maven Central, update
OneAuth to ingest that final (non-RC) Common version and raise a PR into `dev`.

The mechanic (see tools/oneauth.py):
  1. MERGE `dev` into the standing `android/common-ingestion` branch (server-side, via a transient
     PR — ADO does the merge; conflicts surface for a human, never forced).
  2. On `android/common-ingestion`, bump the Common version in FOUR files (libs.versions.toml,
     cgmanifest.json, deps/README.md, CHANGELOG.md) in one commit.
  3. Open `android/common-ingestion -> dev` PR titled "Merge latest common <ver> to dev".

PREVIEW-FIRST (like integ_prs): `build()` only READS — it resolves the versions, checks whether
the ingestion branch is behind dev, computes the four edits, and detects an existing PR — then
returns a NeedsSkill so the skill previews and the `create-oneauth-common-pr` command does the
WRITES (`--dry-run` / `--execute`). Idempotent: an existing open PR is reused.

Version source: `state.versions.common` (final non-RC Common) + `state.versions.msal` (for the
changelog line), both populated at Phase 2.

Mock knobs (mocks.local.yaml / tests):
  common : inject the Common version (skip state.versions).
  msal   : inject the MSAL version (skip state.versions).
  fail   : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import oneauth as OA

ID = "oneauth_common_pr"
KIND = "agent"

MOCKABLE = {
    "common": {"kind": "input", "desc": "Inject the Common version (skip state.versions)."},
    "msal": {"kind": "input", "desc": "Inject the MSAL version (skip state.versions)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _versions(state):
    v = getattr(state, "versions", None) or {}
    common = mock_input("common", MISSING)
    msal = mock_input("msal", MISSING)
    common = common if common is not MISSING else v.get("common")
    msal = msal if msal is not MISSING else v.get("msal")
    return common, msal


def _pr_url(pr_num=None):
    tail = f"/pullrequest/{pr_num}" if pr_num else ""
    return f"{OA.ORG}/{OA.PROJECT}/_git/{OA.REPO}{tail}"


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"oneauth_common_pr: {fail}")

    common, msal = _versions(state)
    if not common or not msal:
        return Blocked(
            "oneauth_common_pr: need both the Common and MSAL versions on state.versions "
            "(populated at Phase 2). Provide the `common`/`msal` mocks for testing.")

    # ---- READ-ONLY plan ----
    plan = {"common": common, "msal": msal, "ingest": OA.INGEST_BRANCH, "target": OA.TARGET_BRANCH,
            "behind": None, "merge_needed": None, "changed_files": None, "existing_pr": None,
            "notes": []}

    okab, ab, d = OA.ahead_behind(OA.TARGET_BRANCH, OA.INGEST_BRANCH)
    if okab:
        plan["behind"] = ab.get("behind")
        plan["merge_needed"] = bool(ab.get("behind"))
    else:
        plan["notes"].append(f"ahead/behind check failed ({d})")

    # compute the four edits against dev's current content (read-only)
    files, read_err = {}, None
    for key, path in OA.FILES.items():
        okr, txt, dr = OA.read_text(path, OA.TARGET_BRANCH)
        if not okr:
            read_err = dr
            break
        files[key] = txt
    if read_err:
        hint = " — run `az login`" if str(read_err).startswith("AUTH") else ""
        return Blocked(f"oneauth_common_pr: couldn't read the OneAuth files ({read_err}){hint}.")
    try:
        changed = OA.apply_edits(files, common, msal)
    except ValueError as e:
        return Blocked(f"oneauth_common_pr: the version-bump anchors don't match "
                       f"({e}) — the OneAuth file layout may have changed; a human should check.")
    plan["changed_files"] = sorted(changed.keys())

    oke, pr, de = OA.find_open_pr(OA.INGEST_BRANCH, OA.TARGET_BRANCH)
    plan["existing_pr"] = pr if oke else None
    if not oke:
        plan["notes"].append(f"existing-PR lookup failed ({de})")

    merge_txt = (f"merge dev into {OA.INGEST_BRANCH} ({plan['behind']} behind) then "
                 if plan.get("merge_needed") else "")
    pr_txt = "reuse the open PR" if plan.get("existing_pr") else f"open a PR into {OA.TARGET_BRANCH}"
    summary = (f"Ingest AndroidCommon {common} into OneAuth: {merge_txt}bump "
               f"{len(plan['changed_files'])} file(s) and {pr_txt}.")
    return NeedsSkill(
        tool="create-oneauth-common-pr",
        payload={
            "release": state.release_id,
            "plan": plan,
            "followup_command": f"create-oneauth-common-pr --release {state.release_id} --dry-run",
        },
        record_as=ID,
        summary=summary,
        note=(f"AndroidCommon {common} → {OA.INGEST_BRANCH} → {OA.TARGET_BRANCH}; "
              f"{'merge dev first; ' if plan.get('merge_needed') else ''}"
              f"{len(plan['changed_files'])} file(s) to bump"),
        outbound=True,
    )


run = legacy_run(build)
