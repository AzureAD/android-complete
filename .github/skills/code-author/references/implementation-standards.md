# Android Auth Implementation Standards

Detailed engineering standards for authoring code and pull requests across `common`,
`broker`, `msal`, `adal`, and `authenticator`.

---

## 1. Feature Flags / Flights (`Flight Off` Means Zero Behavioral Change)

1. **Default-off**: New features and non-trivial behavioral fixes must default to `false` in
   `CommonFlight`, `BrokerFlight`, or `EcsFlight`.
2. **Strict off-path isolation**:
   - Audit the entire diff for any code that executes when the flight is `false`.
   - Do **not** run new state-restoration markers, new ViewModel allocations, altered
     navigation routes, or extra log statements when the flight is disabled.
3. **Centralized flight evaluation**:
   - When a shared list or policy in `Common` is conditionally extended by a flight (e.g.,
     `getBrowserSsoAuthorizedApps()`), evaluate the flight centrally inside the no-arg
     helper rather than requiring every caller to pass flight flags manually.
4. **Dual-state testing**:
   - Every test suite for flighted code must test both the flight-enabled and flight-disabled
     paths.

---

## 2. Telemetry & OpenTelemetry Sampler Invariants

1. **Regression attribution**:
   - Emit bounded telemetry on flighted paths covering operation outcomes, latency/duration,
     reuse/wait paths, and structured rejection reasons.
   - Never log PII, raw tokens, certificates, or unredacted user identifiers.
2. **Sampler expectation sync**:
   - In `broker` and `common`, adding a new `SpanName` or changing sampled span configuration
     will fail CI if sampler expectation tests (e.g., `OpenTelemetrySamplerTest` / expected
     span maps) are not updated in the same commit.
   - Always search for existing `SpanName` references in `src/test` when adding a span.

---

## 3. Architecture & Cross-Layer Responsibility

1. **Map all converging callers**:
   - Before placing a guard in a single caller or UI screen, trace all entry points that
     reach the underlying operation (e.g., `RegisterDeviceTask`, admin SDM join, pre-created
     join, AccountManager inline flows).
   - Document why a guard lives at a specific layer (and add a brief code comment when a
     handler guard protects direct callers that bypass upstream coordination).
2. **Avoid parallel validators**:
   - Group related caller/package variants using data/metadata models (e.g., `AppFamily`) and
     validate through a single shared validator rather than cloning allowlists and
     validation methods per partner.
3. **Reservation lifetime**:
   - For async/callback operations in `Broker`, bind in-flight reservations or locks to the
     actual terminal worker callback—not merely the caller's wait timeout—so a timed-out
     caller cannot allow a second overlapping operation to corrupt shared keys or state.

---

## 4. Java / Kotlin Interop, Input Normalization & Code Hygiene

1. **Java compatibility & same-module Lombok**:
   - Adding a parameter with a default value in Kotlin or replacing a constructor in Java/Kotlin
     breaks binary/source compatibility unless `@JvmOverloads` is present or the original
     constructor signature is preserved.
   - In mixed Java/Kotlin modules (`broker4j`, `common4j`), `compileKotlin` executes before
     Java annotation processing (Lombok). Never rely on Lombok-generated `@Getter`/`@Setter`
     methods from Kotlin code in the *same* Gradle module without an explicit Java method
     declaration on that Java class.
2. **Boundary normalization**:
   - Strings crossing Activity/Bundle/IPC boundaries (such as `tenantId`) must be checked
     with `.trim()` / `isNotBlank()` consistently in both the router and the downstream
     configuration so whitespace-only values cannot pass one check and fail another.
3. **License headers**:
   - `common` / `common4j` / `msal` / `adal`: Include the standard Microsoft MIT license
     header block at the top of every new file.
   - `broker` / `broker4j`: Include `// Copyright (c) Microsoft Corporation. All rights reserved.`
4. **Diff coverage**:
   - CI enforces diff coverage (typically >= 80%) on modified classes. Exercise every new
     overload, fallback branch, and error-mapping path in unit tests.

---

## 5. Pull Request Checklist

- [ ] Branch name uses `<alias>/<descriptive-slug>` (or `users/<alias>/<slug>`).
- [ ] PR title ends with `, Fixes AB#<workItemId>` and body contains `Fixes AB#<workItemId>`.
- [ ] `CHANGELOG.md` `vNext` section updated (`MINOR` or `PATCH`) and matching
      `changelog:minor` or `changelog:patch` label applied.
- [ ] Target branch (`dev`) merged cleanly; if `Broker` depends on a `Common` change, the
      `common` submodule pointer references the committed `Common` revision.
