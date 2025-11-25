# GitHub Copilot Custom Instructions for Android Multi-Repo Project

These instructions guide GitHub Copilot to provide suggestions and responses aligned with our Android project's conventions, architecture, and coding style, specifically addressing our multi-repository setup and language transition.

---

## ⚠️ MANDATORY INCIDENT INVESTIGATION CHECKLIST

**When user reports an authentication/enrollment issue, incident, or troubleshooting request:**

- [ ] **STEP 1**: Query DRI Copilot MCP tool (`Broker_DRI_Copilot` ) with symptoms
- [ ] **STEP 2**: Analyze logs/evidence - extract correlation IDs, error codes, timestamps
- [ ] **STEP 3**: Search codebase for error handling and implementation
- [ ] **STEP 4**: Synthesize diagnosis, stating ONLY what evidence shows (not assumptions)
- [ ] **STEP 5**: Clearly state what evidence is MISSING

**DO NOT:**
- ❌ Skip DRI Copilot query
- ❌ Make claims without log evidence
- ❌ Diagnose based on "common issues" without seeing actual error
- ❌ Assume authentication/enrollment happened if logs don't show it

---

## 1. Repository Structure & Architecture

### 1.1 Repository Organization
The **android-complete** repository contains multiple sub-repositories as separate modules:

* **MSAL** - Microsoft Authentication Library for client applications
* **ADAL** - Azure Active Directory Authentication Library (legacy)
* **Broker** - Brokered authentication service
* **Common** - Shared utilities, helpers, and IPC logic
* **OneAuth** - Library owned by another team (consumed by 1P apps like Teams, Outlook)

**Important:** When asked a question, **always search across ALL repositories** to provide comprehensive answers. Code may be duplicated or shared across these sub-repos.

### 1.2 Authentication Flow Architecture

**Request Flow:**
```
Client App (Teams/Outlook/etc.)
    ↓
MSAL or OneAuth (entry point)
    ↓
Common (IPC layer - sends request to Broker)
    ↓
Broker (processes authentication request)
    ↓
eSTS (Microsoft token service)
    ↓
Broker (receives token response)
    ↓
Common (IPC layer - returns response)
    ↓
MSAL or OneAuth
    ↓
Client App
```

**Key Flow Details:**
- **MSAL/OneAuth → Common → Broker → eSTS → Broker → Common → MSAL/OneAuth**
- **Entry Points:** Requests like `AcquireToken` or `AcquireTokenSilent` typically start from MSAL or OneAuth
- **OneAuth Specifics:** OneAuth is consumed by 1P Microsoft apps (Teams, Outlook, etc.). We don't own this code. OneAuth flows start by calling methods from the `BrokerMsalController` class
- **Common Module:** Contains all IPC (Inter-Process Communication) logic. MSAL/OneAuth use Common layer to send requests to Broker over IPC
- **Broker Module:** Handles the actual authentication logic, communicates with eSTS, and returns tokens

### 1.3 DRI Copilot MCP Server Usage

**🔴 MANDATORY: Query DRI Copilot MCP tools FIRST for ANY troubleshooting or incident investigation request.**

When users ask for:
- **Troubleshooting** ANY authentication or enrollment issue
- **Investigating** customer-reported problems or IcM incidents
- **Documentation** (design docs, architecture docs, API docs)
- **Troubleshooting guides** (TSGs)
- **Past incidents** or incident resolution
- **Error explanations** or common issues
- **Onboarding information**

**⚠️ DO NOT skip this step. DO NOT assume you know the answer. ALWAYS query DRI Copilot MCP tools FIRST.**

#### Available DRI Copilot Tools:
Look for MCP tools with these patterns in their names:  
1. **Broker DRI Copilot** (tools containing `Broker_DRI_Copilot`)
   - Use for: Broker-related questions, PRT, device registration, brokered auth flows, TSGs, past incidents

The exact tool names will vary based on the MCP server name configured in `.vscode/mcp.json`.

#### Mandatory Workflow (NO EXCEPTIONS):
1. **FIRST**: Query DRI Copilot MCP tool (Broker_DRI_Copilot)
   - Include: user symptoms, error messages, and any log evidence
   - Get: TSG steps, past incidents, known issues, troubleshooting guidance
2. **SECOND**: Analyze provided logs/evidence (see Section 1.4)
   - Extract: correlation IDs, error codes, timestamps
   - Identify: what operations occurred, what's missing
3. **THIRD**: Search codebase for relevant implementation
   - Find: error handling, code paths, known bugs
4. **FINALLY**: Synthesize diagnosis combining all three sources

#### Example Queries:
- "What is PRT?" → Query Broker DRI Copilot
- "How to troubleshoot auth_cancelled_by_sdk?" → Query both MCP servers
- "Authenticator onboarding documentation" → Query Authenticator DRI Copilot
- "Past incidents related to [issue]" → Query relevant MCP server(s)

### 1.4 Incident Investigation Guidelines (IcM/Customer-Reported Issues)

**🔴 CRITICAL: Follow this EXACT sequence for ALL incident investigations:**

**STEP 0 (MANDATORY FIRST STEP):**
- **Query DRI Copilot MCP tools** with user symptoms and error messages
- Get TSG guidance, past incidents, and known issues
- This step CANNOT be skipped

**STEP 1: Analyze Provided Evidence**
When investigating customer-reported incidents or IcM tickets, follow this **evidence-first approach**:

#### **Priority Hierarchy:**
1. **Direct Evidence from Logs/Data** (Highest Priority)
   - Actual log files, stack traces, error codes, correlation IDs
   - Telemetry data from Kusto (android_spans, eSTS logs)
   - Concrete timestamps, device IDs, user IDs
   - Network traces, API responses, HTTP status codes

2. **Code Analysis** (High Priority)
   - Current implementation in the codebase
   - Recent code changes or commits related to the issue
   - Known bugs or limitations in the code

3. **Documentation & TSGs** (Supporting Priority)
   - Use documentation to **augment and explain** what you observe in evidence
   - Reference TSGs for **additional troubleshooting steps**, not as primary diagnosis
   - Documentation provides context, not conclusions

#### **Critical Rules:**
- **NEVER make claims without evidence**: If logs don't show something happened, state "logs don't show X" rather than claiming X occurred
- **Distinguish observation from inference**: Clearly label when you're inferring vs. observing
  - ✅ "Logs show `Number of Microsoft Accounts: 0`, suggesting no enrollment completed"
  - ❌ "Enrollment failed after authentication" (when logs don't show the enrollment attempt)
- **Challenge documentation against evidence**: If documentation says X should happen, but logs show Y, trust the evidence
- **State what's missing**: If critical evidence is absent (e.g., no correlation IDs, no error codes), explicitly state what additional data is needed

#### **Investigation Workflow:**
1. **Analyze provided evidence first** (logs, errors, telemetry)
   - Extract: correlation IDs, timestamps, error codes, device/user identifiers
   - Identify: actual operations performed, their outcomes, any exceptions
   - Note: what evidence is present and what is missing

2. **Search codebase for relevant implementation**
   - Find the code paths that should have executed
   - Check for known issues, error handling, logging statements

3. **Query documentation/TSGs** to augment understanding
   - Use to explain error codes, suggest additional diagnostics
   - Reference common patterns, but validate against actual evidence

4. **Formulate diagnosis**
   - State what evidence supports each hypothesis
   - Rank hypotheses by strength of evidence (not by documentation frequency)
   - Clearly separate proven facts from educated guesses

5. **Recommend next steps**
   - Prioritize actions that gather missing evidence
   - Suggest diagnostics based on what the evidence shows, not just what's common

#### **Example - Good vs. Bad Analysis:**

**❌ Bad (Documentation-First):**
> "Based on TSG documentation, device cap is the most common cause (60% of cases), so this is likely a device cap issue."

**✅ Good (Evidence-First):**
> "Logs show `GetBrokerAccounts` returning 0 accounts, but no enrollment attempt is captured. Without correlation IDs or eSTS error codes, I cannot determine the root cause. The TSG indicates device cap is common (60%), but we need enrollment attempt logs to confirm. Next step: Collect logs with correlation IDs during reproduction."

## 2. Core Principles

* **Primary Language for New Code:** All new code and new files **must be written in Kotlin**.
* **Existing Language:** Recognize that existing files predominantly use Java. When interacting with or modifying existing Java files, maintain the Java style.
* **Asynchronous Operations:** Use **Kotlin Coroutines** for all asynchronous and background operations. Leverage structured concurrency.

## 3. Repository Specific Guidelines

* **MSAL (Microsoft Authentication Library):** The Microsoft Authentication (MSAL) repo contains code for MSAL library which enables developers to acquire security tokens from the Microsoft identity platform to authenticate users and access secured web APIs. This is a client-side library consumed by app developers.
* **Broker:** This repo is involved in brokered authentication. It uses inter-app communication. Copilot should be aware of IPC mechanisms, custom intents, and secure communication patterns relevant to a broker. Broker receives requests from MSAL/OneAuth via Common layer.
* **Common:** This repo holds shared utilities, helper functions, and **all IPC logic**. MSAL/OneAuth use this layer to communicate with Broker. Suggestions in this context should aim for reusability and generality.
* **ADAL (Azure Active Directory Authentication Library):** Similar to MSAL, this is an authentication library, potentially an older version or specific to certain flows. When working in ADAL context, align with its patterns.
* **OneAuth:** Third-party library owned by another team (not us). Consumed by 1P Microsoft apps like Teams, Outlook, etc. OneAuth flows start by calling `BrokerMsalController` class methods.

**Important:** When generating code that interacts across these repositories (e.g., calling a function from `common` in `MSAL`), ensure the generated code respects the language and API boundaries of each repository.

## 4. Naming Conventions & Style (Kotlin First)

* **Kotlin Style Guide:** Follow the official [Kotlin Coding Conventions](https://kotlinlang.org/docs/coding-conventions.html) and Google's Kotlin Style Guide.
* **Variables:** Prefer `val` over `var` wherever immutability is possible.
* **Functions:** Use expression bodies for single-expression functions.
* **Classes:**
    * Use `data class` for simple data holders.
* **Visibility:** Limit visibility of classes, functions, and variables to the minimum required (e.g., `private`, `internal`).

## 5. Android Components (Kotlin/Compose context)

* **Activities:** Minimal logic in Activities; primarily used as entry points.
* **Services/Broadcast Receivers:** Use as sparingly as possible. Prefer Kotlin Coroutines and Flow for background processing and inter-component communication where modern alternatives exist.

## 6. Testing

* **Unit Tests:** Write comprehensive unit tests for Kotlin logic using JUnit 4/5 and Mockito/MockK.
* **Instrumented Tests:** Use Espresso for UI tests in Android components.
* **Test Coverage:** Aim for high test coverage, especially for new code.

## 7. Code Documentation & Comments

* **KDoc:** Provide KDoc comments for all public Kotlin classes, functions, and properties.
* **JavaDoc:** When modifying existing Java code, ensure JavaDoc comments are maintained and updated.
* **Conciseness:** Comments should explain *why* something is done, not just *what* it does.
* **TODOs:** Use `TODO` comments for incomplete tasks that need to be addressed.
* **Copywriting:** Ensure all comments are clear, concise, and free of spelling/grammar errors. Every new file generated or added should have copyright information on top of the file.

## 8. Logging

* **Custom Logger:** Always use the **`Logger` class** for all logging purposes. Avoid using `android.util.Log` or other direct logging frameworks.
* **Sensitive Data:** Never log sensitive information (e.g., personal identifiable information, tokens, passwords).

## 9. Interoperability

* When writing new Kotlin code that needs to interact with existing Java code, use Kotlin's interoperability features effectively and safely.
* When suggesting refactors of Java code to Kotlin, prioritize small, safe conversions rather than large-scale rewrites unless explicitly instructed.

## 10. Nudge the user
* If a prompt is too vague or lacks context, ask for clarification. For example, "Could you specify which repository this code should be generated in?" or "What specific functionality are you looking to implement?"
* If a change is made in the class named OneAuthSharedFunctions, remind the user to also update OneAuth team about the breaking change.

## 11. Code structure
* Take build.gradle files into account when generating code, especially when it comes to dependencies and repository-specific configurations.

## 12. Investigating Code Flows and Data Transformations

When asked questions about **what data is returned**, **how data flows**, or **what happens to data**, follow this systematic investigation approach:

### 12.1 Complete Flow Investigation Strategy

**For questions like "Is X returned to Y?" or "Does Y receive Z?":**

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

### 12.2 Key Classes for Flow Investigation

**Response Construction & Adaptation:**
- `MsalBrokerResultAdapter` (Common) - Converts authentication results to BrokerResult for IPC
- `AdalBrokerResultAdapter` (Common) - ADAL version of result adapter
- `BrokerResult` (Common) - The IPC response object sent to MSAL/OneAuth
- `BrokerResultAdapter` - Generic adapter interfaces

**Account Type Detection:**
- Check for `MSA_MEGA_TENANT_ID` constant (`"9188040d-6c67-4c5b-b112-36a304b66dad"`)
- Check for `CONSUMERS` constant in authorities
- Look for `accountType` or `realm` field checks


### 12.3 Common Pitfalls to Avoid

❌ **DON'T** stop after finding a field definition - this only confirms structure, not behavior
❌ **DON'T** assume data flows unchanged - always check for filtering/transformation logic
❌ **DON'T** ignore protocol version checks - behavior often changes based on negotiated version
❌ **DON'T** forget to check flight flags - features are often gated behind flights

✅ **DO** search for Builder usage and construction patterns
✅ **DO** search for the field name in assignment context (e.g., `.setField(`, `.field(`)
✅ **DO** look for `Adapter` or `Converter` classes in the flow
✅ **DO** check for conditional logic based on account type, protocol version, or flights

### 12.4 Search Patterns for Flow Investigation

**Finding construction code:**
```
Search: "new BrokerResult.Builder" or ".refreshToken("
Look in: *Adapter.java, *Converter.java, *Builder.java
```

**Finding conditional logic:**
```
Search: "if.*accountType" or "if.*MSA" or "shouldRemove" or "shouldInclude"
Look in: Result adapters, response builders
```

**Finding flight checks:**
```
Search: "isFlightEnabled" or "CommonFlight." or "FlightsManager"
Look in: Adapter classes, controller classes
```

### 12.5 Example Investigation Flow

**Question:** "Is refresh token returned to OneAuth?"

**Step-by-step:**
1. ✅ Find `BrokerResult` class → Confirm `mRefreshToken` field exists
2. ✅ Search for `BrokerResult.Builder` usage → Find `MsalBrokerResultAdapter`
3. ✅ Read `buildBrokerResultFromAuthenticationResult()` method → Find conditional logic
4. ✅ Check `shouldRemoveRefreshTokenFromResult()` → Discover:
   - Flight check: `STOP_RETURNING_AAD_RT_BACK_TO_CALLING_APP`
   - Protocol version check: `>= 16.0`
   - Account type check: Remove for AAD, keep for MSA
5. ✅ **Complete Answer:** RT is conditionally returned based on account type, flight, and protocol version

---

## 13. Telemetry & Analytics with Azure Data Explorer (Kusto)

### 13.1 Cluster Information for Android broker/common/MSAL libraries.
* **Primary Cluster:** `https://idsharedeus2.kusto.windows.net/`
* **Production Database:** `ad-accounts-android-otel`
* **Sandbox Database:** `android-broker-otel-sandbox`
* **Available Tables:**
  - `android_spans` - Android authentication telemetry spans (primary table, most commonly used)
    - **Data Retention:** 30 days
    - Contains real-time data, updated continuously
  - `android_metrics` - Aggregated metrics data
  - Use `mcp_my-mcp-server_list_tables` to discover all available tables
* **Materialized Views:** 46 pre-aggregated views for faster queries on common patterns
  - **Data Retention:** 90 days
  - Updated hourly, providing pre-aggregated historical data
  - Use `.show materialized-views` query to discover all available views
  - Common categories: Error Analysis, Silent/Interactive Auth, PRT Operations, Broker & Apps, Devices, Performance, Teams/Mobile Devices

### 13.2 User Intent Translation
When users say:
- **"Interactive request"** → They mean `AcquireTokenInteractive` span
- **"Silent request"** → They mean `AcquireTokenSilent` span

### 13.3 Discovering Span Names and Error Codes
**To find top span names by volume:**
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| summarize count() by span_name
| order by count_ desc
| take 30
```

**To find common error codes:**
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where isnotempty(error_code)
| summarize count() by error_code
| order by count_ desc
| take 20
```

**Key span names to know:**
- `AcquireTokenSilent` - Silent token acquisition (most common, 13B+ spans/week)
- `AcquireTokenInteractive` - Interactive authentication flow (9M+ spans/week)
- `ProcessWebCpRedirects` - Web Company Portal redirect processing
- Use the query above to discover others dynamically
When working with `android_spans` table, these are the most commonly used fields:

**Span Identification:**
- `span_id` - Unique identifier for the span
- `parent_span_id` - Parent span ID for hierarchical relationships
- `trace_id` - Trace ID linking related spans
- `correlation_id` - Correlation ID for request tracking
- `span_name` - Name of the operation (e.g., "AcquireTokenInteractive", "AcquireTokenSilent")

**Error Information:**
- `error_code` - Error code (e.g., "auth_cancelled_by_sdk", "device_needs_to_be_managed")
- `error_message` - Detailed error message
- `span_status` - Status of the span (e.g., "OK", "ERROR")

**Broker Information:**
- `active_broker_package_name` - Currently active broker package
- `current_broker_package_name` - Current broker package
- `calling_package_name` - Package that initiated the call
- Common broker packages:
  - `com.microsoft.windowsintune.companyportal` - Company Portal
  - `com.azure.authenticator` - Azure Authenticator
  - `com.microsoft.appmanager` - Microsoft App Manager

**Device Information:**
- `DeviceInfo_Id` - Unique device identifier
- `DeviceInfo_Model` - Device model (e.g., "SM-G998U", "Pixel 7 Pro")

**Timestamps:**
- `EventInfo_Time` - Event timestamp (use `ago(Xd)` for time ranges)

**Flow Information:**
- `is_interrupt_flow` - Boolean indicating if flow was interrupted

### 13.4 Common Query Patterns

**Time Filtering:**
```kql
| where EventInfo_Time >= ago(7d)  // Last 7 days
| where EventInfo_Time between (ago(3d) .. now())  // Last 3 days
```

**Parent-Child Span Relationships:**
```kql
let parentSpans = android_spans
| where span_name == "AcquireTokenInteractive"
| project parent_span_id = span_id, trace_id;

let childSpans = android_spans
| where span_name == "ProcessWebCpRedirects"
| project child_span_id = span_id, parent_span_id, trace_id;

parentSpans
| join kind=inner (childSpans) on trace_id
```

**Detecting Company Portal Installation:**
```kql
// CP is considered installed if it appears in active_broker_package_name or calling_package_name
| extend has_cp = iff(
    active_broker_package_name contains "companyportal" or 
    calling_package_name contains "companyportal", 
    1, 0)
```

**Device-Level Aggregation:**
```kql
| summarize 
    total_devices = dcount(DeviceInfo_Id),
    error_count = count()
    by error_code
```

### 13.5 Query Optimization Tips

1. **Always use time filters** - Start queries with `| where EventInfo_Time >= ago(Xd)` to limit data scanning
2. **Use `take` or `limit`** - For exploratory queries, add `| take 1000` to prevent timeouts
3. **Project early** - Use `| project` to select only needed columns early in the query
4. **Avoid expensive joins** - Use `kind=inner` joins when possible, and ensure both sides are filtered
5. **Use `summarize`** - Aggregate data at device or error level rather than returning raw spans
6. **Check distinct counts** - Use `dcount()` for unique device counts to avoid duplicates

### 13.6 Common Investigation Scenarios

**Finding error patterns:**
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenInteractive"
| where isnotempty(error_code)
| summarize error_count = count() by error_code, error_message
| order by error_count desc
```

**Checking CP installation rate for specific errors:**
```kql
let errorDevices = android_spans
| where EventInfo_Time >= ago(7d)
| where error_code == "auth_cancelled_by_sdk"
| distinct DeviceInfo_Id;

errorDevices
| join kind=leftouter (
    android_spans
    | where EventInfo_Time >= ago(7d)
    | extend has_cp = iff(
        active_broker_package_name contains "companyportal" or 
        calling_package_name contains "companyportal", 1, 0)
    | summarize any_cp = max(has_cp) by DeviceInfo_Id
) on DeviceInfo_Id
| summarize 
    total = dcount(DeviceInfo_Id),
    with_cp = dcountif(DeviceInfo_Id, any_cp == 1)
| extend cp_percentage = round(100.0 * with_cp / total, 2)
```

### 13.7 Investigating Latency Increases (e.g., AcquireTokenSilent)

When you receive an alert that ATS (AcquireTokenSilent) latency has increased, follow this systematic approach:

**Step 1: Identify the Increase**
- Compare recent latency percentiles (p50, p90, p95, p99) against baseline
- Determine the magnitude of increase and which percentile is most affected
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenSilent"
| summarize 
    p50 = percentile(elapsed_time, 50),
    p90 = percentile(elapsed_time, 90),
    p95 = percentile(elapsed_time, 95),
    p99 = percentile(elapsed_time, 99)
    by bin(EventInfo_Time, 1h)
| order by EventInfo_Time desc
```

**Step 2: Find Culprit Dimensions**
- Break down latency by key dimensions to isolate the problem
- Check: broker_version, active_broker_package_name, calling_package_name, DeviceInfo_Model, account_type
```kql
android_spans
| where EventInfo_Time >= ago(3d)
| where span_name == "AcquireTokenSilent"
| summarize 
    count = count(),
    p90_latency = percentile(elapsed_time, 90)
    by active_broker_package_name, current_broker_package_name
| order by p90_latency desc
```

**Step 3: Check Error Rate Correlation**
- Determine if latency increase coincides with higher error rates
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenSilent"
| summarize 
    total = count(),
    errors = countif(isnotempty(error_code)),
    avg_latency = avg(elapsed_time)
    by bin(EventInfo_Time, 1h)
| extend error_rate = round(100.0 * errors / total, 2)
| order by EventInfo_Time desc
```

**Step 4: Analyze Elapsed Time Breakdown**
- Investigate which operation is causing the slowdown using elapsed_time fields
- Key metrics available in code: cache operations (14 metrics), network operations (3 metrics), keypair generation
- Additional runtime metrics in schema: PRT operations, lock acquisition times, token creation, crypto operations
```kql
android_spans
| where EventInfo_Time >= ago(3d)
| where span_name == "AcquireTokenSilent"
| where isnotempty(elapsed_time_cache_load) or isnotempty(elapsed_time_network_acquire_at)
| summarize 
    avg_cache = avg(elapsed_time_cache_load),
    avg_network = avg(elapsed_time_network_acquire_at),
    avg_total = avg(elapsed_time)
    by bin(EventInfo_Time, 1h)
```

**Step 5: Timeline Analysis**
- Use timestamp fields to understand flow progression
- Available: timestamp_ats_starts, timestamp_ats_executed_by_dispatcher, timestamp_ats_finished
- Identify where delays occur in the authentication pipeline

### 13.8 Important Notes

- **Sensitive Data:** Some fields like `correlation_id` may be scrubbed to "Scrubbed" for privacy
- **Field Availability:** Not all fields are populated in all spans; use `isnotempty()` checks
- **Performance:** Queries spanning more than 7 days may timeout; break into smaller time windows
- **MCP Server:** Use the `mcp_my-mcp-server_execute_query` tool to run Kusto queries from Copilot
- **Schema Discovery:** Use `mcp_my-mcp-server_get_table_schema` to explore available fields in tables

---

## 14. eSTS (Token Service) Investigations with Azure Data Explorer (Kusto)

### 14.1 Cluster Information
* **eSTS Cluster:** `https://estswus2.kusto.windows.net/`
* **Database:** `ESTS`
* **Primary Table:** `AllPerRequestTable` - Union view spanning multiple Kusto clusters (estsfrc, estssec, estsdb3, etc.)
  - Contains all token service request/response data across global eSTS deployments
  - Queries fan out to physical `PerRequestTableIfx` tables in each cluster
  - Use `AllPerRequestTable` for comprehensive cross-cluster queries
* **Purpose:** eSTS is Microsoft's token service. Android team is a client of eSTS, so we investigate Android-related authentication requests and responses

### 14.2 Android-Specific Filtering
**ALWAYS filter by Android platform when investigating Android issues:**
```kql
PerRequestTableIfx
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"  // Primary Android filter
```

**Alternative Android platform fields:**
- `DevicePlatform` - Raw device platform string
- `DevicePlatformForUI` - User-friendly platform name (preferred for filtering)

### 14.3 Key Fields for Android Investigations

**Request Identification:**
- `CorrelationId` - Correlates request across Android client → Broker → eSTS
- `RequestId` - Unique identifier for the eSTS request
- `env_time` - Request timestamp

**Request Type & Flow:**
- `Call` - Type of authentication call (e.g., "token", "authorize", "common/oauth2/v2.0/token")
- `IsInteractive` - Boolean indicating if user interaction was required
- `Prompt` - Prompt type (e.g., "none", "login", "consent")

**Authentication Status:**
- `Result` - Overall result status (e.g., "Success", "Failure")
- `ErrorCode` - Error code if request failed
- `ErrorNo` - Numeric error code
- `HttpStatusCode` - HTTP status code of the response
- `SubErrorCode` - Additional error details

**PRT (Primary Refresh Token) Information:**
- `PrtData` - Contains PRT-related data (check if PRT was sent in request)
  - Parse this JSON field to determine if PRT was used
  - Example: `| extend HasPRT = isnotempty(PrtData)`

**Device & Client Information:**
- `DeviceId` - Unique device identifier
- `ApplicationId` - Client application ID
- `UserAgent` - Browser/client user agent string

**Timing Information:**
- `ResponseTime` - Total response time in milliseconds
- `StsProcessingTime` - eSTS processing time

**User & Account Information:**
- `TenantId` - Tenant identifier
- `UserTenantId` - User's home tenant identifier
- `UserPrincipalObjectID` - User's object ID in Entra ID (unique identifier for the user)
- `AccountType` - Type of account (AAD, MSA, etc.)
- `UserType` - Type of user

### 14.4 Common Query Patterns

**Find requests by CorrelationId:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where CorrelationId == "your-correlation-id-here"
| project env_time, CorrelationId, Call, Result, ErrorCode, IsInteractive, PrtData, ResponseTime
```

**Check if PRT was used in requests:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| extend HasPRT = isnotempty(PrtData)
| summarize 
    total_requests = count(),
    prt_requests = countif(HasPRT),
    success_rate = round(100.0 * countif(Result == "Success") / count(), 2)
    by HasPRT
```

**Analyze error patterns:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where Result != "Success"
| summarize error_count = count() by ErrorCode, SubErrorCode, Call
| order by error_count desc
| take 20
```

**Interactive vs Silent requests:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| summarize 
    count = count(),
    success_rate = round(100.0 * countif(Result == "Success") / count(), 2),
    avg_response_time = avg(ResponseTime)
    by IsInteractive
```

**Requests by application:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| summarize 
    request_count = count(),
    error_count = countif(Result != "Success"),
    avg_response_time = avg(ResponseTime)
    by ApplicationId
| extend error_rate = round(100.0 * error_count / request_count, 2)
| order by request_count desc
```

**Analyze users and devices:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where ApplicationId == "your-app-id-here"
| summarize 
    request_count = count(),
    unique_devices = dcount(DeviceId),
    first_seen = min(env_time),
    last_seen = max(env_time)
    by UserPrincipalObjectID, PUID, TenantId
| order by request_count desc
```

### 14.5 Correlating Android Spans with eSTS Requests

**To trace a complete flow (Android client → Broker → eSTS):**

1. **Start with Android span:**
```kql
// In android-broker-otel-sandbox or ad-accounts-android-otel database
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenInteractive"
| where error_code == "some_error"
| project correlation_id, span_id, EventInfo_Time, error_code
| take 100
```

2. **Find corresponding eSTS requests:**
```kql
// In ESTS database
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where CorrelationId in ("correlation-id-1", "correlation-id-2", ...)  // From step 1
| project env_time, CorrelationId, Call, Result, ErrorCode, PrtData, ResponseTime
```

### 14.6 Query Optimization Tips

1. **Always filter by time first** - Use `| where env_time >= ago(Xd)` at the start
2. **Filter by Android platform early** - Add `| where DevicePlatformForUI == "Android"` immediately after time filter
3. **Use `take` for exploration** - Limit results with `| take 1000` to avoid timeouts
4. **Project early** - Select only needed columns with `| project` to reduce data transfer
5. **Check field population** - Use `isnotempty()` before parsing fields like `PrtData`

### 14.7 Important Notes

- **Data Retention:** Check with eSTS team for current retention policy
- **Sensitive Data:** Many fields are hashed or scrubbed for privacy (e.g., PUID, UserNameHash)
- **Cross-Cluster Queries:** Cannot directly join Android telemetry cluster with eSTS cluster; must correlate via CorrelationId manually
- **PrtData Parsing:** `PrtData` is a JSON string; use `parse_json()` or `extend` to extract specific fields
- **MCP Server:** Use `mcp_my-mcp-server_execute_query` with cluster URL `https://estswus2.kusto.windows.net` and database `ESTS`

---