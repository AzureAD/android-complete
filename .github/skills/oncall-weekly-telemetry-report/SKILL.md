---
name: oncall-weekly-telemetry-report
description: Generate the weekly Android on-call (OCE) WoW + 60-day trend telemetry reports as polished self-contained HTML. Produces TWO reports by default — the Android Broker report (from `android_spans` materialized views) and the Authenticator app report (from the Authenticator scenario materialized views) — plus a combined index page. Use this skill for the weekly OCE rotation when asked to "produce the OCE report", "weekly on-call report", "WoW telemetry report", "weekly broker health report", "authenticator weekly report", "authapp telemetry report", or "generate this week's on-call summary". Supports modes `both` (default), `broker`, and `authapp`. Writes to `$env:USERPROFILE\android-oce-reports\` (outside the workspace so reports are never committed).
---

# OCE Weekly Report — router

This skill produces the weekly Android on-call telemetry reports. It is a **router**: it resolves
the reporting window, decides which app playbook(s) to run, and stitches the results together into
a combined index. **All app-specific analysis lives in the playbooks** — read the one(s) you need
after resolving the mode.

| Playbook | Covers | Read when mode is |
|---|---|---|
| [`assets/playbooks/broker.md`](assets/playbooks/broker.md) | Android Broker — error codes/types, spike + code attribution, latency, broker version adoption | `broker`, `both` |
| [`assets/playbooks/authapp.md`](assets/playbooks/authapp.md) | Authenticator app — scenario funnels (Passkey / Entra MFA / Entra PSI / MSA NGC+SA), error reasons, abandonment, Broker API responsiveness, crash rate | `authapp`, `both` |

> **Do not read both playbooks into one context when running `both`.** Run them as two parallel
> sub-agents (see § Orchestration). Each playbook is large by design; interleaving them degrades
> both reports and risks cross-contaminating the two apps' incompatible Kusto conventions.

---

## Mode selection

**Default is `both`.** Infer the mode from the request; only ask if genuinely ambiguous.

| Signal in the request | Mode |
|---|---|
| *"the OCE report"*, *"weekly on-call report"*, *"this week's telemetry"*, no app named | `both` |
| *"broker report"*, *"broker health"*, *"error codes"*, *"spike attribution"* | `broker` |
| *"authenticator report"*, *"authapp"*, *"passkey/MFA/PSI/MSA scenarios"*, *"registration success rate"* | `authapp` |

Optional flags the user may add: `--skip-crashes` (Authenticator only — skips the App Center
crash layer, which needs a secret), `--end YYYY-MM-DD` (see § Reporting window).

---

## Reporting window (shared by both apps)

> **⚠️ Do NOT ask the user for a reporting date by default.** Both reports use the **same rolling
> 7-day window** ending at start-of-day UTC on the invocation day, so the most recent complete days
> are always captured and the two reports are directly comparable.
> [`assets/scripts/bootstrap-report.ps1`](assets/scripts/bootstrap-report.ps1) computes and stamps
> the window automatically. The previous "confirm the Sunday bucket with the user" flow silently
> produced stale windows (a Thursday run emitted a 4-day partial window and dropped the last
> complete week) — that failure mode is what this section explicitly guards against.

1. **Reporting window** — do NOT ask. Run `bootstrap-report.ps1`; it resolves the window silently
   and prints:
   ```
   Resolved reporting window (UTC):   # example values for a run on 2026-07-15
     Last 7 days:   2026-07-08 -> 2026-07-15  (exclusive upper bound)
     Baseline:      2026-07-01 -> 2026-07-08
     60-day trend:  2026-05-16 -> 2026-07-15  (literal 60d ending today; rolling 7d buckets anchored at curEnd)
     Trend buckets: 8 complete rolling weeks; final bucket == the Last-7-days window above (classifier WoW == displayed WoW)
     bucket-trends.js: --start=2026-05-16 --end=2026-07-15   (pass BOTH; --end disables the partial-end auto-drop heuristic)
     Sparkline (8 rolling weeks): 2026-05-20 -> 2026-07-15  (SPARK_START -> SPARK_END, exclusive; no Sunday alignment needed)
   ```
   > **The "classifier WoW == displayed WoW" clause is Broker-specific.** Broker's classifier and its
   > WoW tables count the same thing (error-code devices/requests), so aligning the buckets genuinely
   > makes the two numbers equal. **Authenticator aligns the *window*, not the *measure*:** its
   > classifier grades bad-outcome volumes (`Failed + Unknown`) while the scoreboard headline is a
   > success-**rate** delta in percentage points. Those can differ in magnitude, and can move the same
   > direction while telling opposite stories, purely from traffic shifts. In `authapp` mode
   > `bootstrap-report.ps1` prints the qualified wording. Do **not** diagnose an Authenticator sign
   > mismatch as a bucketing bug — check the printed bucket dates against the report window first; if
   > they agree, the difference is rate-vs-volume and belongs in the prose.
   These dates are stamped into each report's `<title>`, `<div class="meta">`, and Generated banner
   during bootstrap — you do not hand-edit them.

   **In `both` mode, resolve the window ONCE and pass the same `-EndDate` to both bootstrap calls.**
   Letting each playbook resolve its own window independently is how the two reports drift onto
   different days when a run straddles UTC midnight.

   **Override only when the user explicitly requests a non-default window.** Signals: "the week of
   X", "as of last Friday", "the report from three weeks ago", "in-progress data", "just today's
   numbers". Then use:
   ```pwsh
   .\bootstrap-report.ps1 -App broker -EndDate 2026-07-02   # e.g. reproduce the report as of Jul 2
   ```
   `-EndDate` is the exclusive upper bound of the current window (`curEnd`); the script derives
   `curStart = curEnd - 7d` and `prevStart = curEnd - 14d` deterministically. `-EndDate` must be
   today or earlier — future dates are refused.

   **Supported override = `-EndDate` only (shifts the window *end*).** The 7-day primary span, the
   7-day baseline, and the 60-day trend form a fixed frame that moves *rigidly* with `-EndDate`.
   There is **no** custom start-date and **no** arbitrary-span flag — a request like "last 30 days"
   or "from Jun 1 to Jun 20" is **not** expressible via a single parameter. To produce a longer or
   custom span you must edit the KQL window placeholders (`<CUR_START>` / `<CUR_END>` /
   `<PREV_START>`; the baseline end is always `<CUR_START>`, so there is no separate `<PREV_END>`
   token) by hand. If the user asks for a non-7-day span, say so up front rather than silently
   emitting a 7-day report.

2. **Comparison baseline** — auto-computed as the immediately-prior 7 days
   (`[curEnd - 14d, curEnd - 7d)`). No user input.

3. **60-day trend window** — auto-computed as the **literal last 60 days ending today**
   (`[curEnd - 60d, curEnd)`), so both bounds move with `-EndDate`. Trend and sparkline sections are
   bucketed into **rolling 7-day windows anchored at `curEnd`** (`bin_at(t, 7d, datetime(curEnd))`),
   **not** Sun-Sat calendar weeks. The final bucket is therefore `[curEnd - 7d, curEnd)` — byte-for-byte
   the same window as the headline WoW numbers — so **the novelty classifier's WoW equals the WoW the
   report prints**, by construction. Every bucket is a complete 7 days; there is no partial end bar and
   no separate classification cutoff. Invoke as
   `bucket-trends.js --start=<curEnd-60d> --end=<curEnd>` (**pass both** — see the note below).

   Because 60 is not a multiple of 7, the **oldest** bucket (`curEnd - 63d`) covers 4 days and is the
   partial one — the safe end to be partial on. `--start` drops it, leaving 8 complete rolling weeks.

> **⚠️ Why not `startofweek()`.** Calendar-week bucketing cut off at `startofweek(curEnd)` lagged the
> report's rolling window by up to a full week, so anything that turned in the last ~6 days was
> structurally invisible to the noise gate — exactly the period an on-call engineer cares about most.
> On the 2026-08-01 run the gate's "current" week was 07/19–07/26 against a report window of
> 07/25–08/01 (**one day of overlap**): `authorization_pending` read **+63.2%** in the report and
> **−37.1%** to the classifier, and was filed *"ONGOING — do not re-triage"*. Same for `expired_token`
> (+26.7% vs −51.0%). Re-bucketing on `bin_at` promoted both to ACCELERATING **and** demoted
> `access_denied`, a former false positive (actually −53.2%). Alignment adds real signal *and* removes
> phantom signal — it is not merely "more alerts". **Do not reintroduce `startofweek()`.**

> **⚠️ Always pass `--end=<curEnd>` to `bucket-trends.js`, not just `--start`.** Its partial-end
> auto-drop heuristic is guarded by `if (!endArg …)`. Under rolling alignment the newest bucket is
> genuinely complete, so omitting `--end` would let a **real** 70% collapse be silently discarded as
> "looks partial". Passing `--end` filters nothing (every bucket label is `< curEnd` by construction)
> and disables the heuristic. The script now warns if you omit it.

---

## Outputs

All outputs land in `$env:USERPROFILE\android-oce-reports\` — **outside the workspace**, so reports
can never be committed accidentally.

| Mode | Files produced |
|---|---|
| `broker` | `oncall-wow-report-<curEnd>.html` |
| `authapp` | `authapp-wow-report-<curEnd>.html` |
| `both` | both of the above **plus** `oce-index-<curEnd>.html` |

`<curEnd>` is `YYYY-MM-DD`, the end-date of the rolling 7-day window. Raw KQL payloads are cached
under `_data/<app>-<curEnd>/` so each report is reproducible; folders older than 60 days are pruned
automatically by `bootstrap-report.ps1`.

### The combined index (`both` mode only)

After both reports validate, build the index:

```pwsh
.\.github\skills\oncall-weekly-telemetry-report\assets\scripts\build-index.ps1 -EndDate <curEnd>
```

It reads the headline KPI tiles out of both finished reports and emits a single one-page digest
with links to each. It is a **digest, not an analysis** — do not write new findings into it that
aren't already in one of the two reports. If a cross-app finding matters (e.g. an Authenticator
Broker-API responsiveness spike that maps to a Broker error code), write it in **both** reports and
let the index link them.

---

## Orchestration (`both` mode)

Run the two playbooks **in parallel as background sub-agents**, not sequentially. Serially, `both`
roughly doubles an already-long run; in parallel it costs about as much wall-clock as one report.

1. Resolve the window once (`bootstrap-report.ps1 -App broker` prints it; capture `curEnd`).
2. Bootstrap **both** report files up front with the same `-EndDate`, so neither agent can drift:
   ```pwsh
   $S = '.\.github\skills\oncall-weekly-telemetry-report\assets\scripts'
   & "$S\bootstrap-report.ps1" -App broker  -EndDate <curEnd>
   & "$S\bootstrap-report.ps1" -App authapp -EndDate <curEnd>
   ```
3. Launch two `general-purpose` background agents. Give each the **full** context it needs — the
   playbook path, the resolved window dates, its bootstrapped report path, and its `_data` folder.
   Require each agent to read **both** its playbook **and** § Shared hard rules in this file before
   writing any HTML. Instruct each to do the work itself (not to advise), and to run its own
   validator before reporting back.
4. Wait for both. Do not start the index until both validators pass.
5. Run `build-index.ps1`.
6. Report the three file paths to the user in chat. Do **not** paste report contents into chat.

If one agent fails, still publish the other report and say plainly in chat which app failed and
why — a half-delivered rotation report beats a blocked one.

---

## Shared assets

| File | Purpose |
|---|---|
| [`scripts/bootstrap-report.ps1`](assets/scripts/bootstrap-report.ps1) | Bootstrap a report from its app's canonical template. `-App broker\|authapp`. Resolves + stamps the rolling window, creates `_data/<app>-<curEnd>/`, prunes old data, detects stub-vs-real collisions. |
| [`scripts/run-kql.ps1`](assets/scripts/run-kql.ps1) | Direct-REST Kusto helper — drop-in fallback when the Kusto MCP times out. `-App broker\|authapp` selects the cluster + database; `-Cluster`/`-Database` still override explicitly. |
| [`scripts/validate-report.ps1`](assets/scripts/validate-report.ps1) | Pre-publish validator. `-App broker\|authapp` selects the check profile. Shared checks (stale tokens, mojibake, div balance, sparkline coverage, header/filename date agreement) run for both. |
| [`scripts/build-index.ps1`](assets/scripts/build-index.ps1) | Build the combined `oce-index-<curEnd>.html` digest from two finished reports. |
| [`scripts/bucket-trends.js`](assets/scripts/bucket-trends.js) | Bucket any `{key, week, metric}` series into 60-day regression / spike / improvement / flat. App-agnostic — `--key=` selects the grouping column. |
| [`scripts/classify-novelty.js`](assets/scripts/classify-novelty.js) | Reads a `bucket-trends.js --json=` sidecar and labels each key **NEW / ACCELERATING / ONGOING / VOLATILE / RECOVERY / IMPROVING / STABLE** against its own 7-week baseline, plus family clustering. App-agnostic. **Mandatory in both playbooks** — it is what stops the attention section from being a volume-ranked list where a flat-but-huge code outranks a real step change, and it is also the **noise gate**: its `attention` set (`NEW` + `ACCELERATING`), plus at most 2 wins, is all that renders visibly with charts — everything else collapses into a fold. |
| [`scripts/agg.js`](assets/scripts/agg.js) | Per-key per-dim top-N rollup with WoW deltas. |
| [`scripts/find-suspect-prs.ps1`](assets/scripts/find-suspect-prs.ps1) | Parallel `git log -S` + `--grep` for a symbol. `-Repos` selects which repos to scan (broker/common, or authenticator). |
| [`scripts/visual-smoke.ps1`](assets/scripts/visual-smoke.ps1) | Optional Playwright layout smoke test — catches rendered-layout bugs (text bleed, cards touching) that HTML/CSS validation can't see. |
| [`templates/index-template.html`](assets/templates/index-template.html) | Canonical layout for the combined index page. |

App-specific assets (queries, cheatsheets, report templates) are listed in each playbook.

---

## Shared hard rules

These apply to **both** reports. App-specific hard rules (HLL device counting, TDigest percentiles,
`Merge*` helpers, the Originator pre-check, Authenticator `*DCount` columns, volume floors) live in
the playbooks — and **they are not interchangeable**. The Broker's "never `sum(countDevices)`" rule
is *actively wrong* on the Authenticator side, where `sum(SucceededDCount)` is the correct idiom.
Never carry a convention across the two playbooks.

- **Never carry a numeric telemetry value forward between runs.** Every KPI, table cell, delta %,
  device/request count, sparkline point, and verdict number must be re-pulled from Kusto for *this*
  run — never copied from a previous report, from a checkpoint/summary, from notes, or from memory.
  Telemetry shifts between runs and stale numbers read as fabricated. Near-miss precedent: a
  `no_tokens_found` count was about to be carried as ~23.7M when the actual current-window value
  was ~4.86M — a ~5× error that only the re-pull caught. If a number isn't backed by a query result
  file in this run's `_data/<app>-<curEnd>/`, it does not go in the report.
- **Never hardcode the "Generated" date.** It is the *run* date in **UTC**, auto-stamped by
  `bootstrap-report.ps1`. If you rebuild a body programmatically, derive it live with a **UTC-date**
  formatter (`new Date().toISOString().slice(0,10)` in Node,
  `[datetime]::UtcNow.ToString('yyyy-MM-dd')` in PowerShell) — never paste a literal.
- **⚠️ UTF-8 trap — DO NOT use PowerShell `@'...'@` heredocs to compose HTML containing emojis,
  em-dashes, arrows, or middle dots.** PowerShell silently strips multi-byte UTF-8 characters when
  piping heredocs to `Set-Content` / `Out-File`. Use Node (`fs.writeFileSync`),
  `[IO.File]::WriteAllText($path, $text, [System.Text.UTF8Encoding]::new($false))`, or explicit
  Unicode-pair literals (`[char]0xD83D + [char]0xDCCA` for 📊). The validator's `U+FFFD` check
  catches the worst case (mojibake) but cannot detect characters silently stripped to nothing.
- **Never use regex to bulk-edit balanced HTML.** This skill has burned twice on regex strip scripts
  that ate matched-pair `</div>` closes, producing inception-style nested-callout bugs that take a
  depth-tracking script to find. Make targeted, single-occurrence string replacements (with explicit
  before/after context) or rewrite the affected block end-to-end.
- **No shorthand in user-facing text.** All UI text — callouts, table headers, KPI labels, verdicts,
  badges — says `devices` and `requests`, never `devs` / `reqs`. Internal variable, column, and file
  names can stay short.
- **A moved metric is a question, not a verdict.** Never publish a regression verdict without the
  app's diagnostic ladder having been walked (Broker: Originator pre-check + dim slicing;
  Authenticator: volume floor + rate normalisation + error-reason decomposition).
- **Every red/amber table pill must be reconciled.** The scoreboard / WoW tables colour a row from
  its own rolling delta; the attention section is populated from `classify-novelty.js`'s **novelty**
  verdict. Those answer different questions, so a row can be legitimately red in the table and
  legitimately absent from attention — but a reader who sees that mismatch with no explanation
  concludes the report is broken. Precedent: `Passkey WebAuthN Registration` shipped carrying
  `tag-bad` (−1.27 pts, worst delta in the table) directly above the words *"Quiet week — 0 NEW or
  ACCELERATING"*. Both statements were true: the scenario peaks at ~732 bad-outcome devices, under
  the 1,000-device peak-floor, so it is **structurally excluded** from classification and can never
  appear in attention however sharply it moves. Every `tag-bad`/`tag-warn` row must therefore be
  **either** promoted into attention **or** named in a muted `<div class="reconcile-note">` giving
  the reason it is not escalated — test the reasons in this order: (1) below the classification
  floor, (2) within its own normal band, (3) ONGOING and flat. `validate-report.ps1` check 19
  hard-fails an unreconciled pill.
- **Filename collision rule.** If a report already exists for the same end-date, do not silently
  overwrite. Open it, list its top-3 findings, and explicitly state in chat what changed in the new
  data before regenerating. A second run on the same window without a delta is wasted work.
- **Do not create a separate Markdown summary** of either report — the HTML *is* the deliverable.
- **Do not commit** any report. They live in `$env:USERPROFILE\android-oce-reports\` precisely so
  they can't be staged accidentally.

---

## Router checklist

- [ ] Mode resolved (`both` default) and stated in chat before work starts.
- [ ] Window resolved **once** and the same `-EndDate` passed to every bootstrap call.
- [ ] Each selected playbook read **in full** by the agent that owns it.
- [ ] In `both` mode, the two playbooks ran as **parallel** sub-agents, not interleaved in one context.
- [ ] Each report passed its own `validate-report.ps1 -App <app>` run.
- [ ] `oce-index-<curEnd>.html` built (in `both` mode) **after** both validators passed.
- [ ] File paths reported in chat; no report content pasted into chat; nothing committed.
