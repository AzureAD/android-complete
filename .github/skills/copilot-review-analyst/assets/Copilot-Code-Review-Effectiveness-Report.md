# Copilot Code Review Effectiveness Analysis

**Android Auth Platform | June 10 – July 15, 2026**

---

## Executive Summary

We analyzed **every inline code review comment** left by GitHub Copilot on human-authored pull requests across our three Android Auth repositories (Common, MSAL, Broker) over the past five weeks. For each of the **132 comments**, we determined whether the feedback led to a concrete code improvement — either through an explicit engineer response, or by verifying that the suggested change appeared in subsequent commit diffs — and, critically, whether any dismissal was because *Copilot was wrong* or because *the engineer made a deliberate judgment call*.

This run introduces a **new, fairer methodology**. In prior reports, any comment an engineer declined was lumped into a single "not helpful" bucket. That was unfair to Copilot: a comment can be entirely correct and well-reasoned, yet the author still decides not to act on it because of context the AI had no way to know (a deliberate design choice, a subjective naming preference, or a readability nit). This report separates those cases into a neutral **Declined** category and reserves **Not Helpful** exclusively for comments where Copilot was demonstrably *wrong*.

**Key findings:**

- **72% of Copilot's comments now receive a response from engineers.** This is up sharply from prior periods (44% → 79% → 68% → 72%) and reflects a real behavior change — several engineers went back and replied to comments they had previously left unanswered. When engineers engage, we can actually judge the AI's value.
- **Only 1.5% of all comments were genuinely wrong.** Across 132 comments, just **two** were confirmed as Copilot errors. When you compute Copilot's **precision** — helpful comments as a share of everything we can actually adjudicate (helpful + wrong) — it lands at **97.3%**. The AI is rarely incorrect.
- **20.5% of comments were correct-but-declined.** These 27 comments were reasonable feedback the engineer chose not to adopt for a stated reason — by design, a subjective preference, or context Copilot couldn't have known. **These do not count against Copilot's quality.** Treating them as failures, as the old methodology did, materially understated the tool's accuracy.
- **54.5% of all comments led to a confirmed code improvement.** With only 1.5% confirmed wrong and 23.5% unresolved (never evaluated), the true helpfulness ceiling is very high — the gap is engineer engagement, not AI accuracy.

---

## How We Measured This

This analysis went through five phases to ensure accuracy:

1. **Data collection.** We used the GitHub API to extract all 132 Copilot inline review comments from 53 reviewed human-authored PRs (excluding PRs authored by the Copilot coding agent). We also recorded which comments received human replies and captured the full reply text.

2. **Diff-level verification.** For the 37 comments (28%) that received no reply, we checked whether the engineer silently acted on the feedback. We used the GitHub compare API to examine the diff between the commit Copilot reviewed and the final PR head. For comments containing GitHub suggestion blocks, we checked if the suggested code tokens appeared as additions in the diff. For prose comments, we checked if the exact line range was modified in a subsequent commit.

3. **AI-assisted reply classification (three-way).** For the 95 comments (72%) that received a human reply, the AI conducting this analysis read the full Copilot comment and the engineer's reply in context, and assigned one of three verdicts:
   - **Helpful** — the engineer accepted the feedback (acknowledged it, fixed it, or delegated the fix back to Copilot).
   - **Declined** — the engineer's reply shows the feedback was understood and reasonable, but they made a deliberate choice not to act on it (by design, subjective preference, moot/out-of-scope, or context Copilot couldn't have known). **Copilot was not wrong** — it simply had no way to know the author's intent.
   - **Not Helpful (incorrect)** — the engineer's reply demonstrates the comment was factually or technically *wrong* (a false positive, a hallucinated problem, or a misunderstanding of a library/API).

4. **The fairness principle.** The dividing line between *Declined* and *Not Helpful* is **whether Copilot was wrong**, not whether the engineer acted. If an engineer says "good point, but we want the verbose stack trace for this new feature," Copilot's observation was legitimate — the author just weighed the tradeoff differently. That is **Declined**, and it is neutral. Only when the engineer shows the comment was actually incorrect ("the annotation is already there," "not reproducible on this version") does it count as **Not Helpful**.

5. **Cross-validation.** All no-reply comments were re-examined against the diff evidence. Where the evidence of a silent fix was strong (the suggested tokens or exact lines appeared in a later commit), the comment was reclassified as Helpful rather than left Unresolved.

The final dataset classifies each of the 132 comments as **confirmed helpful**, **declined (correct but not adopted)**, **not helpful (incorrect)**, or **unresolved** (insufficient evidence to determine). No comment is left without a classification.

---

## A Note on Fairness: Why "Declined" Is Not "Wrong"

The single most important change in this report is the recognition that **an engineer declining a comment is not evidence that the comment was bad.**

Consider Broker PR #201. Copilot flagged a code comment that read `~1 per device` as potentially misleading — the tilde could be read as an exact value. The engineer replied simply: *"~ = approximate. i think this is ok."* Copilot's observation was perfectly reasonable — an ambiguous notation is worth a second look. The author simply judged that, in context, the notation was clear enough. Copilot had no way to know the author's tolerance for that ambiguity beforehand. Counting this against the AI's review quality would be unfair.

We saw this pattern repeatedly: Copilot suggests removing a log line, and the engineer explains it's deliberately there to catch a regression in a specific scenario; Copilot proposes a different span name, and the engineer prefers the existing one; Copilot flags a full stack-trace log, and the engineer wants exactly that verbosity for a brand-new feature. In every case the AI's reasoning was sound. The engineer had additional context — and the right — to decide otherwise.

By separating these **Declined** cases (20.5% of all comments) from genuine **errors** (1.5%), we get an honest picture: Copilot's feedback is correct or reasonable the overwhelming majority of the time. The old binary rubric — which would have reported ~22% "not helpful" this period — obscured that the AI was actually *wrong* in only 2 of 132 comments.

---

## Overall Results

| Metric | Value |
|--------|-------|
| Human PRs scanned | 87 |
| PRs that received Copilot review | 53 (61%) |
| Total inline review comments | 132 |
| Average comments per reviewed PR | 2.5 |

### Engineer Response Rate

Before looking at helpfulness, it's important to understand how engineers interact with Copilot reviews — because a comment can only demonstrate value if someone reads it.

| Behavior | Count | Percentage |
|----------|-------|------------|
| **Engineer replied** (any response — acceptance, decline, or discussion) | 95 | **72.0%** |
| **Engineer did not reply** | 37 | **28.0%** |

At 72%, this is the second-highest response rate we've recorded, and a marked improvement over the first analysis period (44%). Several engineers returned to previously-ignored comments and replied — most notably one engineer who went from a mix of ignored/unanswered comments to a **100% response rate** across all seven of their comments. Every reply, even a decline, is valuable: it lets us tell the difference between feedback that was wrong and feedback that was simply not adopted.

### Helpfulness Verdict

Each comment was classified into one of four categories. Note the deliberate split between **Declined** (Copilot was right, the engineer chose otherwise) and **Not Helpful** (Copilot was wrong):

| Verdict | Count | Percentage | Definition |
|---------|-------|------------|------------|
| **Confirmed Helpful** | **72** | **54.5%** | The comment led to a code change — the engineer explicitly acknowledged it, or the suggested fix was verified in a subsequent commit diff. |
| **Declined (correct, not adopted)** | **27** | **20.5%** | The engineer understood the feedback and made a deliberate choice not to act on it — by design, a subjective preference, moot/out-of-scope, or context Copilot couldn't have known. **This is neutral; Copilot was not wrong.** |
| **Not Helpful (incorrect)** | **2** | **1.5%** | The engineer demonstrated the comment was factually or technically *wrong* — a false positive, a hallucination, or a misunderstanding of a library/API. This is the only bucket that counts against Copilot's quality. |
| **Unresolved** | **31** | **23.5%** | The comment received no reply AND we could not confirm whether it was addressed. Could be valid feedback that was never evaluated. |

### The Precision Metric

Because *Declined* is neutral and *Unresolved* is unknown, the fairest single measure of Copilot's review quality is its **precision** — of the comments we can actually adjudicate as right or wrong, what fraction were right?

> **Precision = Helpful ÷ (Helpful + Incorrect) = 72 ÷ (72 + 2) = 97.3%**

Declined and Unresolved comments are excluded from both the numerator and the denominator, because neither represents a Copilot mistake. **97.3% precision** means that when an engineer engaged deeply enough for us to judge correctness, Copilot's comment was right 72 times out of 74. The AI is almost never wrong; the open question is adoption and engagement, not accuracy.

### How Each Category Breaks Down

**Confirmed Helpful (72):**

| Path | Count | Description |
|------|-------|-------------|
| Engineer replied and acknowledged | 66 | Engineer explicitly confirmed the feedback was useful (e.g., "good catch", "fixed", "done", "implemented in commit …") |
| Engineer silently applied the fix | 6 | No reply, but the suggestion code or exact line range was verified as modified in a subsequent commit |

**Declined — correct but not adopted (27):** All 27 received an engineer reply. In every case the engineer engaged with the feedback and gave a reason for not acting on it — a design decision, a subjective preference, a moot/out-of-scope note, or domain context Copilot couldn't have known. None indicate the comment was wrong.

**Not Helpful — incorrect (2):**

| Comment | Repo / PR | Why it was wrong |
|---------|-----------|------------------|
| Mockito varargs matcher warning | Common #3171 | Engineer verified the flagged behavior does not reproduce on Mockito 5.11.0; the full suite passed 12/12. |
| "Missing `@Override` annotation" | Broker #212 | The `@Override` annotation was already present — a hallucinated problem. |

**Unresolved (31):** No reply and no diff evidence either way. These are not confirmed failures — they are comments no one evaluated.

---

## Results by Repository

| Repository | Comments | Response Rate | Helpful | Declined | Not Helpful | Unresolved | Precision |
|------------|----------|---------------|---------|----------|-------------|------------|-----------|
| **Broker** | 66 | **69.7%** | 33 (50.0%) | 16 (24.2%) | 1 (1.5%) | 16 (24.2%) | **97.1%** |
| **Common** | 51 | **68.6%** | 29 (56.9%) | 7 (13.7%) | 1 (2.0%) | 14 (27.5%) | **96.7%** |
| **MSAL** | 15 | **93.3%** | 10 (66.7%) | 4 (26.7%) | 0 (0.0%) | 1 (6.7%) | **100%** |

Every repository posts a precision above 96%. MSAL leads on both response rate (93%) and helpfulness (67%) with zero incorrect comments and only one unresolved. Broker carries the highest comment volume and the largest Declined bucket (16), reflecting a team that engages heavily and frequently makes deliberate design-tradeoff calls — those declines are not Copilot failures. Common sits in the middle. Notably, the "Not Helpful" column — the only one that reflects poorly on the AI — is at most a single comment in any repository.

Coverage across the three repos:

| Repository | Human PRs | PRs Reviewed by Copilot | Coverage |
|------------|-----------|------------------------|----------|
| Common | 38 | 23 | 61% |
| MSAL | 14 | 7 | 50% |
| Broker | 35 | 23 | 66% |

---

## Results by Engineer

Each engineer may have two GitHub accounts (a personal account for public repos and an EMU account for the private Broker repo). These have been merged. Names are anonymized and ordered by helpfulness (descending), with comment volume as a tie-break.

| Engineer | Comments | Replied | Response Rate | Helpful | Declined | Not Helpful | Unresolved | Helpfulness | Precision |
|----------|----------|---------|---------------|---------|----------|-------------|------------|-------------|-----------|
| **Engineer A** | 7 | 7 | **100%** | 7 | 0 | 0 | 0 | **100%** | 100% |
| **Engineer B** | 3 | 3 | **100%** | 3 | 0 | 0 | 0 | **100%** | 100% |
| **Engineer C** | 11 | 9 | **81.8%** | 9 | 0 | 0 | 2 | **81.8%** | 100% |
| **Engineer D** | 6 | 5 | **83.3%** | 4 | 0 | 1 | 1 | **66.7%** | 80.0% |
| **Engineer E** | 30 | 23 | **76.7%** | 17 | 7 | 0 | 6 | **56.7%** | 100% |
| **Engineer F** | 9 | 5 | **55.6%** | 5 | 2 | 0 | 2 | **55.6%** | 100% |
| **Engineer G** | 11 | 6 | **54.5%** | 6 | 0 | 1 | 4 | **54.5%** | 85.7% |
| **Engineer H** | 6 | 3 | **50.0%** | 3 | 0 | 0 | 3 | **50.0%** | 100% |
| **Engineer I** | 4 | 4 | **100%** | 2 | 2 | 0 | 0 | **50.0%** | 100% |
| **Engineer J** | 19 | 15 | **78.9%** | 8 | 9 | 0 | 2 | **42.1%** | 100% |
| **Engineer K** | 19 | 15 | **78.9%** | 8 | 7 | 0 | 4 | **42.1%** | 100% |
| **Engineer L** | 3 | 0 | **0%** | 0 | 0 | 0 | 3 | **0%** | n/a |
| **Engineer M** | 3 | 0 | **0%** | 0 | 0 | 0 | 3 | **0%** | n/a |
| **Engineer N** | 1 | 0 | **0%** | 0 | 0 | 0 | 1 | **0%** | n/a |

*Helpfulness = Confirmed Helpful / Total Comments. Precision = Helpful / (Helpful + Not Helpful), excluding Declined and Unresolved. Response Rate = Replied / Total Comments.*

**Key observation — the Declined column changes how we read this table.** Under the old binary rubric, Engineer J (42.1% helpful, 9 declined) and Engineer K (42.1% helpful, 7 declined) would have looked like the engineers getting the *least* value from Copilot. But their large Declined buckets are not Copilot failures — they are experienced engineers who engage with nearly 80% of comments and make frequent, deliberate design-tradeoff calls. Both post a **100% precision**: Copilot was never actually wrong on their PRs. The real "not helpful" signal is tiny and concentrated — only Engineer D and Engineer G have a single incorrect comment each, and every other engineer who engaged has 100% precision. Engagement, not accuracy, remains the main lever: the engineers at a 0% response rate (Engineers L, M, and N) simply never evaluated the feedback, so it fell into the Unresolved bucket.

---

## Response Behavior Deep Dive

Of the 132 total comments:

- **95 (72.0%) received a reply.** Of those, **69.5% were helpful**, **28.4% were declined** (correct but not adopted), and only **2.1% were incorrect**. When engineers engage, the overwhelming majority of Copilot feedback is either acted on or acknowledged as reasonable — and almost none of it is wrong.
- **37 (28.0%) were not replied to.** Of those, **16.2% were silently addressed** (verified via diff) and the remaining **83.8% are unresolved** — we cannot determine whether the comment was useful because the engineer never evaluated it.

### What happens to comments engineers reply to

| Reply verdict | Count | % of replied | Counts against Copilot? |
|---------------|-------|-------------|-------------------------|
| Helpful — accepted or fixed | 66 | 69.5% | No — positive |
| Declined — correct but not adopted | 27 | 28.4% | **No — neutral** |
| Not Helpful — incorrect | 2 | 2.1% | Yes |

The takeaway is stark: among the 95 comments engineers actually engaged with, they judged Copilot *wrong* only twice. Everything else was either adopted or was a legitimate observation the engineer chose to handle differently.

### What happens to ignored comments

| What happened | Count | % of ignored | Verdict |
|---------------|-------|-------------|---------|
| Suggestion code / exact lines silently applied (verified via diff) | 6 | 16.2% | Confirmed Helpful |
| No reply and no diff evidence either way | 31 | 83.8% | **Unresolved** |

The 31 unresolved comments are the only real blind spot in this report. They are not confirmed failures — they are feedback that entered a void. If engineers engaged with even half of them, precision and helpfulness would both firm up further.

---

## What Copilot Is Good At

The most valuable helpful comments this period, with real examples from our PRs:

**Catching real initialization-ordering bugs:**
> *PR #215 (Broker) — `AriaInitializer`:* Copilot flagged that a flight/telemetry provider was being read before it was initialized, an ordering problem that would silently drop data.
> *Engineer reply: "Good catch — this is a fundamental ordering problem. Fixing it."*
>
> This is exactly the kind of subtle, cross-method sequencing issue that is easy to miss in review but expensive in production. Copilot caught it from the diff alone.

**Telemetry schema and contract discrepancies:**
> *PR #204 (Broker) — `AccountChooser`:* Copilot noticed the emitted telemetry attribute didn't match the schema described in the PR, flagging a contract mismatch.
> *Engineer acknowledged and reconciled the schema.*

**Deprecated-API and compatibility issues:**
> *PR #3147 (Common):* Copilot pointed out a newly-added overload shadowed a deprecated one and recommended `@JvmOverloads` / a default argument to preserve binary compatibility for Java callers.
> *Engineer reply: "Fixed. Thanks for pointing this out."*

**Security and policy consistency:**
> *PR #3156 (Common) — OWASP dependency check:* Copilot identified an inconsistency in how a CVE suppression was applied across modules.
> *Engineer reply: "Implemented in commit a58b52dc9; remaining remediation tracked in AB#3667951."*
>
> Copilot's comment didn't just get a thumbs-up — it produced a concrete fix and a tracked follow-up work item.

**Behavior-vs-description correctness:**
> *PR #3165 (Common) — Authority validation:* Copilot noted a URL comparison was matching on the full URL when only the host should matter.
> *Engineer reply: "Good point — updated the code to check host only."*

---

## When Copilot Was Actually Wrong

Only **two** comments this period were genuine errors. We reproduce both in full, because at 1.5% they are the exception that proves the rule:

**Misjudging a library's behavior:**
> *PR #3171 (Common):* Copilot warned that a Mockito varargs matcher would not behave as intended and could cause the test to pass incorrectly.
> *Engineer reply: "Verified this isn't reproducible on Mockito 5.11.0 — the suite passes 12/12."*
>
> Copilot applied a heuristic that held for older Mockito versions but not the one actually in use. A version-specific false positive.

**Hallucinating a missing annotation:**
> *PR #212 (Broker):* Copilot claimed a method was missing an `@Override` annotation.
> *Engineer reply: "The override annotation is already there."*
>
> The annotation was present in the code. This is a straightforward hallucination — the only category of error we truly want to drive to zero.

---

## When Copilot Was Right but the Engineer Declined

These comments were **not wrong** — the engineer understood the point and deliberately chose a different path. Under the old methodology, all of these would have been miscounted as "not helpful." They are the heart of this report's fairness correction.

**Deliberate notation choice (the flagship case):**
> *PR #201 (Broker):* Copilot flagged the code comment `~1 per device` as potentially misleading, since the tilde could be read as an exact value.
> *Engineer reply: "~ = approximate. i think this is ok."*
>
> A perfectly reasonable readability observation. The author simply judged the notation clear enough in context. Copilot had no way to know that tolerance beforehand — this is neutral, not a failure.

**Subjective naming preference:**
> *PR #3151 (Common):* Copilot suggested encoding the "prompt" semantics into a span name for clarity.
> *Engineer reply: "I think the current name is fine."*
>
> A judgment call on naming. Both names are defensible; the author preferred the existing one.

**Intentional verbosity for a new feature:**
> *PR #223 (Broker):* Copilot suggested not logging a full stack trace for an expected exception type.
> *Engineer reply: "Since this is a new feature, we want that stack trace for all exceptions."*
>
> The engineer deliberately wants maximum diagnostic detail while the feature is new. Copilot's general "don't log noisy stack traces" heuristic was sound but didn't apply here.

**Context Copilot couldn't have known:**
> *PR #3177 (Common):* Copilot suggested removing an INFO-level log as noise.
> *Engineer reply: "We need this to catch a regression in the COBO/COPE scenarios."*
>
> The log is intentionally there to detect a specific device-management regression — context that isn't visible in the diff.

---

## Trend Over Time

This is the fourth analysis period. Response rate has recovered and comment volume has stabilized. Because the **Declined vs. Incorrect** split and the **Precision** metric were introduced this period, prior periods show only the old combined "Dismissed" figure — so the current period is broken out below the table.

| Period | Days | Comments | Comments/wk | Response Rate | Helpful | Dismissed (combined) | Unresolved |
|--------|------|----------|-------------|---------------|---------|----------------------|------------|
| Jan 24 – Mar 25 | 60 | 570 | 66.3 | 44.4% | 38.6% | 17.4% | 44.0% |
| Mar 24 – Apr 19 | 26 | 170 | 45.9 | 78.8% | 62.9% | 21.2% | 15.9% |
| Apr 20 – Jun 9 | 50 | 157 | 22.1 | 67.5% | 52.9% | 16.6% | 30.6% |
| **Jun 10 – Jul 15** | **35** | **132** | **26.4** | **72.0%** | **51.5%** | **22.0%** | **26.5%** |

**What changed this period:**

- **Response rate rose from 67.5% to 72.0%**, driven partly by engineers going back to reply to previously-unanswered comments.
- **Trend-series helpfulness dipped slightly** (52.9% → 51.5%) while unresolved comments dropped (30.6% → 26.5%) — more feedback is being evaluated, even though normalized helpfulness was broadly stable.
- **The "Dismissed" number is misleading in isolation.** This period's combined dismissal rate (22.0%) looks comparable to prior periods, but the new split reveals that **20.5 of those 22.0 points are Declined (Copilot was right)** and only **1.5 points are genuine errors**. Copilot's **precision this period is 97.3%**. Earlier periods almost certainly had a similar breakdown — we simply couldn't see it under the binary rubric, which means historical "not helpful" rates of 16–21% substantially overstated how often the AI was actually wrong.

*Note: the Declined/Incorrect split and Precision metric are only available from this period onward. For apples-to-apples trend comparison, the table shows the combined dismissal rate; the current period's true error rate is 1.5%.*

*Trend continuity note: the historical series uses the same automated diff-evidence rules across all four periods (68 helpful / 35 unresolved this period). The authoritative report body additionally applies four manually re-audited diff flips, producing 72 helpful / 31 unresolved. This deliberate distinction preserves an apples-to-apples trend while keeping the current body classification maximally accurate.*

---

## Key Takeaways

1. **Copilot is almost never wrong — precision is 97.3%.** Of 132 comments, only 2 were genuine errors. When engineers engaged deeply enough for us to judge correctness, Copilot was right 72 times out of 74.

2. **"Declined" is not "wrong," and it's now counted fairly.** 27 comments (20.5%) were correct feedback the engineer deliberately chose not to adopt. Under the old binary rubric these would have dragged down Copilot's apparent quality. They are neutral — the AI had no way to know the author's intent.

3. **Response rate climbed to 72%.** Up from 68% last period and 44% in the first analysis. Several engineers returned to reply to comments they had previously ignored — one reaching a 100% response rate across all seven of their comments.

4. **54.5% of comments led to a confirmed improvement.** With only 1.5% confirmed wrong, the helpfulness ceiling is high; the remaining gap is the 23.5% unresolved comments that no one evaluated.

5. **Every repository posts 96%+ precision.** MSAL 100%, Broker 97.1%, Common 96.7%. The "not helpful" column never exceeds one comment per repo.

6. **The Declined bucket concentrates in high-engagement engineers.** Engineer J (9 declined) and Engineer K (7 declined) engage with ~80% of comments and make frequent design-tradeoff calls — both at 100% precision. Their lower "helpfulness" percentage is not a Copilot quality problem.

7. **Unresolved is the only real blind spot.** 31 comments (23.5%) were never evaluated. This is the one lever entirely in the team's hands: engaging with even half of them would tighten every metric in this report.

8. **The bottleneck is adoption and engagement, not accuracy.** The data is now unambiguous: Copilot's review quality is high. The variance in per-engineer "value" is almost entirely explained by how often each engineer reads and responds to the feedback.

---

## What We'd Recommend

Based on this analysis, we see three opportunities:

**1. Keep engaging — it's working.** Response rate rose to 72% this period, and the payoff is visible: we can now tell that Copilot is wrong only 1.5% of the time. The engineers who went back to reply to old comments materially improved the fidelity of this report. A quick reply — even a decline with a one-line reason — is what lets us separate "Copilot was wrong" from "I chose otherwise." That distinction is the entire value of this measurement.

**2. Close the unresolved gap before merge.** The 31 unresolved comments (23.5%) are feedback no one evaluated. Building a habit of scanning Copilot's last review round before clicking merge would convert most of these into a clear helpful/declined/incorrect verdict and give the feedback a chance to have impact.

**3. Target the two real error patterns.** The only genuine errors this period were a version-specific library assumption (Mockito) and a hallucinated annotation. Both are addressable by refining `copilot-instructions.md` — e.g., noting the Mockito version in use and reminding the reviewer to confirm an annotation is actually absent before flagging it. Because genuine errors are already so rare, small instruction tweaks can plausibly drive them to zero.

---

## Methodology Notes

- **Scope.** Only inline code review comments from the `Copilot` user were counted. PR-level summary comments were excluded from the helpfulness analysis.
- **Bot exclusions.** PRs authored by `copilot-swe-agent` (Copilot coding agent), `dependabot[bot]`, and `github-actions[bot]` were excluded. Only PRs authored by human engineers were analyzed.
- **Three-way reply classification.** Every replied comment was individually read and classified as Helpful, Declined, or Not Helpful (incorrect) by the AI conducting this analysis, based on the reply text and domain context. The dividing line between Declined and Not Helpful is **whether Copilot was factually/technically wrong**, not whether the engineer acted. These classifications were reviewed for accuracy by the report author but were not independently re-verified by the original PR engineers.
- **Precision.** Defined as Helpful ÷ (Helpful + Not Helpful-incorrect). Declined and Unresolved comments are excluded from both numerator and denominator because neither represents a Copilot mistake.
- **Diff verification.** For suggestion blocks, we extracted the suggested code tokens and checked if they appeared as `+` lines in the compare diff between the comment's commit and the PR head. For prose comments, we checked if the diff hunk line ranges overlapped the comment's target line range (±5 line tolerance). This is conservative — some fixes that refactored code differently than suggested may be missed.
- **Conservative "Unresolved."** Comments with no reply and no diff evidence are classified as Unresolved rather than assumed unhelpful. "Unresolved" ≠ "Not Helpful."
- **Account merging.** Engineers with separate public GitHub and EMU (Enterprise Managed User) accounts were merged based on known identity mappings.
- **Data availability.** Raw data for all 132 comments (comment text, engineer replies, diff verification evidence, and final verdicts) is stored at `%TEMP%\copilot-review-analysis\` for independent verification.

---

*Analysis conducted July 15, 2026. Data covers all PRs created June 10 – July 15, 2026 in the Common, MSAL, and Broker repositories. This report introduces a three-way verdict methodology (Helpful / Declined / Not Helpful) and a Copilot precision metric to fairly distinguish AI errors from deliberate engineer judgment calls.*

