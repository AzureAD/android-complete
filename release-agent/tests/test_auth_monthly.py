"""Offline provider contracts for explicitly owned Monthly failure investigations."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from tests._auth_evidence import auth_snapshot
from tests.test_auth_categories import state, Suites
from tests._context import context, invoke
from tools import auth_monthly as M, auth_partition as A, pipelines as P, testplans as T
from steps.bug_bash import clone_plans_auth as C, ui_test_status as U
from steps.bug_bash.ui_results import current_binding, completed_result


def monthly_state(rows=None):
    st = state()
    st.pipeline_runs["rcs"][-1]["auth"] = auth_snapshot(rc=st.pipeline_runs["rcs"][-1]["rc"], rows={
        P.AUTH_UI_SUITES[0]: [("test_2916347_android", "Failed"), ("test_2916524_android", "Passed")],
        P.MONTHLY_REPORT_ONLY: [(r["testCaseTitle"], r["outcome"]) for r in (rows if rows is not None else [
            {"testCaseTitle": "monthly one", "outcome": "Failed"},
            {"testCaseTitle": "monthly two", "outcome": "Failed"}])]})
    return st


class Provider(Suites):
    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.cases, self.points, self.writes = {}, {}, []
        self.fail_read = None

    def get_case(self, url, *args):
        if "/_apis/identities?" in url:
            return True, {"value": [{"id": "owner-guid",
                                    "properties": {"Account": {"$value": "owner@example.test"}}}]}, ""
        if re.search(r"/Plans/900\?", url, re.I):
            return True, {"id": 900, "name": T.broker_plan_name("2026-08"), "rootSuite": {"id": 899},
                          "areaPath": T.BROKER_AREA_PATH, "iteration": T.BROKER_ITERATION}, ""
        if "/workitems/" in url:
            cid = int(url.split("/workitems/")[1].split("?")[0])
            if self.fail_read == cid:
                return False, None, "readback interrupted"
            return True, deepcopy(self.cases[cid]), ""
        raise AssertionError(url)

    def get(self, plan, sid, *args, **kwargs):
        if plan == 900:
            return True, {"id": sid, "name": T.BROKER_UI_SUITE_NAME,
                          "suiteType": "staticTestSuite", "parentSuite": {"id": 899}}, ""
        ok, row, detail = super().get(plan, sid, *args, **kwargs)
        if ok and row is None:
            return False, None, "Suite not found"
        if ok:
            row["defaultConfigurations"] = [{"id": 84}]
        return ok, row, detail

    def get_all(self, url, *args):
        modern = re.search(r"/Suites/(\d+)/TestPoint", url, re.I)
        if modern:
            return True, [{"id": p["id"], "testCaseReference": p["testCase"],
                           "configuration": p["configuration"], "results": {"outcome": p["outcome"]},
                           "tester": p.get("assignedTo")} for p in self.points.get(int(modern[1]), [])], ""
        point = re.search(r"/Suites/(\d+)/points", url, re.I)
        if point:
            return True, deepcopy(self.points.get(int(point[1]), [])), ""
        if re.search(r"/Plans/900/suites\?", url, re.I):
            return True, [{"id": 901, "name": T.BROKER_UI_SUITE_NAME,
                           "suiteType": "staticTestSuite", "parentSuite": {"id": 899}}], ""
        return super().all(url, *args)

    def send(self, url, method, body, timeout):
        self.writes.append((url, method, deepcopy(body)))
        if "/workitems/$Test%20Case?" in url:
            cid = 4_000_000 + len(self.cases)
            self.cases[cid] = {"id": cid, "fields": {
                **{p["path"].split("/")[-1]: p["value"] for p in body if "/fields/" in p["path"]},
                "System.WorkItemType": "Test Case", "System.TeamProject": T.PROJECT},
                "relations": [p["value"] for p in body if p["path"] == "/relations/-"]}
            return True, {"id": cid}, ""
        membership = re.search(r"/Suites/(\d+)/TestCase\?", url)
        if membership:
            sid = int(membership[1])
            self.points[sid] = [{"id": 100 + len(self.points.get(sid, [])) + i,
                                 "testCase": {"id": row["workItem"]["id"]},
                                 "configuration": {"id": cfg["configurationId"]},
                                 "outcome": "Unspecified"}
                                for i, row in enumerate(body) for cfg in row["pointAssignments"]]
            return True, {}, ""
        point = re.search(r"/Suites/(\d+)/points/([\d,]+)", url, re.I)
        if point:
            for p in self.points[int(point[1])]:
                if str(p["id"]) in point[2].split(","):
                    if "outcome" in body:
                        p["outcome"] = body["outcome"]
                    if "tester" in body:
                        p["assignedTo"] = body["tester"]
            return True, {}, ""
        return super().send(url, method, body, timeout)


@pytest.fixture
def provider(monkeypatch):
    p = Provider(monkeypatch)
    monkeypatch.setattr(P, "_ado_rest_get", p.get_case)
    monkeypatch.setattr(P, "_ado_rest_get_all", p.get_all)
    monkeypatch.setattr(P, "_ado_rest_send", p.send)
    return p


def cloned(provider, rows=None):
    st = monthly_state(rows)
    result = invoke(C.build, st, layout=M.LAYOUT)
    assert result.kind == "done", result
    data = st.get_step("bug_bash", C.ID).data
    st.steps["bug_bash." + C.ID]["status"] = "done"
    return st, data, data["monthly"]


def test_real_engine_accepts_reviewed_fresh_monthly_layout(provider, tmp_path):
    from tests._context import fresh_orchestrator
    from orchestrator.effects import execution_input_is_valid

    config = tmp_path / "phases.yaml"
    config.write_text(
        "version: 1\nphases:\n"
        "  - id: bug_bash\n    name: Fresh Monthly test\n    steps:\n"
        "      - id: clone_plans_auth\n        name: Create categories\n"
        "        kind: auto\n        owner: agent\n        effect_mode: transactional\n"
        "        effect_recovery: frozen\n        effect_retry: true\n",
        encoding="utf-8")
    (tmp_path / "readiness.yaml").write_text("items: []\n", encoding="utf-8")
    st = monthly_state()
    st.steps = {}
    checkpoints = []
    st._checkpoint = lambda: checkpoints.append(deepcopy(st.steps))
    name = "TEST ONLY - Fresh Monthly real engine"
    orch = fresh_orchestrator(str(config), st, mocks={
        "bug_bash.clone_plans_auth": {"name": name, "layout": M.LAYOUT}})
    orch.run_until_gate()
    step = st.get_step("bug_bash", C.ID)
    assert step.status == "done", step.note
    assert step.data["layout"] == M.LAYOUT
    assert step.data["suite_name"] == name
    assert len(provider.posts) == 4
    assert len(provider.cases) == 2
    assert len(step.data["monthly"]["bindings"]) == 2
    assert checkpoints
    executions = [c["bug_bash.clone_plans_auth"]["execution"] for c in checkpoints
                  if c.get("bug_bash.clone_plans_auth", {}).get("execution")]
    assert executions and all(execution_input_is_valid(C.ID, e) for e in executions)
    writes = deepcopy(provider.writes)
    orch.run_until_gate()
    assert provider.writes == writes


@pytest.mark.parametrize("value", ["unknown-layout", None, {}, []])
def test_invalid_engine_layout_input_never_writes(provider, value):
    from steps.lib import mockctx

    with mockctx.active({"layout": value}):
        result = invoke(C.build, monthly_state())
    assert result.kind == "blocked" and "layout" in result.reason
    assert not provider.writes


def test_engine_layout_input_cannot_migrate_frozen_v1_or_override_explicit_parameter(provider):
    from steps.lib import mockctx
    from tests._context import context
    from orchestrator.state import StepState

    st = monthly_state()
    prepared = C.prepare_effect(context(st))
    st.set_step("bug_bash", C.ID, StepState(execution={"effect_input": prepared}))
    with mockctx.active({"layout": M.LAYOUT}):
        result = C.prepare_effect(context(st))
    assert result.kind == "blocked" and "Historical" in result.reason
    st = monthly_state()
    with mockctx.active({"layout": A.LAYOUT}):
        result = invoke(C.build, st, layout=M.LAYOUT)
    assert result.kind == "blocked" and "Conflicting" in result.reason
    assert not provider.writes


def test_failed_only_exact_title_retries_and_canonical_gate_unchanged():
    rows = [{"testCaseTitle": "recovered", "outcome": "Failed"},
            {"testCaseTitle": "recovered", "outcome": "Passed"},
            {"testCaseTitle": "failed", "outcome": "Failed"},
            {"testCaseTitle": "failed", "outcome": "Failed"},
            {"testCaseTitle": "NA", "outcome": "NotApplicable"}]
    st = monthly_state(rows)
    rc = st.pipeline_runs["rcs"][-1]
    before = deepcopy(rc)
    frozen = M.source(rc, st.release_id)
    assert [r["title"] for r in frozen["rows"]] == ["failed"]
    assert len(frozen["rows"][0]["attempts"]) == 2
    assert M.validate_source(frozen) == frozen
    ok, projected, _ = P.project_auth_ui_results(rc)
    assert ok and len(projected["cases"]) == 2
    assert A.classification(rc, projected)["mapped_case_ids"] == [2916347, 2916524]
    assert all(s["case_id"] is None for s in projected["sources"] if s["suite"] == P.MONTHLY_REPORT_ONLY)
    assert rc == before


@pytest.mark.skipif(not os.environ.get("RELEASE_AGENT_MONTHLY_CAPTURE"),
                    reason="immutable captured source supplied explicitly for local proof")
def test_actual_captured_monthly_preview_has_17_owned_cases_and_unchanged_gates(monkeypatch):
    path = Path(os.environ["RELEASE_AGENT_MONTHLY_CAPTURE"])
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "2598f899848167532e1c2df25c26e02635a19d3d8321ed2c10fe82cb817c7d91"
    envelope = json.loads(raw)
    rc = {"rc": 2, "auth": envelope["snapshot"]}
    def no_provider(*a, **k):
        raise AssertionError("preview must not call providers")
    monkeypatch.setattr(P, "_ado_rest_get", no_provider)
    monkeypatch.setattr(P, "_ado_rest_get_all", no_provider)
    monkeypatch.setattr(P, "_ado_rest_send", no_provider)
    preview = M.preview(rc, envelope["release_id"], "TEST ONLY Monthly failure tracker v2 2026-10-19")
    assert len(preview["new_cases"]) == len(preview["source"]["rows"]) == 17
    assert len(preview["suite_creates"]) == 4
    assert all(r["verdict"] == "Failed" and len(r["attempts"]) == 1
               and r["attempts"][0]["run_id"] == 695864941 for r in preview["source"]["rows"])
    assert len({r["attempts"][0]["result_id"] for r in preview["source"]["rows"]}) == 17
    assert len(preview["classification"]["mapped_case_ids"]) == 26
    assert preview["quality_gates"][P.MONTHLY_REPORT_ONLY] == {
        "failed": 17, "passed": 359, "pct": 95.5, "present": True, "total": 376}
    assert preview["quality_gates"][P.AUTH_UI_SUITES[0]] == {
        "failed": 7, "passed": 22, "pct": 75.9, "present": True, "total": 29}
    assert not preview["results"] and not preview["assignments"] and not preview["canonical_mapping_changes"]
    assert path.read_bytes() == raw


@pytest.mark.parametrize("damage", ["duplicate", "title", "attempt", "result", "link", "recovered", "extra"])
def test_source_integrity_fails_closed(damage):
    st = monthly_state()
    source = M.source(st.pipeline_runs["rcs"][-1], st.release_id)
    row = source["rows"][0]
    if damage == "duplicate":
        source["rows"].append(deepcopy(row))
    elif damage == "title":
        row["title"] += " "
    elif damage == "attempt":
        row["attempts"] = []
    elif damage == "result":
        row["attempts"][0]["result_id"] = True
    elif damage == "link":
        row["links"] = []
    elif damage == "recovered":
        row["verdict"] = "Passed"
    else:
        source["unexpected"] = 1
    with pytest.raises((ValueError, TypeError)):
        M.validate_source(source)


def test_zero_failures_no_folder_no_cases(provider):
    st, data, record = cloned(provider, [{"testCaseTitle": "pass", "outcome": "Passed"}])
    assert len(provider.posts) == 3
    assert not provider.cases and record["suite_id"] is None and not record["bindings"]
    assert M.checked(record) and A.target(data)["monthly"] == record
    ok, receipt, detail = M.fill(record, M.current(st.pipeline_runs["rcs"][-1], record), {}, lambda _: None)
    assert ok, detail
    assert receipt["status"] == "complete" and not receipt["applied_points"]


def test_v2_creates_static_sibling_new_owned_exact_cases_no_clone_fills(provider):
    st, data, record = cloned(provider)
    assert data["layout"] == M.LAYOUT and len(provider.posts) == 4 and len(provider.cases) == 2
    assert all(provider.rows[record["suite_id"]][k] == v
               for k, v in M.suite_payload(data["suite_id"]).items())
    assert not any(method == "PATCH" for _, method, _ in provider.writes)
    assert all(p["outcome"] == "Unspecified" and "assignedTo" not in p
               for p in provider.points[record["suite_id"]])
    for case in provider.cases.values():
        fields = case["fields"]
        assert fields["System.Tags"] == M.TAG and "ReleaseBugBash" not in fields["System.Tags"]
        assert not any("Automat" in k or k == "System.AssignedTo" for k in fields)
        assert M.MARKER in fields["System.Description"]
    assert "ReleaseBugBash" in data["queries"]["manual"]
    assert all(str(cid) not in data["queries"]["automated"] for cid in provider.cases)
    before = len(provider.writes)
    assert invoke(C.build, st, layout=M.LAYOUT).kind == "done"
    assert len(provider.writes) == before
    assert M.validate_live(record)


def test_v1_does_not_migrate_or_receive_monthly_record(provider):
    st = monthly_state()
    assert invoke(C.build, st).kind == "done"
    data = st.get_step("bug_bash", C.ID).data
    assert data["layout"] == A.LAYOUT and "monthly" not in data and len(provider.posts) == 3
    assert invoke(C.build, st, layout=M.LAYOUT).kind == "blocked"
    assert len(provider.posts) == 3
    data["monthly"] = {}
    with pytest.raises(ValueError):
        A.target(data)


@pytest.mark.parametrize("where", ["suite", "case"])
def test_unknown_create_intent_never_reposts(provider, monkeypatch, where):
    original = P._ado_rest_send
    def interrupted(url, method, body, timeout):
        if (where == "suite" and isinstance(body, dict) and body.get("name") == M.NAME
                or where == "case" and "/workitems/" in url):
            raise RuntimeError("lost provider acknowledgement")
        return original(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", interrupted)
    st = monthly_state()
    with pytest.raises(RuntimeError):
        invoke(C.build, st, layout=M.LAYOUT)
    record = st.get_step("bug_bash", C.ID).data["monthly"]
    assert any(op["status"] == "creating" for op in record["operations"].values())
    before = len(provider.writes)
    assert invoke(C.build, st, layout=M.LAYOUT).kind == "blocked"
    assert len(provider.writes) == before


def test_acknowledged_case_recovers_without_create_replay(provider):
    provider.fail_read = 4_000_000
    st = monthly_state()
    assert invoke(C.build, st, layout=M.LAYOUT).kind == "blocked"
    record = st.get_step("bug_bash", C.ID).data["monthly"]
    assert any(op.get("id") == 4_000_000 and op["status"] == "created"
               for op in record["operations"].values())
    provider.fail_read = None
    assert invoke(C.build, st, layout=M.LAYOUT).kind == "done"
    assert len(provider.cases) == 2


@pytest.mark.parametrize("damage", ["tag", "title", "membership", "config", "point", "child", "duplicate"])
def test_live_owned_isolation_fails_closed(provider, damage):
    _, _, record = cloned(provider)
    point = provider.points[record["suite_id"]][0]
    if damage in ("tag", "title"):
        provider.cases[point["testCase"]["id"]]["fields"]["System." + ("Tags" if damage == "tag" else "Title")] += " changed"
    elif damage == "membership":
        provider.points[record["suite_id"]].pop()
    elif damage == "config":
        point["configuration"]["id"] = 85
    elif damage == "point":
        point["id"] = 9999
    elif damage == "child":
        provider.rows[900] = {"id": 900, "name": "foreign", "parentSuite": {"id": record["suite_id"]}}
    else:
        provider.rows[900] = {**deepcopy(provider.rows[record["suite_id"]]), "id": 900}
    with pytest.raises(ValueError):
        M.validate_live(record)


def test_fills_preserve_manual_resolution_and_ack_history(provider):
    st, _, record = cloned(provider)
    projection = M.current(st.pipeline_runs["rcs"][-1], record)
    saved = []
    ok, first, detail = M.fill(record, projection, {}, lambda r: saved.append(deepcopy(r)))
    assert ok, detail
    assert len(first["applied_points"]) == 2 and len(first["history"]) == 2
    provider.points[record["suite_id"]][0]["outcome"] = "Passed"
    before = len(provider.writes)
    ok, second, detail = M.fill(record, projection, first, lambda _: None)
    assert ok, detail
    M.validate_fill(second, record, projection)
    assert len(second["preserved_points"]) == 1 and len(second["history"]) == 2
    assert len(provider.writes) == before
    assert provider.points[record["suite_id"]][0]["outcome"] == "Passed"
    ok, third, detail = M.fill(record, projection, second, lambda _: None)
    assert ok and third["preserved_points"] == second["preserved_points"]


def test_partial_fill_keeps_all_prior_acknowledgements_at_every_checkpoint(provider):
    st, _, record = cloned(provider)
    projection = M.current(st.pipeline_runs["rcs"][-1], record)
    ok, previous, _ = M.fill(record, projection, {}, lambda _: None)
    assert ok
    saves = []
    def checkpoint(value):
        saves.append(deepcopy(value))
        if len(saves) == 1:
            raise RuntimeError("crash")
    with pytest.raises(RuntimeError):
        M.fill(record, projection, previous, checkpoint)
    assert len(saves[0]["applied_points"]) == 2 and len(saves[0]["history"]) == 2
    assert M.fill(record, projection, saves[0], lambda _: None)[0]


def test_stale_or_incomplete_source_blocks_fill_before_writes(provider):
    st, data, record = cloned(provider)
    before = len(provider.writes)
    st.pipeline_runs["rcs"][-1]["auth"]["test"]["evidence"]["apk_build_id"] += 1
    with pytest.raises(ValueError):
        M.current(st.pipeline_runs["rcs"][-1], record)
    assert len(provider.writes) == before


def test_new_failures_cannot_append_to_frozen_membership(provider):
    st, _, record = cloned(provider, [{"testCaseTitle": "pass", "outcome": "Passed"}])
    rc = monthly_state().pipeline_runs["rcs"][-1]
    with pytest.raises(ValueError, match="coverage changed"):
        M.current(rc, record)


def test_previously_tracked_recovered_row_retained_without_new_cases(provider):
    st, _, record = cloned(provider)
    first_source = M.current(st.pipeline_runs["rcs"][-1], record)
    ok, first, detail = M.fill(record, first_source, {}, lambda _: None)
    assert ok, detail
    refreshed = monthly_state([{"testCaseTitle": "monthly one", "outcome": "Passed"},
                               {"testCaseTitle": "monthly two", "outcome": "Failed"}])
    projection = M.current(refreshed.pipeline_runs["rcs"][-1], record)
    assert len(projection["rows"]) == 2
    before_cases = deepcopy(provider.cases)
    ok, summary, detail = M.fill(record, projection, first, lambda _: None)
    assert ok, detail
    assert sorted(p["outcome"] for p in summary["applied_points"]) == ["Failed", "Passed"]
    assert provider.cases == before_cases and len(summary["history"]) == 3


@pytest.mark.parametrize("reached_provider", [True, False])
def test_unknown_outcome_receipt_recovers_by_live_identity_without_create_replay(provider, monkeypatch, reached_provider):
    st, _, record = cloned(provider)
    projection = M.current(st.pipeline_runs["rcs"][-1], record)
    saved = []
    original = P._ado_rest_send
    def interrupted(url, method, body, timeout):
        if "/points/" in url:
            if reached_provider:
                original(url, method, body, timeout)
            raise RuntimeError("lost outcome acknowledgement")
        return original(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", interrupted)
    with pytest.raises(RuntimeError):
        M.fill(record, projection, {}, lambda r: saved.append(deepcopy(r)))
    assert saved[-1]["uncertain_points"]
    monkeypatch.setattr(P, "_ado_rest_send", original)
    ok, summary, detail = M.fill(record, projection, saved[-1], lambda _: None)
    assert ok, detail
    assert len(summary["applied_points"]) == 2 and not summary.get("uncertain_points")
    assert len(provider.cases) == 2
    if reached_provider:
        assert any(h["receipt"] == "uncertain_readback" for h in summary["history"])


def test_foreign_same_name_monthly_leaf_never_adopted(provider, monkeypatch):
    original = provider.send
    def suite_with_foreign_leaf(url, method, body, timeout):
        result = original(url, method, body, timeout)
        if isinstance(body, dict) and body.get("name") == "Automated Tests":
            parent = body["parentSuite"]["id"]
            provider.rows[990] = {"id": 990, **M.suite_payload(parent)}
        return result
    monkeypatch.setattr(P, "_ado_rest_send", suite_with_foreign_leaf)
    st = monthly_state()
    assert invoke(C.build, st, layout=M.LAYOUT).kind == "blocked"
    assert not provider.cases and len(provider.posts) == 3


def test_partial_receipt_remapping_blocks_before_outcome_writes(provider):
    st, _, record = cloned(provider)
    projection = M.current(st.pipeline_runs["rcs"][-1], record)
    ok, first, detail = M.fill(record, projection, {}, lambda _: None)
    assert ok, detail
    first["applied_points"][0]["config_id"] += 1
    before = len(provider.writes)
    ok, _, detail = M.fill(record, projection, first, lambda _: None)
    assert not ok and "receipt" in detail and len(provider.writes) == before


def test_monthly_ack_without_applied_outcome_cannot_publish_complete_fill(provider, monkeypatch):
    st, _, record = cloned(provider)
    projection = M.current(st.pipeline_runs["rcs"][-1], record)
    original = P._ado_rest_send
    def ack_only(url, method, body, timeout):
        if "/points/" in url and "outcome" in body:
            return True, {}, ""
        return original(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", ack_only)
    saved = []
    ok, partial, detail = M.fill(record, projection, {}, lambda r: saved.append(deepcopy(r)))
    assert not ok and "readback incomplete" in detail
    assert partial["status"] == "incomplete" and partial["uncertain_points"]
    assert any(h["receipt"] == "provider_ack" for h in saved[-1]["history"])
    assert not partial["applied_points"]
    monkeypatch.setattr(P, "_ado_rest_send", original)
    assert M.fill(record, projection, partial, lambda _: None)[0]


def test_completed_result_requires_monthly_receipt_before_downstream(provider):
    st, _, record = cloned(provider)
    binding = current_binding(context(st))
    st.steps["bug_bash." + U.ID] = {"data": {"result": {
        "status": "complete", "stage": "complete", "binding": binding, "id": "reviewed"}}}
    with pytest.raises(ValueError, match="Monthly fill"):
        completed_result(context(st))


def test_failed_monthly_assignment_is_point_tester_only_and_manual_resolved_not_reassigned(provider, monkeypatch):
    from tools import distribution as D
    st, _, record = cloned(provider)
    projection = M.current(st.pipeline_runs["rcs"][-1], record)
    ok, summary, detail = M.fill(record, projection, {}, lambda _: None)
    assert ok, detail
    monkeypatch.setattr(D, "resolve_tester_identity", lambda *a, **k: "owner-guid")
    failed = summary["applied_points"][0]
    assert D.set_point_testers(record["plan_id"], record["suite_id"], [failed["point_id"]],
                               "owner@example.com", expected_points={str(failed["point_id"]): failed})[0]
    assignment = provider.writes[-1]
    assert assignment[1:] == ("PATCH", {"tester": {"id": "owner-guid"}})
    assert "/points/" in assignment[0]
    provider.points[record["suite_id"]][0]["outcome"] = "Passed"
    before = len(provider.writes)
    assert not D.set_point_testers(record["plan_id"], record["suite_id"],
                                   [failed["point_id"]], "owner@example.com")[0]
    assert len(provider.writes) == before


def ready_fill(provider, monkeypatch):
    from tools import distribution as D
    from tools.ui_mapping import config_for
    st, data, record = cloned(provider)
    provider.points[data["automated_suite_id"]] = [
        {"id": 201 + i, "testCase": {"id": cid}, "configuration": {"id": 84}, "outcome": "Unspecified"}
        for i, cid in enumerate(data["classification"]["mapped_case_ids"])]
    ok, projection, detail = P.project_mrwp_ui_results(st.pipeline_runs["rcs"][-1])
    assert ok, detail
    provider.points[901] = [
        {"id": 501 + i, "testCase": {"id": cid}, "configuration": {"id": config_for(*pair)},
         "outcome": "Unspecified"} for i, (cid, pair) in enumerate(
             (cid, pair) for cid, variants in projection["verdicts"].items() for pair in variants)]
    monkeypatch.setattr(D, "resolve_tester_identity", lambda *a, **k: "owner-guid")
    return st, data, record


def test_p3s3_publishes_only_after_all_fills_and_point_tester_readback(provider, monkeypatch):
    st, data, record = ready_fill(provider, monkeypatch)
    outcome = invoke(U.build, st)
    assert outcome.kind == "done", outcome
    result = completed_result(context(st))
    assert result["monthly"]["status"] == "complete"
    assert len(result["monthly"]["applied_points"]) == 2
    assert result["auth"]["automated_case_ids"] == [2916347, 2916524]
    assert all(p["outcome"] == "Failed" and p["assignedTo"]["id"] == "owner-guid"
               for p in provider.points[record["suite_id"]])
    assert all(not ("/workitems/" in u and method == "PATCH") for u, method, _ in provider.writes)
    owned_failures = [r for r in result["investigations"]["auth"] if r["suite"] == P.MONTHLY_REPORT_ONLY]
    assert all(r["case_id"] in provider.cases and r["status"] == "owned_monthly_investigation"
               for r in owned_failures)


def test_monthly_fill_error_invalidates_publication_and_preserves_partial_receipts(provider, monkeypatch):
    st, _, record = ready_fill(provider, monkeypatch)
    original = P._ado_rest_send
    def fail(url, method, body, timeout):
        if f"/Suites/{record['suite_id']}/points/" in url and body.get("outcome"):
            return False, None, "provider failure"
        return original(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", fail)
    assert invoke(U.build, st).kind == "blocked"
    partial = st.get_step("bug_bash", U.ID).data["monthly"]
    assert partial["uncertain_points"] and partial["status"] == "incomplete"
    with pytest.raises(ValueError, match="Completed UI fill"):
        completed_result(context(st))


def test_downstream_uses_live_monthly_outcomes_and_retained_resolutions(provider, monkeypatch):
    from steps.bug_bash import distribute_tests as D
    from tools import bugbash as B
    st, _, record = ready_fill(provider, monkeypatch)
    assert invoke(U.build, st).kind == "done"
    provider.points[record["suite_id"]][0]["outcome"] = "Passed"
    result = completed_result(context(st))
    groups = D._monthly_groups(context(st), result)
    assert len(groups) == 1
    assert sorted(p["outcome"] for p in groups[0]["points"]) == ["Failed", "Passed"]
    triage = D._owner_triage(context(st), [], {"authenticator": {"triage_tags": []}})
    assert len([v for v in triage.values() if "monthly_investigation" in v["reasons"]]) == 1
    ok, points, detail = B._points_of_suite(record["plan_id"], record["suite_id"])
    assert ok, detail
    assert B._case_state([points[0]["outcome"]]) == "passed"


def test_monthly_owner_assignment_errors_visible_nonblocking(provider, monkeypatch):
    st, _, record = ready_fill(provider, monkeypatch)
    original = P._ado_rest_send
    def deny_tester(url, method, body, timeout):
        if f"/Suites/{record['suite_id']}/points/" in url and "tester" in body:
            return False, None, "denied owner assignment"
        return original(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", deny_tester)
    assert invoke(U.build, st).kind == "done"
    assert completed_result(context(st))["monthly"]["status"] == "complete"
    errors = st.get_step("bug_bash", U.ID).data["monthly_assignment_errors"]
    assert len(errors) == 2 and all("denied" in e["detail"] for e in errors)


def test_v2_invitation_and_rollout_links_require_complete_fill(provider, monkeypatch):
    from steps.bug_bash import send_invite as I
    from steps.rollout_start import notice as N
    from steps.lib import mockctx
    st, _, record = ready_fill(provider, monkeypatch)
    assert invoke(I.build, st).kind == "blocked"
    assert N._plans(context(st))[0] is None
    assert invoke(U.build, st).kind == "done"
    with mockctx.active({"now": "2026-09-11T10:00:00-07:00", "flags": "{}"}):
        invitation = invoke(I.build, st)
    assert invitation.kind == "needs_skill", invitation
    link = M.links(record)[0]
    from html import escape
    assert escape(link["url"], quote=True) in invitation.payload["body"]
    assert ["steps", "bug_bash.ui_test_status", "data", "result"] in [
        p["path"] for p in invitation.notification["state_matches"]]
    plans, detail = N._plans(context(st))
    assert plans["monthly"] == link, detail
    st.steps["bug_bash." + U.ID]["data"]["result"]["monthly"]["status"] = "incomplete"
    assert invoke(I.build, st).kind == "blocked" and N._plans(context(st))[0] is None


def test_v2_progress_reads_actual_monthly_testers_and_resolution_once(provider, monkeypatch):
    from tools import bugbash as B
    st, data, record = ready_fill(provider, monkeypatch)
    assert invoke(U.build, st).kind == "done"
    result = completed_result(context(st))
    monkeypatch.setattr(B, "_broker_points", lambda *a, **k: (True, [], ""))
    for point in provider.points[record["suite_id"]]:
        point["assignedTo"] = {"id": "owner-guid", "uniqueName": "owner@example.test"}
    provider.points[record["suite_id"]][0]["outcome"] = "Passed"
    args = {"auto_failed_ids": result["auth"]["failed_case_ids"],
            "auth_automated_ids": result["auth"]["automated_case_ids"],
            "broker_ui_result": result["broker"], "auth_ui_result": result["auth"],
            "monthly_result": result["monthly"]}
    ok, progress, detail = B.gather_progress(900, T.BROKER_MANUAL_SUITE_NAME, T.AUTH_PLAN,
                                           data["suite_id"], **args)
    assert ok, detail
    tests = [t for owner in progress["owners"].values() for t in owner["tests"]
             if t.get("investigation_kind") == "monthly"]
    assert len(tests) == 2 and sorted(t["state"] for t in tests) == ["failed", "passed"]
    args["monthly_result"] = {}
    assert not B.gather_progress(900, T.BROKER_MANUAL_SUITE_NAME, T.AUTH_PLAN, data["suite_id"], **args)[0]


@pytest.mark.parametrize("reached_provider", [True, False])
def test_uncertain_membership_never_reposts_and_recovers_only_exact_readback(provider, monkeypatch, reached_provider):
    original = P._ado_rest_send
    def interrupted(url, method, body, timeout):
        if "/TestCase?" in url:
            if reached_provider:
                original(url, method, body, timeout)
            return False, None, "membership acknowledgement lost"
        return original(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", interrupted)
    st = monthly_state()
    assert invoke(C.build, st, layout=M.LAYOUT).kind == "blocked"
    monkeypatch.setattr(P, "_ado_rest_send", original)
    before = len(provider.writes)
    result = invoke(C.build, st, layout=M.LAYOUT)
    assert result.kind == ("done" if reached_provider else "blocked")
    assert len(provider.writes) == before and len(provider.cases) == 2


@pytest.mark.parametrize("damage", ["missing", "false_inherit", "duplicate", "cycle", "invalid"])
def test_monthly_inherited_configurations_fail_closed(provider, monkeypatch, damage):
    _, data, record = cloned(provider)
    original = T.get_suite
    def changed(plan, sid, *a, **k):
        ok, row, detail = original(plan, sid, *a, **k)
        if sid == data["suite_id"]:
            if damage == "missing":
                row.pop("defaultConfigurations")
            elif damage == "duplicate":
                row["defaultConfigurations"] *= 2
            elif damage == "invalid":
                row["defaultConfigurations"] = [{"id": True}]
            else:
                row["defaultConfigurations"] = []
                row["inheritDefaultConfigurations"] = damage != "false_inherit"
                row["parentSuite"] = {"id": sid}
        return ok, row, detail
    monkeypatch.setattr(T, "get_suite", changed)
    with pytest.raises(ValueError, match="configuration"):
        M.validate_live(record)
