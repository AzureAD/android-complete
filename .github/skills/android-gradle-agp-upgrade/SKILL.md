---
name: android-gradle-agp-upgrade
description: >
  Upgrade an Android Gradle project's Gradle wrapper, Android Gradle Plugin (AGP),
  and compileSdk/targetSdk (Android API level) safely and in the correct order.
  Includes a verification workflow to run after each change and a categorized,
  growing catalog of real upgrade errors with fixes. USE WHEN: upgrade Gradle,
  upgrade AGP, bump compileSdk/targetSdk, "Android API 37", "AGP 9", "Gradle 9",
  gradle-wrapper.properties distributionUrl, build fails after version bump,
  deprecation/removal errors after Gradle upgrade, DSL removed in AGP.
  DO NOT USE FOR: writing app feature code, non-Android Gradle projects.
---

# Android Gradle / AGP / SDK Upgrade

Guidance for upgrading an Android project across three coupled versions:
**Gradle** (build engine), **AGP** (Android Gradle Plugin), and **compileSdk /
targetSdk** (Android API level). Use this together with the verification workflow
and the categorized error catalog below.

## Golden rules (read first)

1. **Upgrade in dependency order: Gradle → AGP → compileSdk/targetSdk.**
   This is both the real compatibility chain AND Google's documented sequence.
   - AGP has a **hard minimum Gradle version** — a new AGP will not run on an old
     Gradle. So Gradle must move first (or together with AGP).
   - AGP **gates the maximum supported `compileSdk`** — bumping `compileSdk` to a
     new API level on an old AGP only produces a "compiled against a newer SDK
     than this AGP supports" warning/error. So AGP must be new enough before the
     SDK bump.
   - Gradle is generally **backward-compatible with older AGP**, so bumping Gradle
     alone is the lowest-risk single step and flushes out pure-Gradle removals
     before AGP changes any behavior.

2. **NEVER change `minSdkVersion` (minSdk) as part of a Gradle/AGP/SDK upgrade.**
   `minSdk` defines the oldest OS the app still supports; raising it silently drops
   real users/devices and is a product decision, not a build-tooling change. It is
   NOT required by any Gradle, AGP, or compileSdk upgrade. Leave `minSdk` exactly
   as-is unless the user explicitly and separately asks to change it. If a build
   error seems to "want" a higher minSdk, find the real cause (a dependency raised
   its own minSdk) and pin/adjust that dependency instead of raising the app minSdk.

3. **Change only the bare-minimum version fields** required for the upgrade. Do not
   refactor source, bump unrelated dependencies, or "improve" code unless an error
   strictly requires it.

4. **One version bump at a time, then verify.** Isolating each change keeps error
   categorization (A/B/C/D below) accurate.

5. **Always name the underlying dependency and give a suggested fix.** Whenever a
   failure is caused by a dependency or plugin (directly declared OR bundled
   transitively inside another plugin), do NOT stop at the raw stacktrace. Trace it
   to the responsible artifact and report to the user, explicitly:
   - **What the underlying dependency is** — group:artifact:version and, if it is
     bundled, the parent plugin that pulls it in (e.g. "SpotBugs `4.7.1`, bundled
     transitively by `com.microsoft.identity.buildsystem`").
   - **Why it fails** — the specific removed/changed API and the version boundary
     (e.g. "calls `JavaPluginConvention`, removed in Gradle 9").
   - **Where the version is controlled** — the file/POM that actually pins it, and
     whether any repo `ext` var is a red herring that does NOT control it.
   - **A concrete suggestion if applicable** — the specific compatible version /
     coordinate change / upstream action that would resolve it, ordered lowest-risk
     first. If no fix is known or the failing version is already the latest, say so
     plainly rather than guessing.
   Surface this even when the fix lives outside the current repo, so the user can
   act (e.g. hand it to the plugin's owning team).

6. **When presenting a decision (Option A vs B), give the human enough context to
   actually decide — never just the choice.** For each option include:
   - **What the thing is / does** in plain language (assume the user may be new to
     Android/Gradle) — e.g. what a flag toggles, what a plugin/DSL block produces.
   - **Where it's used in THIS codebase** — grep for every occurrence and list them
     (which modules, how many call sites, whether any are cosmetic vs functional).
     Don't assume a single occurrence; there are often several.
   - **Impact on artifacts/release/consumers** — does it change the published
     `.aar`/`.jar`, POM, javadoc/sources jars, CI pipeline steps, or downstream
     repos? Explicitly say when the real impact is low (e.g. a generated artifact
     that the `publishing {}` block never actually attaches) and how you verified.
   - **Longevity / risk** — is an opt-out a permanent fix or a stopgap removed in a
     future major (e.g. AGP 10)? What breaks later if we defer?
   - **A recommendation with reasoning**, then ask the user to choose. Do NOT apply a
     hard-to-reverse or behavior-changing option without sign-off.

## Verification workflow (run after EACH change)

Run these from the project root (Windows `.\gradlew.bat`, macOS/Linux `./gradlew`),
fastest first, adding `--stacktrace` (and `--info` when needed) to trace failures:

1. `.\gradlew.bat --version` — confirm the wrapper picked up the new Gradle and
   check the reported JDK (AGP 9 / Gradle 9 need **JDK 17+**).
2. `.\gradlew.bat help` — evaluates settings + plugins with no compilation; catches
   plugin/script incompatibilities and Gradle API removals early.
3. `.\gradlew.bat :<module>:assembleDebug` — compiles the library/app; catches
   **SDK API changes** (category A).
4. `.\gradlew.bat build -x test` — broadest; catches lint/AGP DSL issues (category B).

**In Android Studio:** "Sync Project with Gradle Files" (elephant icon) ≈ step 2;
`Build → Make Project` (Ctrl+F9) ≈ steps 3–4. Ensure `Settings → Build Tools →
Gradle → Gradle JDK` is **17+** for AGP 9 / Gradle 9. Also confirm the **Android Studio
version itself supports the target AGP** — a too-old Studio refuses to sync a newer AGP;
and do NOT let the AGP Upgrade Assistant auto-rewrite files if you're doing the bump
manually (it will fight your edits).

**`help` is configuration ONLY — it is NOT proof the project builds.** Distinguish the
three levels and don't declare success early:
- **Configures** = `.\gradlew.bat help` / `:<module>:help` succeeds (settings + plugins +
  scripts evaluate). This is where most Gradle/AGP DSL removals surface.
- **Compiles** = `.\gradlew.bat :<module>:assembleDebug` (or `assemble<Flavor><BuildType>`)
  succeeds — Java + Kotlin actually compile against the new SDK (Category A shows here).
- **Tests pass** = the `test*`/unit-test tasks run green (runtime/classpath issues, e.g.
  Robolectric, only appear here).
Always push at least to `assemble` (and ideally tests) before calling an upgrade done.

## When a missing config/param blocks tests — ASK the user (don't fake it)

If tests fail (often a WHOLE class/suite erroring identically) because of a **missing
external input** the harness needs — a mock-API URL, a config/JSON string, a test
property, an endpoint, a test account id — and you cannot find the value in the repo
(it typically lives in CI variables, not source), **use the `vscode_askQuestions` tool
to ask the human for the value**, then re-run the tests with it. Don't invent a
placeholder URL and don't declare the tests broken by the upgrade before you've tried
the real input.

- **OK to request via askQuestions** (non-secret harness inputs): mock-API base URL,
  a native-auth config string, test account emails / client-ids / authority URLs,
  robolectric SDK version, feature-flag toggles.
- **NEVER request via askQuestions** (secrets): passwords, tokens, PATs, private keys,
  certificates (e.g. `LabVaultAppCert`, `vstsMavenAccessToken`). Secrets must be typed
  by the user directly into their own terminal or global `~/.gradle/gradle.properties`,
  never routed through the model. If a suite needs one of these, say so and stop.
- **Where CI gets these** (Azure DevOps example): a pipeline `variables: - group: <name>`
  (Library → Variable groups). In this repo the values live in the **`devex-ciam-test`**
  group, injected as `-PmockApiUrl=$(MOCK_API_URL) -PnativeAuthConfigString=$(NATIVE_AUTH_CONFIG_STRING) -PlabSecret=$(LabVaultAppCert)`. Point the user to Pipelines → Library →
  that group (or a teammate who owns it) if they don't have the value handy.
- **Passing large multi-line values without hanging the shell**: prefer ONE quoted `-P`
  argument on a SINGLE line. In PowerShell wrap the whole arg in DOUBLE quotes
  (`"-PnativeAuthConfigString=..."`) — these configs use single quotes and contain no
  `$`, so nothing gets mangled. Do NOT use a `@'...'@` here-string interactively: a
  mis-pasted here-string leaves PowerShell at a `>>` continuation prompt that LOOKS
  frozen (press Ctrl+C to recover). Alternatively put the value as ONE line in
  `gradle.properties`, run, then revert it (don't commit lab/test fixtures).

## Where versions live (typical)

- **Gradle**: `gradle/wrapper/gradle-wrapper.properties` → `distributionUrl` (the
  `gradle-X.Y.Z-all.zip` token). Each nested/composite build may have its own wrapper.
- **AGP**: `com.android.tools.build:gradle:<version>` in the buildscript classpath,
  or a version-catalog / `ext` variable. NOTE: a project may alias this under a
  confusingly named variable (e.g. `gradleVersion` used for the AGP classpath).
- **compileSdk / targetSdk / buildToolsVersion**: module `build.gradle(.kts)` or a
  shared `versions.gradle` / `ext { }` block.
- **Kotlin Gradle plugin (KGP)**: may be a `buildscript` classpath dep
  (`org.jetbrains.kotlin:kotlin-gradle-plugin:<ver>`) driven by an `ext` var such as
  `kotlinVersion`, OR resolved via the `plugins {}` DSL. Note the SAME var often also
  drives `kotlin-stdlib` app dependencies, so bumping it is not purely a build-tooling
  change.

## Gradle ↔ Kotlin compatibility (quick reference)

Each Gradle version is tested against a specific Kotlin Gradle plugin (KGP) range.
An old KGP on a new Gradle fails at plugin-apply time (removed Gradle APIs). Always
check the version needed for the TARGET Gradle before bumping. Source of truth:
https://docs.gradle.org/current/userguide/compatibility.html#kotlin

| Kotlin (KGP) | Tested with Gradle |
| --- | --- |
| 1.8.10 / 1.8.20 | 8.0 / 8.2 |
| 1.9.24 | 8.10 |
| 2.0.20 / 2.0.21 | 8.11 / 8.12 |
| 2.2.0 | 9.0.0 |
| 2.2.20 | 9.2.0 |
| 2.2.21 | 9.3.0 |
| 2.3.0 | 9.4.0 |

Key boundary: **Kotlin 1.8.x/1.9.x do NOT support Gradle 9** (KGP 2.2.0 is the first
Gradle-9-capable release). Prefer the KGP row matching the target Gradle minor (e.g.
Gradle 9.3.x → KGP 2.2.21). Moving 1.x → 2.x also brings the K2 compiler and
kotlin-stdlib 2.x — watch for `strictly` stdlib constraints on other libraries.

## Error categorization scheme

Tag every distinct error encountered during an upgrade as exactly one of:

- **A — SDK API change**: symbol removed/changed/deprecated in the new Android API
  (surfaces when compiling against the new `compileSdk`).
- **B — AGP behavior change**: caused by the new AGP (removed/renamed DSL, changed
  defaults, variant API changes, new required config, min Gradle/JDK/Kotlin bumps
  demanded by AGP). Sub-tag `B-gradle` for pure-Gradle removals surfaced by the
  Gradle upgrade itself (not AGP).
- **C — Pre-existing issue**: unrelated to the upgrade; the bump merely exposed it.
- **D — Unknown**: not confident yet; needs more investigation.

## Error catalog (append real cases here as they occur)

Format per entry:

```
### <short title>
- Category: A | B | B-gradle | C | D
- Trigger: <which version bump surfaced it>
- Symptom: <exact error text / signature>
- Root cause: <traced cause>
- Fix (lowest risk first): <concrete change>
- Verify: <command that should now pass>
```

### `project.findProperty` used inside `settings.gradle` credentials block
- Category: B-gradle
- Trigger: Gradle 8.x → 9.x upgrade
- Symptom: `groovy.lang.MissingPropertyException: Could not get unknown property
  'project' for Credentials ... DefaultPasswordCredentials_Decorated` at settings
  evaluation (e.g. `settings.gradle` line with `project.findProperty(...)` inside a
  `pluginManagement { repositories { maven { credentials { ... } } } }` block).
- Root cause: A settings script has no `project` object (that exists only in
  `build.gradle`). Older Gradle tolerated/deferred this; Gradle 9 eagerly evaluates
  the credentials closure during settings evaluation, so the missing property fails.
- Fix (lowest risk first): Replace `project.` with the settings-level provider API
  DIRECTLY inside the credentials closure (do NOT hoist `def` vars above the
  `pluginManagement {}` block — that block MUST be the first statement in a settings
  file, else you get `The pluginManagement {} block must appear before any other
  statements in the script`). Working form:
  `username System.getenv("X") != null ? System.getenv("X") : providers.gradleProperty("vstsUsername").getOrNull()`
  `password System.getenv("Y") != null ? System.getenv("Y") : providers.gradleProperty("vstsMavenAccessToken").getOrNull()`
  `providers` resolves in a settings script; only `project` does not. The same
  `project.findProperty` pattern inside `build.gradle` buildscript/allprojects is
  fine — do NOT change those.
- Verify: `.\gradlew.bat help --stacktrace` configures without the MissingPropertyException.

### 401 Unauthorized fetching artifacts from a private Maven feed after upgrade
- Category: C (pre-existing environment/auth; upgrade only exposed it)
- Trigger: Gradle 8.x → 9.x upgrade (also any wrapper/plugin bump that invalidates cache)
- Symptom: `HttpErrorStatusCodeException: Could not GET '<private-feed>/.../*.jar'.
  Received status code 401 from server: Unauthorized` (e.g. kotlin-scripting-common
  from an Azure DevOps `pkgs.visualstudio.com` feed).
- Root cause: The feed credentials (`vstsUsername` / `vstsMavenAccessToken` or
  equivalent) resolve to null — no PAT set and no `ENV_VSTS_*` env vars — so requests
  are unauthenticated. A warm Gradle cache previously hid this; the new Gradle ships
  a new Kotlin and re-resolves the plugin classpath, forcing fresh downloads that now
  hit the feed.
- Fix (lowest risk first): The USER adds their PAT (Packaging→Read scope) to the
  GLOBAL `~/.gradle/gradle.properties` (`C:\Users\<user>\.gradle\gradle.properties`
  on Windows), matching the exact property names the scripts look up:
  `vstsUsername=<any-non-empty>` and `vstsMavenAccessToken=<PAT>`. NEVER ask for or
  handle the PAT yourself — secrets must be typed by the user directly, and kept in
  the global (not repo) gradle.properties so they aren't committed.
- Verify: `.\gradlew.bat help --stacktrace` resolves the plugin classpath without 401.

### `NoClassDefFoundError: JavaPluginConvention` from a plugin bundled inside another plugin
- Category: B-gradle
- Trigger: Gradle 8.x → 9.x upgrade
- Symptom: `java.lang.NoClassDefFoundError: org/gradle/api/plugins/JavaPluginConvention`
  during project configuration, top of stack in a third-party plugin (here
  `com.github.spotbugs.snom.internal.SpotBugsTaskFactory`). `JavaPluginConvention`
  and the whole `Convention` API were removed in Gradle 9.
- Root cause: An OLD plugin still calls the removed API. Critically, the plugin may
  not be applied directly in your `plugins {}` block — it can be BUNDLED as a
  transitive dependency of another (internal) plugin. Here SpotBugs 4.7.1 is a
  transitive `implementation` dep of `com.microsoft.identity.buildsystem:0.2.5`,
  which applies SpotBugs unconditionally (no disable toggle in its extension).
- Investigation steps: read the FULL stacktrace (saved to the big-output file) to get
  the exact failing class → map class package to the owning artifact → grep the repo
  for where that plugin/version is declared. If a project `ext` version var exists
  (e.g. `spotBugsGradlePluginVersion`) but nothing in the build scripts reads it, the
  real version is baked into the OTHER plugin's published POM (bumping the ext does
  nothing — verify by re-running and confirming the same class/line still fails).
- Fix (lowest risk first):
  1. If the bundling plugin has a newer release compatible with Gradle 9, bump it.
     (Check the Gradle Plugin Portal for the latest version FIRST — if the failing
     version IS already the latest, this path is closed.)
  2. Patch the bundling plugin upstream (bump its bundled plugin to a Gradle-9-safe
     major, e.g. SpotBugs 6.x, note the groupId also changed
     `gradle.plugin.com.github.spotbugs.snom` → `com.github.spotbugs.snom`) and
     publish a new version, then consume it. Cleanest, but outside the app repo.
  3. Local workaround: force/substitute the bundled plugin to a Gradle-9-compatible
     version on the plugin classpath (dependency substitution is needed when the
     groupId changed across majors). Fiddly because `plugins {}`-DSL transitive deps
     don't override as simply as `buildscript` classpath deps — test carefully.
  4. Build the bundling plugin from source with the version bump and publish to
     mavenLocal (heavier, fully local).
- Note: a stale `spotBugsGradlePluginVersion` ext var in the app repo is a red
  herring — it's consumed only when building the buildsystem plugin itself, not when
  consuming the published plugin. Do not leave a speculative bump in place; revert it.
- Resolution taken (this repo): option 2/4 — the `com.microsoft.identity.buildsystem`
  plugin was patched to bundle SpotBugs 6.x and built to `mavenLocal` as `0.2.6-dev`,
  then all modules that apply it (common, common4j, LabApiUtilities) were pinned to
  `0.2.6-dev`. This cleared the error and `help` reached BUILD SUCCESSFUL.
  ⚠️ MERGE BLOCKER: `0.2.6-dev` is a LOCAL mavenLocal build — CI and other devs won't
  have it. Before merging, the plugin owners must publish a real version (e.g. `0.2.6`)
  to the shared feed, then swap all three pins to it. Track this as a follow-up.
- Verify: `.\gradlew.bat help --stacktrace` configures without the NoClassDefFoundError.

### `NoClassDefFoundError: SelfResolvingDependency` from the Kotlin Gradle plugin
- Category: B-gradle
- Trigger: Gradle 8.x → 9.x upgrade
- Symptom: `java.lang.NoClassDefFoundError: org/gradle/api/artifacts/SelfResolvingDependency`
  during plugin application, stack top in the Kotlin plugin (e.g.
  `org.jetbrains.kotlin.gradle.targets.js.npm.DefaultNpmDependencyExtension.<init>`
  ← `KotlinBasePluginWrapper.apply` ← `KotlinAndroidPluginWrapper.apply`).
  `SelfResolvingDependency` was removed in Gradle 9.
- Root cause: An OLD Kotlin Gradle plugin (1.8.x/1.9.x) calls the removed API.
  Kotlin only gained Gradle 9 support in KGP 2.2.0+. Unlike a plugin bundled inside
  another plugin, KGP is often declared DIRECTLY in the repo (root `buildscript`
  classpath `org.jetbrains.kotlin:kotlin-gradle-plugin:${kotlinVersion}`), so it's
  fixable in-repo without rebuilding any wrapper plugin.
- Investigation steps: read the FULL stacktrace to confirm the `org.jetbrains.kotlin`
  frames → grep for `kotlin-gradle-plugin` and the `kotlinVersion` ext var → confirm
  it's a live buildscript classpath dep (not a red herring). Check the Gradle↔Kotlin
  matrix above for the KGP version the target Gradle needs.
- Fix (lowest risk first): Bump `kotlinVersion` to the KGP row matching the target
  Gradle (e.g. Gradle 9.3.x → `2.2.21`; `2.2.0` is the minimum Gradle-9-capable KGP).
  CAUTION: this is a MAJOR bump — the same var usually also feeds `kotlin-stdlib`, and
  1.x → 2.x pulls in the K2 compiler + stdlib 2.x. Realign any library pinned low to
  avoid a stdlib-2.x `strictly` conflict (e.g. an OpenTelemetry-Kotlin extension
  deliberately held back for Kotlin 1.8). Get user sign-off before a 1.x → 2.x jump.
- Verify: `.\gradlew.bat help --stacktrace` configures without the NoClassDefFoundError.

### `Could not get unknown property 'archivesBaseName'` (or set fails) on Gradle 9
- Category: B-gradle
- Trigger: Gradle 8.x → 9.x upgrade
- Symptom: build script line like `project.archivesBaseName = "common"` or a read of
  `archivesBaseName` fails with `Could not get unknown property 'archivesBaseName'`
  / `Could not set unknown property`. The `archivesBaseName` convention property (from
  the removed base-plugin `Convention`) was removed in Gradle 9.
- Root cause: The project's own build script uses the removed convention accessor.
- Fix (lowest risk first): Use the `base { }` extension: replace
  `project.archivesBaseName = "x"` with `base { archivesName = "x" }`, and replace
  reads of `archivesBaseName` with `base.archivesName.get()` (or reference
  `archiveBaseName` on the specific archive task). Pure in-repo change.
- Verify: `.\gradlew.bat help --stacktrace` configures without the property error.

### Project-level `sourceCompatibility`/`targetCompatibility` unknown property on Gradle 9
- Category: B-gradle
- Trigger: Gradle 8.x → 9.x upgrade
- Symptom: `Could not set unknown property 'sourceCompatibility'` (or `targetCompatibility`)
  on a Java (non-Android) module where they were set at project scope, e.g. bare
  `sourceCompatibility = "1.8"` in `build.gradle`. These were java-plugin convention
  properties, removed with the `Convention` API in Gradle 9.
- Root cause: Project-scope java convention accessors removed.
- Fix (lowest risk first): Move them into the `java { }` extension:
  `java { sourceCompatibility = "1.8"; targetCompatibility = "1.8" }`. If a `java { }`
  block already exists in the module, DELETE the duplicate project-scope lines rather
  than adding a second block. (Android modules use `compileOptions { }` instead — leave
  those as-is.)
- Verify: `.\gradlew.bat help --stacktrace` configures without the property error.

### `NoClassDefFoundError: JavaPluginConvention` from the gmazzo BuildConfig plugin
- Category: B-gradle
- Trigger: Gradle 8.x → 9.x upgrade
- Symptom: `NoClassDefFoundError: org/gradle/api/plugins/JavaPluginConvention`, stack top
  in `com.github.gmazzo...BuildConfigPlugin.configure` / `JavaHandler.getSourceSets`.
  Used in non-Android Java modules to synthesize `BuildConfig` constants.
- Root cause: Old gmazzo BuildConfig plugin (e.g. `3.0.0`) calls the removed API.
- Fix (lowest risk first): Check the Gradle Plugin Portal for the latest
  `com.github.gmazzo.buildconfig` and bump it (here `3.0.0` → `6.0.10`). This is a
  MAJOR jump that also changes the DSL — see the two entries below. It's declared in
  each module's own `plugins {}` block, so bump every module that applies it.
- Verify: `.\gradlew.bat help --stacktrace` gets past the NoClassDefFoundError.

### gmazzo BuildConfig 6.x DSL: field/task API changes after the major bump
- Category: C (surfaced by the plugin bump, not by Gradle itself)
- Trigger: gmazzo BuildConfig 3.x → 6.x
- Symptom(s):
  1. `Could not find method buildConfigField() ... on source set 'main'` — the 3.x
     form of calling `buildConfigField(...)` directly inside `sourceSets { main { } }`
     is gone.
  2. `Could not get unknown property 'generateBuildConfig'` — unquoted references to
     the generated tasks (`dependsOn generateBuildConfig` / `compileJava.dependsOn
     generateBuildConfig, generateTestBuildConfig`) no longer resolve, because 6.x
     registers those tasks LAZILY (they aren't eager project properties anymore).
- Root cause: 6.x moved `buildConfigField` into a nested `buildConfig { }` DSL and
  register tasks lazily.
- Fix (lowest risk first):
  1. Wrap per-source-set fields in a `buildConfig { }` block:
     `sourceSets { main { buildConfig { buildConfigField(...) } } }`. The 3-arg
     raw-expression form `buildConfigField("String","NAME","\"$value\"")` still works.
  2. Quote the task names so the reference stays lazy:
     `compileJava.dependsOn 'generateBuildConfig', 'generateTestBuildConfig'`.
- Verify: `.\gradlew.bat help --stacktrace` configures the module.

### gmazzo BuildConfig 6.x: `generator ... is final and cannot be changed` (Kotlin + useJavaOutput)
- Category: C (plugin-bump behavior change)
- Trigger: gmazzo BuildConfig 3.x → 6.x, in a module that ALSO applies a Kotlin plugin
- Symptom: `The value for property 'generator' is final and cannot be changed any
  further` at the line calling `useJavaOutput()` inside a `buildConfig { }` block.
- Root cause: In 6.x the `generator` property is finalized-on-read. When a Kotlin
  plugin is applied, the output would default to Kotlin, and the property gets FINALIZED
  the first time `sourceSets.main` is realized (e.g. by an eager `task sourcesJar { from
  sourceSets.main.java.srcDirs }` earlier in the script). A `buildConfig { useJavaOutput() }`
  block placed AFTER that realization then fails. A twin module without Kotlin (or with
  its `buildConfig {}` at the top) doesn't hit this — the trigger is Kotlin + ordering.
- Investigation steps: compare against a sibling module that works — check (a) whether
  the failing module applies a Kotlin plugin and the working one doesn't, and (b) whether
  the `buildConfig {}` block sits before/after the first `sourceSets.main` access.
- Fix (lowest risk first): SPLIT the config so the generator is set BEFORE any source-set
  realization. Put a minimal `buildConfig { packageName("..."); useJavaOutput() }` block
  high in the script (right after `plugins {}` / version setup, before the first task that
  touches `sourceSets.main`), and keep only the `buildConfigField(...)` calls in the later
  block (they depend on computed vars and don't finalize `generator`). Do NOT just move the
  whole block up if it references variables defined later.
- Verify: `.\gradlew.bat help --stacktrace` configures the module without the final-property error.

### Known NON-blocking warnings on Gradle 9 + Kotlin 2.2 (do not "fix")
- Category: n/a (informational — these do NOT fail the build)
- Trigger: Gradle 9 + Kotlin Gradle plugin 2.2.x
- Symptoms that look scary but are benign:
  1. `w: Android Publication 'distDebug'/'distRelease' Misconfigured for Variant ...`
     each followed by a `Stacktrace:` and a bare `java.lang.Throwable`. This is a
     Kotlin 2.2 DIAGNOSTIC (note the `w:` prefix and that it's a captured `Throwable`,
     not a thrown exception) from `KotlinToolingDiagnostics$AndroidPublicationNotConfigured`.
     It only suggests adding `android { publishing { singleVariant("distDebug") {} } }`.
     Safe to ignore for the upgrade; address separately if/when publication is reworked.
  2. `Deprecated Gradle features were used in this build, making it incompatible with
     Gradle 10.` — forward-looking notice for the NEXT major; irrelevant to a Gradle 9
     upgrade. Use `--warning-mode all` later to plan the Gradle 10 hop.
- How to tell pass from fail: scroll to the LAST line — `BUILD SUCCESSFUL` means the
  config/build passed regardless of `w:` warnings and `Throwable` diagnostic dumps.
  Only a `BUILD FAILED` + `* What went wrong:` block is an actual failure.

## Progress log — common (AzureAD) Gradle 9 upgrade
State after Gradle-only step (AGP and SDK NOT yet bumped): `help` = BUILD SUCCESSFUL on
Gradle 9.3.1 / JDK 17. Changes required, all minimal:
- wrapper `gradle-8.11.1` → `gradle-9.3.1`.
- `settings.gradle` credentials: `project.findProperty` → `providers.gradleProperty(...).getOrNull()`.
- `versions.gradle` `kotlinVersion` `1.8.0` → `2.2.21` (KGP Gradle-9 support).
- `buildsystem` `0.2.5` → `0.2.6-dev` (SpotBugs 6.x) in common, common4j, LabApiUtilities
  — ⚠️ mavenLocal only; must publish a real version before merge.
- gmazzo buildconfig `3.0.0` → `6.0.10` (common4j, LabApiUtilities) + DSL/ordering fixes.
- `archivesBaseName` → `base.archivesName` (common).
- project-scope `sourceCompatibility`/`targetCompatibility` → `java { }` (common4j,
  keyvault, labapi; de-duped where a `java {}` already existed).
Env prerequisite: private feed PAT in global `~/.gradle/gradle.properties` (see 401 entry).
Next per the golden order: bump AGP → then compileSdk/targetSdk to API 37.

## AGP 9 error catalog (Category B — AGP behavior changes)

### `kotlin-android` plugin no longer required / incompatible with AGP 9 (built-in Kotlin)
- Category: B (AGP behavior change)
- Trigger: AGP 8.x → 9.x
- Symptom: applying `id 'kotlin-android'` (`org.jetbrains.kotlin.android`) fails —
  "The 'org.jetbrains.kotlin.android' plugin is no longer required for Kotlin support
  since AGP 9.0. Solution: Remove the plugin." AGP 9 enables **built-in Kotlin** by
  default (`android.builtInKotlin=true`) and the new DSL (`android.newDsl=true`), and
  the legacy Kotlin plugin is incompatible with the new DSL.
- Root cause: AGP 9 compiles Kotlin itself (runtime dep on KGP ≥ 2.2.10); the separate
  JetBrains Android Kotlin plugin is redundant and reaches for removed internal types.
- Fix (lowest risk first):
  1. Migrate to built-in Kotlin (recommended): remove `id 'kotlin-android'`. Then the
     `android { kotlinOptions { } }` block breaks (it came from that plugin) — move it
     to the top-level built-in-Kotlin extension:
     `import org.jetbrains.kotlin.gradle.dsl.JvmTarget` +
     `kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_1_8 } }`.
  2. Opt out: `android.builtInKotlin=false` (keep the plugin) — removed in AGP 10.
- Pinning the built-in Kotlin (KGP) version: AGP 9 built-in Kotlin has a runtime dep on
  KGP (default 2.2.10). To use a SPECIFIC compiler version, override in the TOP-LEVEL
  buildscript: `buildscript { dependencies { classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:<VER>") } }`
  (same pattern for KSP). AGP enforces a FLOOR: built-in Kotlin needs KGP >= 2.2.10
  (declaring lower is silently bumped to 2.2.10); downgrading below that requires opting
  out of built-in Kotlin or a `strictly` constraint (min 2.0.0), and must still fit the
  Gradle<->KGP matrix. NOTE: a repo `kotlinVersion` ext usually only drives the
  `kotlin-stdlib` DEPENDENCY, not the built-in COMPILER version — those are separate.
  A stdlib newer than the compiler is fine; align them via the classpath override only
  if you need an exact match.
- Verify: `.\gradlew.bat :<module>:help --stacktrace` gets past the plugins block.

### `Could not find method kotlinOptions()` after removing kotlin-android
- Category: B
- Trigger: AGP 9 built-in Kotlin migration (companion to the entry above)
- Symptom: `Could not find method kotlinOptions() ... on object of type
  LibraryExtensionImpl` — the `kotlinOptions { }` block inside `android { }` is gone
  once `kotlin-android` is removed.
- Fix: use the top-level `kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_1_8 } }`
  extension (import `org.jetbrains.kotlin.gradle.dsl.JvmTarget`). Remove the old block.
- Verify: `.\gradlew.bat :<module>:help --stacktrace` configures past that line.

### `getDefaultProguardFile('proguard-android.txt')` no longer supported on AGP 9
- Category: B
- Trigger: AGP 8.x → 9.x (`android.r8.proguardAndroidTxt.disallowed` false→true)
- Symptom: "getDefaultProguardFile('proguard-android.txt') is no longer supported since
  it includes -dontoptimize ... Instead use 'proguard-android-optimize.txt'".
- Root cause: `proguard-android.txt` carries `-dontoptimize`, which blocks R8
  optimizations; AGP 9 disallows it by default.
- Fix: swap to `getDefaultProguardFile('proguard-android-optimize.txt')`. If you truly
  need to disable optimization, add `-dontoptimize` in a custom keep file instead.
- Verify: `.\gradlew.bat :<module>:help --stacktrace` configures past the buildTypes block.

### `Could not get unknown property 'libraryVariants'` / `applicationVariants` on AGP 9
- Category: B
- Trigger: AGP 8.x → 9.x (`android.newDsl=true` by default)
- Symptom: `Could not get unknown property 'libraryVariants'` (or `applicationVariants`)
  on `LibraryExtensionImpl`/`ApplicationExtensionImpl`. The legacy Variant API was
  removed by the new DSL. Common uses: renaming output `.aar`/`.apk` via
  `variant.outputs.all { outputFileName = ... }`, and building per-variant Javadoc/
  JavadocJar tasks via `variant.javaCompileProvider` / `variant.javaCompiler.classpath`.
- Root cause: AGP 9's new DSL removes the legacy `libraryVariants`/`applicationVariants`
  collections and the old per-variant accessors (`javaCompileProvider`,
  `javaCompiler.classpath`, `variant.outputs`). Replacement is the `androidComponents`
  API (`onVariants { }` / `beforeVariants { }`).
- Assess impact BEFORE choosing: not all usages are equal.
  * Output-filename renaming → usually cosmetic; Maven publication names the published
    artifact from `groupId:artifactId:version`, not the intermediate file name.
  * Per-variant Javadoc/JavadocJar → these generate the `-javadoc.jar` companion
    artifact. Check whether the `publishing { publications { } }` block actually
    ATTACHES those jars (e.g. `artifact tasks["...JavadocJar"]` or via a component). If
    the publication only does `from components.release` and never references the
    javadoc/sources jars, their real impact is low.
  * `doFirst { println(...) }` logging → cosmetic.
- Fix (present BOTH to the user; pick per scope):
  1. Opt out (documented stopgap): `android.newDsl=false` in `gradle.properties`.
     Restores the legacy Variant API and unblocks ALL usages with zero code rewrite.
     Built-in Kotlin still works alongside it. ⚠️ Removed in AGP 10 — track the real
     migration as follow-up. Best when the variant logic is large and/or partly
     unsupported by the new API (e.g. Javadoc needs `javaCompiler.classpath`, which
     `androidComponents` does not expose).
  2. Full migration to `androidComponents`: rewrite `variant.outputs` renaming via
     `onVariants`; re-implement Javadoc off source sets directly (the new API has no
     `javaCompileProvider`/`javaCompiler` equivalent). Future-proof but larger and
     touches published artifacts — treat as a dedicated task, not part of the bump.
- Note on Javadoc risk for beginners: Javadoc is API docs generated from `/** */`
  comments at build time into a `-javadoc.jar`; it is NOT checked-in files. Losing it
  matters for PUBLIC libraries; for internal libraries it is often non-critical —
  especially if the publication never attached the jar in the first place.
- Verify: `.\gradlew.bat :<module>:help --stacktrace` configures without the property error.

### `Could not get unknown property '<variant>'` for SoftwareComponent container on AGP 9
- Category: B
- Trigger: AGP 8.x → 9.x
- Symptom: `Could not get unknown property 'distRelease' for SoftwareComponent
  container ...` at a `publishing { publications { x(MavenPublication) { from
  components.distRelease } } }` line. Often foreshadowed earlier by a Kotlin 2.2
  warning: "Android Publication 'distDebug' Misconfigured ... configure singleVariant".
- Root cause: AGP 9 no longer AUTO-creates the `components.<variant>` software
  components for publishing. Each publishable variant must be explicitly declared.
- Fix: add a `publishing { }` block INSIDE `android { }` declaring each variant:
  `android { publishing { singleVariant("distRelease") {}; singleVariant("distDebug") {} } }`.
  Add `withSourcesJar()` / `withJavadocJar()` inside a `singleVariant` only if you
  actually want those companion artifacts (minimal form = AAR only, matching most
  pre-AGP-9 setups that never attached them). The `<variant>` name is flavor+buildType
  (e.g. flavor `dist` + `release` = `distRelease`).
- Verify: `.\gradlew.bat :<module>:help --stacktrace` configures the publishing block.

### IMPORTANT: apply AGP 9 fixes to EVERY Android module, not just the first one
- Category: process note (not an error itself)
- The built-in-Kotlin migration (`kotlin-android` removal + `kotlinOptions` →
  `kotlin { compilerOptions }`) and the `proguard-android.txt` → `-optimize.txt` swap
  must be repeated in EVERY module that applies `com.android.library`/`application`
  and hits them. A single-module `:common:help` passing does NOT mean the full build
  passes — `.\gradlew.bat help` configures all modules and will fail module-by-module.
- Up front, grep the whole repo to find every occurrence and fix them in one pass:
  `kotlin-android`, `kotlinOptions`, `getDefaultProguardFile('proguard-android.txt')`.
  In this repo the Android modules were: `common`, `testutils`, `uiautomationutilities`
  (plus `common4j` has a `kotlinOptions` from the Kotlin JVM plugin — that is NOT an
  Android/built-in-Kotlin case; leave it).

## Progress log — common (AzureAD) AGP 9.1.1 upgrade
State: full `.\gradlew.bat help` = BUILD SUCCESSFUL on Gradle 9.3.1 + AGP 9.1.1 + JDK 17.
Why AGP 9.1.1: its compatibility table pins Gradle 9.3.1 (min+default) and supports max
API 37 — matches the target SDK step. Changed `gradleVersion` ext `8.10.0` → `9.1.1`.
AGP-9 fixes applied (all Category B):
- Removed `id 'kotlin-android'` and moved `kotlinOptions` → top-level
  `kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_1_8 } }` in `common` + `testutils`.
- `getDefaultProguardFile('proguard-android.txt')` → `-optimize.txt` in `common`,
  `testutils`, `uiautomationutilities`.
- `android { publishing { singleVariant("distRelease"/"distDebug") {} } }` in `common`
  (AGP 9 no longer auto-creates publishing components).
- `android.newDsl=false` in `gradle.properties` (STOPGAP, removed in AGP 10) to keep the
  legacy `libraryVariants` Variant API (output-file naming + per-variant Javadoc tasks)
  in `common`. Follow-up before AGP 10: migrate those to the `androidComponents` API.
Next per the golden order: bump compileSdk/targetSdk/buildTools to API 37.

## gmazzo BuildConfig 6.x — compile-stage traps that `help` does NOT catch
These two only appear at `assemble`/compile (task-graph or javac), NOT at `help` —
textbook "configures != compiles". If you migrated gmazzo 3.x → 6.x, always run
`:<module>:assembleDebug` (or an equivalent compile), never stop at `help`.

### `Task with name 'generateBuildConfig' not found` at assemble (gmazzo 6.x task rename)
- Category: B (plugin major behavior change)
- Trigger: gmazzo BuildConfig 3.x → 6.x
- Symptom: `UnknownTaskException: Task with name 'generateBuildConfig' not found in
  project ':x'` (or `generateTestBuildConfig`) when a `dependsOn '...'` / `compileJava
  .dependsOn '...'` resolves during assemble. Passes `help` because string-based
  `dependsOn` is LAZY — the missing name is only resolved when the task graph is built.
- Root cause: 6.x RENAMED the generate tasks by appending `Classes`:
  `generateBuildConfig` → `generateBuildConfigClasses`,
  `generateTestBuildConfig` → `generateTestBuildConfigClasses`
  (also `generateTestFixturesBuildConfigClasses`). Quoting the OLD name (a common 3.x→6.x
  half-migration) is still the wrong name.
- Fix: use the 6.x names. Discover them with `.\gradlew.bat :<module>:tasks --all` and
  grep `BuildConfig`. Update every `dependsOn`.
- Verify: `.\gradlew.bat :<module>:assembleDebug` gets past task-graph resolution.

### `cannot find symbol` for BuildConfig at compile (gmazzo 6.x output dir change)
- Category: B (plugin major behavior change)
- Trigger: gmazzo BuildConfig 3.x → 6.x
- Symptom: `error: cannot find symbol` in source files that import/reference the
  generated `BuildConfig` (e.g. `...java.BuildConfig`), failing `:x:compileJava` even
  though `generate...BuildConfigClasses` ran successfully.
- Root cause: 6.x changed the generated output directory from
  `build/generated/source/buildConfig/<sourceSet>` (3.x, "source" singular) to
  `build/generated/sources/buildConfig/<sourceSet>` (6.x, "sources" PLURAL). Scripts that
  manually add the old path via `sourceSets.main.java.srcDirs = ['src/main', "$buildDir/
  generated/source/buildConfig/main"]` now point at an empty dir. Worse, that `srcDirs =`
  ASSIGNMENT overrides the 6.x plugin's automatic source-set wiring, so the generated
  file never reaches javac.
- Fix (lowest risk first): update the manual path `generated/source/` → `generated/sources/`
  (for every source set: main/test). Verify the real path with
  `Get-ChildItem -Recurse build/generated -Filter BuildConfig.java`. (Cleaner long-term:
  drop the manual generated srcDir and let 6.x auto-wire — but only if nothing else does a
  `srcDirs =` assignment that would wipe it.)
- Verify: `.\gradlew.bat :<module>:assembleDebug` = BUILD SUCCESSFUL.

### Stale incremental state: a Java `cannot find symbol` for a KOTLIN class after build-script edits
- Category: C (environment/tooling state — NOT a real code error)
- Trigger: any build-script change during the upgrade, then an INCREMENTAL build (esp.
  Android Studio Assemble/Make, but CLI incremental too).
- Symptom: `error: cannot find symbol` in a `.java` file for a class that is actually a
  `.kt` (Kotlin) source in the same module (e.g. Java `OAuth2Strategy.java` importing
  `OTelUtility` which is `OTelUtility.kt`). Studio fails but a plain CLI run may "pass"
  (or vice-versa).
- Root cause: Java-depends-on-Kotlin needs `compileKotlin` output on `compileJava`'s
  classpath. After you edit source sets / task deps / generated-source paths, an
  incremental build can hold stale outputs so the Kotlin `.class` isn't where the
  incremental Java compile looks. The code is fine; the build STATE is stale.
- How to confirm it's staleness (not real): run a CLEAN compile —
  `.\gradlew.bat :<module>:clean :<module>:compileJava` (or `clean :<app>:assembleDebug`).
  If clean is BUILD SUCCESSFUL, the code is fine and the incremental failure was stale.
- Fix: In Android Studio after ANY build-script edit, do **File → Sync Project with
  Gradle Files → Build → Clean Project → Build → Rebuild Project**. On CLI, prefix with
  `clean`. Do NOT chase the phantom `cannot find symbol` as a code bug first — verify
  with a clean build.
- Broader lesson: incremental builds cut BOTH ways during an upgrade — they can MASK a
  real error (stale up-to-date task) AND INVENT a fake one (stale mismatched outputs).
  Trust a CLEAN build as the source of truth; confirm the final state with `clean` at
  least once per module you changed.
- Verify: `.\gradlew.bat clean :<module>:assembleDebug` = BUILD SUCCESSFUL.

### IDE "Cannot resolve method/symbol" for Lombok (or other generated) code while Gradle builds fine
- Category: C (IDE indexing state — NOT a real compile error)
- Trigger: after build-script edits during the upgrade; shows up in Android Studio's
  **Problems** panel (e.g. "Project Errors 100"), not the Gradle **Build** window.
- Symptom: many `Cannot resolve method 'getX'/'builder'/'getSlice'/'getState'...` on
  classes annotated with Lombok `@Getter`/`@Builder`/`@Data`, etc. The methods don't
  exist in source — Lombok generates them at compile time via annotation processing.
- Root cause: The Gradle build runs Lombok (`annotationProcessor org.projectlombok:lombok`)
  so it compiles fine, but Android Studio's EDITOR resolves symbols from its own index,
  which needs the Lombok IDE plugin + annotation processing enabled + a fresh sync. After
  build-script churn that index goes stale and every generated accessor shows red.
- Confirm it's IDE-only: run a CLEAN CLI compile of the offending module
  (`.\gradlew.bat clean :<module>:compileJava`). BUILD SUCCESSFUL ⇒ the code is fine and
  the red markers are stale IDE analysis. Trust the Gradle **Build** window, not Problems.
- Fix (Android Studio): **File → Invalidate Caches… → Invalidate and Restart** (rebuilds
  the symbol index); let **Gradle Sync** finish; ensure **Settings → Build → Compiler →
  Annotation Processors → Enable annotation processing** is ON; ensure the **Lombok**
  plugin is installed/enabled; then **Build → Rebuild Project**.
- Verify: `.\gradlew.bat clean :<module>:compileJava` = BUILD SUCCESSFUL (authoritative);
  IDE Problems clear after Invalidate Caches + sync.

### `generateJavadoc` prints `package ... does not exist` for Kotlin packages but build is SUCCESSFUL
- Category: C (pre-existing; Javadoc-on-mixed-Kotlin/Java, tolerated by `failOnError false`)
- Trigger: any full `assemble` (esp. Android Studio "Assemble Project") that reaches a
  module's `javadocJar` → `generateJavadoc`. A targeted `:app:assembleDebug` may NOT
  trigger a dependency module's `assemble`/javadoc, so this only shows on a FULL build.
- Symptom: under `> Task :<module>:generateJavadoc`, many `error: package
  com.x...<kotlinPkg> does not exist` / `cannot find symbol` for the module's OWN
  internal packages — yet the run ends `BUILD SUCCESSFUL`.
- Root cause: the Javadoc tool only processes `.java` files
  (`source = sourceSets.main.java`) and the task's classpath does NOT include the compiled
  KOTLIN output. Java files that import Kotlin classes (e.g. a `base64`/`nativeauth`
  package that is `.kt`) can't be resolved by Javadoc. The task sets `failOnError false`,
  so it PRINTS the errors but does not fail — this is long-standing, NOT upgrade-caused.
- How to confirm harmless: run `.\gradlew.bat :<module>:generateJavadoc` and check the
  LAST line is `BUILD SUCCESSFUL`. Verify the flagged packages are Kotlin (`*.kt`).
- Fix: none needed for an upgrade (leave as-is; the `-javadoc.jar` was already incomplete
  pre-upgrade). OPTIONAL cleanup: put Kotlin output on the Javadoc classpath —
  `generateJavadoc { dependsOn 'compileKotlin'; classpath += files(tasks.named('compileKotlin').flatMap { it.destinationDirectory }) }`.
- Verify: task run ends `BUILD SUCCESSFUL` (the `error:` lines are swallowed by `failOnError false`).

### Unit tests fail with `MalformedURLException: no protocol` — missing test harness property (not the upgrade)
- Category: C (pre-existing test-environment requirement)
- Trigger: running unit tests (`:<module>:test` / `:<module>:testLocalDebugUnitTest`) in a
  bare local invocation, i.e. WITHOUT the gradle properties CI normally supplies.
- Symptom: a whole test class errors on every method; the root cause (scroll up to
  `Caused by:`) is a class-initializer failure like
  `java.net.MalformedURLException: no protocol: 1234/...` at a test `ApiConstants`/mock
  config `<clinit>`. Here it was the NativeAuth mock-API tests
  (`NativeAuthResponseHandlerTest`, `NativeAuthRequestProviderTest`).
- Root cause: a test builds URLs from a BuildConfig value (`MOCK_API_URL`) that defaults
  to EMPTY unless a project property (`-PmockApiUrl=<http url>`) is passed. Empty base →
  `URL("1234/...")` throws → static init fails → all tests in the class error. The default
  has always been empty; the upgrade did not touch it.
- Confirm it's pre-existing (not a regression): re-run the class WITH the property, e.g.
  `.\gradlew.bat :<module>:test --tests "*NativeAuthRequestProviderTest" -PmockApiUrl=https://x.example/`.
  If it goes BUILD SUCCESSFUL, the failure was the missing harness config, not the bump.
- Fix: none in the app — supply the property when running those tests locally
  (`-PmockApiUrl=<url>`), or exclude the mock-API suite; CI already passes it. Do NOT
  "fix" the tests as if the upgrade broke them.
- TWO TIERS of such tests exist — don't conflate them:
  * UNIT (e.g. `NativeAuthResponseHandlerTest`, `NativeAuthRequestProviderTest`) only
    need a syntactically valid `-PmockApiUrl` to get past URL construction → then pass.
  * INTEGRATION (e.g. `*OAuth2StrategyTest`, `NativeAuthControllerTest`, `*ScenarioTest`
    using `MockApiUtils`/`MockApiEndpoint`) POST canned responses to a LIVE mock-API
    service and call it — a fake URL yields assertion failures (`err=0, fail=N`). These
    only pass in the CI mock-API job with the real service URL + network; a local run
    cannot satisfy them regardless of the upgrade.
- Broader lesson: when unit tests fail after an upgrade, FIRST read the `Caused by:` root
  cause and check whether it's a test-harness/env input (URL, secret, property, network)
  vs a real code/API break. `fail` (assertion) with `err=0` on integration suites that
  talk to external/mock services is almost always environment, not the version bump.
  Confirm by reproducing on the base branch or by supplying the missing input.
- Verify: `.\gradlew.bat :<module>:test -PmockApiUrl=<url>` passes the UNIT suites;
  integration suites need the real mock-API service (CI only).
- Resolution (this repo, CONFIRMED): with the REAL values supplied — `-PmockApiUrl=<mock
  service url>` + `-PnativeAuthConfigString=<single-quote JSON config>` (both from the
  `devex-ciam-test` variable group; see "ASK the user" section) — a clean
  `:common4j:test :common:testLocalDebugUnitTest` run on the upgraded Gradle 9.3.1 /
  AGP 9.1.1 branch was **BUILD SUCCESSFUL** (common4j 806 tests, full common suite, only
  2 skipped each). Same code, only the environment input changed ⇒ the failures were
  100% environmental, NOT the upgrade. This is the canonical "prove-it-isn't-the-upgrade"
  evidence: empty config → `MalformedURLException`; real config → all pass.
- NOTE on the config format: `nativeAuthConfigString` uses SINGLE quotes on purpose — it
  is injected verbatim into a Java string literal via
  `buildConfigField("String","NATIVE_AUTH_CONFIG_STRING","\"$value\"")`, so double quotes
  would break the generated source. Pass it as-is; do not convert to double-quote JSON.

## Status update / decision-log template (minimal)

When reporting progress on a Gradle/AGP/SDK upgrade, keep it short: the target versions,
a one-line-per-DECISION log (only the points where a real choice or correction happened),
current verified state, and open follow-ups. Skip the routine mechanical edits. Template:

```
Upgrade: Gradle <old>→<new>, AGP <old>→<new>, API <old>→<new>. Branch: <branch>.

Decisions made / corrections:
- Plugin: bumped <plugin> <old>→<new> because <removed API / Gradle-9 incompat>.
  (Note any MERGE BLOCKER, e.g. a mavenLocal-only build that must be published first.)
- Kotlin: <KGP bump 1.x→2.x for Gradle-9> + <AGP-9 built-in Kotlin: removed kotlin-android,
  moved kotlinOptions → kotlin{compilerOptions}>. Called out because it's a major (K2/stdlib).
- <Any stopgap>: e.g. android.newDsl=false to keep legacy Variant API — removed in AGP 10
  (follow-up: migrate to androidComponents).
- Testing: <what passed> and <what was environment-only>, e.g. NativeAuth mock-API tests
  needed CI-only inputs (mockApiUrl + config from devex-ciam-test) — supplied → all green;
  proven not upgrade-related.

Verified: <commands that passed, e.g. clean :common:assembleLocalDebug, full test run>.
Pending: <e.g. API 37 bump; publish real plugin version; androidComponents migration>.
```

Guidance: one bullet per DECISION, not per file touched. Always flag stopgaps and merge
blockers explicitly so the reviewer sees what still needs action.
