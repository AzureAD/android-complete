# STRIDE Threat Analysis Guide

## Table of Contents
- [Overview](#overview)
- [Categories](#categories)
- [Common Android Auth Threats](#common-android-auth-threats)
- [Trust Boundary Crossings](#trust-boundary-crossings)

## Overview

STRIDE is a threat classification model. In TMT, threats are auto-generated based on element types and trust boundary crossings. For markdown exports (Mode C), generate threats manually using these patterns.

**When to generate**: Ask the user via askQuestion tool. Include when requested.

**Important**: These are common mitigation *patterns*. Before listing any mitigation in a threat model, verify it actually exists in the codebase for the feature being modeled. Do not assume a mitigation is present — confirm with code evidence.

## Categories

### Spoofing (S)
Can an attacker impersonate a legitimate entity?

Common mitigations:
- Caller package signature validation
- Certificate pinning
- OS-level caller UID verification (Android Binder)

### Tampering (T)
Can an attacker modify data in transit or at rest?

Common mitigations:
- Signed tokens (JWT with session key / PoP tokens)
- OS-level IPC integrity (Android Binder)
- Encrypted storage at rest

### Repudiation (R)
Can an attacker deny having performed an action?

Common mitigations:
- Audit logging with correlation IDs
- Telemetry spans with timestamps

### Information Disclosure (I)
Can an attacker access confidential data?

Common mitigations:
- OS-level process isolation (Android Binder)
- Token encryption at rest
- Nonce-bound tokens
- Short token lifetime

### Denial of Service (D)
Can an attacker disrupt service availability?

Common mitigations:
- Rate limiting / concurrency limits
- Flight gating for gradual rollout
- Input validation and size limits

### Elevation of Privilege (E)
Can an attacker gain unauthorized capabilities?

Common mitigations:
- Package + signature allow-lists
- Minimum API level enforcement
- Android permission model (signature-level)

## Common Android Auth Threats

### IPC Channel (App → Broker)
| Category | Threat | Mitigation |
|----------|--------|------------|
| Spoofing | Malicious app impersonates allowed caller | Caller package signature validation against known hashes |
| Tampering | Modified auth request in transit | Android Binder provides OS-level IPC integrity |
| Info Disclosure | Token leakage via IPC | Android process isolation via Binder |
| Elevation | Unauthorized app requests SSO | Package allow-list, flight gating |

### HTTPS Channel (Device → eSTS)
| Category | Threat | Mitigation |
|----------|--------|------------|
| Spoofing | Man-in-the-middle on TLS | TLS/HTTPS, system CA validation |
| Tampering | Modified PRT headers | Session-key signed JWT (PoP token) |
| Info Disclosure | PRT theft on network | HTTPS only, nonce-bound tokens, short token lifetime |
| DoS | Token replay attacks | Server-side nonce validation, short token lifetime |

### Trust Boundary Crossings
Threats typically arise at these crossing points:
- **App Sandbox → Broker Sandbox**: IPC via AccountManager/BoundService (Binder)
- **Device → Internet**: HTTPS to eSTS
- **User → App**: UI interactions, consent flows

## Trust Boundary Crossings

When generating STRIDE analysis, focus on data flows that cross trust boundaries. Each crossing is a potential attack surface. Data flows within the same boundary are lower risk.
