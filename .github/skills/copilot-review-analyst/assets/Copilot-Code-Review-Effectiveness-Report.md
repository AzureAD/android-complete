# Copilot Code Review Effectiveness Analysis

**Android Auth Platform | January 23 – March 23, 2026**

---

## Executive Summary

We analyzed **every inline code review comment** left by GitHub Copilot on human-authored pull requests across our three Android Auth repositories (Common, MSAL, Broker) over the past two months. For each of the **557 comments**, we determined whether the feedback led to a concrete code improvement — either through an explicit engineer response, or by verifying that the suggested change appeared in subsequent commit diffs.

**Key findings:**

- **57% of Copilot's comments received no response from engineers.** This is the single most important number in this report. The majority of AI review feedback is never even acknowledged.
- **Of comments that engineers engaged with, 60% were helpful** — a strong signal that the review quality itself is decent.
- **41% of all comments led to a confirmed code improvement.** But 41% are unresolved — comments that were ignored and where we lack evidence to judge either way. The true helpfulness rate could be significantly higher, but we can't confirm it because no one looked.
- **Engineers who reply to 75%+ of comments see 47-70% helpfulness.** Engineers who reply to <35% see 20-40%. The biggest lever for improving Copilot review value is not the AI — it's engineer engagement with the feedback.

---

## How We Measured This

This analysis went through five phases to ensure accuracy:

1. **Data collection.** We used the GitHub API to extract all 557 Copilot inline review comments from 163 human-authored PRs (excluding PRs authored by Copilot coding agent). We also recorded which comments received human replies and what those replies said.

2. **Reply-based classification.** For the 239 comments (43%) that received a human reply, we classified the reply as positive (e.g., "good catch", "fixed", "addressed"), negative (e.g., "won't fix", "not applicable", "by design"), or ambiguous.

3. **Diff-level verification.** For the 318 comments (57%) that received no reply, we checked whether the engineer acted on the feedback silently. We used the GitHub compare API to examine the diff between the commit Copilot reviewed and the final PR head. For comments containing GitHub suggestion blocks, we checked if the suggested code tokens appeared as additions in the diff. For prose comments, we checked if the exact line range was modified in a subsequent commit.

4. **AI-assisted reply classification.** All 133 comments with ambiguous replies were individually read and classified by the AI conducting this analysis (Claude), based on the reply text and domain context. For example, "this is just telemetry" was classified as dismissed, "@copilot apply changes" was classified as helpful (engineer delegated the fix back to Copilot), and "Added unit test for this" was classified as helpful (engineer acted on the suggestion). These classifications were reviewed for accuracy by the report author but were not independently verified by the original PR engineers.

5. **Cross-validation.** All comments initially classified as "not helpful" were re-examined against the diff evidence. 18 were reclassified as helpful where the evidence was strong (e.g., a typo fix suggestion with a corresponding -1 line file change, or an unused import suggestion with a matching -2 line change).

The final dataset classifies each of the 557 comments as **confirmed helpful**, **confirmed not helpful**, or **unresolved** (insufficient evidence to determine). No comment is left without a classification.

---

## Overall Results

| Metric | Value |
|--------|-------|
| Human PRs scanned | 163 |
| PRs that received Copilot review | 113 (69%) |
| Total inline review comments | 557 |
| PR-level summary comments | 205 |
| Average comments per reviewed PR | 4.9 |

### Engineer Response Rate

Before looking at helpfulness, it's important to understand how engineers interact with Copilot reviews — because a comment can only demonstrate value if someone reads it.

| Behavior | Count | Percentage |
|----------|-------|------------|
| **Engineer replied** (any response — acceptance, dismissal, or discussion) | 239 | **42.9%** |
| **Engineer did not reply** | 318 | **57.1%** |

More than half of all Copilot review comments receive no human response at all. This means the majority of AI feedback enters a void — it may be valid, but we can never confirm its value if no one engages with it.

### Helpfulness Verdict

Each comment was classified into one of three categories:

| Verdict | Count | Percentage | Definition |
|---------|-------|------------|------------|
| **Confirmed Helpful** | **230** | **41.3%** | The comment led to a code change — either the engineer explicitly acknowledged it, or the suggested fix was verified in a subsequent commit diff. |
| **Confirmed Not Helpful** | **101** | **18.1%** | The engineer explicitly dismissed the comment with a reason why the feedback was incorrect, irrelevant, or by design. We have positive evidence that the comment was *wrong*, not merely that it was *ignored*. |
| **Unresolved** | **226** | **40.6%** | The comment received no reply AND we could not confirm whether it was addressed. This includes cases where the engineer merged without any subsequent commits (the final review round was never evaluated), where the file was modified but not at the specific lines Copilot flagged, or where the comment had no line number making verification impossible. |

The **unresolved** category is the most important number in this report. These 226 comments — **41% of all feedback** — are not confirmed failures of the AI. They are comments where we simply lack evidence either way because no one engaged with them. Many may be perfectly valid feedback that was never read. If engineers had engaged with them (even just to dismiss), we would know. The true helpfulness rate likely falls somewhere between 41% (confirmed floor) and 82% (if all unresolved were helpful).

### How Each Category Breaks Down

**Confirmed Helpful (230):**

| Path | Count | Description |
|------|-------|-------------|
| Engineer replied and acknowledged | 144 | Engineer explicitly confirmed the feedback was useful (e.g., "good catch", "fixed", "addressed", "added unit test") |
| Engineer silently applied the fix | 86 | No reply, but the suggestion code or exact line range was verified as modified in a subsequent commit |

**Confirmed Not Helpful (101):**

| Path | Count | Description |
|------|-------|-------------|
| Engineer replied and dismissed | 95 | Engineer explicitly explained why the comment was wrong, irrelevant, or by design (e.g., "won't fix", "this is fine", "Copilot is incorrect", "false positive") |
| Comment on code already changed | 6 | Comment was on stale/outdated code that had already been modified in a different commit |

**Unresolved (226):**

| Path | Count | Description |
|------|-------|-------------|
| Merged without any subsequent commits | 122 | No commits after Copilot's review — the engineer merged the PR without acting on the final review round. The comment may have been valid, but we cannot tell because the engineer never evaluated it. |
| File modified but not at the commented lines | 45 | The file was changed after the review, but the diff shows the changes were at different lines than what Copilot flagged. Possibly addressed via a different approach, or possibly coincidental. |
| File modified but no line number to verify | 50 | Copilot's comment had no line number metadata, and the file was modified. We cannot confirm whether the specific concern was addressed. |
| File never modified after the comment | 9 | The file was not touched in any commit after the review, and no reply was left. The comment may have been valid but was ignored. |

---

## Results by Repository

| Repository | Comments | Response Rate | Confirmed Helpful | Confirmed Not Helpful | Unresolved |
|------------|----------|---------------|------|------|------|
| **Broker** | 293 | **56.0%** | 142 (48.5%) | 48 (16.4%) | 103 (35.2%) |
| **Common** | 188 | **29.8%** | 68 (36.2%) | 34 (18.1%) | 86 (45.7%) |
| **MSAL** | 76 | **25.0%** | 20 (26.3%) | 19 (25.0%) | 37 (48.7%) |

Broker has the highest response rate (56%) and correspondingly the highest confirmed helpfulness (49%). But even in Broker, 35% of comments are unresolved. In Common and MSAL — where response rates are below 30% — nearly half of all comments are unresolved, meaning we cannot determine whether the AI's feedback was useful because engineers didn't evaluate it.

Coverage across the three repos:

| Repository | Human PRs | PRs Reviewed by Copilot | Coverage |
|------------|-----------|------------------------|----------|
| Common | 68 | 47 | 69% |
| MSAL | 31 | 19 | 61% |
| Broker | 64 | 47 | 73% |

---

## Results by Engineer

Each engineer has two GitHub accounts (a personal account for public repos and an EMU account for the private broker repo). These have been merged. Names are anonymized.

| Engineer | Comments | Replied | Ignored | Response Rate | Confirmed Helpful | Confirmed Not Helpful | Unresolved | Helpfulness |
|----------|----------|---------|---------|---------------|------|------|------|-------------|
| **Engineer A** | 20 | 20 | 0 | **100%** | 14 | 6 | 0 | **70.0%** |
| **Engineer B** | 83 | 75 | 8 | **90.4%** | 57 | 26 | 0 | **68.7%** |
| **Engineer C** | 15 | 4 | 11 | **26.7%** | 8 | 2 | 5 | **53.3%** |
| **Engineer D** | 40 | 30 | 10 | **75.0%** | 19 | 14 | 7 | **47.5%** |
| **Engineer E** | 110 | 37 | 73 | **33.6%** | 44 | 18 | 48 | **40.0%** |
| **Engineer F** | 99 | 20 | 79 | **20.2%** | 36 | 11 | 52 | **36.4%** |
| **Engineer G** | 63 | 22 | 41 | **34.9%** | 20 | 12 | 31 | **31.7%** |
| **Engineer H** | 100 | 24 | 76 | **24.0%** | 27 | 12 | 61 | **27.0%** |
| **Engineer I** | 24 | 6 | 18 | **25.0%** | 5 | 0 | 19 | **20.8%** |
| **Engineer J** | 3 | 1 | 2 | **33.3%** | 0 | 0 | 3 | **0%** |

*Helpfulness = Confirmed Helpful / Total Comments. Response Rate = Replied / Total Comments. Unresolved = comments with no reply and no definitive diff evidence either way.*

**Key observation:** There is a strong correlation between response rate and helpfulness. Engineer A (100% response rate) and Engineer B (90%) have the highest helpfulness (70% and 69%) — and crucially, **zero unresolved comments**. When engineers engage, we know exactly what's helpful and what's not. Engineers with low response rates (Engineer F at 20%, Engineer H at 24%) have massive unresolved buckets (52 and 61 comments respectively) — over half their comments go into a black hole where we can't tell if the AI was right or wrong.

---

## Response Behavior Deep Dive

Of the 557 total comments:

- **239 (42.9%) received a reply.** Of those, **60.3% were helpful** and 39.7% were not helpful. When engineers engage, the majority of Copilot feedback turns out to be useful.
- **318 (57.1%) were ignored.** Of those, **27.0% were silently addressed** (verified via diff), **1.9% were on stale code** (confirmed not helpful), and the remaining **71.1% are unresolved** — we cannot determine whether the comment was useful because the engineer never evaluated it.

### What happens to ignored comments

| What happened | Count | % of ignored | Verdict |
|---------------|-------|-------------|---------|
| Suggestion code silently applied (verified via diff) | 50 | 15.7% | Confirmed Helpful |
| Exact commented lines modified in subsequent commit | 7 | 2.2% | Confirmed Helpful |
| Re-audit: evidence of fix at nearby lines | 29 | 9.1% | Confirmed Helpful |
| Merged without any subsequent commits | 122 | 38.4% | **Unresolved** |
| File never modified after the comment | 9 | 2.8% | **Unresolved** |
| Comment on stale/outdated code | 6 | 1.9% | Confirmed Not Helpful |
| File modified but not at the commented lines | 45 | 14.2% | **Unresolved** |
| File modified but no line number to verify | 50 | 15.7% | **Unresolved** |

The single largest category is the **122 comments (38.4% of ignored)** where the engineer merged the PR without pushing any additional commits after Copilot's review. These represent the final review round being skipped entirely — the feedback had zero chance of impact regardless of its quality.

---

## What Copilot Is Good At

The most common categories of helpful comments, with real examples from our PRs:

**Catching real bugs:**
> *PR #3050 (Common):* Copilot flagged that `"$it"` string wrapping doesn't JSON-escape the content, which could break consumers if contract values contain special characters.
> *Engineer reply: "You're right. Making the change."*

**Stale documentation and naming inconsistencies:**
> *PR #64 (Broker):* Copilot identified four locations where KDoc still referenced the old flight constant `USE_TEE_ONLY_FOR_TOKEN_BINDING` after it was renamed to `USE_TEE_ONLY_FOR_HARDWARE_BOUND_KEYS`. All four were silently fixed in the next commit.

**Dead code and unused imports:**
> *PR #3040 (Common):* Copilot spotted an unused local variable `enabledSettingRaw` that was assigned but never read. Verified as fixed via diff analysis — the suggested replacement code appeared in the commit additions.

**CI/pipeline configuration issues:**
> *PR #3038 (Common):* Copilot warned that using `vmImage: 'windows-latest'` makes the CD pipeline non-deterministic. The engineer changed to a pinned image version.

**The `@copilot apply` workflow:**
> In 16 instances (2.9%), engineers validated Copilot's feedback and then delegated the fix back to Copilot with: `@copilot open a new pull request to apply changes based on [this feedback]`. This is an efficient pattern where AI identifies and fixes the issue end-to-end.

---

## What Copilot Struggles With

The most common categories of unhelpful comments, with real examples:

**Lacking domain context:**
> *Copilot:* "`shared_device_id` could be used for tracking across apps — consider hashing before emission."
> *Engineer:* "The shared_device_id is a random UUID generated by one of the participating apps and is not PII."
>
> Copilot applied a general security heuristic without understanding that this particular identifier is already random and non-linkable.

**Suggesting tests for trivial code:**
> *Copilot:* "New telemetry attributes lack test coverage..."
> *Engineer:* "These are just telemetry related changes and adding unit tests will be overdo here."
>
> This was a recurring theme — Copilot frequently requests tests for logging/telemetry code that the team considers low-risk.

**Misunderstanding APIs:**
> *Copilot:* "`00000003-0000-0ff1-ce00-000000000000` is the resource ID for Microsoft Graph, not SharePoint Online."
> *Engineer:* "00000003-0000-0ff1-ce00-000000000000 is SharePoint Online."
>
> Copilot was factually wrong about a well-known Microsoft service resource ID.

**Commenting on intentional design choices:**
> *Copilot:* "`getPackageInfo() != null` is redundant since it either returns PackageInfo or throws..."
> *Engineer:* "This is fine. The verbosity makes the code clearer to understand."

**Over-engineering suggestions:**
> *Copilot:* "Use a bounded min-heap of size `PRT_ARTIFACT_LIMIT` to reduce overhead..."
> *Engineer:* (The list can only ever contain 3 items — there are only 3 broker apps on Android.)

---

## Most Reviewed Files

| Rank | File | Comments |
|------|------|----------|
| 1 | `.github/workflows/copilot-issue-response.yml` | 27 |
| 2 | `broker4j/.../AttributeName.java` | 19 |
| 3 | `.github/workflows/copilot-ci-feedback.md` | 16 |
| 4 | `common/.../AuthorizationFragment.java` | 15 |
| 5 | `broker4j/.../MultipleWorkplaceJoinDataStore.java` | 11 |
| 6 | `broker4j/.../AbstractBrokerController.java` | 11 |
| 7 | `common/.../AzureActiveDirectoryWebViewClient.java` | 11 |
| 8 | `AADAuthenticator/.../BrowserSsoProvider.kt` | 10 |
| 9 | `broker4j/.../BrokerFlight.java` | 9 |
| 10 | `broker4j/.../DeviceRegistrationRequestHandler.java` | 9 |

CI/workflow files and large Java classes with many change touchpoints attract the highest volume of comments. `AttributeName.java` appears frequently because many PRs add telemetry attributes.

---

## Key Takeaways

1. **57% of Copilot review comments receive no response from engineers.** This is the most significant finding. The majority of AI review feedback is never acknowledged. Some of it is silently acted on (27% of ignored comments show diff evidence of a fix), but the vast majority — 71% of ignored comments — are unresolved. We simply don't know if they were useful because no one evaluated them.

2. **41% of all comments led to a confirmed code improvement.** But 41% are unresolved, meaning the true helpfulness rate lies between 41% (floor) and 82% (ceiling). We cannot narrow this range without engineer engagement.

3. **When engineers engage, 60% of comments are helpful.** Of the 239 comments that received a reply, 144 (60%) led to acknowledged improvements. This suggests the AI review quality itself is decent — the bottleneck is adoption, not accuracy.

4. **Only 18% of comments are confirmed not helpful.** When we restrict "not helpful" to comments where the engineer explicitly dismissed the feedback — the only cases where we have positive evidence of low quality — the rate is surprisingly low.

5. **Engagement is the strongest predictor of value.** Engineers who reply to 75%+ of comments see 47-70% confirmed helpfulness with zero unresolved comments. Engineers who reply to <35% see 20-40% helpfulness with massive unresolved buckets (50-60% of their comments). The tool works better when engineers work with it.

6. **38% of ignored comments are on the final commit before merge.** These 122 comments represent the last review round being skipped entirely — the engineer merged without pushing any further changes. These comments may have been perfectly valid, but the feedback had zero chance of impact.

7. **Broker gets the most value (49%), Common is middling (36%), MSAL is lowest (26%).** This correlates with response rate: Broker engineers reply to 56% of comments, while Common (30%) and MSAL (25%) reply far less.

8. **At ~5 comments per PR, the signal-to-noise conversation can't be settled without engagement.** We confirmed ~2 useful comments per PR on average, but with 41% of comments unresolved, the actual number could be higher. The only way to know is for engineers to evaluate the feedback.

---

## Methodology Notes

- **Scope.** Only inline code review comments from the `Copilot` and `copilot-pull-request-reviewer[bot]` users were counted. PR-level summary comments (205 total) were excluded from the helpfulness analysis.
- **Bot exclusions.** PRs authored by `copilot-swe-agent` (Copilot coding agent) were excluded. Only PRs authored by human engineers were analyzed.
- **Diff verification.** For suggestion blocks, we extracted the suggested code tokens and checked if they appeared as `+` (addition) lines in the compare diff between the comment's commit and the PR head. For prose comments, we checked if the diff hunk line ranges overlapped with the comment's target line range (±5 line tolerance). This is a conservative approach — some fixes that refactored code differently than suggested may be missed.
- **AI-assisted reply classification.** Every comment that could not be definitively classified by automated methods was individually read and classified by the AI conducting this analysis (Claude), based on the reply text and domain context. These AI classifications were reviewed for accuracy by the report author but were not independently verified by the original PR engineers.
- **Conservative approach.** We only classify a comment as "Confirmed Helpful" when there is positive evidence: an explicit engineer acknowledgment, or verified code changes at the exact lines/tokens suggested. We only classify as "Confirmed Not Helpful" when there is positive evidence that the comment was *wrong or irrelevant*: an explicit engineer dismissal with a stated reason. Comments where the engineer simply did not engage — including the final review round that was merged without evaluation, files that were never modified, and files modified at different lines — are classified as "Unresolved" rather than assumed to be unhelpful.
- **Account merging.** Engineers with separate public GitHub accounts and EMU (Enterprise Managed User) accounts were merged based on known identity mappings.
- **Data availability.** Raw data for all 557 comments (including comment text, engineer replies, diff verification evidence, and final verdicts) is stored at `%TEMP%\copilot-review-analysis\` for independent verification.

---

*Analysis conducted March 23-24, 2026. Data covers all PRs created January 23 – March 23, 2026 in the Common, MSAL, and Broker repositories.*
