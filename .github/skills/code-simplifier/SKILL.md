---
name: code-simplifier
description: "Simplify bloated diffs, pull requests, and recent code changes across Android Auth repositories (Common, Broker, MSAL, ADAL, Authenticator) to the smallest, clearest, strictly flight-isolated implementation that preserves behavior. Use when asked to simplify code, shrink a PR or diff, reduce complexity or line count, remove over-engineered helpers or abstractions, check for simpler native Android/WebView/Kotlin/Java primitives, flatten nested flight checks, or audit whether all new code is strictly behind a flight. Triggers include 'simplify', 'any opportunity to simplify', 'lots of code changes', 'too much code', 'make this simpler and smaller', 'is there a simpler solution', or '/code-simplifier'."
---

<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Code Simplifier

Shrink working-tree diffs, commits, and pull requests across `common`, `broker`, `msal`, `adal`, and `authenticator` to the **smallest, easiest-to-read change** that solves the root cause with **100% flight-off isolation** and preserved behavior.

## Operating Modes

- **Apply mode (default when asked to simplify, shrink, or clean up changes)**: Audit the full diff against the base branch, apply the simplifications across production and test files, verify locally (targeted unit tests, static analysis, `git diff --check`), and report the before-vs-after file/line diffstat. Commit and push only when the user requested updating the branch or PR.
- **Audit mode (when asked an open question such as "can we simplify this?" or "why do we need this complex code?" without an edit request)**: Present a short, ranked list of concrete simplifications with line-count impact and native/platform alternatives before editing.

## The 6 Simplification Lenses

### 1. Platform & Existing Primitives; Resist Review-Driven State Accretion
- Challenge every custom state machine, shadow history/lifecycle tracker, reflection helper, or multi-callback state flag: *Does Android, WebView, Kotlin/JDK, or an existing repository class already provide a native API, callback parameter, or invariant that solves this directly?*
- **Verify exact native API semantics**: Confirm edge-case behavior before swapping in a coarse platform call (for example, `WebView.clearHistory()` wipes *all* back-stack entries rather than replacing one entry, breaking multi-step Back flows—whereas a small `HashMap` preserves prior pages without `TreeMap` + multi-callback state tracking).
- **Stop review-driven ping-pong**: Do **not** silently bolt on 50+ line multi-callback state machines (`onPageStarted`/`doUpdateVisitedHistory`/`onPageFinished`, `NavigableMap`/`TreeMap`) to satisfy theoretical automated-reviewer edge cases—or re-add them when a bot flags their removal. Fix genuine risks in a few flat lines; if addressing a synthetic scenario requires disproportionate complexity, push back with clear technical rationale or surface the tradeoff to the developer.

### 2. Data-Driven Extension & Collapsing Unnecessary Touchpoints
- **Extend central data registries, not cross-repo plumbing**: When adding a new partner, allowlist identity, or policy variant, extend the existing central data set and no-arg provider (such as `AppRegistry.getBrowserSsoAuthorizedApps()`) so downstream callers (`Broker`'s `BrowserAllowList`) need zero new production logic, zero boolean parameters threaded across repos, and zero duplicate package prechecks (which risk case-sensitivity or semantic drift against shared validators).
- Delete single-caller `*Tracker`, `*Helper`, or `*Coordinator` classes and constructor/interface callbacks added only to pass state for a localized fix. Inline a focused method (~10-40 lines) in the owning class instead.
- Revert incidental collaborator touchpoints and unsolicited overview-doc prose (`PROJECT_OVERVIEW.md`) completely back to the base branch (`git checkout origin/dev -- <path>`).
- Remove `@VisibleForTesting` getters/setters and test-only nullable constructor parameters / `@JvmOverloads` shims when production only uses a single constructor.

### 3. Smallest State Representation & Idiomatic JVM Immutability
- Before keeping new `Map`s, `Set`s, `NavigableMap`/`TreeMap`s, wrapper data classes, ownership tokens (`new Object()`), or cached IDs, check existing locks and cardinality invariants on the class.
- Prefer a single primitive field (`boolean`, `int`) under an existing lock—or a lazy nullable `HashMap`/`HashSet` initialized only when the flight is enabled—over parallel static/instance collections or sorted maps.
- For `@JvmField` or Java-exposed Kotlin collections (such as `AppRegistry` allowlists), use `buildSet { ... }` / `buildList { ... }` (genuinely unmodifiable on the JVM in one expression) instead of `setOf(...)` (backed by a mutable `LinkedHashSet` from Java) or custom defensive-copy wrappers.

### 4. Single-Boundary Flight Isolation & Flat Readability
- **100% flight-off zero-path guarantee**: Trace every touched line with `flight = false`. Remove eager field allocations (`= new Object()`, `= new HashMap<>()`), constructor wiring changes, speculative `try/catch` + `Logger.warn` wrappers, and modified shared helpers that execute when the flight is off.
- **Gate once at the call boundary**: Check `isFlightEnabled(...)` at the entry call site, extract non-trivial flight-enabled logic into its own clearly named method, and **never repeat `isFlightEnabled(...)` inside a helper called exclusively from a flight-gated branch**.
- **Flatten control flow**: Use early guard returns (`if (...) return;`) rather than deep nested `if` blocks.

### 5. Strip Dead `SDK_INT` / `ApiXXImpl` Wrappers & Defensive Boilerplate
- **No dead `SDK_INT` checks or Dalvik-era `ApiXXImpl` classes**: Check `minSdkVersion` (`24` in `gradle/versions.gradle`) before adding version guards, and never add `@RequiresApi` `Api24Impl` static inner classes on API 21+ (ART verifies API calls lazily). If Robolectric fails on an API 24+ method in unit tests, annotate the test class with `@Config(sdk = [Build.VERSION_CODES.P])`—never bloat production code to work around Robolectric's default SDK.
- Remove speculative `try/catch (RuntimeException)` blocks around in-memory calls, redundant null/type re-checks already guaranteed by callers, unused overloads, and comments that merely restate what the code does. Keep comments only where *why* is non-obvious.

### 6. Trim Test Bloat: Behavioral Boundaries Over Constant Copies & $2^N$ Loops
- Revert modifications or extra parameters added to existing test helpers shared by legacy tests whenever possible, and delete standalone test files for removed helper/tracker plumbing.
- **Drop tautological constant-copy tests & cartesian flag loops**: Do not copy production constants (such as Base64 thumbprints) into tests just to assert `PROD_CONST == COPIED_CONST`, and do not nest `listOf(false, true).forEach` across $N$ independent flights ($2^N$ growth). Test orthogonal flights independently.
- Replace repetitive 400-600 line suites with focused behavioral boundary tests (maintaining >= 80% CI diff coverage on modified classes):
  1. `flight = false` preserves exact legacy behavior.
  2. `flight = true` exercises the real runtime boundary (e.g., `ICallingAppValidator.requireAllowedCaller` with valid and unknown signers, not just static set contents).
  3. Real edge cases verified with non-tautological assertions (for example, `clearInvocations(mock)` + explicit `verify(mock, never())` on the guarded action).

## Step-by-Step Workflow

1. **Measure baseline**: Run `git diff <base>...HEAD --stat` (and include unstaged diffs) and read every changed hunk end-to-end. Classify each hunk as *essential*, *collapsible*, *unflighted leak*, or *revertible touchpoint*.
2. **Re-derive from the root cause**: State the bug or requirement in one sentence. Ask: *"What is the smallest change in the owning class—using native platform and repo primitives—that makes this true strictly behind the flight?"*
3. **Cut, revert, and inline**: Revert incidental collaborator files, delete single-use classes and plumbing tests, shrink state fields, extract a flat flight-on helper gated once at the boundary, and trim redundant tests.
4. **Verify `flight = false` isolation**: Confirm line-by-line that when the flight returns `false`, zero new objects are allocated, zero new logs fire, and legacy execution is bit-for-bit identical.
5. **Run targeted validation**: Execute targeted Gradle unit tests (`--tests "<Pattern>"`), static analysis (`pmd`, `spotbugsLocalDebug`, `lintLocalDebug` as configured in the target repo), and `git diff --check` (follow [code-author](../code-author/SKILL.md) Gradle/JDK setup).
6. **Report concise diffstat**: Show before -> after file and line counts (`+before -> +after`), what abstractions/unflighted paths were removed, and test verification results.

## Concrete Examples

Read [references/simplification-examples.md](references/simplification-examples.md) for real Android Auth before/after simplifications (collapsing ownership-token maps in `Broker`, eliminating `*Tracker` classes and collaborator callbacks in `Common`, flattening nested flight gates, and avoiding state-machine accretion).
