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
the single go/hold decision. This module also owns the auth evidence checks and the
informational suite verdict reused by the report.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked, InProgress
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from steps.build_verify._common import latest_rc, stash_auth, valid_id, valid_counts
from tools import pipelines as P
from tools.pipelines import AUTH_ORG, AUTH_PROJECT, AUTH_UI_SUITES, AUTH_UI_PASS_THRESHOLD

ID = "auth_ecs"
KIND = "agent"

# Mock knobs (mocks.local.yaml): consumed inside verify_auth_ecs via mock_input.
MOCKABLE = {
    "auth_build": {"kind": "input",
                   "desc": "Inject the ECS auth build {build_id,rc,version,status,result} (skip the One lookup)."},
    "test_build": {"kind": "input",
                   "desc": "Inject the post-build UI-test run id (skip the resource-link scan)."},
    "test_status": {"kind": "input",
                   "desc": "Inject the UI-test build status; in-progress runs are not captured."},
    "suites": {"kind": "input",
               "desc": "Inject the Firebase suite rates {name:{present,passed,failed,total,pct}}."},
    "capture": {"kind": "input", "desc": "Inject complete attributed Authenticator source evidence."},
    "rc": {"kind": "input", "desc": "Override the RC iteration number (else from the auth build version)."},
}


def auth_build_url(build_id):
    return f"{AUTH_ORG}/{AUTH_PROJECT}/_build/results?buildId={build_id}"


def auth_evidence_issues(model):
    auth = model.get("auth") or {}
    build = auth.get("build") or {}
    if (not valid_id(build.get("run_id")) or build.get("complete") is not True
            or build.get("result") not in ("succeeded", "partiallySucceeded", "failed", "canceled")):
        return ["Authenticator ECS: missing completed build with a valid id/result"]
    if not valid_id(build.get("rc")) or build.get("rc") != model.get("rc"):
        return ["Authenticator ECS: build belongs to a different RC"]
    # A completed failed build is reportable evidence, not a missing test snapshot.
    if build["result"] in ("failed", "canceled"):
        return []
    test = auth.get("test") or {}
    if not valid_id(test.get("run_id")) or test.get("complete") is not True:
        return ["Authenticator ECS: missing completed UI-test run"]
    suites = test.get("suites")
    if not isinstance(suites, dict):
        return ["Authenticator ECS: UI-test results have not been captured"]
    issues = []
    for name in AUTH_UI_SUITES:
        suite = suites.get(name)
        # Explicitly absent/empty suites on a completed run are quality failures;
        # an unread suite is missing evidence.
        if not isinstance(suite, dict) or type(suite.get("present")) is not bool:
            issues.append(f"Authenticator ECS: missing snapshot for {name}")
        elif suite["present"] and not valid_counts(suite):
            if not all(type(suite.get(k)) is int and suite[k] == 0
                       for k in ("total", "passed", "failed")):
                issues.append(f"Authenticator ECS: invalid counts for {name}")
    ok, _, detail = P.inspect_auth_ui_evidence({"rc": model.get("rc"), "auth": auth})
    if not ok:
        issues.append(detail)
    return issues


def auth_pass_pct(suite):
    if not suite.get("present") or not valid_counts(suite):
        return None
    executed = suite["passed"] + suite["failed"]
    return suite["passed"] * 100.0 / executed if executed else None


def auth_gate(suites) -> dict:
    """The Authenticator ECS quality bar — SEPARATE from the MRWP 90% UI gate. Both Firebase
    device suites (AUTH_UI_SUITES) must clear AUTH_UI_PASS_THRESHOLD (>=90% pass rate).

    `suites` is the map from `pipelines.auth_ui_suite_rates`
    ({name: {present, passed, failed, total, pct}}). Returns
      {verdict, blocking, threshold, suites, detail}
    where verdict is:
      * 'clean'     — every gated suite present AND pct >= threshold: pass.
      * 'attention' — a suite is missing, has no executed result, or is below threshold: BLOCK.
    `blocking` is True only for 'attention' (its own block — it does NOT feed rc_ui_gate)."""
    thr = AUTH_UI_PASS_THRESHOLD
    suites = suites or {}
    lines, failing = [], []
    for name in AUTH_UI_SUITES:
        s = suites.get(name) or {"present": False, "pct": None}
        if not s.get("present"):
            failing.append(name)
            lines.append(f"  \u2022 {name}: no result")
            continue
        pct = auth_pass_pct(s)
        mark = "OK" if (pct is not None and pct >= thr) else "BELOW"
        if pct is None or pct < thr:
            failing.append(name)
        lines.append(f"  \u2022 {name}: {s.get('passed', 0)}/"
                     f"{(s.get('passed', 0) + s.get('failed', 0))} passed"
                     f" ({'n/a' if pct is None else str(pct) + '%'}) [{mark}]")
    body = "\n".join(lines)
    if not failing:
        return {"verdict": "clean", "blocking": False, "threshold": thr, "suites": suites,
                "detail": (f"Authenticator ECS UI tests clear the {thr:.0f}% bar in both "
                           f"suites:\n{body}")}
    return {"verdict": "attention", "blocking": True, "threshold": thr, "suites": suites,
            "detail": (f"Authenticator ECS UI gate NOT met (both suites must be >= {thr:.0f}%). "
                       f"Below/missing: {', '.join(failing)}.\n{body}\n"
                       f"\u2192 Investigate the failing Firebase suite(s), then re-run the "
                       f"post-build UI test. Scout re-evaluates when it completes.")}


def verify_auth_ecs(state):
    """Body for the `auth_ecs` step — a DATA-AVAILABILITY check for the Authenticator ECS RC
    build + its post-build UI test (msazure/One, cross-org). Like the MRWP steps, it only
    confirms the build RAN and the test data exists, then CAPTURES it into rcs[rc].auth for
    the RC report. It does NOT apply the 90% gate — a sub-90% result is DATA, not a block.
    The `rc_report` step consolidates MRWP + auth and makes the go/hold decision.

    Flow: resolve the auth working-branch from state.versions.authenticator -> find the
    current-RC ECS build (def 475778). In-flight build -> hold (poll); no build at all ->
    block (nothing to report on); a completed-but-failed build -> record it (no test data) +
    Done; a green build -> find its post-build UI-test run (not run yet -> hold), read the two
    Firebase suites, snapshot everything + the informational verdict, and Done.

    Mock knobs: auth_build ({build_id,rc,version,status,result}), test_build (id),
    test_status, suites (the auth_ui_suite_rates map), rc (override the RC number)."""
    label = "Authenticator ECS"

    # 1) resolve the current-RC ECS build (or take the injected one)
    ab = mock_input("auth_build", MISSING)
    if ab is MISSING:
        auth_branch = (getattr(state, "versions", None) or {}).get("authenticator")
        if not auth_branch:
            return Blocked(f"{label}: no authenticator branch on state.versions yet — run "
                           f"orchestrator_health first.")
        ok, ab, detail = P.find_auth_ecs_build(auth_branch)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not query the auth build ({detail}){hint}.")
        if not ab:
            return Blocked(f"{label}: no ECS release-candidate auth build found for this "
                           f"release ({detail}). Verify the auth working-branch was cut.")
    build_id = ab.get("build_id")
    rc_num = mock_input("rc", MISSING)
    rc_num = rc_num if rc_num is not MISSING else ab.get("rc")
    links = [{"name": f"{label} build", "url": auth_build_url(build_id)}]

    # 1.5) the build must have run to completion (data availability). In-flight -> hold.
    status, result = ab.get("status"), ab.get("result")
    if not valid_id(build_id) or not valid_id(rc_num) or status is None:
        return Blocked(f"{label}: missing build id, RC iteration or build status.", links=links)
    if int(rc_num) < int(latest_rc(state).get("rc") or 0):
        return Blocked(f"{label}: build {build_id} belongs to older RC{rc_num}; waiting for "
                       "the current-RC auth build.", links=links)
    # A refreshed discovery invalidates the prior capture immediately, including while
    # the newly discovered APK/tests are pending or incomplete. Never reuse stale data.
    old = latest_rc(state).get("auth")
    if old:
        latest_rc(state).pop("auth", None)
    if status != "completed":
        return InProgress(
            f"{label} build {build_id} is still running (status: {status}) — Scout is polling "
            f"every 30 min and will capture its UI tests when it completes.", links=links)

    # A build that ran but did NOT succeed has no usable UI-test data. Record the fact (the
    # RC report consolidates it and decides) and finish — this step never gates, it only
    # confirms the build ran + captures the data it produced.
    if result not in ("succeeded", "partiallySucceeded", "failed", "canceled"):
        return Blocked(f"{label}: unknown terminal build result: {result!r}.", links=links)
    if result in ("failed", "canceled"):
        stash_auth(state, rc_num, {
            "build": {"run_id": str(build_id), "rc": rc_num, "version": ab.get("version"),
                      "build_number": ab.get("build_number"),
                      "result": result, "complete": True},
            "test": None,
            "verdict": "attention",
        })
        return Done(
            f"{label} build {build_id} completed but did not succeed (result: {result}) — "
            f"recorded for the RC report (no UI-test data). The report decides go/hold.",
            links=links)

    # 2) find the post-build UI-test run (data availability). Not run yet -> hold.
    tb = mock_input("test_build", MISSING)
    if tb is MISSING:
        ok, tb, detail = P.find_auth_ui_test_build(build_id)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not query the post-build UI tests ({detail}){hint}.",
                           links=links)
    if not tb:
        # The test auto-triggers off build completion (PR 16976328); it just hasn't run yet.
        return InProgress(
            f"{label} build {build_id} is green, but its post-build UI-test run hasn't "
            f"appeared yet — Scout is polling every 30 min and will capture it once it runs.",
            links=links)
    links.append({"name": f"{label} UI tests", "url": auth_build_url(tb)})

    # 3) read the per-suite pass rates (the DATA the RC report gates on) + compute the
    # informational verdict. This step does NOT enforce it — rc_report consolidates MRWP +
    # auth and makes the go/hold decision.
    suites = mock_input("suites", MISSING)
    capture = mock_input("capture", MISSING)
    test_status = mock_input("test_status", MISSING)
    if test_status is MISSING:
        if suites is not MISSING or capture is not MISSING:
            test_status = "completed"  # Injected suites are a completed offline observation.
        else:
            ok, test_status, _, detail = P.get_build_status(AUTH_ORG, AUTH_PROJECT, tb)
            if not ok or not test_status:
                return Blocked(f"{label}: could not read UI-test build status ({detail}).", links=links)
    if test_status != "completed":
        return InProgress(f"{label} UI-test run {tb} is still running (status: {test_status}).",
                          links=links)
    if capture is MISSING:
        ok, capture, detail = P.collect_auth_ui_evidence(tb, build_id, rc_num)
        if not ok:
            return Blocked(f"{label}: could not read UI test results for run {tb} ({detail}).",
                           links=links)
    if suites is MISSING:
        suites = capture["suites"]
    gate = auth_gate(suites)

    # 4) snapshot the whole leg into the RC iteration (its own report section).
    snapshot = {
        "build": {"run_id": str(build_id), "rc": rc_num, "version": ab.get("version"),
                  "build_number": ab.get("build_number"),
                  "result": result, "complete": True},
        "test": {"run_id": str(tb), "complete": True, "suites": suites,
                 "evidence": capture["evidence"]},
        "verdict": gate["verdict"],
    }
    issues = auth_evidence_issues({"rc": rc_num, "auth": snapshot})
    if issues:
        return Blocked("; ".join(issues) + "; retry verification when results are available.",
                       links=links)
    stash_auth(state, rc_num, snapshot)
    # Always Done: the build ran and the data is captured. A sub-90% result is DATA for the
    # RC report, not a block here (mirrors MRWP, where failing tests don't block verify).
    bar = "clears the 90% bar" if gate["verdict"] == "clean" else "is BELOW the 90% bar"
    return Done(
        f"{label} build {build_id} + UI tests captured — {bar}; the RC report consolidates "
        f"this with MRWP and decides go/hold.", links=links)


def build(state):
    return verify_auth_ecs(state)


run = legacy_run(build)
