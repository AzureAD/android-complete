---
name: codebase-researcher
description: Systematically explore unfamiliar codebases to find implementations, patterns, and architecture. Use this skill when asked to find code, locate implementations, understand how features work, trace call flows, or explore project structure. Triggers include "where is X implemented", "find the code for Y", "how does Z work in this repo", "trace the flow of", "show me the implementation of", or any request requiring codebase exploration with evidence-based findings.
---

# Codebase Researcher

Explore this Android authentication multi-repo codebase systematically with evidence-based findings.

## Repository Structure

This workspace contains multiple sub-repositories:

| Module | Purpose | Key Paths |
|--------|---------|-----------|
| **MSAL** | Client authentication library | `msal/msal/src/main/java/com/microsoft/identity/client/` |
| **Broker** | Brokered authentication service | `broker/AADAuthenticator/src/main/java/com/microsoft/identity/broker/` |
| **Common** | Shared utilities + IPC logic | `common/common/src/main/java/com/microsoft/identity/common/` |
| **ADAL** | Legacy auth library | `adal/adal/src/main/java/com/microsoft/aad/adal/` |
| **OneAuth** | 1P apps library (external) | `oneauth/` |

**⚠️ CRITICAL: Always search across ALL repositories.** Code is often duplicated or shared.

## Authentication Flow

```
Client App → MSAL/OneAuth → Common (IPC) → Broker → eSTS → Broker → Common → MSAL/OneAuth → Client App
```

## Core Principles

1. **Never guess** - Only report what is actually found in the repo
2. **Always cite sources** - Every finding must include file path and line numbers
3. **Acknowledge gaps** - Explicitly state when something cannot be found
4. **Rate confidence** - Assign HIGH/MEDIUM/LOW to each finding
5. **Search all modules** - Check MSAL, Broker, Common, ADAL for each query

## Research Workflow

### Step 1: Understand the Target

Clarify what to find:
- Feature/concept name
- Which layer (client MSAL, IPC Common, or Broker)
- Expected patterns (class names, function signatures)

### Step 2: Search Strategy (Multi-Repo)

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

Use the output format below.

## Output Format

```markdown
## Research: [Topic]

### Findings

#### Finding 1: [Brief description]
- **Module**: MSAL | Broker | Common | ADAL
- **File**: [path/to/file.ext](path/to/file.ext#L10-L25)
- **Confidence**: HIGH | MEDIUM | LOW
- **Evidence**: [What makes this the right code]

[Code snippet if helpful]

#### Finding 2: ...

### Not Found

- [Thing that was searched for but not located]
- Search attempts: [what was tried]

### Suggested Next Steps

- [Additional areas to explore]
- [Related code that might be relevant]
```

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

## Anti-Patterns to Avoid

| Anti-Pattern | Problem | Correct Approach |
|--------------|---------|------------------|
| Searching only one module | Miss cross-module code | Search MSAL, Broker, Common, ADAL |
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

**Output**:
```markdown
## Research: Token Caching Implementation

### Findings

#### Finding 1: MsalOAuth2TokenCache - MSAL cache implementation
- **Module**: Common
- **File**: [common/common/src/.../cache/MsalOAuth2TokenCache.java](common/common/src/.../cache/MsalOAuth2TokenCache.java#L45-L120)
- **Confidence**: HIGH
- **Evidence**: Core cache class with `save()`, `load()`, `remove()` methods

#### Finding 2: SharedPreferencesAccountCredentialCache - Persistence
- **Module**: Common
- **File**: [common/common/src/.../cache/SharedPreferencesAccountCredentialCache.java](...)
- **Confidence**: HIGH
- **Evidence**: SharedPreferences-based storage for credentials

### Not Found

- Distributed/remote caching
- Search attempts: grep "Redis", "remote.*cache", "distributed"

### Suggested Next Steps

- Check `BrokerOAuth2TokenCache` for broker-specific caching
- Review cache encryption in `AndroidAuthSdkStorageEncryptionManager`
```
