---
name: code-author
description: Author production-ready Android Auth implementations, regression telemetry, unit tests, and pull requests across Common, Broker, MSAL, ADAL, and Authenticator. Use this skill when implementing features, bug fixes, or PBIs—either directly in chat or inside the `code-feature` dynamic workflow. Enforces strict default-off flight isolation, OTel sampler synchronization, central cross-layer placement, Java/Kotlin ABI compatibility, and PR hygiene.
---

# Code Author

Author production-ready code, tests, telemetry, and pull requests across the Android Auth
repositories (`common`, `broker`, `msal`, `adal`, and `authenticator`) without avoidable
review or CI round-trips.

Read [implementation standards](references/implementation-standards.md) before writing or
modifying production code.

## Core Implementation Rules

### 1. Strict Flight Isolation (Zero Off-Path Side Effects)
- Gate every behavioral change behind a default-off flight (`CommonFlight`, `BrokerFlight`,
  or `EcsFlight`).
- **When the flight is `false`, the legacy path must execute with zero new side effects**:
  - Do not initialize new ViewModels/coordinators, alter navigation graphs, write new
    saved-state markers, or emit new diagnostic logs on the flight-off path.
  - Keep only the minimal flight-read check needed to select between the legacy path and the
    flighted path.
- Write unit tests for **both** `flight = true` and `flight = false`.

### 2. Regression-Attribution Telemetry & Sampler Synchronization
- Emit flight-gated, privacy-safe telemetry (health spans or bounded `SystemMetadata` /
  `AttributeName` keys) so new behavior, reuse/wait paths, and guard rejections can be
  attributed in Kusto.
- **Whenever you add or rename a `SpanName` or sampled operation in Broker or Common, update
  the OpenTelemetry sampler configuration and sampler expectation unit tests in the same
  commit** so CI never fails on sampler map mismatches.

### 3. Central & Cross-Layer Placement (Common vs. Broker vs. Client App)
- Trace all converging entry points before choosing where a fix or feature lives (e.g.,
  pre-created join, manual admin SDM join, ordinary non-SDM WPJ, silent vs. interactive token
  flows).
- Centralize shared allowlists, policy metadata (such as `AppFamily`), and flight checks in
  the shared layer (`Common` or the central `Broker` handler) instead of duplicating
  validators per caller.
- When a bug originates in a client/UI layer (such as Activity recreation in Authenticator)
  but can corrupt state or trigger duplicate network/ADRS calls in `Broker`, enforce a
  defensive guard in `Broker` as well—tied to the actual worker completion, not just caller
  timeout.

### 4. Java/Kotlin ABI, Input Normalization & License Headers
- **Binary & source compatibility**:
  - Preserve existing Java-callable constructors and method signatures. Use `@JvmOverloads`
    and `@JvmStatic` when adding default parameters or companion helpers consumed from Java.
  - Use `internal` or `@VisibleForTesting` for test-injection constructors rather than
    replacing public/package-private constructors.
  - **Same-module Kotlin + Lombok**: In mixed Java/Kotlin Gradle modules (`broker4j`,
    `common4j`), `compileKotlin` runs *before* `compileJava` / Lombok annotation processing.
    If new Kotlin code calls a Lombok-generated `@Getter` / `@Setter` / `@Builder` on a Java
    class in the *same* Gradle module, declare the Java accessor explicitly on that Java class
    so `compileKotlin` can resolve it.
- **Consistent input normalization & URL/URI parsing**:
  - Normalize string inputs (such as `tenantId`, `loginHint`, package names) consistently at
    entry boundaries using trimmed/non-blank checks (`trim()`, `isNotBlank()`) so routing and
    downstream handlers cannot disagree on whitespace-only values.
  - **JDK `URI` vs. `URL` host parsing**: `java.net.URI.getHost()` returns `null` when a
    hostname contains an underscore (`_`, common in enterprise ADFS servers like
    `adfs_server.contoso.com`), whereas `java.net.URL` parses it; conversely, reject raw
    backslashes (`\`) in authority/path before host comparison to prevent parser-differential
    bypasses between `URL` and WebView/RFC 3986.
- **Static analysis (PMD & SpotBugs) hygiene**:
  - Avoid PMD-flagged constructs (collapsible `if` statements, unused local variables/imports)
    and annotate intentional broad catches with `@SuppressFBWarnings` + a clear `justification`.
- **License headers**:
  - Add the full repository license header to every new file (full MIT header in `common`,
    `common4j`, `msal`, `adal`; Microsoft copyright header in `broker`).
- **Meaningful unit tests & diff coverage**:
  - Test the real runtime boundary and state transitions (never tautological assertions on
    pre-initialized constants). Ensure all new branches and overloads are covered so CI diff
    coverage (>= 80%) passes on the first run.

### 5. Pull Request & Branch Hygiene
- **Branch naming**: Use `<alias>/<feature-or-fix-slug>` (e.g.,
  `shjameel/island-browser-sso-identities`, or `users/<alias>/<slug>` in ADO repos)—never
  leave random worktree-generated branch names on published PRs.
- **Changelog & PR labels**:
  - Update `CHANGELOG.md` under `vNext` (`MINOR` for additive internal/feature changes with
    no breaking API impact, `PATCH` for bug fixes).
  - Apply the matching `changelog:minor` or `changelog:patch` label on the PR so the
    `Changelog Check` pipeline passes immediately.
- **Submodule & base branch sync (fast-forward only, never force-push)**:
  - Merge `origin/dev` via `git merge origin/dev` and push incremental commits fast-forward
    (`git merge-base --is-ancestor <old> <new>`)—never `git commit --amend` or `git push --force`
    on an open PR, which breaks reviewer diff history and GitHub's merge ref.
  - **Release-cut `vNext` changelog trap**: After merging `origin/dev`, check `CHANGELOG.md` /
    `changes.txt`—if `dev` cut a release while the PR was open, move your PR's changelog bullet
    back under the new `vNext` heading so `Changelog Check` passes.
  - **Delete/modify conflict on retired flights**: If `origin/dev` deleted an adjacent retired
    flight enum entry, keep the retired flight deleted and retain only your new flight entry.
  - When a feature spans `Common` and `Broker`, only point `Broker`'s `common` submodule to a
    committed and pushed `Common` revision.
- **PR creation & inline thread replies via `gh` CLI**:
  - For `AzureAD/*` repos on `github.com`, use `gh pr create` and post inline review replies
    (`/pulls/<id>/comments/<comment_id>/replies`) with the developer's public GitHub account
    (`shahzaibj` from `.github/developer-local.json`) rather than relying on EMU-bound
    `shjameel_microsoft` tokens that return `403 Forbidden`.
  - For `msft.ghe.com/security/ad-accounts-for-android` (`broker`), always use `gh pr create` /
    `gh pr edit` / `gh api --hostname msft.ghe.com` with the GHE account (`shjameel`)—never
    use the app's built-in `create_pull_request` / `update_pull_request` tools on GHE repos
    (they target `github.com` and return `404 Not Found`).
  - Normalize new `.kt` / `.java` files to LF (`\n`) line endings so `git diff --check` and
    rebases onto `origin/dev` pass without CRLF whitespace warnings.
  - Include `Fixes AB#<workItemId>` in both the PR title and body when a work item exists.
- **Local Gradle vs. CI pipeline verification**:
  - Always try to run targeted Gradle tests locally first (scoped with `--tests "<Pattern>"`;
    never run unfiltered `:AADAuthenticator:testLocalDebugUnitTest`). In `common`, `broker`, `msal`, and
    `adal`, `settings.gradle` reads `ENV_VSTS_MVN_CRED_USERNAME` and
    `ENV_VSTS_MVN_CRED_ACCESSTOKEN`; if they are not already set in the environment, try
    setting `$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"` (when present),
    `$env:ENV_VSTS_MVN_CRED_USERNAME = "vsts"`, and
    `$env:ENV_VSTS_MVN_CRED_ACCESSTOKEN = (az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv)`
    before invoking `.\gradlew.bat`. Also ensure `local.properties` (`sdk.dir`) is present in
    any isolated sub-repo worktree (copied from the main checkout or `%LOCALAPPDATA%\Android\Sdk`).
  - Only if a local VSTS token or Android SDK is still unavailable on the machine, do not block
    implementation or code review—record the targeted Gradle test commands in the PR test plan
    so CI pipelines (and any PR observer) execute them, and proceed with thorough code and
    unit-test review.
