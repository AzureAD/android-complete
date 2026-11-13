# Telemetry & Compliance Lens

This lens checks for required telemetry instrumentation, PII handling, and compliance requirements in Android Identity Platform design docs.

## What to Check

### Required Telemetry Coverage
- Every new auth flow or operation must emit a telemetry span covering: start, success, and failure
- Failure paths must emit distinct error codes / sub-error codes (not just a generic failure signal)
- New flows must specify: span name, relevant attributes/dimensions, and success/failure signal names
- Existing telemetry spans being modified must document what changes and backward compatibility of dashboards/alerts

### Span & Signal Naming
- Span names must follow the established pattern for the repo (check existing spans in the codebase)
- Error codes must be unique and searchable in Kusto (`android_spans` table)
- New error codes must not collide with existing ones — verify against the existing error code registry

### PII Handling (Required)
- User identifiers (UPN, email, OID, TID) must be scrubbed or hashed before being emitted in telemetry
- Device identifiers must follow the platform's PII guidelines (device ID hashing)
- `Logger` class (not `android.util.Log`) must be used for all logging
- Sensitive values (tokens, passwords, certificate private keys) must NEVER appear in telemetry or logs
- Design must explicitly state which attributes in new telemetry events are PII and how they're handled

### Compliance Gates
- **GDPR**: Any new telemetry data collected from EU users must be assessed for GDPR relevance — consent model, data retention period
- **FedRAMP / GCC High**: Features targeting government cloud (GCC, GCC High, DoD) must not emit data to non-FedRAMP-approved endpoints
- **SOC2**: Security-relevant events (auth success, auth failure, token issuance, account removal) must be auditable

### Supportability
- New features must emit enough telemetry to diagnose customer-reported issues without requiring a debug build
- Error paths must be diagnosable from Kusto: correlate a customer incident → find the span → identify root cause
- Silent failure modes (places where the feature fails but emits no signal) are a supportability gap → 🟡

### Alerting
- High-impact new flows should have a corresponding alert on failure rate spike
- If the design introduces a new critical path (e.g., new broker IPC call in the auth critical path), a latency SLA should be defined

## Red Flags — Auto-escalate to 🔴

- No telemetry section in the design for a new user-facing feature
- PII (UPN, email, device ID) emitted in raw form in telemetry events
- Tokens or credentials present in any log or telemetry event
- `android.util.Log` used instead of the team's `Logger` class
- New flow that is completely unobservable (no success/failure signal)

## Yellow Flags — 🟡 Raise for Discussion

- Telemetry section present but failure paths not covered (only success signal described)
- Error codes not specified (generic failure signal only)
- PII handling acknowledged but hashing/scrubbing mechanism not described
- No alerting proposed for high-impact new flows
- New government cloud scenario without FedRAMP/GCC compliance analysis
- Supportability story not discussed (how would on-call diagnose a customer issue with this feature?)

## Questions to Generate

- If the doc introduces a new auth flow: "What telemetry spans are emitted? What are the success and failure signals? Are error codes specific enough to diagnose customer issues from Kusto?"
- If telemetry attributes include user data: "Which attributes in the new telemetry events contain PII? How are they scrubbed or hashed before emission?"
- If the design modifies an existing telemetry schema: "Do changes to telemetry attributes break existing Kusto queries or dashboards? Is there a migration plan?"
- If the design adds a new critical path: "Is there a latency SLA for this new path? What alert will fire if the error rate spikes?"
- If the doc targets enterprise customers: "Has this feature been assessed for FedRAMP / GCC High compliance requirements?"
