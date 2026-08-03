# Increasing Code Coverage

Read this file when the user wants to **raise/improve/boost** coverage numbers (not just set
up tracking). It is an integrated capability of this skill, but a *distinct effort* from
onboarding: establish measurement + a visible baseline first (steps 1–4 of the skill) so the
effort can be verified. If tracking is already in place, jump straight in.

## Table of contents
- [The loop](#the-loop)
- [Finding gaps with `coverage_report.py gaps`](#finding-gaps)
- [Choosing targets](#choosing-targets)
- [Writing tests that count](#writing-tests-that-count)
- [Locking in gains with a gate](#gate)
- [Anti-patterns](#anti-patterns)

## The loop
Raising coverage is an iterative loop, not a one-shot:
1. **Baseline** — record current per-module numbers (the weekly report already does this).
2. **Find gaps** — run `coverage_report.py gaps` on the latest report to get a ranked worklist
   of the least-covered classes/files.
3. **Pick targets** — take the highest-value items off the list (see below).
4. **Write tests** for real behavior in those units; re-run coverage.
5. **Re-run `gaps`** to confirm the target dropped off / shrank, and watch the module % rise in
   the weekly trend.
6. **Lock it in** — once a module reaches a healthy level, add a no-regression gate so it can't
   slide back.

<a id="finding-gaps"></a>
## Finding gaps with `coverage_report.py gaps`
The `gaps` subcommand turns a raw coverage report into a prioritized, per-class/per-file
worklist. It reads the **same** JaCoCo / Cobertura / LCOV reports the build already produces —
no extra tooling.

```bash
# Biggest absolute wins first (default): classes with the most uncovered lines.
python3 coverage_report.py gaps --input '**/build/reports/**/*.xml' --metric LINE --top 25

# Lowest-coverage classes first, ignoring anything already >60% or tiny (<5 missed lines).
python3 coverage_report.py gaps --input coverage.cobertura.xml \
    --sort pct --max-pct 60 --min-missed 5

# Branch gaps for a JS module from LCOV, saved as a checklist artifact.
python3 coverage_report.py gaps --input coverage/lcov.info --metric BRANCH \
    --out-md gaps.md --out-json gaps.json
```

Key flags:
- `--metric LINE|BRANCH` — line gaps are the usual target; branch gaps expose untested
  conditionals/error paths.
- `--sort missed` (default) ranks by **most uncovered units** = biggest number bump per test
  written. `--sort pct` ranks by **lowest coverage** = worst-tested code first.
- `--max-pct N` hides units already well covered; `--min-missed N` hides trivial ones. Together
  they focus attention on "big and poorly covered".
- `--top N` caps the list (0 = all). `--out-md` / `--out-json` persist it as a work artifact.

Format is auto-detected; pass `--format jacoco|cobertura|lcov` to force it. The output columns
are `Class/File | Coverage | Missed | Covered/Total`.

<a id="choosing-targets"></a>
## Choosing targets
The ranked list tells you *where the uncovered code is*; combine it with judgment on *what is
worth covering*:
- **Prefer big + low-coverage + high-churn** modules — best return on effort. Cross-reference
  the `gaps` list against `git log`/churn if available.
- **Cover behavior that matters**: core logic, error/exception paths, boundary conditions,
  regression tests for recently-fixed bugs, and any newly-added code.
- **Skip / exclude the denominator noise**: generated code, DTOs/`data class`es, `toString`,
  builders, and test code. Exclude these in the build tool so the number reflects meaningful
  coverage instead of chasing 100% on trivial code.
- Use `--sort pct` for the "worst offenders" view when you want to eliminate near-zero classes,
  and `--sort missed` when you want the fastest overall percentage gain.

<a id="writing-tests-that-count"></a>
## Writing tests that count
- Assert on observable outcomes (return values, state changes, thrown exceptions, emitted
  events) — never write tests that execute code without asserting just to move the number.
- One behavior per test; name tests for the behavior, not the method.
- For each `gaps` target, open the class and cover its untested branches first (constructors and
  simple getters are low value even if uncovered).
- Re-run tests + `gaps` after each batch so progress is visible and you don't over-invest in one
  class.

<a id="gate"></a>
## Locking in gains with a gate
Prevent backsliding once a module improves. **Prefer a no-regression gate over a fixed absolute
threshold** — it's fair to modules that start low and doesn't block unrelated PRs.
- Compare a PR's coverage to the baseline/dev branch; **fail only if it *lowers* coverage**.
- Make the gate flip via a variable (e.g. `ENFORCE_COVERAGE_GATE`) between report-only and
  enforcing, with an emergency off-switch.
- Introduce it gradually: report-only first, then enforce once numbers are stable.
- Exclude generated/test/DTO code from the denominator where the build tool supports it.

<a id="anti-patterns"></a>
## Anti-patterns
- Tests that assert nothing (executing code just to raise the number).
- Chasing a global percentage instead of covering risky code paths.
- Hard-gating an absolute threshold on day one — blocks unrelated work and breeds resentment.
- Padding coverage with generated/DTO code left in the denominator.
