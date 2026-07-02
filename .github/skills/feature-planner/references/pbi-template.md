# PBI Description Template

Use this template for every PBI created by the feature-planner skill. The description must be
self-contained — a Copilot coding agent should be able to implement the PBI using only this
description and the repo's `copilot-instructions.md`.

---

## Template

```html
<h2>Objective</h2>
<p>Implement [what] in [which module] that [does what] for [purpose].</p>

<h2>Target Repository</h2>
<table>
  <tr><td><strong>Repo</strong></td><td>[org/repo-name]</td></tr>
  <tr><td><strong>Base Branch</strong></td><td>dev</td></tr>
  <tr><td><strong>Module</strong></td><td>[common | common4j | msal | AADAuthenticator | broker4j | adal | authenticator | 1ES-Pipelines]</td></tr>
</table>

<h2>Context</h2>
<p>[Why this change is needed. How it fits into the larger feature. What user problem it solves.]</p>

<h2>Feature Flag</h2>
<p>Wrap all new behavior behind <code>ExperimentationFeatureFlag.[FLAG_NAME]</code>.</p>

<h2>Technical Requirements</h2>
<ul>
  <li>[Specific implementation detail 1]</li>
  <li>[Specific implementation detail 2]</li>
  <li>[Pattern to follow — reference existing class/method if applicable]</li>
  <li>[Error handling requirements]</li>
  <li>[Telemetry/logging requirements]</li>
</ul>

<h2>Scope</h2>
<p><strong>In scope:</strong></p>
<ul>
  <li>[What to implement]</li>
</ul>
<p><strong>Out of scope:</strong></p>
<ul>
  <li>[What NOT to implement — important for constraining the agent]</li>
</ul>

<h2>Files to Modify/Create</h2>
<ul>
  <li><code>[path/to/existing/File.kt]</code> — [what to change]</li>
  <li><code>[path/to/new/File.kt]</code> — [new file, what it contains]</li>
</ul>

<h2>Acceptance Criteria</h2>
<ul>
  <li>[ ] [Functional criterion 1]</li>
  <li>[ ] [Functional criterion 2]</li>
  <li>[ ] Feature flag integration: behavior is off when flag is disabled</li>
  <li>[ ] Unit tests added for new/modified logic</li>
  <li>[ ] No new lint warnings introduced</li>
  <li>[ ] Compile check passes: <code>./gradlew :[module]:compile[Variant]Kotlin</code></li>
  <li>[ ] Unit tests pass: <code>./gradlew :[module]:test[Variant]UnitTest</code></li>
</ul>

<h2>Dependencies</h2>
<p>[List any PBIs that must be completed before this one, with AB# IDs]</p>
<p>Or: "None — this PBI can be implemented independently."</p>

<h2>Additional Context</h2>
<p>[Links to design docs, related PRs, or existing patterns to follow]</p>
```

---

## Example: Adding Retry Logic to IPC Token Request

```html
<h2>Objective</h2>
<p>Add automatic retry with exponential backoff for failed IPC token requests in the Common
module to improve reliability when the Broker process is temporarily unavailable.</p>

<h2>Target Repository</h2>
<table>
  <tr><td><strong>Repo</strong></td><td>AzureAD/microsoft-authentication-library-common-for-android</td></tr>
  <tr><td><strong>Base Branch</strong></td><td>dev</td></tr>
  <tr><td><strong>Module</strong></td><td>common</td></tr>
</table>

<h2>Context</h2>
<p>Users experience intermittent IPC failures when the Broker process is starting up or under
heavy load. Adding retry logic at the IPC layer in Common will transparently improve
reliability for all consumers (MSAL, OneAuth) without requiring changes in each client.</p>

<h2>Feature Flag</h2>
<p>Wrap all new behavior behind <code>ExperimentationFeatureFlag.IPC_RETRY_ENABLED</code>.</p>

<h2>Technical Requirements</h2>
<ul>
  <li>Add retry logic in the IPC strategy classes (BrokerAccountManagerStrategy or similar)</li>
  <li>Use exponential backoff: 500ms, 1s, 2s (3 attempts max)</li>
  <li>Only retry on transient IPC errors (DeadObjectException, RemoteException), not on auth errors</li>
  <li>Log each retry attempt using the Logger class with correlation ID</li>
  <li>Add telemetry span attribute for retry count</li>
  <li>Follow existing error handling patterns in the IPC layer</li>
</ul>

<h2>Scope</h2>
<p><strong>In scope:</strong></p>
<ul>
  <li>Retry logic with exponential backoff</li>
  <li>Logging and telemetry for retries</li>
  <li>Feature flag gating</li>
</ul>
<p><strong>Out of scope:</strong></p>
<ul>
  <li>UI-level retry indication</li>
  <li>Per-operation retry configuration</li>
  <li>Changes in MSAL or Broker repos</li>
</ul>

<h2>Files to Modify/Create</h2>
<ul>
  <li><code>common/src/main/java/com/microsoft/identity/common/internal/broker/ipc/IpcRetryPolicy.kt</code> — New file: retry policy with exponential backoff</li>
  <li><code>common/src/main/java/com/microsoft/identity/common/internal/broker/ipc/BrokerAccountManagerStrategy.java</code> — Wrap IPC calls with retry policy</li>
  <li><code>common/src/test/java/com/microsoft/identity/common/internal/broker/ipc/IpcRetryPolicyTest.kt</code> — New file: unit tests</li>
</ul>

<h2>Acceptance Criteria</h2>
<ul>
  <li>[ ] Retries on DeadObjectException and RemoteException</li>
  <li>[ ] Does not retry on AuthenticationException or other auth errors</li>
  <li>[ ] Respects max retry count of 3</li>
  <li>[ ] Backoff timing: 500ms, 1s, 2s</li>
  <li>[ ] Feature flag disables all retry behavior when off</li>
  <li>[ ] Logger output includes correlation ID and retry attempt number</li>
  <li>[ ] Unit tests cover: success first try, retry-then-success, max-retries-exceeded</li>
  <li>[ ] Compile check passes: <code>./gradlew :common:compileDebugKotlin</code></li>
  <li>[ ] Unit tests pass: <code>./gradlew :common:testDebugUnitTest</code></li>
</ul>

<h2>Dependencies</h2>
<p>None — this PBI can be implemented independently.</p>
```
