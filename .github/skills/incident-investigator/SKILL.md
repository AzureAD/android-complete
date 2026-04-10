---
name: incident-investigator
description: Systematically investigate IcM incidents and customer-reported authentication issues for Android Broker/MSAL. Use this skill when asked to investigate an incident, troubleshoot auth failures, analyze customer logs, diagnose PRT/SSO issues, or review IcM tickets. Triggers include "investigate incident", "troubleshoot IcM", "analyze these logs", "what's wrong with this auth flow", "diagnose this issue", or any request involving incident investigation with evidence-based diagnosis.
---

# Incident Investigator

Investigate Android authentication incidents systematically with evidence-first diagnosis.

## Investigation Workflow

Execute these steps IN ORDER. Do not skip steps.

### Step 1: Gather IcM Context

Use the `android-dri-search-hosted` MCP server tools to gather all incident context. Execute these sub-steps in order:

#### Step 1a: Get Incident Details

Use `tool_search_tool_regex` with pattern `android-dri-s` to load the tools, then call `mcp_android-dri-s_get_incident` with the incident ID extracted from the IcM URL or user message.
<!--Use agency's icm mcp tools to gather incident details with the incident ID extracted from the IcM URL or user message.-->

```
mcp_android-dri-s_get_incident(incident_id="<incident_id>")
```

Extract from the response:
- **Affected app(s)**: Outlook, Teams, other 1P apps?
- **Account(s)**: Specific user or tenant-wide?
- **Device context**: SDM enabled? Device model? Android version?
- **Symptoms**: What exactly fails? Error messages?
- **Repro conditions**: When does it happen vs. not happen?
- **Error codes**: Any error codes or correlation IDs mentioned

#### Step 1b & 1c: Search Similar Incidents + TSGs (parallel, maximize batch)

After Step 1a completes, fire **all** of the following searches **in a single parallel batch** — they are independent and all use output from Step 1a. The goal is to complete all searches in ONE round trip.

**Plan your queries upfront.** Before calling any search tool, identify 2-3 distinct query angles from Step 1a:
1. **Symptom-focused** query (e.g., "shared devices long login slow authentication")
2. **Error/technical-focused** query (e.g., error codes, operation names, specific failure patterns)
3. **Feature/component-focused** query (e.g., "broker performance latency response time")

Then execute all searches using `batch_search` — combine ICM and TSG queries in a single call:

**Use `mcp_android-dri-s_batch_search`** to run all ICM and TSG searches in one round trip. Each entry in the `searches` array specifies `type` (`"icm"` or `"tsg"`) and `query`.

```
mcp_android-dri-s_batch_search(searches=[
  {"type": "icm", "query": "<symptom query>"},
  {"type": "icm", "query": "<error/technical query>"},
  {"type": "tsg", "query": "<error/technical query>"},
  {"type": "tsg", "query": "<feature/component query>"}
])
```

Review similar incidents for:
- Known root causes that match the current symptoms
- Previously successful mitigations
- Patterns across similar incidents

**Relevance filtering:** Search results are ranked by embedding similarity, NOT by actual relevance to the incident. Many results will be noise. Before including an incident in your report, verify that its root cause or symptoms have a **concrete, logical connection** to the current incident's failure mode. Do NOT pad the report with loosely related incidents — only include incidents where the root cause or symptom could plausibly explain the current issue.

**Search Related TSGs:** Already included in the `batch_search` call above. Use `search_tsgs` only if you need a standalone single-query TSG search outside of a batch.

**Query design tips:**
- Use specific technical terms (error codes, operation names, component names) over vague symptom descriptions
- If Step 1a has error codes, search for those directly — they yield the most precise TSG matches
- Avoid mixing too many unrelated terms in one query (e.g., "slow login SDM sign-in time" dilutes results); split into focused queries instead
- When the issue is about performance/latency, always include a query targeting dashboard/telemetry TSGs (e.g., "performance latency response time broker dashboard")

Collect from TSGs:
- Recommended troubleshooting steps
- Known solutions for the identified error pattern
- Relevant dashboards and Kusto queries
- Escalation guidance if applicable

### Step 1d: Categorize Findings and Confirm with User (BLOCKING)

**CRITICAL: Do NOT skip this step. Do NOT jump to a single root cause.**

After gathering search results, group similar incidents by **distinct root cause category** — not by
individual ticket. Typical categories might include:
- Keystore/KeyMaster hardware issue (specific device models)
- OEM battery optimization killing broker background process (Samsung, etc.)
- MDM/compliance policy triggering re-registration
- CA policy changes or device cap limits
- Broker/MSAL code regression
- Server-side (eSTS) issue

**Present ALL plausible categories** to the user as a summary table:

```markdown
## Possible Cause Categories

| # | Category | Matching Incidents | Key Signal |
|---|----------|--------------------|------------|
| 1 | [Category name] | IcM 123, 456 | [What distinguishes this cause] |
| 2 | [Category name] | IcM 789 | [What distinguishes this cause] |
```

**Only ask clarifying questions if the available context is insufficient to narrow down.**
If the user (or the IcM ticket) already provided device model, error codes, logs, or
enrollment type, use that information directly — do not re-ask what you already know.
When context IS missing, ask using `askQuestion`:
- Device make/model
- Enrollment type (COPE, MAM-WE, fully managed, etc.)
- Whether specific log signatures are present or absent
- Any other known details (frequency, recent changes, error codes)

**ONLY proceed to a diagnosis after you can confidently narrow to one category** — either
from the user's provided context or from their answers to your questions.

**Why this matters:** Search results are ranked by embedding similarity and can cluster
around a single well-documented cause (e.g., keystore issues have many incidents). This
does NOT mean it is the most likely cause for the current case. Always present the full
spread of possibilities.

### Step 2: Extract Log Evidence

Search logs for these key patterns:

| Pattern | What It Tells You |
|---------|-------------------|
| `correlation_id:` | Request tracking ID for eSTS correlation |
| `error_code` or `Error` | Specific failure reason |
| `No PRT present` | Missing Primary Refresh Token |
| `SignOut` or `removeAccount` | Account removal events |
| `disabled by MDM` | MDM policy interference |
| `invoked for package name:` | Which app made the request |
| `executed successfully` vs `failed` | Operation outcome |

Build a timeline of events with correlation IDs.

### Step 3: Analyze Account/Token State

Check these indicators in logs:

| Log Message | Indicates |
|-------------|-----------|
| `Found [N] Accounts...` | How many accounts in cache |
| `No PRT present for the account` | PRT missing or wiped |
| `Home Account id doesn't have uid or tenant id` | Incomplete account state |
| `Found more than one account entry` | Duplicate account issue |
| `PRT is already registered-device PRT` | Valid WPJ PRT exists |
| `Loading Workplace Join entry for tenant:` | Device is WPJ'd |

### Step 4: Identify Operation Flow

Map the operations that occurred:

| Operation | Purpose |
|-----------|---------|
| `GetDeviceModeMsalBrokerOperation` | Check if SDM enabled |
| `GetCurrentAccountMsalBrokerOperation` | Fetch signed-in account |
| `AcquireTokenSilentMsalBrokerOperation` | Silent token acquisition |
| `AcquireTokenInteractiveMsalBrokerOperation` | Interactive auth |
| `SignOutFromSharedDeviceMsalBrokerOperation` | SDM sign-out (⚠️ key for SDM issues) |
| `GetPreferredAuthMethodMsalBrokerOperation` | Auth method check |

### Step 5: Form Hypotheses

**Present multiple hypotheses — never commit to a single root cause without user confirmation.**

Rank by evidence strength:

| Confidence | Criteria |
|------------|----------|
| **HIGH** | Direct log evidence shows the issue AND user-confirmed details match |
| **MEDIUM** | Logs suggest but don't confirm, or user hasn't confirmed device/context details |
| **LOW** | Inference based on patterns, no direct evidence |

**Rules:**
- Always list at least 2 hypotheses unless log evidence conclusively rules out all but one
- If the user has not yet confirmed device model, enrollment type, or log signatures,
  do NOT assign HIGH confidence to any hypothesis
- Clearly state what evidence would promote or eliminate each hypothesis

Common root causes to consider:
- MDM triggering sign-out (Imprivata, other MDMs)
- PRT deleted/expired/revoked
- Device cap reached
- Account-specific CA policy
- SDM misconfiguration
- Broker/app version incompatibility

### Step 6: Identify Missing Evidence

State explicitly what's NOT in the logs that would help:
- Missing correlation IDs?
- No sign-out operation captured?
- No eSTS error codes?
- Logs from wrong time window?

## Output Format

```markdown
## Investigation: IcM [Number]

### IcM Summary
| Field | Value |
|-------|-------|
| Affected App(s) | |
| Account | |
| Device | Android [version], Broker [version] |
| SDM Enabled | Yes/No |
| Symptoms | |

### Key Correlation IDs
| Correlation ID | Operation | Result |
|----------------|-----------|--------|
| `abc-123...` | AcquireTokenSilent | ✅/❌ |

### Evidence from Logs

#### Finding 1: [Description]
- **Timestamp**: 
- **Evidence**: [Exact log line]
- **Implication**: 

### Hypotheses (Ranked by Evidence)

| # | Hypothesis | Confidence | Supporting Evidence |
|---|------------|------------|---------------------|
| 1 | | HIGH/MED/LOW | |

### Missing Evidence
- [ ] [What additional data is needed]

### Recommended Actions
1. [Next step]
2. [Next step]
```

## Common Patterns

### Pattern: MDM-Triggered Sign-Out (SDM)
**Symptoms**: User signs in, immediately signed out
**Evidence to look for**:
- `SignOutFromSharedDeviceMsalBrokerOperation` from MDM package
- `disabled by MDM` messages
- `No PRT present` after successful auth

### Pattern: Missing PRT
**Symptoms**: Silent auth fails, interactive works
**Evidence to look for**:
- `No PRT present for the account`
- Check if `AcquireTokenSilent` fails but `AcquireTokenInteractive` succeeds
- Look for prior sign-out or PRT revocation

### Pattern: Device Cap
**Symptoms**: New device can't register
**Evidence to look for**:
- Error during device registration
- eSTS error about device limit
- Check eSTS logs with correlation ID

### Pattern: Duplicate Accounts
**Symptoms**: Inconsistent auth behavior
**Evidence to look for**:
- `Found more than one account entry for user`
- Multiple accounts with same UPN but different home account IDs

## DRI Search Queries Reference

### Tool Reference: `android-dri-search-hosted` MCP Server

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `mcp_android-dri-s_get_incident` | Fetch incident details by ID | `incident_id` (required) |
| `mcp_android-dri-s_batch_search` | Search past incidents AND/OR TSGs in a single call | `searches` (required): array of `{"type": "icm"\|"tsg", "query": "..."}` |
| `mcp_android-dri-s_search_tsgs` | Search troubleshooting guides (single query) | `query` (required) |

### Query Strategy

1. **Start with `get_incident`** — always get the full incident context first
2. **Then use `batch_search`** — combine ICM and TSG searches in a single call for maximum parallelism
3. **Match query specificity to evidence confidence:**
   - **High-confidence signals** (specific error codes, stack traces, operation names): query
     those directly — they yield the best results. Do NOT dilute with broad exploratory queries.
   - **Low-confidence / vague symptoms** (e.g., "device re-registration", "intermittent failures"
     with no error codes): use multiple query angles to avoid clustering around one cause.
     Vague symptoms have many possible root causes, so broaden the search.
4. **Iterate if needed** — if initial search is too broad, narrow with specific error codes found in logs

## eSTS Correlation

Use the Kusto MCP tool to correlate with eSTS when needed:

```
mcp_my-mcp-server_execute_query
```

**Parameters:**
- **cluster**: `https://estswus2.kusto.windows.net`
- **database**: `ESTS`
- **query**: (see below)

**Basic correlation query:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where CorrelationId == "[correlation-id]"
| project env_time, CorrelationId, Call, Result, ErrorCode, PrtData
```

For more Kusto queries, see [references/kusto-queries.md](references/kusto-queries.md).

## Key Reminders

1. **Query `android-dri-search-hosted` FIRST** - Get incident details, similar incidents, and TSGs before analyzing logs
2. **Evidence over assumptions** - Only state what logs show
3. **State what's missing** - Be explicit about evidence gaps
4. **Search all log files** - Issue may span multiple log segments
5. **Check for sign-out operations** - Critical for SDM issues
