<!-- Copyright (c) Microsoft Corporation. All rights reserved. -->

# Android Research Standards

Explore this Android authentication multi-repo codebase systematically with evidence-based findings.
Use the tools and output contract supplied by the caller. This reference provides domain
methodology, not orchestration, approval, or tool permissions. Missing relevant sources are
evidence gaps; never infer their contents or bypass a denied read.

## Repository Structure

This workspace contains multiple sub-repositories:

| Module | Purpose | Key Paths |
|--------|---------|-----------|
| **MSAL** | Client authentication library | `msal/msal/src/main/java/com/microsoft/identity/client/` |
| **Broker** | Brokered authentication service | `broker/AADAuthenticator/src/main/java/com/microsoft/identity/broker/` |
| **Common** | Shared utilities + IPC logic | `common/common/src/main/java/com/microsoft/identity/common/` |
| **ADAL** | Legacy auth library | `adal/adal/src/main/java/com/microsoft/aad/adal/` |
| **OneAuth** | 1P apps library (external) | `oneauth/` |
| **Authenticator** | Microsoft Authenticator app (MFA, Brooklyn, VerifiedID) | `authenticator/` |
| **1ES-Pipelines** | Production CI/CD pipeline YAML | `1ES-Pipelines/production/`, `1ES-Pipelines/templates/`, `1ES-Pipelines/scripts/` |

**⚠️ CRITICAL: Always search across ALL repositories.** Code is often duplicated or shared.

Authenticator app behavior is distinct from broker/MSAL/common/ADAL auth-library behavior.
Use app-specific evidence for MFA, Brooklyn, VerifiedID/FaceCheck, push notifications, and
app-level UI. Report missing app evidence rather than treating library evidence as equivalent.

## Authentication Flow

```
Client App → MSAL/OneAuth → Common (IPC) → Broker → eSTS → Broker → Common → MSAL/OneAuth → Client App
```

## Core Principles

1. **Never guess** - Only report what is actually found in the repo
2. **Always cite sources** - Every finding must include file path and line numbers
3. **Acknowledge gaps** - Explicitly state when something cannot be found
4. **Rate confidence** - Assign HIGH/MEDIUM/LOW to each finding
5. **Search all modules** - Check MSAL, Broker, Common, ADAL, and 1ES-Pipelines for each query

## Research Workflow

### Step 1: Understand the Target

Clarify what to find:
- Feature/concept name
- Which layer (client MSAL, IPC Common, or Broker)
- Expected patterns (class names, function signatures)

### Step 2: Search Strategy (Multi-Repo)

First identify the target project and the relevant cross-repository boundaries.
Search only available, authorized sources with the caller's permitted tools.

Execute searches in this order, **always searching across all modules**:

1. **Semantic search** - Start with natural language query
2. **Grep search** - Exact patterns, class names, error codes
3. **File search** - Find by naming convention (e.g., `**/*Operation*.kt`)
4. **Directory exploration** - List relevant directories in each module
5. **Read files** - Confirm findings with actual code

### Step 3: Validate Findings

For each potential finding:
- Read the actual code (don't rely only on search snippets)
- Identify which module it belongs to (MSAL/Broker/Common)
- Note the exact location (file + line range)
- Assess confidence level

### Step 4: Report Results

Return facts, source references, confidence, conflicts, assumptions, and missing evidence
in the caller's requested structure. Record checked-out revisions when provided/available.
Do not treat a research result as approval or start a downstream phase.

## Confidence Levels

| Level | Criteria |
|-------|----------|
| **HIGH** | Exact match found. Code clearly implements the requested feature. Function/class names match. |
| **MEDIUM** | Likely match. Code appears related but naming differs, or implementation is partial. |
| **LOW** | Possible match. Found tangentially related code, or inference required. |

## Key Classes by Domain

### Authentication Entry Points
- `PublicClientApplication` (MSAL) - Client-facing API
- `BrokerMsalController` (Common) - Routes requests to Broker via IPC
- `LocalMSALController` (Common) - Non-brokered auth fallback

### Broker Operations
- `AcquireTokenSilentMsalBrokerOperation` (Broker) - Silent token flow
- `GetIntentForInteractiveRequestMsalBrokerOperation` (Broker) - Interactive flow
- `IBrokerOperation` (Broker) - Operation interface

### IPC & Results
- `BrokerOperationExecutor` (Common) - Executes broker operations
- `MsalBrokerResultAdapter` (Common) - Converts results for IPC
- `BrokerResult` (Common) - IPC response object

## Data Flow Investigation

When asked questions about **what data is returned**, **how data flows**, or **what happens to data**, follow this systematic investigation approach.

### Complete Flow Investigation Strategy

**Trigger phrases:** "Is X returned to Y?", "Does Y receive Z?", "What happens to [field]?", "How does [data] flow?"

**Step-by-step Process:**

1. **Find the Data Structure** (e.g., `BrokerResult`, `TokenResponse`)
   - Confirm the field exists in the data class
   - Check serialization annotations (`@SerializedName`)

2. **Find the Construction/Population Code** ⚠️ **CRITICAL - Don't skip this!**
   - Search for `Builder` or factory methods that create the object
   - Search for where the field is actually set (e.g., `.refreshToken(`)
   - Look in adapter/converter classes (e.g., `*ResultAdapter`, `*Converter`)

3. **Check for Conditional Logic** ⚠️ **CRITICAL - Don't skip this!**
   - Search for `if` statements around the field assignment
   - Look for account type checks (e.g., `MSA_MEGA_TENANT_ID`, `accountType`)
   - Look for protocol version checks
   - Look for flight/feature flag checks (`CommonFlightsManager`, `isFlightEnabled`)

4. **Trace the Complete Flow**
   - Follow from entry point → IPC → processing → response construction → IPC → return
   - Verify no filtering/scrubbing happens in any layer

### Key Classes for Flow Investigation

**Response Construction & Adaptation:**
- `MsalBrokerResultAdapter` (Common) - Converts authentication results to BrokerResult for IPC
- `AdalBrokerResultAdapter` (Common) - ADAL version of result adapter
- `BrokerResult` (Common) - The IPC response object sent to MSAL/OneAuth
- `BrokerResultAdapter` - Generic adapter interfaces

**Account Type Detection:**
- Check for `MSA_MEGA_TENANT_ID` constant (`"9188040d-6c67-4c5b-b112-36a304b66dad"`)
- Check for `CONSUMERS` constant in authorities
- Look for `accountType` or `realm` field checks

### Flow Investigation Pitfalls

❌ **DON'T** stop after finding a field definition - this only confirms structure, not behavior
❌ **DON'T** assume data flows unchanged - always check for filtering/transformation logic
❌ **DON'T** ignore protocol version checks - behavior often changes based on negotiated version
❌ **DON'T** forget to check flight flags - features are often gated behind flights

✅ **DO** search for Builder usage and construction patterns
✅ **DO** search for the field name in assignment context (e.g., `.setField(`, `.field(`)
✅ **DO** look for `Adapter` or `Converter` classes in the flow
✅ **DO** check for conditional logic based on account type, protocol version, or flights

## Scenario-Scoped Defect Surfaces

When a code-path trace is produced for an incident investigation, the deliverable often includes a "defect surface" list — spots where a defect could plausibly cause the observed symptom. **Filter this list aggressively to the scenario.** A 10+ item list enumerating every conceivable failure mode in every cited file is noise; a 3–6 item list ranked by relevance to the *specific* scenario is signal.

### Filter rules

Keep a defect-surface entry only if **all** of these are true:

1. **The branch is reachable in the scenario being investigated.** Drop entries that depend on flights/protocol versions/account types that don't match the customer's setup.
2. **The failure mode produces the observed symptom.** Drop entries whose failure mode would produce a *different* symptom than what the IcM reports.
3. **The entry cites a specific branch or call**, not just "this file is involved." If the description is "this layer does X — could fail," it belongs in the trace narrative, not the defect surface.

### Anti-pattern (avoid)

> 10. **Scope normalisation inconsistency between MSAL & broker** — `<some strategy class>`. If MSAL normalizes scopes differently than broker, cache mismatch could occur causing eSTS to reject.

Problems: no evidence the customer's app is hitting this branch; "could occur" is generic speculation; the failure mode (eSTS rejection) doesn't match a "silent request never reached eSTS" symptom.

### Good example

> 1. **PRT lookup keyed by account + authority** — `<PRT controller>.<loadPrt method>` (cite the exact file:line from your local broker source). If the second-leg request supplies a different `authority` than the PRT was issued under (sovereign cloud, common-vs-tenanted), the lookup returns null and the silent path falls through to interactive — matching the IcM's missing-silent-redeem symptom exactly.

Reasons it works: cites a specific branch; the failure mode (lookup returns null) directly produces the observed symptom; scenario-grounded language.

> **Note on citations:** in the *delivered* report, cite the concrete `file:line` you verified in the broker source. Keep those private-repo paths in the investigation artifact, not in this shared skill.

### Rule of thumb

If you find yourself writing more than 6 defect-surface entries, you're probably enumerating possibilities rather than filtering them. Re-read the IcM, drop entries whose failure mode doesn't match the symptom, and re-rank.

### Flow Investigation Search Patterns

**Finding construction code:**
```
grep_search: "new BrokerResult.Builder" or ".refreshToken("
file_search: *Adapter.java, *Converter.java, *Builder.java
```

**Finding conditional logic:**
```
grep_search: "if.*accountType" or "if.*MSA" or "shouldRemove" or "shouldInclude"
```

**Finding flight checks:**
```
grep_search: "isFlightEnabled" or "CommonFlight." or "FlightsManager"
```

### Example Flow Investigation

**Question:** "Is refresh token returned to OneAuth?"

**Process:**
1. ✅ Find `BrokerResult` class → Confirm `mRefreshToken` field exists
2. ✅ Search for `BrokerResult.Builder` usage → Find `MsalBrokerResultAdapter`
3. ✅ Read `buildBrokerResultFromAuthenticationResult()` method → Find conditional logic
4. ✅ Check `shouldRemoveRefreshTokenFromResult()` → Discover:
   - Flight check: `STOP_RETURNING_AAD_RT_BACK_TO_CALLING_APP`
   - Protocol version check: `>= 16.0`
   - Account type check: Remove for AAD, keep for MSA
5. ✅ **Complete Answer:** RT is conditionally returned based on account type, flight, and protocol version

## Search Patterns for This Repo

### Finding Broker Operations
```
file_search: **/broker/**/operation/**/*.kt
grep_search: class.*BrokerOperation|IBrokerOperation
```

### Finding Controllers
```
file_search: **/controllers/**/*.java
grep_search: class.*Controller|BrokerMsalController
```

### Finding Error Handling
```
grep_search: ErrorStrings|ClientException|error_code
file_search: **/exception/**/*.java
```

### Finding Feature Flags
```
grep_search: isFlightEnabled|CommonFlight|FlightsManager
grep_search: ExperimentationFeatureFlag
```

### Finding IPC Logic
```
grep_search: BrokerOperationBundle|IIpcStrategy|BoundServiceStrategy
file_search: **/broker/ipc/**/*.java
```

### Finding Pipeline Logic (1ES-Pipelines)

> **Important:** `1ES-Pipelines/` is in `.gitignore`. Always use `includeIgnoredFiles: true` when searching with `grep_search`.

```
file_search: 1ES-Pipelines/**/*.yml
grep_search: "template:" or "- stage:" or "trigger:" in 1ES-Pipelines/ (includeIgnoredFiles: true)
```

Key pipeline IDs: `2519` (monthly-release), `2828` (start-monthly-release), `3076` (ui-automation)

### Finding Release Templates
```
file_search: 1ES-Pipelines/templates/**/*.yml
grep_search: "parameters:" in 1ES-Pipelines/templates/ (includeIgnoredFiles: true)
```

### Finding Pipeline Scripts
```
file_search: 1ES-Pipelines/scripts/**/*.py
file_search: 1ES-Pipelines/scripts/**/*.ps1
```

### Pipeline Architecture Patterns
- **Cross-pipeline triggering**: Parent pipelines (monthly-release, weekly-validation) use `scripts/queue-build.ps1` to trigger child pipelines. The script queues an ADO build via REST API and polls for completion.
- **Cross-pipeline artifact download**: Child pipelines receive `sourcePipelineId`/`sourceBuildId` as template parameters, threaded through the template chain down to `run-on-firebase.yml`/`run-on-firebase-with-flank.yml`, which use `DownloadPipelineArtifact@2` to pull artifacts from the parent pipeline.
- **1ES compliance**: Production pipelines extend `v1/1ES.Official.PipelineTemplate.yml@1ESPipelineTemplates`, non-production extend `v1/1ES.Unofficial.PipelineTemplate.yml@1ESPipelineTemplates`.

When investigating CI/CD pipelines, release processes, or build issues:
1. Search `1ES-Pipelines/` for production pipeline YAML
2. Search `azure-pipelines/` for legacy pipeline YAML
3. Search `scripts/release/` for PowerShell/Python release scripts
4. Verify referenced templates/scripts against current source rather than relying on historical pipeline descriptions

## Anti-Patterns to Avoid

| Anti-Pattern | Problem | Correct Approach |
|--------------|---------|------------------|
| Searching only one module | Miss cross-module code | Search MSAL, Broker, Common, ADAL, 1ES-Pipelines |
| "This is likely in..." | Speculation without evidence | Search first, report only what's found |
| Path without line numbers | Imprecise, hard to verify | Always include line numbers |
| Stopping at field definition | Misses conditional logic | Trace to Builder/Adapter for full behavior |
| Ignoring protocol versions | Behavior changes by version | Check for version conditionals |

## Example: Token Caching Research

**Request**: "Where is token caching implemented?"

**Process**:
1. `semantic_search("token cache implementation")` → Found `TokenCacheAccessor`
2. `grep_search("TokenCacheAccessor")` → Found in common + msal
3. `file_search("**/cache/**/*.java")` → Found cache directories
4. `read_file` on matches → Confirmed locations

This is an illustrative search sequence, not verified evidence for a new task.
Return the actual checked source locations and confidence in the caller's schema;
do not copy example citations or add a downstream phase handoff.
