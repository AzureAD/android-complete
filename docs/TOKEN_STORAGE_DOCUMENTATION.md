# Token Storage Documentation

## Overview
This document provides comprehensive details about how authentication tokens are stored in the Microsoft Authentication Library (MSAL) for Android. It covers the complete flow from when `saveTokens` is triggered to the final storage location, including what data is saved, where it's saved, and the cache structure used.

---

## Table of Contents
1. [Token Storage Flow](#token-storage-flow)
2. [Entry Point: saveTokens Method](#entry-point-savetokens-method)
3. [What Data Gets Saved](#what-data-gets-saved)
4. [Where Tokens Are Stored](#where-tokens-are-stored)
5. [Cache Structure](#cache-structure)
6. [Cache Key Generation](#cache-key-generation)
7. [Cache Value Serialization](#cache-value-serialization)
8. [Security Considerations](#security-considerations)
9. [Code References](#code-references)

---

## Token Storage Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. BaseController.saveTokens()                                          │
│    - Called after successful token acquisition                          │
│    - Location: common/common4j/.../controllers/BaseController.java      │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. MsalOAuth2TokenCache.saveAndLoadAggregatedAccountData()             │
│    - Orchestrates the save operation                                    │
│    - Merges with other tenant cache records                             │
│    - Location: common/common4j/.../cache/MsalOAuth2TokenCache.java     │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. MsalOAuth2TokenCache.save()                                         │
│    - Validates schema compliance of tokens                              │
│    - Calls saveAccounts() and saveCredentialsInternal()                │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. SharedPreferencesAccountCredentialCache.saveAccount()               │
│    SharedPreferencesAccountCredentialCache.saveCredential()            │
│    - Generates cache keys using CacheKeyValueDelegate                   │
│    - Serializes values to JSON                                          │
│    - Merges additional fields from existing records                     │
│    - Location: common/common4j/.../cache/                               │
│              SharedPreferencesAccountCredentialCache.java               │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. SharedPreferencesFileManager.put()                                  │
│    - Encrypts values (if encryption manager is configured)              │
│    - Stores in Android SharedPreferences                                │
│    - Maintains in-memory LRU cache (256 entries)                        │
│    - Location: common/common/src/.../internal/cache/                    │
│              SharedPreferencesFileManager.java                          │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 6. Android SharedPreferences (Disk Storage)                            │
│    - File: com.microsoft.identity.client.account_credential_cache      │
│    - Location: /data/data/{package_name}/shared_prefs/                 │
│    - Format: XML file with encrypted key-value pairs                    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Entry Point: saveTokens Method

### Location
`common/common4j/src/main/com/microsoft/identity/common/java/controllers/BaseController.java`

### Method Signature
```java
protected List<ICacheRecord> saveTokens(
    @NonNull final OAuth2Strategy strategy,
    @NonNull final AuthorizationRequest request,
    @NonNull final TokenResponse tokenResponse,
    @NonNull final OAuth2TokenCache tokenCache
) throws ClientException
```

### When It's Called
This method is invoked after a successful token acquisition, typically in the following scenarios:
- Interactive token acquisition (`acquireToken`)
- Silent token acquisition with a refresh token (`acquireTokenSilent`)
- Device code flow token acquisition
- ROPC (Resource Owner Password Credentials) flow

### What It Does
1. Logs the token saving operation
2. Delegates to `tokenCache.saveAndLoadAggregatedAccountData()`
3. Returns a list of `ICacheRecord` objects containing saved tokens

---

## What Data Gets Saved

The token cache stores the following types of records, organized in a `CacheRecord` object:

### 1. AccountRecord
**Purpose**: Stores user account information

**Fields**:
- `home_account_id`: Unique identifier across tenants (`<uid>.<utid>`)
- `environment`: Token issuer host (e.g., `login.microsoftonline.com`)
- `realm`: Tenant/organizational identifier (tenant ID for AAD)
- `local_account_id`: User's object ID within a tenant
- `username`: User's UPN or email
- `authority_type`: Type of authority (e.g., `MSSTS`, `B2C`)
- `alternative_account_id`: Optional alternative identifier
- `first_name`, `family_name`, `middle_name`: User's name components
- `name`: Full display name
- `avatar_url`: User's profile picture URL
- `client_info`: Base64 encoded client info from token response
- `additional_fields`: Map for extensibility

**Location**: `common/common4j/src/main/com/microsoft/identity/common/java/dto/AccountRecord.java`

---

### 2. AccessTokenRecord
**Purpose**: Stores access tokens for accessing protected resources

**Fields**:
- **Inherited from Credential**:
  - `home_account_id`: Links to AccountRecord
  - `environment`: Token issuer
  - `client_id`: Application's client ID
  - `credential_type`: Always `"AccessToken"`
  - `secret`: The actual access token (JWT or opaque string)
  - `cached_at`: Timestamp when token was cached (epoch milliseconds)
  - `expires_on`: Token expiration time (epoch seconds)

- **Access Token Specific**:
  - `access_token_type` / `token_type`: Token type (e.g., `Bearer`, `pop`)
  - `authority`: Full authority URL
  - `extended_expires_on`: Extended expiration for offline scenarios
  - `realm`: Tenant ID
  - `target`: Requested scopes/resources (space-delimited)
  - `kid`: Key ID for PoP (Proof of Possession) tokens
  - `requested_claims`: Claims request string that produced this token
  - `refresh_on`: Proactive refresh time (based on `refresh_in` from response)
  - `application_identifier`: Package name and signature (for True MAM scenarios)
  - `mam_enrollment_identifier`: Intune MAM enrollment ID

**Location**: `common/common4j/src/main/com/microsoft/identity/common/java/dto/AccessTokenRecord.java`

---

### 3. RefreshTokenRecord
**Purpose**: Stores refresh tokens for obtaining new access tokens

**Fields**:
- **Inherited from Credential**:
  - `home_account_id`
  - `environment`
  - `client_id`
  - `credential_type`: `"RefreshToken"`
  - `secret`: The actual refresh token
  - `cached_at`

- **Refresh Token Specific**:
  - `family_id`: Family ID for Family Refresh Tokens (FRT/FOCI)
  - `target`: Scopes/resources (optional, usually empty for multi-resource tokens)

**Special Handling**:
- **Multi-Resource Refresh Tokens (MRRT)**: For AAD v1/v2, one refresh token can be used for all resources
- **Family Refresh Tokens (FRT/FOCI)**: First-party apps in the same "family" can share refresh tokens
- When saving a new refresh token, old refresh tokens for the same account are automatically removed

**Location**: `common/common4j/src/main/com/microsoft/identity/common/java/dto/RefreshTokenRecord.java`

---

### 4. IdTokenRecord
**Purpose**: Stores ID tokens containing user claims

**Fields**:
- **Inherited from Credential**:
  - `home_account_id`
  - `environment`
  - `client_id`
  - `credential_type`: `"IdToken"` (v2) or `"V1IdToken"` (v1)
  - `secret`: The actual ID token (JWT)
  - `cached_at`

- **ID Token Specific**:
  - `realm`: Tenant ID
  - Additional fields parsed from the JWT claims

**Location**: `common/common4j/src/main/com/microsoft/identity/common/java/dto/IdTokenRecord.java`

---

### 5. V1IdTokenRecord
**Purpose**: Stores ADAL (v1) format ID tokens for backward compatibility

Same structure as IdTokenRecord but with `credential_type` = `"V1IdToken"`

---

## Where Tokens Are Stored

### Primary Storage: Android SharedPreferences

**File Name**: `com.microsoft.identity.client.account_credential_cache`

**File Location**: 
```
/data/data/{package_name}/shared_prefs/com.microsoft.identity.client.account_credential_cache.xml
```

**Access Mode**: `Context.MODE_PRIVATE` (only accessible by the app)

### Special Storage Files

#### 1. Broker FOCI Cache (Family Refresh Tokens)
**File Name**: `com.microsoft.identity.client.account_credential_cache.foci-1`

**Purpose**: Separate cache for Family Refresh Tokens used by the Broker

**Location**: Same shared_prefs directory

#### 2. Broker UID-Sequestered Cache
**File Name Pattern**: `com.microsoft.identity.client.account_credential_cache.uid-{uid}`

**Purpose**: Per-application caches when running in broker mode

**Example**: `com.microsoft.identity.client.account_credential_cache.uid-10123`

### Storage Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│ In-Memory Layer: LruCache (256 entries)                    │
│ - Fast access for frequently used tokens                    │
│ - Automatic eviction of least recently used entries         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ Encryption Layer: KeyAccessorStringAdapter                  │
│ - Encrypts values before persisting                         │
│ - Decrypts values when reading                              │
│ - Uses Android KeyStore for key management                  │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ Persistence Layer: Android SharedPreferences                │
│ - XML-based file storage                                    │
│ - Atomic operations via SharedPreferences.Editor            │
│ - Automatic file system synchronization                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Cache Structure

### Cache Entry Format

Each cache entry consists of:
1. **Cache Key**: Composite key identifying the entry uniquely
2. **Cache Value**: JSON-serialized object containing the token/account data

### Cache Organization

The cache is a flat key-value store where:
- **Accounts** are indexed by `home_account_id-environment-realm`
- **Credentials** are indexed by multiple components including credential type, client ID, and scopes

### Data Flow

```
Token Response (from eSTS)
    │
    ├─> AccountRecord ──> Cache Key ──> JSON Value ──> Encrypt ──> SharedPreferences
    │
    ├─> AccessTokenRecord ──> Cache Key ──> JSON Value ──> Encrypt ──> SharedPreferences
    │
    ├─> RefreshTokenRecord ──> Cache Key ──> JSON Value ──> Encrypt ──> SharedPreferences
    │
    └─> IdTokenRecord ──> Cache Key ──> JSON Value ──> Encrypt ──> SharedPreferences
```

---

## Cache Key Generation

Cache keys are generated by `CacheKeyValueDelegate` to ensure uniqueness and efficient lookup.

**Location**: `common/common4j/src/main/com/microsoft/identity/common/java/cache/CacheKeyValueDelegate.java`

### Account Cache Key Format

```
<home_account_id>-<environment>-<realm>
```

**Example**:
```
a1b2c3d4-e5f6-7890-abcd-ef1234567890.12345678-9abc-def0-1234-56789abcdef0-login.microsoftonline.com-12345678-9abc-def0-1234-56789abcdef0
```

**Components**:
- `home_account_id`: User's unique identifier (UID.UTID)
- `environment`: Authority host (e.g., `login.microsoftonline.com`)
- `realm`: Tenant ID

---

### Credential Cache Key Format

Base format (all credentials):
```
<home_account_id>-<environment>-<credential_type>-<client_id>-<realm>-<target>
```

#### Access Token Key

```
<home_account_id>-<environment>-accesstoken-<client_id>-<realm>-<target>[-<application_identifier>][-<mam_enrollment_identifier>][-<auth_scheme>][-<requested_claims_hash>]
```

**Example**:
```
a1b2c3d4-e5f6-7890-abcd-ef1234567890.12345678-9abc-def0-1234-56789abcdef0-login.microsoftonline.com-accesstoken-12345678-90ab-cdef-1234-567890abcdef-12345678-9abc-def0-1234-56789abcdef0-user.read mail.read
```

**Optional Suffixes**:
- `application_identifier`: Added for True MAM scenarios (package name + signature)
- `mam_enrollment_identifier`: Added when app is enrolled in Intune
- `auth_scheme`: Added for PoP tokens (e.g., `-pop`)
- `requested_claims_hash`: Hash of the requested claims string

---

#### Refresh Token Key

```
<home_account_id>-<environment>-refreshtoken-<client_id_or_family_id>--<target>
```

**Notes**:
- Realm component is **empty** (double dash: `--`)
- For Family Refresh Tokens, `client_id` is replaced with `family_id` (e.g., `1`)
- Target is usually empty for multi-resource tokens

**Example (MRRT)**:
```
a1b2c3d4-e5f6-7890-abcd-ef1234567890.12345678-9abc-def0-1234-56789abcdef0-login.microsoftonline.com-refreshtoken-12345678-90ab-cdef-1234-567890abcdef--
```

**Example (FRT/FOCI)**:
```
a1b2c3d4-e5f6-7890-abcd-ef1234567890.12345678-9abc-def0-1234-56789abcdef0-login.microsoftonline.com-refreshtoken-1--
```

---

#### ID Token Key

```
<home_account_id>-<environment>-idtoken-<client_id>-<realm>-
```

**Notes**:
- Target component is **empty** (trailing dash)

**Example**:
```
a1b2c3d4-e5f6-7890-abcd-ef1234567890.12345678-9abc-def0-1234-56789abcdef0-login.microsoftonline.com-idtoken-12345678-90ab-cdef-1234-567890abcdef-12345678-9abc-def0-1234-56789abcdef0-
```

---

### Key Generation Rules

1. **Case Normalization**: All components are converted to lowercase
2. **Null Sanitization**: Null values are converted to empty strings
3. **Trimming**: Whitespace is trimmed from all components
4. **Delimiter**: Components are separated by `-` (hyphen)
5. **Claims Hashing**: Requested claims strings are hashed to avoid delimiter conflicts

---

## Cache Value Serialization

Cache values are JSON-serialized using Gson.

**Location**: `common/common4j/src/main/com/microsoft/identity/common/java/cache/CacheKeyValueDelegate.java`

### Serialization Process

1. **Convert to JSON**: Object is serialized to JSON using Gson
2. **Merge Additional Fields**: Any fields in the `additional_fields` map are added to the JSON
3. **String Representation**: Final JSON is stored as a string

### Example: AccessTokenRecord JSON

```json
{
  "home_account_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890.12345678-9abc-def0-1234-56789abcdef0",
  "environment": "login.microsoftonline.com",
  "credential_type": "AccessToken",
  "client_id": "12345678-90ab-cdef-1234-567890abcdef",
  "secret": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIs...",
  "cached_at": "1701234567890",
  "expires_on": "1701238167",
  "extended_expires_on": "1701241767",
  "realm": "12345678-9abc-def0-1234-56789abcdef0",
  "target": "user.read mail.read",
  "token_type": "Bearer",
  "authority": "https://login.microsoftonline.com/12345678-9abc-def0-1234-56789abcdef0/",
  "refresh_on": "1701237267"
}
```

### Example: AccountRecord JSON

```json
{
  "home_account_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890.12345678-9abc-def0-1234-56789abcdef0",
  "environment": "login.microsoftonline.com",
  "realm": "12345678-9abc-def0-1234-56789abcdef0",
  "local_account_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "username": "user@contoso.com",
  "authority_type": "MSSTS",
  "name": "John Doe",
  "first_name": "John",
  "family_name": "Doe",
  "client_info": "eyJ1aWQiOiJhMWIyYzNkNC1lNWY2LTc4OTAtYWJjZC1lZjEyMzQ1Njc4OTAiLCJ1dGlkIjoiMTIzNDU2Nzg5YWJjZGVmMDEyMzQ1Njc4OTBhYmNkZWYifQ=="
}
```

---

## Security Considerations

### Encryption

**Encryption Manager**: `KeyAccessorStringAdapter`

**Implementation**: `common/common/src/main/java/com/microsoft/identity/common/internal/cache/SharedPreferencesFileManager.java`

#### Encryption Flow

```java
// Encryption (on write)
if (encryptionManager != null) {
    encryptedValue = encryptionManager.encrypt(value);
    editor.putString(key, encryptedValue).apply();
}

// Decryption (on read)
if (encryptionManager != null) {
    decryptedValue = encryptionManager.decrypt(storedValue);
    return decryptedValue;
}
```

#### What Gets Encrypted

- **Values Only**: Cache values (JSON strings) are encrypted
- **Keys**: Cache keys are **NOT** encrypted (used for indexing)
- **Null/Empty Values**: Not encrypted, stored as-is

#### Encryption Key Storage

- Keys are managed by Android KeyStore
- Hardware-backed encryption on supported devices
- Keys are bound to the application and cannot be exported

---

### Access Protection

1. **App-Level Isolation**:
   - SharedPreferences use `Context.MODE_PRIVATE`
   - Files are only accessible by the owning app
   - Linux file permissions: `rw-------` (600)

2. **UID Sequestration** (Broker Mode):
   - Each app gets its own cache file
   - Prevents cross-app token access
   - Format: `cache.uid-{application_uid}`

3. **Root Protection**:
   - Tokens are encrypted at rest
   - Even with root access, encryption keys in KeyStore are protected
   - SafetyNet/Play Integrity can detect rooted devices

---

### Token Lifecycle Security

1. **Automatic Cleanup**:
   - Old refresh tokens are removed when new ones are saved
   - Prevents accumulation of stale tokens

2. **Schema Validation**:
   - All tokens are validated before storage
   - Schema-non-compliant tokens are rejected

3. **Field Merging**:
   - Additional fields from existing tokens are preserved
   - Prevents data loss during updates

4. **Atomic Operations**:
   - SharedPreferences.Editor uses atomic apply()
   - Ensures consistency even if app crashes during write

---

## Code References

### Key Classes and Locations

#### Controllers
- **BaseController**: `common/common4j/src/main/com/microsoft/identity/common/java/controllers/BaseController.java`
  - `saveTokens()` method (line 903)

#### Cache Implementation
- **MsalOAuth2TokenCache**: `common/common4j/src/main/com/microsoft/identity/common/java/cache/MsalOAuth2TokenCache.java`
  - `saveAndLoadAggregatedAccountData()` (line 521)
  - `save()` methods (lines 206, 258)
  - `saveAccounts()` (line 1658)
  - `saveCredentialsInternal()` (line 1664)

- **SharedPreferencesAccountCredentialCache**: `common/common4j/src/main/com/microsoft/identity/common/java/cache/SharedPreferencesAccountCredentialCache.java`
  - `saveAccount()` (line 102)
  - `saveCredential()` (line 120)

- **CacheKeyValueDelegate**: `common/common4j/src/main/com/microsoft/identity/common/java/cache/CacheKeyValueDelegate.java`
  - `generateCacheKey()` for accounts (line 102)
  - `generateCacheKey()` for credentials (line 142)
  - `generateCacheValue()` (lines 134, 212)

- **SharedPreferencesFileManager**: `common/common/src/main/java/com/microsoft/identity/common/internal/cache/SharedPreferencesFileManager.java`
  - `putString()` with encryption (line 136)
  - `getString()` with decryption (line 177)

#### Data Transfer Objects (DTOs)
- **CacheRecord**: `common/common4j/src/main/com/microsoft/identity/common/java/cache/CacheRecord.java`
- **AccountRecord**: `common/common4j/src/main/com/microsoft/identity/common/java/dto/AccountRecord.java`
- **AccessTokenRecord**: `common/common4j/src/main/com/microsoft/identity/common/java/dto/AccessTokenRecord.java`
- **RefreshTokenRecord**: `common/common4j/src/main/com/microsoft/identity/common/java/dto/RefreshTokenRecord.java`
- **IdTokenRecord**: `common/common4j/src/main/com/microsoft/identity/common/java/dto/IdTokenRecord.java`
- **Credential** (base class): `common/common4j/src/main/com/microsoft/identity/common/java/dto/Credential.java`

#### Interfaces
- **IAccountCredentialCache**: `common/common4j/src/main/com/microsoft/identity/common/java/cache/IAccountCredentialCache.java`
- **INameValueStorage**: `common/common4j/src/main/com/microsoft/identity/common/java/interfaces/INameValueStorage.java`

---

## Summary

### Token Storage Process

1. **Trigger**: `BaseController.saveTokens()` is called after token acquisition
2. **Orchestration**: `MsalOAuth2TokenCache` coordinates the save operation
3. **Validation**: Token schema is validated before storage
4. **Key Generation**: Unique cache keys are generated based on token attributes
5. **Serialization**: Tokens are converted to JSON strings
6. **Encryption**: JSON values are encrypted using Android KeyStore
7. **Persistence**: Encrypted data is saved to SharedPreferences XML file
8. **Caching**: Recently used tokens are kept in an in-memory LRU cache

### Storage Location

**Primary File**: `/data/data/{package_name}/shared_prefs/com.microsoft.identity.client.account_credential_cache.xml`

**Format**: Encrypted key-value pairs in XML

**Security**: App-private, encrypted, hardware-backed on supported devices

### Cache Structure

**Key Format**: Composite keys with delimited components
- Accounts: `homeAccountId-environment-realm`
- Credentials: `homeAccountId-environment-credentialType-clientId-realm-target[optional-components]`

**Value Format**: Encrypted JSON-serialized objects

### Data Saved

- **AccountRecord**: User identity information
- **AccessTokenRecord**: Short-lived access tokens with metadata
- **RefreshTokenRecord**: Long-lived refresh tokens (MRRT/FRT)
- **IdTokenRecord**: ID tokens with user claims

---

## Additional Notes

### Multi-Tenant Support

The cache supports multiple tenants for the same user:
- Each tenant gets a separate AccountRecord (different realm)
- Tokens are scoped to specific tenants via the realm field
- `mergeCacheRecordWithOtherTenantCacheRecords()` aggregates cross-tenant data

### Broker Support

When running with a broker (Company Portal, Authenticator):
- Tokens are stored in the broker's cache
- UID-sequestered files prevent cross-app access
- FOCI cache enables family app token sharing

### Cache Maintenance

- **Cleanup**: Old tokens are automatically removed when new ones are saved
- **Deduplication**: Multiple access tokens for the same scope replace each other
- **Memory Cache**: LRU cache (256 entries) reduces disk I/O
- **Atomic Operations**: Ensures data consistency

---

## Diagrams

### Component Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        Application Layer                        │
│  (MSAL PublicClientApplication, acquireToken methods)           │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                      Controller Layer                           │
│         (BaseController, LocalMSALController)                   │
│                   saveTokens() entry point                      │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                    Token Cache Layer                            │
│      (MsalOAuth2TokenCache, OAuth2TokenCache)                  │
│  - Schema validation                                            │
│  - Multi-tenant aggregation                                     │
│  - Credential lifecycle management                              │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│              Account/Credential Cache Layer                     │
│    (SharedPreferencesAccountCredentialCache)                   │
│  - Key generation (CacheKeyValueDelegate)                       │
│  - JSON serialization                                           │
│  - Field merging                                                │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                  Storage Manager Layer                          │
│           (SharedPreferencesFileManager)                        │
│  - In-memory LRU cache                                          │
│  - Encryption/Decryption                                        │
│  - SharedPreferences I/O                                        │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│                   Android Platform Layer                        │
│  - SharedPreferences (XML file)                                 │
│  - KeyStore (encryption keys)                                   │
│  - File system (MODE_PRIVATE)                                   │
└────────────────────────────────────────────────────────────────┘
```

---

**Document Version**: 1.0  
**Last Updated**: December 2024  
**Applicable to**: MSAL Android, Common Library
