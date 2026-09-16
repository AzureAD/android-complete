# Research Discipline — Scope Contract & Claim Ledger

How to not fool yourself during a two-pass investigation. Two mechanics, both cheap, both mandatory:

1. **Scope Contract** — write down the trust boundary *before* investigating, so evidence from an
   unrelated subsystem cannot leak into the analysis.
2. **Claim Ledger** — carry every claim across passes **verbatim**, so the challenger attacks the real
   claim instead of a reworded one.

---

## The failure these prevent (a real run)

A finding lived in **one** cross-app channel inside a single app. During the adversarial pass, the
challenge question was framed around a **different, co-resident channel** in the same app — a separate
IPC subsystem, with a separate allow-list, a separate consumer set, and no data path to the sink. The
challenger dutifully answered *that* question, found no cross-validation (correctly — the two subsystems
are unrelated), and the result was used to **retire the higher-severity argument**.

The self-diagnosis afterwards was exact:

> *"When I wrote Pass 2's claim, I unconsciously reframed 'cross-app credential theft' as 'cross-app
> credential theft **over the other channel**' and asked the challenger to check whether that channel
> cross-validates the allow-list. That question was a category error — the two live in different IPC
> subsystems. So the reconciliation that 'killed' the higher-severity argument was actually killing a
> strawman I invented."*

Two independent defects, and note that **both passes ran correctly** — the process produced a wrong
answer anyway:

| Defect | What happened | Caught by |
|--------|---------------|-----------|
| **Scope leak** | A component with no path to the sink was treated as relevant to the sink's trust decision | **Scope Contract** |
| **Claim drift** | The claim changed wording between passes; the refutation answered the new wording | **Claim Ledger** |

> **Why this is the dangerous class of error:** it doesn't look like a mistake. The evidence is real, the
> greps are real, the citations resolve. Only the *relevance* is wrong — and relevance is exactly what a
> `file:line` citation cannot prove. Down-classifying on it produces a confident, well-cited, wrong verdict
> that a reviewer has no obvious way to catch.

---

## Part 1 — The Scope Contract

**Write this before dispatching Pass 1.** It is short, and it goes at the top of the finding report.

```markdown
## Scope Contract

**Sink lives in:** <app/process> → <module/library> → <subsystem or channel>
**Entry point:** <exported component type / IPC surface / deep link / network path>
**Trust decision under attack:** <the specific allow-list, trust store, validator, or config the finding is about>
**Who consumes this channel:** <the callers that actually bind to / call this entry point>
**Asset at risk:** <what an attacker gets — be specific about which credential/data, for which account type>

**IN SCOPE** (on the path from entry point to sink):
- <module/class/layer> — <why it is on the path>

**OUT OF SCOPE** (deliberately excluded, and why):
- <adjacent subsystem that looks related> — <no data/control path to the sink; separate trust domain>

**Boundary risk:** <the co-resident subsystem most likely to get confused with this one>
```

### The co-resident subsystem trap

The single highest-risk pattern in this codebase: **one app hosting two or more independent cross-app
channels**, each with its own allow-list, its own consumer set, and its own validator stack. They are
*architecturally parallel* and *semantically unrelated* — and their descriptions sound nearly identical
in prose ("cross-app SSO", "caller allow-list", "trusted callers").

Prose is where they get merged. `"cross-app credential theft"` reads as one idea; in the code it is two
disjoint subsystems that share no data path. **Once the phrase loses its channel qualifier, the analysis
is already wrong** — and nothing downstream will flag it.

So: **always qualify the channel, in every sentence, in every claim, in every challenge prompt.**
Never write "the allow-list" — write "the *<channel>* allow-list". Never write "cross-app credential
theft" — write "cross-app *<account-type>* credential theft *via <channel>*". The qualifier is what makes
a category error visible.

Related traps, same shape:
- Two flights with similar names governing different call sites.
- A validator in `common` and a same-named-ish validator in `broker` — different callers, different defaults.
- The same class name existing in both a library module and an app module.
- A control that exists on the path *in one flavor/build variant* but not the one that ships.

### Admissibility test — apply to EVERY piece of evidence

Before any control counts as a **mitigation** *or* as a **refutation**:

| # | Question | If "no" |
|---|----------|---------|
| 1 | Can you name the **call path** from the finding's entry point to this control, hop by hop? | **Inadmissible** — you have a co-resident component, not a control on this path |
| 2 | Does it govern the **same trust decision** named in the contract? | Inadmissible — it protects a different asset |
| 3 | Does it sit in an **IN SCOPE** row of the contract? | Stop — either it is irrelevant, or the contract is wrong and must be **amended explicitly** |
| 4 | Does it apply to the **same consumer set**? | Inadmissible — it protects different callers |
| 5 | Is it on the **current base-branch HEAD**, in the **shipping** flavor? | Inadmissible — stale or non-shipping |

> **"Adjacent in the same app" is not evidence of anything.** Two subsystems can sit in one process, one
> package, even one file, and still be different trust domains. Proximity is not a path — the hop-by-hop
> call chain is.

### Amending the contract

Contracts are allowed to be wrong — they are a hypothesis. What is **not** allowed is silently drifting
out of one. To amend:

1. State the amendment explicitly: *"Amending scope: `<X>` moves OUT OF SCOPE → IN SCOPE because `<call
   path>`."*
2. **Re-evaluate every claim** whose verdict depended on the old boundary (the ledger tells you which).
3. Record the amendment in the report. An amendment mid-investigation is a **Confidence: Medium** signal
   at best — the ground moved under earlier conclusions.

---

## Part 2 — The Claim Ledger

Every assertion that could move severity is a **numbered claim**, recorded **verbatim**, and carried
across passes **without rewording**.

```markdown
## Claim Ledger

| ID | Claim (VERBATIM — never reword) | Channel/subsystem | Evidence | Pass 2 result | Status |
|----|--------------------------------|-------------------|----------|---------------|--------|
| C1 | "<exact claim text as first written>" | <channel from contract> | `<file:line>` | upheld / refuted / untested | **UPHELD** |
| C2 | "<exact claim text>" | <channel> | searches proving absence | refuted — <how> | **RETIRED** |
| C3 | "<exact claim text>" | <channel> | — | not reached (time budget) | **OPEN** |
```

Rules:

1. **Verbatim carry-forward.** The challenger prompt must contain the claim's **exact text**, copied, in
   quotes, with its channel tag. Do not summarize it, "clarify" it, or make it more precise. If a claim is
   too vague to challenge, that is a **defect in the claim** — go fix C*n* itself, in the ledger, and note
   the edit. Never fix it in-flight inside the challenge prompt.
2. **One claim per challenge.** Bundling invites the challenger to answer the easiest one and generalize.
3. **Tag every claim with its channel/subsystem** from the Scope Contract. A claim without a channel tag
   cannot be challenged, because "cross-app credential theft" is ambiguous across channels — which is
   precisely how the real failure happened.
4. **Untested ≠ refuted.** A claim the challenger ran out of budget on is **OPEN**, not disproven. Only an
   evidenced attack retires a claim. This distinction is load-bearing: "we didn't confirm it" silently
   becoming "we ruled it out" is the same failure in a different costume.
5. **Severity moves only on ledger transitions.** If you are about to change a tier or Sev, point at the
   claim IDs that changed status. If you cannot, you are reacting to a *narrative*, not to evidence.

### The strawman check — run before accepting ANY refutation

A refutation is only valid if it attacked the actual claim. Check all four:

| # | Check | Failure looks like |
|---|-------|--------------------|
| 1 | **Verbatim match** — does the refutation quote the claim as written? | The claim got "clarified" in the challenge prompt |
| 2 | **Same channel** — same subsystem tag as the ledger row? | The challenge names a different channel/module |
| 3 | **Same asset & consumers** — same credential/data, same caller set? | Claim was about account type A, refutation is about B |
| 4 | **New-noun test** — does the challenge introduce any component **not** in the claim and **not** IN SCOPE? | A module appears in the challenge that appears nowhere in the contract |

> **Check 4 is the cheap one and it would have caught the real failure by itself.** Diff the nouns in the
> challenge prompt against (claim text ∪ Scope Contract IN SCOPE). Any noun that appears in neither is a
> **scope leak** — stop and re-issue the challenge. It is a 10-second check that catches a category error
> no amount of grepping will.

**If any check fails: the refutation is VOID.** Do not average it in, do not treat it as partial evidence,
do not let it lower Confidence. Restore the claim's prior status, re-issue the challenge against the
verbatim claim, and note the void attempt in the report — a voided refutation is a useful signal that the
claim is easy to misread.

### Reconciliation, corrected

The earlier rule ("the challenger wins by default") is **only** true for refutations that pass the
strawman check. Corrected order of operations:

1. Run the **strawman check** on each refutation.
2. **Void** any that fail; re-issue against the verbatim claim.
3. For surviving refutations, the challenger wins by default (it had strictly more information).
4. Disagreement that survives ⇒ scoped reconciliation pass + **Confidence: Low**, both conclusions kept.

> A challenger that "wins" on a strawman is worse than no challenger at all: it converts an open question
> into false confidence, and it does so with citations attached.

---

## Report requirements

Every finding report carries both artifacts, and the HTML research page surfaces them:

- `## Scope Contract` — near the top, before the analysis.
- `## Claim Ledger` — with every claim's final status and any **VOID** refutations noted.
- Any **scope amendment**, stated explicitly with its re-evaluation.

`scripts/lint_finding.py` checks these exist and flags claims with no channel tag. It cannot judge whether
the reasoning is right — that is what the checks above are for — but it makes a *missing* contract or
ledger impossible to ship.
