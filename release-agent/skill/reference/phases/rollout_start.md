# Reference — Phase 5 (Rollout Start)

Use `task` for one requested operation and `next` only for explicit release
progression. Release progression does not require background automations or unrelated
integration setup. Result recording never approves a rollout gate.
The detailed source/target rules below remain enforced by the checked adapters;
they are not permission to send or start stages during a status request.

## `identify_auth_build` — final Authenticator build/version capture

`rollout_start.identify_auth_build` is the first Phase-5 step (`depends_on: [finalize.integ_prs_merged]`).
It reads pipeline 475778 (`AndroidBuildBroker1ES`) on `state.versions.authenticator`
(`release/YYYY/MM/DD`) and records the latest completed successful run into
`state.pipeline_runs.final_auth`.

If no run is found, the step blocks (`RECHECK_WHEN_BLOCKED = True`) for release-owner
investigation; `release-drainer` re-checks automatically and contacts the owner by
email and Scout. Once `identify_auth_build` completes, three steps become eligible in
parallel:
- `tag_authenticator` (auto idempotent git tag on `final_auth.commit`)
- `wiki_payload` (`create-payload-wiki` in IdentityWiki)
- `signoff_start` (`start-release-signoff` on pipeline 397224)

*(Note: the former `rollout_start.notice` step was removed pending the release-comms
rework; its HTML layout is preserved in `templates/rollout-start-notice.html`.)*

## `signoff_start`, `upload_alpha`, `verify_alpha_uploaded`, `upload_whats_new` — pipeline 397224 stages

`rollout_start.signoff_start`, `rollout_start.upload_alpha`, and
`rollout_start.upload_whats_new` are checked external writes, not human gates. Each locates
the Android Build Release run in msazure/One pipeline 397224 for
`state.versions.authenticator` and starts the stage by its canonical YAML `stage:` identifier:
- `signoff_start` -> `Sign_Off` (`Release Sign Off`)
- `upload_alpha` -> `Upload_To_Alpha_GPS` (`Upload Alpha - Google Play`)
- `verify_alpha_uploaded` -> automated read-only check (`RECHECK_WHEN_BLOCKED = True`) confirming `Upload_To_Alpha_GPS` completed with `succeeded` before `upload_whats_new` and `beta_play_store` can run
- `upload_whats_new` -> `Upload_Whats_New` (`Upload What's New`)

Selection is deterministic:

- When pipeline 397224 exposes a pipeline-resource link to AndroidBuildBroker1ES, Scout
  matches that resource to `state.pipeline_runs.final_auth.authenticator_build_id`.
- Until that resource link exists, Scout falls back to the newest pipeline-397224 run on
  the Authenticator release branch.
- After Release Sign Off starts, every downstream stage is pinned to the build ID in
  its completed prerequisite's durable run link; a newer pipeline run cannot redirect
  Upload Alpha, Verify Alpha, Upload What's New, or Beta.
- Each checked command binds the selected build id, canonical stage identifier and current
  stage state in the review hash before it sends ADO's Run-stage request.

Run the follow-up with the matching command and fixed automation identity:
`start-release-signoff --executor release-signoff-automation`,
`start-upload-alpha --executor upload-alpha-automation`, or
`start-upload-whats-new --executor upload-whats-new-automation`, always with
`--release <id> --execute --auto-approve`.
If the run or stage cannot be found, the step blocks for release-owner investigation
instead of starting a different run.

## `beta_play_store` — owner-approved 100% Beta Play Store start (`Progress_Beta_100`)

This is the final Phase-5 step. It targets the same deterministically selected
pipeline-397224 run, starting canonical stage `Progress_Beta_100` (`100% Beta - Play Store`)
only after `verify_alpha_uploaded` and `upload_whats_new` complete AND the release owner
reviews the exact build/stage plan and approves its review hash. It is deliberately
excluded from the scheduled auto-writer allowlist. Its target build is parsed from the
durable `upload_whats_new` (or prior stage) completion link, so a newer pipeline run cannot
redirect Beta.

Run `start-beta-play-store --release <id>` to preview. Show the preview with fresh
status and ask the release owner Approve/Deny. Approval executes the same request with
`--execute --review-hash <hash> --approved-by <state.owner_email>`. The command rejects
any other reviewer, verifies that owner is signed in to Azure CLI, and rejects
`--auto-approve` or execution-time `--as-of`.

**Friday policy:** determine Friday in the release owner's timezone. A release cannot
start on Friday by default. Keep the step held and tell the owner why. Only after the
owner confirms manager approval may Scout accept the manager identity via
`step-action ... --param manager_approved_by=<manager>` and pass the identical value as
`--manager-approved-by <manager>` in both preview and execution. The date and manager
identity are bound into the review hash. This is an authenticated release-owner
attestation; the manager is not separately authenticated or stored as a two-party receipt.
