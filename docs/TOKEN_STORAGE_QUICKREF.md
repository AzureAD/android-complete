# Token Storage Quick Reference

> For comprehensive details, see [TOKEN_STORAGE_DOCUMENTATION.md](TOKEN_STORAGE_DOCUMENTATION.md)

## Quick Summary

### Where are tokens stored?
**Android SharedPreferences** at:
```
/data/data/{package_name}/shared_prefs/com.microsoft.identity.client.account_credential_cache.xml
```

### What gets stored?
- **AccountRecord**: User identity (username, IDs, name, etc.)
- **AccessTokenRecord**: Access tokens with expiration, scopes, etc.
- **RefreshTokenRecord**: Refresh tokens (MRRT/FRT)
- **IdTokenRecord**: ID tokens with user claims

### Storage Flow (Simplified)
```
BaseController.saveTokens()
    ↓
MsalOAuth2TokenCache.saveAndLoadAggregatedAccountData()
    ↓
SharedPreferencesAccountCredentialCache.saveAccount/saveCredential()
    ↓
SharedPreferencesFileManager.put() [with encryption]
    ↓
Android SharedPreferences (encrypted XML file)
```

### Cache Key Examples

**Account Key:**
```
{homeAccountId}-{environment}-{realm}
```

**Access Token Key:**
```
{homeAccountId}-{environment}-accesstoken-{clientId}-{realm}-{scopes}
```

**Refresh Token Key:**
```
{homeAccountId}-{environment}-refreshtoken-{clientId}--
```

**ID Token Key:**
```
{homeAccountId}-{environment}-idtoken-{clientId}-{realm}-
```

### Security
- ✅ Values are **encrypted** using Android KeyStore
- ✅ Keys are **hardware-backed** on supported devices  
- ✅ Files are **app-private** (MODE_PRIVATE)
- ✅ **In-memory LRU cache** (256 entries) for performance
- ✅ Broker mode uses **UID-sequestered** caches per app

### Key Code Locations
- Entry point: `BaseController.saveTokens()` (~line 903)
- Cache logic: `MsalOAuth2TokenCache.java`
- Key generation: `CacheKeyValueDelegate.java`
- Storage: `SharedPreferencesFileManager.java`
- DTOs: `dto/` package (AccountRecord, AccessTokenRecord, etc.)

*Note: Line numbers are approximate and may change as code evolves*

### Special Files
- **FOCI Cache**: `...cache.foci-1` (Family Refresh Tokens - FOCI = Family of Client IDs)
- **UID Cache**: `...cache.uid-{uid}` (Per-app broker cache)

---

📖 **Full Documentation**: See [TOKEN_STORAGE_DOCUMENTATION.md](TOKEN_STORAGE_DOCUMENTATION.md) for comprehensive details
