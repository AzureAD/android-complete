# Secrets (passwords / PINs) & test files (APKs)

How to hand the agent **secret values** (lab-account passwords, device lock-screen PINs, keystore
passwords) and **test files** (APKs, configs) **without either leaking into the chat transcript**.
The rule the whole skill follows: a secret is *typed onto the device* but is **never printed, logged,
or committed** — only a masked length ever appears. See also the Guardrails in
[../SKILL.md](../SKILL.md) and the credential-typing notes in
[ui-interaction.md](ui-interaction.md) / [common-blockers.md](common-blockers.md).

Table of contents:
- [TL;DR](#tldr)
- [Why not just paste the password in chat](#why-not-just-paste-the-password-in-chat)
- [The encrypted secret store (recommended)](#the-encrypted-secret-store-recommended)
- [Fetch lab passwords from Key Vault (no paste at all)](#fetch-lab-passwords-from-key-vault-no-paste-at-all)
- [Using a stored secret in a run](#using-a-stored-secret-in-a-run)
- [Unlocking a device with a stored PIN](#unlocking-a-device-with-a-stored-pin)
- [Environment-variable alternative](#environment-variable-alternative)
- [Security model / at-rest notes](#security-model--at-rest-notes)
- [Handing over APKs and other test files](#handing-over-apks-and-other-test-files)
- [Quick reference](#quick-reference)

## TL;DR

| You need to give the agent... | Do this | Agent uses |
|---|---|---|
| A **lab tenant** password (ID4SLab2 etc.) | *Nothing* — the agent pulls it from Key Vault: `scripts/labapi.ps1 fetch-password -TestTenant ID4SLAB2 -IntoSecret labpw` | `deviceui.ps1 input-text -SecretRef labpw` |
| A lab/account **password** (any other) | `scripts/secrets.ps1 set -Name labpw` (type it at the masked prompt) | `deviceui.ps1 input-text -SecretRef labpw` |
| A device **lock-screen PIN** | `scripts/secrets.ps1 set -Name devicepin` | `deviceui.ps1 unlock -SecretRef devicepin` |
| A **keystore** password | `scripts/secrets.ps1 set -Name kspw` | build step reads `E2E_SECRET_KSPW` / `-SecretRef kspw` |
| An **APK** or other file | Drop it in a folder, give the **path** (paths aren't secret) | `appcontrol.ps1 install -ApkPath <path>` |

The secret value is entered **in your own terminal**, encrypted at rest, and referenced later **by
name**. It never appears in the chat, in a script argument, in `run.json`, or in the report.

## Why not just paste the password in chat

Anything you type in chat — or that the agent echoes back — becomes part of the transcript and any
session logs. That is exactly how the very first 1579381 run leaked a lab password. Passing a secret
as a **command argument** (`input-text -Text 'P@ssw0rd'`) is just as bad: it lands in the transcript,
in the agent's tool-call record, and in shell history. The store below removes both leak paths.

## The encrypted secret store (recommended)

`scripts/secrets.ps1` keeps named secrets encrypted on your machine with **Windows DPAPI** (per-user,
per-machine). You run `set` once **in your own terminal** (not through the agent), typing the value at
a masked prompt so it never reaches the agent:

```powershell
# In YOUR terminal — the value is read with a masked prompt and never echoed:
./scripts/secrets.ps1 set  -Name labpw       # lab / account password
./scripts/secrets.ps1 set  -Name devicepin   # physical-device lock-screen PIN

# Safe to run anytime (these NEVER print the value):
./scripts/secrets.ps1 list                    # names only (+ any E2E_SECRET_* env names)
./scripts/secrets.ps1 test       -Name labpw  # prints just:  resolves=yes length=NN
./scripts/secrets.ps1 get-masked -Name labpw  # prints:       labpw = ******** (NN chars)
./scripts/secrets.ps1 remove     -Name labpw  # delete when done
./scripts/secrets.ps1 path                    # where the .sec files live
```

Secrets are stored one file per name at `%USERPROFILE%\.android-e2e-secrets\<name>.sec`. There is
**no** command that prints a stored value — the closest are `test` (yes/no + length) and `get-masked`
(asterisks + length), both safe to show in chat.

> Naming convention used across the skill: `labpw` (account password), `devicepin` (lock-screen PIN),
> `kspw` (keystore password). Stick to these so `-SecretRef` calls are predictable.

## Fetch lab passwords from Key Vault (no paste at all)

For the **shared lab-tenant passwords** (ID4SLab2, MSIDLAB4, …) you don't need to type anything: their
values live in the `msidlabs` Key Vault (the same secret the LAB generator's "Fetch Password for Tenant"
link points at). If you're signed into the **Azure CLI** (`az login`) with a vault-entitled account
(**`TM-MSIDLABS-DevKV`**), the agent can read one straight into the store:

```powershell
# Pull ID4SLab2's shared password from Key Vault into DPAPI secret 'labpw' (prints only a masked length):
./scripts/labapi.ps1 fetch-password -TestTenant ID4SLAB2 -IntoSecret labpw
#  -> Fetched password into DPAPI secret 'labpw' (20 chars). ...

# Then type it on-device exactly like any other stored secret:
./scripts/deviceui.ps1 input-text -SecretRef labpw -Secret
```

The value is read with `az keyvault secret show ... --query value -o tsv`, written **only** to
`%USERPROFILE%\.android-e2e-secrets\labpw.sec` (DPAPI-encrypted), and **never** printed. Use `-SecretId
<uri>` to fetch the exact secret a `create-user` response named in `credentialVaultKeyName`. This is the
operator's own entitled CLI session being used on their behalf — if `az` isn't signed in or the entitlement
is missing, `fetch-password` fails with a setup message and you fall back to `secrets.ps1 set -Name labpw`.
See [lab-api.md → Fetch a tenant password from Key Vault directly](lab-api.md#fetch-a-tenant-password-from-key-vault-directly).

## Using a stored secret in a run

The agent types a stored secret straight into the focused field with `-SecretRef` (which implies
`-Secret`, so only the length is reported):

```powershell
# password into the eSTS/WebView field — resolved from the store, never echoed:
./scripts/deviceui.ps1 input-text -SecretRef labpw -Clear -CharByChar -Serial <serial>
# output is just:  Typed (char-by-char): [NN chars]
```

`-SecretRef <name>` resolves the value **inside** `deviceui.ps1` (env var first, then the DPAPI file)
and uses it only to drive `adb input text`. Compare with `-Text $pw` which would put the literal value
in the tool call. Prefer `-SecretRef` for every password; keep `-Text` for non-secret input (UPNs,
search strings). All the WebView typing advice (bulk-first, `-CharByChar` fallback, dismiss the passkey
sheet) still applies — see [ui-interaction.md](ui-interaction.md).

## Unlocking a device with a stored PIN

On a real device that sleeps or relocks mid-run, unlock it with the stored PIN — never with the PIN in
the clear:

```powershell
./scripts/deviceui.ps1 unlock -SecretRef devicepin -Serial <serial>
# output is just:  Unlock attempted with a N-digit PIN.
```

It wakes the screen, swipes up to reveal the keypad, types the resolved PIN, and presses ENTER.
**Caution:** a *wrong* PIN counts toward Android's lockout / Gatekeeper throttle, so only use it with
the correct PIN (same caveat as `locksettings verify` in [common-blockers.md](common-blockers.md)). For
a throwaway emulator you can skip the store and pass `-Pin 1234` directly since it isn't secret.

## Environment-variable alternative

If you'd rather not persist anything to disk (e.g., a shared/Cloud PC), export the secret as an
environment variable in the **agent's** shell session instead. The resolver checks
`E2E_SECRET_<NAME>` **before** the DPAPI file, where `<NAME>` is the ref uppercased with every
non-alphanumeric character turned into `_`:

```powershell
$env:E2E_SECRET_LABPW = (Read-Host 'lab password' -AsSecureString |
  ConvertFrom-SecureString -AsPlainText)      # PS7; for 5.1 set it however you prefer
./scripts/deviceui.ps1 input-text -SecretRef labpw -CharByChar -Serial <serial>
```

The variable lives only for that process/session and disappears when it ends. `secrets.ps1 list` also
shows which `E2E_SECRET_*` names are currently set so you can tell where a ref will resolve from.

## Security model / at-rest notes

- **DPAPI** encryption means a `.sec` file can be decrypted **only by the same Windows user on the same
  machine** — copying it elsewhere yields nothing. Good enough for throwaway lab passwords and device
  PINs; it is **not** a vault for production secrets.
- The plaintext exists only transiently in memory while typing, and is released via
  `Marshal::ZeroFreeBSTR`. No command writes it to stdout, so it can't reach the transcript.
- `%USERPROFILE%\.android-e2e-secrets\` is **outside the repo** — it can't be accidentally committed.
  Still, treat these as disposable: `remove` them after a test campaign, and rotate lab passwords freely
  (the lab temp users auto-delete in ~60 min anyway — see [lab-api.md](lab-api.md)).
- Never put a secret in `run.json`, a screenshot, a filename, or the report. The report records the
  **UPN only**, never a password (see [test-reporting.md](test-reporting.md)).

## Handing over APKs and other test files

File **paths are not secret**, so files don't go in the secret store — just make the file reachable and
give the agent its path. Two conventions, in order of preference:

1. **Drop-folder + path.** Copy the file somewhere stable and tell the agent the full path. The user's
   existing APK folder works well: `C:\Users\<you>\Downloads\APKs\`. Example the skill already uses:
   the Authenticator build at
   `C:\Users\zhipanwang\Downloads\APKs\app-production-arm64-v8a-release-signed.apk`.
   ```powershell
   ./scripts/appcontrol.ps1 install -ApkPath 'C:\Users\zhipanwang\Downloads\APKs\app-production-arm64-v8a-release-signed.apk' -Serial <serial>
   ```
2. **Newest-match in a known folder.** If you'll drop a fresh build repeatedly, say "use the newest
   `*-release-signed.apk` in `Downloads\APKs`" and the agent picks it:
   ```powershell
   $apk = Get-ChildItem 'C:\Users\zhipanwang\Downloads\APKs\*-release-signed.apk' |
     Sort-Object LastWriteTime -Descending | Select-Object -First 1
   ./scripts/appcontrol.ps1 install -ApkPath $apk.FullName -Serial <serial>
   ```

Tips:
- Match the device ABI (or use a **universal** APK). A `arm64-v8a` APK installs on an arm64 physical
  device but crashes with a dexopt error on an `x86_64` emulator — see
  [troubleshooting.md](troubleshooting.md) and [emulator-performance.md](emulator-performance.md).
- Only a file's *associated password* (e.g. a **keystore** password) is a secret — store that with
  `secrets.ps1 set -Name kspw`; the keystore file itself just needs a path.
- Prefer `-ApkPath` over pasting file contents; never paste a binary or a large config into chat.

## Quick reference

| Task | Command |
|---|---|
| **Fetch a lab-tenant password from Key Vault** (no paste) | `labapi.ps1 fetch-password -TestTenant ID4SLAB2 -IntoSecret labpw` |
| Store a password (masked prompt, your terminal) | `secrets.ps1 set -Name labpw` |
| Store a device PIN | `secrets.ps1 set -Name devicepin` |
| Confirm a secret resolves (safe) | `secrets.ps1 test -Name labpw` → `resolves=yes length=NN` |
| Show masked value (safe) | `secrets.ps1 get-masked -Name labpw` |
| List secret names (safe) | `secrets.ps1 list` |
| Delete a secret | `secrets.ps1 remove -Name labpw` |
| Type a password on device | `deviceui.ps1 input-text -SecretRef labpw -Clear -CharByChar` |
| Unlock a device | `deviceui.ps1 unlock -SecretRef devicepin` |
| Env-var alternative | set `E2E_SECRET_LABPW`, then `-SecretRef labpw` |
| Install an APK | `appcontrol.ps1 install -ApkPath <path>` |
