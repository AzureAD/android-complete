# Emulator Performance & Android Studio Integration

Table of contents:
- [Why the emulator is slow (and the #1 fix)](#why-the-emulator-is-slow-and-the-1-fix)
- [Diagnose your host in one command](#diagnose-your-host-in-one-command)
- [Fast-path: use a physical device](#fast-path-use-a-physical-device)
- [Making the emulator as fast as it can be](#making-the-emulator-as-fast-as-it-can-be)
- [Slow Play Store / network downloads](#slow-play-store--network-downloads)
- [Android Studio: Device Manager & Running Devices](#android-studio-device-manager--running-devices)

## Why the emulator is slow (and the #1 fix)

The dominant factor is **GPU rendering mode**:

- **With a real GPU** the emulator uses **host (hardware) rendering** — UI, `screencap`, and `uiautomator dump` are fast.
- **Without a usable GPU** — typically a **Cloud PC / VM / RDP session** (e.g. a `CPC-*` Windows 365 box, which reports only a "Microsoft Hyper-V Video" / "Remote Display" adapter) — the emulator falls back to **SwiftShader software rendering** (the CPU draws every frame). Everything graphical is slow, and there is **no flag that makes a GPU appear**; `-gpu host` can't help when the host has no GPU.

CPU virtualization (WHPX/HAXM/AEHD) is separate and usually still works on a VM (nested virtualization), so the CPU isn't the bottleneck — the **GPU is**.

**#1 fix: run the scenario on a physical device instead of the emulator.** The skill's device pool includes
connected real devices, and on a GPU-less host `emulator.ps1 ensure` / `devicelease.ps1 acquire` will
**auto-prefer a connected physical device** for you. A phone renders on its own GPU and downloads over real
Wi-Fi, so flows that crawl on a software-rendered emulator (installing Company Portal, driving WebViews)
run in seconds.

## Diagnose your host in one command

```powershell
./scripts/emulator.ps1 resolve-sdk
```
It prints the host GPU/perf profile, e.g. on a Cloud PC:
```
host GPU:     NONE -> emulator uses SLOW software (SwiftShader) rendering
host type:    VM (Virtual Machine), RDP session, Cloud PC
emu defaults: -gpu swiftshader_indirect -cores 6 -memory 6144
TIP:          use a physical device for a fast run (emulator.ps1 ensure -PreferPhysical).
```
If `host GPU` says NONE, expect a slow emulator and prefer a real device.

## Fast-path: use a physical device

1. Connect the device over USB (enable **USB debugging**) or Wi-Fi (`adb connect <ip>:5555`), and accept the
   RSA prompt. Confirm it's in the pool: `./scripts/emulator.ps1 pool`.
2. Acquire/target it — on a GPU-less host it's automatic, or force it explicitly:
   ```powershell
   ./scripts/devicelease.ps1 acquire -Owner $AgentId -PreferPhysical -Wait   # lease a real device
   # or, without leasing:
   ./scripts/emulator.ps1 ensure -PreferPhysical -Wait
   ```
3. Drive everything against that `$serial` exactly as with an emulator.

**Caveats for real devices:** brokered flows with a **production** broker (Company Portal / Authenticator)
won't release tokens to a **debug-signed** calling app (caller-trust) — that's a signing/environment limit,
not a defect (see [troubleshooting.md](troubleshooting.md#signing--redirect-uri-mismatch-aadsts50011)).
Biometric prompts need a **real enrolled fingerprint** (the emulator-only `emu finger` simulation doesn't
apply) — see [ui-interaction.md](ui-interaction.md). Never print credentials; type them on the device.

## Making the emulator as fast as it can be

When you must use the emulator (no device available), these help — but won't beat a GPU-less ceiling:

- **More cores/RAM.** `emulator.ps1` now auto-sizes `-cores`/`-memory` from the host (e.g. 6 cores / 6144 MB
  on a big Cloud PC, up from a stock AVD's 4/1536). Override with `-Cores` / `-Memory`.
- **Keep the warm snapshot.** Don't pass `-ColdBoot` unless a snapshot is corrupt — a warm boot from
  snapshot is much faster than a cold boot.
- **Explicit GPU mode.** The script auto-picks `host` when a GPU exists, else `swiftshader_indirect`.
  Force with `-Gpu host` on a machine that has a GPU but defaulted to software.
- **Headless where possible.** `-NoWindow` skips the Qt UI (uiautomator/screencap still work); saves some
  overhead, especially over RDP.
- **Use x86_64 images on x86 hosts.** ARM images on an x86 host run under slow translation. (On a physical
  ARM phone, native arm64 is fine — build the matching ABI.)
- **Install the matching APK ABI.** On an `x86_64` emulator, prefer a **universal** APK
  (`app-production-universal-release-signed.apk`). A large **`arm64-v8a`-only** APK can pass the ABI check
  (the image lists `x86_64,arm64-v8a`) yet **crash install-time dexopt** — a Watchdog kill / `Broken pipe` /
  `Can't find service: package`. Keep the arm64 APK for **physical** devices. Details in
  [troubleshooting.md](troubleshooting.md#install-failures).
- **Know when to abandon the emulator.** On some GPU-less hosts the emulator boots but stays **unstable** —
  the Bluetooth HAL crash-loops (`hci_backend_aidl.cc:40 initializationComplete`) and repeatedly restarts
  `system_server`, so a heavy app can't be driven. `-feature -Bluetooth` may not fix it. Once you've tried the
  [cold-boot recipe](troubleshooting.md#emulator-wont-start-or-boot) and it still won't settle, **switch to a
  physical device** rather than burning time — accept that a biometric-gated step then becomes a blocker on
  that host (see [common-blockers.md](common-blockers.md#decision-emulator-vs-physical-device)).
- **Raise timeouts, don't fight slowness.** On a slow host, prefer longer `wait-text` / boot / install
  timeouts and poll for completion (e.g. `is-installed` in a loop) instead of fixed sleeps, so a slow-but-
  correct step isn't misread as a failure.

## Slow Play Store / network downloads

Large downloads (e.g. Company Portal ~30 MB) can take **minutes** on an emulator that finishes in **seconds**
on a phone. Causes and mitigations:

- The emulator routes through a **user-mode NAT** with its own DNS; throughput is limited and unrelated to
  your real link speed. `-netspeed full -netdelay none` (already set) removes artificial throttling but can't
  make the NAT fast.
- Software rendering also slows the Play Store UI itself.
- **Mitigation:** poll for install completion by package presence rather than a fixed wait; mind any
  **time-boxed** feature step (a slow download can outrun a TTL — re-trigger with the app already installed).
  For download-heavy flows, **use a physical device** — this is the clearest win.

## Android Studio: Device Manager & Running Devices

Two different things, and the skill already lines up with the first:

- **Device Manager (the AVD list).** The skill creates AVDs in the **default AVD home** (`~/.android/avd`,
  unless you set `ANDROID_AVD_HOME`) — the **same** place Android Studio reads. So **AVDs the skill creates
  already appear in Android Studio's Device Manager**, and you can start/stop/wipe/delete them there. (Verify
  with `./scripts/emulator.ps1 list` vs. Device Manager — same names.)

- **Running Devices (the embedded mirror window).** This tool window mirrors an emulator that Android Studio
  **launched into it**. The skill starts the emulator as a **standalone process** (its own window / gRPC
  endpoint), so it shows up in `adb devices` and Device Manager but **not automatically inside the Running
  Devices window**. Two ways to get parity:

  1. **Start from Studio, let the skill reuse it (recommended).** In Studio: *Settings → Tools → Emulator →
     "Launch in the Running Devices tool window"* (enable), then start the AVD from **Device Manager** — it
     opens inside **Running Devices**. Now run the skill **without** `-Avd`/`-Serial`; `ensure` **prefers an
     already-running emulator**, so it drives the exact device you see mirrored in Studio. (Confirm the skill
     picked it: its `SERIAL=` matches `adb devices`.)
  2. **Mirror the skill's emulator.** With that same setting enabled, recent Android Studio can also mirror an
     externally-started emulator in Running Devices once it detects it (it may prompt). If it doesn't appear,
     use option 1 — it's the reliable path.

  Either way the skill and Studio share one emulator, one AVD list, and one adb — no duplication.

> Net: if you're on a Cloud PC/VM (no GPU), the fastest, least-friction setup is a **connected physical
> device** (the skill auto-prefers it). If you want the emulator visible in Studio's **Running Devices**,
> start it from Studio and let the skill reuse it.
