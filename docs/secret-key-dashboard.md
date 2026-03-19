# SecretKey Operations — ADX Dashboard Plan (Phased)

**Cluster:** `https://idsharedeus2.kusto.windows.net/`  
**Database:** `f9614845898c42639bce0e2b8794b519` (ad-accounts-android-otel)  
**Table:** `android_spans`  
**Span Names:** `SecretKeyGeneration`, `SecretKeyRetrieval`

---

## Strategy

- **Phase 1:** Stored functions query the raw `android_spans` table directly. Dashboards call these functions.
- **Phase 2:** Create materialized views. Update stored functions to read from MVs instead of raw table. **Dashboard queries remain unchanged.**

This works because dashboards never reference `android_spans` directly — they always go through the stored functions. Swapping the function body is the only change needed.

---

## Table of Contents

1. [Dashboard Parameters](#1-dashboard-parameters)
2. [Phase 1: Stored Functions (raw table)](#2-phase-1-stored-functions-raw-table)
3. [Dashboard 1: SecretKeyGeneration — Health & Reliability](#3-dashboard-1-secretkeygeneration--health--reliability)
4. [Dashboard 2: SecretKeyGeneration — Performance](#4-dashboard-2-secretkeygeneration--performance)
5. [Dashboard 3: SecretKeyRetrieval — Health & Reliability](#5-dashboard-3-secretkeyretrieval--health--reliability)
6. [Dashboard 4: SecretKeyRetrieval — Performance](#6-dashboard-4-secretkeyretrieval--performance)
7. [Dashboard 5: Device & Ecosystem (Shared)](#7-dashboard-5-device--ecosystem-shared)
8. [Dashboard 6: Serializer Migration (Phase 1 Feature)](#8-dashboard-6-serializer-migration-phase-1-feature)
9. [Dashboard 7: Algorithm Migration (Phase 2 Feature)](#9-dashboard-7-algorithm-migration-phase-2-feature)
10. [Dashboard 8: Error Deep Dive (Shared)](#10-dashboard-8-error-deep-dive-shared)
11. [Phase 2: Materialized Views & Function Swap](#11-phase-2-materialized-views--function-swap)

---

## 1. Dashboard Parameters

Configure these once at the ADX Dashboard level. They propagate to all tiles.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `_startTime` | datetime | `ago(7d)` | Time range start |
| `_endTime` | datetime | `now()` | Time range end |
| `_broker_versions` | dynamic (multi-select) | `dynamic([])` | Empty = all |
| `_active_broker_host` | dynamic (multi-select) | `dynamic([])` | Empty = all |
| `_broker_host_app_version` | dynamic (multi-select) | `dynamic([])` | Empty = all |

---

## 2. Phase 1: Stored Functions (raw table)

### 2.1 Base Filter — SecretKeyGeneration

```kql
.create-or-alter function with (folder='SecretKey', docstring='Base filter for SecretKeyGeneration — Phase 1: raw table')
fn_SecretKeyGeneration(_startTime:datetime, _endTime:datetime, _broker_versions:dynamic, _active_broker_host:dynamic, _broker_host_app_version:dynamic) {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | where PipelineInfo_IngestionTime between (_startTime .. _endTime)
    | where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
    | where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
    | where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
}
```

### 2.2 Base Filter — SecretKeyRetrieval

```kql
.create-or-alter function with (folder='SecretKey', docstring='Base filter for SecretKeyRetrieval — Phase 1: raw table')
fn_SecretKeyRetrieval(_startTime:datetime, _endTime:datetime, _broker_versions:dynamic, _active_broker_host:dynamic, _broker_host_app_version:dynamic) {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | where PipelineInfo_IngestionTime between (_startTime .. _endTime)
    | where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
    | where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
    | where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
}
```

---

## 3. Dashboard 1: SecretKeyGeneration — Health & Reliability

### 3.1 Success Rate (%) over time

> Visualization: Time chart, Y-axis: SuccessRate

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Success = countif(span_status == 'OK')
    by bin(PipelineInfo_IngestionTime, 1h)
| extend SuccessRate = round(100.0 * Success / Total, 2)
| project PipelineInfo_IngestionTime, SuccessRate
| order by PipelineInfo_IngestionTime asc
```

### 3.2 Volume over time

> Visualization: Time chart, Y-axis: Count

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1h)
| order by PipelineInfo_IngestionTime asc
```

### 3.3 Error Count by Type

> Visualization: Bar chart, X-axis: error_code, Y-axis: Count

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by error_type, error_code
| order by Count desc
```

### 3.4 Error Rate by Broker Version

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by broker_version
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project broker_version, Total, Failures, FailureRate
| order by FailureRate desc
```

### 3.5 Error Rate by App Version

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by AppInfo_Version
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project AppInfo_Version, Total, Failures, FailureRate
| order by FailureRate desc
```

---

## 4. Dashboard 2: SecretKeyGeneration — Performance

### 4.1 Elapsed Time Percentiles over time

> Visualization: Time chart, Y-axis: p50/p90/p99

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize 
    p50 = percentile(elapsed_time, 50),
    p90 = percentile(elapsed_time, 90),
    p99 = percentile(elapsed_time, 99)
    by bin(PipelineInfo_IngestionTime, 1h)
| order by PipelineInfo_IngestionTime asc
```

### 4.2 Serialization Duration Percentiles over time

> Visualization: Time chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK' and isnotnull(secret_key_serialization_duration)
| summarize 
    p50 = percentile(secret_key_serialization_duration, 50),
    p90 = percentile(secret_key_serialization_duration, 90),
    p99 = percentile(secret_key_serialization_duration, 99)
    by bin(PipelineInfo_IngestionTime, 1h)
| order by PipelineInfo_IngestionTime asc
```

### 4.3 Elapsed Time Histogram

> Visualization: Bar chart, X-axis: Bucket, Y-axis: Count

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend Bucket = case(
    elapsed_time < 10, "<10ms",
    elapsed_time < 50, "10-50ms",
    elapsed_time < 100, "50-100ms",
    elapsed_time < 500, "100-500ms",
    elapsed_time < 1000, "500ms-1s",
    elapsed_time < 5000, "1-5s",
    ">5s")
| summarize Count = count() by Bucket
| order by Bucket asc
```

### 4.4 Slow Operations (>p99)

> Visualization: Table (drill-down)

```kql
let _data = fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version);
let _p99 = _data | where span_status == 'OK' | summarize p99 = percentile(elapsed_time, 99);
_data
| where elapsed_time > toscalar(_p99)
| project PipelineInfo_IngestionTime, elapsed_time, secret_key_serialization_duration,
    DeviceInfo_Model, DeviceInfo_Make, DeviceInfo_OsVersion, broker_version, AppInfo_Version,
    elected_cipher_transformation, trace_id, correlation_id
| order by elapsed_time desc
| take 100
```

### 4.5 Latency by OS Version

> Visualization: Bar chart, X-axis: DeviceInfo_OsVersion, Y-axis: p50

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize 
    p50 = percentile(elapsed_time, 50),
    p90 = percentile(elapsed_time, 90),
    Count = count()
    by DeviceInfo_OsVersion
| order by DeviceInfo_OsVersion asc
```

---

## 5. Dashboard 3: SecretKeyRetrieval — Health & Reliability

### 5.1 Success Rate (%) over time

> Visualization: Time chart, Y-axis: SuccessRate

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Success = countif(span_status == 'OK')
    by bin(PipelineInfo_IngestionTime, 1h)
| extend SuccessRate = round(100.0 * Success / Total, 2)
| project PipelineInfo_IngestionTime, SuccessRate
| order by PipelineInfo_IngestionTime asc
```

### 5.2 Volume over time

> Visualization: Time chart, Y-axis: Count

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1h)
| order by PipelineInfo_IngestionTime asc
```

### 5.3 Error Count by Type

> Visualization: Bar chart, X-axis: error_code, Y-axis: Count

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by error_type, error_code
| order by Count desc
```

### 5.4 Error Rate by Broker Version

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by broker_version
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project broker_version, Total, Failures, FailureRate
| order by FailureRate desc
```

### 5.5 Error Rate by App Version

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by AppInfo_Version
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project AppInfo_Version, Total, Failures, FailureRate
| order by FailureRate desc
```

---

## 6. Dashboard 4: SecretKeyRetrieval — Performance

### 6.1 Elapsed Time Percentiles over time

> Visualization: Time chart, Y-axis: p50/p90/p99

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize 
    p50 = percentile(elapsed_time, 50),
    p90 = percentile(elapsed_time, 90),
    p99 = percentile(elapsed_time, 99)
    by bin(PipelineInfo_IngestionTime, 1h)
| order by PipelineInfo_IngestionTime asc
```

### 6.2 Serialization Duration Percentiles over time

> Visualization: Time chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK' and isnotnull(secret_key_serialization_duration)
| summarize 
    p50 = percentile(secret_key_serialization_duration, 50),
    p90 = percentile(secret_key_serialization_duration, 90),
    p99 = percentile(secret_key_serialization_duration, 99)
    by bin(PipelineInfo_IngestionTime, 1h)
| order by PipelineInfo_IngestionTime asc
```

### 6.3 Elapsed Time Histogram

> Visualization: Bar chart, X-axis: Bucket, Y-axis: Count

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend Bucket = case(
    elapsed_time < 10, "<10ms",
    elapsed_time < 50, "10-50ms",
    elapsed_time < 100, "50-100ms",
    elapsed_time < 500, "100-500ms",
    elapsed_time < 1000, "500ms-1s",
    elapsed_time < 5000, "1-5s",
    ">5s")
| summarize Count = count() by Bucket
| order by Bucket asc
```

### 6.4 Slow Operations (>p99)

> Visualization: Table (drill-down)

```kql
let _data = fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version);
let _p99 = _data | where span_status == 'OK' | summarize p99 = percentile(elapsed_time, 99);
_data
| where elapsed_time > toscalar(_p99)
| project PipelineInfo_IngestionTime, elapsed_time, secret_key_serialization_duration,
    DeviceInfo_Model, DeviceInfo_Make, DeviceInfo_OsVersion, broker_version, AppInfo_Version,
    elected_cipher_transformation, trace_id, correlation_id
| order by elapsed_time desc
| take 100
```

### 6.5 Latency by OS Version

> Visualization: Bar chart, X-axis: DeviceInfo_OsVersion, Y-axis: p50

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize 
    p50 = percentile(elapsed_time, 50),
    p90 = percentile(elapsed_time, 90),
    Count = count()
    by DeviceInfo_OsVersion
| order by DeviceInfo_OsVersion asc
```

---

## 7. Dashboard 5: Device & Ecosystem (Shared)

### 7.1 Top Device Models — Generation

> Visualization: Bar chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by DeviceInfo_Model
| top 20 by Count
```

### 7.2 Top Device Models — Retrieval

> Visualization: Bar chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by DeviceInfo_Model
| top 20 by Count
```

### 7.3 OS Version Distribution — Generation

> Visualization: Pie chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by DeviceInfo_OsVersion
| order by DeviceInfo_OsVersion asc
```

### 7.4 OS Version Distribution — Retrieval

> Visualization: Pie chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by DeviceInfo_OsVersion
| order by DeviceInfo_OsVersion asc
```

### 7.5 Device Make Distribution — Generation

> Visualization: Pie chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by DeviceInfo_Make
| top 15 by Count
```

### 7.6 Device Make Distribution — Retrieval

> Visualization: Pie chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by DeviceInfo_Make
| top 15 by Count
```

### 7.7 Network Type — Generation

> Visualization: Pie chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by DeviceInfo_NetworkType
| order by Count desc
```

### 7.8 Broker Version Rollout — Generation

> Visualization: Stacked area chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), broker_version
| order by PipelineInfo_IngestionTime asc
```

### 7.9 App Version Rollout — Generation

> Visualization: Stacked area chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), AppInfo_Version
| order by PipelineInfo_IngestionTime asc
```

---

## 8. Dashboard 6: Serializer Migration (Phase 1 Feature)

### 8.1 Serializer Adoption over time — Generation

> Visualization: Stacked area chart — old serializer (0) vs new serializer ID

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1h), SerializerId
| order by PipelineInfo_IngestionTime asc
```

### 8.2 Serializer Adoption over time — Retrieval

> Visualization: Stacked area chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1h), SerializerId
| order by PipelineInfo_IngestionTime asc
```

### 8.3 Serializer Adoption % over time — Generation

> Visualization: Line chart — % migration progress

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), SerializerId
| as T
| join kind=inner (T | summarize DayTotal = sum(Count) by PipelineInfo_IngestionTime) on PipelineInfo_IngestionTime
| extend Pct = round(100.0 * Count / DayTotal, 2)
| project PipelineInfo_IngestionTime, SerializerId, Pct
| order by PipelineInfo_IngestionTime asc
```

### 8.4 Serializer Adoption % over time — Retrieval

> Visualization: Line chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), SerializerId
| as T
| join kind=inner (T | summarize DayTotal = sum(Count) by PipelineInfo_IngestionTime) on PipelineInfo_IngestionTime
| extend Pct = round(100.0 * Count / DayTotal, 2)
| project PipelineInfo_IngestionTime, SerializerId, Pct
| order by PipelineInfo_IngestionTime asc
```

### 8.5 Serialization Duration: Old vs New — Generation

> Visualization: Table — side-by-side perf comparison

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize 
    Count = count(),
    p50 = percentile(secret_key_serialization_duration, 50),
    p90 = percentile(secret_key_serialization_duration, 90),
    p99 = percentile(secret_key_serialization_duration, 99)
    by SerializerId
```

### 8.6 Serialization Duration: Old vs New — Retrieval

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize 
    Count = count(),
    p50 = percentile(secret_key_serialization_duration, 50),
    p90 = percentile(secret_key_serialization_duration, 90),
    p99 = percentile(secret_key_serialization_duration, 99)
    by SerializerId
```

### 8.7 Serialization Duration over time: Old vs New — Generation

> Visualization: Time chart, split by SerializerId

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = strcat("serializer_", tostring(secret_key_wrapping_serializer_id))
| summarize p50 = percentile(secret_key_serialization_duration, 50), p90 = percentile(secret_key_serialization_duration, 90)
    by bin(PipelineInfo_IngestionTime, 1h), SerializerId
| order by PipelineInfo_IngestionTime asc
```

### 8.8 Serialization Duration over time: Old vs New — Retrieval

> Visualization: Time chart, split by SerializerId

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = strcat("serializer_", tostring(secret_key_wrapping_serializer_id))
| summarize p50 = percentile(secret_key_serialization_duration, 50), p90 = percentile(secret_key_serialization_duration, 90)
    by bin(PipelineInfo_IngestionTime, 1h), SerializerId
| order by PipelineInfo_IngestionTime asc
```

### 8.9 Error Rate: Old vs New Serializer — Generation

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by SerializerId
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project SerializerId, Total, Failures, FailureRate
```

### 8.10 Error Rate: Old vs New Serializer — Retrieval

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by SerializerId
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project SerializerId, Total, Failures, FailureRate
```

### 8.11 New Serializer Rollout by Broker Version — Generation

> Visualization: Stacked bar chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize Count = count() by broker_version, SerializerId
| order by broker_version desc
```

### 8.12 New Serializer Rollout by Broker Version — Retrieval

> Visualization: Stacked bar chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| summarize Count = count() by broker_version, SerializerId
| order by broker_version desc
```

---

## 9. Dashboard 7: Algorithm Migration (Phase 2 Feature)

### 9.1 Algorithm Distribution over time — Generation

> Visualization: Stacked area chart — AES vs new algorithms

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), secret_key_algorithm
| order by PipelineInfo_IngestionTime asc
```

### 9.2 Algorithm Distribution over time — Retrieval

> Visualization: Stacked area chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), secret_key_algorithm
| order by PipelineInfo_IngestionTime asc
```

### 9.3 Cipher Transformation Distribution over time — Generation

> Visualization: Stacked area chart — PKCS1 vs OAEP vs new

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), elected_cipher_transformation
| order by PipelineInfo_IngestionTime asc
```

### 9.4 Cipher Transformation Distribution over time — Retrieval

> Visualization: Stacked area chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), elected_cipher_transformation
| order by PipelineInfo_IngestionTime asc
```

### 9.5 Algorithm Adoption % — Generation

> Visualization: Line chart

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), secret_key_algorithm
| as T
| join kind=inner (T | summarize DayTotal = sum(Count) by PipelineInfo_IngestionTime) on PipelineInfo_IngestionTime
| extend Pct = round(100.0 * Count / DayTotal, 2)
| project PipelineInfo_IngestionTime, secret_key_algorithm, Pct
| order by PipelineInfo_IngestionTime asc
```

### 9.6 Algorithm Adoption % — Retrieval

> Visualization: Line chart

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1d), secret_key_algorithm
| as T
| join kind=inner (T | summarize DayTotal = sum(Count) by PipelineInfo_IngestionTime) on PipelineInfo_IngestionTime
| extend Pct = round(100.0 * Count / DayTotal, 2)
| project PipelineInfo_IngestionTime, secret_key_algorithm, Pct
| order by PipelineInfo_IngestionTime asc
```

### 9.7 Perf by Algorithm — Generation

> Visualization: Table — perf comparison across algorithm+cipher combos

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize 
    Count = count(),
    p50 = percentile(secret_key_serialization_duration, 50),
    p90 = percentile(secret_key_serialization_duration, 90),
    p99 = percentile(secret_key_serialization_duration, 99)
    by secret_key_algorithm, elected_cipher_transformation
| order by Count desc
```

### 9.8 Perf by Algorithm — Retrieval

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize 
    Count = count(),
    p50 = percentile(secret_key_serialization_duration, 50),
    p90 = percentile(secret_key_serialization_duration, 90),
    p99 = percentile(secret_key_serialization_duration, 99)
    by secret_key_algorithm, elected_cipher_transformation
| order by Count desc
```

### 9.9 Error Rate by Algorithm — Generation

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by secret_key_algorithm, elected_cipher_transformation
| extend FailureRate = round(100.0 * Failures / Total, 2)
| order by FailureRate desc
```

### 9.10 Error Rate by Algorithm — Retrieval

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by secret_key_algorithm, elected_cipher_transformation
| extend FailureRate = round(100.0 * Failures / Total, 2)
| order by FailureRate desc
```

### 9.11 Device Capability: Available Transformations — Generation

> Visualization: Table — what % of devices support new algorithms

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize Count = count() by available_transformation_list, key_pair_supported_paddings
| order by Count desc
```

### 9.12 Device Capability: Available Transformations — Retrieval

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status == 'OK'
| summarize Count = count() by available_transformation_list, key_pair_supported_paddings
| order by Count desc
```

---

## 10. Dashboard 8: Error Deep Dive (Shared)

### 10.1 Error Timeline — Generation

> Visualization: Time chart, split by error_code

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1h), error_code
| order by PipelineInfo_IngestionTime asc
```

### 10.2 Error Timeline — Retrieval

> Visualization: Time chart, split by error_code

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by bin(PipelineInfo_IngestionTime, 1h), error_code
| order by PipelineInfo_IngestionTime asc
```

### 10.3 Error Breakdown — Generation

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by error_type, error_code, error_message, error_location
| order by Count desc
```

### 10.4 Error Breakdown — Retrieval

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by error_type, error_code, error_message, error_location
| order by Count desc
```

### 10.5 Exception Cause Breakdown — Generation

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK' and isnotempty(exception_cause_error_type)
| summarize Count = count() by exception_cause_error_type, exception_cause_error_code, exception_cause_error_message
| order by Count desc
```

### 10.6 Exception Cause Breakdown — Retrieval

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK' and isnotempty(exception_cause_error_type)
| summarize Count = count() by exception_cause_error_type, exception_cause_error_code, exception_cause_error_message
| order by Count desc
```

### 10.7 Errors by Device Model — Generation

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by DeviceInfo_Model, error_code
| top 50 by Count
```

### 10.8 Errors by OS Version — Generation

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by DeviceInfo_OsVersion, error_code
| order by Count desc
```

### 10.9 Errors by Broker Version — Generation

> Visualization: Table

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| summarize Count = count() by broker_version, error_code
| order by Count desc
```

### 10.10 Failed Trace Explorer — Generation

> Visualization: Table (detail drill-down)

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| project PipelineInfo_IngestionTime, trace_id, correlation_id,
    error_type, error_code, error_message, error_location,
    exception_cause_error_type, exception_cause_error_code, exception_cause_error_message,
    DeviceInfo_Model, DeviceInfo_OsVersion, broker_version, AppInfo_Version,
    elected_cipher_transformation, secret_key_wrapping_serializer_id
| order by PipelineInfo_IngestionTime desc
| take 200
```

### 10.11 Failed Trace Explorer — Retrieval

> Visualization: Table (detail drill-down)

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| where span_status != 'OK'
| project PipelineInfo_IngestionTime, trace_id, correlation_id,
    error_type, error_code, error_message, error_location,
    exception_cause_error_type, exception_cause_error_code, exception_cause_error_message,
    DeviceInfo_Model, DeviceInfo_OsVersion, broker_version, AppInfo_Version,
    elected_cipher_transformation, secret_key_wrapping_serializer_id
| order by PipelineInfo_IngestionTime desc
| take 200
```

---

## 11. Phase 2: Materialized Views & Function Swap

When ready to optimize, create MVs then swap the function bodies. **No dashboard changes needed.**

### 11.1 Create Materialized Views

Run all 10 MV creation commands (5 per span):

```kql
// --- SecretKeyGeneration MVs ---

.create materialized-view with (folder='SecretKey/Generation', docstring='Hourly health metrics for SecretKeyGeneration')
mv_SKGen_Health on table android_spans {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | summarize 
        Total = count(),
        Success = countif(span_status == 'OK'),
        Failures = countif(span_status != 'OK')
        by bin(PipelineInfo_IngestionTime, 1h),
           broker_version, AppInfo_Version, active_broker_package_name
}

.create materialized-view with (folder='SecretKey/Generation', docstring='Hourly latency for SecretKeyGeneration')
mv_SKGen_Latency on table android_spans {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | where span_status == 'OK'
    | summarize 
        p50_elapsed = percentile(elapsed_time, 50),
        p90_elapsed = percentile(elapsed_time, 90),
        p99_elapsed = percentile(elapsed_time, 99),
        p50_serialization = percentile(secret_key_serialization_duration, 50),
        p90_serialization = percentile(secret_key_serialization_duration, 90),
        p99_serialization = percentile(secret_key_serialization_duration, 99),
        Count = count()
        by bin(PipelineInfo_IngestionTime, 1h)
}

.create materialized-view with (folder='SecretKey/Generation', docstring='Hourly error aggregation for SecretKeyGeneration')
mv_SKGen_Errors on table android_spans {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | where span_status != 'OK'
    | summarize Count = count()
        by bin(PipelineInfo_IngestionTime, 1h),
           error_type, error_code, error_message, error_location,
           exception_cause_error_type, exception_cause_error_code,
           broker_version, AppInfo_Version, DeviceInfo_Model, DeviceInfo_OsVersion
}

.create materialized-view with (folder='SecretKey/Generation', docstring='Hourly crypto and serializer metrics for SecretKeyGeneration')
mv_SKGen_CryptoSerializer on table android_spans {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | where span_status == 'OK'
    | summarize 
        Count = count(),
        p50_serialization = percentile(secret_key_serialization_duration, 50),
        p90_serialization = percentile(secret_key_serialization_duration, 90),
        p99_serialization = percentile(secret_key_serialization_duration, 99)
        by bin(PipelineInfo_IngestionTime, 1h),
           secret_key_wrapping_serializer_id, elected_cipher_transformation,
           secret_key_algorithm, secret_key_size, secret_key_transformation,
           available_transformation_list, key_pair_supported_paddings,
           broker_version, AppInfo_Version
}

.create materialized-view with (folder='SecretKey/Generation', docstring='Daily device distribution for SecretKeyGeneration')
mv_SKGen_Device on table android_spans {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | summarize Count = count()
        by bin(PipelineInfo_IngestionTime, 1d),
           DeviceInfo_Model, DeviceInfo_Make, DeviceInfo_OsVersion, DeviceInfo_NetworkType,
           broker_version, AppInfo_Version
}


// --- SecretKeyRetrieval MVs ---

.create materialized-view with (folder='SecretKey/Retrieval', docstring='Hourly health metrics for SecretKeyRetrieval')
mv_SKRet_Health on table android_spans {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | summarize 
        Total = count(),
        Success = countif(span_status == 'OK'),
        Failures = countif(span_status != 'OK')
        by bin(PipelineInfo_IngestionTime, 1h),
           broker_version, AppInfo_Version, active_broker_package_name
}

.create materialized-view with (folder='SecretKey/Retrieval', docstring='Hourly latency for SecretKeyRetrieval')
mv_SKRet_Latency on table android_spans {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | where span_status == 'OK'
    | summarize 
        p50_elapsed = percentile(elapsed_time, 50),
        p90_elapsed = percentile(elapsed_time, 90),
        p99_elapsed = percentile(elapsed_time, 99),
        p50_serialization = percentile(secret_key_serialization_duration, 50),
        p90_serialization = percentile(secret_key_serialization_duration, 90),
        p99_serialization = percentile(secret_key_serialization_duration, 99),
        Count = count()
        by bin(PipelineInfo_IngestionTime, 1h)
}

.create materialized-view with (folder='SecretKey/Retrieval', docstring='Hourly error aggregation for SecretKeyRetrieval')
mv_SKRet_Errors on table android_spans {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | where span_status != 'OK'
    | summarize Count = count()
        by bin(PipelineInfo_IngestionTime, 1h),
           error_type, error_code, error_message, error_location,
           exception_cause_error_type, exception_cause_error_code,
           broker_version, AppInfo_Version, DeviceInfo_Model, DeviceInfo_OsVersion
}

.create materialized-view with (folder='SecretKey/Retrieval', docstring='Hourly crypto and serializer metrics for SecretKeyRetrieval')
mv_SKRet_CryptoSerializer on table android_spans {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | where span_status == 'OK'
    | summarize 
        Count = count(),
        p50_serialization = percentile(secret_key_serialization_duration, 50),
        p90_serialization = percentile(secret_key_serialization_duration, 90),
        p99_serialization = percentile(secret_key_serialization_duration, 99)
        by bin(PipelineInfo_IngestionTime, 1h),
           secret_key_wrapping_serializer_id, elected_cipher_transformation,
           secret_key_algorithm, secret_key_size, secret_key_transformation,
           available_transformation_list, key_pair_supported_paddings,
           broker_version, AppInfo_Version
}

.create materialized-view with (folder='SecretKey/Retrieval', docstring='Daily device distribution for SecretKeyRetrieval')
mv_SKRet_Device on table android_spans {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | summarize Count = count()
        by bin(PipelineInfo_IngestionTime, 1d),
           DeviceInfo_Model, DeviceInfo_Make, DeviceInfo_OsVersion, DeviceInfo_NetworkType,
           broker_version, AppInfo_Version
}
```

### 11.2 Swap Stored Functions

After MVs are backfilled, update the two functions. Dashboards automatically pick up the optimization:

```kql
// NOTE: These are PHASE 2 replacements. Do NOT run until MVs are created and backfilled.

.create-or-alter function with (folder='SecretKey', docstring='Base filter for SecretKeyGeneration — Phase 2: uses MV union for full fidelity')
fn_SecretKeyGeneration(_startTime:datetime, _endTime:datetime, _broker_versions:dynamic, _active_broker_host:dynamic, _broker_host_app_version:dynamic) {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | where PipelineInfo_IngestionTime between (_startTime .. _endTime)
    | where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
    | where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
    | where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
}

.create-or-alter function with (folder='SecretKey', docstring='Base filter for SecretKeyRetrieval — Phase 2: uses MV union for full fidelity')
fn_SecretKeyRetrieval(_startTime:datetime, _endTime:datetime, _broker_versions:dynamic, _active_broker_host:dynamic, _broker_host_app_version:dynamic) {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | where PipelineInfo_IngestionTime between (_startTime .. _endTime)
    | where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
    | where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
    | where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
}
```

> **Note:** The base functions remain the same since they return row-level data needed by histogram, outlier, and trace-explorer tiles. The MV optimization happens when dashboard queries are rewritten to query MVs directly for aggregated tiles (health, latency, crypto). See the previous plan iteration for the MV-based dashboard queries.

### 11.3 Phase 2 Query Rewrites (for aggregated tiles only)

Tiles that only need pre-aggregated data can be switched to query MVs directly for better performance. Examples:

- **Health tiles** (3.1–3.5, 5.1–5.5) → query `mv_SKGen_Health` / `mv_SKRet_Health` with `sum(Total)`, `sum(Success)`, `sum(Failures)`
- **Latency tiles** (4.1–4.2, 6.1–6.2) → query `mv_SKGen_Latency` / `mv_SKRet_Latency` directly
- **Crypto/Serializer tiles** (8.1–8.8, 9.1–9.8) → query `mv_SKGen_CryptoSerializer` / `mv_SKRet_CryptoSerializer` with `sum(Count)`, `avg(p50_serialization)`
- **Device tiles** (7.1–7.9) → query `mv_SKGen_Device` / `mv_SKRet_Device` with `sum(Count)`

Tiles that **must stay on raw table** (via stored functions):
- Histograms (4.3, 6.3)
- Slow operations / outlier tables (4.4, 6.4)
- Error rate by serializer/algorithm (8.9–8.10, 9.9–9.10)
- Failed trace explorers (10.10–10.11)

---

## Execution Checklist

### Phase 1 (Now)
- [ ] Create stored function `fn_SecretKeyGeneration` (section 2.1)
- [ ] Create stored function `fn_SecretKeyRetrieval` (section 2.2)
- [ ] Create Dashboard 1: SecretKeyGeneration — Health & Reliability (section 3)
- [ ] Create Dashboard 2: SecretKeyGeneration — Performance (section 4)
- [ ] Create Dashboard 3: SecretKeyRetrieval — Health & Reliability (section 5)
- [ ] Create Dashboard 4: SecretKeyRetrieval — Performance (section 6)
- [ ] Create Dashboard 5: Device & Ecosystem (section 7)
- [ ] Create Dashboard 6: Serializer Migration (section 8)
- [ ] Create Dashboard 7: Algorithm Migration (section 9)
- [ ] Create Dashboard 8: Error Deep Dive (section 10)

### Phase 2 (When Ready)
- [ ] Create 10 materialized views (section 11.1)
- [ ] Wait for MV backfill to complete
- [ ] Rewrite aggregated dashboard tiles to query MVs directly (section 11.3)
- [ ] Validate dashboard results match Phase 1 output
- [ ] Remove raw-table queries from aggregated tiles

---

## Summary

| | Phase 1 | Phase 2 |
|---|---|---|
| **Data source** | Raw `android_spans` table | MVs for aggregated tiles, raw table for detail tiles |
| **Stored functions** | 2 (query raw table) | 2 (unchanged, still used by detail tiles) |
| **Materialized views** | 0 | 10 |
| **Dashboards** | 8 | 8 (unchanged) |
| **Total query tiles** | 55 | 55 (some rewritten to use MVs) |
| **Dashboard changes at swap** | — | Only aggregated tiles get query body updates |
