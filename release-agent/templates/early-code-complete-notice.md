# Early Code-Complete Notice — email template

Source (AAD-gated EngHub, provided by release owner 2026-07-29):
https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/authn-sdk-msal-android/android-auth-libraries/releases/combined-release-checklist/early-code-complete-notice-email-template

Two variants exist upstream:
  * "Initial announcement"  → Phase-0 step `notice` (the early reminder, CCD-7).
  * "Update announcement"   → the CCD-day reminder (Phase-1 `final_reminder`, future).

Placeholders filled deterministically by `prepare-notice`:
  {month}       release month name, e.g. "August"
  {ccd_long}    long CCD, e.g. "Wednesday, August 12th, 2026"
  {ccd_date}    CCD as MM/DD/YYYY, e.g. "08/12/2026"
  {owner}       release owner display name (or email)
  {owner_at}    release owner @-handle for the table (owner_email local part)

The hotfix cherry-pick guide link is a fixed external reference (see EXTERNAL-REFERENCES.md).

===INITIAL:SUBJECT===
Android Authenticator App & Broker Libraries Code Completion Date - {month} Release
===INITIAL:BODY===
Hi everyone,

This is a reminder that the Microsoft Android Authenticator app and Broker libraries code complete date for the {month} release is **{ccd_long}.**

Any check-ins made after code complete will require following [the hotfix cherry-pick guide](https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/microsoft-authenticator/microsoft-authenticator/release/cherry-pick-to-hotfix-guidelines) and EM approval.

| **Month** | **Code Complete Date** | **Android Release Owner** |
| --- | --- | --- |
| {month} | {ccd_date} | **Primary (Release Owner — covers Broker + Auth App):** @{owner_at} |

Thank you,

{owner}
===UPDATE:SUBJECT===
Android Authenticator App & Broker Libraries Code Completion Date - {month} Release (Today)
===UPDATE:BODY===
Hi everyone,

This is a reminder that the Microsoft Android Authenticator app and Broker libraries code complete date for the {month} release is **Today**

Any check-ins made after code complete will require following [the hotfix cherry-pick guide](https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/microsoft-authenticator/microsoft-authenticator/release/cherry-pick-to-hotfix-guidelines) and EM approval.

| **Month** | **Code Complete Date** | **Android Release Owner** |
| --- | --- | --- |
| {month} | {ccd_date} | **Primary (Release Owner — covers Broker + Auth App):** @{owner_at} |

Thank you,

{owner}
