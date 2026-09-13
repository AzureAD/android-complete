"""Step: `mrwp_ecs` — verify the ECS Monthly Release Work Pipeline run completed
(Phase 2, build_verify).

The orchestrator triggers MRWP (def 2519) twice — once per flight provider. This step
verifies the ECS run "ran to completion" (every stage executed; skipped/canceled/pending
= block) and reports its Test-tab results. Red/yellow stages and failed tests do NOT
block — they're triaged in a later phase. All logic is shared in `_mrwp.verify_mrwp`.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext, thaw

from steps.build_verify._mrwp import verify_mrwp

from orchestrator.authority import PipelineScope, PipelineSlot

EVIDENCE = (PipelineScope(PipelineSlot.ECS),)
ID = "mrwp_ecs"
KIND = "agent"
EFFECT_MODE = "read_only"

# Mock knobs (mocks.local.yaml): mrwp_id (inject build id), stages (inject stage list),
# tests (inject test summary). Consumed inside _mrwp.verify_mrwp via mock_input.
MOCKABLE = {
    "mrwp_id": {"kind": "input", "desc": "Inject the ECS MRWP build id (skip orchestrator lookup)."},
    "stages": {"kind": "input", "desc": "Inject the ECS run's stage list [{name,state,result}]."},
    "tests": {"kind": "input", "desc": "Inject the ECS test summary {total,passed,failed,categories}."},
    "suites": {"kind": "input", "desc": "Inject the ECS failing suites [{name,failed,total,category,tests}]."},
    "rc": {"kind": "input", "desc": "Inject the authoritative RC iteration number (else from the tag)."},
}


def build(context: StepContext):
    return verify_mrwp(context, "ECS")
