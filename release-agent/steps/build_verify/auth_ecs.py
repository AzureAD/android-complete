"""Step: `auth_ecs` — track the Authenticator ECS RC build + its post-build UI tests
(Phase 2, build_verify).

The orchestrator cuts the auth working-branch; the RC auth-app build (msazure/One def
475778) self-triggers off that cut, and its post-build UI tests (def 444678) self-trigger
off the build (completion trigger, PR 16976328). None of this is part of the Engineering
release-verification chain, so this step discovers the ECS build independently (cross-org),
follows the deterministic build->test resource link, and applies its OWN quality bar: both
Firebase device suites must pass >= 90%. It is a SEPARATE report section and its own
pass/block — it does NOT feed the MRWP 90% UI gate. All logic lives in
`_common.verify_auth_ecs`.
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
