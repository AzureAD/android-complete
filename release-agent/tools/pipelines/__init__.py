"""Read-only ADO pipeline queries for Phase 2 (build_verify) release verification.

Every function shells out to `az` and returns an (ok, data, detail) triple — no
writes, deterministic, so the build_verify agent steps stay pure verification. The
release chain these read (all in identitydivision/Engineering):

    3038 Code Complete Calendar Checker  → on the CCD, triggers →
    2828 Release Orchestrator            → self-tags AuthenticatorBranch=release-YYYY-MM-DD
                                           + RC<N>-ECS=<id> / RC<N>-Local=<id> (the two MRWP
                                             runs for RC iteration N; a re-trigger adds RC<N+1>-*)
    2519 Monthly Release Work Pipeline   → runs twice (ECS + Local), ~23 stages each

The orchestrator's self-tags are the traceability anchor: find the 2828 run for a
release month by its AuthenticatorBranch tag, then read RC-<provider>=<id> to get the
MRWP build ids directly (no log parsing).
"""
from __future__ import annotations

from ._rest import *
from .orchestrator import *
from .tests_results import *
from .rc_model import *
from .auth_app import *
