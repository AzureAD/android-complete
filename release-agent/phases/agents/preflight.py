"""Phase-0 pre-flight agents — compatibility shim.

The real logic now lives in the co-located step modules under `steps/preflight/`
(one home per step, each authoring the uniform `build(state) -> Outcome`). This
module keeps the historical `phases.agents.preflight` surface — the `run_*`
callables, the pure helpers, and `REGISTRY` — so the engine's agent seam and the
existing tests keep working while the logic lives under `steps/`.

  step `breaking` (S2)  -> steps.preflight.breaking
  step `wiki`     (S0)  -> steps.preflight.wiki
  step `cg`       (S8)  -> steps.preflight.cg
  step `cron`     (S10) -> steps.preflight.cron

Each step's `run` is `legacy_run(build)` (Outcome → StepResult), so the engine
still dispatches `run(phase_id, step, state) -> StepResult` unchanged.
"""
from __future__ import annotations

from steps.preflight import breaking as _breaking
from steps.preflight import cg as _cg
from steps.preflight import cron as _cron
from steps.preflight import wiki as _wiki

# ---- pure helpers re-exported (imported directly by unit tests) ------------
parse_breaking = _breaking.parse_breaking
_draft_breaking_comms = _breaking._draft_breaking_comms
_payload_template = _wiki._payload_template
_page_name = _wiki._page_name
_wiki_url = _wiki._wiki_url
_cg_summary = _cg._cg_summary
_cg_report = _cg._cg_report
_iso_age_days = _cron._iso_age_days

# ---- legacy run(...) callables (Outcome-backed adapters) -------------------
run_breaking = _breaking.run
run_wiki = _wiki.run
run_cg_alerts = _cg.run
run_cron_check = _cron.run


# ---- registry --------------------------------------------------------------
REGISTRY = {
    "breaking_detect": run_breaking,
    "wiki_payload": run_wiki,
    "cg_alerts": run_cg_alerts,
    "cron_check": run_cron_check,
}
