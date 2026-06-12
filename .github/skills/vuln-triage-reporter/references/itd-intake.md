# ITD / FireWatch Manual Intake

FireWatch (Glasswing) findings are **not reachable** through the Security MCP server. Confirmed: the
`microsoft-authentication-library-common-for-android` repo findings are not in the Security Copilot signal
store under either service-tree ID (`8d0d308e` AuthN SDK - MSAL Android, `0b97f26e` Microsoft Authenticator
- Android). The page itself is an auth-gated SPA behind Azure Front Door. So intake is manual.

> Note: these service-tree GUIDs and team IDs are inert routing identifiers (useless without corp access),
> so they are fine to list here. **Do NOT** add the genuinely sensitive items (telemetry sampling/coverage
> numbers, internal security-control logic, PII/tenant data, or finding-content paired with IcM IDs).

## What the agent does
1. Scaffold one folder per finding under `.github/local-context/msrc/itd-investigations/` via
   `scripts/scaffold_itd.py` (folders named `<n>-<vulntype>-<component>`).
2. Each folder gets a placeholder `README.md`.

## What to ask the USER to do (they must do this — agent cannot)
For each FireWatch finding:
1. Open the finding in the FireWatch Partner Portal.
2. **Save Page As → "Web Page, Complete"** into the matching folder.
3. Confirm the save produced **both**:
   - `Finding Detail - FireWatch Partner Portal.html` (the wrapper: badges, severity, source, assignee)
   - `Finding Detail - FireWatch Partner Portal_files/report-content.html` (**the full report — required**)

> The portal disables copy/right-click, but that only blocks in-page selection. The **saved** HTML is fully
> readable by the agent. The report body lives in `_files/report-content.html`; if that sub-file is
> missing, only the wrapper was saved and the report is lost — re-save.

## Saved HTML structure (what the agent transcribes)

**Wrapper** (`...Partner Portal.html`) — read from the `fd-header` / `fd-kv` blocks:
- Vulnerability Type, Severity badge, Source (e.g. `Glasswing`), Finding ID, Repository, Exploitable,
  Assignee, breadcrumb (service tree → repo → finding GUID).

**Report** (`_files/report-content.html`) — the rich content:
- `<h1>` title, Metadata table (Finding ID, Exploitability Tier, Severity, Vulnerability Class, CWE,
  CVSS, Attack Vector, Discovered By, Validation verdict), Description, Affected Code Locations table,
  Source-to-Sink trace, Exploitation Scenario, Impact, References, Validation, Suggested Fix.

`scripts/transcribe_finding.py` parses these into the folder `README.md`. **Strip the exploitation
PoC/sample-exploit blocks** from committed output — keep class, CWE, sink locations, and the fix.

## Mapping findings to IcMs
Each FireWatch GUID corresponds to an `[ITD]` IcM (the security team mirrors them). Match by GUID in the
IcM title/summary, or by component + vuln class + date. Record the IcM id in the finding `README.md`.
