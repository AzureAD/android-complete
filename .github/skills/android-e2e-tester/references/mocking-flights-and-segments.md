# Mocking, Flights & Segment Testing — Getting an E2E Run Unblocked

Table of contents:
- [Principle: make it testable, don't fake a pass](#principle-make-it-testable-dont-fake-a-pass)
- [Temporary code changes — the revert discipline](#temporary-code-changes--the-revert-discipline)
- [Setting feature flags / flights](#setting-feature-flags--flights)
- [Mocking unavailable data or dependencies](#mocking-unavailable-data-or-dependencies)
- [Segment testing when the flow can't run end to end](#segment-testing-when-the-flow-cant-run-end-to-end)
- [Reporting mocked / segmented runs](#reporting-mocked--segmented-runs)

When a run is blocked by something that isn't a defect in the code under test — a feature flag that's
off, a server API that isn't deployed yet, a piece in the middle that isn't implemented — **don't stop at
"blocked" and don't fake a pass.** Get as much of the feature under real test as you can by setting the
flag, mocking the missing input, or testing the flow in segments. Temporary code changes are allowed for
this, provided they're reverted and never committed.

## Principle: make it testable, don't fake a pass

- Prefer the **most real** option available: real data > a faithful mock at the network boundary >
  a stub at the client boundary > a hardcoded value. The closer to real, the more the test proves.
- A mock must match the **actual contract** (field names, types, status codes) you're standing in for —
  a mock that doesn't match the agreed server/API shape proves nothing.
- Never let a mock silently turn a red flow green. State clearly (in the report) which parts were real
  and which were mocked, and what that means for confidence.

## Temporary code changes — the revert discipline

Making a temp change (flip a flag default, stub a response, inject a value) is fine **only** with this
discipline:

1. Keep the change **in the working tree only** — never `git commit`/`git push` it, and never stage it
   into a feature commit.
2. Leave a searchable marker at the edit site, e.g. `// TODO: REVERT — E2E mock, do not commit`.
3. **Revert after the run** — `git restore <file>` (or `git stash` the change while you commit real
   work, then drop it). Verify with `git status` that the tree is clean of test-only edits before you
   finish.
4. If the change is in a **library** the app consumes as a dependency, remember you must re-publish +
   rebuild for it to take effect — see
   [troubleshooting.md → Testing a local library change](troubleshooting.md#testing-a-local-library-change-publish-to-mavenlocal).

## Setting feature flags / flights

Get the flag into the state the scenario needs, cheapest option first:

1. **A runtime override the app already exposes** — a test-app UI toggle, a config/spinner, `adb shell
   setprop`, or a flights provider you can set at startup. Use this if it exists; no code change needed.
2. **Flip the flag's source default (temp).** If the app never installs a flights provider so every
   lookup returns the coded default (a real gap we hit — see
   [troubleshooting.md → Feature flags / flights](troubleshooting.md#feature-flags--flights-not-taking-effect)),
   the only lever is the **default** in the library: `MyFlight("Key", true)`. Flip it, re-publish +
   rebuild, run, then **revert**. Never commit the flip; the shipped default must stay as designed.
3. **Multiple flags:** set every flag the path reads, and confirm from logs that the flag-gated branch
   actually executes (don't assume the flip took — verify the code path ran).

## Mocking unavailable data or dependencies

When a step needs data you can't produce naturally (a server response for an API that isn't deployed, a
service you can't trigger from the device), mock it at the **highest-fidelity** point you can:

| Situation | Mock approach |
|---|---|
| Server API / endpoint not deployed yet | Point the client at a **local mock server** returning the agreed JSON, or use the repo's existing `MockWebServer`/interceptor test infra if present. Match the real contract exactly. |
| A single response **field** the server will add later | Inject it at the client's response-parsing boundary (temp code), using the agreed field name/shape, so the downstream path runs. |
| A collaborating app/broker you can't drive | Use a **mock broker** (`:mockcp`, `:mockauthapp`, `:mockltw` — see the app-and-module map) instead of the real one. |
| Data normally fetched at runtime (account, config) | Feed it via **adb intent extras** or a test hook the app exposes, or preload cache/prefs the app reads. |
| A push/callback you can't originate | Simulate the inbound intent/broadcast with `adb shell am start`/`am broadcast` carrying the expected payload. |

Rules: the mock must be faithful to the real contract; gate it behind the feature flag or a debug hook
where possible; mark and revert it (above); and record in the report that the segment ran against a mock.

## Segment testing when the flow can't run end to end

If a piece in the middle is genuinely missing and **can't** be mocked faithfully, don't declare the whole
feature blocked — **test the segments that can run**, each with its own success criterion:

1. **Segment before the gap:** drive the flow up to the boundary and assert the correct outputs at that
   boundary (the right request was made, the right state/telemetry was produced, the parked/queued item
   exists). That proves everything up to the gap.
2. **Segment after the gap:** feed the boundary inputs the missing piece *would* have produced (via a
   mock/fixture) and assert the rest of the flow proceeds correctly. That proves everything after the gap.
3. Together the two segments cover the feature minus the exact missing piece; name the gap precisely and
   what still needs an end-to-end pass once it's available.

This is strictly better than "blocked": you localize exactly what's untested (the gap) and prove the rest.

## Reporting mocked / segmented runs

In the Phase 7 report, always state:
- **What was real vs mocked** (which flag was flipped, which data/dependency was mocked, at which boundary).
- **Which segments passed** and the success signal for each (with correlation_ids where relevant).
- **The remaining gap** — the exact piece that still needs a true end-to-end pass, and what unblocks it
  (server API deployed, feature implemented, real credential/policy).
- **Confirmation that all temp changes were reverted** and the tree is clean.
