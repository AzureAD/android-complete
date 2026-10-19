"""Release-point ownership, exact failure receipts and destination isolation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from orchestrator.authority import WriteOperation
from steps.bug_bash import distribute_tests as S, ui_results as R, ui_test_status as U
from steps.lib.mockctx import MISSING
from tools import bugbash as B, distribution as D, pipelines as P, testplans as T
from tests.test_distribution import inputs, state, inspect
from tests.test_bugbash_broker_triage import receipt, row, stub


def point(pid, cid, owner="old@example.test", outcome="Failed", config=84):
    return {"id": pid, "testCase": {"id": cid}, "configuration": {"id": config},
            "outcome": outcome, "state": "Completed",
            "assignedTo": {"id": "id-" + owner, "uniqueName": owner}}


@pytest.mark.parametrize("page", [
    {"value": [None]}, {"value": [{"id": "tester", "properties": "bad"}]},
    {"value": [{"id": "tester", "properties": {"Account": "bad"}}]},
])
def test_malformed_point_tester_lookup_is_an_explicit_hold(monkeypatch, page):
    monkeypatch.setattr(P, "_ado_rest_get", lambda *a: (True, page, ""))
    with pytest.raises(ValueError):
        D.point_tester({"id": "tester", "displayName": "Not an address"})


@pytest.mark.parametrize("field", ["testCaseReference", "results", "configuration"])
def test_progress_malformed_point_fields_do_not_crash_or_become_empty_work(monkeypatch, field):
    record = {"id": 1, "testCaseReference": {"id": 100},
             "configuration": {"id": 292}, "results": {"outcome": "Passed"},
             "tester": {"id": "tester", "uniqueName": "owner@example.test"}}
    record[field] = "malformed"
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, [record], ""))
    ok, result, detail = B._points_of_suite(900, 901)
    assert not ok and result is None and "Malformed" in detail


def test_rescheduled_ccd_requires_new_explicit_oof_confirmation():
    roster = [{"upn": "tester@example.test", "name": "Tester"}]
    confirmation = D.confirm_oof([], roster, "owner@example.test", "2026-09", ccd="2026-09-09")
    assert D.validate_oof(confirmation, roster, "owner@example.test", "2026-09",
                          ccd="2026-09-09") == []
    with pytest.raises(ValueError, match="ask again"):
        D.validate_oof(confirmation, roster, "owner@example.test", "2026-09", ccd="2026-09-16")
    refreshed = D.confirm_oof([], roster, "owner@example.test", "2026-09", ccd="2026-09-16")
    assert D.validate_oof(refreshed, roster, "owner@example.test", "2026-09",
                          ccd="2026-09-16") == []


def test_distribution_ignores_shared_assignedto_and_revision(state, inputs):
    _, first = inspect(state, inputs, oof=[])
    for case in inputs["broker_cases"] + inputs["auth_cases"]:
        case["assignee"], case["revision"] = "foreign-release@example.test", 999
    _, after = inspect(state, inputs)
    assert after["review_hash"] == first["review_hash"]
    assert after["_targets"] == first["_targets"]
    assert after["_current"] == first["_current"]


def test_ui_triage_authority_and_operations_are_point_scoped():
    assert WriteOperation.SET_POINT_TESTERS in U.WRITES
    assert WriteOperation.SET_ASSIGNED_TO not in U.WRITES
    calls = []
    ctx = SimpleNamespace(effect=SimpleNamespace(services=SimpleNamespace(
        set_point_testers=lambda *args, **kw: (calls.append((args, kw)) is None, ""))))
    summary = {"target": {"plan_id": 900, "suite_id": 901}, "applied_points": [
        {"point_id": 11, "case_id": 100, "outcome": "Failed"},
        {"point_id": 12, "case_id": 100, "outcome": "Passed"},
        {"point_id": 13, "case_id": 200, "outcome": "Failed"}]}
    assigned, errors = U._assign_failure_points(
        ctx, summary, "owner@example.test", {"test_plan_org": "org", "test_plan_project": "project"})
    assert (assigned, errors) == (2, [])
    assert [c[0] for c in calls] == [
        (900, 901, [11], "owner@example.test"), (900, 901, [13], "owner@example.test")]


def test_owner_triage_writes_only_failed_release_points_without_case_reads(monkeypatch):
    points = [point(1, 100), point(2, 100, outcome="Passed", config=85), point(3, 200)]
    before = deepcopy(points)
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, deepcopy(points), ""))
    monkeypatch.setattr(D, "resolve_tester_identity", lambda *a, **kw: "owner-id")
    monkeypatch.setattr(D, "case_assignment_snapshot", lambda *a: pytest.fail("No shared case read"))
    writes = []
    def send(url, method, body, timeout):
        assert "/Plans/900/Suites/901/points/1?" in url
        writes.append(body)
        points[0]["assignedTo"] = {"id": body["tester"]["id"]}
        return True, {}, ""
    monkeypatch.setattr(P, "_ado_rest_send", send)
    assert D.set_point_testers(900, 901, [1], "owner@example.test") == (True, "")
    assert writes == [{"tester": {"id": "owner-id"}}]
    assert points[1:] == before[1:]
    assert points[0]["outcome"] == "Failed"


@pytest.mark.parametrize("damage", ["missing", "resolved", "identity", "config"])
def test_triage_point_write_rejects_stale_identity_and_reads_back(monkeypatch, damage):
    points = [point(1, 100)]
    if damage == "missing":
        points = []
    elif damage == "resolved":
        points[0]["outcome"] = "Passed"
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, deepcopy(points), ""))
    monkeypatch.setattr(D, "resolve_tester_identity", lambda *a, **kw: "owner-id")
    def send(*args):
        if damage == "identity":
            points[0]["testCase"]["id"] = 200
        else:
            points[0]["configuration"]["id"] = 85
        points[0]["assignedTo"] = {"id": "owner-id"}
        return True, {}, ""
    monkeypatch.setattr(P, "_ado_rest_send", send)
    assert not D.set_point_testers(900, 901, [1], "owner@example.test")[0]


def test_progress_uses_each_release_point_tester_for_shared_case(monkeypatch):
    manual = [row(1, 100, outcome="Passed")]
    auth = [row(2, 100, outcome="Failed")]
    stub(monkeypatch, [], auth=auth, manual=manual)
    manual[0]["tester_upn"] = "broker@example.test"
    auth[0]["tester_upn"] = "auth@example.test"
    monkeypatch.setattr(D, "_cases_assignedto", lambda *a: pytest.fail("No shared case owner read"))
    ok, progress, detail = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[], broker_ui_result=receipt([]))
    assert ok, detail
    assert progress["total"] == 1 and progress["done"] == 0
    assert progress["owners"]["broker@example.test"]["tests"][0]["products"] == ["Broker"]
    assert progress["owners"]["broker@example.test"]["remaining"] == 0
    assert progress["owners"]["auth@example.test"]["tests"][0]["products"] == ["Authenticator"]
    assert progress["owners"]["auth@example.test"]["remaining"] == 1


def test_auth_triage_tracks_only_acknowledged_failure_points(monkeypatch):
    live = [row(1, 100, outcome="Passed"), row(2, 100, 328, "Unspecified")]
    stub(monkeypatch, [], auth=live)
    ok, progress, detail = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[100], auto_failed_ids=[100],
        broker_ui_result=receipt([]), auth_ui_result={
            "target": {"plan_id": 2, "suite_id": 3}, "applied_points": [
                {"point_id": 1, "case_id": 100, "outcome": "Failed"}]})
    assert ok, detail
    assert progress["total"] == progress["done"] == 1
    assert B.all_complete(progress)


def test_missing_auth_failure_point_cannot_disappear_as_completed_work(monkeypatch):
    stub(monkeypatch, [], auth=[row(2, 100, outcome="Passed")])
    ok, progress, detail = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[100], auto_failed_ids=[100],
        broker_ui_result=receipt([]), auth_ui_result={
            "target": {"plan_id": 2, "suite_id": 3}, "applied_points": [
                {"point_id": 1, "case_id": 100, "outcome": "Failed"}]})
    assert not ok and progress is None and "failure points missing" in detail


def test_shared_case_triage_counters_remain_product_specific(monkeypatch):
    stub(monkeypatch, [row(1, 100, outcome="Failed")], auth=[row(2, 100, outcome="Passed")])
    ok, progress, detail = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[100], auto_failed_ids=[100],
        broker_ui_result=receipt([row(1, 100)]), auth_ui_result={
            "target": {"plan_id": 2, "suite_id": 3}, "applied_points": [
                {"point_id": 2, "case_id": 100, "outcome": "Failed"}]})
    assert ok, detail
    assert progress["total"] == progress["remaining"] == progress["auto_failed_remaining"] == 1
    assert progress["auto_failed_remaining_by_product"] == {"Broker": 1, "Authenticator": 0}


def test_manual_owner_does_not_inherit_another_point_owners_triage_flag(monkeypatch):
    manual, failed = row(2, 100, outcome="Unspecified"), row(1, 100, outcome="Failed")
    stub(monkeypatch, [failed], manual=[manual])
    manual["tester_upn"], failed["tester_upn"] = "manual@example.test", "triage@example.test"
    ok, progress, detail = B.gather_progress(
        1, "Manual", 2, 3, auth_automated_ids=[], broker_ui_result=receipt([row(1, 100)]))
    assert ok, detail
    assert not progress["owners"]["manual@example.test"]["tests"][0]["auto_failed"]
    assert progress["owners"]["triage@example.test"]["tests"][0]["auto_failed"]


@pytest.mark.parametrize("damage", ["template", "release", "marker", "parent", "auth",
                                   "missing_plan", "missing_suite", "description", "parent_shape"])
def test_release_destination_identity_is_rechecked_before_writes(monkeypatch, damage):
    binding = {"broker": {"plan_id": 900, "suite_id": 901},
               "auth": {"plan_id": T.AUTH_PLAN, "suite_id": 902}}
    plan = {"id": 900, "name": T.broker_plan_name("2026-09"), "areaPath": T.BROKER_AREA_PATH,
            "iteration": T.BROKER_ITERATION, "rootSuiteId": 899,
            "description": "release-agent:broker-plan:2026-09"}
    suite = {"id": 901, "name": T.BROKER_UI_SUITE_NAME, "suiteType": "staticTestSuite",
             "parentSuite": {"id": 899}}
    if damage == "template":
        binding["broker"]["plan_id"] = T.BROKER_MASTER_PLAN
    elif damage == "release":
        plan["name"] = T.broker_plan_name("2026-10")
    elif damage == "marker":
        plan["description"] = "release-agent:broker-plan:2026-10"
    elif damage == "parent":
        suite["parentSuite"]["id"] = 999
    elif damage == "auth":
        binding["auth"]["plan_id"] = -1
    elif damage == "missing_plan":
        plan = None
    elif damage == "missing_suite":
        suite = None
    elif damage == "description":
        plan["description"] = []
        # Empty/missing descriptions are allowed; a nonempty malformed one is not.
        plan["description"].append("unexpected")
    else:
        suite["parentSuite"] = []
    monkeypatch.setattr(T, "get_plan", lambda *a: (True, plan, ""))
    monkeypatch.setattr(T, "get_suite", lambda *a, **kw: (True, suite, ""))
    monkeypatch.setattr(T, "validate_auth_query_suite", lambda *a, **kw: (True, {}, ""))
    with pytest.raises(ValueError):
        D.validate_release_targets("2026-09", "2026-09-09", binding)


@pytest.mark.parametrize("damage", [None, "other_release", "template"])
def test_explicit_clone_test_names_remain_usable_without_weakening_ownership(monkeypatch, damage):
    names = {"broker": "TEST Broker release rehearsal", "auth": "TEST Auth release rehearsal"}
    binding = {"broker": {"plan_id": 900, "suite_id": 901},
               "auth": {"plan_id": T.AUTH_PLAN, "suite_id": 902}}
    plan = {"id": 900, "name": names["broker"], "areaPath": T.BROKER_AREA_PATH,
            "iteration": T.BROKER_ITERATION, "rootSuiteId": 899,
            "description": "release-agent:broker-plan:2026-09"}
    suite = {"id": 901, "name": T.BROKER_UI_SUITE_NAME, "suiteType": "staticTestSuite",
             "parentSuite": {"id": 899}}
    monkeypatch.setattr(T, "get_plan", lambda *a: (True, plan, ""))
    monkeypatch.setattr(T, "get_suite", lambda *a, **kw: (True, suite, ""))
    seen = []
    monkeypatch.setattr(T, "validate_auth_query_suite",
                        lambda sid, name, *a, **kw: (seen.append(name) is None, {}, ""))
    if damage == "other_release":
        plan["description"] = "release-agent:broker-plan:2026-10"
    elif damage == "template":
        binding["broker"]["plan_id"] = T.BROKER_MASTER_PLAN
    if damage:
        with pytest.raises(ValueError):
            D.validate_release_targets("2026-09", "2026-09-09", binding, expected_names=names)
    else:
        D.validate_release_targets("2026-09", "2026-09-09", binding, expected_names=names)
        assert seen == [names["auth"]]
        with pytest.raises(ValueError, match="this release"):
            D.validate_release_targets("2026-09", "2026-09-09", binding)


def test_distribution_live_triage_selects_exact_failed_points(monkeypatch):
    def group(sid, *points):
        return {"plan_id": 900 if sid == 901 else T.AUTH_PLAN, "suite_id": sid,
                "points": [{"id": pid, "case_id": str(cid), "config_id": str(config),
                            "tester_id": "tester", "tester_upn": "old@example.test"}
                           for pid, cid, config in points]}
    auth = group(902, (1, 100, 84), (2, 100, 85))
    broker = group(901, (4, 200, 84), (5, 200, 85))
    fill = {"auth": {"target": {"plan_id": T.AUTH_PLAN, "suite_id": 902},
                     "applied_points": [{"point_id": 1, "case_id": 100, "outcome": "Failed"}]},
            "broker": {"applied_points": [{"point_id": 4, "case_id": 200,
                                           "config_id": 84, "outcome": "Failed"}],
                       "target": {"suite_id": 901}, "failed_case_ids": [200]}}
    monkeypatch.setattr(S, "completed_result", lambda context: fill)
    data = {"clone_plans_broker": {"plan_id": 900}, "clone_plans_auth": {"suite_id": 902}}
    ctx = SimpleNamespace(
        input=lambda *a: MISSING,
        evidence=SimpleNamespace(step=lambda stage, step: SimpleNamespace(data=data[step])),
        services=SimpleNamespace(testplans=SimpleNamespace(
            find_suite_id_by_name=lambda *a: (True, 899, ""),
            read_point_testers=lambda plan, sid, ids: (
                True, [auth] if sid == 902 else [broker] if sid == 901 else [], ""))))
    result = S._point_sets(ctx, ["A:100", "B:200"], {"broker": {"suite_name": "Manual"}}, set(), {
        "A:100": {"reasons": ["failed_automation"]}, "B:200": {"reasons": ["failed_automation"]}})
    assert [(g["prefix"], [p["id"] for p in g["points"]]) for g in result] == [("A", [1]), ("B", [4])]


def test_reviewed_distribution_preserves_other_configurations_of_shared_case(monkeypatch):
    points = [point(1, 100), point(2, 100, config=85)]
    before = deepcopy(points[1])
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, deepcopy(points), ""))
    def send(url, method, body, timeout):
        assert "/points/1?" in url
        points[0]["assignedTo"] = {"id": body["tester"]["id"]}
        return True, {}, ""
    monkeypatch.setattr(P, "_ado_rest_send", send)
    ok, detail = D.sync_point_testers(
        900, 901, {"100": "owner@example.test"}, expected_testers={1: "id-old@example.test"},
        expected_identities={"100": "owner-id"},
        expected_points={"1": {"case_id": "100", "config_id": "84"}})
    assert ok, detail
    assert points[1] == before and points[0]["outcome"] == "Failed"


@pytest.mark.parametrize("identity", [{"case_id": "999", "config_id": "84"},
                                    {"case_id": "100", "config_id": "85"}])
def test_failure_triage_rejects_changed_acknowledged_identity_before_write(monkeypatch, identity):
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, [point(1, 100)], ""))
    monkeypatch.setattr(P, "_ado_rest_send", lambda *a: pytest.fail("No stale point writes"))
    ok, detail = D.set_point_testers(
        900, 901, [1], "owner@example.test", expected_points={"1": identity})
    assert not ok and "case/configuration changed" in detail


@pytest.mark.parametrize("damage", ["duplicate", "unmapped", "outcome", "config"])
def test_applied_record_rejects_malformed_receipts_before_owner_triage(damage):
    target = {"plan_id": 900, "suite_id": 901}
    points = [{"point_id": 1, "case_id": 100, "config_id": 84, "outcome": "Failed"}]
    if damage == "duplicate":
        points.append(deepcopy(points[0]))
    elif damage == "unmapped":
        points[0]["case_id"] = 999
    elif damage == "outcome":
        points[0]["outcome"] = "Unknown"
    else:
        del points[0]["config_id"]
    with pytest.raises(ValueError):
        R.applied_record({"target": target, "applied_points": points}, [100], "broker", target)


@pytest.mark.parametrize("suites", [
    [], [{"id": 1}, {"id": 1}], [{"id": 1, "parentSuite": ["bad"]}],
    [{"id": 1, "parentSuite": {"id": 2}}, {"id": 2, "parentSuite": {"id": 1}}],
])
def test_missing_duplicate_malformed_or_cyclic_release_suite_blocks(monkeypatch, suites):
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, suites, ""))
    ok, subtree, detail = D._suite_subtree(900, 1)
    assert not ok and subtree is None and detail


def test_ui_failure_reminder_conditions_bind_stable_sources_and_represented_failures(monkeypatch):
    binding = {"release": "2026-09", "rc": 1, "ecs_build": 2, "local_build": 3,
               "auth_apk_build": 4, "auth_test_build": 5,
               "broker": {"plan_id": 900, "suite_id": 901},
               "auth": {"plan_id": T.AUTH_PLAN, "suite_id": 902}}
    result = {"id": "volatile-receipt", "binding": binding,
              "investigations": {"broker": [{"title": "failed"}], "auth": []}}
    record = SimpleNamespace(data={"result": result, "auth": {"failures": []}})
    committed = []
    ctx = SimpleNamespace(
        release=SimpleNamespace(ccd="2026-09-09", owner_email="owner@example.test"),
        evidence=SimpleNamespace(
            pipeline_runs={"rcs": [{"rc": 1, "ecs": {"run_id": "2"}, "local": {"run_id": "3"},
                                   "auth": {"build": {"run_id": "4", "rc": 1},
                                            "test": {"run_id": "5"}}}]},
            step=lambda phase, step: SimpleNamespace(
                data={"plan_id": "900", "ui_suite_id": "901"} if step == "clone_plans_broker"
                else {"suite_id": "902"})),
        recovery=lambda: SimpleNamespace(step=lambda *a: record),
        effect=SimpleNamespace(commit=SimpleNamespace(commit=committed.append)))
    monkeypatch.setattr(R, "current_binding", lambda *a: deepcopy(binding))
    U._replace_failure_reminder(ctx, "failure note", [], 1, [])
    conditions = R.reminder_preconditions(ctx)
    assert list(committed[0].gate_preconditions) == list(conditions)
    assert "volatile-receipt" not in str(conditions)
    values = {tuple(row["path"]): row["value"] for row in conditions}
    assert values[("pipeline_runs", "rcs", -1, "auth", "test", "run_id")] == "5"
    assert values[("steps", "bug_bash.ui_test_status", "data", "result", "status")] == "complete"
    result["id"] = "another-receipt"
    assert R.reminder_preconditions(ctx) == conditions
    result["investigations"]["auth"] = [{"title": "new actual failure"}]
    assert R.reminder_preconditions(ctx) != conditions


def test_retry_receipts_include_acknowledged_partial_points_not_uncertain_or_foreign_targets():
    binding = {"release": "2026-09", "broker": {"plan_id": 900, "suite_id": 901},
               "auth": {"plan_id": T.AUTH_PLAN, "suite_id": 902}}
    first = {"point_id": 1, "case_id": 100, "config_id": 84, "outcome": "Passed"}
    second = {"point_id": 2, "case_id": 200, "config_id": 84, "outcome": "Failed"}
    data = {"result": {"binding": binding,
                       "broker": {"target": binding["broker"], "applied_points": [first]}},
            "summary": {"target": binding["broker"], "applied_points": [second],
                        "uncertain_points": [{"point_id": 3, "case_id": 300}]}}
    assert U._prior_applied_points(data, binding) == {"broker": [first, second], "auth": []}
    changed = deepcopy(binding)
    changed["broker"]["plan_id"] = 999
    assert U._prior_applied_points(data, changed) == {"broker": [], "auth": []}
    changed = {**binding, "release": "another-release"}
    assert U._prior_applied_points(data, changed) == {"broker": [], "auth": []}


@pytest.mark.parametrize("product", ["broker", "auth"])
def test_failed_retry_with_empty_receipt_preserves_prior_acknowledged_writes(monkeypatch, product):
    from tests.test_ui_result_ownership import state as owner_state
    from tests._ui_results import publish, broker_fill
    from tests._context import invoke

    st = owner_state()
    publish(st)
    data = st.get_step("bug_bash", U.ID).data
    binding = data["result"]["binding"]
    before = U._prior_applied_points(data, binding)
    assert before[product]
    monkeypatch.setattr(D, "validate_release_targets", lambda *a, **kw: None)
    monkeypatch.setattr(T, "fill_ui_automation_results",
                        lambda *a, **kw: (False, {"target": binding["broker"],
                                                  "applied_points": []}, "manual result changed"))
    if product == "auth":
        monkeypatch.setattr(T, "fill_ui_automation_results", broker_fill)
        monkeypatch.setattr(T, "fill_auth_ui_results",
                            lambda *a, **kw: (False, {"target": binding["auth"],
                                                      "applied_points": []}, "manual result changed"))
    outcome = invoke(U.build, st)
    assert outcome.kind == "blocked"
    current = st.get_step("bug_bash", U.ID).data
    assert current["result"]["status"] == "incomplete"
    assert U._prior_applied_points(current, binding)[product] == before[product]


def test_ui_rerun_passes_existing_receipts_to_both_writers(monkeypatch):
    from tests.test_ui_result_ownership import state as result_state
    from tests._ui_results import publish, broker_fill, auth_fill
    from tests._context import invoke
    st = result_state()
    publish(st)
    prior = deepcopy(st.get_step("bug_bash", U.ID).data["result"])
    calls = {}
    def broker(plan, verdicts, **kw):
        calls["broker"] = kw["prior_points"]
        return broker_fill(plan, verdicts, **kw)
    def auth(plan, suite, verdicts, **kw):
        calls["auth"] = kw["prior_points"]
        return auth_fill(plan, suite, verdicts, **kw)
    monkeypatch.setattr(T, "fill_ui_automation_results", broker)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth)
    monkeypatch.setattr(D, "validate_release_targets", lambda *a, **kw: None)
    monkeypatch.setattr(D, "set_point_testers", lambda *a, **kw: (True, ""))
    assert invoke(U.build, st).kind == "done"
    assert calls == {product: prior[product]["applied_points"] for product in ("broker", "auth")}


def test_interrupted_retry_keeps_receipts_without_changing_frozen_effect_input(monkeypatch):
    from orchestrator.step_context import thaw
    binding = {"release": "2026-09", "broker": {"plan_id": 900, "suite_id": 901},
               "auth": {"plan_id": T.AUTH_PLAN, "suite_id": 902}}
    point = {"point_id": 1, "case_id": 100, "config_id": 84, "outcome": "Failed"}
    record = SimpleNamespace(data={"result": {"binding": binding, "broker": {
        "target": binding["broker"], "applied_points": [point]}}})
    def commit(update):
        record.data = thaw(update.values)
    ctx = SimpleNamespace(new_id=lambda: "retry", recovery=lambda: SimpleNamespace(step=lambda *a: record),
                          effect=SimpleNamespace(commit=SimpleNamespace(commit=commit)))
    calls = []
    def interrupted(context, prepared):
        calls.append(prepared["prior_points"])
        raise OSError("interrupted before provider acknowledgement")
    monkeypatch.setattr(U, "_apply_prepared", interrupted)
    frozen = {"binding": deepcopy(binding)}
    assert U._run_effect(ctx, frozen).kind == "blocked"
    assert U._run_effect(ctx, frozen).kind == "blocked"
    assert calls == [{"broker": [point], "auth": []}] * 2
    assert frozen == {"binding": binding}
