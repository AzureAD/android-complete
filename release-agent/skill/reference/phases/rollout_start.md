# Reference — Phase 5 (Rollout Start)

## `identify_auth_build` — final Authenticator build/version capture

`rollout_start.identify_auth_build` is the first Phase-5 step. It reads pipeline
475778 (`AndroidBuildBroker1ES`) on `state.versions.authenticator` (`release/YYYY/MM/DD`)
and records the latest completed successful run into `state.pipeline_runs.final_auth`.

If no run is found, the step blocks for release-owner investigation; the release digest
contacts the owner by email and Scout. This step is the single source for the build id,
app version, build number and built commit used by tag, payload and rollout notice.

## `notice` — initial Authenticator rollout email

`rollout_start.notice` is a real Scout notification, not a dummy. It sends to
`MAuthenticatorRel@microsoft.com` and CCs `windevxeng@microsoft.com` (Intune AOSP).
Use the shared notification prepare/claim/result protocol; previewing is not permission
to send. The `send_to` test mock redirects all delivery to the supplied address and
clears the production CC.

The model is deterministic and source-only:

- App version/commit source: `state.pipeline_runs.final_auth`, captured by
  `rollout_start.identify_auth_build` from pipeline 475778.
- Release-build link: the exact `state.pipeline_runs.final_auth.authenticator_build_id`.
- Release-branch link: the Authenticator repository contents view pinned to the exact
  `state.versions.authenticator` branch.
- Authenticator and DID payload: commits reachable from that exact built commit since
  the previous successful dated release build. PR entries use the canonical ADO PR title
  from the PR record rather than the merge-commit message; non-PR commits preserve their
  commit title. Preserve source links. Any entry attributed to DID appears only in DID,
  even when it also changed non-DID paths.
- DID classification: canonical `VerifiableCredential-Wallet`,
  `VerifiableCredential-SDK`, `WalletLibrary`, and FaceCheck-extension paths.
- Generated OneLoc/LEGO localization commits are omitted.
- Feature flags: exact `EcsFlight.kt` additions and code-default changes. These are
  **not rollout intent**; default-true additions require owner review.
- Authenticator test suite, payload page and SDK versions: completed Phase 3-4 evidence.

Never infer Major/Minor classifications, expected rollout intent, Safe Fly approval,
or future progression dates. The email states when these have no
separate structured source.

Missing a successful final build/version from `final_auth`, exact manifest,
Authenticator suite, payload-page link, or SDK version blocks the step. A failed final
build, branch date, or arbitrary pipeline build number is never an app-version source.

## `signoff_start` — start Release Sign Off on pipeline 397224

`rollout_start.signoff_start` is a checked external write, not a human gate. It locates
the Android Build Release run in msazure/One pipeline 397224 for
`state.versions.authenticator`, starts the stage named `Release Sign Off`, then keeps
the step in-flight until ADO reports that stage has finished.

Selection is deterministic:

- When pipeline 397224 exposes a pipeline-resource link to AndroidBuildBroker1ES, Scout
  matches that resource to `state.pipeline_runs.final_auth.authenticator_build_id`.
- Until that resource link exists, Scout falls back to the newest pipeline-397224 run on
  the Authenticator release branch.
- The checked command binds the selected build id, stage identity and current stage
  state in the review hash before it sends ADO's Run-stage request.
- After the Run-stage request is accepted, the step is recorded as in-flight/pending
  with the exact build/stage in step data. It is not marked done at launch time.
- Subsequent `step-action --phase rollout_start --step signoff_start` polls the same
  stored run. Only a completed successful stage marks the step done; failed, skipped
  or canceled results block for owner investigation.

Run the follow-up with
`start-release-signoff --release <id> --execute --auto-approve --executor release-signoff-automation`.
If the run or stage cannot be found, the step blocks for release-owner investigation
instead of starting a different run.
