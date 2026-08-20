"""Step: `mrwp_local` — verify the Local Monthly Release Work Pipeline run completed
(Phase 2, build_verify).

Local-flighting counterpart to `mrwp_ecs`: verifies the Local MRWP (def 2519) run "ran
to completion" (every stage executed; skipped/canceled/pending = block) and reports its
Test-tab results. Red/yellow stages and failed tests do NOT block — triaged later. All
logic is shared in `_common.verify_mrwp`.
"""
from __future__ import annotations

from steps.lib.agent import legacy_run
from steps.build_verify import _common as K

ID = "mrwp_local"
KIND = "agent"

MOCKABLE = {
    "mrwp_id": {"kind": "input", "desc": "Inject the Local MRWP build id (skip orchestrator lookup)."},
    "stages": {"kind": "input", "desc": "Inject the Local run's stage list [{name,state,result}]."},
    "tests": {"kind": "input", "desc": "Inject the Local test summary {total,passed,failed,categories}."},
    "suites": {"kind": "input", "desc": "Inject the Local failing suites [{name,failed,total,category,tests}]."},
}


def build(state):
    return K.verify_mrwp(state, "Local")


run = legacy_run(build)
