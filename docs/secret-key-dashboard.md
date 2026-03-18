# SecretKey Operations — ADX Dashboard & Materialized Views Plan

**Cluster:** `https://idsharedeus2.kusto.windows.net/`  
**Database:** `f9614845898c42639bce0e2b8794b519` (ad-accounts-android-otel)  
**Table:** `android_spans`  
**Span Names:** `SecretKeyGeneration`, `SecretKeyRetrieval`

---

## Table of Contents

1. [Stored Functions](#1-stored-functions)
2. [Materialized Views — SecretKeyGeneration](#2-materialized-views--secretkeygeneration)
3. [Materialized Views — SecretKeyRetrieval](#3-materialized-views--secretkeyretrieval)
4. [Dashboard Parameters](#4-dashboard-parameters)
5. [Dashboard 1: SecretKeyGeneration — Health & Reliability](#5-dashboard-1-secretkeygeneration--health--reliability)
6. [Dashboard 2: SecretKeyGeneration — Performance](#6-dashboard-2-secretkeygeneration--performance)
7. [Dashboard 3: SecretKeyRetrieval — Health & Reliability](#7-dashboard-3-secretkeyretrieval--health--reliability)
8. [Dashboard 4: SecretKeyRetrieval — Performance](#8-dashboard-4-secretkeyretrieval--performance)
9. [Dashboard 5: Device & Ecosystem (Shared)](#9-dashboard-5-device--ecosystem-shared)
10. [Dashboard 6: Serializer Migration (Phase 1)](#10-dashboard-6-serializer-migration-phase-1)
11. [Dashboard 7: Algorithm Migration (Phase 2)](#11-dashboard-7-algorithm-migration-phase-2)

---

## 1. Stored Functions

### 1.1 SecretKeyGeneration Base Filter

```kql
.create-or-alter function with (folder='SecretKey', docstring='Base filter for SecretKeyGeneration')
fn_SecretKeyGeneration(_startTime:datetime, _endTime:datetime, _broker_versions:dynamic, _active_broker_host:dynamic, _broker_host_app_version:dynamic) {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | where PipelineInfo_IngestionTime between (_startTime .. _endTime)
    | where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
    | where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
    | where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
}
```

### 1.2 SecretKeyRetrieval Base Filter

```kql
.create-or-alter function with (folder='SecretKey', docstring='Base filter for SecretKeyRetrieval')
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

## 2. Materialized Views — SecretKeyGeneration

### 2.1 mv_SKGen_Health

```kql
.create materialized-view with (folder='SecretKey/Generation', docstring='Hourly health metrics for SecretKeyGeneration')
mv_SKGen_Health on table android_spans {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | summarize 
        Total = count(),
        Success = countif(span_status == 'OK'),
        Failures = countif(span_status != 'OK')
        by bin(PipelineInfo_IngestionTime, 1h),
           broker_version,
           AppInfo_Version,
           active_broker_package_name
}
```

### 2.2 mv_SKGen_Latency

```kql
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
```

### 2.3 mv_SKGen_Errors

```kql
.create materialized-view with (folder='SecretKey/Generation', docstring='Hourly error aggregation for SecretKeyGeneration')
mv_SKGen_Errors on table android_spans {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | where span_status != 'OK'
    | summarize 
        Count = count()
        by bin(PipelineInfo_IngestionTime, 1h),
           error_type,
           error_code,
           error_message,
           error_location,
           exception_cause_error_type,
           exception_cause_error_code,
           broker_version,
           AppInfo_Version,
           DeviceInfo_Model,
           DeviceInfo_OsVersion
}
```

### 2.4 mv_SKGen_CryptoSerializer

```kql
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
           secret_key_wrapping_serializer_id,
           elected_cipher_transformation,
           secret_key_algorithm,
           secret_key_size,
           secret_key_transformation,
           available_transformation_list,
           key_pair_supported_paddings,
           broker_version,
           AppInfo_Version
}
```

### 2.5 mv_SKGen_Device

```kql
.create materialized-view with (folder='SecretKey/Generation', docstring='Daily device distribution for SecretKeyGeneration')
mv_SKGen_Device on table android_spans {
    android_spans
    | where span_name == 'SecretKeyGeneration'
    | summarize 
        Count = count()
        by bin(PipelineInfo_IngestionTime, 1d),
           DeviceInfo_Model,
           DeviceInfo_Make,
           DeviceInfo_OsVersion,
           DeviceInfo_NetworkType,
           broker_version,
           AppInfo_Version
}
```

---

## 3. Materialized Views — SecretKeyRetrieval

### 3.1 mv_SKRet_Health

```kql
.create materialized-view with (folder='SecretKey/Retrieval', docstring='Hourly health metrics for SecretKeyRetrieval')
mv_SKRet_Health on table android_spans {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | summarize 
        Total = count(),
        Success = countif(span_status == 'OK'),
        Failures = countif(span_status != 'OK')
        by bin(PipelineInfo_IngestionTime, 1h),
           broker_version,
           AppInfo_Version,
           active_broker_package_name
}
```

### 3.2 mv_SKRet_Latency

```kql
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
```

### 3.3 mv_SKRet_Errors

```kql
.create materialized-view with (folder='SecretKey/Retrieval', docstring='Hourly error aggregation for SecretKeyRetrieval')
mv_SKRet_Errors on table android_spans {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | where span_status != 'OK'
    | summarize 
        Count = count()
        by bin(PipelineInfo_IngestionTime, 1h),
           error_type,
           error_code,
           error_message,
           error_location,
           exception_cause_error_type,
           exception_cause_error_code,
           broker_version,
           AppInfo_Version,
           DeviceInfo_Model,
           DeviceInfo_OsVersion
}
```

### 3.4 mv_SKRet_CryptoSerializer

```kql
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
           secret_key_wrapping_serializer_id,
           elected_cipher_transformation,
           secret_key_algorithm,
           secret_key_size,
           secret_key_transformation,
           available_transformation_list,
           key_pair_supported_paddings,
           broker_version,
           AppInfo_Version
}
```

### 3.5 mv_SKRet_Device

```kql
.create materialized-view with (folder='SecretKey/Retrieval', docstring='Daily device distribution for SecretKeyRetrieval')
mv_SKRet_Device on table android_spans {
    android_spans
    | where span_name == 'SecretKeyRetrieval'
    | summarize 
        Count = count()
        by bin(PipelineInfo_IngestionTime, 1d),
           DeviceInfo_Model,
           DeviceInfo_Make,
           DeviceInfo_OsVersion,
           DeviceInfo_NetworkType,
           broker_version,
           AppInfo_Version
}
```

---

## 4. Dashboard Parameters

Configure these once at the ADX Dashboard level. They propagate to all tiles.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `_startTime` | datetime | `ago(7d)` | Time range start |
| `_endTime` | datetime | `now()` | Time range end |
| `_broker_versions` | dynamic (multi-select) | `dynamic([])` | Empty = all |
| `_active_broker_host` | dynamic (multi-select) | `dynamic([])` | Empty = all |
| `_broker_host_app_version` | dynamic (multi-select) | `dynamic([])` | Empty = all |

---

## 5. Dashboard 1: SecretKeyGeneration — Health & Reliability

### 5.1 Success Rate (%) over time

> Visualization: Time chart, Y-axis: SuccessRate

```kql
mv_SKGen_Health
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Total = sum(Total), Success = sum(Success) by PipelineInfo_IngestionTime
| extend SuccessRate = round(100.0 * Success / Total, 2)
| project PipelineInfo_IngestionTime, SuccessRate
| order by PipelineInfo_IngestionTime asc
```

### 5.2 Volume over time

> Visualization: Time chart, Y-axis: Count

```kql
mv_SKGen_Health
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Total) by PipelineInfo_IngestionTime
| order by PipelineInfo_IngestionTime asc
```

### 5.3 Error Count by Type

> Visualization: Bar chart, X-axis: error_code, Y-axis: Count

```kql
mv_SKGen_Errors
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by error_type, error_code
| order by Count desc
```

### 5.4 Error Rate by Broker Version

> Visualization: Table

```kql
mv_SKGen_Health
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Total = sum(Total), Failures = sum(Failures) by broker_version
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project broker_version, Total, Failures, FailureRate
| order by FailureRate desc
```

### 5.5 Error Rate by App Version

> Visualization: Table

```kql
mv_SKGen_Health
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Total = sum(Total), Failures = sum(Failures) by AppInfo_Version
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project AppInfo_Version, Total, Failures, FailureRate
| order by FailureRate desc
```

---

## 6. Dashboard 2: SecretKeyGeneration — Performance

### 6.1 Elapsed Time Percentiles over time

> Visualization: Time chart, Y-axis: p50/p90/p99

```kql
mv_SKGen_Latency
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| project PipelineInfo_IngestionTime, p50_elapsed, p90_elapsed, p99_elapsed
| order by PipelineInfo_IngestionTime asc
```

### 6.2 Serialization Duration Percentiles over time

> Visualization: Time chart

```kql
mv_SKGen_Latency
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| project PipelineInfo_IngestionTime, p50_serialization, p90_serialization, p99_serialization
| order by PipelineInfo_IngestionTime asc
```

### 6.3 Elapsed Time Histogram

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

### 6.4 Slow Operations (>p99)

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

### 6.5 Latency by OS Version

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

## 7. Dashboard 3: SecretKeyRetrieval — Health & Reliability

### 7.1 Success Rate (%) over time

> Visualization: Time chart, Y-axis: SuccessRate

```kql
mv_SKRet_Health
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Total = sum(Total), Success = sum(Success) by PipelineInfo_IngestionTime
| extend SuccessRate = round(100.0 * Success / Total, 2)
| project PipelineInfo_IngestionTime, SuccessRate
| order by PipelineInfo_IngestionTime asc
```

### 7.2 Volume over time

> Visualization: Time chart, Y-axis: Count

```kql
mv_SKRet_Health
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Total) by PipelineInfo_IngestionTime
| order by PipelineInfo_IngestionTime asc
```

### 7.3 Error Count by Type

> Visualization: Bar chart, X-axis: error_code, Y-axis: Count

```kql
mv_SKRet_Errors
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by error_type, error_code
| order by Count desc
```

### 7.4 Error Rate by Broker Version

> Visualization: Table

```kql
mv_SKRet_Health
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Total = sum(Total), Failures = sum(Failures) by broker_version
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project broker_version, Total, Failures, FailureRate
| order by FailureRate desc
```

### 7.5 Error Rate by App Version

> Visualization: Table

```kql
mv_SKRet_Health
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_active_broker_host) == 0 or active_broker_package_name in (_active_broker_host))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Total = sum(Total), Failures = sum(Failures) by AppInfo_Version
| extend FailureRate = round(100.0 * Failures / Total, 2)
| project AppInfo_Version, Total, Failures, FailureRate
| order by FailureRate desc
```

---

## 8. Dashboard 4: SecretKeyRetrieval — Performance

### 8.1 Elapsed Time Percentiles over time

> Visualization: Time chart, Y-axis: p50/p90/p99

```kql
mv_SKRet_Latency
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| project PipelineInfo_IngestionTime, p50_elapsed, p90_elapsed, p99_elapsed
| order by PipelineInfo_IngestionTime asc
```

### 8.2 Serialization Duration Percentiles over time

> Visualization: Time chart

```kql
mv_SKRet_Latency
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| project PipelineInfo_IngestionTime, p50_serialization, p90_serialization, p99_serialization
| order by PipelineInfo_IngestionTime asc
```

### 8.3 Elapsed Time Histogram

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

### 8.4 Slow Operations (>p99)

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

### 8.5 Latency by OS Version

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

## 9. Dashboard 5: Device & Ecosystem (Shared)

### 9.1 Top Device Models — Generation

> Visualization: Bar chart

```kql
mv_SKGen_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by DeviceInfo_Model
| top 20 by Count
```

### 9.2 Top Device Models — Retrieval

> Visualization: Bar chart

```kql
mv_SKRet_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by DeviceInfo_Model
| top 20 by Count
```

### 9.3 OS Version Distribution — Generation

> Visualization: Pie chart

```kql
mv_SKGen_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by DeviceInfo_OsVersion
| order by DeviceInfo_OsVersion asc
```

### 9.4 OS Version Distribution — Retrieval

> Visualization: Pie chart

```kql
mv_SKRet_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by DeviceInfo_OsVersion
| order by DeviceInfo_OsVersion asc
```

### 9.5 Device Make Distribution — Generation

> Visualization: Pie chart

```kql
mv_SKGen_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by DeviceInfo_Make
| top 15 by Count
```

### 9.6 Device Make Distribution — Retrieval

> Visualization: Pie chart

```kql
mv_SKRet_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by DeviceInfo_Make
| top 15 by Count
```

### 9.7 Network Type Distribution — Generation

> Visualization: Pie chart

```kql
mv_SKGen_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by DeviceInfo_NetworkType
| order by Count desc
```

### 9.8 Broker Version Rollout — Generation

> Visualization: Stacked area chart

```kql
mv_SKGen_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by PipelineInfo_IngestionTime, broker_version
| order by PipelineInfo_IngestionTime asc
```

### 9.9 App Version Rollout — Generation

> Visualization: Stacked area chart

```kql
mv_SKGen_Device
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by PipelineInfo_IngestionTime, AppInfo_Version
| order by PipelineInfo_IngestionTime asc
```

---

## 10. Dashboard 6: Serializer Migration (Phase 1)

### 10.1 Serializer Adoption over time — Generation

> Visualization: Stacked area chart — old serializer (0) vs new serializer ID

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1h), secret_key_wrapping_serializer_id
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| project PipelineInfo_IngestionTime, SerializerId, Count
| order by PipelineInfo_IngestionTime asc
```

### 10.2 Serializer Adoption over time — Retrieval

> Visualization: Stacked area chart

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1h), secret_key_wrapping_serializer_id
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| project PipelineInfo_IngestionTime, SerializerId, Count
| order by PipelineInfo_IngestionTime asc
```

### 10.3 Serializer Adoption % over time — Generation

> Visualization: Line chart — shows % migration progress toward 100% new serializer

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1d), secret_key_wrapping_serializer_id
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| as T
| join kind=inner (T | summarize DayTotal = sum(Count) by PipelineInfo_IngestionTime) on PipelineInfo_IngestionTime
| extend Pct = round(100.0 * Count / DayTotal, 2)
| project PipelineInfo_IngestionTime, SerializerId, Pct
| order by PipelineInfo_IngestionTime asc
```

### 10.4 Serializer Adoption % over time — Retrieval

> Visualization: Line chart

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1d), secret_key_wrapping_serializer_id
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| as T
| join kind=inner (T | summarize DayTotal = sum(Count) by PipelineInfo_IngestionTime) on PipelineInfo_IngestionTime
| extend Pct = round(100.0 * Count / DayTotal, 2)
| project PipelineInfo_IngestionTime, SerializerId, Pct
| order by PipelineInfo_IngestionTime asc
```

### 10.5 Serialization Duration: Old vs New — Generation

> Visualization: Table — side-by-side perf comparison

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize 
    Count = sum(Count),
    avg_p50 = avg(p50_serialization),
    avg_p90 = avg(p90_serialization),
    avg_p99 = avg(p99_serialization)
    by secret_key_wrapping_serializer_id
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| project SerializerId, Count, avg_p50, avg_p90, avg_p99
```

### 10.6 Serialization Duration: Old vs New — Retrieval

> Visualization: Table

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize 
    Count = sum(Count),
    avg_p50 = avg(p50_serialization),
    avg_p90 = avg(p90_serialization),
    avg_p99 = avg(p99_serialization)
    by secret_key_wrapping_serializer_id
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| project SerializerId, Count, avg_p50, avg_p90, avg_p99
```

### 10.7 Serialization Duration over time: Old vs New — Generation

> Visualization: Time chart, split by SerializerId

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| extend SerializerId = strcat("serializer_", tostring(secret_key_wrapping_serializer_id))
| summarize p50 = avg(p50_serialization), p90 = avg(p90_serialization)
    by bin(PipelineInfo_IngestionTime, 1h), SerializerId
| order by PipelineInfo_IngestionTime asc
```

### 10.8 Serialization Duration over time: Old vs New — Retrieval

> Visualization: Time chart, split by SerializerId

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| extend SerializerId = strcat("serializer_", tostring(secret_key_wrapping_serializer_id))
| summarize p50 = avg(p50_serialization), p90 = avg(p90_serialization)
    by bin(PipelineInfo_IngestionTime, 1h), SerializerId
| order by PipelineInfo_IngestionTime asc
```

### 10.9 Error Rate: Old vs New Serializer — Generation

> Visualization: Table — critical to verify new serializer doesn't increase errors

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by secret_key_wrapping_serializer_id
| extend 
    SerializerId = tostring(secret_key_wrapping_serializer_id),
    FailureRate = round(100.0 * Failures / Total, 2)
| project SerializerId, Total, Failures, FailureRate
```

### 10.10 Error Rate: Old vs New Serializer — Retrieval

> Visualization: Table

```kql
fn_SecretKeyRetrieval(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by secret_key_wrapping_serializer_id
| extend 
    SerializerId = tostring(secret_key_wrapping_serializer_id),
    FailureRate = round(100.0 * Failures / Total, 2)
| project SerializerId, Total, Failures, FailureRate
```

### 10.11 New Serializer Rollout by Broker Version — Generation

> Visualization: Stacked bar chart

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by broker_version, secret_key_wrapping_serializer_id
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| project broker_version, SerializerId, Count
| order by broker_version desc
```

### 10.12 New Serializer Rollout by Broker Version — Retrieval

> Visualization: Stacked bar chart

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by broker_version, secret_key_wrapping_serializer_id
| extend SerializerId = tostring(secret_key_wrapping_serializer_id)
| project broker_version, SerializerId, Count
| order by broker_version desc
```

---

## 11. Dashboard 7: Algorithm Migration (Phase 2)

### 11.1 Algorithm Distribution over time — Generation

> Visualization: Stacked area chart — AES vs new algorithms

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1d), secret_key_algorithm
| order by PipelineInfo_IngestionTime asc
```

### 11.2 Algorithm Distribution over time — Retrieval

> Visualization: Stacked area chart

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1d), secret_key_algorithm
| order by PipelineInfo_IngestionTime asc
```

### 11.3 Cipher Transformation Distribution over time — Generation

> Visualization: Stacked area chart — PKCS1 vs OAEP vs new

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1d), elected_cipher_transformation
| order by PipelineInfo_IngestionTime asc
```

### 11.4 Cipher Transformation Distribution over time — Retrieval

> Visualization: Stacked area chart

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1d), elected_cipher_transformation
| order by PipelineInfo_IngestionTime asc
```

### 11.5 Algorithm Adoption % — Generation

> Visualization: Line chart — % migration from old to new algorithm

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1d), secret_key_algorithm
| as T
| join kind=inner (T | summarize DayTotal = sum(Count) by PipelineInfo_IngestionTime) on PipelineInfo_IngestionTime
| extend Pct = round(100.0 * Count / DayTotal, 2)
| project PipelineInfo_IngestionTime, secret_key_algorithm, Pct
| order by PipelineInfo_IngestionTime asc
```

### 11.6 Algorithm Adoption % — Retrieval

> Visualization: Line chart

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize Count = sum(Count) by bin(PipelineInfo_IngestionTime, 1d), secret_key_algorithm
| as T
| join kind=inner (T | summarize DayTotal = sum(Count) by PipelineInfo_IngestionTime) on PipelineInfo_IngestionTime
| extend Pct = round(100.0 * Count / DayTotal, 2)
| project PipelineInfo_IngestionTime, secret_key_algorithm, Pct
| order by PipelineInfo_IngestionTime asc
```

### 11.7 Perf by Algorithm — Generation

> Visualization: Table — perf comparison across algorithm+cipher combos

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize 
    Count = sum(Count),
    avg_p50 = avg(p50_serialization),
    avg_p90 = avg(p90_serialization),
    avg_p99 = avg(p99_serialization)
    by secret_key_algorithm, elected_cipher_transformation
| project secret_key_algorithm, elected_cipher_transformation, Count, avg_p50, avg_p90, avg_p99
| order by Count desc
```

### 11.8 Perf by Algorithm — Retrieval

> Visualization: Table

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| where (array_length(_broker_host_app_version) == 0 or AppInfo_Version in (_broker_host_app_version))
| summarize 
    Count = sum(Count),
    avg_p50 = avg(p50_serialization),
    avg_p90 = avg(p90_serialization),
    avg_p99 = avg(p99_serialization)
    by secret_key_algorithm, elected_cipher_transformation
| project secret_key_algorithm, elected_cipher_transformation, Count, avg_p50, avg_p90, avg_p99
| order by Count desc
```

### 11.9 Error Rate by Algorithm — Generation

> Visualization: Table — verifies new algorithm isn't causing failures

```kql
fn_SecretKeyGeneration(_startTime, _endTime, _broker_versions, _active_broker_host, _broker_host_app_version)
| summarize 
    Total = count(),
    Failures = countif(span_status != 'OK')
    by secret_key_algorithm, elected_cipher_transformation
| extend FailureRate = round(100.0 * Failures / Total, 2)
| order by FailureRate desc
```

### 11.10 Error Rate by Algorithm — Retrieval

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

### 11.11 Device Capability: Available Transformations — Generation

> Visualization: Table — shows what % of devices support new algorithms

```kql
mv_SKGen_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| summarize Count = sum(Count) by available_transformation_list, key_pair_supported_paddings
| order by Count desc
```

### 11.12 Device Capability: Available Transformations — Retrieval

> Visualization: Table

```kql
mv_SKRet_CryptoSerializer
| where PipelineInfo_IngestionTime between (_startTime .. _endTime)
| where (array_length(_broker_versions) == 0 or broker_version in (_broker_versions))
| summarize Count = sum(Count) by available_transformation_list, key_pair_supported_paddings
| order by Count desc
```

---

## Execution Order

### Step 1: Create Stored Functions
Run sections 1.1 and 1.2.

### Step 2: Create Materialized Views
Run sections 2.1–2.5 (Generation) and 3.1–3.5 (Retrieval).  
Wait for backfill to complete before proceeding.

### Step 3: Create ADX Dashboards
Configure dashboard parameters (section 4), then create tiles using queries from sections 5–11.

### Summary

| Component | Count |
|---|---|
| Stored Functions | 2 |
| Materialized Views | 10 |
| Dashboards | 7 |
| Total Query Tiles | 47 |