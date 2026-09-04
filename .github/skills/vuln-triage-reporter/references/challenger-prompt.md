# Challenger prompt template (Pass 2) — copy this, don't improvise

> **Why this file exists.** The adversarial second pass is a **non-negotiable** step, and it is the step
> most likely to fail silently. In a real run the challenger dispatch was **blocked by content filtering**
> after ~10 minutes and returned *no content at all*. The run had no second pass, and nothing in the output
> said so. The skill already warned about this in prose — but a warning is not a template, and every
> *less* sensitive artifact in this skill is scaffolded by a script while the most failure-prone prompt was
> left to be improvised each time.
>
> **Copy the template below.** Do not free-hand an adversarial prompt.

---

## The rule

A challenge prompt must read as **an independent second-opinion review of our own source code**, because
that is exactly what it is. It must **not** read as a request to attack a live system.

The analytical rigor is identical either way. Only the framing changes. What makes a challenge sharp is the
**Scope Contract** and the **verbatim Claim Ledger** — not aggressive phrasing.

### Phrasing that gets blocked → what to write instead

| ❌ Avoid | ✅ Use |
|---|---|
| "Your ONLY job is to **BREAK** this" | "Independently verify each statement; report which hold and which do not" |
| "hunt for the bypass", "find the exploit" | "identify any reachable code path on which this validation is not invoked" |
| "SPECIFIC ATTACK IDEAS TO PURSUE" | "Coverage questions the first review may have under-examined" |
| "build the strongest case that it is still exploitable" | "state whether the cited control is sufficient, and what it does not cover" |
| "craft an APK whose signer matches" | "describe how multi-signer APKs and certificate rotation are handled" |
| "**BROKEN** / attacker wins" | "**CORRECTED** — the statement does not hold, because …" |
| *(silence on output)* | "Do not produce exploit steps, payloads, or a proof-of-concept." |

> Add the "do not produce exploit steps" instruction **explicitly**. It both keeps the output at
> engineering-triage level (Non-Negotiable #8) and materially reduces the chance of a block.

---

## Detect the failure — it is silent

A blocked dispatch returns an empty or refusal-shaped result, **not** an error. Before accepting any
challenger output, confirm it contains:

- a per-statement verdict section, **and**
- a `## Searches Run (audit trail)` section with real commands.

If either is missing, treat the pass as **not run**. Re-dispatch with the template below. Never record
Confidence as High on a run whose challenger did not actually execute — if it cannot be made to run, stamp
Confidence **Low** and say the challenger did not complete.

---

## Template — fill the `<>` slots and dispatch

```text
You are providing an **independent second-opinion code review** of our own <component> source at
<repo path> (submodules current: <list>). A first reviewer concluded that <one-line summary of the Pass 1
conclusion>. Your job is to **verify that conclusion rigorously and report which validations are, and are
not, actually present and reachable** on every code path.

Do **not** produce exploit steps, payloads, or a proof-of-concept. Report only: which validations exist,
where they are invoked, which code paths reach them, and which code paths do not. Cite `file:line` for
every statement. Where a validation is absent on a path, say so plainly and cite the searches that
establish the absence.

## REVIEW BOUNDARY (stay within this)
- **Subsystem**: <channel — the exact component/interface under review>
- **Methods under review**: <entry points>. The <request/bundle/intent> originates from an arbitrary
  calling app, so treat every value in it as untrusted input.
- **IN SCOPE**: <files/classes from the Scope Contract>
- **OUT OF SCOPE** (do not use as evidence in either direction): <co-resident subsystems>
- Evidence is admissible only if you can name the **hop-by-hop call path** from one of the entry points
  above to the code you cite. "Same app/package" is not a call path.

## STATEMENTS TO INDEPENDENTLY VERIFY (quote each verbatim, then confirm or correct it)
**P1-A**: "<claim text copied VERBATIM from the Claim Ledger — never paraphrased>"
**P1-B**: "<...>"
<one per severity-relevant claim>

## COVERAGE QUESTIONS THE FIRST REVIEW MAY HAVE UNDER-EXAMINED
Please answer each explicitly, with citations:
1. **Path enumeration.** Enumerate every dispatch branch of <entry points> (<variants: legacy vs modern,
   flag on/off, protocol versions>). For each branch state whether <the validation> is invoked. Flag any
   branch where it is not.
2. **Identity resolution edge cases.** <shared-UID semantics, multi-signer packages, certificate rotation,
   UID reuse after uninstall — whichever apply>
3. **Gate defaults and polarity.** For each flag/flight involved, state its default and whether `true`
   means enforcing or permissive, verified at the consumption site rather than the declaration.
4. **Ordering invariants.** Confirm whether <check X> precedes <early-return Y> on *every* path that
   reaches it, or only the one the first review inspected.
5. <the single most load-bearing open question — mark it: "This is the most important question in this
   review — trace it fully.">

## OUTPUT FORMAT
- `## Verification of each statement` — P1-A..P1-n: **CONFIRMED** / **CORRECTED** (with the correction,
  cited) / **NOT REACHED**.
- `## Path coverage table` — path | validation invoked? | file:line
- `## Gaps found` — validations absent on a reachable path, cited; or "none found" plus the absence-proof
  searches.
- `## Residual concerns` — ranked, with what would resolve each.
- `## Confidence recommendation` — High / Medium / Low, with reasoning.
- `## Searches Run (audit trail)` — VERBATIM every search/command run and its result, **including those
  returning nothing**. Mandatory; do not summarize.
```

---

## Reading the result

- **CORRECTED** is the valuable outcome — it means Pass 1 was wrong somewhere. Run the **strawman check**
  (verbatim · same channel · same asset/consumers · no new out-of-scope nouns) before accepting it, then
  apply the disagreement rules in SKILL.md.
- A challenger that reports **"none found"** with real absence proofs is a genuine HELD, and supports High
  confidence.
- A challenger that reports "none found" with **no** audit trail has not done the work. Re-dispatch.
- **Follow up on residuals.** If the challenger caps its own confidence on a specific residual, resolve
  that residual and **send it back with the evidence** (`write_agent`) rather than accepting the cap. In a
  real run this converted a Medium-High into a justified High, and confirmed the remaining findings were
  properties of shipping code rather than artifacts of a stale local checkout.
