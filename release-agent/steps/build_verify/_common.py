"""Shared Phase-2 snapshot storage, evidence primitives, and Engineering recovery links.

Step decisions and report presentation live with their owning steps, not here.
Engineering coordinates come from tools.pipelines; Authenticator uses msazure/One.
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.coordinates import coords
from tools.pipelines import ENGINEERING_ORG as ORG, ENGINEERING_PROJECT as PROJECT

# Surfaced in every block reason so the engineer knows how to recover / escalate.
RECOVERY_TSG = ("https://eng.ms/docs/microsoft-security/identity/"
                "entra-developer-application-platform/auth-client/"
                "authn-sdk-msal-android/android-auth-libraries/releases/"
                "internal-release-checklist/release-orchestrator-recovery")
ESCALATION_CHAT = ("https://teams.microsoft.com/l/chat/"
                   f"{coords.team('android_core')['chat']}/conversations")

# Standard help tail appended to orchestrator/MRWP block reasons.
UNBLOCK_HELP = (
    "\n→ Each failed stage's output describes the root cause + corrective action. "
    "Follow it, then click Retry on the failed stage. Recovery TSG: "
    f"{RECOVERY_TSG} . If unresolved within 2h, escalate: {ESCALATION_CHAT}")


def build_url(build_id):
    return f"{ORG}/{PROJECT}/_build/results?buildId={build_id}"


def links_for(build_id, name="ADO run"):
    return [{"name": name, "url": build_url(build_id)}]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _pipeline_runs(state) -> dict:
    """The nested pipeline_runs container on state."""
    return getattr(state, "pipeline_runs", None) or {}


def stash_checker(state, run_id, when=None):
    """Record the (single) Code Complete Checker run that fired the release."""
    pr = _pipeline_runs(state)
    pr["checker"] = {"run_id": str(run_id), "when": when, "resolved_at": _now_iso()}
    state.pipeline_runs = pr


def stash_orchestrator(state, run_id, parked=None):
    """Record the (single) Release Orchestrator run + its parked flag. Versions are NOT stored
    here — state.versions is the single source of truth (populated by orchestrator_health)."""
    pr = _pipeline_runs(state)
    pr["orchestrator"] = {"run_id": str(run_id),
                          "parked": parked,
                          "resolved_at": _now_iso()}
    state.pipeline_runs = pr


def latest_rc(state) -> dict:
    """The current RC iteration (the last entry in rcs), or {} when none resolved yet."""
    rcs = _pipeline_runs(state).get("rcs") or []
    return rcs[-1] if rcs else {}


def stash_mrwp(state, provider, snapshot, rc=None):
    """Record an MRWP provider run's FULL verification snapshot into an RC iteration.
    `provider` is 'ECS' or 'Local'; `snapshot` carries run_id + stage/test results (run_id,
    id_source, complete, ran, total, failed_stages, yellow_stages, never_ran, tests,
    failed_suites).

    `rc` (optional) is the AUTHORITATIVE RC iteration number from the orchestrator tag
    (RC<N>-ECS / RC<N>-Local, surfaced by pipelines.mrwp_run_ids). When given, the snapshot
    merges into the rcs entry with THAT number (created if absent), so the stored rc mirrors
    the pipeline exactly — and ECS/Local resolving in separate steps land in the same entry.

    When `rc` is None (mock-injected id, or the log fallback where the tag isn't available),
    it falls back to run_id-change detection: a changed run_id for this provider means RC
    Testing was re-triggered → append the next local rc (last+1)."""
    key = provider.lower()                       # 'ecs' | 'local'
    pr = _pipeline_runs(state)
    rcs = pr.setdefault("rcs", [])
    if rc is not None:
        cur = next((e for e in rcs if e.get("rc") == rc), None)
        if cur is None:
            cur = {"rc": rc}
            rcs.append(cur)
    else:
        cur = rcs[-1] if rcs else None
        existing = (cur or {}).get(key) or {}
        if cur is None or (existing.get("run_id") and existing["run_id"] != str(snapshot.get("run_id"))):
            cur = {"rc": (rcs[-1]["rc"] + 1) if rcs else 1}
            rcs.append(cur)
    snap = dict(snapshot)
    snap["run_id"] = str(snapshot.get("run_id"))
    snap["resolved_at"] = _now_iso()
    cur[key] = snap
    cur["resolved_at"] = _now_iso()
    state.pipeline_runs = pr


def stash_auth(state, rc, snapshot):
    """Record the Authenticator ECS build + UI-test snapshot into the RC iteration `rc`.

    The auth leg is a SEPARATE report section keyed off the SAME RC iteration as MRWP — it
    merges into the rcs entry with that number (created if the auth step resolves it before
    the MRWP steps do), so a release's ECS build, both MRWP runs, and the auth build/test
    all land in one rcs[<n>] record. `snapshot` = {build:{...}, test:{...}, verdict, ...}."""
    pr = _pipeline_runs(state)
    rcs = pr.setdefault("rcs", [])
    cur = next((e for e in rcs if e.get("rc") == rc), None)
    if cur is None:
        cur = {"rc": rc}
        rcs.append(cur)
    snap = dict(snapshot)
    snap["resolved_at"] = _now_iso()
    cur["auth"] = snap
    cur["resolved_at"] = _now_iso()
    state.pipeline_runs = pr


def valid_id(value):
    return (not isinstance(value, bool) and isinstance(value, (str, int))
            and str(value).isascii() and str(value).isdigit() and int(value) > 0)


def valid_counts(counts):
    """Unexecuted/skipped tests may explain total > passed + failed, never the reverse."""
    if not isinstance(counts, dict):
        return False
    values = [counts.get(k) for k in ("total", "passed", "failed")]
    return (all(type(v) is int and v >= 0 for v in values)
            and values[0] > 0 and values[1] + values[2] <= values[0])
