# STRIDE Threat Analysis Guide

## Table of Contents
- [Overview](#overview)
- [Categories](#categories)
- [Common Android Auth Threats](#common-android-auth-threats)
- [Trust Boundary Crossings](#trust-boundary-crossings)

## Overview

STRIDE is a threat classification model. In TMT, threats are auto-generated based on element types and trust boundary crossings. For markdown exports (Mode C), generate threats manually using these patterns.

**When to generate**: Ask the user via askQuestion tool. Include when requested.

## Categories

### Spoofing (S)
Can an attacker impersonate a legitimate entity?

Common mitigations:
- Caller package signature validation
- Certificate pinning
- Mutual TLS

### Tampering (T)
Can an attacker modify data in transit or at rest?

Common mitigations:
- Signed tokens (JWT with session key)
- Integrity checks on IPC payloads
- Encrypted storage

### Repudiation (R)
Can an attacker deny having performed an action?

Common mitigations:
- Audit logging with correlation IDs
- Telemetry spans with timestamps
- Non-repudiation tokens

### Information Disclosure (I)
Can an attacker access confidential data?

Common mitigations:
- Encrypted IPC channels
- Token encryption at rest
- Minimal token lifetime (e.g., 5-min validity)
- Nonce-bound tokens

### Denial of Service (D)
Can an attacker disrupt service availability?

Common mitigations:
- Rate limiting on API endpoints
- Flight gating for gradual rollout
- Input validation and size limits

### Elevation of Privilege (E)
Can an attacker gain unauthorized capabilities?

Common mitigations:
- URL domain allow-lists
- Minimum API level enforcement
- Android permission model (signature-level)

## Common Android Auth Threats

### IPC Channel (App → Broker)
| Category | Threat | Mitigation |
|----------|--------|------------|
| Spoofing | Malicious app impersonates Chrome | Caller signature validation against known hashes |
| Tampering | Modified auth request in transit | AccountManager framework provides OS-level IPC integrity |
| Info Disclosure | Token leakage via IPC | Tokens scoped to caller UID, encrypted in transit |
| Elevation | Unauthorized app requests SSO | Package allow-list, flight gating |

### HTTPS Channel (Device → eSTS)
| Category | Threat | Mitigation |
|----------|--------|------------|
| Spoofing | Man-in-the-middle on TLS | Certificate pinning, system CA validation |
| Tampering | Modified PRT headers | Session-key signed JWT (PoP token) |
| Info Disclosure | PRT theft on network | HTTPS only, nonce-bound tokens, 5-min validity |
| DoS | Token replay attacks | Server-side nonce validation, short token lifetime |

### Trust Boundary Crossings
Threats typically arise at these crossing points:
- **App Sandbox → Broker Sandbox**: IPC via AccountManager
- **Device → Internet**: HTTPS to eSTS
- **User → App**: UI interactions, consent flows

## Trust Boundary Crossings

When generating STRIDE analysis, focus on data flows that cross trust boundaries. Each crossing is a potential attack surface. Data flows within the same boundary are lower risk.
