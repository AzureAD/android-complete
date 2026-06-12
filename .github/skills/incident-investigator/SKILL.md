---
name: incident-investigator
description: Systematically investigate IcM incidents and customer-reported authentication issues for Android Broker/MSAL. Use this skill when asked to investigate an incident, troubleshoot auth failures, analyze customer logs, diagnose PRT/SSO issues, or review IcM tickets. Triggers include "investigate incident", "troubleshoot IcM", "analyze these logs", "what's wrong with this auth flow", "diagnose this issue", or any request involving incident investigation with evidence-based diagnosis.
---

# Incident Investigator

Investigate Android authentication incidents systematically with evidence-first diagnosis.

> **Security vulnerabilities?** For `[MSRC]`/`[ITD]`-tagged IcMs (vulnerability reports filed by the
> security team, FireWatch/Glasswing findings), use the **`vuln-triage-reporter`** skill instead. That
> workflow is built for code-evidence-based severity classification (agree/rebut the filed tier), not
> log/auth-failure diagnosis.

## Investigation Workflow

Execute these steps IN ORDER. Do not skip steps.

### Step 1: Gather IcM Context

Query DRI Copilot MCP FIRST. The tool name varies by local config, so use `tool_search_tool_regex` with pattern `Android_DRI_Copilot` to find the correct tool, then invoke it.

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

## Zero-Row Guard for Kusto Queries

When a `android_spans` query for an incident's correlation IDs returns zero rows, do **not** conclude "the broker was never invoked" from that alone. Zero rows is ambiguous — it could mean the query is wrong (e.g., querying `correlation_id` instead of `correlation_id_v2`), or coverage is genuinely absent. Resolve the ambiguity with **two additional queries** before relying on the result:

1. **Reference healthy trace** — pull one known-good recent example of the same operation to confirm what the row *should* look like:
   ```kql
   android_spans
   | where EventInfo_Time >= ago(30m)
   | where calling_package_name == '<same-package>'
   | where span_name == '<same-span>'
   | where span_status == 'OK'
   | where isnotempty(correlation_id_v2)
   | take 1
   ```
   Then pull every span for that CID and inspect the shape. If your incident CID query doesn't return that shape, you know what's missing.
2. **Same-tenant, same-window cross-check** — confirm the affected tenant was actively brokering at the incident time (rules out a global outage / data-pipeline gap):
   ```kql
   android_spans
   | where EventInfo_Time between (<start> .. <end>)
   | where tenant_id == '<affected-tenant>'
   | where calling_package_name contains '<affected-app>'
   | summarize matches = count(), span_names = make_set(span_name, 10)
   ```
   Non-zero matches with the incident CIDs still absent is the strongest single piece of evidence for "the broker was simply not given these requests."

Only after both checks succeed (reference trace shape is known + tenant was active in window) does a zero-row CID lookup constitute strong evidence.

## Output Format

For investigations that warrant a standalone document (typical for IcMs flipped to MSAL Android / Broker), use the structure below. Sections 1–2 give a non-technical reader the elevator pitch; sections 3+ are for SMEs.

```markdown
# IcM [Number] — [Short Symptom Title]

**Title:** [full IcM title]
**Severity / State:** ... | **Tenant:** ...
**Reported correlation IDs:** ...
**Broker logs reference:** [bundle ID, or "not yet retrieved"]

---

## How This Flow Is Supposed to Work

Plain-English happy path (3–5 numbered steps, no file links, no jargon). Closes with the design principle being violated (e.g., "one interactive auth + one PRT → many silent resource tokens").

## What's Broken

What the user sees + the binary or N-ary set of *possibilities* that could explain it. Each possibility maps to a different owning team. Note which possibility current evidence points at, and what evidence is missing to fully distinguish them.

---

## Internal Investigation

### Ask
One-paragraph restatement of what the IcM is asking *our* team to confirm or rule out.

### Actions Taken So Far
Bullet list of completed work. For telemetry passes, include the reference-trace and same-window cross-check (see Zero-Row Guard above). For code review, link to file + line ranges.

### Code-Path Investigation
Numbered steps tracing the relevant code path end-to-end. For each step: Module | File + line range (linked) | 1–3 sentence overview | ⚠️ Conditional callouts for flights, protocol versions, account types, tenant matching. Keep prose tight.

#### Defect Surface
Ranked list of 3–6 spots where a *scenario-specific* defect could live. Filter ruthlessly — see the codebase-researcher skill's guidance on scenario-scoped defect surfaces.

### Log Investigation
Use a stub ("Pending — log bundle X not yet retrieved") if logs aren't available yet, rather than skipping the section.

### Open Questions / Next Steps
Checkbox items mapped back to specific defect-surface entries ("If logs show X, focus on Step Y").
```

## Broker Telemetry Signatures

When confirming whether the broker was invoked for a given correlation ID, look for these `android_spans` signatures (top-level spans only, `parent_span_name` empty):

| Pattern | Signature | Meaning |
|---|---|---|
| **Healthy interactive + silent** | `AcquireTokenInteractive` (OK, multi-second) followed by `AcquireTokenSilent` (OK, sub-second), both with `controller_name = BrokerSsoController` | PIA + PRT bootstrap, then silent PRT-redeem for new scope succeeded. Expected shape. |
| **Silent failed → UI required** | `AcquireTokenSilent` → `span_status = ERROR`, `error_code = interaction_required`, `controller_name = BrokerSsoController` | Broker was invoked, attempted PRT-redeem, but eSTS returned `invalid_grant` / `interaction_required`. The caller would then go interactive. |
| **Broker never invoked** | No `AcquireTokenSilent` or `AcquireTokenInteractive` span for the CID (with `correlation_id_v2` check, per Zero-Row Guard) | Request never crossed IPC into the broker. Caller likely used non-broker auth path, or didn't call us at all. |

The **absence** of both healthy and failed-silent patterns — combined with the Zero-Row Guard checks — is what allows you to confidently state "the broker was not given this request" in an IcM update.

### ⚠️ Critical: `acquireTokenInteractive` can silently fall back to a silent flow

This is a non-obvious broker behavior that has misled investigations:

**Even when a caller invokes `acquireTokenInteractive`, the broker may attempt a silent flow first** when an
account is already in storage, launching interactive UI only if the silent path fails or a prompt is forced.
The forced-prompt exceptions are limited to a caller-supplied `Prompt.LOGIN` / `force_prompt` or a nonce in
the request's extra query parameters. (The exact gating lives in the broker `AccountChooser` logic — consult
the broker source locally for the precise conditions and any controlling flight; do not rely on memory.)

**Investigation implications:**

- Do **not** treat "the caller called interactive" as a self-sufficient explanation for an unexpected user prompt. Ask whether `Prompt.LOGIN` / `force_prompt` or a nonce was set.
- Telemetry shape for "ATI that silently succeeded" is a single `AcquireTokenInteractive` span with sub-second `elapsed_time` and no top-level child `AcquireTokenSilent` span (the silent attempt is emitted as an inner span, not the top-level one).
- Telemetry shape for "ATI that went interactive" is `AcquireTokenInteractive` with multi-second `elapsed_time` and an interactive child span.
- When asking the calling app team for confirmation, the right question is **"did you set `Prompt.LOGIN` / `force_prompt` / a nonce?"** — not "did you call silent or interactive."

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

### Initial Query (always start here)

When given just an incident ID, query DRI Copilot with:

```
"Investigate IcM [number]. What are the affected apps, symptoms, and known issues?"
```

This single query extracts:
- Affected application(s)
- Customer-reported symptoms
- Account/device context
- Any known root cause or past similar incidents

### Follow-up Queries (after initial context)

Once you have context from the initial query, use targeted follow-ups:

```
"TSG for error code [error_code]"           # After finding error in logs
"Past incidents related to [symptom]"        # After identifying symptom from IcM
"How to troubleshoot [specific_issue]"       # For deep-dive guidance
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

1. **Query DRI Copilot FIRST** - Get IcM context before analyzing logs
2. **Evidence over assumptions** - Only state what logs show
3. **State what's missing** - Be explicit about evidence gaps
4. **Search all log files** - Issue may span multiple log segments
5. **Check for sign-out operations** - Critical for SDM issues
