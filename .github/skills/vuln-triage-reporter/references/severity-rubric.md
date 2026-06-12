# Severity Classification Rubric

Right-size each finding against **our** codebase reality, not the filed severity. The filed
classification (MSRC severity, FireWatch/Glasswing tier) is an **input**. Our classification is the output.

## Core principle: evidence or it didn't happen

Every tier assignment requires **cited code evidence** (`file:line`). This cuts both ways:
- To **down-classify**, cite the mitigating control that blocks real-world exploitation.
- To **up-classify** (or confirm CRITICAL), cite the absence of any gate AND confirmed reachability.
- "I couldn't find an exploit path" / "I didn't see a mitigation" is **not** evidence. Show the control,
  or show the systematic searches (`codebase-researcher` style) that prove its absence.

## The tiers

### CRITICAL — must fix
- Reachable in a **shipping** configuration (not debug/test/root-only).
- **No** mitigating control found after a deep, beyond-the-obvious sweep.
- Real-world mass exploitation is plausible.
- **Evidence required:** sink `file:line` + reachability proof + the searches that establish no flight
  gate / allow-list / export restriction / IPC check exists.

### Important
- A genuine weakness exists, but blast radius is limited by a **partial** mitigation or **elevated
  prerequisites** (attacker must already control a federated IdP page, must win a race, etc.).
- **Evidence required:** sink `file:line` + the specific mitigation/precondition that limits it, cited.

### Moderate
- Defense-in-depth gap. Exploitation needs **unlikely preconditions**: root, a debuggable build,
  physical device access, or a non-default config.
- **Evidence required:** the cited precondition (e.g. file only readable on rooted device; component
  `android:exported="false"`) that blocks mass exploitation.

### Low / Won't-Fix
- Not reachable in the shipping config, OR already gated off by default.
- **Evidence required:** citation proving non-reachability — flight defaulting off, non-exported
  component, sibling handler enforcing an allow-list the finding assumed was missing, dead code, etc.

## Defense-in-depth checklist (the "look beyond" sweep)

The past failure mode was stopping too early. For **every** finding, explicitly check each layer and record
what you found (or the search that proves absence):

| Layer | What to look for | Where |
|-------|------------------|-------|
| Component export | `android:exported`, intent-filter, permission | `AndroidManifest.xml` (all modules) |
| IPC boundary | caller package / signature / UID validation | Common IPC layer, Broker operation dispatch |
| Sibling handlers | do adjacent methods enforce allow-lists this sink skips? | same file as the sink |
| Flight gates | `CommonFlight*`, ECS default state | flight managers, `*FlightsManager` |
| Upstream validation | scheme/host/path allow-lists before the sink | the dispatcher / classifier feeding the sink |
| Build/config gating | debug-only, test-only, emulator-only paths | `BuildConfig`, gradle, `if (DEBUG)` |
| Reachability conditions | what must be true at runtime to hit the sink | call-graph from a real entry point |

A finding is only **CRITICAL** if **all** of these come back empty after a genuine search — and you can
show the searches.

## Agree / Rebut record

For each finding capture:
- **Filed:** `<source>` → `<their tier>` (e.g. Glasswing → IMPORTANT, Tier 1).
- **Ours:** `<our tier>`.
- **Delta:** AGREE / DOWN-CLASSIFY / UP-CLASSIFY.
- **Justification:** 1–3 sentences anchored to the cited evidence above.

## Eng-days heuristic (for roll-up)

Rough, author-adjustable, keyed off **our** tier:
- CRITICAL: 5–8 (fix + tests + coordinated release + MSRC process)
- Important: 3–5
- Moderate: 1–3
- Low / Won't-Fix: 0.5–1 (write-up + close)

Always flag these as estimates; the on-call engineer adjusts.
