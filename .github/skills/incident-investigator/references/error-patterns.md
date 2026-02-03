# Common Error Codes and Patterns

Reference for common authentication error codes encountered in Android Broker/MSAL.

## Broker Error Codes

| Error Code | Description | Common Cause | TSG |
|------------|-------------|--------------|-----|
| `auth_cancelled_by_sdk` | SDK cancelled the authentication | CA policy, interrupt flow, timeout | Check for interrupt flow, CA requirements |
| `device_needs_to_be_managed` | Device management required | Intune enrollment required | Verify device compliance |
| `no_account` | No account found in cache | Account removed or never signed in | Check for sign-out operations |
| `invalid_grant` | Token grant is invalid | PRT revoked, password changed | Re-authenticate interactively |
| `interaction_required` | User interaction needed | MFA, consent, password expired | Trigger interactive auth |

## PRT-Related Issues

### PRT is Null or Missing

**Log Pattern:**
```
No PRT present for the account
```

**Possible Causes:**
1. User signed out (check for `SignOutFromSharedDeviceMsalBrokerOperation`)
2. PRT expired (14-day sliding window)
3. Password changed / account revoked
4. MDM policy enforcement
5. Device registration lost

**Investigation Steps:**
1. Look for sign-out operations in logs
2. Check eSTS for PRT revocation
3. Verify device is still WPJ'd (`Loading Workplace Join entry`)

### PRT Revocation by eSTS

Check eSTS with:
```kql
AllPerRequestTable
| where CorrelationId == "[id]"
| where Call contains "prt"
| project env_time, Result, ErrorCode, SubErrorCode
```

## MDM-Related Issues

### Account Type Disabled by MDM

**Log Pattern:**
```
Account type com.microsoft.authapppassthroughbackup is disabled by MDM
```

**Meaning:** MDM has disabled the passthrough backup account type. This is informational and may not directly cause auth failures, but indicates MDM is actively managing the device.

### MDM Sign-Out in SDM

**Log Pattern:**
```
SignOutFromSharedDeviceMsalBrokerOperation is invoked for package name: [mdm-package]
```

**Impact:** MDM is triggering global sign-out, which removes all accounts and PRTs.

## Account State Issues

### Home Account ID Missing UID

**Log Pattern:**
```
Home Account id doesn't have uid or tenant id information, returning null
```

**Meaning:** Account record is incomplete. May indicate:
- Corrupted account state
- Partial sign-in that didn't complete
- Migration issue from older broker version

### Multiple Account Entries

**Log Pattern:**
```
Found more than one account entry for user in appSpecificRecords
```

**Meaning:** Duplicate account records exist. May cause inconsistent behavior.

## SDM-Specific Issues

### SDM Sign-Out Timing

In Shared Device Mode, any app can trigger global sign-out. Common pattern:
1. User signs into Outlook
2. MDM app immediately triggers `SignOutFromSharedDeviceMsalBrokerOperation`
3. All accounts/PRTs are wiped
4. User appears signed out

**Investigation:** Look for sign-out operations from non-Microsoft packages.

### SDM Account Count

**Log Pattern:**
```
Found [0] Accounts...
```

In SDM, there should be 0 (signed out) or 1 (signed in) account. Multiple accounts suggest misconfiguration.

## eSTS Error Codes

Common eSTS errors visible in broker logs or eSTS telemetry:

| Error | SubError | Meaning |
|-------|----------|---------|
| `invalid_grant` | `bad_token` | Token is invalid/revoked |
| `invalid_grant` | `token_expired` | Token has expired |
| `interaction_required` | `consent_required` | User needs to consent |
| `interaction_required` | `login_required` | Fresh login needed |
| `access_denied` | `policy_violation` | CA policy blocked access |
