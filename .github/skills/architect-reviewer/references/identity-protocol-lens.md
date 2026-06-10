# Identity Protocol Lens

This lens checks OAuth 2.0, OIDC, and PKCE correctness in the design.

## What to Check

### PKCE
- PKCE required for ALL public clients (code_challenge_method must be S256; `plain` is disallowed)
- `code_verifier` must be generated fresh per authorization request (never reused)
- `code_verifier` must use cryptographically random bytes (min 32 bytes / 256 bits)

### Token Lifetimes
- Access Token: max 1 hour (3600s)
- Refresh Token: max 90 days for non-privileged; shorter for CAE/continuous access evaluation
- ID Token: used for identity only, never as an access credential
- Tokens must NOT be used past their `exp` claim

### Refresh Token Semantics
- Refresh token rotation required on every redemption (old RT invalidated on new RT issuance)
- RT must be stored encrypted at rest (Android Keystore or AccountManager)
- Silent auth must handle RT expiry and prompt user gracefully

### Redirect URI
- Custom scheme redirect URIs require signature/package validation when used in broker flows
- `https://` redirect URIs only for web-based flows
- Wildcard redirect URIs are not permitted

### State / Nonce
- `state` parameter required in authorization requests; validated on response (CSRF protection)
- `nonce` required in OIDC flows; validated against ID token `nonce` claim
- State and nonce must be cryptographically random per request

### Grant Types
- Authorization Code + PKCE is the preferred/required grant for interactive flows
- Implicit grant is forbidden for new designs
- Client Credentials only allowed for confidential clients (service-to-service)
- Resource Owner Password Credentials (ROPC) grant: only allowed if explicitly justified and approved; flag always

### Token Storage
- Tokens must NEVER be stored in SharedPreferences without encryption
- Token cache must use Android Keystore-backed encryption
- Tokens must NEVER be written to logs, even at DEBUG level

### CAE / Continuous Access Evaluation
- Designs affecting token issuance or consumption must address CAE claims challenges
- `xms_cc` claim handling required if the app is CAE-capable

## Red Flags — Auto-escalate to 🔴

- Implicit grant flow proposed for any new feature
- ROPC grant proposed without explicit architectural justification
- Token passed in URL query parameters (visible in logs/referer headers)
- `code_challenge_method=plain` used anywhere
- State or nonce absent from interactive flows
- Token storage in unencrypted SharedPreferences or plain file
- Tokens logged at any log level

## Yellow Flags — 🟡 Raise for Discussion

- Non-standard token lifetime proposed (shorter or longer than defaults without justification)
- RT rotation omitted or described as "optional"
- Missing description of how RT expiry is handled in silent flows
- New grant type introduced without comparison to existing grant types
- CAE not mentioned in a flow that issues or validates access tokens

## Questions to Generate

- If the doc describes token storage: "Where are tokens persisted and what encryption mechanism is used? Is Android Keystore involved?"
- If the doc describes an interactive auth flow: "Is PKCE enforced? Is `state` validated on callback?"
- If the doc introduces new token types or lifetimes: "What is the justification for the proposed token lifetime? How does this interact with CAE?"
- If the doc touches refresh logic: "Is RT rotation enforced on every redemption? What happens when the RT expires mid-session?"
- If the doc involves redirect URIs: "How are redirect URIs validated? Is package/signature validation in place for broker flows?"
