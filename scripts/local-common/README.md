# Test local Common changes with OneAuthTestApp (via Maven Local)

Build and run **OneAuthTestApp** against **locally-modified Common** (`common` + `common4j`)
without publishing to any feed. Maven Local (`~/.m2`) is the hand-off: you publish your local
Common there, then point OneAuthTestApp at that version.

This is the same "Maven local" workflow used previously — captured here as repeatable scripts.

---

## How it works

```
common / common4j  --(publishToMavenLocal)-->  ~/.m2  --(mavenLocal via init script)-->  OneAuthTestApp (:OneAuth)
                         unique local version                 -PandroidCommonVersion=<ver>
```

1. **Publish** local Common to Maven Local under a unique version (e.g. `0.0.0-local-<timestamp>`).
2. **Inject** `mavenLocal()` into the OneAuthTestApp build with a Gradle **init script**
   (`oneauth-mavenlocal.init.gradle`) — this avoids editing the OneAuth repo (owned by another team).
3. **Override** the Common version the OneAuth SDK asks for with `-PandroidCommonVersion=<ver>`
   (a hook that already exists in the OneAuth SDK build).

Because the version is local-only and unique, Gradle can only get it from Maven Local, so you get an
unambiguous, obvious-in-logs artifact.

---

## Prerequisites

- **JDK 17** (default: `C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot`). Override with `-JavaHome`.
- Global Maven feed credentials in `~/.gradle/gradle.properties` (`vstsUsername`, `vstsMavenAccessToken`)
  — already required for any normal build here.
- The **main checkout** `…\android-complete` with the `common` sub-repo cloned and your changes on it.
  > These scripts live in a git **worktree** (`android-complete.worktrees\…`) whose sub-repos are not
  > cloned. They auto-detect the sibling main checkout; override with `-SuperprojectRoot` if needed.

---

## Quick start

From `scripts\local-common\`:

```powershell
# 1) Publish your local Common to Maven Local (fresh timestamped version, recorded for step 2)
.\publish-common-to-maven-local.ps1

# 2a) Fast check — prove OneAuthTestApp resolves your local Common (no NDK/CMake needed)
.\build-oneauthtestapp-with-local-common.ps1 -VerifyOnly

# 2b) Full build + install on the connected device/emulator against your local Common
.\build-oneauthtestapp-with-local-common.ps1
```

`publish-…` writes the version it used to `.last-published-version`; `build-…` reads it automatically,
so you don't have to copy/paste the version between steps.

---

## Scripts

### `publish-common-to-maven-local.ps1`
Publishes, **in the required order**, from the android-complete superproject:
1. `:common4j` → `com.microsoft.identity:common4j:<Version>`
2. `:common`   → `com.microsoft.identity:common:<Version>` (the **dist release** publication),
   with its POM pinned to the same `common4j` version via `-PdistCommon4jVersion`.

Params: `-Version` (default `0.0.0-local-<timestamp>`), `-SuperprojectRoot`, `-JavaHome`.

### `build-oneauthtestapp-with-local-common.ps1`
Runs the OneAuthTestApp build with `--init-script oneauth-mavenlocal.init.gradle` and
`-PandroidCommonVersion=<Version>`. The default task `:app:installDebug` builds the debug APK and
**installs it on the connected device/emulator** (a device/emulator must be attached).

Params: `-Version` (default = last published), `-Task` (default `:app:installDebug`; use
`:app:assembleDebug` to build only, no install), `-VerifyOnly` (dependency check, implies
`-SkipNativeBuild`), `-SkipNativeBuild`, `-SuperprojectRoot`, `-JavaHome`, `-GradleArgs`
(passthrough, e.g. `--refresh-dependencies`).

### `oneauth-mavenlocal.init.gradle`
Init script that adds `mavenLocal()` to every project of the OneAuthTestApp build. Non-invasive —
nothing in the OneAuth checkout is modified.

---

## Manual commands (if you prefer not to use the scripts)

Publish (from `…\android-complete`, `JAVA_HOME` = JDK 17):

```powershell
$ver = "0.0.0-local-mytest1"
.\gradlew.bat :common4j:publishToMavenLocal -PprojVersion=$ver -PincludeAuthenticatorApp=false
.\gradlew.bat :common:publishDistReleasePublicationToMavenLocal `
    -PprojVersion=$ver -PdistCommon4jVersion=$ver -PincludeAuthenticatorApp=false
```

Consume (from `…\android-complete\oneauth\testapps\android\OneAuthTestApp`, `JAVA_HOME` = JDK 17):

```powershell
$init = "…\scripts\local-common\oneauth-mavenlocal.init.gradle"
# fast resolve check
.\gradlew.bat :OneAuth:dependencyInsight --configuration debugRuntimeClasspath `
    --dependency com.microsoft.identity:common `
    -PandroidCommonVersion=$ver -PskipNativeBuild=true --init-script $init --console=plain
# full build + install on the connected device
.\gradlew.bat :app:installDebug -PandroidCommonVersion=$ver --init-script $init
```

A successful resolve check shows (no `FAILED` lines):

```
com.microsoft.identity:common:0.0.0-local-mytest1
  Variant distReleaseRuntimeElements-published:
com.microsoft.identity:common4j:0.0.0-local-mytest1
\--- com.microsoft.identity:common:0.0.0-local-mytest1
```

---

## Gotchas (learned the hard way)

- **Publish from the superproject, not standalone `common\`.** The standalone `common\settings.gradle`
  uses `project.findProperty(...)` for feed creds inside `pluginManagement`, which only works in CI
  (env vars). The superproject `settings.gradle` reads creds from `~/.gradle/gradle.properties`.
- **`-PincludeAuthenticatorApp=false` is required** for the Common publish: the main checkout's
  `gradle.properties` sets it `true`, but the `authenticator\` repo isn't cloned, so the build
  fails fast without the opt-out.
- **Order matters:** publish `common4j` **first**, then `common`, in **separate** invocations. The
  `common` *dist* flavor resolves `common4j` as an external Maven artifact (no project substitution),
  so it must already be in Maven Local.
- **Use a fresh/unique version each run** (the default timestamp does this) to dodge Gradle caching of
  a previously-seen version. The OneAuth build already sets `cacheChangingModulesFor(0, ...)`, but a
  new version is the simplest guarantee.
- **`-PskipNativeBuild=true`** skips the OneAuth CMake/NDK native build — great for a quick
  resolve/compile check, but **omit it for a real functional run** of the app (the app needs the
  native lib at runtime).
- **JDK 17** is required for these builds.
- **Not our repo:** OneAuth is owned by another team; the init-script approach keeps that checkout
  untouched. If OneAuth ever adds `RepositoriesMode.FAIL_ON_PROJECT_REPOS`, the init script would
  need to switch to a settings-level injection instead.
