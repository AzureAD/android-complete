# Flag ON/OFF Verification — prove the fix works, and prove it's reversible

Every remediation in this codebase ships behind a **default-OFF ECS flight** (see
[remediation-execution.md](remediation-execution.md)). That buys a zero-risk rollback — but only if
someone actually **proved both states behave as claimed**. This file is how.

> **The bar: a fix is not done until all four cells of the matrix have evidence.** Not "the tests pass" —
> the *matrix* passes. A security fix nobody demonstrated blocking the attack is a hope; a kill-switch
> nobody demonstrated restoring legacy behavior is an outage waiting for a rollback.

---

## The verification matrix

|  | **Attack / exploit input** | **Legitimate input** |
|--|---------------------------|----------------------|
| **Flight OFF** (default, legacy) | **A. Still succeeds** — reproduces the original vulnerability | **B. Works** — unchanged behavior |
| **Flight ON** (fix active) | **C. Blocked** — the fix denies it | **D. Works** — no regression |

What each cell buys you, and why none is optional:

- **A — the fix is actually the thing that blocks it.** If the attack fails with the flight OFF, either
  something *else* already blocked it (→ the finding is **Already-Covered**; go back to Gate 0 and stop) or
  the test doesn't reproduce the vulnerability (→ the test is worthless and cell C proves nothing). **This
  is the cell people skip, and it is the one that catches a fix for a bug that wasn't there.**
- **B — flight-OFF is byte-for-byte legacy.** The rollback is real.
- **C — the fix works.** This is the acceptance criterion and the close-out evidence for the IcM.
- **D — no regression for real users** at 100% rollout.

**A and C are the same input, differing only by flight state.** That pairing *is* the proof: same input,
opposite outcomes, flag as the only variable. Anything less is correlation.

---

## Layer 1 — Automated paired tests (the gate)

Parameterize on flight state so one test body covers both rows. The flight state must be the **only**
difference between the paired cases — same input, same fixtures, same setup.

```java
// Cell A + C: the exploit input, both flight states.
@Test public void maliciousInput_flightOff_legacyBehavior() {
    withFlight(FIX_FLIGHT, false);
    // asserts the ORIGINAL (vulnerable) behavior — this proves the test reproduces the finding
    assertEquals(LEGACY_RESULT, subject.handle(MALICIOUS_INPUT));
}

@Test public void maliciousInput_flightOn_isBlocked() {
    withFlight(FIX_FLIGHT, true);
    // the negative test — the acceptance criterion for the fix
    assertThrows(SecurityException.class, () -> subject.handle(MALICIOUS_INPUT));
}

// Cell B + D: the legitimate input must be unaffected in BOTH states.
@Test public void legitimateInput_flightOff_works() { withFlight(FIX_FLIGHT, false); assertOk(...); }
@Test public void legitimateInput_flightOn_works()  { withFlight(FIX_FLIGHT, true);  assertOk(...); }
```

Rules:

- **Four tests minimum, named for their cell.** A reviewer should map test → matrix cell from the name
  alone. Match the module's existing language (Java vs Kotlin) and test framework — don't introduce one.
- **Override the flight the way the module already does it** — find an existing test that manipulates a
  flight and copy its mechanism (a flights-provider override, a fake/mock provider, a test double). Do not
  invent a new flag-injection seam, and do not make the production code more testable "while you're in
  there" — that widens the diff.
- **Assert the legacy result in the OFF case**, not just "no exception". Cell A must pin the old behavior
  precisely enough that a future refactor breaking flight-OFF parity fails this test.
- **Run them and parse the results XML** — a JUnit4 method missing `@Test` silently never runs (this has
  bitten a real run). Confirm all four appear in
  `build/test-results/<task>/TEST-*.<TestClass>.xml`, and confirm the count.

Build/run recipe for `common` (credentials gotcha, `local` flavor) is in
[remediation-execution.md](remediation-execution.md#building--testing-the-common-module-gradle-credentials-gotcha).

---

## Layer 2 — Manual on-device confirmation (sign-off)

Unit tests prove the branch; the device proves the **wiring** — that the flight key is actually read at
the sink, in a real build, on a real IPC path. These are different claims, and only the device settles the
second one.

Use a lightweight test-host app (e.g. **BrokerHost**) rather than building the full Authenticator, and
toggle the flight from the in-app **Broker Flights** screen — no ECS round-trip needed.

```markdown
### Manual verification — [MSRC|ITD] <id>

**Build:** <module/flavor>, commit <sha>   **Device:** <model / API level>   **Test host:** <app>

1. Install the build. Open **Broker Flights** → confirm `<flight key>` reads **OFF** (the shipped default).
   - ⚠️ If it does not default OFF, **stop** — the ship-dark guarantee is broken. This check alone is
     worth the device pass: a default that only *looks* OFF in the enum but resolves ON at runtime is
     invisible to unit tests.
2. **Cell A** — exercise the exploit path. Confirm the original behavior still occurs (the finding
   reproduces). Capture the `Logger` output.
3. **Cell B** — exercise the legitimate flow (real sign-in / real caller). Confirm success.
4. Toggle `<flight key>` **ON**. Force-stop and relaunch so the flight is re-read.
5. **Cell C** — repeat the exploit path. Confirm it is now **denied**, and that the denial is logged.
6. **Cell D** — repeat the legitimate flow. Confirm it still succeeds.
7. Toggle **OFF** again and re-confirm step 2 — proves the kill-switch is live, not one-way.

**Evidence:** log excerpts per cell (redact tokens/PII — never paste a credential into a report).
```

> **Cannot get a device / repro rig?** Say so explicitly in the report as a **Verification Gap** with the
> concrete ask ("needs a device with <condition>"), rather than implying the matrix passed. An honest gap
> is actionable; an unstated one is a false close-out.

---

## The verification report (attach to the PR and the IcM)

```markdown
## Fix Verification — [MSRC|ITD] <id>

**Flight:** `<key>`  ·  **Default:** OFF  ·  **Rollout:** ECS 1% → 10% → 100%

| Cell | Scenario | Flight | Expected | Result | Evidence |
|------|----------|--------|----------|--------|----------|
| A | exploit input | OFF | legacy (vulnerable) behavior | ✅ | `<test name>` / log |
| B | legitimate flow | OFF | works | ✅ | `<test name>` / log |
| C | exploit input | ON | **blocked** | ✅ | `<test name>` / log |
| D | legitimate flow | ON | works | ✅ | `<test name>` / log |

**Automated:** <n> tests, <task>, all green (verified in results XML)
**Manual:** <device / API level>, or "not performed — <reason>" (⇒ Verification Gap)
**Rollback:** flight OFF restores legacy — demonstrated by cells A + B
**Not covered:** <conditions that remain unverified — server state, downstream consumers, other OEMs>
```

**"Not covered" is mandatory and must not be empty-by-default.** Static analysis and a single device can't
cover every OEM, API level, or server state; naming the remaining gaps is what makes the rest of the table
trustworthy.

---

## Public-repo safety (this trips people up)

Three of the four target repos are **public**. The verification artifacts are where vulnerability detail
most easily leaks, because they describe the attack by construction:

- **Test names and fixtures must not describe the exploit.** `maliciousInput_flightOn_isBlocked` is fine;
  a name or comment spelling out the bypass technique is not.
- **No real exploit payloads** in committed fixtures — use a minimal synthetic input that trips the same
  code path.
- **The verification report itself is sensitive** → it lives in `$VULN_TRIAGE_WORKSPACE`, and goes in the
  **IcM**. The PR gets only a generic description plus a corp-gated work-item link.
- Run the public-token sweep + `scripts/safety_check.py` before pushing.
