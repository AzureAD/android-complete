# First-Run Intake Interview

The very first thing this skill does. It replaces "guess what the engineer meant" with **one compact
menu**, then restates the resolved plan (scope · depth · ETA · **output folder**) before any agent
launches.

> **Why this exists — real user feedback.** Two engineers ran the skill cold. One was on the wrong
> branch and never got a report; the other did not know the run would take ~35 minutes or that it
> would jump straight to writing a fix. Neither was asked what they actually wanted. A 60-second
> interview prevents both.

---

## Rule 1 — Ask once, in one message

Present **all four questions together** as a short numbered menu with defaults pre-filled. Do **not**
interrogate one question at a time — that is the single most common complaint about agent skills.

**Skip any question you can already answer** from what the engineer said. If they opened with
*"triage IcM 12345"*, Q1 is answered — do not re-ask it; just confirm it in the plan echo.

If the engineer says *"just go"* / *"use defaults"*, take **B → Standard → 2** and proceed.

---

## Rule 2 — The menu

```markdown
Before I start — 4 quick questions (reply with the letters/numbers, or "defaults"):

**1. What should I look at?**
   a) Specific finding(s) — give me the IcM / MSRC / ITD id(s)
   b) My current on-call shift (Wed -> Wed window)            [default]
   c) A specific date range — tell me the week (e.g. "Aug 3-10") or start/end
   d) A FireWatch / ITD report that isn't in IcM — I'll tell you how to save it
   e) Findings I already investigated — point me at your notes/markdown file
      and I'll verify + challenge them instead of starting from scratch
   f) Nothing new — just re-render / finalize the report for this shift

**2. How deep?**
   1) Fast     — single pass, ~5-8 min/finding. Triage direction only, NOT a final verdict
   2) Standard — two passes (investigate + adversarial challenge), ~15-25 min/finding   [default]
   3) Deep     — two passes + extra sweeps for a cross-module or high-severity finding, 30 min+

**3. What do you want out of it?**
   1) A verdict — agree with the filed severity, or rebut it
   2) Verdict + the written report artifacts on disk                                    [default]
   3) The above + remediation OPTIONS for anything we keep (I will NOT write code)
   4) The above + implement the fix and open a PR (I'll still show you options first,
      and get your go/no-go before any push)

**4. Anything I should know?**
   Prior/related IcMs, a suspected root cause, a deadline, "this one's already covered by X",
   or an existing investigation to build on. If nothing — say "no".
```

---

## Rule 3 — Map the answer to a run

| Q1 | Resolves to | Notes |
|----|-------------|-------|
| **a** | Mode (a) — triage the named ids | Run `shift.py check <icm>` per id first; SEEN ⇒ tell them it is already triaged this shift and offer mode (d) re-run instead |
| **b** | Mode (b) — sweep the current shift | `shift.py window` with no args |
| **c** | Mode (b) with an explicit window | Convert plain English to dates. *"this week"* ⇒ the current Wed→Wed shift. *"Aug 3-10"* ⇒ `--start/--end`. **Echo the resolved dates back** — never silently guess a year or a boundary |
| **d** | Step 2 ITD manual intake | Give them the Save-Page-As instructions from [itd-intake.md](itd-intake.md) |
| **e** | Verification mode | Read their file, then run the **adversarial pass first** against their conclusions, and only run a full Pass 1 if the challenge finds a gap. Cheapest useful run — say so |
| **f** | Mode (c) — finalize | No research; re-render from the existing folder |

| Q2 | Passes | Say this out loud |
|----|--------|-------------------|
| **Fast** | Pass 1 only | "⚠️ Fast mode is a **direction, not a verdict** — a single pass has been wrong before. I'll flag Confidence as Low and recommend a Standard re-run before you act on it." |
| **Standard** | Pass 1 + adversarial Pass 2 | The documented default |
| **Deep** | Pass 1 + Pass 2 + targeted follow-ups | Use for cross-module (`common`/`broker`) or anything filed Important+ |

| Q3 | Stops at |
|----|----------|
| **1** | Classification + a chat summary — **but the report files are still written** (they are cheap and the chat is not a deliverable) |
| **2** | Full artifact set (default) |
| **3** | Artifacts + a **Fix Options** table per kept finding — no code |
| **4** | Options → engineer picks → implement → diff review → go/no-go → PR |

---

## Rule 4 — Echo the plan before launching

Never launch agents straight off the menu. Restate it in **five lines** and wait for a "go":

```markdown
**Plan:** triage 2 finding(s) — IcM NNNNNN, NNNNNN
**Depth:** Standard (2 passes each, run in parallel)
**ETA:** ~20-30 min before I have verdicts. I'll post progress at each pass boundary.
**Output:** <absolute path to the shift folder>   <- your report lands here, on disk
**Then:** I'll show you the verdicts and ask what to do next. Nothing auto-runs.

Go?
```

The **Output** line is mandatory. Stating the absolute path up front is what makes a missing report
obvious at the end instead of a week later.

---

## Rule 5 — Handle the common cold-start problems proactively

| Symptom | What to say |
|---------|-------------|
| Submodules missing / empty | "Your checkout is missing `<x>` — every search would return a false 'no sink'. Run the repo submodule sync first." Do **not** proceed. |
| Running from a git worktree | "Worktrees don't carry the submodules — switch to the main `android-complete` checkout." |
| No IcM MCP | "I can't query IcM, so I can't sweep a window. You can still paste finding id(s) + detail and I'll triage those (option **a**)." |
| Workspace not writable | Say where it is trying to write and how to override with `VULN_TRIAGE_WORKSPACE`. |
| Engineer seems to be on an old copy of the skill | State the branch/commit the skill is being read from, so a stale copy is visible immediately. |

---

## Rule 6 — Close the loop

At the end of the run, verify what actually got written and hand back the path:

```
python scripts/verify_outputs.py            # add --expect <n> to assert n finding reports
```

A non-zero exit means the run is **not** complete — generate the missing artifacts before reporting
done. Then give the engineer the absolute folder path (and the `file:///` link), because the chat
transcript is not a deliverable.
