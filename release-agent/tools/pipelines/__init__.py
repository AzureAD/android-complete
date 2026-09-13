"""ADO pipeline queries for release verification and explicit gate approval writes.

Reads use `az`/ADO REST and return success, data, and detail. The explicit
`submit_pipeline_approval` writer is separately capability-fenced by core; the
build_verify steps receive read-only services. Approval recovery reads a frozen
approval id, validates identity and build ownership, and never submits again. The
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
from .ui_projection import *
from .rc_model import *
from .auth_app import *
from .auth_evidence import *
