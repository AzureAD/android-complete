# Coverage Report Generation

How to make a build emit a machine-readable coverage report that
`scripts/coverage_report.py` can parse. The parser auto-detects **JaCoCo XML**,
**Cobertura XML**, and **LCOV** tracefiles, so the only job here is to get the build
tool to produce one of those.

## Table of contents
- [Which format do I have?](#which-format-do-i-have)
- [Gradle + JaCoCo (Java/Kotlin/Android)](#gradle--jacoco-javakotlinandroid)
- [Android Gradle Plugin specifics](#android-gradle-plugin-specifics)
- [Maven + JaCoCo](#maven--jacoco)
- [.NET (Coverlet / VSTest → Cobertura)](#net-coverlet--vstest--cobertura)
- [Python (coverage.py → Cobertura)](#python-coveragepy--cobertura)
- [Node/JS (nyc/jest → Cobertura or LCOV)](#nodejs-nycjest--cobertura-or-lcov)
- [C/C++ (lcov / gcovr)](#cc-lcov--gcovr)
- [Go (gocover-cobertura)](#go-gocover-cobertura)
- [Finding the report in CI](#finding-the-report-in-ci)

## Which format do I have?
- Root element `<report>` → **JaCoCo** (`--format jacoco`, or let auto-detect handle it).
- Root element `<coverage>` → **Cobertura** (`--format cobertura`).
- Text lines like `SF:`, `DA:`, `LF:`, `end_of_record` (usually `lcov.info` / `*.info`) →
  **LCOV** (`--format lcov`).

The parser reads the top-level aggregate `<counter>` totals (JaCoCo), the
`lines-covered`/`lines-valid`/`branches-*` attributes (Cobertura), or the summed
`LF`/`LH` + `BRF`/`BRH` records (LCOV; falls back to counting `DA:` records when the
summaries are absent). It does **not** need per-class/per-file detail, so any
correctly-formed report works.

## Gradle + JaCoCo (Java/Kotlin/Android)
Apply the plugin and ensure a report task emits XML:

```groovy
plugins { id 'jacoco' }
jacoco { toolVersion = "0.8.10" }

tasks.named('jacocoTestReport') {
    dependsOn test            // or the flavor-specific unit test task
    reports {
        xml.required = true   // REQUIRED - this is what the parser reads
        html.required = true  // optional, for humans
    }
}
```

Run: `./gradlew jacocoTestReport` → XML at
`build/reports/jacoco/jacocoTestReport/jacocoTestReport.xml`.

**Gate coverage behind a flag** so normal builds aren't slowed down:
```groovy
def enableCodeCoverage = project.hasProperty("codeCoverageEnabled")
    ? codeCoverageEnabled.toBoolean() : false
tasks.withType(Test) { jacoco { enabled = enableCodeCoverage } }
```
Then in CI: `./gradlew jacocoTestReport -PcodeCoverageEnabled=true`.

**Reuse existing test runs.** If the build already runs a coverage task (many Android
setups have `<flavor>UnitTestCoverageReport`), do NOT add a second test run — point the
parser at the XML that task already produces. Re-running tests just to get coverage
doubles CI time.

## Android Gradle Plugin specifics
- AGP unit-test coverage is per **build variant**: `testDebugUnitTest` → JaCoCo exec →
  `create<Variant>UnitTestCoverageReport`. Use the variant your CI actually builds
  (e.g. `dist`/`release`), not `localDebug`, or the `.exec` won't exist and the report
  will be empty.
- `includeNoLocationClasses = true` is required for **Robolectric** tests.
- Modules with no `src/test` sources produce no report — skip them (don't fail the build).
- Robolectric/instrumented mixes: only unit-test coverage flows through JaCoCo XML here;
  instrumented (Espresso) coverage needs a connected device and is out of scope for the
  weekly trend.

## Maven + JaCoCo
```xml
<plugin>
  <groupId>org.jacoco</groupId>
  <artifactId>jacoco-maven-plugin</artifactId>
  <version>0.8.10</version>
  <executions>
    <execution><goals><goal>prepare-agent</goal></goals></execution>
    <execution><id>report</id><phase>test</phase><goals><goal>report</goal></goals></execution>
  </executions>
</plugin>
```
Run `mvn test` → XML at `target/site/jacoco/jacoco.xml`.

## .NET (Coverlet / VSTest → Cobertura)
```bash
dotnet test --collect:"XPlat Code Coverage" -- \
  DataCollectionRunSettings.DataCollectors.DataCollector.Configuration.Format=cobertura
```
Emits `**/TestResults/**/coverage.cobertura.xml`. Parse with `--format cobertura`.

## Python (coverage.py → Cobertura)
```bash
coverage run -m pytest
coverage xml   # -> coverage.xml (Cobertura)
```

## Node/JS (nyc/jest → Cobertura or LCOV)
Either format works — the parser reads both natively.
```bash
# jest — Cobertura
jest --coverage --coverageReporters=cobertura   # -> coverage/cobertura-coverage.xml
# jest — LCOV (often already the default)
jest --coverage --coverageReporters=lcov         # -> coverage/lcov.info
# nyc
nyc --reporter=cobertura npm test                # or --reporter=lcovonly -> coverage/lcov.info
```

## C/C++ (lcov / gcovr)
```bash
# lcov -> lcov.info (parse with --format lcov / auto-detect)
lcov --capture --directory . --output-file coverage.info
# or gcovr -> Cobertura
gcovr --cobertura -o coverage.xml
```

## Go (gocover-cobertura)
```bash
go test -coverprofile=cover.out ./...
go run github.com/boumenot/gocover-cobertura < cover.out > coverage.xml
```

## Finding the report in CI
When report paths vary per module, locate JaCoCo reports by content rather than a fixed path:
```bash
grep -rlZ --include='*.xml' 'JACOCO//DTD' "$SOURCES_DIR" | tr '\0' '\n'
```
For Cobertura, match on the `<coverage` root or the well-known filename
(`coverage.cobertura.xml` / `cobertura-coverage.xml` / `coverage.xml`). For LCOV, match the
well-known filename (`lcov.info` / `*.info`) or grep for `end_of_record`.
