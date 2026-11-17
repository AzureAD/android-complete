"""Step: `mrwp_ecs` — verify the ECS Monthly Release Work Pipeline run completed
(Phase 2, build_verify).

The orchestrator triggers MRWP (def 2519) twice — once per flight provider. This step
verifies the ECS run "ran to completion" (every stage executed; skipped/canceled/pending
= block) and reports its Test-tab results. Red/yellow stages and failed tests do NOT
block — they're triaged in a later phase. All logic is shared in `_mrwp.verify_mrwp`.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from steps.build_verify._mrwp import verify_mrwp

from orchestrator.authority import PipelineScope, PipelineSlot

EVIDENCE = (PipelineScope(PipelineSlot.ECS),)
ID = "mrwp_ecs"
KIND = "agent"
NAME = 'Verify MRWP (ECS) ran to completion + tests'
REQUIRED_TOOLS = ('az',)
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

KNOWLEDGE = {'summary': 'Confirm the ECS Monthly Release Work Pipeline run ran to completion + report its '
            'tests.',
 'what': 'The orchestrator triggers MRWP (def 2519) twice — once per flight provider. This '
         "checks the ECS run 'ran to completion': EVERY stage executed (state completed, "
         'result succeeded/succeededWithIssues/failed). Red (failed) and yellow (warnings) '
         "stages are acceptable — they're triaged later. The ONLY blocker is a stage that "
         'never ran (pending, skipped, or canceled — i.e. the pipeline aborted partway). Also '
         'reports the Test-tab results (unit / instrumented / UI-automation totals).\n',
 'where': ['Monthly Release Work Pipeline (def 2519): '
           'https://identitydivision.visualstudio.com/Engineering/_build?definitionId=2519'],
 'how': 'If it blocks, a stage never ran — the pipeline aborted. Open the run, find the stage '
        "that didn't execute, follow its output + the recovery TSG, Retry, then rerun this "
        "step. Failing tests do NOT block here; they're triaged in the bug-bash phase.\n",
 'faqs': [{'q': 'The run has red stages / failing tests — why did it still pass?',
           'a': 'By design. A run is valid as long as every stage EXECUTED. Red/yellow stages '
                'and failed tests are expected at this checkpoint and triaged later. Only a '
                'stage that never ran (aborted pipeline) blocks.'},
          {'q': 'How is the ECS run identified?',
           'a': "From the orchestrator run's RC<N>-ECS=<build id> tag for the current RC "
                'iteration N (the highest N that has both an ECS and a Local id; a re-trigger '
                'adds a higher-numbered RC<N+1>-* set). If those tags are missing it falls '
                "back to the orchestrator's trigger-task log. The run is then read directly by "
                'build id.'}]}
