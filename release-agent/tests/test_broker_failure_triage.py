"""Failure-only v4 contracts exercised through the existing in-memory provider."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import broker_failure_triage as F, broker_scenarios as S, broker_plans as B, testplans as T
from tests._mrwp_evidence import current_rc, snapshot, PROD
from tests.test_broker_scenarios import ADO, create, evidence, scenario_state, distribution_points


def source(rc, catalog=(100, 200), *, policy=None):
    ok, projection, detail = F.project(rc, **({"classification_policy": policy} if policy is not None else {}))
    assert ok, detail
    frozen = {"schema_version": F.VERSION, "layout": F.LAYOUT, "scenario_projection": projection,
              "root_configs": [293], "broker_cases": [701, 702], "broker_configs": list(T.BROKER_CONFIGS),
              "ui_cases": list(catalog), "ui_configs": list(T.BROKER_UI_CONFIGS),
              "native_query": "SELECT [System.Id] FROM WorkItems WHERE [System.Tags] CONTAINS 'Native'"}
    B._validate_source(frozen)
    return frozen


def production(monkeypatch, rc, frozen=None):
    from tests._context import invoke, context
    from tests._ui_results import auth_fill
    from tools import distribution as D
    from steps.bug_bash import ui_results, ui_test_status as U
    ado = ADO(monkeypatch)
    st, checkpoints = scenario_state(ado, frozen or source(rc), rc=rc)
    st.ccd = "2000-01-01"
    monkeypatch.setattr(T, "validate_auth_query_suite", lambda *a, **kw: (True, {}, ""))
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    assignments = []
    monkeypatch.setattr(D, "set_point_testers", lambda pid, sid, points, upn, **kw:
                        (assignments.append((sid, points, kw)) is None, ""))
    outcome = invoke(U.build, st)
    assert outcome.kind == "done", outcome
    result = ui_results.completed_result(context(st))
    return ado, st, result, assignments, checkpoints


@pytest.mark.parametrize("legacy_evidence", [False, True])
def test_fresh_default_source_and_clone_keep_stress_separate_through_fill_and_report(
        monkeypatch, legacy_evidence):
    from orchestrator.state import ReleaseState, StepState
    from steps.bug_bash import clone_plans_broker as clone, ui_results, ui_test_status
    from steps.build_verify import rc_report as report, _rc_report_rendering as rendering
    from tests._context import context, invoke
    from tests._ui_results import auth_fill, checkpoint_memory
    from tests.test_stress_reporting import legacy_snapshot
    from tools import distribution as D

    stress = "Stress Tests - RC MSAL with RC Broker (API 32)"
    rc = current_rc(
        {PROD: [("test_100_UI", "Failed")], stress: [("stress_pass", "Passed")]},
        {PROD: [("test_200_UI", "Passed")],
         stress: [("stress_failure", "Failed"), ("stress_failure", "Failed")]})
    if legacy_evidence:
        for flight in ("ecs", "local"):
            rc[flight] = legacy_snapshot(rc[flight])
    original = deepcopy(rc)
    ado = ADO(monkeypatch)
    monkeypatch.setattr(T, "_suite_full", lambda *a: (
        True, {"inheritDefaultConfigurations": False, "defaultConfigurations": [{"id": 293}]}, ""))
    monkeypatch.setattr(T, "_native_auth_query", lambda *a: (
        True, "SELECT [System.Id] FROM WorkItems WHERE [System.Tags] CONTAINS 'Native'", ""))
    master_reads = []

    def master_cases(plan, root, timeout):
        master_reads.append((plan, root))
        return True, [{"id": cid} for cid in (
            (701, 702) if root == T.BROKER_MANUAL_ROOT_SUITE else (100, 200))], ""

    monkeypatch.setattr(D, "broker_manual_cases", master_cases)
    monkeypatch.setattr(B, "verify_ui_configurations", lambda *a: (True, {}, ""))
    state = ReleaseState(release_id="2000-01", owner_email="owner@example.test", ccd="2000-01-01")
    state.pipeline_runs = {"rcs": [rc]}
    checkpoint_memory(state)
    cloned = invoke(clone.build, state)
    assert cloned.kind == "done", cloned
    record = state.resources[B.RESOURCE]
    assert record["source"]["scenario_projection"]["classification_policy"] == 2
    assert len(master_reads) == 2
    assert "failures only" in cloned.note and "Stress Tests" in cloned.note
    assert "UI Automation (Android Broker)" not in cloned.note
    assert record["automation"]["flights"]["Local"]["categories"] == {
        "Stress Tests": record["suites"]["category:Local:Stress Tests"]}
    assert set(record["automation"]["flights"]["ECS"]["categories"]) == {"UI Automation"}
    assert "classification_amendment" not in record
    stress_suite = record["suites"]["category:Local:Stress Tests"]
    assert ado.suites[record["plan_id"], stress_suite]["name"] == "Stress Tests"
    assert len(ado.points[record["plan_id"], record["suites"]["broker"]]) == 4

    state.set_step("bug_bash", "clone_plans_auth", StepState(data={"suite_id": 8000}))
    monkeypatch.setattr(T, "validate_auth_query_suite", lambda *a, **kw: (True, {}, ""))
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    monkeypatch.setattr(D, "set_point_testers", lambda *a, **kw: (True, ""))
    filled = invoke(ui_test_status.build, state)
    assert filled.kind == "done", filled
    result = ui_results.completed_result(context(state))
    points = result["broker"]["applied_points"]
    assert len(points) == 2 and all(point["outcome"] == "Failed" for point in points)
    assert any(point["suite_id"] == stress_suite for point in points)
    assert len(distribution_points(state, ado)) == 3
    model = report.rc_report_model(context(state))
    gate, auth = report.rc_ui_gate(model), report.auth_report_gate(model)
    assert (gate["ui_passed"], gate["ui_failed"], gate["ui_total"]) == (2, 2, 4)
    for text in (rendering.rc_email_plain(model, {}, gate, auth, report.rc_next_action(model)),
                 rendering.rc_email_html(model, {}, gate, auth, report.rc_next_action(model))):
        assert "Stress Tests" in text and "UI+Stress" in text
    before = deepcopy(ado.writes)
    assert invoke(clone.build, state).kind == "done"
    assert invoke(ui_test_status.build, state).kind == "done"
    assert ado.writes == before and len(master_reads) == 2
    assert state.pipeline_runs["rcs"][-1] == original


def test_fresh_plan_help_describes_failure_only_stress_hierarchy():
    from orchestrator.knowledge import get_knowledge

    clone = get_knowledge("bug_bash", "clone_plans_broker")
    fill = get_knowledge("bug_bash", "ui_test_status")
    assert "Stress Tests" in clone["what"] and "UI Automation" in clone["what"]
    assert "classification_policy 2" in clone["how"]
    assert "THREE FLAT" not in clone["what"]
    assert "no-ID" in clone["what"] and "release-owned" in clone["what"]
    assert "Stress Tests separate from UI Automation" in fill["what"]
    assert "not a fake UI-suite ID" in fill["how"]


@pytest.mark.parametrize("all_categories", [False, True])
def test_production_failure_only_hierarchy_publication_distribution_progress_and_replay(monkeypatch, all_categories):
    from tests._context import invoke
    from tools import bugbash
    from steps.bug_bash import ui_test_status as U
    rc = evidence()
    if all_categories:
        suites = {PROD: [("test_100_Failed[a]", "Failed"), ("test_100_Failed[b]", "Failed")],
                  "AADAuthenticator_InstrumentedTests": [("instrumented", "Failed")],
                  "common_UnitTests": [("unit", "Failed")],
                  "Lab Api Tests": [("lab", "Failed")],
                  "Native Auth E2E Tests": [("native", "Failed")],
                  "Stress Tests - RC MSAL with RC Broker (API 32)": [("stress", "Failed")],
                  "Unrecognized UI Automation (API 35)": [("other", "Failed")]}
        rc = current_rc(suites, suites)
    ado, st, published, assignments, _ = production(monkeypatch, rc)
    record = st.resources["broker_test_plan"]
    rows = record["scenario_bindings"]
    assert "ui_suite_id" not in record
    assert published["broker"]["target"] == F.target(record)
    assert "ui" not in record["suites"]
    assert all(r["verdict"] == "Failed" and r["kind"] == "failure" for r in rows)
    assert len(published["broker"]["applied_points"]) == len(rows)
    assert all(p["outcome"] == "Failed" for p in published["broker"]["applied_points"])
    assert {s["name"] for s in ado.suites.values() if s.get("parentSuite", {}).get("id") ==
            record["plan_root_id"]} == {T.BROKER_MANUAL_SUITE_NAME, T.BROKER_NATIVE_AUTH_SUITE_NAME, S.TRIAGE}
    assert {sid for sid, *_ in assignments} == set(published["broker"]["suite_targets"])
    assert len(distribution_points(st, ado)) == 1 + len(published["broker"]["suite_targets"])
    for flight, flight_info in record["automation"]["flights"].items():
        assert ado.suites[record["plan_id"], flight_info["suite_id"]]["parentSuite"]["id"] == record["automation"]["root_suite_id"]
        for category, sid in flight_info["categories"].items():
            assert ado.suites[record["plan_id"], sid]["parentSuite"]["id"] == flight_info["suite_id"]
            assert ado.points[record["plan_id"], sid]
            assert all(r["category"] == category and r["flight"] == flight for r in rows if r["suite_id"] == sid)
    assert all("/wit/workitems/" not in url or method == "POST" for url, method, _ in ado.writes)
    assert ado.suites[record["plan_id"], record["suites"]["native"]]["queryString"] == record["source"]["native_query"]
    assert len(ado.points[record["plan_id"], record["suites"]["broker"]]) == 4

    def progress(pid, sid, timeout):
        return True, [{"point_id": str(p["id"]), "case_id": str(p["testCase"]["id"]),
                       "config_id": str(p["configuration"]["id"]), "outcome": p["outcome"],
                       "tester_upn": st.owner_email, "name": "failure"} for p in ado.points[pid, sid]], ""
    monkeypatch.setattr(bugbash, "_points_of_suite", progress)
    ok, failures, detail = bugbash._broker_ui_triage_points(record["plan_id"], published["broker"], 30)
    assert ok, detail
    assert len(failures) == len(rows)
    before = len(ado.writes)
    assert invoke(U.build, st).kind == "done"
    assert len(ado.writes) == before
    assert create(ado, record["source"], record)[0]
    assert len(ado.writes) == before
    if all_categories:
        assert all(set(f["categories"]) == set(F.CATEGORIES) for f in record["automation"]["flights"].values())
        assert len({(r["suite_id"], r["case_id"], r["config_id"]) for r in rows}) == len(rows)
        assert all(not r["reuse_case_id"] for r in rows if r["category"] == "UI Automation")


@pytest.mark.parametrize("unit_only", [False, True])
def test_zero_failure_manual_only_valid_for_fill_publication_distribution_and_progress(monkeypatch, unit_only):
    from tools import bugbash
    suite = "common_UnitTests" if unit_only else PROD
    rc = current_rc({suite: [("test_pass", "Passed"), ("test_na", "NotApplicable"),
                              ("test_recovered", "Failed"), ("test_recovered", "Passed")]})
    ado, st, result, assignments, _ = production(monkeypatch, rc)
    record = st.resources["broker_test_plan"]
    assert set(record["suites"]) == {"broker", "native"}
    assert record["automation"]["root_suite_id"] is None and record["automation"]["flights"] == {}
    assert record["scenario_bindings"] == result["broker"]["applied_points"] == []
    assert not assignments and not ado.cases
    assert len(distribution_points(st, ado)) == 1
    assert bugbash._broker_ui_triage_points(record["plan_id"], result["broker"], 30) == (True, [], "")


def test_resolved_failures_retain_hierarchy_case_and_creation_history(monkeypatch):
    from tests._context import invoke, context
    from steps.bug_bash import ui_results, ui_test_status as U
    rc = evidence()
    ado, st, previous, _, _ = production(monkeypatch, rc)
    resource = deepcopy(st.resources["broker_test_plan"])
    original_writes = len(ado.writes)
    for slot in ("ecs", "local"):
        data = {s["name"]: [(t["title"], "Passed") for t in s["test_results"]]
                for s in rc[slot]["tests"]["suites"]}
        st.pipeline_runs["rcs"][-1][slot] = snapshot(int(rc[slot]["run_id"]), data)
    outcome = invoke(U.build, st)
    assert outcome.kind == "done", outcome
    result = ui_results.completed_result(context(st))
    assert result["broker"]["failed_case_ids"] == []
    assert all(p["outcome"] == "Passed" for p in result["broker"]["applied_points"])
    assert all(r["verdict"] == "Passed" and r["links"] for r in result["broker"]["scenario_bindings"])
    assert st.resources["broker_test_plan"] == resource
    assert all(method == "PATCH" and "/points/" in url for url, method, _ in ado.writes[original_writes:])
    assert len(result["broker"]["applied_points"]) == len(previous["broker"]["applied_points"])


@pytest.mark.parametrize("damage", ["ecs_build", "local_build", "rc", "new_failure", "missing_history"])
def test_changed_source_blocks_before_either_product_outcome_writes(monkeypatch, damage):
    from tests._context import invoke
    from steps.bug_bash import ui_test_status as U
    ado, st, _, _, _ = production(monkeypatch, evidence())
    rc = st.pipeline_runs["rcs"][-1]
    if damage == "rc":
        rc["rc"] += 1
    else:
        slot = "local" if damage == "local_build" else "ecs"
        data = {s["name"]: [(t["title"], t["verdict"]) for t in s["test_results"]]
                for s in rc[slot]["tests"]["suites"]}
        if damage == "new_failure":
            data[PROD].append(("test_new_failure", "Failed"))
        if damage == "missing_history":
            data.pop("AADAuthenticator_InstrumentedTests")
        rc[slot] = snapshot(303 if damage.endswith("_build") else int(rc[slot]["run_id"]), data)
    before = len(ado.writes)
    assert invoke(U.build, st).kind == "blocked"
    assert len(ado.writes) == before


@pytest.mark.parametrize("damage", ["receipt_suite", "scenario_key", "duplicate_point", "live_suite", "live_missing",
                                   "manifest_build", "typed_root", "outcome"])
def test_failure_receipts_and_typed_destination_fail_closed(monkeypatch, damage):
    from steps.bug_bash import ui_test_status as U
    ado, st, _, _, _ = production(monkeypatch, evidence())
    record = st.resources["broker_test_plan"]
    step = st.get_step("bug_bash", U.ID)
    result = step.data["result"]["broker"]
    receipt = result["applied_points"][0]
    if damage == "receipt_suite":
        receipt["suite_id"] = record["suites"]["broker"]
    elif damage == "scenario_key":
        receipt["scenario_key"] = "other"
    elif damage == "duplicate_point":
        result["applied_points"].append(deepcopy(receipt))
    elif damage == "manifest_build":
        result["scenario_bindings"][0]["build_id"] += 1
    elif damage == "typed_root":
        record["automation"]["anchor"]["suite_id"] += 1
    elif damage == "outcome":
        receipt["outcome"] = "Passed"
    elif damage == "live_suite":
        ado.points[record["plan_id"], receipt["suite_id"]][0]["configuration"]["id"] += 100
    else:
        ado.points[record["plan_id"], receipt["suite_id"]].clear()
    st.set_step("bug_bash", U.ID, step)
    before = len(ado.writes)
    with pytest.raises(ValueError):
        distribution_points(st, ado)
    assert len(ado.writes) == before


@pytest.mark.parametrize("failure", ["case", "membership", "pin_lost_ack", "crash_after_case"])
def test_v4_retains_uncertain_intents_and_exact_receipt_recovery(monkeypatch, failure):
    ado = ADO(monkeypatch)
    frozen, record = source(evidence()), {}
    ado.failure = failure
    if failure == "crash_after_case":
        with pytest.raises(SystemExit):
            create(ado, frozen, record)
    else:
        assert not create(ado, frozen, record)[0]
    before = len(ado.writes)
    posted_memberships = {url for url, method, _ in ado.writes if method == "POST" and "TestCase?" in url}
    ok, _, detail, _, _ = create(ado, frozen, record)
    if failure in ("case", "crash_after_case"):
        assert not ok and "Uncertain create" in detail and len(ado.writes) == before
        if failure == "crash_after_case":
            key = next(k for k, op in record["operations"].items() if op["status"] == "creating")
            S.recover_create(record, key, max(ado.cases), "Exact in-memory receipt", lambda: None)
            assert create(ado, frozen, record)[0]
    else:
        assert ok, detail
        assert not any(method == "POST" and url in posted_memberships for url, method, _ in ado.writes[before:])


def test_v4_export_is_exact_and_does_not_write_providers():
    frozen = source(evidence())
    manifest = F.approval_manifest(frozen, "2000-01-isolated", "TEST ONLY - failures")
    assert manifest["provider_writes_executed"] is False
    assert len(manifest["case_payloads"]) == manifest["preview"]["new_cases"]
    assert len(manifest["outcome_writes"]) == manifest["preview"]["failure_points"]
    assert all(p["outcome"] == "Failed" for p in manifest["outcome_writes"])
    assert all("System.AssignedTo" not in p["path"] for c in manifest["case_payloads"] for p in c["payload"])
    assert manifest["frozen_source"] == frozen
    assert all(p["source_build_url"].endswith(str(p["source_build_id"])) for p in manifest["outcome_writes"])
    assert len(manifest["suite_payloads"]) == len(manifest["preview"]["suites"])


@pytest.mark.parametrize("rc", [None, {}, {"rc": 2}, {"rc": True}])
def test_missing_source_is_a_prewrite_hold(rc):
    assert F.project(rc)[0] is False


@pytest.mark.parametrize("applied", [False, True])
def test_uncertain_outcomes_never_borrow_unacknowledged_ownership(monkeypatch, applied):
    from tools import pipelines as P
    ado = ADO(monkeypatch)
    frozen, resource = source(evidence()), {}
    assert create(ado, frozen, resource)[0]
    ok, projection, detail = F.project(evidence(), tracked=resource["scenario_bindings"])
    assert ok, detail
    bound = F.bind_projection(resource, projection)
    original = ado.send
    uncertain = []
    def send(url, method, body, timeout):
        if not uncertain and method == "PATCH" and "/points/" in url:
            uncertain.append(url)
            if applied:
                original(url, method, body, timeout)
            else:
                ado.writes.append((url, method, deepcopy(body)))
            return False, None, "Lost outcome acknowledgement"
        return original(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", send)
    checkpoints = []
    def fill(prior=()):
        return T.fill_ui_automation_results(resource["plan_id"], bound["verdicts"],
            automation_target=F.target(resource), scenario_resource=resource,
            scenario_bindings=bound["scenario_bindings"], prior_points=prior,
            checkpoint=lambda summary: checkpoints.append(deepcopy(summary)))
    ok, partial, detail = fill()
    assert not ok and partial["uncertain_points"] and checkpoints[-1]["uncertain_points"]
    before = len(ado.writes)
    resumed, summary, detail = fill(partial["applied_points"])
    if applied:
        assert not resumed and len(ado.writes) == before
    else:
        assert resumed, detail
        assert len(summary["applied_points"]) == len(resource["scenario_bindings"])


def test_isolated_v4_cli_export_creation_and_replay_are_separate_from_source(monkeypatch, tmp_path):
    from argparse import Namespace
    from orchestrator.commands import broker_scenario_plan as command
    from orchestrator.state import ReleaseState
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    st = ReleaseState(release_id="2000-01", owner_email="owner@example.test")
    st.pipeline_runs = {"rcs": [evidence()]}
    snapshot_path = tmp_path / "protected" / "release-state.json"
    st.save(str(snapshot_path))
    original = snapshot_path.read_bytes()
    monkeypatch.setattr(B, "prepare_source", lambda **kw: (True, frozen, ""))
    export = tmp_path / "approval.json"
    args = Namespace(snapshot=str(snapshot_path), isolated_root=str(tmp_path / "isolated"),
                     name="TEST ONLY - failure triage", execute=False, approval_manifest=str(export),
                     confirm_source_sha256=None, recover_create=None, resource_id=None, reason=None)
    result = command.run(args)
    approved = json.loads(export.read_text(encoding="utf-8"))
    assert approved == F.approval_manifest(frozen, result["preview"]["instance"], args.name)
    assert not ado.writes and not (tmp_path / "isolated").exists()
    args.execute = True
    with pytest.raises(ValueError, match="read-only"):
        command.run(args)
    assert not ado.writes
    args.approval_manifest = None
    args.confirm_source_sha256 = result["preview"]["source_sha256"]
    applied = command.run(args)
    assert applied["fill_status"] == "complete"
    assert applied["applied_points"] == approved["preview"]["failure_points"]
    writes = len(ado.writes)
    assert command.run(args)["plan_id"] == applied["plan_id"]
    assert len(ado.writes) == writes and snapshot_path.read_bytes() == original


def test_collected_rc2_failure_only_production_path(monkeypatch, capsys):
    path = os.environ.get("RELEASE_AGENT_MRWP_STATE")
    if not path:
        pytest.skip("Set RELEASE_AGENT_MRWP_STATE for the read-only actual RC2 proof")
    raw = Path(path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "52efad6cce6fc118c084cff2e7c8ce587ee29f99d5b6bff8b595e4ae19f6fe96"
    collected = json.loads(raw)
    rc = collected["pipeline_runs"]["rcs"][-1]
    historical = collected["resources"]["broker_test_plan"]["source"]
    frozen = source(rc, historical["ui_cases"])
    for key in ("root_configs", "broker_cases", "broker_configs", "native_query"):
        frozen[key] = deepcopy(historical[key])
    ado, st, result, _, _ = production(monkeypatch, rc, frozen)
    record = st.resources["broker_test_plan"]
    rows = record["scenario_bindings"]
    counts = Counter((r["flight"], r["category"]) for r in rows)
    assert counts == {("ECS", "UI Automation"): 6, ("ECS", "Instrumented Tests"): 1,
                      ("Local", "Stress Tests"): 1, ("Local", "Instrumented Tests"): 1}
    assert len(rows) == len(result["broker"]["applied_points"]) == 9
    assert all(p["outcome"] == "Failed" for p in result["broker"]["applied_points"])
    assert len(frozen["scenario_projection"]["warnings"]) == 24
    concurrent = next(r for r in rows if r["title"] == "test_concurrentAcquireTokenSilent_withBroker")
    assert concurrent["flight"] == "Local" and concurrent["original_case_id"] is None
    assert len(concurrent["links"]) == len(concurrent["attempts"]) == 2
    assert len(ado.points[record["plan_id"], record["suites"]["broker"]]) == 86
    assert len(distribution_points(st, ado)) == 5
    from tools import bugbash
    def progress(pid, sid, timeout):
        return True, [{"point_id": str(p["id"]), "case_id": str(p["testCase"]["id"]),
                       "config_id": str(p["configuration"]["id"]), "outcome": p["outcome"],
                       "tester_upn": st.owner_email, "name": "failure"} for p in ado.points[pid, sid]], ""
    monkeypatch.setattr(bugbash, "_points_of_suite", progress)
    ok, progress_rows, detail = bugbash._broker_ui_triage_points(record["plan_id"], result["broker"], 30)
    assert ok and len(progress_rows) == 9, detail
    assert all(len(f["categories"]) == 2 for f in record["automation"]["flights"].values())
    assert Path(path).read_bytes() == raw
    name = "TEST ONLY - MRWP failure triage by flight RC2"
    instance = collected["release_id"] + "-scenario-" + S.digest(name)[:16]
    manifest = F.approval_manifest(frozen, instance, name)
    legacy = os.environ.get("RELEASE_AGENT_V3_RESOURCE")
    if legacy:
        from steps.bug_bash.ui_results import applied_record
        previous = Path(legacy).read_bytes()
        assert hashlib.sha256(previous).hexdigest() == "7ef133d93e194b67c9e9204b8d8bd61bc0901980df719f1e08678fb6ad74dc5f"
        old = json.loads(previous)
        B._validate_source(old["source"])
        assert S.digest(old["source"]) == "bd5c3a7a80e301ab8cfa72075622bc3e605496d15aed2f90a1a4b21654cee116"
        old_result = applied_record(old["fill"], [r["case_id"] for r in old["scenario_bindings"]],
                                    "broker", {"plan_id": old["plan_id"], "suite_id": old["ui_suite_id"]})
        assert old_result["mapping_version"] == 3 and len(old_result["applied_points"]) == 224
        assert Path(legacy).read_bytes() == previous
    output = os.environ.get("RELEASE_AGENT_APPROVAL_MANIFEST")
    if output:
        with Path(output).open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
    with capsys.disabled():
        print("\nRC2 FAILURE-ONLY OFFLINE PROOF " + json.dumps({
            **{k: v for k, v in manifest["preview"].items() if k != "warnings"},
            "warnings": len(manifest["preview"]["warnings"]),
            "instance": instance, "applied_points": 9, "protected_state_unchanged": True,
            "modeled_provider_writes": len(ado.writes),
            "owned_case_ids_are_fake": True}, sort_keys=True))
