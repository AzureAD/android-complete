# Reference — Release Orchestrator pipeline (def 2828)

Source of truth: `AuthClientAndroidPipelines` @ `/production/monthly-release/release-orchestrator.yml`
(org `identitydivision.visualstudio.com`, project `Engineering`). Templates under
`/templates/release/`. This doc is a documentation snapshot — always defer to the live YAML.

The orchestrator is the spine of Phase 2 (build/verify) and Phase 4 (finalize). Several
release-agent steps observe or approve its stages; this page records the stage order, the two
manual gates, and the branch/PR model so those steps can't drift from reality.

## Stages (in dependency order)

| # | Stage (displayName) | dependsOn | Notes |
|---|---------------------|-----------|-------|
| 1 | Validate Branch and Versions availability | — | |
| 2 | Create Release Branches | 1 | `create-branches.yml`; creates release/ + working-release/ for common/msal/broker AND the Authenticator (unless `debugSkipAuthenticatorBranch`). |
| 3 | Trigger RC Testing | 2 | fires MRWP (def 2519) twice (ECS + Local). Verified by Phase-2 `mrwp_ecs`/`mrwp_local`. |
| 4 | **Remove RC Tags** 🚦 | 3 | **1st manual gate** — approved by `gate_watch`. On approval the publish stages run. |
| 5 | Publish Internal | 4 | internal artifacts → ADO Maven feed. |
| 6 | Publish to Maven Central | 5 | MSAL/Common → Maven Central. Verified by `verify_pub`. |
| 7 | Update Pipeline Variables | 5 | MSAL-PROD-Version, MSAL-PROD-BRANCH, Broker-PROD-Version. |
| 8 | Create Release Integration Branches | 5 | cuts `release-integration/*` (see branch model). |
| 9 | **Publish GitHub Release Notes** 🚦 | 8 | **2nd manual gate** — approved by `publish_notes_gate`. Publishes `v<version>` GitHub releases for Common, MSAL, Broker. Verified by `verify_release_notes`. |

Two manual approval gates: **Remove RC Tags** (stage 4) and **Publish GitHub Release Notes**
(stage 9). `approve-orchestrator-gate` dispatches to whichever gate step is holding.

## Repositories the orchestrator drives
Declared as pipeline `resources.repositories` (all GitHub/GHE):
- `common` — github.com/AzureAD/microsoft-authentication-library-common-for-android (ref dev)
- `msal`   — github.com/AzureAD/microsoft-authentication-library-for-android (ref dev)
- `broker` — msft.ghe.com/security/ad-accounts-for-android (ref dev)
- `android-complete` — github.com/AzureAD/android-complete (ref master)

The **Authenticator** repo (`msazure/One AD-MFA-phonefactor-phoneApp-android`) is NOT a pipeline
resource — the orchestrator reaches it by QUEUEING pipelines in msazure/One (e.g. the Cut Branch
pipeline **467780**) via a service connection.

## Branch model (per release version `<v>`; auth uses the date branch `YYYY/MM/DD`)

| Role | Libs (common/msal/broker) | Authenticator |
|------|----------------------------|---------------|
| release | `release/<v>` | `release/YYYY/MM/DD` |
| working-release | `working/release/<v>` | `working-release/YYYY/MM/DD` (hyphen prefix) |
| release-integration | `release-integration/<v>` | `release-integration/YYYY/MM/DD` |
| mainline (integration target) | `dev` | `working` |

Prefixes come from the YAML: libs use `working/release/` + `release/`; the auth working-release is
`working-<authenticatorReleaseBranch>` → `working-release/YYYY/MM/DD`.

### How the release-integration branches are cut (stage 8)
- **Libs:** `create-release-integration-branches.yml` checks out common/msal/broker, then for each
  creates `release-integration/<version>` from the current `working/release/<version>` and pushes
  it (idempotent — skips if the remote branch already exists). It also PRINTS the GitHub PR compare
  links (freeze + integration) for the engineer.
- **Authenticator:** the `CreateAuthenticatorReleaseIntegrationBranch` job computes the target as
  `release/… -> release-integration/…`, then QUEUES msazure/One pipeline **467780** with
  `sourceBranch = working-release/YYYY/MM/DD`, `targetBranch = release-integration/YYYY/MM/DD` to cut
  the branch in the auth repo, and prints the ADO PR-create link `release-integration/… -> working`.

## The 8 integration/freeze PRs (opened by `integ_prs`, 2 per repo)
For each repo the engineer (or `integ_prs`) opens:
- **FREEZE**: `working-release/<v>` → `release/<v>` (direct merge — freezes the release branch).
- **INTEGRATION**: `release-integration/<v>` → mainline (`dev` for libs, `working` for auth).

The auth **INTEGRATION** PR (`release-integration/YYYY/MM/DD → working`) is what reflects the
release's final version bumps + cherry-picks back into the auth mainline — i.e. the automated
equivalent of the old manual checklist "Phase 4 Step 9" (merge Auth App release into working). It is
NOT a separate release-agent step; the orchestrator cuts the branch and `integ_prs` opens the PR.

## Gotcha — stage timing when observing a live run
The release-integration branches (and thus the auth `release-integration/…` branch) do NOT exist
until stage 8 runs, which is AFTER the Remove RC Tags gate (stage 4). A run parked at 'Remove RC
Tags' will show NO release-integration branches yet — that's expected, not a bug. Don't conclude a
branch is missing from a run that hasn't advanced past its gate.

## Checklist mapping (combined-release-checklist Phase 4)
| Checklist Step | Orchestrator / release-agent |
|----------------|------------------------------|
| 1 Remove RC Tags gate | stage 4 · `gate_watch` |
| 2 integration PRs | stage 8 (branches) · `integ_prs` (opens PRs) |
| 3 OneAuth Common ingestion | `oneauth_common_pr` |
| 4 Publish GitHub Release Notes gate | stage 9 · `publish_notes_gate` |
| 5 verify Maven Central + GitHub | `verify_pub` (Maven) + `verify_release_notes` (GitHub) |
| 6 final broker email + Teams announce | `release_announcement` (Teams); the broker EMAIL is a broader comms item, tracked separately |
| 7 Auth App gradle bump → build | orchestrator-automated (checklist text is stale) |
| 8 tag Auth App release commit | `tag_authenticator` |
| 9 merge Auth App release → working | stage 8 auth job + `integ_prs` auth integration PR (checklist text is stale) |
