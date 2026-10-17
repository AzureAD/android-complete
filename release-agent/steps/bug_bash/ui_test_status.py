"""Step: `ui_test_status` — fill BOTH the Broker + Authenticator UI suites from the RC
pipelines, and assign failed release-point Testers to the release owner (Phase 3, bug_bash;
runs right before `distribute_tests`).

BROKER: maps validated current-RC ECS/Local snapshots, with no pipeline refetch.
Distinct titles/API suites mapping to one plan
point use failed-wins (only retries of the SAME exact title get pass-any, in Phase 2).
Unmapped source tests and untouched plan points are diagnosed explicitly.

AUTHENTICATOR: this step ALSO fills the Authenticator bug-bash suite (from `clone_plans_auth`)
from this release's auth ECS post-build UI-test run (`state.rcs[-1].auth.test`, captured by
the Phase-2 `auth_ecs` step). For the automated cases only (join on `test_<caseId>_`), it
writes Passed->Passed / Failed->Failed and LEAVES manual cases untouched; reassignment of
applied FAILED points to the release owner (`state.owner_email`) is attempted for triage.
Monthly UI Tests is intentionally report-only; all its failures remain owner investigations.

Depends on `clone_plans_broker` (and, for the auth fill, `clone_plans_auth` + the Phase-2 RC
runs). Missing, incomplete or stale evidence for either app blocks BEFORE external writes.
Refresh evidence in Phase 2, then re-run this step to fill from the new snapshot.

TRIAGE: only acknowledged FAILED points — Broker AND Authenticator — are
submitted for release-specific Tester assignment, never shared System.AssignedTo.
Assignment errors are nonblocking and
recorded separately, never treated as successful owner changes. Then the
downstream `ui_failures` human reminder is forward-populated with the full per-test list.

Mock knobs (mocks.local.yaml / tests):
  fail          : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext, thaw
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

from orchestrator.outcomes import Done, Blocked
from orchestrator.evidence import StepData, UIFailureReminder
from steps.build_verify._common import latest_rc
from steps.lib.mockctx import MISSING
from tools import testplans as T
from tools import pipelines as P
from tools import distribution as D
from steps.bug_bash import ui_results

from orchestrator.authority import OwnStepData, UIFailureContribution, WriteOperation

EVIDENCE = (OwnStepData(), UIFailureContribution("bug_bash.ui_failures"))
WRITES = (WriteOperation.FILL_AUTH_UI_RESULTS, WriteOperation.FILL_UI_AUTOMATION_RESULTS,
          WriteOperation.SET_POINT_TESTERS)
ID = "ui_test_status"
KIND = "agent"
EFFECT_MODE = "idempotent"
EFFECT_RECOVERY = "match_current"


def prepare_effect(context):
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"ui_test_status: {fail}")
    plan_id = _broker_plan_id(context)
    if not plan_id:
        return Blocked(
            "ui_test_status: the Broker test plan hasn't been cloned yet "
            "(clone_plans_broker) — run that first so the UI Automation suite exists."
        )
    rc = latest_rc(context)
    ok, projection, detail = P.project_mrwp_ui_results(rc)
    if not ok:
        return Blocked(f"ui_test_status: {detail}.")
    ok, auth_projection, detail = P.project_auth_ui_results(rc)
    if not ok:
        return Blocked(
            f"ui_test_status: {detail}. No test-plan writes attempted."
        )
    if not _auth_suite_id(context):
        return Blocked(
            "ui_test_status: Authenticator release suite missing; run "
            "clone_plans_auth first."
        )
    try:
        current_binding = ui_results.current_binding(context)
        destination_names = D.release_target_names(
            context.evidence.step("bug_bash", "clone_plans_broker").data,
            context.evidence.step("bug_bash", "clone_plans_auth").data)
        context.services.testplans.validate_release_targets(
            context.release.release_id, context.release.ccd, current_binding,
            expected_names=destination_names)
    except ValueError as exc:
        return Blocked(f"ui_test_status: {exc}")
    auth = rc.get("auth") or {}
    binding = {
        "release": context.release.release_id,
        "rc": rc.get("rc"),
        "ecs": (rc.get("ecs") or {}).get("run_id"),
        "local": (rc.get("local") or {}).get("run_id"),
        "auth_build": (auth.get("build") or {}).get("run_id"),
        "auth_test": (auth.get("test") or {}).get("run_id"),
        "broker_plan": _broker_plan_id(context),
        "auth_suite": _auth_suite_id(context),
    }
    frozen_broker = _freeze_broker_projection(projection)
    frozen_auth = _freeze_auth_projection(auth_projection)
    evidence = {
        "binding": binding,
        "pipeline_runs": context.evidence.pipeline_runs,
        "current_binding": current_binding,
        "broker_projection": frozen_broker,
        "auth_projection": frozen_auth,
    }
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "binding": current_binding,
        "destination_names": destination_names,
        "broker_plan_id": plan_id,
        "evidence_sha256": digest,
        "broker_projection": frozen_broker,
        "auth_projection": frozen_auth,
        "rc": {"rc": rc["rc"],
               "ecs": {"run_id": binding["ecs"]}, "local": {"run_id": binding["local"]},
               "auth": {"build": {"run_id": binding["auth_build"]},
                        "test": {"run_id": binding["auth_test"]}}},
        "owner_email": context.release.owner_email,
        "coordinates": {
            "test_plan_org": T.ORG,
            "test_plan_project": T.PROJECT,
        },
    }


def _freeze_broker_projection(projection):
    frozen = deepcopy(projection)
    frozen["verdicts"] = [
        {
            "case_id": case_id,
            "flight": key[0],
            "suite": key[1],
            "outcome": outcome,
        }
        for case_id, results in projection["verdicts"].items()
        for key, outcome in results.items()
    ]
    return frozen


def _thaw_broker_projection(frozen):
    projection = deepcopy(frozen)
    verdicts = {}
    for item in projection.pop("verdicts", []):
        verdicts.setdefault(int(item["case_id"]), {})[
            (item["flight"], item["suite"])
        ] = item["outcome"]
    projection["verdicts"] = verdicts
    return projection


def _freeze_auth_projection(projection):
    frozen = deepcopy(projection)
    frozen["cases"] = [
        {"case_id": case_id, **case}
        for case_id, case in projection["cases"].items()
    ]
    return frozen


def _thaw_auth_projection(frozen):
    projection = deepcopy(frozen)
    projection["cases"] = {
        int(case.pop("case_id")): case
        for case in projection.get("cases", [])
    }
    return projection


def execute(context: StepContext):
    return _run_effect(context, thaw(context.effect.execution["effect_input"]))

ORG = T.ORG
PROJECT = T.PROJECT

MOCKABLE = {
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _broker_plan_id(context):
    """The release's cloned Broker plan id (from clone_plans_broker), or None."""
    return (context.evidence.step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _auth_suite_id(context):
    """The release's Authenticator bug-bash suite id (from clone_plans_auth), or None."""
    return (context.evidence.step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")


def _prior_applied_points(data, binding):
    """Reuse only acknowledged points from this release and these exact destinations."""
    data = thaw(data or {})
    result = data.get("result") or {}
    previous = result.get("binding") or {}
    receipts = {"broker": [], "auth": []}
    if previous.get("release") != binding["release"]:
        return receipts
    for product, partial in (("broker", data.get("summary")),
                             ("auth", (data.get("auth") or {}).get("mapping"))):
        if previous.get(product) != binding[product]:
            continue
        candidates = [result.get(product), partial]
        points = {}
        for record in candidates:
            if (not isinstance(record, dict) or record.get("target") != binding[product]
                    or not isinstance(record.get("applied_points"), list)):
                continue
            validated = ui_results.applied_record(
                record, {p.get("case_id") for p in record["applied_points"] if isinstance(p, dict)},
                product, binding[product])
            for point in validated["applied_points"]:
                points[int(point["point_id"])] = point
        receipts[product] = list(points.values())
    return receipts


def _retain_applied_points(summary, prior_points, product, target):
    """A failed retry cannot erase acknowledged writes from its earlier attempt."""
    if not isinstance(summary.get("applied_points"), list):
        raise ValueError("Failed UI write returned a malformed applied-point receipt")
    ui_results.applied_record(
        summary, {p.get("case_id") for p in summary["applied_points"] if isinstance(p, dict)},
        product, target)
    retained = {int(point["point_id"]): deepcopy(point) for point in prior_points or []}
    retained.update({int(point["point_id"]): deepcopy(point) for point in summary["applied_points"]})
    return {**summary, "applied_points": list(retained.values())}


def _fill_auth(context, notes, projection, binding, owner, coordinates, prior_points=None):
    """Write only validated mapped cases; retain report-only failures and assignment errors."""
    suite_id = binding["auth"]["suite_id"]
    plan_id = binding["auth"]["plan_id"]
    outcomes = {cid: v["outcome"] for cid, v in projection["cases"].items()
                if v["outcome"] in ("Passed", "Failed")}
    case_titles = {cid: "; ".join(v["titles"]) for cid, v in projection["cases"].items()}

    ok, summ, d = context.effect.services.fill_auth_ui_results(
        plan_id,
        suite_id,
        outcomes,
        org=coordinates["test_plan_org"],
        project=coordinates["test_plan_project"],
        **({"prior_points": prior_points} if prior_points else {}),
    )
    if not ok and isinstance(summ, dict):
        summ = _retain_applied_points(summ, prior_points, "auth", binding["auth"])
    data = thaw(context.recovery().step("bug_bash", ID).data or {})
    previous_mapping = (data.get("auth") or {}).get("mapping")
    data["auth"] = {"suite_id": suite_id, "mapping": summ if isinstance(summ, dict) else previous_mapping,
                         "failures": projection["failures"], "provenance": projection["provenance"]}
    context.effect.commit.commit(StepData(data))
    if not ok:
        return False, f"Could not completely fill auth UI results ({d}); retry with current evidence"
    if (not isinstance(summ, dict)
            or summ.get("target") != {"plan_id": int(plan_id), "suite_id": int(suite_id)}
            or not isinstance(summ.get("applied_points"), list)):
        return False, "Missing actual applied Authenticator target/points; retry ui_test_status"
    ui_results.applied_record(summ, outcomes, "auth", binding["auth"])

    # Only acknowledged Failed point writes may trigger an owner assignment.
    failed_ids = sorted({p["case_id"] for p in summ["applied_points"] if p["outcome"] == "Failed"})
    assigned, errors = _assign_failure_points(context, summ, owner, coordinates)
    if failed_ids and not owner:
        notes.append("No release owner on record - failed Auth point Testers unchanged (set-owner).")

    # Titles for the failed cases (for the ui_failures render), keyed by str(case_id).
    failed_titles = {}
    for cid in failed_ids:
        try:
            t = case_titles.get(int(cid))
        except (TypeError, ValueError):
            t = None
        if t:
            failed_titles[str(cid)] = t

    data = thaw(context.recovery().step("bug_bash", ID).data or {})
    data = dict(data or {})
    data["auth"] = {"suite_id": suite_id, "passed": summ.get("set_passed", 0),
                         "failed": summ.get("set_failed", 0), "failed_case_ids": failed_ids,
                         "failed_case_titles": failed_titles,
                         "failures": projection["failures"], "provenance": projection["provenance"],
                         "mapping": summ,
                         "assignment_errors": errors,
                         "failed_assigned_to_owner": assigned}
    context.effect.commit.commit(StepData(data))
    owner_note = (f"; {assigned} failed case(s) assigned to owner {owner}"
                  if assigned else "")
    if errors:
        notes.append(f"Auth reassignment incomplete: {len(errors)} case(s); see auth.assignment_errors.")
    return True, (f"Auth: {summ.get('set_passed', 0)} Passed, {summ.get('set_failed', 0)} Failed "
                  f"filled in suite {suite_id}{owner_note}. {P.AUTH_MAPPING_NOTE}")


def _assign_failure_points(context, summary, owner, coordinates):
    """Only applied failure points in this release are eligible for owner triage."""
    failed = [p for p in summary["applied_points"] if p["outcome"] == "Failed"]
    ids = sorted({p["case_id"] for p in failed})
    errors = []
    if not failed:
        return 0, errors
    if not owner:
        return 0, [{"case_id": cid, "detail": "Release owner missing; point Tester unchanged"} for cid in ids]
    for cid in ids:
        points = [p["point_id"] for p in failed if p["case_id"] == cid]
        ok, detail = context.effect.services.set_point_testers(
                summary["target"]["plan_id"], summary["target"]["suite_id"], points, owner,
                expected_points={str(p["point_id"]): {
                    key: p[key] for key in ("case_id", "config_id") if key in p}
                    for p in failed if p["case_id"] == cid},
                org=coordinates["test_plan_org"],
                project=coordinates["test_plan_project"])
        if not ok:
            errors.append({"case_id": cid, "point_ids": points, "detail": detail})
    return len(ids) - len(errors), errors


def _reassign_broker_failures(context, summary, notes, owner, coordinates):
    """Record release-point Tester triage results, separate from outcome-fill success."""
    ids = sorted({p["case_id"] for p in summary["applied_points"] if p["outcome"] == "Failed"})
    assigned, errors = _assign_failure_points(context, summary, owner, coordinates)
    data = thaw(context.recovery().step("bug_bash", ID).data or {})
    data = dict(data or {})
    data["broker"] = {"failed_case_ids": ids, "failed_assigned_to_owner": assigned,
                           "assignment_errors": errors}
    context.effect.commit.commit(StepData(data))
    if errors:
        notes.append(f"Broker reassignment incomplete: {len(errors)} case(s) failed; "
                     "see broker.assignment_errors and retry.")
    return assigned


def _replace_failure_reminder(context, note, links, broker_count, failed_ids):
    """Replace only this producer's note prefix/links/data, never human status or notes."""
    auth = context.recovery().step("bug_bash", ID).data.get("auth") or {}
    context.effect.commit.commit(UIFailureReminder(
        note, tuple(links), broker_count, tuple(failed_ids), tuple(auth.get("failures") or ()),
        gate_preconditions=ui_results.reminder_preconditions(context)))


def _surface_ui_failures(context, rc, failures, coordinates):
    """Forward-populate the downstream `ui_failures` HUMAN-review reminder with the combined
    Phase-2 UI failure list — BOTH the Broker MRWP UI suites AND the Authenticator ECS failed
    cases — so the engineer sees everything in one place when the engine holds at that step.

    `ui_failures` is a bare human reminder (no module), so it can't compute anything itself;
    this producer step (which just filled both suites and knows the auth failures) writes the
    reminder's note + links. The note is a rich, step-8-style markdown block: an emoji header, a
    bold summary line, then EVERY failing UI test listed individually (never collapsed to a
    per-suite stat) — Broker MRWP and Authenticator ECS alike — each flagged 🔬 as an
    investigation the RELEASE OWNER owns, with an inline link to its ADO test case. Only sets
    note/links/data — never status — so the step stays a pending human hold. No failures
    anywhere -> clears the stale generated reminder, preserving human work."""
    links = []

    def _eng_run_url(rid):
        return (
            f"{coordinates['test_plan_org']}/"
            f"{coordinates['test_plan_project']}/_build/results?buildId={rid}"
        )

    def _case_url(cid):
        return (
            f"{coordinates['test_plan_org']}/"
            f"{coordinates['test_plan_project']}/_workitems/edit/{cid}"
        )

    # Broker MRWP UI failures — every failing test, GROUPED by provider (ECS/Local) then by
    # bucket (suite name, e.g. 'PROD MSAL - RC Broker (API 32)'). Preserves discovery order.
    #   broker = { "ECS": {suite_name: [{title, case_id}, …]}, "Local": {…} }
    from collections import OrderedDict
    broker = OrderedDict()
    broker_count = len(failures)
    for test in failures:
        buckets = broker.setdefault(test["flight"], OrderedDict())
        buckets.setdefault(test["suite"], []).append(test)
    for prov, label in (("ecs", "ECS"), ("local", "Local")):
        links.append({"name": f"MRWP {label} run", "url": _eng_run_url(rc[prov]["run_id"])})

    # Authenticator ECS failures — the failed automated case ids from the fill.
    astep = (context.recovery().step("bug_bash", ID).data or {}).get("auth") or {}
    failed_ids = astep.get("failed_case_ids") or []
    auth_failures = astep.get("failures") or []
    auth = rc.get("auth") or {}

    if not broker_count and not auth_failures:
        _replace_failure_reminder(context, "", [], 0, [])
        return

    # ---- build the step-8-style markdown note ----
    from orchestrator import schedule
    month_year = schedule.target_month_label(context.release)
    owner = context.release.owner_email or "the release owner"
    title = f"\U0001f9ea {month_year + ' ' if month_year else ''}Bug Bash \u2014 UI failures to investigate"

    total = broker_count + len(auth_failures)
    summary = (f"**{total} failing UI test(s)** across Broker MRWP + Authenticator ECS \u2014 "
               f"for {owner} to investigate (flake vs real bug). "
               "See UI result-fill details for assignment status; unmapped tests have no case link.")
    lines = [title, summary]

    def _mark(cid, text):
        """A 🔬 investigate line — links the case when we could parse its id."""
        if cid:
            return f"- \U0001f52c [{cid}]({_case_url(cid)}) \u2014 {text}"
        return f"- \U0001f52c {text}"

    if broker_count:
        lines.append(f"**Broker (MRWP)** \u2014 {broker_count} failing test(s):")
        # separate by provider (ECS / Local), then by bucket (suite name)
        for label, buckets in broker.items():
            prov_n = sum(len(v) for v in buckets.values())
            lines.append(f"**{label}** \u2014 {prov_n} failing:")
            for suite, tests in buckets.items():
                lines.append(f"_{suite}_ ({len(tests)}):")
                for t in tests:
                    lines.append(_mark(t["case_id"], t["title"]))
                    for link in t.get("links", []):
                        lines.append(f"  [Source {link['run_id']}/{link['result_id']}]({link['url']})")
                        links.append({"name": f"Test {link['run_id']}/{link['result_id']}", "url": link["url"]})

    if auth_failures:
        lines.append(f"**Authenticator (ECS)** \u2014 {len(auth_failures)} failing distinct test(s):")
        lines.append(P.AUTH_MAPPING_NOTE)
        for failure in auth_failures:
            lines.append(_mark(failure["case_id"],
                               f"[{failure['suite']}; {failure['status']}] {failure['title']}"))
            for link in failure["links"]:
                lines.append(f"  [Source {link['run_id']}/{link['result_id']}]({link['url']})")
                links.append({"name": f"Test {link['run_id']}/{link['result_id']}", "url": link["url"]})
        for key, name in (("build", "Authenticator ECS build"), ("test", "Authenticator ECS UI tests")):
            rid = (auth.get(key) or {}).get("run_id")
            if rid:
                links.append({"name": name,
                              "url": f"https://msazure.visualstudio.com/One/_build/results?buildId={rid}"})

    lines.append("\u25b6 Once you've investigated and re-run all of these, just let me know and "
                 "I'll mark this step complete for you.")

    note = "\n".join(lines)
    _replace_failure_reminder(context, note, links, broker_count, failed_ids)


def build(context: StepContext):
    """Invalidate durably before work; publish only after both required fills succeeded."""
    if context.effect is None:
        raise ValueError("UI publication requires an authorized effect context")
    return execute(context)


def _run_effect(context, prepared):
    """Apply only the validated, frozen desired-state snapshot for this execution."""

    data = thaw(context.recovery().step("bug_bash", ID).data or {})
    data = dict(data or {})
    acknowledged = _prior_applied_points(data, prepared["binding"])
    prior = prepared.get("prior_points") or {}
    prepared = {**prepared, "prior_points": {
        product: list({int(p["point_id"]): p for p in
                       [*prior.get(product, []), *acknowledged[product]]}.values())
        for product in ("broker", "auth")}}
    # Keep existing receipt slots durable across the reset, including a crash before
    # either writer returns. They must not alter the frozen effect operation key.
    for product in ("broker", "auth"):
        points = prepared["prior_points"][product]
        if not points:
            continue
        receipt = {"target": prepared["binding"][product], "applied_points": points}
        if product == "broker":
            data["summary"] = {**(data.get("summary") or {}), **receipt}
        else:
            auth = data.setdefault("auth", {})
            auth["mapping"] = {**(auth.get("mapping") or {}), **receipt}
    for key in ("broker", "provenance", "fill_status"):
        data.pop(key, None)
    data["result"] = {"id": context.new_id(), "status": "incomplete", "stage": "validation",
                      "binding": prepared["binding"]}
    data["fill_status"] = "incomplete"
    context.effect.commit.commit(StepData(data))
    # A missing persistence capability must prevent even the first provider write.
    try:
        outcome = _apply_prepared(context, prepared)
        if isinstance(outcome, Done):
            data = thaw(context.recovery().step("bug_bash", ID).data or {})
            result = data["result"]
            for product, summary in (("broker", data["summary"]),
                                     ("auth", data["auth"]["mapping"])):
                if summary.get("target") != result["binding"][product] or not isinstance(
                        summary.get("applied_points"), list):
                    raise ValueError(f"Missing actual applied {product} target/points; retry ui_test_status")
                result[product] = ui_results.applied_record(
                    summary, result[product]["automated_case_ids"], product, result["binding"][product])
            if result["binding"] != prepared["binding"]:
                raise ValueError("UI fill result changed its frozen execution binding")
            result.update(status="complete", stage="complete")
            data["fill_status"] = "complete"
            context.effect.commit.commit(StepData(data))
            ui_results.completed_result(replace(context, evidence=context.recovery()))
        else:
            data = thaw(context.recovery().step("bug_bash", ID).data or {})
            data["result"]["error"] = outcome.reason
            context.effect.commit.commit(StepData(data))
    except (ValueError, OSError) as exc:
        data = thaw(context.recovery().step("bug_bash", ID).data or {})
        data["result"].update(status="incomplete", error=str(exc))
        data["fill_status"] = "incomplete"
        context.effect.commit.commit(StepData(data))
        outcome = Blocked(f"ui_test_status: {exc}. Results may be partially applied; retry this step.")
    return outcome


def _apply_prepared(context, prepared):
    binding = prepared["binding"]
    plan_id = prepared["broker_plan_id"]
    rc = prepared["rc"]
    projection = _thaw_broker_projection(prepared["broker_projection"])
    auth_projection = _thaw_auth_projection(prepared["auth_projection"])
    owner = prepared["owner_email"]
    coordinates = prepared["coordinates"]
    if ui_results.current_binding(context) != binding:
        return Blocked("UI execution binding changed; no test-plan writes attempted")
    context.services.testplans.validate_release_targets(
        context.release.release_id, context.release.ccd, binding,
        org=coordinates["test_plan_org"], project=coordinates["test_plan_project"],
        expected_names=prepared.get("destination_names"))

    data = thaw(context.recovery().step("bug_bash", ID).data or {})
    data = dict(data or {})
    data["plan_id"] = plan_id
    data["provenance"] = projection["provenance"]
    data["result"].update(
        binding=binding, stage="broker_write",
        broker={"automated_case_ids": sorted(projection["verdicts"])},
        auth={"automated_case_ids": sorted(cid for cid, case in auth_projection["cases"].items()
                                            if case["outcome"] in ("Passed", "Failed"))},
        investigations={"broker": projection["failures"], "auth": auth_projection["failures"],
                        "unmapped_broker": [s for p in projection["provenance"]["providers"]
                                            for s in p["skipped_mapping"]],
                        "report_only_or_unmapped_auth": [s for s in auth_projection["sources"]
                                                        if s["status"] != "mapped"]})
    context.effect.commit.commit(StepData(data))
    ok, summ, d = context.effect.services.fill_ui_automation_results(
        plan_id,
        projection["verdicts"],
        suite_id=binding["broker"]["suite_id"],
        org=coordinates["test_plan_org"],
        project=coordinates["test_plan_project"],
        **({"prior_points": prepared["prior_points"]["broker"]}
           if prepared["prior_points"]["broker"] else {}),
    )
    if not ok and isinstance(summ, dict):
        summ = _retain_applied_points(
            summ, prepared["prior_points"]["broker"], "broker", binding["broker"])
    if isinstance(summ, dict):
        data["summary"] = summ
    data["result"]["stage"] = "auth_write" if ok else "broker_write"
    context.effect.commit.commit(StepData(data))
    if not ok:
        return Blocked(f"ui_test_status: couldn't completely fill the UI Automation results ({d}). "
                       "Some points may already have changed; retry with current evidence.")
    if (not isinstance(summ, dict) or summ.get("target") != binding["broker"]
            or not isinstance(summ.get("applied_points"), list)):
        return Blocked("Missing actual applied Broker target/points; retry ui_test_status")
    ui_results.applied_record(summ, projection["verdicts"], "broker", binding["broker"])

    # Fill Authenticator from captured evidence, then triage acknowledged failure points.
    notes = []
    skipped = sum(len(p["skipped_mapping"]) for p in projection["provenance"]["providers"])
    if skipped:
        notes.append(f"{skipped} source test(s) skipped mapping; see provenance for exact titles/reasons.")
    untouched = len(summ.get("untouched_points") or [])
    unmatched = len(summ.get("unmatched_verdicts") or [])
    if untouched or unmatched:
        notes.append(f"{untouched} plan point(s) left untouched; {unmatched} projected verdict(s) "
                     "had no matching plan point; see summary mapping diagnostics.")
    # Retain acknowledged Auth points until the writer returns its next receipt.
    ok, auth_note = _fill_auth(
        context, notes, auth_projection, binding, owner, coordinates, prepared["prior_points"]["auth"]
    )
    if not ok:
        return Blocked(f"ui_test_status: {auth_note}. Broker results may already have changed.")

    # Triage only acknowledged failed Broker points in this release's exact UI suite.
    broker_assigned = _reassign_broker_failures(
        context, summ, notes, owner, coordinates
    )

    # Forward-populate the downstream `ui_failures` human-review reminder with the combined
    # Broker + Authenticator Phase-2 UI failure list (it has no module of its own).
    _surface_ui_failures(context, rc, projection["failures"], coordinates)

    p, f, na = summ.get("set_passed", 0), summ.get("set_failed", 0), summ.get("set_not_applicable", 0)
    tail = (f" {auth_note}." if auth_note else "")
    if broker_assigned:
        tail += f" {broker_assigned} failed Broker UI case(s) assigned to owner {owner}."
    if notes:
        tail += " " + " ".join(notes)
    links = [{
        "name": f"Broker UI Automation (plan {plan_id})",
        "url": T.plan_web_url(
            plan_id,
            org=coordinates["test_plan_org"],
            project=coordinates["test_plan_project"],
        ),
    }]
    astep = (context.recovery().step("bug_bash", ID).data or {}).get("auth") or {}
    if astep.get("suite_id"):
        links.append({"name": f"Authenticator results (suite {astep['suite_id']})",
                      "url": T.plan_web_url(
                          binding["auth"]["plan_id"],
                          astep["suite_id"],
                          org=coordinates["test_plan_org"],
                          project=coordinates["test_plan_project"],
                      )})
    return Done(
        f"Filled UI Automation results in Broker plan {plan_id}: {p + f + na} test points "
        f"({p} Passed, {f} Failed, {na} N/A) across {summ.get('cases_touched', 0)} cases."
        f"{tail}",
        links=links)
