# CI/CD Pipeline Integration

Wire coverage generation, parsing, and (scheduled) Kusto ingestion into a pipeline. The
coverage logic is CI-agnostic — anything that can run tests and execute a Python script works.
This file gives concrete patterns for **Azure DevOps** and **GitHub Actions**.

## Table of contents
- [The four steps](#the-four-steps)
- [If no scheduled build exists yet](#if-no-scheduled-build-exists-yet)
- [Azure DevOps](#azure-devops)
- [GitHub Actions](#github-actions)
- [Multi-module / multi-repo aggregation](#multi-module--multi-repo-aggregation)

## The four steps
Every integration is the same shape, regardless of CI system:
1. **Run tests with coverage** so the build emits JaCoCo/Cobertura XML (see
   `references/coverage-generation.md`). Prefer reusing an existing test run.
2. **Parse** each XML → append to a shared NDJSON: `coverage_report.py parse ...`.
3. **Publish** the NDJSON + a Markdown summary as a build artifact (cheap; every run).
4. **Ingest** the NDJSON into Kusto — **only on the scheduled reporting run** (see the
   cost-control gate in `references/kusto-setup.md`).

Reporting/parse steps should be **non-fatal** (warn, don't fail) so a coverage hiccup never
breaks the build. Ingestion, when it runs, **should** fail loudly if it can't write the data.

## If no scheduled build exists yet
The trend needs a recurring run that executes the unit tests. If the team has no weekly/nightly
pipeline, create a minimal scheduled one whose only job is: build + run unit tests with
coverage + parse + ingest. Reuse it as the home for the coverage trend.

- **Azure DevOps**: add a `schedules:` block (cron) to a YAML pipeline, or configure the
  schedule in the pipeline's UI (Edit → Triggers) — UI schedules can't pass parameters, so
  rely on the runtime day/`Build.Reason` gate for Sunday-only ingestion.
- **GitHub Actions**: add `on: schedule: - cron:` to the workflow.

Pick a low-traffic time (e.g. Sunday early UTC) so the scheduled coverage run doesn't compete
with weekday CI.

## Azure DevOps
Minimal per-module steps (bash), assuming the coverage XML already exists:

```yaml
- bash: |
    nd="$(Build.ArtifactStagingDirectory)/coverage-all.ndjson"
    xml=$(find "$(Build.SourcesDirectory)" -type f -name '*.xml' | xargs grep -l 'JACOCO//DTD' | head -1)
    python3 scripts/coverage_report.py parse --input "$xml" --format jacoco \
      --repo "$(Build.Repository.Name)" --module "mymodule" \
      --branch "$(Build.SourceBranchName)" --commit "$(Build.SourceVersion)" \
      --build-id "$(Build.BuildNumber)" --out "$nd"
  displayName: Parse coverage
  continueOnError: true

- bash: |
    python3 scripts/coverage_report.py report \
      --input "$(Build.ArtifactStagingDirectory)/coverage-all.ndjson" \
      --metric LINE --goal 75 \
      --out-md "$(Build.ArtifactStagingDirectory)/coverage-summary.md"
    echo "##vso[task.uploadsummary]$(Build.ArtifactStagingDirectory)/coverage-summary.md"
  displayName: Build coverage summary
  continueOnError: true

- publish: $(Build.ArtifactStagingDirectory)
  artifact: coverage-report
```

Scheduled, cost-gated ingest (add a `forceIngestCoverage` boolean pipeline parameter,
default false):

```yaml
- task: AzureCLI@2
  displayName: Ingest coverage into Kusto
  inputs:
    azureSubscription: '<WIF-service-connection>'
    scriptType: bash
    scriptLocation: inlineScript
    inlineScript: |
      FORCE="${{ parameters.forceIngestCoverage }}"
      if [ "$FORCE" != "True" ] && [ "$FORCE" != "true" ]; then
        if [ "$(Build.Reason)" != "Schedule" ] || [ "$(date -u +%u)" != "7" ]; then
          echo "Skipping ingest (not a Sunday scheduled run)."; exit 0
        fi
      fi
      # ... NDJSON -> CSV -> POST .ingest (see references/kusto-setup.md) ...
```

## GitHub Actions
```yaml
on:
  schedule:
    - cron: '0 6 * * 0'   # 06:00 UTC every Sunday
  workflow_dispatch:
    inputs:
      forceIngestCoverage:
        type: boolean
        default: false

permissions:
  id-token: write          # for azure/login OIDC
  contents: read

jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.x' }
      - name: Run tests with coverage
        run: ./gradlew jacocoTestReport -PcodeCoverageEnabled=true
      - name: Parse coverage
        run: |
          xml=$(find . -name '*.xml' | xargs grep -l 'JACOCO//DTD' | head -1)
          python3 scripts/coverage_report.py parse --input "$xml" --format jacoco \
            --repo "${{ github.repository }}" --module mymodule \
            --branch "${{ github.ref_name }}" --commit "${{ github.sha }}" \
            --build-id "${{ github.run_number }}" --out coverage-all.ndjson
      - name: Build summary
        run: |
          python3 scripts/coverage_report.py report --input coverage-all.ndjson \
            --metric LINE --goal 75 --out-md coverage-summary.md
          cat coverage-summary.md >> "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v4
        with: { name: coverage-report, path: coverage-all.ndjson }
      - name: Ingest into Kusto (scheduled/forced only)
        if: github.event_name == 'schedule' || inputs.forceIngestCoverage
        run: |
          # azure/login (OIDC) then NDJSON -> CSV -> POST .ingest
          # see references/kusto-setup.md
```
GitHub `on: schedule` only fires Sunday here, so the `if:` is enough — no runtime day check
needed (unlike a daily ADO cron).

## Multi-module / multi-repo aggregation
- Append every module's rows to **one** NDJSON, then `report`/ingest once. Use `--repo` /
  `--module` per parse call so rows stay attributable.
- When each module publishes its own coverage artifact, download them all and locate each
  module's report by exact filename (handle re-runs by taking the newest by timestamp).
- Keep the coverage-artifact publish **gated by a variable** so only the pipelines that opt in
  (weekly/nightly) publish — release/hotfix pipelines shouldn't pay the cost.
