# Broker Patterns Lens

This lens checks correctness of brokered authentication designs for the Android Identity Platform.

## Architecture Context

The broker authentication flow follows this path:
```
Client App (MSAL / OneAuth)
    → Common (IPC layer)
    → Broker (ad-accounts-for-android)
    → eSTS
    → Broker
    → Common (IPC response)
    → Client App
```

The **Common** module owns all IPC logic. The **Broker** module owns all token acquisition logic. MSAL and OneAuth are clients that never communicate with eSTS directly in brokered scenarios.

## What to Check

### Broker Detection & Fallback
- Design must describe how the client detects broker availability (AccountManager, content provider)
- Fallback to non-brokered flow must be explicitly defined: when is it allowed, when is it prohibited?
- Broker version compatibility checks must be addressed (older broker may not support new features)
- Feature flags must gate new broker capabilities until the broker is at sufficient adoption

### IPC Contract (AIDL / Bundle-based)
- Any new IPC message must be fully specified: Bundle key names, value types, whether optional or required
- IPC contracts are additive only for backward compatibility — new keys allowed, existing keys must not be removed or renamed
- Bundle keys must follow the established naming convention (check existing `BrokerOperationBundle` / `BrokerRequest` patterns)
- Large payloads (> binder limit ~1MB) must use content provider transfer, not direct binder calls
- All new AIDL interfaces must include a version int for future negotiation

### Session / Account Management
- Multi-account scenarios must be considered: what happens when multiple accounts are present?
- Account removal must be handled: what if the account is removed from the broker mid-flow?
- `AccountManager` interactions must specify which account type is targeted
- SSO token cache sharing rules: can the new feature read/write accounts from other apps in the same broker?

### Security Boundaries
- Client apps must NOT receive raw refresh tokens — broker returns access tokens only (for MSAL-brokered flows)
- Any flow where the broker returns credentials to the calling app must justify and describe the binding mechanism
- Binding between broker and MSAL must use signature verification or AccountManager; intent spoofing must be addressed
- New IPC endpoints exposed by broker must require caller identity validation

### OneAuth-Specific
- OneAuth flows start from `BrokerMsalController` — if the design modifies the broker protocol, impact on `BrokerMsalController` must be analyzed
- OneAuth team must be notified of any breaking IPC contract changes (this team does not own OneAuth)
- Changes to `OneAuthSharedFunctions` require notification to the OneAuth team

### Feature Flags
- New broker capabilities must be gated behind a feature flag until broker adoption is sufficient
- Feature flag name must be specified (ECS/Flight configuration)
- Rollout plan must specify the minimum broker version required and how it's enforced

## Red Flags — Auto-escalate to 🔴

- Design removes or renames existing IPC Bundle keys (backward compatibility break)
- Client app receives raw refresh tokens from broker
- No caller identity validation on new IPC endpoint
- Fallback behavior to non-brokered flow not defined
- Breaking change to `OneAuthSharedFunctions` without OneAuth team notification mention

## Yellow Flags — 🟡 Raise for Discussion

- Broker version compatibility not addressed for new features
- Multi-account edge cases not discussed
- Feature flag absent for new broker capability
- Large payload handling not addressed (approaching binder limit)
- Account removal mid-flow not handled

## Questions to Generate

- If the doc introduces a new IPC message: "What is the full Bundle schema — keys, value types, required vs. optional? How does this degrade on older broker versions?"
- If the doc modifies brokered auth flow: "Is caller identity validated when the broker receives this new IPC message? How is intent spoofing prevented?"
- If the doc affects fallback behavior: "Under what conditions does the client fall back to non-brokered auth? Is that fallback acceptable for this feature?"
- If the doc touches account management: "How does this behave when multiple accounts are present? What happens if the account is removed from the broker mid-flow?"
- If the doc touches OneAuth: "Has the OneAuth team been notified of this IPC contract change?"
