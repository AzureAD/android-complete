# Search Patterns for Android Auth Repo

Reference for effective search patterns in this multi-repo authentication codebase.

## Table of Contents

1. [Module-Specific Paths](#module-specific-paths)
2. [Authentication Flow Patterns](#authentication-flow-patterns)
3. [Common Search Patterns](#common-search-patterns)
4. [Data Flow Investigation](#data-flow-investigation)
5. [Error & Exception Patterns](#error--exception-patterns)

---

## Module-Specific Paths

### MSAL (Client Library)
```
Base: msal/msal/src/main/java/com/microsoft/identity/client/

Key directories:
- client/                    # Public API classes
- client/internal/           # Internal implementation
- client/exception/          # MSAL exceptions
- client/configuration/      # Configuration classes
```

### Broker (Authentication Service)
```
Base: broker/AADAuthenticator/src/main/java/com/microsoft/identity/broker/

Key directories:
- operation/msal/            # MSAL broker operations
- operation/adal/            # ADAL broker operations  
- operation/brokerapi/       # Broker API operations
- accountmanager/            # Account management
- flighting/                 # Feature flags
```

### Common (Shared + IPC)
```
Base: common/common/src/main/java/com/microsoft/identity/common/

Key directories:
- internal/controllers/      # BrokerMsalController, LocalMSALController
- internal/broker/           # IPC, BrokerResult, adapters
- internal/cache/            # Token caching
- internal/commands/         # Command pattern implementations
- exception/                 # Shared exceptions
```

### ADAL (Legacy)
```
Base: adal/adal/src/main/java/com/microsoft/aad/adal/

Key directories:
- AcquireTokenRequest.java   # Token acquisition
- AuthenticationContext.java # Main entry point
```

---

## Authentication Flow Patterns

### Entry Points (Where requests start)

| Looking for | Search pattern | Module |
|-------------|---------------|--------|
| MSAL public API | `class PublicClientApplication` | MSAL |
| Acquire token | `acquireToken\|AcquireToken` | MSAL, Common |
| Silent flow | `AcquireTokenSilent\|acquireTokenSilent` | All |
| Interactive flow | `AcquireTokenInteractive\|getIntentForInteractive` | All |

### IPC Layer (Common → Broker)

| Looking for | Search pattern | Module |
|-------------|---------------|--------|
| Broker controller | `BrokerMsalController\|BrokerOperationExecutor` | Common |
| IPC strategies | `IIpcStrategy\|BoundServiceStrategy\|AccountManager` | Common |
| Operation bundles | `BrokerOperationBundle` | Common |
| Result adapters | `MsalBrokerResultAdapter\|BrokerResult` | Common |

### Broker Operations (Request handling)

| Looking for | Search pattern | Module |
|-------------|---------------|--------|
| All operations | `file: **/operation/**/*.kt` | Broker |
| Silent operation | `AcquireTokenSilentMsalBrokerOperation` | Broker |
| Interactive operation | `GetIntentForInteractiveRequest` | Broker |
| Account operations | `GetAccountsMsalBrokerOperation` | Broker |

---

## Common Search Patterns

### Finding Classes by Type

```bash
# Controllers
grep: class.*Controller
file: **/controllers/**/*.java

# Operations (Broker)
grep: class.*Operation|IBrokerOperation
file: **/operation/**/*.kt

# Adapters
grep: class.*Adapter|ResultAdapter
file: **/result/**/*.java

# Exceptions
grep: class.*Exception
file: **/exception/**/*.java
```

### Finding Feature Flags

```bash
# Flight checks
grep: isFlightEnabled|CommonFlight|FlightsManager

# Experimentation flags
grep: ExperimentationFeatureFlag

# Feature conditionals
grep: if.*flight|when.*flight
```

### Finding Error Handling

```bash
# Error code definitions
grep: ErrorStrings|error_code|ERROR_CODE

# Exception throwing
grep: throw.*Exception|ClientException|ServiceException

# Error messages
grep: errorMessage|error_message
```

### Finding Logging

```bash
# Logger usage
grep: Logger\.(v|d|i|w|e)|TAG\s*=

# Method tracing
grep: methodName\s*=|methodTag
```

---

## Data Flow Investigation

### Step 1: Find the Data Structure

```bash
# Find class definition
grep: class.*BrokerResult|data class.*Result

# Find field
grep: mRefreshToken|refreshToken|private.*token
```

### Step 2: Find Construction Code

```bash
# Builder usage
grep: BrokerResult\.Builder|\.build\(\)

# Factory methods
grep: create.*Result|build.*Result

# Field assignment
grep: \.refreshToken\(|\.setRefreshToken\(
```

### Step 3: Check Conditional Logic

```bash
# Account type checks
grep: MSA_MEGA_TENANT_ID|accountType.*==|isMsa\(

# Protocol version checks
grep: negotiatedProtocolVersion|PROTOCOL_VERSION|>= 16

# Flight checks
grep: shouldRemove|shouldInclude|isEnabled
```

### Key Adapter Classes

| Class | Purpose | Location |
|-------|---------|----------|
| `MsalBrokerResultAdapter` | Builds BrokerResult from auth results | Common |
| `AdalBrokerResultAdapter` | ADAL version of result adapter | Common |
| `TokenResponseAdapter` | Token response conversion | Common |
| `AccountAdapter` | Account data conversion | MSAL, Common |

---

## Error & Exception Patterns

### Exception Hierarchy

```bash
# Base exceptions
grep: BaseException|ClientException|ServiceException

# UI required
grep: UiRequiredException|MsalUiRequiredException

# Broker exceptions
grep: BrokerCommunicationException
```

### Error Code Constants

```bash
# In Common
file: **/ErrorStrings.java
grep: ERROR_CODE|public static final String

# Error string definitions  
grep: INVALID_GRANT|AUTH_FAILED|DEVICE_
```

### Error Code Usage

```bash
# Throwing with error code
grep: ClientException\(.*ERROR

# Checking error codes
grep: getErrorCode\(\)|errorCode.*==
```

---

## Quick Reference: Multi-Module Search

When searching for a concept, **always check all modules**:

```bash
# Example: Finding "refresh token" handling

# 1. MSAL layer
grep: refreshToken
includePattern: msal/**/*.java

# 2. Common/IPC layer  
grep: refreshToken|mRefreshToken
includePattern: common/**/*.java

# 3. Broker layer
grep: refreshToken
includePattern: broker/**/*.kt

# 4. ADAL layer
grep: refreshToken
includePattern: adal/**/*.java
```

### Cross-Module Tracing

For complete flow tracing:
1. Start at MSAL (`PublicClientApplication`)
2. Follow to Common (`BrokerMsalController`)
3. Trace IPC (`BrokerOperationBundle`)
4. Find Broker handler (`*BrokerOperation.kt`)
5. Check result adapter (`*ResultAdapter`)
6. Trace return path back through Common to MSAL
