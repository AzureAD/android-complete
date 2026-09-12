"""Step: `ui_test_status` — fill BOTH the Broker + Authenticator UI suites from the RC
pipelines, and reassign failed auth automation to the release owner (Phase 3, bug_bash;
runs right before `distribute_tests`).

BROKER: maps validated current-RC ECS/Local snapshots, with no pipeline refetch.
Distinct titles/API suites mapping to one plan
point use failed-wins (only retries of the SAME exact title get pass-any, in Phase 2).
Unmapped source tests and untouched plan points are diagnosed explicitly.

AUTHENTICATOR: this step ALSO fills the Authenticator bug-bash suite (from `clone_plans_auth`)
from this release's auth ECS post-build UI-test run (`state.rcs[-1].auth.test`, captured by
the Phase-2 `auth_ecs` step). For the automated cases only (join on `test_<caseId>_`), it
writes Passed->Passed / Failed->Failed and LEAVES manual cases untouched; reassignment of
applied FAILED cases to the release owner (`state.owner_email`) is attempted for triage.
Monthly UI Tests is intentionally report-only; all its failures remain owner investigations.

Depends on `clone_plans_broker` (and, for the auth fill, `clone_plans_auth` + the Phase-2 RC
runs). Missing, incomplete or stale evidence for either app blocks BEFORE external writes.
Refresh evidence in Phase 2, then re-run this step to fill from the new snapshot.

REASSIGNMENT: only cases actually written FAILED — Broker AND Authenticator — are
submitted for release-owner assignment in ADO. Assignment errors are nonblocking and
recorded separately, never treated as successful owner changes. Then the
downstream `ui_failures` human reminder is forward-populated with the full per-test list.

Mock knobs (mocks.local.yaml / tests):
  fail          : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.build_verify._common import latest_rc
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import testplans as T
from tools import pipelines as P
from tools import distribution as D
from steps.bug_bash import ui_results

ID = "ui_test_status"
KIND = "agent"

ORG = T.ORG
PROJECT = T.PROJECT

MOCKABLE = {
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _broker_plan_id(state):
    """The release's cloned Broker plan id (from clone_plans_broker), or None."""
    return (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _auth_suite_id(state):
    """The release's Authenticator bug-bash suite id (from clone_plans_auth), or None."""
    return (state.get_step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")


def _fill_auth(state, notes, projection):
    """Write only validated mapped cases; retain report-only failures and assignment errors."""
    suite_id = _auth_suite_id(state)
    outcomes = {cid: v["outcome"] for cid, v in projection["cases"].items()
                if v["outcome"] in ("Passed", "Failed")}
    case_titles = {cid: "; ".join(v["titles"]) for cid, v in projection["cases"].items()}

    ok, summ, d = T.fill_auth_ui_results(T.AUTH_PLAN, suite_id, outcomes)
    step = state.get_step("bug_bash", ID)
    step.data["auth"] = {"suite_id": suite_id, "mapping": summ,
                         "failures": projection["failures"], "provenance": projection["provenance"]}
    state.set_step("bug_bash", ID, step)
    state.checkpoint()
    if not ok:
        return False, f"Could not completely fill auth UI results ({d}); retry with current evidence"
    if (not isinstance(summ, dict)
            or summ.get("target") != {"plan_id": int(T.AUTH_PLAN), "suite_id": int(suite_id)}
            or not isinstance(summ.get("applied_points"), list)):
        return False, "Missing actual applied Authenticator target/points; retry ui_test_status"

    # Only acknowledged Failed point writes may trigger an owner assignment.
    failed_ids = sorted({p["case_id"] for p in summ["applied_points"] if p["outcome"] == "Failed"})
    owner = state.owner_email
    assigned = 0
    errors = []
    if owner:
        for cid in failed_ids:
            oka, detail = D.set_assigned_to(cid, owner)
            if oka:
                assigned += 1
            else:
                errors.append({"case_id": cid, "detail": detail})
    elif failed_ids:
        notes.append("No release owner on record — failed auth cases not reassigned (set-owner).")

    # Titles for the failed cases (for the ui_failures render), keyed by str(case_id).
    failed_titles = {}
    for cid in failed_ids:
        try:
            t = case_titles.get(int(cid))
        except (TypeError, ValueError):
            t = None
        if t:
            failed_titles[str(cid)] = t

    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["auth"] = {"suite_id": suite_id, "passed": summ.get("set_passed", 0),
                         "failed": summ.get("set_failed", 0), "failed_case_ids": failed_ids,
                         "failed_case_titles": failed_titles,
                         "failures": projection["failures"], "provenance": projection["provenance"],
                         "mapping": summ,
                         "assignment_errors": errors,
                         "failed_assigned_to_owner": assigned}
    state.set_step("bug_bash", ID, step)
    owner_note = (f"; {assigned} failed case(s) assigned to owner {owner}"
                  if assigned else "")
    if errors:
        notes.append(f"Auth reassignment incomplete: {len(errors)} case(s); see auth.assignment_errors.")
    return True, (f"Auth: {summ.get('set_passed', 0)} Passed, {summ.get('set_failed', 0)} Failed "
                  f"filled in suite {suite_id}{owner_note}. {P.AUTH_MAPPING_NOTE}")


def _reassign_broker_failures(state, ids, notes):
    """Reassign every failing Broker UI case to the release owner for investigation — mirrors the
    auth reassignment in _fill_auth so BOTH apps' failures land in the owner's queue. Stores the
    ids + assigned count on the step. Best-effort: a case with no parseable id is skipped, and a
    missing owner is noted rather than fatal."""
    owner = state.owner_email
    assigned = 0
    errors = []
    if ids and owner:
        for cid in ids:
            oka, detail = D.set_assigned_to(cid, owner)
            if oka:
                assigned += 1
            else:
                errors.append({"case_id": cid, "detail": detail})
    elif ids and not owner:
        notes.append("No release owner on record — failed Broker UI cases not reassigned (set-owner).")
    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["broker"] = {"failed_case_ids": ids, "failed_assigned_to_owner": assigned,
                           "assignment_errors": errors}
    state.set_step("bug_bash", ID, step)
    if errors:
        notes.append(f"Broker reassignment incomplete: {len(errors)} case(s) failed; "
                     "see broker.assignment_errors and retry.")
    return assigned


def _replace_failure_reminder(state, note, links, broker_count, failed_ids):
    """Replace only this producer's note prefix/links/data, never human status or notes."""
    import hashlib

    def digest(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    step = state.get_step("bug_bash", "ui_failures")
    data = dict(step.data or {})
    generated = data.pop("ui_test_status_generated", {})
    human_note = step.note or ""
    old_links = generated.get("links", [])
    length = generated.get("note_length", 0)
    if length:
        start = human_note.find("\U0001f9ea")
        if start >= 0 and digest(human_note[start:start + length]) == generated.get("note_sha256"):
            human_note = (human_note[:start] + human_note[start + length:]).strip("\n")
    step.note = "\n".join(part for part in (note, human_note) if part)
    step.links = [link for link in (step.links or []) if link not in old_links]
    new_links = [link for link in links if link not in step.links]
    step.links.extend(new_links)
    for key in ("broker_failed_tests", "auth_failed_cases", "auth_failed_tests"):
        data.pop(key, None)
    if note:
        data.update(broker_failed_tests=broker_count, auth_failed_cases=failed_ids,
                    ui_test_status_generated={"note_length": len(note), "note_sha256": digest(note),
                                              "links": new_links})
    step.data = data
    state.set_step("bug_bash", "ui_failures", step)


def _surface_ui_failures(state, rc, failures):
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
        return f"{ORG}/{PROJECT}/_build/results?buildId={rid}"

    def _case_url(cid):
        return f"{ORG}/{PROJECT}/_workitems/edit/{cid}"

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
    astep = (state.get_step("bug_bash", ID).data or {}).get("auth") or {}
    failed_ids = astep.get("failed_case_ids") or []
    auth_failures = astep.get("failures") or []
    auth = rc.get("auth") or {}

    if not broker_count and not auth_failures:
        _replace_failure_reminder(state, "", [], 0, [])
        return

    # ---- build the step-8-style markdown note ----
    try:
        from orchestrator import schedule
        month_year = schedule.target_month_label(state)
    except Exception:
        month_year = ""
    owner = state.owner_email or "the release owner"
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
    _replace_failure_reminder(state, note, links, broker_count, failed_ids)
    step = state.get_step("bug_bash", "ui_failures")
    step.data["auth_failed_tests"] = auth_failures
    state.set_step("bug_bash", "ui_failures", step)


def build(state):
    """Invalidate durably before work; publish only after both required fills succeeded."""
    from uuid import uuid4

    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    for key in ("auth", "broker", "summary", "provenance", "fill_status"):
        step.data.pop(key, None)
    step.data["result"] = {"id": uuid4().hex, "status": "incomplete", "stage": "validation"}
    step.data["fill_status"] = "incomplete"
    state.set_step("bug_bash", ID, step)
    # A missing persistence capability must prevent even the first provider write.
    state.checkpoint()
    try:
        outcome = _build(state)
        if isinstance(outcome, Done):
            step = state.get_step("bug_bash", ID)
            result = step.data["result"]
            for product, summary in (("broker", step.data["summary"]),
                                     ("auth", step.data["auth"]["mapping"])):
                if summary.get("target") != result["binding"][product] or not isinstance(
                        summary.get("applied_points"), list):
                    raise ValueError(f"Missing actual applied {product} target/points; retry ui_test_status")
                result[product] = {
                    "target": summary["target"], "applied_points": summary["applied_points"],
                    "automated_case_ids": result[product]["automated_case_ids"],
                    "failed_case_ids": sorted({p["case_id"] for p in summary["applied_points"]
                                               if p["outcome"] == "Failed"}),
                }
            if result["binding"] != ui_results.current_binding(state):
                raise ValueError("RC/build/target binding changed during fill; retry ui_test_status")
            result.update(status="complete", stage="complete")
            step.data["fill_status"] = "complete"
            state.set_step("bug_bash", ID, step)
            ui_results.completed_result(state)
        else:
            step = state.get_step("bug_bash", ID)
            step.data["result"]["error"] = outcome.reason
            state.set_step("bug_bash", ID, step)
        state.checkpoint()
    except (ValueError, OSError) as exc:
        step = state.get_step("bug_bash", ID)
        step.data["result"].update(status="incomplete", error=str(exc))
        step.data["fill_status"] = "incomplete"
        state.set_step("bug_bash", ID, step)
        outcome = Blocked(f"ui_test_status: {exc}. Results may be partially applied; retry this step.")
        state.checkpoint()
    return outcome


def _build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"ui_test_status: {fail}")

    plan_id = _broker_plan_id(state)
    if not plan_id:
        return Blocked("ui_test_status: the Broker test plan hasn't been cloned yet "
                       "(clone_plans_broker) — run that first so the UI Automation suite exists.")

    rc = latest_rc(state)
    ok, projection, d = P.project_mrwp_ui_results(rc)
    if not ok:
        return Blocked(f"ui_test_status: {d}.")
    ok, auth_projection, d = P.project_auth_ui_results(rc)
    if not ok:
        return Blocked(f"ui_test_status: {d}. No test-plan writes attempted.")
    if not _auth_suite_id(state):
        return Blocked("ui_test_status: Authenticator release suite missing; run clone_plans_auth first.")

    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["plan_id"] = plan_id
    step.data["provenance"] = projection["provenance"]
    binding = ui_results.current_binding(state)
    step.data["result"].update(
        binding=binding, stage="broker_write",
        broker={"automated_case_ids": sorted(projection["verdicts"])},
        auth={"automated_case_ids": sorted(cid for cid, case in auth_projection["cases"].items()
                                            if case["outcome"] in ("Passed", "Failed"))},
        investigations={"broker": projection["failures"], "auth": auth_projection["failures"],
                        "unmapped_broker": [s for p in projection["provenance"]["providers"]
                                            for s in p["skipped_mapping"]],
                        "report_only_or_unmapped_auth": [s for s in auth_projection["sources"]
                                                        if s["status"] != "mapped"]})
    state.set_step("bug_bash", ID, step)
    state.checkpoint()
    ok, summ, d = T.fill_ui_automation_results(
        plan_id, projection["verdicts"], suite_id=binding["broker"]["suite_id"])
    step.data["summary"] = summ
    step.data["result"]["stage"] = "auth_write" if ok else "broker_write"
    state.set_step("bug_bash", ID, step)
    state.checkpoint()
    if not ok:
        return Blocked(f"ui_test_status: couldn't completely fill the UI Automation results ({d}). "
                       "Some points may already have changed; retry with current evidence.")
    if (not isinstance(summ, dict) or summ.get("target") != binding["broker"]
            or not isinstance(summ.get("applied_points"), list)):
        return Blocked("Missing actual applied Broker target/points; retry ui_test_status")

    # Also fill the Authenticator bug-bash suite from the captured auth ECS evidence;
    # every FAILED automated auth case is reassigned to the release owner for triage.
    notes = []
    skipped = sum(len(p["skipped_mapping"]) for p in projection["provenance"]["providers"])
    if skipped:
        notes.append(f"{skipped} source test(s) skipped mapping; see provenance for exact titles/reasons.")
    untouched = len(summ.get("untouched_points") or [])
    unmatched = len(summ.get("unmatched_verdicts") or [])
    if untouched or unmatched:
        notes.append(f"{untouched} plan point(s) left untouched; {unmatched} projected verdict(s) "
                     "had no matching plan point; see summary mapping diagnostics.")
    # Store only this attempt's Auth write diagnostics.
    step.data.pop("auth", None)
    state.set_step("bug_bash", ID, step)
    ok, auth_note = _fill_auth(state, notes, auth_projection)
    if not ok:
        return Blocked(f"ui_test_status: {auth_note}. Broker results may already have changed.")

    # Reassign every FAILED Broker UI case to the release owner too — all UI failures (Broker +
    # Auth) are the owner's to investigate.
    failed_broker_ids = sorted({p["case_id"] for p in summ["applied_points"] if p["outcome"] == "Failed"})
    broker_assigned = _reassign_broker_failures(state, failed_broker_ids, notes)

    # Forward-populate the downstream `ui_failures` human-review reminder with the combined
    # Broker + Authenticator Phase-2 UI failure list (it has no module of its own).
    _surface_ui_failures(state, rc, projection["failures"])

    p, f, na = summ.get("set_passed", 0), summ.get("set_failed", 0), summ.get("set_not_applicable", 0)
    tail = (f" {auth_note}." if auth_note else "")
    if broker_assigned:
        tail += f" {broker_assigned} failed Broker UI case(s) assigned to owner {state.owner_email}."
    if notes:
        tail += " " + " ".join(notes)
    links = [{"name": f"Broker UI Automation (plan {plan_id})", "url": T.plan_web_url(plan_id)}]
    astep = (state.get_step("bug_bash", ID).data or {}).get("auth") or {}
    if astep.get("suite_id"):
        links.append({"name": f"Authenticator results (suite {astep['suite_id']})",
                      "url": T.plan_web_url(T.AUTH_PLAN, astep["suite_id"])})
    return Done(
        f"Filled UI Automation results in Broker plan {plan_id}: {p + f + na} test points "
        f"({p} Passed, {f} Failed, {na} N/A) across {summ.get('cases_touched', 0)} cases."
        f"{tail}",
        links=links)


run = legacy_run(build)
