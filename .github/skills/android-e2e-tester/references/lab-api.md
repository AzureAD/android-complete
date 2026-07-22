# LAB API — Provisioning and Managing Test Accounts

The **MSID LAB user-manager API** (`https://labusermanagerapi.azurewebsites.net`) creates and manages
lab test accounts for E2E runs. Drive it with `scripts/labapi.ps1`. The canonical, always-current list of
endpoints/parameters is the LAB **URL generator** web app:
<https://labusermanagerapi.azurewebsites.net/api/WebApp>.

Table of contents:
- [Authentication (why a normal token fails)](#authentication-why-a-normal-token-fails)
- [Endpoints](#endpoints)
- [`labapi.ps1` usage](#labapips1-usage)
- [Response shape (create-user)](#response-shape-create-user)
- [When to use which endpoint](#when-to-use-which-endpoint)
- [Entitlements](#entitlements)

## Authentication (why a normal token fails)

The API sits behind **Azure App Service Authentication (EasyAuth)**. A service token from
`az account get-access-token` is rejected with `consent_required` — EasyAuth wants an **interactive**
Entra session, not a raw bearer token. The LAB URL-generator page works because your browser is already
signed in; the endpoint validates against that existing session.

`labapi.ps1` reproduces that: it opens the endpoint in **headless Microsoft Edge**
(`--headless=new --dump-dom`), which reuses your Entra **WAM SSO** session, then parses the JSON the
endpoint returns. It caches an isolated Edge profile under `%TEMP%\labapi_edge_profile` so the first call
may do a silent WAM handshake and later calls are fast. If a call comes back needing sign-in, run it once
in a visible browser to seed the session:
```powershell
./scripts/labapi.ps1 open -Url "https://labusermanagerapi.azurewebsites.net/api/CreateTempUserID4SLab2?usertype=GlobalMFA"
```

> **Edge `--dump-dom` regression (Edge 150+).** Newer headless Edge can return **0 bytes** for
> `--dump-dom` (and even `--version` prints nothing), which silently broke provisioning. `labapi.ps1`
> now **auto-falls back to the DevTools protocol (CDP)**: it relaunches Edge with
> `--remote-debugging-port`, finds the real `http(s)` page target (skipping `edge://` dialogs), opens a
> `System.Net.WebSockets.ClientWebSocket`, and reads `document.body.innerText` via `Runtime.evaluate`.
> This needs no Node/npm and works on Windows PowerShell 5.1+ and PowerShell 7. You don't do anything —
> if `--dump-dom` is empty the script switches automatically. If you ever need to debug it, `-Raw` prints
> whatever raw text the (dump-dom **or** CDP) path captured.

> This is a **harness auth workaround**, not a code path under test. It only works on a machine where the
> operator is interactively signed in to Edge with an entitled account.

## Endpoints

Base URL: `https://labusermanagerapi.azurewebsites.net/api/`

| Endpoint | Params | What it does |
|---|---|---|
| `CreateTempUserID4SLab2` | `usertype` | Creates a **temp cloud user** in the ID4SLab2 lab. **Auto-deletes after ~60 min.** Returns UPN + metadata. |
| `ResetID4SLab2` | `upn`, `operation` | Resets `mfa` or `password` for a user. **Password reset = temp users only.** |
| `EnablePolicyID4SLab2` | `upn`, `policy` | Enables a CA/special policy for a locked user. |
| `DisablePolicyID4SLab2` | `upn`, `policy` | Disables a CA/special policy for a locked user. |
| `DeleteDeviceID4SLab2` | `upn`, `deviceid` | Removes a device registration from Entra ID. |
| `List of Test Accounts` | `team` | KeyVault **deep-link** to the pre-created test-account JSON secret (team: `Android`, `JS`, `iOS`, `OneAuth`). Opens in the Azure Portal — use `labapi.ps1 open`. |
| `Fetch Password for Tenant` | `testTenant` | KeyVault **deep-link** to a tenant's password secret (`ID4SLAB2`, `ID4SLAB1`, `ARLMSIDLAB1`, `MNCMSIDLAB1`, `MSIDLAB4`, `MSIDLAB3`, `MSIDLAB8`). Opens in the Azure Portal. |

**`usertype` values:** `Basic`, `GlobalMFA`, `MAMCA`, `MDMCA`, `MFAONSPO`, `MFAONEXO`, `FIDOBasic`,
`FIDOMDM`, `AuthappLBAC`, `AuthappRichContext`.

**`policy` values:** `GlobalMFA`, `MAMCA`, `MDMCA`, `MFAONSPO`, `MFAONEXO`, `AuthappLBAC`,
`AuthappRichContext`.

**`operation` values:** `mfa`, `password`.

The two KeyVault items return **portal deep-links to a secret**, not a JSON API — they need an interactive
browser (`labapi.ps1 open`), and reading the secret needs the DevKV entitlement below.

## `labapi.ps1` usage

```powershell
# Create a fresh temp user of a given type (prints UPN=... on success):
./scripts/labapi.ps1 create-user   -UserType GlobalMFA

# Clear a stale MFA registration so a first-time-setup flow can be re-run cleanly:
./scripts/labapi.ps1 reset          -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Operation mfa

# Reset the password of a temp user:
./scripts/labapi.ps1 reset          -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Operation password

# Temporarily disable a CA policy that blocks a segment you're not testing, then re-enable it after:
./scripts/labapi.ps1 disable-policy -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Policy GlobalMFA
./scripts/labapi.ps1 enable-policy  -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Policy GlobalMFA

# Remove a device registration (e.g. clean up after a device-registration test):
./scripts/labapi.ps1 delete-device  -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -DeviceId <objectId>

# Open a KeyVault deep-link (test-account list / tenant password) in a visible browser:
./scripts/labapi.ps1 open -Url "https://labusermanagerapi.azurewebsites.net/api/WebApp"

# Debug: dump the raw DOM Edge returned instead of parsing JSON:
./scripts/labapi.ps1 create-user -UserType Basic -Raw
```

Flags: `-TimeoutSec <n>` (Edge virtual-time budget, default 30), `-Fresh` (throwaway Edge profile instead
of the cached one).

## Response shape (create-user)

`CreateTempUserID4SLab2` returns JSON like:
```json
{
  "title": "User Creation Successful",
  "userType": "GlobalMFA",
  "upn": "Locked_5b335908a3@ID4SLab2.onmicrosoft.com",
  "passwordUri": "https://ms.portal.azure.com/#@.../Microsoft_Azure_KeyVault/Secret/https://msidlabs.vault.azure.net/secrets/ID4SLAB2",
  "credentialVaultKeyName": "https://msidlabs.vault.azure.net:443/secrets/ID4SLab2",
  "tenantId": "c7cef333-42af-492c-afb0-21f74a661133",
  "tenantName": "ID4SLab2.onmicrosoft.com",
  "labName": "ID4SLab2",
  "authority": "https://login.microsoftonline.com/",
  "objectId": "2007370f-74dc-4e03-b4b6-148838cd4323",
  "userObject": { "UserPrincipalName": "Locked_5b335908a3@ID4SLab2.onmicrosoft.com", "...": "..." }
}
```
`labapi.ps1 create-user` echoes the full JSON and a convenience `UPN=<upn>` line. The **password** for a
temp user is the shared lab password (kept in the KeyVault the `passwordUri` points to). In a run, the
user usually supplies the shared password directly — **never print it into the transcript**; type it with
`deviceui.ps1 input-text -Secret`.

## When to use which endpoint

- **Need a clean account** → `create-user`. Remember it self-destructs in ~60 min; provision it just
  before the run, not at the start of a long setup.
- **A first-time-registration flow already registered on a prior attempt** (so the app skips the very step
  you want to test) → `reset -Operation mfa` to clear MFA, or provision a brand-new user.
- **A CA policy blocks a segment you're not testing** (e.g. you want to test token acquisition but MFA
  keeps interrupting) → `disable-policy`, run the segment, then `enable-policy` to restore state.
- **Device-registration test left a stale device** → `delete-device` to clean up so the next run starts fresh.
- **You need the exact UPNs of the durable, pre-created accounts** (not temp) → open the **List of Test
  Accounts** KeyVault link for your team; get the tenant password via **Fetch Password for Tenant**.

## Entitlements

Request/manage at <https://coreidentity.microsoft.com/manage/entitlement>:

- **`TM-MSIDLabs-Ext`** — required for **all** LAB APIs. Needs both **RO** and **RW**.
- **`TM-MSIDLABS-DevKV`** — required to read the **Mobile Build Vault** (the KeyVault deep-links). Needs
  both **RO** and **RW**.

If a call returns a sign-in/consent page instead of data, you're either not signed into Edge with an
entitled account or you're missing one of the above — that's a **user/setup blocker**, not a defect.
