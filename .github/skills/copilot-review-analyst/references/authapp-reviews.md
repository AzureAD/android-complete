# AuthApp / Foundry Review Adapter

## Collection

Use organization **msazure**, project **One**, repository
**AD-MFA-phonefactor-phoneApp-android**. Do not route this repo through
IdentityDivision/Engineering. Prefer ADO MCP tools only when the configured
instance can actually reach msazure. If its schema has no organization selector,
it is already scoped; an empty response is not proof that AuthApp has no reviews.

Use the same inclusive **PR completion-date** window as GitHub's merge-date window.
Enumerate all completed PR pages and exclude service/bot-authored PRs. Retrieve
full threads (all statuses, including fixed/closed), comments, and iterations for
every human PR. Do not filter to active threads or truncate comment bodies.

Export the following JSON array outside the repository and pass it to
`analyze.ps1 -AuthAppInputFile <path>`. Each entry contains the full API objects,
not MCP's abbreviated display:

```json
[
  {
    "pullRequest": {
      "pullRequestId": 123,
      "status": "completed",
      "closedDate": "2026-10-14T12:00:00Z",
      "createdBy": { "id": "author-guid", "displayName": "Engineer" },
      "repository": {
        "id": "repo-guid",
        "name": "AD-MFA-phonefactor-phoneApp-android",
        "url": "https://dev.azure.com/msazure/One/_apis/git/repositories/repo-guid"
      },
      "lastMergeSourceCommit": { "commitId": "final-source-sha" }
    },
    "threads": [],
    "iterations": []
  }
]
```

Populate full thread and iteration objects; empty arrays are valid only if the
API genuinely returned no entries. The exporter must confirm pagination and
repository access before writing an empty array. The adapter cannot infer whether
an abbreviated or partially exported corpus was complete.

If no msazure-scoped MCP instance exists, use `analyze-authapp.ps1`'s read-only
Azure CLI fallback (`az devops invoke --organization https://dev.azure.com/msazure`).
It resolves the repository, pages PRs, and retrieves threads/iterations. It does
not change CLI defaults, PRs, comments, or repository state. API/auth failures
abort collection instead of producing a success-shaped empty report.
The Azure CLI `pullRequests` list resource uses a project-wide endpoint; pass
`searchCriteria.repositoryId` explicitly. A repository route parameter alone
does not scope that list. Reject any returned PR belonging to another repository.

For a bounded check, use `-PRNumbers 17507268` with a window containing that PR's
completion. This is a **sample**, not a full-period report. The reference PR is:
https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/17507268

## Reviewer Attribution

Require both:

- Author display name `One Build Service (msazure)`.
- Content marker `AuthApp PR Reviewer`.

The example contains three marked inline findings and one marked PR summary.
Pipeline/policy/work-item automation also posts comments; service identity alone
is insufficient attribution. Unmarked service threads are excluded and surfaced
as a warning. If the reviewer migrates to a dedicated identity or changes its
marker, verify the new identity/signature against a real review, then update
`-ReviewerDisplayName` / `-ReviewerMarker`; do not relax attribution globally.

Treat Foundry as the **review source**, Azure DevOps as the **hosting platform**,
and the build-service account as the **posting identity**, not as the model name.
Do not infer a particular Foundry model from the account or marker.

## Threads and IDs

Count a marked first non-deleted text comment in a file-anchored thread as one
inline finding. A marked non-file thread is a summary: count it toward reviewed-PR
coverage but never toward inline helpfulness. Exclude system/deleted comments,
automated follow-ups, and human-only threads.

Collect later human text comments in the same thread even if `parentCommentId`
is **0**. The sample's engineer responses have that shape. Preserve the whole
exchange and native thread/comment IDs. ADO comment IDs such as `1` repeat in
every thread, so use the adapter's composite key:

`ado:msazure:<repositoryId>:<PRNumber>:<ThreadId>:<NativeCommentId>`

Use that key in `reply-verdicts.json`, overrides, signal promotions, and joins.
GitHub's numeric IDs remain unchanged for compatibility. Explicit account-map
entries can map `ado:msazure:<authorId>` to a known engineer; otherwise keep the
ADO display name. Do not guess cross-platform identity links from aliases.

## Snapshot Verification and Outcomes

Resolve `pullRequestThreadContext.iterationContext.secondComparingIteration`
to the corresponding iteration's `sourceRefCommit.commitId`. Record this as
`OriginalCommitId` and the PR's `lastMergeSourceCommit.commitId` as
`FinalCommitId`. Keep the original thread path, side, and coordinates.

ADO has no GitHub-style frozen `diff_hunk`. Inspect the file at the immutable
reviewed SHA through appropriately scoped ADO tools or available local Git
objects. Compare with final source code, not whatever `dev` or the checkout
currently contains. If the reviewed SHA is no longer an ancestor of the final
source SHA, apply the force-push-confound rule before scoring.

`precise.ps1` checks available local objects without fetching or changing Git
state. Missing snapshots and rewritten iterations return `unknown` verification
with an explicit explanation; manually verify them through ADO before asserting
adoption or factual error. LEFT-side coordinates refer to the compared target
snapshot and are not valid source-line overlap evidence.

Thread status is **context only**. `fixed` or `closed` does not automatically mean
Helpful; `wontFix` or `byDesign` does not automatically mean Incorrect or Declined.
Read human replies and code evidence using the shared classification rubric.
Foundry self-replies, summary verdicts, severity labels, or policy votes are not
human acceptance.

Use the same canonical outcomes and precision formula as GitHub. In a combined
report, show **GitHub Copilot** and **Foundry** rows separately, including coverage,
comment count, response rate, adoption, Declined, Incorrect, Unresolved, Unknown
(when nonzero), and precision with its evaluated denominator. Describe Foundry's
first measured period as a new baseline, not a trend from zero.
