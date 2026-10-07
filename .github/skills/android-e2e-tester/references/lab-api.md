# LAB API — Provisioning and Managing Test Accounts

The **MSID LAB user-manager API** (`https://labusermanagerapi.azurewebsites.net`) creates and manages
lab test accounts for E2E runs. Drive it with `scripts/labapi.ps1`. The canonical, always-current list of
endpoints/parameters is the LAB **URL generator** web app:
<https://labusermanagerapi.azurewebsites.net/api/WebApp>.

Table of contents:
- [Authentication (why a normal token fails)](#authentication-why-a-normal-token-fails)
- [Endpoints](#endpoints)
- [Account policy (prefer fresh ID4SLAB2 temp users)](#account-policy-prefer-fresh-id4slab2-temp-users)
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
endpoint returns. It caches an isolated **per-process** Edge profile under `%TEMP%\labapi_edge_p<pid>` so the
first call may do a silent WAM handshake and later calls in the same run are fast, while two concurrent runs
never share one profile. If a call comes back needing sign-in, run it once
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

## Account policy (prefer fresh ID4SLAB2 temp users)

**Default to a brand-new ID4SLAB2 temp user for every case** — even when a test case names a specific
**MSIDLAB4** (or other legacy-lab) account. MSIDLAB4 is being deprecated, its durable accounts carry state
from prior runs, and reusing one account across cases is exactly what produces the "already registered, step
skipped" false pass. `create-user` (→ `CreateTempUserID4SLab2`) hands you a clean, isolated account that
self-deletes in ~60 min, so there is nothing to tear down.

Map the **capability** the case wanted onto an ID4SLAB2 `usertype` rather than reusing the named account:

| Case asks for… | Use instead |
|---|---|
| MSIDLAB4 MAM-CA account | `create-user -UserType MAMCA` |
| MSIDLAB4 MDM-CA account | `create-user -UserType MDMCA` |
| A plain/basic Entra user (e.g. old `…?usertype=Basic` endpoint) | `create-user -UserType Basic` |
| Global-MFA user | `create-user -UserType GlobalMFA` |
| FIDO / passkey user | `create-user -UserType FIDOBasic` / `FIDOMDM` |

Provision it **just before** the run (the ~60-min TTL), and pair it with a fresh app install for a truly
independent case. Only fall back to a specific named durable account when the case genuinely needs *that
identity's* pre-provisioned data (a seeded mailbox, a long-lived group membership) a temp user can't have —
then read its password with `fetch-password`, and **don't** mutate or tear it down.

**Freshness gate — poll ≤ 3 min, then recreate or reuse a < 30-min-old user.** A just-created temp user can lag
ESTS replication and show *"This username may be incorrect"* at sign-in. Don't wait indefinitely: if the new
user isn't **consistently** sign-in-able within **3 minutes** of polite polling, either **create another** temp
user, **or reuse a previously created temp user that is still under ~30 minutes old** (inside its 60-min TTL and
already propagated — one that already signed in once is safest). Note the swap in the report; never "fix" the lag
with a password reset. See
[common-blockers.md → Fresh temp user not sign-in-able yet](common-blockers.md#fresh-temp-user-not-sign-in-able-yet-ests-propagation-lag).

## `labapi.ps1` usage

```powershell
# Create a fresh temp user of a given type (prints UPN=... on success):
./scripts/labapi.ps1 create-user   -UserType GlobalMFA

# Clear a stale MFA registration so a first-time-setup flow can be re-run cleanly:
./scripts/labapi.ps1 reset          -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Operation mfa

# Reset the password of a temp user — ONLY when the test case explicitly asks for a password reset/change flow.
# A rejected password at sign-in is NOT a reason to do this (see "When to use which endpoint" below).
./scripts/labapi.ps1 reset          -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Operation password

# Temporarily disable a CA policy that blocks a segment you're not testing, then re-enable it after:
./scripts/labapi.ps1 disable-policy -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Policy GlobalMFA
./scripts/labapi.ps1 enable-policy  -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -Policy GlobalMFA

# Remove a stale device registration at the START of a run if a leftover Entra device would interfere:
./scripts/labapi.ps1 delete-device  -Upn "Locked_xxx@ID4SLab2.onmicrosoft.com" -DeviceId <objectId>

# Open a KeyVault deep-link (test-account list / tenant password) in a visible browser:
./scripts/labapi.ps1 open -Url "https://labusermanagerapi.azurewebsites.net/api/WebApp"

# Fetch a tenant's shared password straight from Key Vault into the local DPAPI store (no paste, no browser):
./scripts/labapi.ps1 fetch-password -TestTenant ID4SLAB2 -IntoSecret labpw
# ...then type it on-device without it ever appearing in chat:
./scripts/deviceui.ps1 input-text -SecretRef labpw -Secret

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
  "upn": "Locked_xxx@ID4SLab2.onmicrosoft.com",
  "passwordUri": "https://ms.portal.azure.com/#@.../Microsoft_Azure_KeyVault/Secret/https://msidlabs.vault.azure.net/secrets/ID4SLAB2",
  "credentialVaultKeyName": "https://msidlabs.vault.azure.net:443/secrets/ID4SLab2",
  "tenantId": "<tenant-guid>",
  "tenantName": "ID4SLab2.onmicrosoft.com",
  "labName": "ID4SLab2",
  "authority": "https://login.microsoftonline.com/",
  "objectId": "<object-id>",
  "userObject": { "UserPrincipalName": "Locked_xxx@ID4SLab2.onmicrosoft.com", "...": "..." }
}
```
`labapi.ps1 create-user` echoes the full JSON and a convenience `UPN=<upn>` line. The **password** for a
temp user is the shared lab password (kept in the KeyVault the `passwordUri` points to). You do **not** need
the user to paste it: pull it from Key Vault directly with `labapi.ps1 fetch-password` (see next section),
which caches it DPAPI-encrypted and types it via `deviceui.ps1 input-text -SecretRef <name> -Secret`.
**Never print the password into the transcript.**

## Fetch a tenant password from Key Vault directly

The "Fetch Password for Tenant" generator link is just an Azure Portal deep-link to a secret in the
`msidlabs` vault (e.g. `https://msidlabs.vault.azure.net/secrets/ID4SLAB2`). If you are signed into the
**Azure CLI** (`az login`) with an account that holds **`TM-MSIDLABS-DevKV`** (or equivalent vault GET
access), you can read that secret straight from the vault — no browser, no manual paste:

```powershell
# Cache the ID4SLab2 shared password into DPAPI secret 'labpw' (prints only a masked length):
./scripts/labapi.ps1 fetch-password -TestTenant ID4SLAB2 -IntoSecret labpw
# -> "Fetched password into DPAPI secret 'labpw' (20 chars). ..."

# Or pass the exact secret URI create-user returned in credentialVaultKeyName / passwordUri:
./scripts/labapi.ps1 fetch-password -SecretId "https://msidlabs.vault.azure.net/secrets/ID4SLAB2" -IntoSecret labpw
```

`-TestTenant` accepts the KeyVault secret names: `ID4SLAB2`, `ID4SLAB1`, `ARLMSIDLAB1`, `MNCMSIDLAB1`,
`MSIDLAB4`, `MSIDLAB3`, `MSIDLAB8` (override the vault with `-Vault`, default `msidlabs`). The command:

- reads the value with `az keyvault secret show --id <uri> --query value -o tsv` (no parentheses in the
  JMESPath, so it survives the `az.cmd`/cmd.exe wrapper),
- writes it **only** to the local DPAPI store (`%USERPROFILE%\.android-e2e-secrets\<IntoSecret>.sec`), the
  same on-disk format `secrets.ps1` uses, and
- prints **only** a masked confirmation (`... (NN chars).`) — the plaintext never touches the host,
  pipeline, logs, or git.

Then `deviceui.ps1 input-text -SecretRef labpw -Secret` resolves it in-process and types it on the device.

If `fetch-password` errors with a vault/auth message, it's a **setup blocker**: run `az login` with an
entitled account (see Entitlements below), or fall back to `secrets.ps1 set -Name labpw` and paste once into
the hidden prompt. This uses **your** entitled CLI session — if the token expires or you sign out, access
stops.

## When to use which endpoint

- **Need a clean account** → `create-user`. Remember it self-destructs in ~60 min; provision it just
  before the run, not at the start of a long setup. Prefer this over any named MSIDLAB4 account (see
  [Account policy](#account-policy-prefer-fresh-id4slab2-temp-users)).
- **A first-time-registration flow already registered on a prior attempt** (so the app skips the very step
  you want to test) → `reset -Operation mfa` to clear MFA, or provision a brand-new user.
- **Sign-in says the password is incorrect or expired** → do **NOT** `reset -Operation password`. That message
  almost always means you're typing the wrong *value* or targeting the wrong *account*, not that the account
  needs changing. First re-pull the shared value with `fetch-password` (it may have rotated in Key Vault) and
  double-check the UPN/tenant the case named; also check the account isn't `Locked_…` from an earlier lockout.
  Only run `reset -Operation password` when the **test case itself** describes a password-change/expiry flow.
  **Never** reset the password of a **shared durable account** (a durable, pre-created account, not a temp
  `Locked_…` user) — other cases reuse it and a reset breaks them. If the value is confirmed correct and it
  still fails, mark the run **BLOCKED** with the exact on-screen error instead of mutating the account.
- **A CA policy blocks a segment you're not testing** (e.g. you want to test token acquisition but MFA
  keeps interrupting) → `disable-policy`, run the segment, then `enable-policy` to restore state.
- **A device-registration test left a stale device** and the *next* run needs a clean slate → optionally
  `delete-device` **at the start of the next run**. Per the clean-state policy you do **not** tear down
  registrations at the end of a case — a fresh app install + fresh account already isolates the next run;
  use `delete-device` only if a lingering Entra device object would actually interfere.
- **You need the exact UPNs of the durable, pre-created accounts** (not temp) → open the **List of Test
  Accounts** KeyVault link for your team; get the tenant password via `fetch-password` (CLI, no paste) or
  the **Fetch Password for Tenant** deep-link.

## Entitlements

Request/manage at <https://coreidentity.microsoft.com/manage/entitlement>:

- **`TM-MSIDLabs-Ext`** — required for **all** LAB APIs. Needs both **RO** and **RW**.
- **`TM-MSIDLABS-DevKV`** — required to read the **Mobile Build Vault** (`msidlabs`): the KeyVault
  deep-links *and* `labapi.ps1 fetch-password` (via `az`). Needs both **RO** and **RW**.

If a call returns a sign-in/consent page instead of data, you're either not signed into Edge with an
entitled account or you're missing one of the above — that's a **user/setup blocker**, not a defect.
