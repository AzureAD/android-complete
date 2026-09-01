"""Step: `auth_ecs` — capture the Authenticator ECS RC build + its post-build UI-test data
(Phase 2, build_verify).

The orchestrator cuts the auth working-branch; the RC auth-app build (msazure/One def
475778) self-triggers off that cut, and its post-build UI tests (def 444678) self-trigger
off the build (completion trigger, PR 16976328). None of this is part of the Engineering
release-verification chain, so this step discovers the ECS build independently (cross-org),
follows the deterministic build->test resource link, and reads the two Firebase device
suites. Like the mrwp_ecs/mrwp_local steps, it is a DATA-AVAILABILITY check: it confirms the
build ran + captures the results into rcs[rc].auth, but it does NOT apply the 90% gate — a
sub-90% result is data, not a block. The rc_report step consolidates MRWP + auth and makes
the single go/hold decision. All logic lives in `_common.verify_auth_ecs`.
"""
from __future__ import annotations

from steps.lib.agent import legacy_run
from steps.build_verify import _common as K

ID = "auth_ecs"
KIND = "agent"

# Mock knobs (mocks.local.yaml): consumed inside _common.verify_auth_ecs via mock_input.
MOCKABLE = {
    "auth_build": {"kind": "input",
                   "desc": "Inject the ECS auth build {build_id,rc,version,status,result} (skip the One lookup)."},
    "test_build": {"kind": "input",
                   "desc": "Inject the post-build UI-test run id (skip the resource-link scan)."},
    "suites": {"kind": "input",
               "desc": "Inject the Firebase suite rates {name:{present,passed,failed,total,pct}}."},
    "rc": {"kind": "input", "desc": "Override the RC iteration number (else from the auth build version)."},
}


def build(state):
    return K.verify_auth_ecs(state)


run = legacy_run(build)
