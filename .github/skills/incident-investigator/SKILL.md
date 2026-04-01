---
name: incident-investigator
description: Systematically investigate IcM incidents and customer-reported authentication issues for Android Broker/MSAL. Use this skill when asked to investigate an incident, troubleshoot auth failures, analyze customer logs, diagnose PRT/SSO issues, or review IcM tickets. Triggers include "investigate incident", "troubleshoot IcM", "analyze these logs", "what's wrong with this auth flow", "diagnose this issue", or any request involving incident investigation with evidence-based diagnosis.
---

# Incident Investigator

Investigate Android authentication incidents systematically with evidence-first diagnosis.

## Investigation Workflow

Execute these steps IN ORDER. Do not skip steps.

### Step 1: Gather IcM Context & Similar Incidents

Query IcMs and TSGs **in parallel** using the `android-dri-s` MCP tools (faster than DRI Copilot Project Explorer):

```
# Call BOTH in parallel (~18s total vs ~70s with DRI Copilot Project Explorer)
mcp_android-dri-s_search_icms   → search for the incident topic / symptoms
mcp_android-dri-s_search_tsgs   → search for relevant troubleshooting guides
```

> **Why not `mcp_dricopilot-mc_Android_DRI_Copilot_Project_Explorer`?**
> It returns AI-synthesized results but takes ~70s. The `android-dri-s` tools return raw data in ~18s (parallel), and we synthesize root causes ourselves with code context — producing equivalent or better analysis.

If given a specific IcM ID, also use `mcp_android-dri-s_get_incident` to fetch full incident details.

Extract from IcM:
- **Affected app(s)**: Outlook, Teams, other 1P apps?
- **Account(s)**: Specific user or tenant-wide?
- **Device context**: SDM enabled? Device model? Android version?
- **Symptoms**: What exactly fails? Error messages?
- **Repro conditions**: When does it happen vs. not happen?

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

Rank by evidence strength:

| Confidence | Criteria |
|------------|----------|
| **HIGH** | Direct log evidence shows the issue |
| **MEDIUM** | Logs suggest but don't confirm |
| **LOW** | Inference based on patterns, no direct evidence |

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

## DRI Copilot Queries

### Available MCP Tools (Performance-Ranked)

| Tool | Speed | Use When |
|------|-------|----------|
| `mcp_android-dri-s_search_icms` | ~18s | Search past incidents by keyword/symptom |
| `mcp_android-dri-s_search_tsgs` | ~18s | Search troubleshooting guides |
| `mcp_android-dri-s_get_incident` | ~10s | Fetch a specific incident by ID |
| `mcp_dricopilot-mc_Android_DRI_Copilot_Project_Explorer` | ~70s | Fallback: AI-synthesized IcM+TSG results in one call |

### Preferred Strategy: Parallel Calls

Always call `search_icms` and `search_tsgs` **in the same tool-call block** so they execute in parallel:

```
# Both fire simultaneously — total wall-clock ~18s
Call 1: mcp_android-dri-s_search_icms("[symptom or keyword]")
Call 2: mcp_android-dri-s_search_tsgs("[symptom or keyword]")
```

Then synthesize root causes, mitigations, and confidence levels yourself using the raw results + code context + log evidence.

### Follow-up Queries (after initial context)

Once you have context from the initial query, use targeted follow-ups:

```
mcp_android-dri-s_search_tsgs("error code [error_code]")     # After finding error in logs
mcp_android-dri-s_search_icms("[specific symptom]")           # After identifying symptom
mcp_android-dri-s_get_incident("[IcM ID]")                    # Deep-dive on a specific incident
```

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

1. **Query `android-dri-s` tools FIRST (in parallel)** - Get IcM + TSG context before analyzing logs
2. **Evidence over assumptions** - Only state what logs show
3. **State what's missing** - Be explicit about evidence gaps
4. **Search all log files** - Issue may span multiple log segments
5. **Check for sign-out operations** - Critical for SDM issues
6. **Always state confidence level** - HIGH/MEDIUM/LOW per hypothesis with supporting evidence
