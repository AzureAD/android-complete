# Log Signals — Judging E2E Pass/Fail from logcat

Table of contents:
- [How auth logging works](#how-auth-logging-works)
- [Capture discipline](#capture-discipline)
- [Success signals](#success-signals)
- [Failure signals](#failure-signals)
- [Operation flow markers](#operation-flow-markers)
- [Common AADSTS / error codes](#common-aadsts--error-codes)
- [Judging success per flow](#judging-success-per-flow)
- [Correlating with eSTS](#correlating-with-ests)

`authlogs.ps1 scan` automates most of this. Use this doc to interpret the evidence it prints and
to reason beyond the heuristic verdict.

## How auth logging works

MSAL, Common, Broker, and ADAL all log through the shared **`Logger`** class (not `android.util.Log`
directly). Lines carry a component tag and, for most auth operations, a **correlation_id** (a GUID)
that ties a client request to the broker and to the eSTS token service. Verbose/PII logging is off by
default; tokens and secrets are redacted. Do not expect to see token values — presence of a
`correlation_id` plus a success marker is the signal, not the token itself.

Typical tags to filter on: `MSAL`, `Broker`, `Common`, `ADAL`, `OneAuth`, plus operation classes like
`BrokerMsalController`, `CommandDispatcher`, `SilentTokenCommand`, `InteractiveTokenCommand`.

## Capture discipline

1. `authlogs.ps1 clear` **immediately before** running the scenario — otherwise stale lines pollute the verdict.
2. Run the scenario.
3. `authlogs.ps1 scan -Package <app-under-test>` — always pass `-Package` so pass/fail signals are
   scoped to auth-relevant + app lines (reduces false positives from system noise).
4. Save every run's snapshot into the run folder so failures can be diffed across iterations.

## Success signals

| Pattern | Meaning |
|---|---|
| `executed successfully` | A broker/command operation completed |
| `AcquireToken...success` / `Token ... acquired` | Token obtained (silent or interactive) |
| `TokenResult ... SUCCESS` | Result object reports success |
| `Retrieved ... token from cache` | Silent cache hit |
| `Saved ... token` | Token cached after acquisition |
| `PRT is already registered` / `Loading Workplace Join entry` | Healthy device/PRT state |

## Failure signals

| Pattern | Meaning / likely cause |
|---|---|
| `FATAL EXCEPTION`, `E AndroidRuntime` | App crash — always a FAIL; capture the stack |
| `AADSTS\d+` | eSTS-side error (see codes below) |
| `error_code=` / `errorCode:` | Broker/MSAL error surfaced to caller |
| `No PRT present` | Missing Primary Refresh Token — silent auth will fail |
| `INTERACTION_REQUIRED` | Silent failed; interactive needed (may be expected) |
| `invalid_grant` | Token/refresh token rejected |
| `BrokerCommunicationException` | Client↔broker IPC broke (bind/permission/signature) |
| `NullPointerException`, `NoSuchMethodError`, `ClassNotFoundException` | Code/wiring bug in the change under test |
| `CertPathValidatorException` | TLS/cert issue (often network/proxy) |

## Operation flow markers

Trace which operations ran (helps localize where a flow broke):

| Log marker | Operation |
|---|---|
| `GetDeviceModeMsalBrokerOperation` | Check if Shared Device Mode is enabled |
| `AcquireTokenSilentMsalBrokerOperation` | Silent token acquisition |
| `AcquireTokenInteractiveMsalBrokerOperation` | Interactive auth |
| `GetCurrentAccountMsalBrokerOperation` | Fetch signed-in account |
| `SignOutFromSharedDeviceMsalBrokerOperation` | SDM sign-out |
| `RemoveAccount` / `removeAccount` | Account removal |

A healthy interactive sign-in usually shows: command dispatched → interactive operation → eSTS round
trip (correlation_id) → token saved → `executed successfully`.

## Common AADSTS / error codes

| Code | Meaning | Usually means |
|---|---|---|
| `AADSTS50011` | Redirect URI mismatch | App registration / signature / redirect config wrong |
| `AADSTS65001` | Consent required | Grant consent in the flow (AI can tap Accept) |
| `AADSTS50076` / `50079` | MFA required | Interaction/MFA step needed |
| `AADSTS50126` | Invalid username/password | Wrong test credential |
| `AADSTS700016` | App not found in tenant | Wrong client id / tenant |
| `AADSTS50058` | Silent sign-in failed, no session | Expected before an interactive sign-in |

`AADSTS50011`/`700016` typically indicate a **real defect** in the change under test → root-cause and
hand to the fix loop. `AADSTS65001`/`50076`/`50058` are often **flow steps**, not defects.

## Judging success per flow

- **Silent token (AcquireTokenSilent):** PASS = token retrieved from cache/refresh with a success
  marker and no `INTERACTION_REQUIRED`/`No PRT`. If it returns `INTERACTION_REQUIRED` when a valid
  account exists, that is a regression.
- **Interactive sign-in (AcquireToken):** PASS = the flow reaches the account/consent, completes, and
  logs token saved + `executed successfully`. A lingering login page or an `AADSTS` error is a FAIL.
- **Sign-out / account removal:** PASS = removal operation runs and a subsequent `GetCurrentAccount`
  shows no account.
- **Crash at any point → FAIL**, regardless of other signals.

Do not declare PASS on the absence of errors alone — require a positive success signal that matches the
scenario. Absent both → verdict is INCONCLUSIVE; investigate (wrong package filter, logging off, or the
action never executed).

## Correlating with eSTS

When on-device logs are ambiguous, take the `correlation_id` from the snapshot and correlate with eSTS
telemetry using the **kusto-analyst** or **incident-investigator** skill. Basic query shape:

```kql
AllPerRequestTable
| where env_time >= ago(1d)
| where CorrelationId == "<correlation-id>"
| project env_time, CorrelationId, Call, Result, ErrorCode
```
