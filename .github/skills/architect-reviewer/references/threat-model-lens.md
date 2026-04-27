# Threat Model Lens

This lens checks whether the design adequately addresses security threats and attack surfaces for the Android Identity Platform.

## What to Check

### Threat Model Presence (Mandatory)
- The design MUST include a threat model or security analysis section
- Acceptable forms: STRIDE analysis, trust boundary diagram, explicit listing of attack surfaces and mitigations
- The absence of any threat model section is an automatic 🔴

### Trust Boundaries
- Design must identify trust boundaries crossed by the feature (app sandbox → broker sandbox, device → network, MSAL app → Broker)
- Any data crossing a trust boundary must be described with its protection mechanism
- New trust boundaries introduced by the design must be explicitly justified

### STRIDE Analysis — Minimum Coverage Required

| STRIDE Category | What to look for in the design |
|----------------|-------------------------------|
| **Spoofing** | How is caller/callee identity validated? Signature check, AccountManager, certificate pinning? |
| **Tampering** | Is data in transit protected (TLS 1.2+)? Is data at rest encrypted (Android Keystore)? |
| **Repudiation** | Are security-relevant events logged for audit without logging PII/tokens? |
| **Information Disclosure** | Can tokens/credentials leak via logs, intents, content providers, or inter-app channels? |
| **Denial of Service** | Can a malicious app cause auth failures for legitimate users? Rate limiting / retry strategy? |
| **Elevation of Privilege** | Can a low-privilege flow be coerced into issuing tokens for higher-privilege scopes? |

### Common Android-Specific Threats

- **Intent Sniffing**: Implicit intents carrying tokens or auth codes can be intercepted — explicit intents or content providers required
- **Screenshot / Screen Overlay**: Auth UI must be protected against overlay attacks (FLAG_SECURE where appropriate)
- **Exported Activities**: Any Activity handling auth callbacks must validate the incoming intent carefully
- **Content Provider Leakage**: Content providers used for IPC must require broker signature permission, not just `android:exported="false"`
- **Logcat Leakage**: Tokens or authorization codes written to Logcat are visible to any app on a debug/rooted device
- **WebView Token Theft**: If WebView is used, JavaScript injection paths must be considered
- **PRT Theft**: Primary Refresh Token is extremely sensitive — any design touching PRT issuance, storage, or redemption requires extra scrutiny

### Data Classification
- Access Tokens → Sensitive; encrypted at rest, never logged
- Refresh Tokens → Highly Sensitive; encrypted at rest, never transmitted over plain HTTP, never logged, never returned to calling app in brokered flows
- Primary Refresh Tokens (PRT) → Critical; must remain inside Broker process, never surfaced to MSAL/OneAuth
- User PII (UPN, display name) → Sensitive; cannot appear in logs without scrubbing

## Red Flags — Auto-escalate to 🔴

- No threat model or security analysis section in the design
- PRT accessed or transmitted outside the Broker process boundary
- Implicit intent used to carry tokens or authorization codes
- Token or auth code written to Logcat at any log level
- TLS not specified for any new network call
- New content provider or exported Activity without permission requirements described
- New scope or privilege level introduced without Elevation of Privilege analysis

## Yellow Flags — 🟡 Raise for Discussion

- Threat model present but only covers happy-path (no adversarial scenarios)
- STRIDE not explicitly applied but attack surface discussion present — verify coverage is adequate
- Screenshot/overlay attack not discussed for new auth UI surfaces
- Retry/DoS resilience not addressed for new network calls
- Audit logging present but PII scrubbing strategy not described

## Questions to Generate

- If no threat model section: "There is no threat model or security analysis in this design. What is the attack surface and what mitigations are in place?"
- If the design passes tokens across process boundaries: "How are tokens protected when crossing the trust boundary between [component A] and [component B]? What prevents interception or tampering?"
- If the design adds a new IPC endpoint or exported component: "How is caller identity validated on this new endpoint? What prevents a malicious app from invoking it?"
- If the design involves PRT: "What prevents PRT from being surfaced outside the Broker process? What is the blast radius if this component is compromised?"
- If the design involves WebView: "How is JavaScript injection risk mitigated in this WebView usage? Is the WebView URL-allowlisted?"
