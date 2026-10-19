"""Source-policy compatibility and the one-leaf, result-preserving TEST ONLY correction."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import broker_failure_triage as F, broker_scenarios as S, broker_plans as B
from tools import testplans as T, pipelines as P
from tests._mrwp_evidence import current_rc, snapshot, PROD
from tests.test_broker_failure_triage import source
from tests.test_broker_scenarios import ADO

STRESS = "Stress Tests - RC MSAL with RC Broker (API 32)"
TITLE = "test_concurrentAcquireTokenSilent_withBroker"


def evidence(*, mixed=False):
    ecs = {PROD: [(f"test_{cid}_Device", "Failed") for cid in range(100, 106)],
           "AADAuthenticator_InstrumentedTests": [("instrumented_ecs", "Failed")],
           STRESS: [(TITLE, "Passed")]}
    local = {STRESS: [(TITLE, "Failed")],
             "AADAuthenticator_InstrumentedTests": [("instrumented_local", "Failed")]}
    if mixed:
        local[PROD] = [("test_100_Local", "Failed")]
    return current_rc(ecs, local)


def _published_point_history(ado, record):
    rows = {r["key"]: r for r in record["scenario_bindings"]}
    results = Counter()
    for receipt in record["fill"]["applied_points"]:
        row = rows[receipt["scenario_key"]]
        run = (6596269 if row["suite"] == STRESS else 6596267 if row["category"] == "UI Automation"
               else 6596262 if row["flight"] == "ECS" else 6596268)
        result_id = 100000 + results[run]
        results[run] += 1
        point = next(p for p in ado.points[record["plan_id"], receipt["suite_id"]]
                     if p["id"] == receipt["point_id"])
        point.update(
            state="NotReady", comment=None, lastResult={"id": str(result_id)},
            lastTestRun={"id": str(run)}, lastResultState="Completed",
            lastResultDetails={"duration": 0,
                               "dateCompleted": "2026-10-18T05:52:57.927Z" if row["suite"] == STRESS
                               else "2026-10-18T05:52:28.587Z",
                               "runBy": {"id": "11111111-1111-1111-1111-111111111111",
                                         "displayName": "Fixture Caller"}},
            assignedTo={"id": "11111111-1111-1111-1111-111111111111"},
            opaqueProviderHistory={"retain": ["every", "field"]})


def completed(monkeypatch, *, mixed=False, published=True):
    ado, record, rc = ADO(monkeypatch), {}, evidence(mixed=mixed)
    frozen = source(rc, range(100, 106), policy=1)
    name = "TEST ONLY - Stress classification correction"
    instance = "2000-01-scenario-" + S.digest(name)[:16]
    ok, pid, detail = B.ensure_plan(instance, name, record, lambda: None, prepared_source=frozen)
    assert ok, detail
    ok, projection, detail = F.project(rc, tracked=record["scenario_bindings"], classification_policy=1)
    assert ok, detail
    bound = S.bind_projection(record, projection)
    ok, fill, detail = T.fill_ui_automation_results(
        pid, bound["verdicts"], automation_target=F.target(record), scenario_resource=record,
        scenario_bindings=bound["scenario_bindings"], checkpoint=lambda partial: None)
    assert ok, detail
    record.update(fill=fill, fill_status="complete")
    if published:
        _published_point_history(ado, record)
    key = next(r["key"] for r in record["scenario_bindings"] if r["suite"] == STRESS)
    return ado, record, rc, key


def test_new_taxonomy_is_distinct_and_legacy_v4_hashes_remain_unchanged():
    rc = evidence()
    legacy = source(rc, range(100, 106), policy=1)
    raw = S.digest(legacy)
    assert "classification_policy" not in legacy["scenario_projection"]
    assert next(r for r in legacy["scenario_projection"]["triage"] if r["suite"] == STRESS)["category"] == "UI Automation"
    future = source(rc, range(100, 106))
    assert future["scenario_projection"]["classification_policy"] == 2
    counts = Counter((r["flight"], r["category"]) for r in F.representations(future))
    assert counts == {("ECS", "UI Automation"): 6, ("ECS", "Instrumented Tests"): 1,
                      ("Local", "Stress Tests"): 1, ("Local", "Instrumented Tests"): 1}
    assert sum(counts.values()) == 9 and ("ECS", "Stress Tests") not in counts
    assert ("Local", "UI Automation") not in counts
    B._validate_source(legacy)
    assert S.digest(legacy) == raw
    for projection, wrong in ((legacy["scenario_projection"], "Stress Tests"),
                              (future["scenario_projection"], "UI Automation")):
        damaged = deepcopy(projection)
        next(r for r in damaged["triage"] if r["suite"] == STRESS)["category"] = wrong
        with pytest.raises(ValueError):
            F.validate_projection(damaged)
    numeric = current_rc(local={STRESS: [("test_100_Stress", "Failed")]})
    mapped = F.representations(source(numeric, (100,)))[0]
    assert mapped["original_case_id"] == 100 and mapped["reuse_case_id"] is None


@pytest.mark.parametrize("suffix, category", [
    ("common_UnitTests", "Unit Tests"), ("AADAuthenticator_InstrumentedTests", "Instrumented Tests")])
def test_frozen_policies_preserve_legacy_unit_precedence_under_stress_prefix(suffix, category):
    name = "Stress Tests - " + suffix
    rc = current_rc({PROD: [("test_100_Device", "Passed")], name: [("failure", "Failed")]})
    legacy = source(rc, policy=1)
    assert legacy["scenario_projection"]["triage"][0]["category"] == category
    assert F.representations(source(rc))[0]["category"] == "Stress Tests"
    ok, v3, detail = S.project(rc, version=3)
    assert ok, detail
    assert v3["triage"][0]["category"] == category


def test_checked_completed_correction_preserves_creates_source_points_and_consumers(monkeypatch):
    from steps.bug_bash.ui_results import applied_record, _scenario_points
    ado, record, rc, key = completed(monkeypatch)
    original, writes, points, cases = deepcopy(record), deepcopy(ado.writes), deepcopy(ado.points), deepcopy(ado.cases)
    preview = F.preview_stress_correction(record, key)
    assert record == original and ado.writes == writes
    request = preview["intent"]["external_request"]
    sid = preview["intent"]["suite_id"]
    assert request["method"] == "PATCH" and request["payload"] == {
        **original["operations"]["suite:category:Local:UI Automation"]["payload"], "name": "Stress Tests"}
    assert preview["intent"]["case_provenance_writes"] == []
    receipts = []
    result = F.correct_stress_category(record, key, preview["correction_sha256"],
                                       lambda: receipts.append(deepcopy(record)))
    assert not result["creates"] and not result["outcome_writes"]
    assert len(ado.writes) == len(writes) + 1 and ado.writes[-1] == (
        request["url"], request["method"], request["payload"])
    assert ado.suites[record["plan_id"], sid]["name"] == "Stress Tests"
    assert receipts[0]["classification_amendment"]["status"] == "applying"
    assert receipts[0]["scenario_bindings"] == original["scenario_bindings"]
    assert receipts[-1]["classification_amendment"]["status"] == "applied"
    assert record["source"] == original["source"] and record["operations"] == original["operations"]
    assert record["classification_amendment"]["history"]["fill"] == original["fill"]
    assert ado.points == points and ado.cases == cases
    assert len(original["fill"]["applied_points"]) == 9
    assert all(p["lastResultState"] == "Completed" and p["lastResultDetails"]["duration"] == 0
               for rows in points.values() for p in rows if p["outcome"] == "Failed")
    assert record["automation"]["flights"]["Local"]["categories"] == {
        "Stress Tests": sid, "Instrumented Tests": original["suites"]["category:Local:Instrumented Tests"]}
    assert B.validate_plan(record["plan_id"], record["identity"], record["source"], resource=record)[0]
    assert F.target(record) == original["fill"]["target"]
    _scenario_points(record["fill"], complete=True, expected_rows=record["scenario_bindings"])
    result = applied_record(record["fill"], [r["case_id"] for r in record["scenario_bindings"]],
                            "broker", F.target(record))
    assert len(result["applied_points"]) == 9
    ok, current, detail = F.project(rc, tracked=record["scenario_bindings"],
                                    classification_policy=F.policy_for_record(record))
    assert ok, detail
    assert S.bind_projection(record, current)["scenario_bindings"] == record["scenario_bindings"]
    before = len(ado.writes)
    assert B.ensure_plan(record["identity"]["release_id"], record["identity"]["name"],
                         record, lambda: None, prepared_source=record["source"])[0]
    assert len(ado.writes) == before
    with pytest.raises(ValueError):
        F.preview_stress_correction(record, key)
    replay = F.preview_stress_correction(record, key, recovery=True)
    F.correct_stress_category(record, key, replay["correction_sha256"], lambda: None, recovery=True)
    assert len(ado.writes) == before


@pytest.mark.parametrize("damage", [
    "mixed", "zero", "source", "key", "name", "marker", "status", "fill", "outcome",
    "point_id", "case_id", "config", "parent", "type", "defaults", "inherit", "external_rename",
    "comment", "result", "run", "state", "manual", "receipt", "stale"])
def test_correction_holds_before_write_on_ambiguous_source_or_provider_drift(monkeypatch, damage):
    ado, record, rc, key = completed(monkeypatch, mixed=damage == "mixed")
    if damage == "mixed":
        with pytest.raises(ValueError, match="mixed"):
            F.preview_stress_correction(record, key)
        return
    approved = F.preview_stress_correction(record, key)["correction_sha256"]
    sid, pid = record["suites"]["category:Local:UI Automation"], record["plan_id"]
    point, suite = ado.points[pid, sid][0], ado.suites[pid, sid]
    if damage == "zero":
        ado.points[pid, sid].clear()
    elif damage == "source":
        record["source"]["broker_cases"].append(999)
    elif damage == "key":
        key = "0" * 64
    elif damage == "name":
        record["identity"]["name"] = "Production plan"
    elif damage == "marker":
        ado.plans[pid]["description"] = F.MARKER
    elif damage == "status":
        record["status"] = "created"
    elif damage == "fill":
        record["fill_status"] = "partial"
    elif damage == "outcome":
        point["outcome"] = "Passed"
    elif damage == "point_id":
        point["id"] += 999
    elif damage == "case_id":
        point["testCase"]["id"] += 999
    elif damage == "config":
        point["configuration"]["id"] = 293
    elif damage == "parent":
        suite["parentSuite"]["id"] = record["plan_root_id"]
    elif damage == "type":
        suite["suiteType"] = "dynamicTestSuite"
    elif damage == "defaults":
        suite["defaultConfigurations"] = [{"id": 293}]
    elif damage == "inherit":
        suite["inheritDefaultConfigurations"] = True
    elif damage == "external_rename":
        suite["name"] = "Stress Tests"
    elif damage == "comment":
        point["comment"] = "Human investigation"
    elif damage in ("result", "run"):
        point["lastResult" if damage == "result" else "lastTestRun"] = {"id": 9}
    elif damage == "state":
        point["state"] = "InProgress"
    elif damage == "manual":
        ado.points[pid, record["suites"]["broker"]][0]["outcome"] = "Passed"
    elif damage == "receipt":
        record["operations"]["suite:category:Local:UI Automation"]["sha256"] = "changed"
    elif damage == "stale":
        point["assignedTo"] = {"id": "another-tester"}
    writes = deepcopy(ado.writes)
    with pytest.raises(ValueError):
        F.correct_stress_category(record, key, approved, lambda: None)
    assert ado.writes == writes


@pytest.mark.parametrize("failure", ["lost_ack", "no_effect", "post_drift", "pre_drift", "checkpoint"])
def test_correction_uncertainty_is_result_only_never_replays_rename(monkeypatch, failure):
    ado, record, rc, key = completed(monkeypatch)
    original_points = deepcopy(ado.points)
    preview = F.preview_stress_correction(record, key)
    original = ado.send
    sid, pid = preview["intent"]["suite_id"], record["plan_id"]

    def send(url, method, body, timeout):
        if url == preview["intent"]["external_request"]["url"] and method == "PATCH":
            if failure == "no_effect":
                ado.writes.append((url, method, deepcopy(body)))
                return True, {}, ""
            result = original(url, method, body, timeout)
            if failure == "post_drift":
                ado.points[pid, sid][0]["comment"] = "Changed during rename"
            return (False, None, "Lost rename acknowledgement") if failure == "lost_ack" else result
        return original(url, method, body, timeout)

    monkeypatch.setattr(P, "_ado_rest_send", send)
    before = len(ado.writes)

    def checkpoint():
        if failure == "checkpoint":
            raise PermissionError("Receipt unavailable")
        if failure == "pre_drift":
            ado.points[pid, sid][0]["comment"] = "Changed after reservation"

    with pytest.raises((ValueError, PermissionError)):
        F.correct_stress_category(record, key, preview["correction_sha256"], checkpoint)
    assert record["classification_amendment"]["status"] == "applying"
    assert len(ado.writes) == before + (failure not in ("checkpoint", "pre_drift"))
    writes = deepcopy(ado.writes)
    with pytest.raises(ValueError):
        F.target(record)
    assert not B.validate_plan(pid, record["identity"], record["source"], resource=record)[0]
    from orchestrator.state import ReleaseState, StepState
    from tests._context import context
    from steps.bug_bash import ui_test_status as U
    state = ReleaseState(release_id="2000-01", owner_email="owner@example.test")
    state.pipeline_runs = {"rcs": [rc]}
    state.resources["broker_test_plan"] = deepcopy(record)
    state.set_step("bug_bash", "clone_plans_broker", StepState(data={"plan_id": pid}))
    assert U.prepare_effect(context(state)).kind == "blocked"
    if failure == "lost_ack":
        recovery = F.preview_stress_correction(record, key, recovery=True)
        F.correct_stress_category(record, key, recovery["correction_sha256"], lambda: None, recovery=True)
        assert record["classification_amendment"]["status"] == "applied"
        assert ado.points == original_points
    else:
        with pytest.raises(ValueError):
            F.preview_stress_correction(record, key, recovery=True)
    assert ado.writes == writes


@pytest.mark.parametrize("damage", [
    "result_only", "run_only", "incomplete", "missing_state", "missing_details", "bad_details",
    "negative_id", "bad_id", "completed_without_ids", "manual_history", "manual_details"])
def test_correction_rejects_inconsistent_published_or_unreceipted_result_history(monkeypatch, damage):
    ado, record, rc, key = completed(monkeypatch)
    pid, sid = record["plan_id"], record["suites"]["category:Local:UI Automation"]
    point = ado.points[pid, sid][0]
    if damage == "result_only":
        point["lastTestRun"] = {"id": "0"}
    elif damage == "run_only":
        point["lastResult"] = None
    elif damage == "incomplete":
        point["lastResultState"] = "InProgress"
    elif damage == "missing_state":
        point.pop("lastResultState")
    elif damage == "missing_details":
        point.pop("lastResultDetails")
    elif damage == "bad_details":
        point["lastResultDetails"] = ["not", "a", "dictionary"]
    elif damage == "negative_id":
        point["lastResult"]["id"] = -1
    elif damage == "bad_id":
        point["lastTestRun"]["id"] = True
    elif damage == "completed_without_ids":
        point["lastResult"] = point["lastTestRun"] = None
        point["lastResultDetails"] = {}
    else:
        manual = ado.points[pid, record["suites"]["broker"]][0]
        manual["lastResultDetails"] = deepcopy(point["lastResultDetails"])
        if damage == "manual_history":
            manual.update(lastResult=deepcopy(point["lastResult"]), lastTestRun=deepcopy(point["lastTestRun"]),
                          lastResultState="Completed")
    writes, original = deepcopy(ado.writes), deepcopy(record)
    with pytest.raises(ValueError):
        F.preview_stress_correction(record, key)
    assert ado.writes == writes and record == original


@pytest.mark.parametrize("stage", ["review", "reservation", "post_rename", "recovery_preview", "recovery_apply"])
@pytest.mark.parametrize("field", ["run", "details", "tester"])
def test_published_history_drift_at_any_correction_boundary_holds_without_replay(monkeypatch, stage, field):
    ado, record, rc, key = completed(monkeypatch)
    pid, sid = record["plan_id"], record["suites"]["category:Local:UI Automation"]
    point = ado.points[pid, sid][0]
    preview = F.preview_stress_correction(record, key)
    before = len(ado.writes)

    def change():
        if field == "run":
            point["lastTestRun"]["id"] = "6599999"  # Same Failed outcome, distinct newer history.
        elif field == "details":
            point["lastResultDetails"]["dateCompleted"] = "2026-10-18T06:52:57.927Z"
        else:
            point["assignedTo"]["id"] = "22222222-2222-2222-2222-222222222222"

    old_send = ado.send
    def send(url, method, body, timeout):
        result = old_send(url, method, body, timeout)
        if url == preview["intent"]["external_request"]["url"] and method == "PATCH":
            if stage == "post_rename":
                change()
            if stage.startswith("recovery_"):
                return False, None, "Lost acknowledgement"
        return result
    monkeypatch.setattr(P, "_ado_rest_send", send)
    if stage == "review":
        change()
    with pytest.raises(ValueError):
        F.correct_stress_category(record, key, preview["correction_sha256"],
                                   change if stage == "reservation" else lambda: None)
    assert len(ado.writes) == before + (stage not in ("review", "reservation"))
    if stage in ("review", "reservation"):
        return
    if stage == "recovery_apply":
        recovery = F.preview_stress_correction(record, key, recovery=True)
        change()
        with pytest.raises(ValueError):
            F.correct_stress_category(record, key, recovery["correction_sha256"], lambda: None, recovery=True)
    else:
        if stage == "recovery_preview":
            change()
        with pytest.raises(ValueError):
            F.preview_stress_correction(record, key, recovery=True)
    assert record["classification_amendment"]["status"] == "applying"
    assert len(ado.writes) == before + 1


@pytest.mark.parametrize("published", [False, True])
def test_correction_history_is_opaque_not_a_duration_or_actor_origin_heuristic(monkeypatch, published):
    ado, record, rc, key = completed(monkeypatch, published=published)
    if published:
        for receipt in record["fill"]["applied_points"]:
            point = next(p for p in ado.points[record["plan_id"], receipt["suite_id"]]
                         if p["id"] == receipt["point_id"])
            point["lastResultDetails"]["duration"] = 123456
            point["lastResultDetails"]["runBy"] = {"id": "different-caller", "displayName": "Another Caller"}
    points = deepcopy(ado.points)
    preview = F.preview_stress_correction(record, key)
    F.correct_stress_category(record, key, preview["correction_sha256"], lambda: None)
    assert ado.points == points


@pytest.mark.parametrize("damage", ["approval", "before", "history", "current_category",
                                   "current_fill_category", "current_point_id", "current_fill_build",
                                   "current_outcome", "operations", "readback"])
def test_applied_correction_cannot_mask_arbitrary_provenance_drift(monkeypatch, damage):
    ado, record, rc, key = completed(monkeypatch)
    preview = F.preview_stress_correction(record, key)
    F.correct_stress_category(record, key, preview["correction_sha256"], lambda: None)
    amendment = record["classification_amendment"]
    if damage == "approval":
        amendment["approval_sha256"] = "0" * 64
    elif damage == "before":
        amendment["before"]["plan"]["name"] = "changed"
    elif damage == "history":
        amendment["history"]["fill"]["applied_points"][0]["outcome"] = "Passed"
    elif damage == "current_category":
        next(r for r in record["scenario_bindings"] if r["key"] == key)["category"] = "Unit Tests"
    elif damage == "current_fill_category":
        next(r for r in record["fill"]["scenario_bindings"] if r["key"] == key)["category"] = "Unit Tests"
    elif damage == "current_point_id":
        record["fill"]["applied_points"][0]["point_id"] += 999
    elif damage == "current_fill_build":
        record["fill"]["scenario_bindings"][0]["build_id"] += 999
    elif damage == "current_outcome":
        record["fill"]["applied_points"][0]["outcome"] = "Passed"
    elif damage == "operations":
        record["operations"]["suite:category:Local:UI Automation"]["payload"]["name"] = "Stress Tests"
    else:
        amendment["readback_sha256"] = "0" * 64
    writes = deepcopy(ado.writes)
    with pytest.raises(ValueError):
        F.target(record)
    assert ado.writes == writes


def test_correction_preserves_point_embedded_suite_identity_and_default_testers(monkeypatch):
    ado, record, rc, key = completed(monkeypatch)
    sid, pid = record["suites"]["category:Local:UI Automation"], record["plan_id"]
    point = ado.points[pid, sid][0]
    point["suite"] = {"id": str(sid), "name": "UI Automation"}
    ado.suites[pid, sid]["defaultTesters"] = [{"id": "tester", "uniqueName": "owner@example.test"}]
    preview = F.preview_stress_correction(record, key)
    F.correct_stress_category(record, key, preview["correction_sha256"], lambda: None)
    assert point["suite"] == {"id": str(sid), "name": "Stress Tests"}
    assert ado.suites[pid, sid]["defaultTesters"] == [{"id": "tester", "uniqueName": "owner@example.test"}]


def test_amendment_retains_historical_fill_but_allows_existing_checked_resolved_fill(monkeypatch):
    ado, record, rc, key = completed(monkeypatch)
    original = deepcopy(record)
    reviewed = F.preview_stress_correction(record, key)
    F.correct_stress_category(record, key, reviewed["correction_sha256"], lambda: None)
    for flight in ("ecs", "local"):
        suites = {s["name"]: [(r["title"], "Passed") for r in s["test_results"]]
                  for s in rc[flight]["tests"]["suites"]}
        rc[flight] = snapshot(int(rc[flight]["run_id"]), suites)
    ok, projection, detail = F.project(rc, tracked=record["scenario_bindings"],
                                        classification_policy=F.policy_for_record(record))
    assert ok, detail
    bound = S.bind_projection(record, projection)
    target = F.target(record)
    ok, fill, detail = T.fill_ui_automation_results(
        record["plan_id"], bound["verdicts"], automation_target=target, scenario_resource=record,
        scenario_bindings=bound["scenario_bindings"], prior_points=record["fill"]["applied_points"],
        checkpoint=lambda partial: record.update(fill=deepcopy(partial)))
    assert ok, detail
    record["fill"] = fill
    assert F.target(record) == target and fill["set_passed"] == 9
    assert record["classification_amendment"]["history"]["fill"] == original["fill"]
    assert record["source"] == original["source"] and record["operations"] == original["operations"]
    assert B.validate_plan(record["plan_id"], record["identity"], record["source"], resource=record)[0]


@pytest.mark.parametrize("lost_ack", [False, True])
def test_isolated_cli_correction_preview_apply_recovery_and_replay(monkeypatch, tmp_path, lost_ack):
    from argparse import Namespace
    from orchestrator.commands import broker_scenario_plan as command
    from orchestrator.state import ReleaseState
    ado, record, rc, key = completed(monkeypatch)
    state = ReleaseState(release_id="2000-01", owner_email="owner@example.test")
    state.pipeline_runs = {"rcs": [rc]}
    snapshot = tmp_path / "protected" / "release-state.json"
    state.save(str(snapshot))
    raw = snapshot.read_bytes()
    record["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
    root = tmp_path / "isolated"
    receipt = root / record["identity"]["release_id"] / "broker-scenario-resource.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(record), encoding="utf-8")
    original = receipt.read_bytes()
    args = Namespace(snapshot=str(snapshot), isolated_root=str(root), name=record["identity"]["name"],
                     execute=False, confirm_source_sha256=None, recover_create=None, resource_id=None,
                     reason=None, correct_stress_category=key, recover_stress_category=None,
                     confirm_correction_sha256=None, correction_preview=str(tmp_path / "correction.json"))
    writes = deepcopy(ado.writes)
    reviewed = command.run(args)
    artifact = json.loads(Path(args.correction_preview).read_text(encoding="utf-8"))
    assert artifact == reviewed["classification_correction"]
    assert ado.writes == writes and receipt.read_bytes() == original
    args.execute, args.correction_preview = True, None
    args.confirm_source_sha256 = reviewed["preview"]["source_sha256"]
    with pytest.raises(ValueError, match="confirm-correction"):
        command.run(args)
    assert ado.writes == writes and receipt.read_bytes() == original
    args.confirm_correction_sha256 = artifact["correction_sha256"]
    if lost_ack:
        old_send = ado.send
        def send(url, method, body, timeout):
            result = old_send(url, method, body, timeout)
            if url == artifact["intent"]["external_request"]["url"] and method == "PATCH":
                return False, None, "Lost rename acknowledgement"
            return result
        monkeypatch.setattr(P, "_ado_rest_send", send)
        with pytest.raises(ValueError, match="Uncertain"):
            command.run(args)
        attempted = json.loads(receipt.read_text(encoding="utf-8"))
        assert attempted["classification_amendment"]["status"] == "applying"
        args.correct_stress_category, args.recover_stress_category, args.execute = None, key, False
        recovery = command.run(args)
        args.confirm_correction_sha256 = recovery["classification_correction"]["correction_sha256"]
        args.execute = True
    result = command.run(args)
    assert not result["creates"] and not result["outcome_writes"]
    applied = json.loads(receipt.read_text(encoding="utf-8"))
    assert applied["classification_amendment"]["status"] == "applied"
    assert applied["source"] == record["source"] and applied["operations"] == record["operations"]
    assert len(ado.writes) == len(writes) + 1
    args.correct_stress_category = args.recover_stress_category = None
    args.confirm_correction_sha256 = None
    assert command.run(args)["fill_status"] == "complete"
    assert len(ado.writes) == len(writes) + 1 and snapshot.read_bytes() == raw
    assert F.target(json.loads(receipt.read_text(encoding="utf-8"))) == F.target(applied)


def test_actual_protected_rc2_and_receipts_have_pure_source_bound_correction_model():
    if os.environ.get("RELEASE_AGENT_STRESS_PROOF") != "1":
        pytest.skip("Opt-in pure offline check of the owner's protected pre-correction fingerprints")
    root = Path(__file__).resolve().parents[2]
    paths = [
        (root / ".release-runs" / "2026-10" / "release-state.json",
         "52efad6cce6fc118c084cff2e7c8ce587ee29f99d5b6bff8b595e4ae19f6fe96"),
        (root / ".release-agent-isolated" / "2026-10-scenario-f7342f158464e3f1" / "broker-scenario-resource.json",
         "7ef133d93e194b67c9e9204b8d8bd61bc0901980df719f1e08678fb6ad74dc5f"),
        (root / ".release-agent-isolated" / "2026-10-scenario-0a0393d9b0382a0b" / "broker-scenario-resource.json",
         "07d6f3ea393920c40f06b9f45bca6bb1511ae1944084a0f51a55fae1bc399460")]
    if not all(path.is_file() for path, _ in paths):
        pytest.skip("Protected local RC2 files are optional read-only proof inputs")
    originals = [path.read_bytes() for path, _ in paths]
    assert [hashlib.sha256(raw).hexdigest() for raw in originals] == [sha for _, sha in paths]
    state, old, record = map(json.loads, originals)
    B._validate_source(old["source"])
    B._validate_source(record["source"])
    assert S.digest(record["source"]) == "af4ade221f14eb0370761809a72f17a899ce0792d4641a15d63658c14cf83cad"
    key = "11ead52ada0ecbfdecca4446f2611166a1df6d7cc31ee3302b66dc81d55f107a"
    intent = F._stress_intent(record, {k: deepcopy(record[k]) for k in F._HISTORY_FIELDS}, key)
    assert (intent["plan_id"], intent["suite_id"], intent["case_id"], intent["point_id"],
            intent["config_id"], intent["build_id"]) == (3793422, 3793433, 3793438, 3267889, 330, 1705758)
    assert intent["external_request"]["payload"] == {
        "name": "Stress Tests", "suiteType": "staticTestSuite",
        "parentSuite": {"id": 3793432}, "inheritDefaultConfigurations": False,
        "defaultConfigurations": [{"id": 330}]}
    ok, future, detail = F.project(state["pipeline_runs"]["rcs"][-1])
    assert ok, detail
    assert Counter((r["flight"], r["category"]) for r in future["triage"]) == {
        ("ECS", "UI Automation"): 6, ("ECS", "Instrumented Tests"): 1,
        ("Local", "Stress Tests"): 1, ("Local", "Instrumented Tests"): 1}
    ok, legacy_v3, detail = S.project(state["pipeline_runs"]["rcs"][-1],
                                     tracked=old["scenario_bindings"], version=3)
    assert ok, detail
    assert S.bind_projection(old, legacy_v3)["scenario_bindings"] == old["scenario_bindings"]
    assert all(path.read_bytes() == raw for (path, _), raw in zip(paths, originals))
