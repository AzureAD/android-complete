---
name: aria-alert-investigator
description: Investigate Aria health-metric alerts (anomaly-detection IcMs of the form "Aria detected an incident in <project> for <metric>"). Use this skill when an IcM was triggered by Aria's anomaly detector on android_spans / android_metrics — NOT for customer-reported authentication failures. Triggers include "investigate Aria alert", "Aria detected an incident", "health metric incident", "telemetry threshold breach", or any IcM where the title starts with "Aria detected".
---

# Aria Alert Investigator

Investigate Aria health-metric alerts evidence-first, without anchoring on guesses.

## When to use this skill vs. the others

| Incident shape | Use |
|----------------|-----|
| Customer/partner reports auth failure, missing PRT, sign-out loop, etc. | `incident-investigator` |
| You need to write Kusto queries for any reason | `kusto-analyst` (this skill calls into it) |
| **IcM title is "Aria detected an incident in `<project>` for `<metric>`"** | **This skill** |
| User says "investigate this Aria alert" / "health metric IcM fired" | **This skill** |

Aria alerts are **statistical anomaly detections on telemetry**, not user-reported problems. The investigation pattern is fundamentally different — there is often no customer, no error chain, and no log file. The signal is "the curve moved" and the job is to determine whether the underlying data actually moved, and if so, why.

---

## Core principles

### Principle 1 — Never guess the metric definition silently

Aria health-metric names are marketing-style labels (`SDM - timed_out_execution`, `failed_get_current_account_operation_count`). They do **not** uniquely identify a Kusto slice.

**Rule:** Before running any investigation query, attempt to decode the metric name into a `(table, span_name, error_code, dimension filters, …)` tuple. If multiple candidates plausibly match, **ask the user to confirm the exact slice** before proceeding. Only fall back to a best-evidenced guess if the user does not know — and if you do, **flag the assumption explicitly in every subsequent finding** ("assuming `span_name == X`, …").

### Principle 2 — Treat the alert value as opaque

Aria reports a number (e.g., `4.48 in the Red band`). This may be a count, rate, percentile, z-score, or a synthesized statistic. Without the cube definition, **do not reverse-engineer it**. Do not say "this means 4 events." Use it only as a directional signal that "Aria thinks something deviated."

### Principle 3 — Look at the trend from multiple angles before concluding anything

A single view will fool you. The same daily count can mean very different things depending on traffic, device base, and seasonality. Before deciding whether a real spike exists, check the trend from several independent angles.

Good angles to combine (pick the ones relevant to the metric):
- **Volume signals**: raw error count, total request volume on the same operation
- **Normalized signals**: error rate (errors ÷ attempts), affected device rate (error devices ÷ active devices) — these strip out the effect of traffic changes
- **Time signals**: hourly view around the alert (catches single-device retry storms), same day-of-week comparison (catches weekly seasonality), longer history to define the historical band

**Why normalized signals matter:** error count rises can come from real regressions OR from traffic growth. Error rate isolates the former. Affected device rate further separates "one device retrying a lot" from "many devices each failing once" — these mean very different things.

If none of the angles you checked shows a deviation outside the historical band, say exactly that and stop:

> "I don't see a deviation in `<list the angles you checked>` over the last N days. The alert grain on `<date>` sits within the historical `<min>–<max>` band."

You **may** offer an opinion after stating the data finding — but only when clearly separated and flagged as opinion, not as a data-driven conclusion. Example:

> "**Data finding:** [the statement above]
>
> **My read (low confidence — based on absence of evidence rather than positive evidence):** This looks consistent with detector noise on a low-volume metric. The team has resolved several past ICMs in this metric family the same way (threshold widened, no code change — see Step 2 results). However, I cannot rule out a real issue you have context for; please confirm before closing."

Rules for opinions:
- State the data finding **first and separately**.
- Explicitly flag confidence (`low / medium / high`) and **why** — if it's based on absence of evidence rather than positive signal, say so.
- Do not assert "this is noise" or "this is a false positive" as fact. Frame as "this looks like" / "consistent with" / "my read is".
- Always invite the user to override with context the data does not show.

### Principle 4 — Past ICMs are pattern signal, not verdict

Pull past Aria ICMs in the same metric family. If they show a recurring resolution ("acknowledged as duplicate, threshold widened, no code change"), call that out as **prior pattern** — not as the answer for this one. Each alert is its own investigation.

### Principle 5 — Compare same-day-of-week for seasonal metrics

SDM, broker, and most auth metrics have strong weekly seasonality (workplace flows). Day-over-day comparisons against a weekend baseline produce false rises on Mondays/Tuesdays. Always include a "same day-of-week, last N weeks" view for any metric that follows business-hour patterns.

### Principle 6 — Code context is the baseline, not an extra step

Span names, error codes, operations, and dimensions are opaque strings without the code that emits them. Use the [`codebase-researcher`](../codebase-researcher/SKILL.md) skill **continuously throughout the workflow** to understand what each signal actually means — what an `error_code` is emitted for, what a `span_name` represents, what a `broker_operation` does, what changed between two `broker_version`s.

Do this whenever an unfamiliar attribute, error code, or operation appears in the data. The data only tells you *that* something moved; the code tells you *what it would mean* if it did.

---

## Workflow

Execute steps in order. Do not skip steps.

### Step 1 — Decode the metric → KQL slice (BLOCKING)

Read the IcM ticket and capture: **project / cube name**, **metric name**, **alert value** (keep opaque), **band**, **timestamp**, and any **past ICMs already linked**.

Then map the metric name to a Kusto filter using these heuristics:

| Metric name fragment | Likely filter |
|----------------------|---------------|
| `SDM`, `shared_device`, `SharedDevice` | `is_shared_device == true` |
| `failed_<X>_count`, `failed_<X>_operation_count` | `error_code != ""` AND span/operation matching `<X>` |
| `timed_out_execution` | `error_code == "timed_out_execution"` |
| `<operation_name>_count` | Some span_name or broker_operation matching the operation |
| `<auth_flow>_failures` | `span_name == <flow>` AND `error_code != ""` |

**List every candidate slice** you generated and **ask the user to confirm**. Use `vscode_askQuestions` with one option per candidate. Example:

```
askQuestion({
  question: "Multiple Kusto slices could back the metric 'SDM - timed_out_execution'. Which one is it?",
  options: [
    { label: "is_shared_device==true AND span_name=='AcquireTokenSilent' AND error_code=='timed_out_execution'" },
    { label: "is_shared_device==true AND span_name=='ATISilently' AND error_code=='timed_out_execution'" },
    { label: "is_shared_device==true AND error_code=='timed_out_execution' (any span)" },
    { label: "I don't know — make your best guess" }
  ]
})
```

**Do not run any data queries until this step completes.** A wrong slice will silently invalidate every step below.

If the user picks "I don't know," pick the candidate with the strongest evidence and **state the assumption explicitly** at the top of every subsequent finding.

### Step 2 — Search for past ICMs in the metric family (parallel with Step 3)

The IcM's "duplicate of …" links from Step 1 capture what was already noted. This step goes further: actively search the DRI Copilot index for past Aria alerts on the **same or sibling** metrics, even if they aren't linked from the current ticket.

Skip this step only if Step 1 already surfaced 3+ past ICMs in the same metric family with consistent resolutions.

```
mcp_mydricopilot_Android_DRI_Copilot_Project_Explorer(
  message="Find past Aria alert ICMs related to <metric_name> in <project>. For each, return root cause, mitigation, and whether it required code change."
)
```

Report what the family pattern looks like (do not draw conclusions about the current ICM from it).

### Step 3 — Investigate the trend (Principle 3)

Delegate all Kusto query construction and execution to the [`kusto-analyst`](../kusto-analyst/SKILL.md) skill. This skill's job is to decide **what** to investigate; `kusto-analyst` decides **how** to query for it.

Investigate freely. Combine whatever angles best characterize this metric — at minimum understand absolute volume, normalized rates (so traffic shifts can't fool you), time/seasonality, and single-device dominance. Use the angle table in Principle 3 as a starting menu, not a checklist.

If one of the angles shows a clear deviation, ask `kusto-analyst` to drill into the dimension(s) most likely to explain it. Useful slicing dimensions:

| Dimension | What it isolates |
|-----------|------------------|
| `broker_version` | Version-specific regressions |
| `calling_package_name` | App-specific regressions (Teams, Outlook, etc.) |
| `DeviceInfo_Make` / `DeviceInfo_Model` | OEM-specific issues |
| `DeviceInfo_OsVersion` | OS-version-specific issues |
| `tenant_id` | Single-tenant issues |
| `error_message` / `error_location` | Sub-categorization within the same `error_code` |

When drilling, ask for **rates within each dimension value** (events ÷ total attempts in that slice), not raw counts — otherwise traffic-share shifts will mislead you.

### Step 4 — Report what you found (Principle 3)

Use this template. State data findings first, then optionally offer an opinion in the separate "My read" section.

```markdown
## Investigation: IcM <number> — Aria alert on <metric>

### Slice used
`<the SLICE you confirmed in Step 1>`
<If user said "don't know": **Assumed slice — not confirmed.**>

### Past ICMs in this metric family
<Bullet list with link, date, mitigation, code-change Yes/No.
Frame as pattern observation only.>

### Trend analysis
<For each angle you checked, one line: angle, historical band, alert-day value, in/out of band.
Example:
- **Raw count (30d daily)**: range 12–22k, alert day = 20.5k. In band.
- **Error rate**: range 12.7–19.6 per 1k, alert day = 13.5 per 1k. In band, on the low end.
- **Total request volume**: range 870k–1.6M/day, alert day = 1.52M. In band.
- **Same-day-of-week (last 6 Tuesdays)**: 19.8k, 20.1k, 21.3k, 19.5k, 22.1k, 20.5k. Alert day fits the Tuesday baseline.
- **Hourly view around alert**: peak 1.3k/h, no single-device dominance (events/devices ratio < 2 in every grain).
>

### What I see in the data
<One of:>
- "I don't see a deviation in `<list the angles>` over the last N days. The alert grain on <date> sits within the historical <min>–<max> band."
- "Error rate moved from <X> to <Y> on <date>. Affected device rate also moved (<A> → <B>). [continue for each angle that moved]"

### My read (optional, only if you have one)
<If the data is inconclusive, you may offer an opinion here. Required format:>
- **Confidence**: low / medium / high
- **Based on**: positive evidence / absence of evidence / pattern match to past ICMs / etc.
- **My read**: <one sentence, framed as "looks like" or "consistent with", not as fact>
- **What would change my mind**: <what data or context would flip the read>

### Suggested next steps for the user
<Only suggest concrete actions. Do NOT label the alert. Examples:>
- Confirm the slice if it was assumed
- Slice the data by `<dimension>` if you suspect a specific population
- Compare against telemetry on a sibling metric `<X>`
- Check the cube definition in the Aria portal
```

---

## Tool reference

### DRI Copilot
- `mcp_mydricopilot_Android_DRI_Copilot_Project_Explorer` — Get IcM context, find similar past ICMs, search TSGs

### Kusto
- Delegate to the [`kusto-analyst`](../kusto-analyst/SKILL.md) skill for all KQL work — cluster/database/table identifiers, field schema, and query construction live there.

---

