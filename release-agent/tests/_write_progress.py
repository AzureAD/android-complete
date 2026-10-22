"""In-memory checkpoint port for executor unit tests; command tests use real state."""
from copy import deepcopy
from types import SimpleNamespace

from orchestrator import revision, write_review as W
from orchestrator.git_write_plans import reconcile_operation


def memory_progress(authorization):
    progress = object.__new__(W.VerifiedProgress)
    progress.scope = revision.digest({})
    progress.existing, progress.failed = False, False
    progress.reconcile = reconcile_operation
    progress.authorization = authorization
    progress.snapshots = []
    progress.orch = SimpleNamespace(_transition_kernel=lambda: SimpleNamespace(
        checkpoint_write_progress=lambda permit, value: progress.snapshots.append(deepcopy(value))))
    authorization._permit = object()
    progress.value = W._sealed({
        "scope": progress.scope, "policy": {"automatic": False, "reviewer": "offline"},
        "reviews": [{"hash": revision.digest({}), "approved_by": "offline"}],
        "operations": [], "finished": False, "manual_completion": None})
    return progress
