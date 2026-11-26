"""Step: `mrwp_local` — verify the Local Monthly Release Work Pipeline run completed
(Phase 2, build_verify).

Local-flighting counterpart to `mrwp_ecs`: verifies the Local MRWP (def 2519) run "ran
to completion" (every stage executed; skipped/canceled/pending = block) and reports its
Test-tab results. Red/yellow stages and failed tests do NOT block — triaged later. All
logic is shared in `_mrwp.verify_mrwp`.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from steps.build_verify._mrwp import verify_mrwp

from orchestrator.authority import PipelineScope, PipelineSlot

EVIDENCE = (PipelineScope(PipelineSlot.LOCAL),)
ID = "mrwp_local"
KIND = "agent"
NAME = 'Verify MRWP (Local) ran to completion + tests'
REQUIRED_TOOLS = ('az',)
EFFECT_MODE = "read_only"
RECHECK_WHEN_BLOCKED = True

def build(context: StepContext):
    return verify_mrwp(context, "Local")

KNOWLEDGE = {'summary': 'Confirm the Local-flighting MRWP run ran to completion + report its tests.',
 'what': 'Local-flighting counterpart to the ECS check: confirms the Local MRWP (def 2519) run '
         "'ran to completion' (every stage executed; skipped/canceled/pending = block) and "
         'reports its Test-tab results. Red/yellow stages and failed tests do NOT block — '
         'triaged later.\n',
 'where': ['Monthly Release Work Pipeline (def 2519): '
           'https://identitydivision.visualstudio.com/Engineering/_build?definitionId=2519'],
 'how': 'Same as the ECS check: only a stage that never ran (aborted pipeline) blocks. Follow '
        "the aborted stage's output + the recovery TSG, Retry, rerun the step. Failing tests "
        'are triaged in the bug-bash phase.\n',
 'faqs': [{'q': "What's the difference between the ECS and Local runs?",
           'a': 'The orchestrator triggers MRWP twice with different flight providers — ECS '
                '(server-driven flights) and Local (local flight overrides). Both must run to '
                "completion; they're verified separately."}]}
