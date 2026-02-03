# Kusto Queries for Incident Investigation

Reference queries for investigating Android authentication issues in telemetry.

## Android Broker Telemetry

**Cluster:** `https://idsharedeus2.kusto.windows.net/`
**Database:** `ad-accounts-android-otel`
**Table:** `android_spans`
**Retention:** 30 days

### Find Spans by Correlation ID

```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where correlation_id == "[correlation-id]"
| project EventInfo_Time, span_name, error_code, error_message, span_status
| order by EventInfo_Time asc
```

### Find Errors for Device

```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where DeviceInfo_Id == "[device-id]"
| where isnotempty(error_code)
| summarize count() by error_code, span_name
| order by count_ desc
```

### AcquireTokenSilent Failures

```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenSilent"
| where span_status != "OK"
| summarize 
    count = count(),
    by error_code, calling_package_name
| order by count desc
```

### AcquireTokenInteractive with Errors

```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name == "AcquireTokenInteractive"
| where isnotempty(error_code)
| project EventInfo_Time, correlation_id, error_code, error_message, calling_package_name
| order by EventInfo_Time desc
| take 100
```

### Find Sign-Out Operations

```kql
android_spans
| where EventInfo_Time >= ago(7d)
| where span_name contains "SignOut"
| project EventInfo_Time, span_name, correlation_id, calling_package_name
| order by EventInfo_Time desc
```

## eSTS Telemetry

**Cluster:** `https://estswus2.kusto.windows.net/`
**Database:** `ESTS`
**Table:** `AllPerRequestTable`

### Find Request by Correlation ID

```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where CorrelationId == "[correlation-id]"
| project env_time, CorrelationId, Call, Result, ErrorCode, HttpStatusCode, PrtData, ResponseTime
```

### Check PRT Usage

```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where CorrelationId == "[correlation-id]"
| extend HasPRT = isnotempty(PrtData)
| project env_time, Call, Result, HasPRT, ErrorCode
```

### Error Pattern by Application

```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where ApplicationId == "[app-client-id]"
| where Result != "Success"
| summarize count() by ErrorCode, Call
| order by count_ desc
```

### User/Device Request History

```kql
AllPerRequestTable
| where env_time >= ago(7d)
| where DevicePlatformForUI == "Android"
| where DeviceId == "[device-id]"
| project env_time, Call, Result, ErrorCode, ApplicationId
| order by env_time desc
| take 100
```

## Common Client IDs

| App | Client ID |
|-----|-----------|
| Outlook | `27922004-5251-4030-b22d-91ecd9a37ea4` |
| Teams | `1fec8e78-bce4-4aaf-ab1b-5451cc387264` |
| Authenticator | `4813382a-8fa7-425e-ab75-3b753aab3abb` |
| Company Portal | `0000000a-0000-0000-c000-000000000000` |

## Correlating Broker + eSTS

1. Get correlation ID from broker logs
2. Query `android_spans` for broker-side view
3. Query `AllPerRequestTable` for eSTS-side view
4. Match timestamps and operations

```kql
// Step 1: Broker side
android_spans
| where correlation_id == "[id]"
| project EventInfo_Time, span_name, error_code

// Step 2: eSTS side  
AllPerRequestTable
| where CorrelationId == "[id]"
| project env_time, Call, Result, ErrorCode
```
