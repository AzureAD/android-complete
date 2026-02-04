---
name: kusto-analyst
description: Analyze Android authentication telemetry using Azure Data Explorer (Kusto). Use this skill for querying android_spans, eSTS correlation, latency investigation, error analysis, and telemetry troubleshooting. Triggers include "query Kusto", "analyze telemetry", "check android_spans", "eSTS correlation", "latency investigation", "error patterns", or any request involving telemetry data analysis.
---

# Kusto Analyst

Analyze Android authentication telemetry using Azure Data Explorer (Kusto) for error analysis, latency investigation, and cross-cluster correlation.

## Available MCP Tools

**Always use these tools to execute Kusto queries:**
- `mcp_my-mcp-server_execute_query` - Execute Kusto queries
- `mcp_my-mcp-server_list_tables` - Discover available tables
- `mcp_my-mcp-server_get_table_schema` - Explore field schema

---

## Android Telemetry Cluster

### Cluster Information
| Property | Value |
|----------|-------|
| **Cluster URL** | `https://idsharedeus2.kusto.windows.net/` |
| **Production Database** | `ad-accounts-android-otel` |
| **Sandbox Database** | `android-broker-otel-sandbox` |

### Primary Tables
| Table | Purpose | Retention |
|-------|---------|-----------|
| `android_spans` | Authentication telemetry spans | 30 days |
| `android_metrics` | Aggregated metrics data | 30 days |

### Materialized Views
- **46 pre-aggregated views** for faster queries
- **Retention:** 90 days (longer than raw tables!)
- **Update frequency:** Hourly
- **Discover with:** `.show materialized-views` query
- **Categories:** Error Analysis, Silent/Interactive Auth, PRT Operations, Broker & Apps, Devices, Performance

### User Intent Translation
| User Says | Span Name |
|-----------|-----------|
| "Interactive request" | `AcquireTokenInteractive` |
| "Silent request" | `AcquireTokenSilent` |
| "PRT operation" | Various PRT-related spans |

---

## android_spans Key Fields

### Span Identification
| Field | Description |
|-------|-------------|
| `span_id` | Unique identifier for the span |
| `parent_span_id` | Parent span ID for hierarchical relationships |
| `trace_id` | Trace ID linking related spans |
| `correlation_id` | Correlation ID for request tracking (use for eSTS correlation) |
| `span_name` | Operation name (e.g., "AcquireTokenInteractive") |

### Error Information
| Field | Description |
|-------|-------------|
| `error_code` | Error code (e.g., "auth_cancelled_by_sdk") |
| `error_message` | Detailed error message |
| `span_status` | Status ("OK", "ERROR") |

### Broker Information
| Field | Description |
|-------|-------------|
| `active_broker_package_name` | Currently active broker package |
| `current_broker_package_name` | Current broker package |
| `calling_package_name` | Package that initiated the call |

**Common Broker Packages:**
- `com.microsoft.windowsintune.companyportal` - Company Portal
- `com.azure.authenticator` - Azure Authenticator
- `com.microsoft.appmanager` - Microsoft App Manager

### Device & Timing
| Field | Description |
|-------|-------------|
| `DeviceInfo_Id` | Unique device identifier |
| `DeviceInfo_Model` | Device model (e.g., "Pixel 7 Pro") |
| `EventInfo_Time` | Event timestamp (use `ago(Xd)` for filtering) |
| `elapsed_time` | Total operation duration |

---

## Common Query Patterns

### Discovery Queries

**Find top span names:**
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| summarize count() by span_name
| order by count_ desc
| take 30
```

**Find common error codes:**
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where isnotempty(error_code)
| summarize count() by error_code
| order by count_ desc
| take 20
```

### Error Analysis

**Error patterns for specific span:**
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenInteractive"
| where isnotempty(error_code)
| summarize error_count = count() by error_code, error_message
| order by error_count desc
```

**Device-level error aggregation:**
```kql
android_spans
| where EventInfo_Time >= ago(7d)
| summarize 
    total_devices = dcount(DeviceInfo_Id),
    error_count = count()
    by error_code
```

### Company Portal Detection

```kql
android_spans
| where EventInfo_Time >= ago(7d)
| extend has_cp = iff(
    active_broker_package_name contains "companyportal" or 
    calling_package_name contains "companyportal", 
    1, 0)
| summarize 
    total = count(),
    with_cp = countif(has_cp == 1)
| extend cp_percentage = round(100.0 * with_cp / total, 2)
```

### Parent-Child Span Relationships

```kql
let parentSpans = android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenInteractive"
| project parent_span_id = span_id, trace_id;

let childSpans = android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "ProcessWebCpRedirects"
| project child_span_id = span_id, parent_span_id, trace_id;

parentSpans
| join kind=inner (childSpans) on trace_id
```

---

## Latency Investigation Workflow

When investigating latency increases (e.g., AcquireTokenSilent), follow these steps:

### Step 1: Identify the Increase

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

### Step 2: Find Culprit Dimensions

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

### Step 3: Check Error Rate Correlation

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

### Step 4: Analyze Elapsed Time Breakdown

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

---

## MATS telemetry

### Cluster Information
| Property | Value |
|----------|-------|
| **Cluster URL** | `https://idsharedeus2.kusto.windows.net/` |
| **Database** | `MATS_Office` |
| **Database ID** | `faab4ead691e451eb230afc98a28e0f2` |


---

## eSTS (Token Service) Cluster

### Cluster Information
| Property | Value |
|----------|-------|
| **Cluster URL** | `https://estswus2.kusto.windows.net/` |
| **Database** | `ESTS` |
| **Primary Table** | `AllPerRequestTable` (cross-cluster union view) |

### Android-Specific Filtering

**⚠️ ALWAYS filter by Android platform:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
```

### Key eSTS Fields

| Category | Field | Description |
|----------|-------|-------------|
| **Request ID** | `CorrelationId` | Links to Android `correlation_id` |
| | `RequestId` | Unique eSTS request ID |
| | `env_time` | Request timestamp |
| **Request Type** | `Call` | Auth call type (e.g., "token") |
| | `IsInteractive` | User interaction required |
| | `Prompt` | Prompt type ("none", "login") |
| **Status** | `Result` | "Success" or "Failure" |
| | `ErrorCode` | Error code if failed |
| | `HttpStatusCode` | HTTP status |
| **PRT** | `PrtData` | PRT-related data (JSON) |
| **Device** | `DeviceId` | Device identifier |
| | `ApplicationId` | Client app ID |
| **User** | `TenantId` | Tenant ID |
| | `UserPrincipalObjectID` | User's Entra ID object ID |
| | `AccountType` | AAD, MSA, etc. |

---

## Cross-Cluster Correlation

To trace a complete flow (Android → Broker → eSTS):

### Step 1: Get correlation IDs from Android spans

```kql
// Run against: https://idsharedeus2.kusto.windows.net/ | ad-accounts-android-otel
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenInteractive"
| where error_code == "some_error"
| project correlation_id, span_id, EventInfo_Time, error_code
| take 100
```

### Step 2: Find corresponding eSTS requests

```kql
// Run against: https://estswus2.kusto.windows.net/ | ESTS
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where CorrelationId in ("correlation-id-1", "correlation-id-2")
| project env_time, CorrelationId, Call, Result, ErrorCode, PrtData, ResponseTime
```

### eSTS Query Examples

**Find requests by CorrelationId:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where CorrelationId == "your-correlation-id-here"
| project env_time, CorrelationId, Call, Result, ErrorCode, IsInteractive, PrtData, ResponseTime
```

**Check PRT usage:**
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

**Error patterns:**
```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where Result != "Success"
| summarize error_count = count() by ErrorCode, SubErrorCode, Call
| order by error_count desc
| take 20
```

---

## Query Optimization Tips

| Tip | Example |
|-----|---------|
| **Always filter by time first** | `| where EventInfo_Time >= ago(7d)` |
| **Use `take` for exploration** | `| take 1000` to prevent timeouts |
| **Project early** | `| project field1, field2` to reduce data |
| **Use `dcount()` for unique counts** | `dcount(DeviceInfo_Id)` |
| **Check field population** | `| where isnotempty(error_code)` |
| **Break long ranges** | Queries > 7 days may timeout |

## Important Notes

- **Sensitive Data:** `correlation_id` may be scrubbed to "Scrubbed" for privacy
- **Field Availability:** Not all fields populated in all spans; use `isnotempty()`
- **Cross-Cluster Joins:** Cannot directly join Android + eSTS clusters; correlate via CorrelationId
- **PrtData Parsing:** Use `parse_json()` or `extend` to extract PRT fields
