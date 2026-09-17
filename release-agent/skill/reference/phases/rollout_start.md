# Reference — Phase 5 (Rollout Start)

## `notice` — initial Authenticator rollout email

`rollout_start.notice` is a real Scout notification, not a dummy. It sends to
`MAuthenticatorRel@microsoft.com` and CCs `windevxeng@microsoft.com` (Intune AOSP).
Use the shared notification prepare/claim/result protocol; previewing is not permission
to send. The `send_to` test mock redirects all delivery to the supplied address and
clears the production CC.

The model is deterministic and source-only:

- App version/commit source: newest successful AndroidBuild-1ES definition 355246 run
  on `state.versions.authenticator`, with its numeric `N.N.N` build tag.
- Release-build link: the exact Authenticator RC build from
  `state.pipeline_runs.rcs[-1].auth.build.run_id` (definition 475778), never the
  separately queried definition-355246 run.
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

Missing a successful final build/version, recorded definition-475778 RC build, exact
manifest, Authenticator suite, payload-page link, or SDK version blocks the step. An RC build,
failed final build, branch date, or pipeline build number is never an app-version source.
