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
