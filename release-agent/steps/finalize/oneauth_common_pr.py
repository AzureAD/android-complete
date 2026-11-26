"""Step: `oneauth_common_pr` — bump AndroidCommon in OneAuth and PR it to dev (Phase 4, finalize).

After the release publishes and `verify_pub` confirms Common is live on Maven Central, update
OneAuth to ingest that final (non-RC) Common version and raise a PR into `dev`.

The mechanic (see tools/oneauth.py):
  1. Compute the exact merge of `dev` and `android/common-ingestion` in an isolated
     Git object database. Conflicts require human resolution and a fresh review.
  2. On `android/common-ingestion`, bump the Common version in FOUR files (libs.versions.toml,
     cgmanifest.json, deps/README.md, CHANGELOG.md) in one commit.
  3. Open `android/common-ingestion -> dev` PR titled "Merge latest common <ver> to dev".

CHECKED-AUTO (like integ_prs): `build()` only READS an advisory candidate — it resolves the
versions, checks ancestry, computes edits and detects an existing PR — then
returns a NeedsSkill for the checked command. Automation executes with
`--auto-approve`, which checkpoints the current plan hash before the single fenced
write attempt.

Version source: `state.versions.common` (final non-RC Common) + `state.versions.msal` (for the
changelog line), both populated at Phase 2.

Step defaults (`step_defaults` / offline scenarios):
  common : inject the Common version (skip state.versions).
  msal   : inject the MSAL version (skip state.versions).
  fail   : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.step_context import MISSING
from tools import oneauth as OA

ID = "oneauth_common_pr"
KIND = "scout"
NAME = 'Ingest published Common into OneAuth (PR to dev)'
REQUIRED_TOOLS = ('az',)
REQUIRES_APPROVAL = False
WRITE_COMMAND = "create-oneauth-common-pr"

def _versions(context):
    v = getattr(context.release, "versions", None) or {}
    common = context.input("common", MISSING)
    msal = context.input("msal", MISSING)
    common = common if common is not MISSING else v.get("common")
    msal = msal if msal is not MISSING else v.get("msal")
    return common, msal

def build(context: StepContext):
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"oneauth_common_pr: {fail}")

    common, msal = _versions(context)
    if not common or not msal:
        return Blocked(
            "oneauth_common_pr: need both the Common and MSAL versions on state.versions "
            "(populated at Phase 2). Provide `common`/`msal` in `step_defaults` for rehearsals.")

    # ---- READ-ONLY plan ----
    plan = {"common": common, "msal": msal, "ingest": OA.INGEST_BRANCH, "target": OA.TARGET_BRANCH,
            "behind": None, "merge_needed": None, "changed_files": None, "existing_pr": None,
            "notes": []}

    okab, ab, d = context.services.repositories.oneauth_ahead_behind(OA.TARGET_BRANCH, OA.INGEST_BRANCH)
    if okab:
        plan["behind"] = ab.get("behind")
        plan["merge_needed"] = bool(ab.get("behind"))
    else:
        plan["notes"].append(f"ahead/behind check failed ({d})")

    # This advisory is not the checked command's commit-pinned write review.
    files, read_err = {}, None
    for key, path in OA.FILES.items():
        okr, txt, dr = context.services.repositories.oneauth_read_text(path, OA.INGEST_BRANCH)
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

    oke, pr, de = context.services.repositories.oneauth_find_open_pr(OA.INGEST_BRANCH, OA.TARGET_BRANCH)
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
            "release": context.release.release_id,
            "plan": plan,
            "followup_command": (
                f"create-oneauth-common-pr --release {context.release.release_id} "
                "--execute --auto-approve --executor oneauth-common-automation"),
            "execution_instructions": (
                "Run the checked command with --execute --auto-approve. It recomputes the "
                "remaining plan and checkpoints each attempted/verified mutation. Rerun this "
                "same command after drift; saved receipts prevent replay and unknown outcomes hold. "
                "Use --repo-dir <clean-OneAuth-checkout> if local path discovery is unavailable. "
                "The planner calculates the merged tree and exact edits without changing your "
                "checkout or refs. Missing objects, dirty work or conflicts require resolution "
                "before another preview; no server-side merge is performed."),
        },
        record_as=ID,
        summary=summary,
        note=(f"AndroidCommon {common} → {OA.INGEST_BRANCH} → {OA.TARGET_BRANCH}; "
              f"{'merge dev first; ' if plan.get('merge_needed') else ''}"
              f"{len(plan['changed_files'])} file(s) to bump"),
        outbound=True,
    )

KNOWLEDGE = {'summary': 'Ingest the published final Common version into OneAuth through an exact checked '
            'PR plan.',
 'what': 'After the release publishes and verify_pub confirms Common is live on Maven Central, '
         'update OneAuth to consume that final (non-RC) Common version. On the standing '
         '`android/common-ingestion` branch it bumps the AndroidCommon version in FOUR files — '
         'the gradle version catalog (msIdentityCommon in libs.versions.toml), the CG manifest '
         '(com.microsoft.identity:common maven version in deps/cgmanifest.json), the '
         "deps/README.md 'MSAL Android Common' table row, and a new '(Android) Ingest "
         "AndroidCommon <ver>' CHANGELOG.md bullet — then opens a PR into `dev`.\n",
 'who': 'Scout composes a read-only advisory, then the scheduled checked command executes the '
        'release-owned write. Its current exact plan hash, provider identities and single '
        'attempt are checkpointed and fenced even when --auto-approve is used.\n',
 'where': ['repo: OneAuth — office.visualstudio.com/OneAuth/_git/OneAuth '
           '(config/coordinates.yaml repos.oneauth)',
           'branch: android/common-ingestion -> dev',
           'version: state.versions.common (final non-RC Common) + state.versions.msal (for '
           'the changelog line)'],
 'how': 'Preview `create-oneauth-common-pr --release <id>` for the complete transient plan '
        'without writes. Scheduled execution uses --execute --auto-approve --executor '
        '<automation-id>; a manually reviewed execution instead supplies its exact '
        '--review-hash and --approved-by. A clean local OneAuth checkout supplies '
        'commit-pinned Git objects. The planner computes the dev/ingestion merge and four-file '
        'edits in an isolated object database, then the checked writer pushes that exact '
        'commit and opens/reuses the ingestion-to-dev PR. No server-side merge is performed. '
        'Conflicts or missing objects hold before writing. Interrupted/uncertain attempts '
        'retain their ownership and cannot be blindly retried. Test version overrides do not '
        'bypass write guards.\n',
 'consequences': 'A real WRITE to OneAuth: the reviewed merge/version-bump commit on '
                 'android/common-ingestion and a PR into dev. Exact repository/tip/content '
                 'checks protect the write; conflicts and uncertain results remain held for '
                 'owner recovery, never a forced merge.\n',
 'faqs': [{'q': 'Why merge dev into the ingestion branch instead of fast-forwarding?',
           'a': 'android/common-ingestion is a long-lived staging branch that can diverge from '
                'dev. The checked planner computes the exact merge in an isolated local Git '
                'object database and preserves its history; it never requests an unreviewed '
                'server-side merge.'},
          {'q': 'What version does it write?',
           'a': 'state.versions.common — the final non-RC Common version published to Maven '
                'Central (verify_pub confirms it first). The changelog line also references '
                'state.versions.msal.'},
          {'q': 'What if the merge conflicts?',
           'a': 'The step holds (attention) and surfaces it — a human resolves the '
                'dev->ingestion merge, then re-runs. Scout never force-merges.'}]}
