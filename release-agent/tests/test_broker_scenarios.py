"""Exact-source creation and fill contracts, using only an in-memory provider."""
from copy import deepcopy
import hashlib
import json
import os
import re

import pytest

from tools import broker_scenarios as S, broker_plans as B, testplans as T, pipelines as P
from tests._mrwp_evidence import current_rc, PROD, RC


def source(rc, catalog=(100, 200)):
    ok, projection, detail = S.project(rc)
    assert ok, detail
    result = {"schema_version": 3, "scenario_projection": projection, "root_configs": [293],
              "broker_cases": [701, 702], "broker_configs": list(T.BROKER_CONFIGS),
              "ui_cases": list(catalog), "ui_configs": list(T.BROKER_UI_CONFIGS),
              "native_query": "SELECT [System.Id] FROM WorkItems WHERE [System.Tags] CONTAINS 'Native'"}
    B._validate_source(result)
    return result


def evidence():
    return current_rc({
        PROD: [("test_100_Scenario[a]", "Failed"), ("test_100_Scenario[a]", "Passed"),
               ("test_100_Scenario[b]", "Failed"), ("test_200_Single", "NotApplicable")],
        "PROD MSAL - RC BrokerHost (API 32)": [("test_100_Host", "Passed")],
        "Stress Tests - RC MSAL with RC Broker (API 32)": [("test_concurrentAcquireTokenSilent_withBroker", "Failed")],
        "Lab Api Tests": [("test_lab", "Failed")],
        "Native Auth E2E Tests": [("test_native", "Passed")],
        "AADAuthenticator_InstrumentedTests": [("test_instrumented", "Failed")],
        "common_UnitTests": [("test_unit", "Failed"), ("test_unit", "Passed")],
    }, {PROD: [("test_100_Local", "Passed")], "Lab Api Tests": [("test_lab", "Passed")]})


class ADO:
    def __init__(self, monkeypatch):
        self.plans, self.suites, self.points, self.cases, self.writes = {}, {}, {}, {}, []
        self.next_id, self.failure, self.checkpoint = 9000, None, None
        self.ignore_assignments = False
        monkeypatch.setattr(P, "_ado_rest_get_all", self.get_all)
        monkeypatch.setattr(P, "_ado_rest_get", self.get)
        monkeypatch.setattr(P, "_ado_rest_send", self.send)

    def new_id(self):
        self.next_id += 1
        return self.next_id

    def get_all(self, url, timeout):
        if "/testplan/plans?" in url.lower():
            return True, deepcopy(list(self.plans.values())), ""
        match = re.search(r"/Plans/(\d+)/[Ss]uites\?", url)
        if match:
            return True, deepcopy([s for (pid, _), s in self.suites.items() if pid == int(match[1])]), ""
        match = re.search(r"/Plans/(\d+)/Suites/(\d+)/points\?", url)
        if match:
            return True, deepcopy(self.points.get(tuple(map(int, match.groups())), [])), ""
        raise AssertionError(url)

    def get(self, url, timeout):
        match = re.search(r"/testplan/plans/(\d+)\?", url)
        if match:
            return True, deepcopy(self.plans[int(match[1])]), ""
        match = re.search(r"/Plans/(\d+)/[Ss]uites/(\d+)\?", url)
        if match:
            return True, deepcopy(self.suites[tuple(map(int, match.groups()))]), ""
        match = re.search(r"/wit/workitems/(\d+)\?", url)
        if match:
            return True, deepcopy(self.cases[int(match[1])]), ""
        raise AssertionError(url)

    def send(self, url, method, body, timeout):
        self.writes.append((url, method, deepcopy(body)))
        if self.failure == "case" and "/wit/workitems/$" in url:
            self.failure = None
            return False, None, "Lost acknowledgement"
        if "/testplan/plans?" in url and method == "POST":
            pid, root = self.new_id(), self.new_id()
            self.plans[pid] = {**body, "id": pid, "rootSuite": {"id": root}}
            self.suites[pid, root] = {"id": root, "name": body["name"], "suiteType": "staticTestSuite"}
            if self.failure == "crash_after_plan":
                self.failure = None
                raise SystemExit("Server created plan; receipt lost")
            return True, deepcopy(self.plans[pid]), ""
        match = re.search(r"/testplan/plans/(\d+)\?", url)
        if match and method == "PATCH":
            plan = self.plans[int(match[1])]
            plan["areaPath"] = body.get("areaPath", T.PROJECT)
            plan.update(body)
            if self.failure == "metadata_lost_ack":
                self.failure = None
                return False, None, "Lost metadata acknowledgement"
            return True, {}, ""
        match = re.search(r"/Plans/(\d+)/suites\?", url)
        if match and method == "POST":
            sid = self.new_id()
            self.suites[int(match[1]), sid] = {**deepcopy(body), "id": sid}
            if "defaultConfigurations" in body:
                self.suites[int(match[1]), sid]["defaultConfigurations"] = []
            if self.failure == "crash_after_suite":
                self.failure = None
                raise SystemExit("Server created suite; receipt lost")
            return True, {"id": sid}, ""
        match = re.search(r"/Plans/(\d+)/suites/(\d+)\?", url)
        if match and method == "PATCH":
            if self.failure == "pin_no_effect":
                self.failure = None
                return True, {}, ""
            key = tuple(map(int, match.groups()))
            modern = "/_apis/testplan/" in url
            self.suites[key].update({k: deepcopy(v) for k, v in body.items()
                                     if not modern or k != "defaultConfigurations"})
            if modern and "name" in body:
                for point in self.points.get(key, []):
                    if isinstance(point.get("suite"), dict):
                        point["suite"]["name"] = body["name"]
            if not modern and "defaultConfigurations" in body and key in self.points:
                existing = {(p["testCase"]["id"], p["configuration"]["id"]): p for p in self.points[key]}
                self.points[key] = [
                    existing.get((cid, config["id"])) or {
                        "id": self.new_id(), "testCase": {"id": cid},
                        "configuration": {"id": config["id"]}, "outcome": "Unspecified"}
                    for cid in sorted({cid for cid, _ in existing})
                    for config in body["defaultConfigurations"]]
            if self.failure == "pin_lost_ack":
                self.failure = None
                return False, None, "Lost pin acknowledgement"
            return True, {}, ""
        if "/wit/workitems/$" in url:
            cid = self.new_id()
            fields = {p["path"].split("/")[-1]: p["value"] for p in body if p["path"].startswith("/fields/")}
            fields["System.WorkItemType"] = "Test Case"
            self.cases[cid] = {"id": cid, "fields": fields,
                               "relations": [p["value"] for p in body if p["path"] == "/relations/-"]}
            if self.failure == "crash_after_case":
                self.failure = None
                raise SystemExit("Server created case; receipt lost")
            return True, {"id": cid}, ""
        match = re.search(r"/Plans/(\d+)/Suites/(\d+)/TestCase\?", url)
        if match and method in ("POST", "PATCH"):
            key = tuple(map(int, match.groups()))
            if method == "POST":
                assert key not in self.points, "No repeated membership POST"
            if self.failure == "repair_no_effect" and method == "PATCH":
                self.failure = None
                return True, {}, ""
            existing = {(p["testCase"]["id"], p["configuration"]["id"]): p
                        for p in self.points.get(key, [])}
            self.points[key] = [
                existing.get((c["workItem"]["id"], cfg["configurationId"])) or {
                    "id": self.new_id(), "testCase": {"id": c["workItem"]["id"]},
                    "configuration": {"id": cfg["configurationId"]}, "outcome": "Unspecified"}
                for c in body for cfg in (
                    [{"configurationId": x["id"]} for x in self.suites[key]["defaultConfigurations"]]
                    if self.ignore_assignments else c["pointAssignments"])]
            if self.failure == "repair_lost_ack" and method == "PATCH":
                self.failure = None
                return False, None, "Lost assignment acknowledgement"
            if self.failure == "membership":
                self.failure = None
                return False, None, "Lost membership acknowledgement"
            return True, {}, ""
        match = re.search(r"/Plans/(\d+)/Suites/(\d+)/points/([\d,]+)\?", url)
        if match and method == "PATCH":
            pid, sid, ids = int(match[1]), int(match[2]), set(map(int, match[3].split(",")))
            for p in self.points[pid, sid]:
                if p["id"] in ids:
                    p.update(body)
            return True, {}, ""
        raise AssertionError((url, method))


def create(ado, frozen, record=None):
    record = record if record is not None else {}
    receipts = []
    def checkpoint():
        receipts.append(deepcopy(record))
    ok, pid, detail = B.ensure_plan("2000-01", "TEST ONLY exact Broker scenarios", record, checkpoint,
                                    prepared_source=frozen)
    return ok, pid, detail, record, receipts


def metadata_regression(monkeypatch):
    ado = ADO(monkeypatch)
    frozen, record = source(evidence()), {}
    ok, pid, detail = B.ensure_plan("2000-01", "TEST ONLY - frozen metadata", record,
                                   lambda: None, prepared_source=frozen)
    assert ok, detail
    ado.plans[pid]["areaPath"] = T.PROJECT
    return ado, frozen, record, pid


def test_completion_patch_preserves_verified_identity_and_optional_metadata(monkeypatch):
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    plan = ado.plans[pid]
    plan.update(owner={"id": "owner-id"}, state="Active", startDate="2000-01-01T00:00:00Z",
                endDate="2000-02-01T00:00:00Z", buildId=123)
    plan["description"] = plan["description"].replace("\n" + B._COMPLETE, "")
    record["status"] = "created"
    before = deepcopy(plan)
    ok, _, detail, _, receipts = create(ado, frozen, record)
    assert ok, detail
    payload = ado.writes[-1][2]
    assert all(payload[k] == before[k] for k in T._PLAN_UPDATE_FIELDS
               if k in before and k != "description")
    assert plan["areaPath"] == T.BROKER_AREA_PATH
    assert payload["description"] == before["description"] + "\n" + B._COMPLETE
    assert receipts[-2]["status"] == "created" and receipts[-1]["status"] == "ready"
    assert record["source"] == frozen and len(ado.plans) == 1


@pytest.mark.parametrize("damage", ["areaPath", "name", "iteration", "description", "rootSuite", "read"])
def test_completion_patch_bad_readback_never_publishes_ready(monkeypatch, damage):
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    ado.plans[pid]["description"] = ado.plans[pid]["description"].replace("\n" + B._COMPLETE, "")
    record["status"] = "created"
    send = ado.send

    def damaged(url, method, body, timeout):
        result = send(url, method, body, timeout)
        if "/testplan/plans/" in url and method == "PATCH":
            if damage == "read":
                get = ado.get
                monkeypatch.setattr(P, "_ado_rest_get", lambda u, t:
                                    (False, None, "HTTP 503") if "/testplan/plans/" in u else get(u, t))
            elif damage == "rootSuite":
                ado.plans[pid][damage] = {"id": record["suites"]["ui"]}
            else:
                ado.plans[pid][damage] = T.PROJECT if damage == "areaPath" else "changed"
        return result

    monkeypatch.setattr(P, "_ado_rest_send", damaged)
    ok, actual, detail, _, receipts = create(ado, frozen, record)
    assert not ok and actual == pid and "readback" in detail
    assert record["status"] == "created" and record["area_path"] == T.BROKER_AREA_PATH
    assert all(r["status"] != "ready" for r in receipts)
    assert len(ado.plans) == 1 and record["source"] == frozen


def test_completion_lost_ack_resumes_by_readback_without_duplicate_patch_or_create(monkeypatch):
    ado = ADO(monkeypatch)
    ado.failure = "metadata_lost_ack"
    frozen = source(evidence())
    ok, pid, _, record, _ = create(ado, frozen)
    assert not ok and record["status"] == "created"
    writes = deepcopy(ado.writes)
    assert create(ado, frozen, record)[0]
    assert ado.writes == writes and len(ado.plans) == 1
    assert ado.plans[pid]["areaPath"] == T.BROKER_AREA_PATH


def test_frozen_metadata_restoration_is_single_checked_patch(monkeypatch):
    ado, frozen, record, pid = metadata_regression(monkeypatch)
    original = deepcopy(record)
    points, writes = deepcopy(ado.points), len(ado.writes)
    preview = B.preview_metadata_repair(record)
    assert len(ado.writes) == writes and record == original
    receipts = []
    result = B.repair_metadata(record, preview["repair_sha256"],
                               lambda: receipts.append(deepcopy(record)))
    assert result["creates"] is False and result["outcome_writes"] is False
    assert len(ado.writes) == writes + 1 and ado.writes[-1][1] == "PATCH"
    assert ado.plans[pid]["areaPath"] == original["identity"]["area"]
    assert receipts[0]["operations"]["metadata-repair"]["status"] == "applying"
    assert receipts[-1]["operations"]["metadata-repair"]["status"] == "applied"
    assert ado.points == points and record["source"] == frozen
    assert all(record["operations"][key] == value for key, value in original["operations"].items())
    writes = deepcopy(ado.writes)
    assert B.ensure_plan("2000-01", record["identity"]["name"], record, lambda: None,
                         prepared_source=frozen)[0]
    assert ado.writes == writes


@pytest.mark.parametrize("damage", [
    "fill", "fill_status", "outcome", "manual_outcome", "run", "area", "name", "iteration", "ownership",
    "completion", "frozen", "plan_id", "record_area", "source", "membership", "stale"])
def test_frozen_metadata_restoration_guards_precede_write(monkeypatch, damage):
    ado, frozen, record, pid = metadata_regression(monkeypatch)
    preview = B.preview_metadata_repair(record)
    sid = record["suites"]["ui"]
    if damage in ("fill", "fill_status"):
        record[damage] = {}
    elif damage == "outcome":
        ado.points[pid, sid][0]["outcome"] = "Passed"
    elif damage == "manual_outcome":
        ado.points[pid, record["suites"]["broker"]][0]["outcome"] = "Passed"
    elif damage == "run":
        ado.points[pid, sid][0]["lastTestRun"] = {"id": 99}
    elif damage in ("area", "name", "iteration"):
        ado.plans[pid][{"area": "areaPath"}.get(damage, damage)] = "different"
    elif damage in ("ownership", "completion"):
        ado.plans[pid]["description"] = ado.plans[pid]["description"].replace(
            record["identity"]["marker"] if damage == "ownership" else B._COMPLETE, "")
    elif damage == "frozen":
        record["operations"]["plan"]["payload"]["areaPath"] = T.PROJECT
    elif damage == "plan_id":
        record["operations"]["plan"]["id"] += 1
    elif damage == "record_area":
        record["area_path"] = T.PROJECT
    elif damage == "source":
        record["source"]["broker_cases"].append(999)
    elif damage == "membership":
        ado.points[pid, sid].pop()
    elif damage == "stale":
        ado.plans[pid]["state"] = "Inactive"
    writes = deepcopy(ado.writes)
    with pytest.raises(ValueError):
        B.repair_metadata(record, preview["repair_sha256"], lambda: None)
    assert ado.writes == writes


@pytest.mark.parametrize("failure", ["metadata_lost_ack", "no_effect"])
def test_frozen_metadata_uncertain_attempt_never_repatches(monkeypatch, failure):
    ado, frozen, record, pid = metadata_regression(monkeypatch)
    preview = B.preview_metadata_repair(record)
    if failure == "no_effect":
        monkeypatch.setattr(P, "_ado_rest_send", lambda *args: (True, {}, ""))
    else:
        ado.failure = failure
    with pytest.raises(ValueError):
        B.repair_metadata(record, preview["repair_sha256"], lambda: None)
    assert record["operations"]["metadata-repair"]["status"] == "applying"
    writes = deepcopy(ado.writes)
    assert not B.validate_plan(pid, record["identity"], frozen, resource=record)[0]
    assert not B.ensure_plan("2000-01", record["identity"]["name"], record, lambda: None,
                             prepared_source=frozen)[0]
    if failure == "no_effect":
        with pytest.raises(ValueError, match="never re-PATCH"):
            B.preview_metadata_repair(record)
    else:
        recovery = B.preview_metadata_repair(record)
        assert recovery["recovery_only"]
        B.repair_metadata(record, recovery["repair_sha256"], lambda: None)
        assert record["operations"]["metadata-repair"]["status"] == "applied"
    assert ado.writes == writes and len(ado.plans) == 1


@pytest.mark.parametrize("failure", ["checkpoint", "point_changed"])
def test_frozen_metadata_reservation_failure_or_later_drift_prevents_patch(monkeypatch, failure):
    ado, frozen, record, pid = metadata_regression(monkeypatch)
    preview = B.preview_metadata_repair(record)
    writes = deepcopy(ado.writes)

    def checkpoint():
        assert record["operations"]["metadata-repair"]["status"] == "applying"
        if failure == "checkpoint":
            raise PermissionError("Receipt unavailable")
        ado.points[pid, record["suites"]["broker"]][0]["outcome"] = "Passed"

    with pytest.raises((PermissionError, ValueError)):
        B.repair_metadata(record, preview["repair_sha256"], checkpoint)
    assert ado.writes == writes and record["operations"]["metadata-repair"]["status"] == "applying"


def unpublished_expansion(monkeypatch, *, large=False):
    ado = ADO(monkeypatch)
    if large:
        cases = [("test_" + str(cid) + "_Scenario", "Passed") for cid in range(1000, 1100)]
        rc = current_rc(
            {PROD: cases, RC: cases[:8], "RC MSAL - RC Broker (API 32)": cases[8:15],
             "AADAuthenticator_InstrumentedTests": [("instrumented_ecs", "Failed")]},
            {PROD: cases, "RC MSAL - RC Broker (API 32)": cases[15:22],
             "AADAuthenticator_InstrumentedTests": [("instrumented_local", "Failed")]})
        frozen = source(rc, range(1000, 1100))
    else:
        frozen = source(evidence())
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    record["status"] = "created"
    ado.plans[pid]["description"] = "\n".join(
        line for line in ado.plans[pid]["description"].splitlines() if line != B._COMPLETE)
    sid = record["suites"]["ui"]
    targets = [sid] + ([record["suites"]["Instrumented Tests"]] if large else [])
    for target in targets:
        configs = [c["id"] for c in ado.suites[pid, target]["defaultConfigurations"]]
        assert T._set_suite_configs(pid, target, configs)[0]
    return ado, frozen, record, pid, sid


def test_config_pins_resume_preserve_exact_222_assignments_and_human_results(monkeypatch):
    ado, frozen, record, pid, sid = unpublished_expansion(monkeypatch, large=True)
    assert len(ado.points[pid, sid]) == 500
    assert len(ado.points[pid, record["suites"]["Instrumented Tests"]]) == 4
    original_memberships = {k: deepcopy(value) for k, value in record["operations"].items()
                            if k.startswith("membership:")}
    writes = len(ado.writes)
    preview = S.preview_membership_repair(record)
    assert len(ado.writes) == writes
    assert sorted(len(op["remove_pairs"]) for op in preview["operations"]) == [2, 278]
    S.repair_membership(record, preview["repair_sha256"], lambda: None)
    assert len(ado.points[pid, sid]) == 222
    assert len(ado.points[pid, record["suites"]["Instrumented Tests"]]) == 2
    assert all(record["operations"][key] == value for key, value in original_memberships.items())
    writes = len(ado.writes)
    assert create(ado, frozen, record)[0]
    assert len(ado.writes) == writes + 1  # Only publish the completion marker.
    assert len(ado.points[pid, sid]) == 222
    ado.points[pid, sid][0]["outcome"] = "Failed"
    record["status"] = "created"  # Force builder resume instead of its ready fast path.
    writes = len(ado.writes)
    assert create(ado, frozen, record)[0]
    assert len(ado.writes) == writes
    assert len(ado.points[pid, sid]) == 222 and ado.points[pid, sid][0]["outcome"] == "Failed"
    assert record["source"] == frozen


@pytest.mark.parametrize("damage", [
    "ready", "fill", "fill_status", "published", "outcome", "run", "result", "state", "comment",
    "manual_result", "unknown_case", "unknown_config", "missing_expected", "partial_matrix",
    "duplicate_point", "changed_configs", "membership_hash", "suite_identity", "stale_preview",
])
def test_assignment_repair_fails_closed_before_any_write(monkeypatch, damage):
    ado, frozen, record, pid, sid = unpublished_expansion(monkeypatch)
    points = ado.points[pid, sid]
    wanted = {(r["case_id"], r["config_id"]) for r in record["scenario_bindings"]
              if r["suite_id"] == sid}
    extra = next(p for p in points if (p["testCase"]["id"], p["configuration"]["id"]) not in wanted)
    approved = S.preview_membership_repair(record)["repair_sha256"]
    if damage == "ready":
        record["status"] = "ready"
    elif damage in ("fill", "fill_status"):
        record[damage] = {}
    elif damage == "published":
        ado.plans[pid]["description"] += "\n" + B._COMPLETE
    elif damage == "outcome":
        extra["outcome"] = "Failed"
    elif damage in ("run", "result"):
        extra["lastTestRun" if damage == "run" else "lastResult"] = {"id": 99}
    elif damage == "state":
        extra["state"] = "InProgress"
    elif damage == "comment":
        extra["comment"] = "Human investigation"
    elif damage == "manual_result":
        ado.points[pid, record["suites"]["broker"]][0]["outcome"] = "Passed"
    elif damage == "unknown_case":
        extra["testCase"]["id"] = 999999
    elif damage == "unknown_config":
        extra["configuration"]["id"] = 999999
    elif damage == "missing_expected":
        points.remove(next(p for p in points if (p["testCase"]["id"], p["configuration"]["id"]) in wanted))
    elif damage == "partial_matrix":
        points.remove(extra)
    elif damage == "duplicate_point":
        points.append(deepcopy(extra))
    elif damage == "changed_configs":
        ado.suites[pid, sid]["defaultConfigurations"].append({"id": 999999})
    elif damage == "membership_hash":
        record["operations"]["membership:" + str(sid)]["sha256"] = "changed"
    elif damage == "suite_identity":
        ado.suites[pid, sid]["name"] = "Other"
    elif damage == "stale_preview":
        extra["id"] = ado.new_id()
    before, writes = deepcopy(record), deepcopy(ado.writes)
    with pytest.raises(ValueError):
        S.repair_membership(record, approved, lambda: None)
    assert ado.writes == writes and record == before and record["source"] == frozen


@pytest.mark.parametrize("failure", ["repair_lost_ack", "repair_no_effect"])
def test_assignment_repair_uncertain_attempt_recovers_by_readback_never_repatches(monkeypatch, failure):
    ado, frozen, record, pid, sid = unpublished_expansion(monkeypatch)
    preview = S.preview_membership_repair(record)
    ado.failure = failure
    checkpoints = []
    with pytest.raises(ValueError, match="Uncertain assignment repair|readback differs"):
        S.repair_membership(record, preview["repair_sha256"],
                            lambda: checkpoints.append(deepcopy(record)))
    key = "assignment-repair:" + str(sid)
    assert checkpoints[-1]["operations"][key]["status"] == "applying"
    writes = deepcopy(ado.writes)
    assert not create(ado, frozen, record)[0]
    assert ado.writes == writes
    if failure == "repair_no_effect":
        with pytest.raises(ValueError, match="never re-PATCH"):
            S.preview_membership_repair(record)
        assert ado.writes == writes
        wanted = {(r["case_id"], r["config_id"]) for r in record["scenario_bindings"]
                  if r["suite_id"] == sid}
        ado.points[pid, sid] = [p for p in ado.points[pid, sid]
                               if (p["testCase"]["id"], p["configuration"]["id"]) in wanted]
    recovery = S.preview_membership_repair(record)
    assert recovery["operations"][0]["recovery_only"] is True
    S.repair_membership(record, recovery["repair_sha256"], lambda: None)
    assert ado.writes == writes and record["operations"][key]["status"] == "applied"
    assert record["source"] == frozen


def test_assignment_repair_checkpoint_precedes_patch_and_later_suite_drift_stops_partial_work(monkeypatch):
    ado, frozen, record, pid, sid = unpublished_expansion(monkeypatch, large=True)
    preview = S.preview_membership_repair(record)
    before = len(ado.writes)

    def blocked():
        assert len(ado.writes) == before
        raise OSError("Disk failure before PATCH")

    with pytest.raises(OSError, match="Disk failure"):
        S.repair_membership(record, preview["repair_sha256"], blocked)
    assert len(ado.writes) == before
    # Synthetic durable state never accepted the failed local checkpoint.
    del record["operations"]["assignment-repair:" + str(sid)]
    later = preview["operations"][1]["suite_id"]

    def concurrent_result():
        if record["operations"].get("assignment-repair:" + str(sid), {}).get("status") == "applied":
            ado.points[pid, later][0]["outcome"] = "Failed"

    with pytest.raises(ValueError, match="changed after repair preview"):
        S.repair_membership(record, preview["repair_sha256"], concurrent_result)
    writes = ado.writes[before:]
    assert len(writes) == 1 and writes[0][1] == "PATCH" and f"/Suites/{sid}/TestCase?" in writes[0][0]
    assert record["operations"]["assignment-repair:" + str(sid)]["status"] == "applied"
    assert "assignment-repair:" + str(later) not in record["operations"]
    assert ado.points[pid, later][0]["outcome"] == "Failed" and record["source"] == frozen


def test_fresh_membership_success_requires_exact_full_readback_and_never_reposts(monkeypatch):
    ado = ADO(monkeypatch)
    ado.ignore_assignments = True
    frozen, record = source(evidence()), {}
    ok, pid, detail, _, _ = create(ado, frozen, record)
    assert not ok and "exact complete point readback" in detail
    sid = record["suites"]["ui"]
    assert record["operations"]["membership:" + str(sid)]["status"] == "applying"
    writes = deepcopy(ado.writes)
    ado.ignore_assignments = False
    assert not create(ado, frozen, record)[0]
    assert ado.writes == writes


@pytest.mark.parametrize("target", ["root", "ui"])
def test_changed_pinned_defaults_on_resume_never_reapply_or_reset_membership(monkeypatch, target):
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    record["status"] = "created"
    sid = ado.plans[pid]["rootSuite"]["id"] if target == "root" else record["suites"]["ui"]
    ado.suites[pid, sid]["defaultConfigurations"] = [{"id": 999999}]
    before, writes = deepcopy(ado.points), deepcopy(ado.writes)
    assert not create(ado, frozen, record)[0]
    assert ado.writes == writes and ado.points == before


@pytest.mark.parametrize("failure", ["pin_lost_ack", "pin_no_effect"])
def test_configuration_pin_uncertainty_is_readback_only_never_repatch(monkeypatch, failure):
    ado = ADO(monkeypatch)
    ado.failure = failure
    frozen, record = source(evidence()), {}
    ok, pid, detail, _, _ = create(ado, frozen, record)
    assert not ok and "configuration-pin" in detail
    root = ado.plans[pid]["rootSuite"]["id"]
    key = "config-pin:" + str(root)
    assert record["operations"][key]["status"] == "applying"
    writes = deepcopy(ado.writes)
    ok, _, detail, _, _ = create(ado, frozen, record)
    if failure == "pin_no_effect":
        assert not ok and "never reapply" in detail
        assert ado.writes == writes
    else:
        assert ok, detail
        assert record["operations"][key]["status"] == "applied"
        root_patches = [w for w in ado.writes if f"/suites/{root}?" in w[0] and w[1] == "PATCH"]
        assert len(root_patches) == 1 and len(ado.plans) == 1


def test_isolated_assignment_repair_requires_separate_consent_and_never_fills(monkeypatch, tmp_path, capsys):
    command, ado, args, snapshot, path = isolated_receipt_execution(monkeypatch, tmp_path)
    frozen = source(evidence())
    record = {}
    ok, pid, detail = B.ensure_plan(path.parent.name, args.name, record, lambda: None,
                                   prepared_source=frozen)
    assert ok, detail
    record["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    record["status"] = "created"
    ado.plans[pid]["description"] = "\n".join(
        line for line in ado.plans[pid]["description"].splitlines() if line != B._COMPLETE)
    sid = record["suites"]["ui"]
    configs = [c["id"] for c in ado.suites[pid, sid]["defaultConfigurations"]]
    assert T._set_suite_configs(pid, sid, configs)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    before, original, writes = path.read_bytes(), snapshot.read_bytes(), deepcopy(ado.writes)
    argv = ["--snapshot", args.snapshot, "--isolated-root", args.isolated_root,
            "--name", args.name, "--repair-membership"]
    assert command.main(argv) == 0
    preview = json.loads(capsys.readouterr().out)
    assert path.read_bytes() == before and ado.writes == writes
    execute = [*argv, "--execute", "--confirm-source-sha256", args.confirm_source_sha256]
    assert command.main(execute) == 1
    assert "confirm-repair-sha256" in json.loads(capsys.readouterr().out)["error"]
    assert path.read_bytes() == before and ado.writes == writes
    execute += ["--confirm-repair-sha256", preview["membership_repair"]["repair_sha256"]]
    assert command.main(execute) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome_writes"] is False and result["creates"] is False
    assert len(ado.writes) == len(writes) + 1 and ado.writes[-1][1] == "PATCH"
    assert "/TestCase?" in ado.writes[-1][0]
    saved = json.loads(path.read_bytes())
    assert saved["status"] == "created" and "fill" not in saved and "fill_status" not in saved
    assert saved["source"] == frozen and snapshot.read_bytes() == original
    assert not path.with_suffix(".pending").exists()
    assert all(saved["operations"][k] == value for k, value in record["operations"].items())


def test_isolated_metadata_restoration_preview_and_execution_never_fills(monkeypatch, tmp_path, capsys):
    command, ado, args, snapshot, path = isolated_receipt_execution(monkeypatch, tmp_path)
    frozen, record = source(evidence()), {}
    ok, pid, detail = B.ensure_plan(path.parent.name, args.name, record, lambda: None,
                                   prepared_source=frozen)
    assert ok, detail
    record["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    ado.plans[pid]["areaPath"] = T.PROJECT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    before, original, writes = path.read_bytes(), snapshot.read_bytes(), deepcopy(ado.writes)
    argv = ["--snapshot", args.snapshot, "--isolated-root", args.isolated_root,
            "--name", args.name, "--repair-metadata"]
    assert command.main(argv) == 0
    preview = json.loads(capsys.readouterr().out)
    assert path.read_bytes() == before and ado.writes == writes
    execute = [*argv, "--execute", "--confirm-source-sha256", args.confirm_source_sha256]
    assert command.main(execute) == 1
    assert path.read_bytes() == before and ado.writes == writes
    capsys.readouterr()
    execute += ["--confirm-repair-sha256", preview["metadata_repair"]["repair_sha256"]]
    assert command.main(execute) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["creates"] and not result["outcome_writes"]
    assert len(ado.writes) == len(writes) + 1
    saved = json.loads(path.read_bytes())
    assert saved["source"] == frozen and "fill" not in saved and "fill_status" not in saved
    assert saved["operations"]["metadata-repair"]["status"] == "applied"
    assert snapshot.read_bytes() == original
    assert all(saved["operations"][k] == value for k, value in record["operations"].items())


def test_exact_projection_preserves_parameter_api_host_and_retries():
    frozen = source(evidence())
    rows = frozen["scenario_projection"]["inventory"]
    assert len(rows) == 6
    assert CounterOutcomes(rows) == {"Passed": 3, "Failed": 2, "NotApplicable": 1}
    assert {r["category"] for r in frozen["scenario_projection"]["triage"]} == {"Lab API", "Instrumented Tests"}
    assert len(frozen["scenario_projection"]["warnings"]) == 2
    mapped = S.representations(frozen["scenario_projection"], frozen["ui_cases"])
    colliding = [r for r in mapped if r["original_case_id"] == 100 and r["flight"] == "ECS"]
    assert len(colliding) == 3 and all(r["reuse_case_id"] is None for r in colliding)
    assert next(r for r in mapped if r["title"] == "test_100_Local")["reuse_case_id"] == 100
    stress = next(r for r in mapped if "concurrent" in r["title"])
    assert stress["original_case_id"] is None and stress["reuse_case_id"] is None and stress["links"]


def CounterOutcomes(rows):
    from collections import Counter
    return dict(Counter(r["verdict"] for r in rows))


def test_builder_fill_and_triage_use_real_owned_ids_and_receipts(monkeypatch):
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    ok, pid, detail, record, receipts = create(ado, frozen)
    assert ok, detail
    assert record["status"] == "ready" and all(r["source"] == frozen for r in receipts)
    assert set(record["suites"]) == {"broker", "native", "ui", "triage", "Lab API", "Instrumented Tests"}
    assert len(ado.points[pid, record["ui_suite_id"]]) == 6
    assert {(p["testCase"]["id"], p["configuration"]["id"])
            for p in ado.points[pid, record["suites"]["broker"]]} == {
                (cid, cfg) for cid in frozen["broker_cases"] for cfg in T.BROKER_CONFIGS}
    native = ado.suites[pid, record["suites"]["native"]]
    assert native["inheritDefaultConfigurations"] and native["queryString"] == frozen["native_query"]
    assert not any("System.AssignedTo" in str(body) for _, _, body in ado.writes)
    ok, projection, detail = S.project(evidence(), tracked=record["scenario_bindings"])
    assert ok, detail
    bound = S.bind_projection(record, projection)
    progress = []
    ok, summary, detail = T.fill_ui_automation_results(
        pid, bound["verdicts"], suite_id=record["ui_suite_id"],
        scenario_resource=record, scenario_bindings=bound["scenario_bindings"], checkpoint=progress.append)
    assert ok, detail
    assert (summary["set_passed"], summary["set_failed"], summary["set_not_applicable"]) == (3, 4, 1)
    assert len(summary["applied_points"]) == 8 and summary["mapping_version"] == 3
    from steps.bug_bash.ui_results import applied_record
    receipt = applied_record(summary, [r["case_id"] for r in record["scenario_bindings"]],
                             "broker", summary["target"])
    assert receipt["suite_targets"] == sorted({r["suite_id"] for r in record["scenario_bindings"]})
    writes = len(ado.writes)
    assert create(ado, frozen, record)[0]
    assert len(ado.writes) == writes
    assert T.fill_ui_automation_results(
        pid, bound["verdicts"], suite_id=record["ui_suite_id"], scenario_resource=record,
        scenario_bindings=bound["scenario_bindings"], checkpoint=progress.append,
        prior_points=summary["applied_points"])[0]
    assert len(ado.writes) == writes
    assert all("suite_id" in p and "scenario_key" in p for p in summary["applied_points"])
    assert any("uncertain_points" in p for p in progress)
    assert not progress[-1].get("uncertain_points")


def test_no_empty_triage_and_no_numeric_id_invention(monkeypatch):
    ado = ADO(monkeypatch)
    frozen = source(current_rc())
    ok, _, detail, record, _ = create(ado, frozen)
    assert ok, detail
    assert set(record["suites"]) == {"broker", "native", "ui"}
    assert not ado.cases  # verified singleton reference on each flight


@pytest.mark.parametrize("failure", ["case", "crash_after_case"])
def test_uncertain_case_create_never_retries_or_guesses(monkeypatch, failure):
    ado = ADO(monkeypatch)
    ado.failure = failure
    frozen, record = source(evidence()), {}
    if failure.startswith("crash"):
        with pytest.raises(SystemExit):
            create(ado, frozen, record)
    else:
        assert not create(ado, frozen, record)[0]
    creates = sum("/wit/workitems/$" in w[0] for w in ado.writes)
    ok, _, detail, _, _ = create(ado, frozen, record)
    assert not ok and "Uncertain create case:" in detail
    assert sum("/wit/workitems/$" in w[0] for w in ado.writes) == creates


def test_uncertain_membership_recovers_only_from_exact_readback(monkeypatch):
    ado = ADO(monkeypatch)
    ado.failure = "membership"
    frozen, record = source(evidence()), {}
    assert not create(ado, frozen, record)[0]
    writes = len([w for w in ado.writes if "/TestCase?" in w[0]])
    assert create(ado, frozen, record)[0]
    assert len([w for w in ado.writes if "/TestCase?" in w[0]]) == writes + 3


@pytest.mark.parametrize("damage", ["duplicate_title", "category_count", "missing_instrumented", "wrong_build"])
def test_incomplete_all_category_evidence_blocks_before_any_write(monkeypatch, damage):
    ado = ADO(monkeypatch)
    rc = evidence()
    summary = rc["ecs"]["tests"]
    if damage == "duplicate_title":
        summary["suites"][0]["test_results"] *= 2
    elif damage == "category_count":
        summary["categories"]["instrumented"]["failed"] += 1
    elif damage == "missing_instrumented":
        summary["suites"] = [s for s in summary["suites"] if s["category"] != "instrumented"]
    else:
        summary["build_id"] = 999
    assert not S.project(rc)[0]
    assert not ado.writes


def test_binding_preserves_resolved_triage_and_blocks_unreviewed_rc():
    frozen = source(evidence())
    rows = S.representations(frozen["scenario_projection"], frozen["ui_cases"])
    rows = [{**r, "case_id": 900 + i, "suite_id": 200 if r["kind"] == "inventory" else 300}
            for i, r in enumerate(rows)]
    record = {"source": frozen, "scenario_bindings": rows}
    rc = evidence()
    rc["local"]["run_id"] = 333  # invalid attributed evidence cannot be accepted
    assert not S.project(rc, tracked=rows)[0]
    changed = current_rc({PROD: [("test_100_new", "Passed")]}, rc=3)
    ok, projection, detail = S.project(changed)
    assert ok, detail
    with pytest.raises(ValueError, match="RC changed"):
        S.bind_projection(record, projection)
    rc = evidence()
    # A fresh fully reconciled snapshot, preserving every scenario but resolving Lab.
    from tests._mrwp_evidence import snapshot
    suites = {}
    for suite in rc["ecs"]["tests"]["suites"]:
        suites[suite["name"]] = [(t["title"], "Passed" if suite["name"] == "Lab Api Tests" else t["verdict"])
                                  for t in suite["test_results"]]
    rc["ecs"] = snapshot(201, suites)
    ok, projection, detail = S.project(rc, tracked=rows)
    assert ok, detail
    bound = S.bind_projection(record, projection)
    assert next(r for r in bound["scenario_bindings"] if r["category"] == "Lab API")["verdict"] == "Passed"
    assert len(bound["scenario_bindings"]) == len(rows)


def test_manual_outcome_drift_blocks_all_scenario_writes(monkeypatch):
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    sid = record["suites"]["Instrumented Tests"]
    ado.points[pid, sid][0]["outcome"] = "Failed"
    ok, projection, detail = S.project(evidence(), tracked=record["scenario_bindings"])
    assert ok, detail
    bound = S.bind_projection(record, projection)
    writes = len(ado.writes)
    assert not T.fill_ui_automation_results(
        pid, bound["verdicts"], suite_id=record["ui_suite_id"], scenario_resource=record,
        scenario_bindings=bound["scenario_bindings"], checkpoint=lambda _: None)[0]
    assert len(ado.writes) == writes


def test_exact_uncertain_case_receipt_recovery_verifies_provenance(monkeypatch):
    ado = ADO(monkeypatch)
    ado.failure = "crash_after_case"
    frozen, record = source(evidence()), {}
    with pytest.raises(SystemExit):
        create(ado, frozen, record)
    key = next(k for k, v in record["operations"].items() if v["status"] == "creating")
    cid = next(iter(ado.cases))
    writes = len(ado.writes)
    original = ado.cases[cid]["fields"]["System.Title"]
    ado.cases[cid]["fields"]["System.Title"] = "Not the reserved scenario"
    with pytest.raises(ValueError, match="provenance mismatch"):
        S.recover_create(record, key, cid, "Verified create receipt", lambda: None)
    assert record["operations"][key]["status"] == "creating"
    ado.cases[cid]["fields"]["System.Title"] = original
    S.recover_create(record, key, cid, "Verified create receipt", lambda: None)
    assert len(ado.writes) == writes
    assert create(ado, frozen, record)[0]
    assert sum("/wit/workitems/$" in w[0] for w in ado.writes) == len(ado.cases)


@pytest.mark.parametrize("action", ["validate", "recover"])
@pytest.mark.parametrize("damage", [
    None, "relation_host", "relation_project", "relation_id", "relation_query", "relation_path",
    "relation_type", "team_project", "missing_team_project", "owned_host", "owned_project",
    "owned_id", "owned_query", "owned_path", "missing_owned_url", "hyperlink",
])
def test_owned_case_related_project_guid_requires_exact_provider_proof(monkeypatch, action, damage):
    ado = ADO(monkeypatch)
    frozen, record = source(evidence()), {}
    if action == "recover":
        ado.failure = "crash_after_case"
        with pytest.raises(SystemExit):
            create(ado, frozen, record)
        key = next(k for k, value in record["operations"].items() if value["status"] == "creating")
        cid = next(iter(ado.cases))
    else:
        ok, pid, detail, record, _ = create(ado, frozen, record)
        assert ok, detail
        key, operation = next((k, value) for k, value in record["operations"].items()
                              if k.startswith("case:") and any(
                                  p["path"] == "/relations/-"
                                  and p["value"]["rel"] == "System.LinkTypes.Related"
                                  for p in value["payload"]))
        cid = operation["id"]
    project_id = "11111111-2222-3333-4444-555555555555"
    other_project = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    case = ado.cases[cid]
    case["fields"]["System.TeamProject"] = T.PROJECT
    case["url"] = f"{T.ORG}/{project_id}/_apis/wit/workItems/{cid}"
    related = next(r for r in case["relations"] if r["rel"] == "System.LinkTypes.Related")
    related["url"] = related["url"].replace(f"/{T.PROJECT}/", f"/{project_id}/")
    if damage == "relation_host":
        related["url"] = related["url"].replace(T.ORG, "https://otherorg.visualstudio.com")
    elif damage == "relation_project":
        related["url"] = related["url"].replace(project_id, other_project)
    elif damage == "relation_id":
        related["url"] = related["url"].rsplit("/", 1)[0] + "/999999"
    elif damage == "relation_query":
        related["url"] += "?api-version=7.1"
    elif damage == "relation_path":
        related["url"] += "/extra"
    elif damage == "relation_type":
        related["rel"] = "System.LinkTypes.Parent"
    elif damage == "team_project":
        case["fields"]["System.TeamProject"] = "Other"
    elif damage == "missing_team_project":
        del case["fields"]["System.TeamProject"]
    elif damage == "owned_host":
        case["url"] = case["url"].replace(T.ORG, "https://otherorg.visualstudio.com")
    elif damage == "owned_project":
        case["url"] = case["url"].replace(project_id, other_project)
    elif damage == "owned_id":
        case["url"] = case["url"].rsplit("/", 1)[0] + "/999999"
    elif damage == "owned_query":
        case["url"] += "?api-version=7.1"
    elif damage == "owned_path":
        case["url"] = case["url"].replace("/_apis/wit/", "/_apis/not-wit/")
    elif damage == "missing_owned_url":
        del case["url"]
    elif damage == "hyperlink":
        next(r for r in case["relations"] if r["rel"] == "Hyperlink")["url"] += "&changed=true"
    before, writes, reads = deepcopy(record), deepcopy(ado.writes), []
    get = ado.get

    def read(url, timeout):
        reads.append(url)
        if url == f"{T.ORG}/_apis/wit/workitems/100?api-version=7.1":
            return True, {"id": 100, "fields": {"System.WorkItemType": "Test Case",
                                               "System.TeamProject": T.PROJECT},
                          "_links": {"self": {"href":
                              f"{T.ORG}/{project_id}/_apis/wit/workItems/100"}}}, ""
        return get(url, timeout)

    monkeypatch.setattr(P, "_ado_rest_get", read)
    if action == "recover":
        checkpoints = []
        if damage:
            with pytest.raises(ValueError, match="source links mismatch"):
                S.recover_create(record, key, cid, "Owner verified exact provider receipt",
                                 lambda: checkpoints.append(deepcopy(record)))
            assert record == before and not checkpoints
        else:
            S.recover_create(record, key, cid, "Owner verified exact provider receipt",
                             lambda: checkpoints.append(deepcopy(record)))
            assert len(checkpoints) == 1
            assert record["operations"][key]["status"] == "created"
            assert record["operations"][key]["id"] == cid
    else:
        ok, _, detail = B.validate_plan(pid, record["identity"], frozen, resource=record)
        assert ok is (damage is None), detail
        if damage:
            assert "source/original links missing" in detail
            assert f"owned case {cid}" in detail and "original case 100" in detail
        assert record == before
    assert f"{T.ORG}/{T.PROJECT}/_apis/wit/workitems/{cid}?$expand=relations&api-version=7.1" in reads
    assert ado.writes == writes and record["source"] == before["source"]
    assert record["operations"][key]["payload"] == before["operations"][key]["payload"]
    assert record["operations"][key]["sha256"] == before["operations"][key]["sha256"]


@pytest.mark.parametrize("action", ["validate", "recover"])
@pytest.mark.parametrize("damage", [
    None, "wrong_canonical_project", "missing_proof", "wrong_original_id", "wrong_original_type",
    "missing_original_project", "canonical_host", "canonical_id", "canonical_query", "canonical_path",
    "canonical_fragment", "missing_canonical", "invalid_self_with_valid_url", "valid_url_only",
])
def test_original_related_case_cross_project_requires_cached_exact_canonical_proof(
        monkeypatch, action, damage):
    ado = ADO(monkeypatch)
    frozen, record = source(evidence()), {}
    if action == "recover":
        ado.failure = "crash_after_case"
        with pytest.raises(SystemExit):
            create(ado, frozen, record)
        key = next(k for k, value in record["operations"].items() if value["status"] == "creating")
        owned_ids = [next(iter(ado.cases))]
    else:
        ok, pid, detail, record, _ = create(ado, frozen, record)
        assert ok, detail
        owned_ids = [r["case_id"] for r in record["scenario_bindings"]
                     if not r["reuse_case_id"] and r["original_case_id"] == 100]
        assert len(owned_ids) == 3
    engineering = "11111111-2222-3333-4444-555555555555"
    devex = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    unrelated = "99999999-8888-7777-6666-555555555555"
    for cid in owned_ids:
        case = ado.cases[cid]
        case["fields"]["System.TeamProject"] = T.PROJECT
        case["url"] = f"{T.ORG}/{engineering}/_apis/wit/workItems/{cid}"
        related = next(r for r in case["relations"] if r["rel"] == "System.LinkTypes.Related")
        related["url"] = f"{T.ORG}/{devex}/_apis/wit/workItems/100"
    original = {"id": 100, "fields": {"System.WorkItemType": "Test Case", "System.TeamProject": "DevEx"},
                "url": f"{T.ORG}/_apis/wit/workItems/100",
                "_links": {"self": {"href": f"{T.ORG}/{devex}/_apis/wit/workItems/100"}}}
    if damage == "wrong_canonical_project":
        original["_links"]["self"]["href"] = original["_links"]["self"]["href"].replace(devex, unrelated)
    elif damage == "wrong_original_id":
        original["id"] = 101
    elif damage == "wrong_original_type":
        original["fields"]["System.WorkItemType"] = "Bug"
    elif damage == "missing_original_project":
        del original["fields"]["System.TeamProject"]
    elif damage == "canonical_host":
        original["_links"]["self"]["href"] = original["_links"]["self"]["href"].replace(
            T.ORG, "https://otherorg.visualstudio.com")
    elif damage == "canonical_id":
        original["_links"]["self"]["href"] = f"{T.ORG}/{devex}/_apis/wit/workItems/101"
    elif damage == "canonical_query":
        original["_links"]["self"]["href"] += "?api-version=7.1"
    elif damage == "canonical_path":
        original["_links"]["self"]["href"] += "/extra"
    elif damage == "canonical_fragment":
        original["_links"]["self"]["href"] += "#other"
    elif damage == "missing_canonical":
        del original["_links"]
    elif damage == "invalid_self_with_valid_url":
        original["url"] = original["_links"]["self"]["href"]
        original["_links"]["self"]["href"] += "/extra"
    elif damage == "valid_url_only":
        original["url"] = original["_links"]["self"]["href"]
        del original["_links"]
    before, writes, original_reads = deepcopy(record), deepcopy(ado.writes), []
    get = ado.get

    def read(url, timeout):
        if url == f"{T.ORG}/_apis/wit/workitems/100?api-version=7.1":
            original_reads.append(url)
            return (False, None, "Unavailable") if damage == "missing_proof" else (True, deepcopy(original), "")
        return get(url, timeout)

    monkeypatch.setattr(P, "_ado_rest_get", read)
    accepted = damage in (None, "valid_url_only")
    if action == "recover":
        checkpoints = []
        if accepted:
            S.recover_create(record, key, owned_ids[0], "Owner verified exact provider receipt",
                             lambda: checkpoints.append(deepcopy(record)))
            assert len(checkpoints) == 1
            assert record["operations"][key]["status"] == "created"
        else:
            with pytest.raises(ValueError, match="source links mismatch"):
                S.recover_create(record, key, owned_ids[0], "Owner verified exact provider receipt",
                                 lambda: checkpoints.append(deepcopy(record)))
            assert record == before and not checkpoints
    else:
        ok, _, detail = B.validate_plan(pid, record["identity"], frozen, resource=record)
        assert ok is accepted, detail
        if not accepted:
            assert f"owned case {owned_ids[0]}" in detail and "original case 100" in detail
        assert record == before
    assert len(original_reads) == 1
    assert ado.writes == writes and record["source"] == before["source"]
    assert all(value["payload"] == before["operations"][key]["payload"]
               and value["sha256"] == before["operations"][key]["sha256"]
               for key, value in record["operations"].items() if "payload" in value)


@pytest.mark.parametrize("kind", ["plan", "suite"])
def test_exact_uncertain_plan_and_suite_receipts_resume_without_duplicate_creates(monkeypatch, kind):
    ado = ADO(monkeypatch)
    ado.failure = "crash_after_" + kind
    frozen, record = source(evidence()), {}
    with pytest.raises(SystemExit):
        create(ado, frozen, record)
    key = next(k for k, v in record["operations"].items() if v["status"] == "creating")
    rid = next(iter(ado.plans)) if kind == "plan" else max(sid for _, sid in ado.suites)
    writes = len(ado.writes)
    S.recover_create(record, key, rid, "Owner verified exact provider receipt", lambda: None)
    assert len(ado.writes) == writes
    ok, _, detail, _, _ = create(ado, frozen, record)
    assert ok, detail
    assert len(ado.plans) == 1
    assert len(ado.suites) == 7  # root, originals, parent and two actual categories


def test_existing_broker_command_persists_exact_create_recovery_and_reports_success(monkeypatch, tmp_path, capsys):
    from orchestrator import cli, mocks
    from orchestrator.state import ReleaseState
    from tests._harness import _active_step
    ado = ADO(monkeypatch)
    ado.failure = "crash_after_case"
    frozen, record = source(evidence()), {}
    with pytest.raises(SystemExit):
        create(ado, frozen, record)
    key = next(k for k, v in record["operations"].items() if v["status"] == "creating")
    rid = next(iter(ado.cases))
    st = ReleaseState(release_id="2000-01", ccd="2000-01-12", owner_email="owner@example.test")
    _active_step(st, "bug_bash", "clone_plans_broker")
    st.resources[B.RESOURCE] = record
    path = tmp_path / st.release_id / "release-state.json"
    st.save(str(path))
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    writes = len(ado.writes)
    assert cli.main(["--runs-root", str(tmp_path), "broker-plan", "--release", st.release_id,
                     "--recover-create", key, "--resource-id", str(rid),
                     "--reason", "Owner verified exact case receipt"]) == 0
    assert json.loads(capsys.readouterr().out)["provider_writes"] is False
    assert len(ado.writes) == writes
    saved = ReleaseState.load(str(path)).resources[B.RESOURCE]["operations"][key]
    assert saved["status"] == "created" and saved["id"] == rid and saved["recovery"]["reason"]


@pytest.mark.parametrize("damage", ["plan_receipt", "suite_receipt", "triage_parent"])
def test_target_validation_rejects_receipt_drift_or_unreviewed_parent_cases(monkeypatch, damage):
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    if damage == "plan_receipt":
        record["operations"]["plan"]["sha256"] = "changed"
    elif damage == "suite_receipt":
        record["operations"]["suite:ui"]["id"] += 1
    else:
        ado.points[pid, record["suites"]["triage"]] = [{
            "id": 999, "testCase": {"id": 999}, "configuration": {"id": 293}, "outcome": "Failed"}]
    writes = len(ado.writes)
    ok, _, detail = B.validate_plan(pid, record["identity"], frozen, resource=record)
    assert not ok and ("receipt" in detail or "membership" in detail)
    assert len(ado.writes) == writes


@pytest.mark.parametrize("suite,category", [
    ("common_UnitTests", "Unit Tests"),
    ("AADAuthenticator_InstrumentedTests", "Instrumented Tests"),
    ("Lab Api Tests", "Lab API"),
    ("Native Auth E2E Tests", "Native Auth E2E"),
    ("Unrecognized UI Automation (API 35)", "Other UI Automation")])
def test_only_actual_additional_failure_categories_create_nonempty_children(monkeypatch, suite, category):
    ado = ADO(monkeypatch)
    rc = current_rc({PROD: [("test_100_Broker", "Failed")], suite: [("failure", "Failed")]})
    frozen = source(rc)
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    assert set(record["suites"]) == {"broker", "native", "ui", "triage", category}
    assert len(ado.points[pid, record["suites"][category]]) == 1
    assert not ado.points.get((pid, record["suites"]["triage"]))


@pytest.mark.parametrize("path,body,content_type", [
    ("/_apis/wit/workitems/$Test%20Case?api-version=7.1",
     [{"op": "add", "path": "/fields/System.Title", "value": "Exact source"}], "application/json-patch+json"),
    ("/_apis/testplan/Plans/1/Suites/2/TestCase?api-version=7.1",
     [{"workItem": {"id": 3}}], "application/json")])
def test_only_work_item_creation_uses_json_patch_content_type(monkeypatch, path, body, content_type):
    from io import BytesIO
    from types import SimpleNamespace
    import urllib.request
    from tools.pipelines import _rest
    monkeypatch.setattr(_rest.shutil, "which", lambda _: "fake-az")
    monkeypatch.setattr(_rest.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout="fake-token"))
    observed = []

    def send(request, **kwargs):
        observed.append(request)
        return BytesIO(b'{"id": 3}')

    monkeypatch.setattr(urllib.request, "urlopen", send)
    assert _rest._ado_rest_send("https://example.test/project" + path, "POST", body, 30)[0]
    assert observed[0].get_header("Content-type") == content_type
    assert json.loads(observed[0].data) == body


def scenario_state(ado, frozen, *, rc=None):
    from orchestrator.state import ReleaseState, StepState
    from steps.bug_bash.clone_plans_broker import plan_binding
    from tests._auth_evidence import auth_snapshot
    from tests._ui_results import checkpoint_memory
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    st = ReleaseState(release_id="2000-01", owner_email="owner@example.test")
    rc = deepcopy(rc if rc is not None else evidence())
    rc["auth"] = auth_snapshot(rc=rc["rc"])
    st.pipeline_runs = {"rcs": [rc]}
    st.resources["broker_test_plan"] = record
    st.set_step("bug_bash", "clone_plans_broker", StepState(
        data=plan_binding({}, record, pid, record["identity"]["name"]).values))
    st.set_step("bug_bash", "clone_plans_auth", StepState(data={"suite_id": 8000}))
    return st, checkpoint_memory(st)


def distribution_points(st, ado):
    from tests._context import context
    from steps.bug_bash import distribute_tests as D, ui_results
    record = st.resources["broker_test_plan"]
    cfg = {"broker": {"suite_name": T.BROKER_MANUAL_SUITE_NAME},
           "authenticator": {"triage_tags": ["Blocked"]}}
    ctx = context(st)
    triage = D._owner_triage(ctx, [], cfg)
    manual = {str(cid) for cid in record["source"]["broker_cases"]}
    selected = sorted({*("B:" + cid for cid in manual), *triage})
    writes = len(ado.writes)
    groups = D._point_sets(ctx, selected, cfg, manual, triage)
    assert len(ado.writes) == writes
    expected = ui_results.completed_result(ctx)["broker"]["applied_points"]
    observed = {(int(g["suite_id"]), int(p["id"]), int(p["case_id"]), int(p["config_id"]))
                for g in groups for p in g["points"] if int(g["suite_id"]) != record["suites"]["broker"]}
    assert observed == {(p["suite_id"], p["point_id"], p["case_id"], p["config_id"])
                        for p in expected if p["outcome"] == "Failed"}
    assert all(g["prefix"] == "B" and g["plan_id"] == record["plan_id"] for g in groups)
    assert {p["case_id"] for g in groups if int(g["suite_id"]) == record["suites"]["broker"]
            for p in g["points"]} == manual
    return groups


@pytest.mark.parametrize("all_categories", [False, True])
def test_production_fill_publication_distribution_reads_each_failure_suite(monkeypatch, all_categories):
    from tests._context import invoke
    from tests._ui_results import auth_fill
    from tests._mrwp_evidence import snapshot
    from tools import distribution as D
    from steps.bug_bash import ui_test_status as U
    rc = evidence()
    if all_categories:
        suites = {s["name"]: [(t["title"], t["verdict"]) for t in s["test_results"]]
                  for s in rc["ecs"]["tests"]["suites"]}
        suites.update({name: [("additional_failure", "Failed")] for name in (
            "common_UnitTests", "Native Auth E2E Tests", "Unrecognized UI Automation (API 35)")})
        rc["ecs"] = snapshot(201, suites)
    ado = ADO(monkeypatch)
    st, _ = scenario_state(ado, source(rc), rc=rc)
    monkeypatch.setattr(D, "validate_release_targets", lambda *a, **kw: None)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    assignments = []
    monkeypatch.setattr(D, "set_point_testers",
                        lambda pid, sid, points, upn, **kw: (assignments.append((sid, points)) is None, ""))
    outcome = invoke(U.build, st)
    assert outcome.kind == "done", outcome
    groups = distribution_points(st, ado)
    expected_suites = {"ui", "Lab API", "Instrumented Tests"}
    if all_categories:
        expected_suites.update(S.CATEGORIES)
    record = st.resources["broker_test_plan"]
    assert {int(g["suite_id"]) for g in groups} == {
        record["suites"][key] for key in expected_suites | {"broker"}}
    assert {sid for sid, _ in assignments} == {record["suites"][key] for key in expected_suites}


@pytest.mark.parametrize("damage", [
    "receipt_suite", "manifest_suite", "duplicate_receipt", "scenario_key",
    "live_plan", "live_suite", "live_case", "live_config", "live_missing", "live_duplicate",
    "live_swapped_suites"])
def test_distribution_rejects_failure_receipt_or_live_target_remapping(monkeypatch, damage):
    from tests._context import invoke
    from tests._ui_results import auth_fill
    from tools import distribution as D
    from steps.bug_bash import ui_test_status as U
    ado = ADO(monkeypatch)
    st, _ = scenario_state(ado, source(evidence()))
    monkeypatch.setattr(D, "validate_release_targets", lambda *a, **kw: None)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    monkeypatch.setattr(D, "set_point_testers", lambda *a, **kw: (True, ""))
    assert invoke(U.build, st).kind == "done"
    record = st.resources["broker_test_plan"]
    step = st.get_step("bug_bash", U.ID)
    result = step.data["result"]["broker"]
    sid, ui = record["suites"]["Instrumented Tests"], record["suites"]["ui"]
    receipt = next(p for p in result["applied_points"] if p["suite_id"] == sid)
    if damage in ("receipt_suite", "manifest_suite"):
        receipt["suite_id"] = ui
        if damage == "manifest_suite":
            next(r for r in result["scenario_bindings"]
                 if r["key"] == receipt["scenario_key"])["suite_id"] = ui
            result["suite_targets"].remove(sid)
    elif damage == "duplicate_receipt":
        result["applied_points"].append(deepcopy(receipt))
    elif damage == "scenario_key":
        receipt["scenario_key"] = "unowned-source"
    elif damage in ("live_plan", "live_suite"):
        original = D.read_point_testers
        def read(pid, suite, ids):
            ok, rows, detail = original(pid, suite, ids)
            if int(suite) == sid:
                for row in rows:
                    row["plan_id" if damage == "live_plan" else "suite_id"] += 1
            return ok, rows, detail
        monkeypatch.setattr(D, "read_point_testers", read)
    else:
        points = ado.points[record["plan_id"], sid]
        if damage == "live_case":
            points[0]["testCase"]["id"] += 10000
        elif damage == "live_config":
            points[0]["configuration"]["id"] += 1
        elif damage == "live_missing":
            points.clear()
        elif damage == "live_duplicate":
            points.append(deepcopy(points[0]))
        else:
            other = next(p for p in ado.points[record["plan_id"], ui] if p["outcome"] == "Failed")
            points[0]["id"], other["id"] = other["id"], points[0]["id"]
    st.set_step("bug_bash", U.ID, step)
    writes = len(ado.writes)
    with pytest.raises(ValueError):
        distribution_points(st, ado)
    assert len(ado.writes) == writes


@pytest.mark.parametrize("slot", ["ecs", "local"])
def test_refreshed_same_rc_build_blocks_production_before_outcome_writes(monkeypatch, slot):
    from tests._context import invoke
    from tests._ui_results import auth_fill
    from tests._mrwp_evidence import snapshot
    from tools import distribution as D
    from steps.bug_bash import ui_test_status as U
    ado = ADO(monkeypatch)
    st, _ = scenario_state(ado, source(evidence()))
    monkeypatch.setattr(D, "validate_release_targets", lambda *a, **kw: None)
    auth_writes = []
    def fill_auth(*args, **kwargs):
        auth_writes.append(args)
        return auth_fill(*args, **kwargs)
    monkeypatch.setattr(T, "fill_auth_ui_results", fill_auth)
    monkeypatch.setattr(D, "set_point_testers", lambda *a, **kw: (True, ""))
    assert invoke(U.build, st).kind == "done"
    previous = deepcopy(st.get_step("bug_bash", U.ID).data["result"])
    resource = deepcopy(st.resources["broker_test_plan"])
    rc = st.pipeline_runs["rcs"][-1]
    suites = {s["name"]: [(t["title"], "Passed") for t in s["test_results"]]
              for s in rc[slot]["tests"]["suites"]}
    rc[slot] = snapshot(303, suites)
    assert S.project(rc, tracked=resource["scenario_bindings"])[0]
    writes, auth_count = len(ado.writes), len(auth_writes)
    outcome = invoke(U.build, st)
    assert outcome.kind == "blocked" and "MRWP build changed" in outcome.reason
    assert len(ado.writes) == writes and len(auth_writes) == auth_count
    assert st.resources["broker_test_plan"] == resource
    assert st.get_step("bug_bash", U.ID).data["result"] == previous


@pytest.mark.parametrize("field,value", [
    ("build_id", 303), ("flight", "Local"), ("rc", 3),
    ("category", "Other UI Automation"), ("reuse_case_id", 100)])
def test_direct_fill_rejects_unpublishable_creation_identity_before_writes(monkeypatch, field, value):
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    ok, projection, detail = S.project(evidence(), tracked=record["scenario_bindings"])
    assert ok, detail
    bound = S.bind_projection(record, projection)
    bound["scenario_bindings"][0][field] = value
    writes = len(ado.writes)
    ok, _, detail = T.fill_ui_automation_results(
        pid, bound["verdicts"], suite_id=record["ui_suite_id"], scenario_resource=record,
        scenario_bindings=bound["scenario_bindings"], checkpoint=lambda _: None)
    assert not ok and "Scenario fill provider identity changed" in detail
    assert len(ado.writes) == writes


def test_production_ui_publication_assignment_progress_and_exclusion(monkeypatch):
    from tests._context import invoke, context
    from tests._ui_results import auth_fill
    from tools import distribution as D, bugbash
    from steps.bug_bash import ui_results, ui_test_status as U
    ado = ADO(monkeypatch)
    st, checkpoints = scenario_state(ado, source(evidence()))
    record = st.resources["broker_test_plan"]
    monkeypatch.setattr(D, "validate_release_targets", lambda *a, **kw: None)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    assignments = []
    monkeypatch.setattr(D, "set_point_testers",
                        lambda pid, sid, points, upn, **kw: (assignments.append((sid, points)) is None, ""))
    outcome = invoke(U.build, st)
    assert outcome.kind == "done", outcome
    published = ui_results.completed_result(context(st))
    assert len(published["broker"]["applied_points"]) == 8
    assert len(published["investigations"]["broker"]) == 4
    assert {sid for sid, _ in assignments} == {
        record["suites"]["ui"], record["suites"]["Lab API"], record["suites"]["Instrumented Tests"]}
    note = st.get_step("bug_bash", "ui_failures").note
    assert "Instrumented Tests" in note and "original case 100" in note and "Source " in note
    assert len(published["broker"]["automated_case_ids"]) == len({r["case_id"] for r in record["scenario_bindings"]})
    partials = [c["steps"]["bug_bash.ui_test_status"]["data"].get("summary") for c in checkpoints
                if "bug_bash.ui_test_status" in c["steps"]]
    assert any(p and p.get("uncertain_points") for p in partials)
    assert U._prior_applied_points(st.get_step("bug_bash", U.ID).data, published["binding"])["broker"]

    def progress_points(pid, sid, timeout):
        return True, [{"point_id": str(p["id"]), "case_id": str(p["testCase"]["id"]),
                       "config_id": str(p["configuration"]["id"]), "outcome": p["outcome"],
                       "tester_upn": st.owner_email, "name": "Scenario"}
                      for p in ado.points[pid, sid]], ""
    monkeypatch.setattr(bugbash, "_points_of_suite", progress_points)
    ok, rows, detail = bugbash._broker_ui_triage_points(record["plan_id"], published["broker"], 30)
    assert ok, detail
    assert len(rows) == 4
    # Only the original manual subtree is a distribution source, never category cases.
    assert {p["testCase"]["id"] for p in ado.points[record["plan_id"], record["suites"]["broker"]]} == {
        701, 702}
    assert invoke(U.build, st).kind == "done"  # production service adapter resumes acknowledged batches
    ui_results.completed_result(context(st))


def test_completed_scenario_receipt_cannot_omit_points(monkeypatch):
    from steps.bug_bash.ui_results import applied_record
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    ok, pid, detail, record, _ = create(ado, frozen)
    assert ok, detail
    _, projection, _ = S.project(evidence(), tracked=record["scenario_bindings"])
    bound = S.bind_projection(record, projection)
    ok, summary, detail = T.fill_ui_automation_results(
        pid, bound["verdicts"], suite_id=record["ui_suite_id"], scenario_resource=record,
        scenario_bindings=bound["scenario_bindings"], checkpoint=lambda _: None)
    assert ok, detail
    summary["applied_points"].pop()
    ids = [r["case_id"] for r in record["scenario_bindings"]]
    assert applied_record(summary, ids, "broker", summary["target"], complete=False)
    with pytest.raises(ValueError, match="omitted source"):
        applied_record(summary, ids, "broker", summary["target"])


def test_isolated_preview_execution_is_fenced_and_never_saves_original_state(monkeypatch, tmp_path):
    from argparse import Namespace
    from orchestrator.commands import broker_scenario_plan as command
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    st, _ = scenario_state(ado, frozen)
    snapshot = tmp_path / "actual" / "release-state.json"
    st.save(str(snapshot))
    original = snapshot.read_bytes()
    monkeypatch.setattr(B, "prepare_source", lambda **kw: (True, frozen, ""))
    args = Namespace(snapshot=str(snapshot), isolated_root=str(tmp_path / "isolated"),
                     name="TEST ONLY - exact collected scenarios", execute=False,
                     confirm_source_sha256=None, recover_create=None, resource_id=None, reason=None)
    before = len(ado.writes)
    preview = command.run(args)["preview"]
    assert len(ado.writes) == before and not (tmp_path / "isolated").exists()
    args.execute = True
    with pytest.raises(ValueError, match="reviewed preview"):
        command.run(args)
    assert len(ado.writes) == before
    args.confirm_source_sha256 = preview["source_sha256"]
    result = command.run(args)
    assert result["plan_id"] != st.resources["broker_test_plan"]["plan_id"]
    assert result["applied_points"] == 8 and result["fill_status"] == "complete"
    assert snapshot.read_bytes() == original
    writes = len(ado.writes)
    assert command.run(args)["plan_id"] == result["plan_id"]
    assert len(ado.writes) == writes
    args.isolated_root = str(snapshot.parent / "unsafe")
    with pytest.raises(ValueError, match="outside"):
        command.run(args)


def isolated_receipt_execution(monkeypatch, tmp_path):
    from argparse import Namespace
    from orchestrator.commands import broker_scenario_plan as command
    from orchestrator.state import ReleaseState
    ado = ADO(monkeypatch)
    frozen = source(evidence())
    st = ReleaseState(release_id="2000-01", owner_email="owner@example.test")
    st.pipeline_runs = {"rcs": [evidence()]}
    snapshot = tmp_path / "source" / "release-state.json"
    st.save(str(snapshot))
    monkeypatch.setattr(B, "prepare_source", lambda **kw: (True, frozen, ""))
    args = Namespace(snapshot=str(snapshot), isolated_root=str(tmp_path / "isolated"),
                     name="TEST ONLY - atomic receipt", execute=True,
                     confirm_source_sha256=S.digest(frozen),
                     recover_create=None, resource_id=None, reason=None)
    instance = st.release_id + "-scenario-" + S.digest(args.name)[:16]
    path = tmp_path / "isolated" / instance / "broker-scenario-resource.json"
    return command, ado, args, snapshot, path


def windows_replace_error(pending, path, winerror):
    exc = PermissionError(13, "Access denied", str(pending))
    exc.filename2 = str(path)
    if winerror is not None:
        exc.winerror = winerror
    return exc


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_isolated_receipt_retries_only_replace_with_same_pending_bytes(monkeypatch, tmp_path, winerror):
    command, ado, args, snapshot, path = isolated_receipt_execution(monkeypatch, tmp_path)
    original = snapshot.read_bytes()
    replace, attempts, delays = os.replace, [], []
    monkeypatch.setattr(command, "_WINDOWS", True)
    monkeypatch.setattr(command.time, "sleep",
                        lambda delay: delays.append(delay) if len(attempts) in (1, 2) else None)

    def sharing_failure(pending, target):
        saved = json.loads(pending.read_bytes())
        if saved.get("operations", {}).get("plan", {}).get("status") == "created" and len(attempts) < 3:
            attempts.append((pending.read_bytes(), target.read_bytes(), len(ado.writes)))
            assert len(set(attempts)) == 1
            if len(attempts) < 3:
                raise windows_replace_error(pending, target, winerror)
        replace(pending, target)

    monkeypatch.setattr(command.os, "replace", sharing_failure)
    result = command.run(args)
    assert result["fill_status"] == "complete" and len(attempts) == 3
    assert delays == [0.05, 0.1]
    assert len(ado.plans) == 1
    assert sum("/testplan/plans?" in url and method == "POST"
               for url, method, _ in ado.writes) == 1
    assert sum("/wit/workitems/$" in url for url, _, _ in ado.writes) == len(ado.cases)
    assert json.loads(path.read_bytes())["fill_status"] == "complete"
    assert not path.with_suffix(".pending").exists()
    assert snapshot.read_bytes() == original


@pytest.mark.parametrize("kind", ["plan", "suite", "case"])
def test_isolated_receipt_permanent_replace_failure_preserves_provider_ids_and_blocks_replay(
        monkeypatch, tmp_path, capsys, kind):
    command, ado, args, snapshot, path = isolated_receipt_execution(monkeypatch, tmp_path)
    original = snapshot.read_bytes()
    replace, failed, delays = os.replace, [], []
    monkeypatch.setattr(command, "_WINDOWS", True)
    monkeypatch.setattr(command.time, "sleep", lambda delay: delays.append(delay) if failed else None)

    def denied_replace(pending, target):
        saved = json.loads(pending.read_bytes())
        operations = saved.get("operations", {})
        key = next((key for key, value in operations.items()
                    if (key == kind or key.startswith(kind + ":")) and value["status"] == "created"), None)
        if key:
            failed.append((pending.read_bytes(), target.read_bytes(), len(ado.writes), key))
            assert len(set(failed)) == 1
            raise windows_replace_error(pending, target, 5)
        replace(pending, target)

    monkeypatch.setattr(command.os, "replace", denied_replace)
    argv = ["--snapshot", args.snapshot, "--isolated-root", args.isolated_root,
            "--name", args.name, "--execute", "--confirm-source-sha256", args.confirm_source_sha256]
    assert command.main(argv) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["stage"] == "receipt_replace" and "Access denied" in error["error"]
    assert "status" not in error and error["workflow_adoption"] is False
    pending = path.with_suffix(".pending")
    assert error["receipt"] == str(path) and error["pending_receipt"] == str(pending)
    assert len(failed) == 5 and delays == list(command._REPLACE_DELAYS)
    assert path.read_bytes() == failed[0][1] and pending.read_bytes() == failed[0][0]
    saved = json.loads(pending.read_bytes())
    operation = saved["operations"][failed[0][3]]
    rid = operation["id"]
    assert rid in (ado.plans if kind == "plan" else ado.cases if kind == "case"
                   else {sid for _, sid in ado.suites})
    writes = deepcopy(ado.writes)
    assert command.main(argv) == 1
    assert json.loads(capsys.readouterr().out)["stage"] == "receipt_pending"
    assert ado.writes == writes
    assert path.read_bytes() == failed[0][1] and pending.read_bytes() == failed[0][0]
    assert snapshot.read_bytes() == original


@pytest.mark.parametrize("windows,winerror", [(False, 32), (True, None), (True, 2), (True, 112)])
def test_isolated_receipt_nonsharing_errors_fail_immediately(monkeypatch, windows, winerror):
    from orchestrator.commands import broker_scenario_plan as command
    error, calls, delays = windows_replace_error("pending", "receipt", winerror), [], []
    monkeypatch.setattr(command, "_WINDOWS", windows)
    monkeypatch.setattr(command.time, "sleep", delays.append)

    def fail(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(command.os, "replace", fail)
    with pytest.raises(OSError) as raised:
        command._replace_receipt("pending", "receipt")
    assert raised.value is error and len(calls) == 1 and not delays


def test_isolated_receipt_exhaustion_raises_last_original_permission_error(monkeypatch):
    from orchestrator.commands import broker_scenario_plan as command
    errors, delays = [], []
    monkeypatch.setattr(command, "_WINDOWS", True)
    monkeypatch.setattr(command.time, "sleep", delays.append)

    def fail(pending, path):
        errors.append(windows_replace_error(pending, path, 5))
        raise errors[-1]

    monkeypatch.setattr(command.os, "replace", fail)
    with pytest.raises(PermissionError) as raised:
        command._replace_receipt("pending", "receipt")
    assert len(errors) == 5 and raised.value is errors[-1]
    assert delays == list(command._REPLACE_DELAYS)


def test_collected_snapshot_offline_builder(monkeypatch, capsys):
    """Opt-in one-off proof against a read-only collected release-state snapshot."""
    path = os.environ.get("RELEASE_AGENT_MRWP_STATE")
    if not path:
        pytest.skip("Set RELEASE_AGENT_MRWP_STATE for the collected-data offline proof")
    raw = open(path, "rb").read()
    state = json.loads(raw)
    rc = state["pipeline_runs"]["rcs"][-1]
    historical = state["resources"]["broker_test_plan"]["source"]
    frozen = source(rc, historical["ui_cases"])
    for key in ("root_configs", "broker_cases", "broker_configs", "native_query"):
        frozen[key] = deepcopy(historical[key])
    ado = ADO(monkeypatch)
    from tests._context import invoke
    from tests._ui_results import auth_fill
    from tools import distribution as D
    from steps.bug_bash import ui_test_status as U
    st, _ = scenario_state(ado, frozen, rc=rc)
    record, pid = st.resources["broker_test_plan"], st.resources["broker_test_plan"]["plan_id"]
    monkeypatch.setattr(D, "validate_release_targets", lambda *a, **kw: None)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    monkeypatch.setattr(D, "set_point_testers", lambda *a, **kw: (True, ""))
    outcome = invoke(U.build, st)
    assert outcome.kind == "done", outcome
    summary = st.get_step("bug_bash", U.ID).data["summary"]
    groups = distribution_points(st, ado)
    failed_points = sum(len(g["points"]) for g in groups if int(g["suite_id"]) != record["suites"]["broker"])
    assert failed_points == sum(p["outcome"] == "Failed" for p in summary["applied_points"])
    rows = frozen["scenario_projection"]["inventory"]
    assert len(ado.points[pid, record["ui_suite_id"]]) == len(rows)
    assert len({r["key"] for r in rows}) == len(rows)
    assert sum("concurrent" in r["title"].lower() and r["verdict"] == "Failed" for r in rows) >= 1
    assert hashlib.sha256(open(path, "rb").read()).digest() == hashlib.sha256(raw).digest()
    with capsys.disabled():
        print("\nCOLLECTED DATA OFFLINE PROOF " + json.dumps({
            **S.preview(frozen), "applied_points": len(summary["applied_points"]),
            "distribution_failure_points": failed_points,
            "owned_case_ids_are_fake": True, "real_state_unchanged": True}, sort_keys=True))
