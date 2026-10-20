"""Offline, locked existing-parent amendment: no clone replay or revision adoption."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import os

import pytest

from orchestrator import auth_monthly_amendment as E, cli_common as CC, revision as R
from orchestrator.cli import main
from steps.bug_bash import clone_plans_auth as C, ui_test_status as U, ui_results as UR
from tests._context import context, fresh_orchestrator, invoke, effect_context
from tests.test_auth_monthly import provider, monthly_state
from tools import auth_monthly as M, auth_partition as A, pipelines as P, testplans as T


def seed(provider, tmp_path, rows=None):
    st = monthly_state(rows)
    result = invoke(C.build, st)
    assert result.kind == "done", result
    step = st.get_step("bug_bash", C.ID)
    step.status, step.completed_at = "done", "2026-10-19T19:20:40+00:00"
    st.set_step("bug_bash", C.ID, step)
    fresh_orchestrator(CC.DEFAULT_CONFIG, st)
    st.workflow_revision["runtime_hash"] = "sha256:" + "0" * 64
    # Native-shaped inherited NULLs, not the original Provider's injected [84].
    for row in provider.rows.values():
        row["defaultConfigurations"] = None
    provider.rows[T.AUTH_ROOT_SUITE] = {
        "id": T.AUTH_ROOT_SUITE, "name": "Root", "suiteType": "staticTestSuite",
        "parentSuite": None, "defaultConfigurations": [{"id": 84}],
        "inheritDefaultConfigurations": False}
    root = str(tmp_path / "runs")
    path = Path(CC.state_path(root, st.release_id))
    st.save(str(path))
    provider.writes.clear()
    return root, st.release_id, path


@contextmanager
def transaction(seed):
    root, release, _ = seed
    with CC.state_lock(root, release):
        _, orch = CC.load_orch(root, release, CC.DEFAULT_CONFIG)
        orch._monthly_runs_root = root
        yield orch


def apply(orch, **kwargs):
    defaults = {"review_hash": E.preview(orch)["review_hash"], "approved_by": "reviewer",
                "executor": "executor", "reserve": True}
    return E.execute(orch, **{**defaults, **kwargs})


@pytest.fixture
def native(provider, monkeypatch):
    def get(plan, sid, *args, **kwargs):
        if plan == T.AUTH_PLAN:
            return True, deepcopy(provider.rows.get(int(sid))), ""
        return provider.get(plan, sid, *args, **kwargs)
    monkeypatch.setattr(T, "get_suite", get)
    return provider


def test_cli_preview_does_not_mutate_and_requires_exact_independent_claim(native, tmp_path, capsys):
    owned = seed(native, tmp_path)
    root, release, path = owned
    before = path.read_bytes()
    assert main(["--runs-root", root, E.COMMAND, "--release", release]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview" and path.read_bytes() == before
    payload = preview["plan"]["payload"]
    assert payload["suite"]["parentSuite"]["id"] == 800
    assert len(payload["cases"]) == 2 and not native.writes
    with transaction(owned) as orch:
        for args in ({"review_hash": "wrong"}, {"approved_by": ""}, {"executor": ""}, {"reserve": False}):
            with pytest.raises(ValueError):
                apply(orch, **args)
        assert not native.writes and E.base_receipt(orch.state) is None


def test_completed_v1_amendment_preserves_clone_pin_and_native_original_points(native, tmp_path):
    owned = seed(native, tmp_path)
    with transaction(owned) as orch:
        before = E.base_clone(orch.state)
        pin = deepcopy(orch.state.workflow_revision)
        source = deepcopy(orch.state.pipeline_runs)
        native.points[before["data"]["manual_suite_id"]] = [
            {"id": 33, "testCase": {"id": 55}, "configuration": {"id": 84},
             "outcome": "Passed", "assignedTo": {"id": "human"}}]
        points = deepcopy(native.points)
        receipt = apply(orch)
        assert receipt["status"] == "complete"
        assert E.base_clone(orch.state) == before and orch.state.workflow_revision == pin
        assert orch.state.pipeline_runs == source
        for sid, values in points.items():
            assert native.points[sid] == values
        assert len(native.writes) == 4  # one suite, two cases, one membership; no fills
        data = orch.state.get_step("bug_bash", C.ID).data
        assert data["layout"] == A.LAYOUT and "monthly" not in data
        target = A.current(context(orch.state), data)
        assert target["layout"] == A.AMENDMENT_LAYOUT
        A.validate_target(target)
        assert UR.current_binding(context(orch.state))["auth"] == target
        assert invoke(C.build, orch.state).kind == "blocked"
        before_receipt = deepcopy(E.base_receipt(orch.state))
        apply(orch, reserve=False)
        assert E.base_receipt(orch.state) == before_receipt and len(native.writes) == 4
        with pytest.raises(ValueError, match="claim/reviewer"):
            apply(orch, executor="other")
        assert native.points[receipt["monthly"]["suite_id"]][0]["outcome"] == "Unspecified"


def test_acknowledged_case_recovers_without_repost_and_partial_holds_p3s3(native, tmp_path):
    owned = seed(native, tmp_path)
    native.fail_read = 4_000_000
    with transaction(owned) as orch:
        with pytest.raises(ValueError, match="incomplete"):
            apply(orch)
        receipt = deepcopy(E.base_receipt(orch.state))
        assert receipt["monthly"]["operations"]["case:" + receipt["plan"]["source"]["rows"][0]["key"]]["id"] == 4_000_000
        with pytest.raises(ValueError, match="hold P3S3"):
            UR.current_binding(context(orch.state))
    with transaction(owned) as orch:
        native.fail_read = None
        assert apply(orch, reserve=False)["status"] == "complete"
        assert len(native.writes) == 4


@pytest.mark.parametrize("unknown", ["suite", "case", "membership"])
def test_unknown_provider_response_never_reposts(native, tmp_path, monkeypatch, unknown):
    owned = seed(native, tmp_path)
    send = native.send
    triggered = False
    def uncertain(url, method, body, timeout):
        nonlocal triggered
        match = (unknown == "suite" and "/suites?" in url or unknown == "case" and "/workitems/" in url
                 or unknown == "membership" and "/TestCase?" in url)
        if match and not triggered:
            triggered = True
            if unknown == "membership":
                send(url, method, body, timeout)
            return False, None, "HTTP timeout"
        return send(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", uncertain)
    with transaction(owned) as orch:
        with pytest.raises(ValueError, match="incomplete"):
            apply(orch)
        writes = len(native.writes)
    with transaction(owned) as orch:
        if unknown == "membership":
            assert apply(orch, reserve=False)["status"] == "complete"
        else:
            with pytest.raises(ValueError, match="Uncertain create"):
                apply(orch, reserve=False)
        assert len(native.writes) == writes


@pytest.mark.parametrize("damage", ["source", "base", "configuration", "halt", "cancel", "runtime", "claim"])
def test_drift_after_ack_retains_history_and_blocks_more_writes(native, tmp_path, monkeypatch, damage):
    owned = seed(native, tmp_path)
    native.fail_read = 4_000_000
    with transaction(owned) as orch:
        with pytest.raises(ValueError):
            apply(orch)
        before = deepcopy(E.base_receipt(orch.state))
        if damage == "source":
            run = orch.state.pipeline_runs["rcs"][-1]["auth"]["test"]["run_id"]
            orch.state.pipeline_runs["rcs"][-1]["auth"]["test"]["run_id"] = str(int(run) + 1)
        elif damage == "base":
            orch.state.steps[E.KEY]["note"] = "changed original completion note"
        elif damage == "configuration":
            native.rows[T.AUTH_ROOT_SUITE]["defaultConfigurations"] = [{"id": 85}]
        elif damage in ("halt", "cancel"):
            setattr(orch.state, "halt" if damage == "halt" else "cancellation",
                    {"reason": "owner stopped", "at": "now"})
        elif damage == "runtime":
            monkeypatch.setattr(orch, "_loaded_runtime_hash", "changed")
        else:
            E.base_receipt(orch.state)["owner"] = "different executor"
        writes = len(native.writes)
        with pytest.raises((ValueError, KeyError)):
            apply(orch, reserve=False)
        assert len(native.writes) == writes
        assert E.base_receipt(orch.state)["monthly"] == before["monthly"]


def test_cancel_retains_intents_and_cannot_restart(native, tmp_path):
    owned = seed(native, tmp_path)
    native.fail_read = 4_000_000
    with transaction(owned) as orch:
        with pytest.raises(ValueError):
            apply(orch)
        receipt = deepcopy(E.base_receipt(orch.state))
        E.cancel(orch, review_hash=receipt["review_hash"], approved_by="reviewer",
                 executor="executor", reason="owner cancelled")
        assert E.base_receipt(orch.state)["monthly"] == receipt["monthly"]
        with pytest.raises(ValueError, match="cancelled"):
            apply(orch)
        with pytest.raises(ValueError):
            A.target(orch.state.get_step("bug_bash", C.ID).data)


def test_zero_failed_scenarios_creates_nothing(native, tmp_path):
    owned = seed(native, tmp_path, [{"testCaseTitle": "passing", "outcome": "Passed"}])
    with transaction(owned) as orch:
        preview = E.preview(orch)
        assert preview["plan"]["payload"]["suite"] is None
        assert preview["plan"]["payload"]["cases"] == []
        assert apply(orch)["status"] == "complete" and not native.writes


def test_foreign_same_named_leaf_or_stale_preview_blocks_before_claim(native, tmp_path):
    owned = seed(native, tmp_path)
    with transaction(owned) as orch:
        old = E.preview(orch)["review_hash"]
        native.rows[T.AUTH_ROOT_SUITE]["defaultConfigurations"] = [{"id": 85}]
        with pytest.raises(ValueError, match="Stale"):
            apply(orch, review_hash=old)
        assert E.base_receipt(orch.state) is None
        native.rows[999] = {"id": 999, **M.suite_payload(800)}
        with pytest.raises(ValueError, match="Foreign"):
            E.preview(orch)
        assert E.base_receipt(orch.state) is None and not native.writes


def test_no_transaction_no_write_capability(native, tmp_path):
    owned = seed(native, tmp_path)
    from orchestrator.engine import Orchestrator
    from orchestrator.state import ReleaseState
    orch = Orchestrator(CC.DEFAULT_CONFIG, ReleaseState.load(str(owned[2])))
    orch._monthly_runs_root = owned[0]
    with pytest.raises(ValueError, match="locked"):
        apply(orch)
    assert not native.writes


@pytest.mark.parametrize("malformed", [None, {}, "unowned"])
def test_malformed_existing_amendment_is_not_absent_or_reset(native, tmp_path, malformed):
    with transaction(seed(native, tmp_path)) as orch:
        orch.state.steps[E.KEY]["data"]["monthly_amendment"] = malformed
        with pytest.raises(ValueError, match="Malformed Monthly amendment receipt"):
            apply(orch)
        assert orch.state.steps[E.KEY]["data"]["monthly_amendment"] == malformed
        assert not native.writes


def prepare_fill(native, st, monkeypatch):
    from tools import distribution as D
    from tools.ui_mapping import config_for
    data = st.get_step("bug_bash", C.ID).data
    native.points[data["automated_suite_id"]] = [
        {"id": 201 + i, "testCase": {"id": cid}, "configuration": {"id": 84}, "outcome": "Unspecified"}
        for i, cid in enumerate(data["classification"]["mapped_case_ids"])]
    ok, projection, detail = P.project_mrwp_ui_results(st.pipeline_runs["rcs"][-1])
    assert ok, detail
    native.points[901] = [
        {"id": 501 + i, "testCase": {"id": cid}, "configuration": {"id": config_for(*pair)},
         "outcome": "Unspecified"} for i, (cid, pair) in enumerate(
             (cid, pair) for cid, variants in projection["verdicts"].items() for pair in variants)]
    monkeypatch.setattr(D, "resolve_tester_identity", lambda *a, **k: "owner-guid")


def test_amended_target_full_p3s3_downstream_and_manual_resolution_survive_retry(native, tmp_path, monkeypatch):
    from steps.bug_bash import distribute_tests as D, send_invite as I
    from steps.rollout_start import notice as N
    from steps.lib import mockctx
    from tools import bugbash as B
    owned = seed(native, tmp_path)
    with transaction(owned) as orch:
        receipt = apply(orch)
        st, record = orch.state, receipt["monthly"]
        base = E.base_clone(st)
        prepare_fill(native, st, monkeypatch)
        with pytest.raises(ValueError, match="Completed UI fill"):
            D._fill_input(context(st))
        assert invoke(I.build, st).kind == "blocked"
        assert N._plans(context(st))[0] is None
        result = invoke(U.build, st)
        assert result.kind == "done", result
        result = UR.completed_result(context(st))
        assert result["auth"]["target"]["layout"] == A.AMENDMENT_LAYOUT
        assert len(result["monthly"]["applied_points"]) == 2
        assert all(p["outcome"] == "Failed" and p["assignedTo"]["id"] == "owner-guid"
                   for p in native.points[record["suite_id"]])
        assert all(not ("/workitems/" in url and method == "PATCH") for url, method, _ in native.writes)
        native.points[record["suite_id"]][0].update(outcome="Passed",
                                                   assignedTo={"id": "human", "uniqueName": "human@example.test"})
        before = len(native.writes)
        retry = invoke(U.build, st)
        assert retry.kind == "done", retry
        assert native.points[record["suite_id"]][0]["outcome"] == "Passed"
        assert not any("/workitems/" in url for url, _, _ in native.writes[before:])
        result = UR.completed_result(context(st))
        groups = D._monthly_groups(context(st), result)
        assert sorted(p["outcome"] for p in groups[0]["points"]) == ["Failed", "Passed"]
        triage = D._owner_triage(context(st), [], {"authenticator": {"triage_tags": []}})
        assert len([v for v in triage.values() if "monthly_investigation" in v["reasons"]]) == 1
        ok, points, detail = B._points_of_suite(record["plan_id"], record["suite_id"])
        assert ok, detail
        assert B._case_state([points[0]["outcome"]]) == "passed"
        with mockctx.active({"now": "2026-09-11T10:00:00-07:00", "flags": "{}"}):
            invitation = invoke(I.build, st)
        assert invitation.kind == "needs_skill", invitation
        link = M.links(record)[0]
        from html import escape
        assert escape(link["url"], quote=True) in invitation.payload["body"]
        assert N._plans(context(st))[0]["monthly"] == link
        matches = I.source_matches(context(st), "clone_plans_auth")
        assert any(m["path"][-1] == "monthly_amendment" for m in matches)
        reminder = UR.reminder_preconditions(effect_context(U.build, st, {"id": "unit-fill"}))
        assert any(m["path"][-1] == "monthly_amendment" for m in reminder)
        assert E.base_clone(st) == base
        monkeypatch.setattr(B, "_broker_points", lambda *a, **k: (True, [], ""))
        result = UR.completed_result(context(st))
        ok, progress, detail = B.gather_progress(
            900, T.BROKER_MANUAL_SUITE_NAME, T.AUTH_PLAN, base["data"]["suite_id"],
            auto_failed_ids=result["auth"]["failed_case_ids"],
            auth_automated_ids=result["auth"]["automated_case_ids"],
            broker_ui_result=result["broker"], auth_ui_result=result["auth"],
            monthly_result=result["monthly"])
        assert ok, detail
        investigations = [t for owner in progress["owners"].values() for t in owner["tests"]
                          if t.get("investigation_kind") == "monthly"]
        assert len(investigations) == 2 and sorted(t["state"] for t in investigations) == ["failed", "passed"]
        st.steps["bug_bash." + U.ID]["data"]["result"]["monthly"]["status"] = "incomplete"
        assert invoke(I.build, st).kind == "blocked" and N._plans(context(st))[0] is None


def test_cli_execute_is_separate_reviewed_capability_and_persists_complete_receipt(native, tmp_path, capsys):
    root, release, path = seed(native, tmp_path)
    assert main(["--runs-root", root, E.COMMAND, "--release", release]) == 0
    preview = json.loads(capsys.readouterr().out)
    original_pin = json.loads(path.read_text())["workflow_revision"]
    assert main(["--runs-root", root, E.COMMAND, "--release", release, "--execute", "--reserve",
                 "--review-hash", preview["review_hash"], "--approved-by", "owner", "--executor", "executor"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    from orchestrator.state import ReleaseState
    loaded = ReleaseState.load(str(path))
    assert E.base_receipt(loaded)["status"] == "complete" and loaded.workflow_revision == original_pin
    with pytest.raises(ValueError):
        R.assert_current(CC.load_orch(root, release, CC.DEFAULT_CONFIG)[1])


def test_checkpoint_retries_only_windows_sharing_errors_not_provider_writes(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def save():
        calls.append("checkpoint")
        if len(calls) < 3:
            error = PermissionError("sharing interference")
            error.winerror = 5
            raise error
    monkeypatch.setattr(E.time, "sleep", lambda _: None)
    if os.name == "nt":
        E._persist(SimpleNamespace(checkpoint=save))
        assert len(calls) == 3
    else:
        with pytest.raises(PermissionError):
            E._persist(SimpleNamespace(checkpoint=save))
        assert len(calls) == 1


@pytest.mark.parametrize("damage", ["version_bool", "plan_version_bool", "extra", "seal", "base"])
def test_completed_amendment_tampering_blocks_target_and_publication(native, tmp_path, damage):
    value = seed(native, tmp_path)
    with transaction(value) as orch:
        apply(orch)
        assert UR.current_binding(context(orch.state))
        data = orch.state.steps[E.KEY]["data"]
        receipt = data["monthly_amendment"]
        if damage == "version_bool":
            receipt["version"] = True
        elif damage == "plan_version_bool":
            receipt["plan"]["version"] = True
        elif damage == "extra":
            receipt["unreviewed"] = True
        elif damage == "seal":
            receipt["monthly"]["bindings"][0]["case_id"] += 1
        else:
            data["suite_name"] += " changed"
        with pytest.raises(ValueError):
            A.target(data)
        with pytest.raises(ValueError):
            UR.current_binding(context(orch.state))


def test_native_rest_null_inheritance_is_preserved_and_resolves_actual_root(monkeypatch):
    from tools import pipelines as P
    rows = {
        3794450: {"id": 3794450, "suiteType": "staticTestSuite", "parentSuite": {"id": T.AUTH_ROOT_SUITE},
                  "inheritDefaultConfigurations": True, "defaultConfigurations": None},
        T.AUTH_ROOT_SUITE: {"id": T.AUTH_ROOT_SUITE, "suiteType": "staticTestSuite", "parentSuite": None,
                           "inheritDefaultConfigurations": False, "defaultConfigurations": [{"id": 84}]},
    }
    calls = []
    def get(url, timeout):
        sid = int(url.split("/suites/", 1)[1].split("?", 1)[0])
        calls.append(sid)
        return True, deepcopy(rows[sid]), ""
    monkeypatch.setattr(P, "_ado_rest_get", get)
    ok, parent, _ = T.get_suite(T.AUTH_PLAN, 3794450)
    assert ok and parent["defaultConfigurations"] is None
    assert M._configs({"plan_id": T.AUTH_PLAN, "parent_suite_id": 3794450}, 1) == [84]
    assert calls == [3794450, 3794450, T.AUTH_ROOT_SUITE]


@pytest.mark.parametrize("field", ["release_id", "ccd", "target_month", "owner_email"])
def test_completed_amendment_consumer_rejects_changed_release_context(native, tmp_path, field):
    with transaction(seed(native, tmp_path)) as orch:
        apply(orch)
        data = orch.state.steps[E.KEY]["data"]
        assert A.current(context(orch.state), data)["layout"] == A.AMENDMENT_LAYOUT
        setattr(orch.state, field, "changed-context")
        with pytest.raises(ValueError, match="release identity changed"):
            A.current(context(orch.state), data)


@pytest.mark.parametrize("configs", [None, "missing", []])
def test_native_null_missing_empty_inherited_configs_walk_to_actual_root(native, monkeypatch, configs):
    sid = 111
    native.rows[sid] = {"id": sid, "suiteType": "staticTestSuite", "parentSuite": {"id": T.AUTH_ROOT_SUITE},
                        "inheritDefaultConfigurations": True}
    if configs != "missing":
        native.rows[sid]["defaultConfigurations"] = configs
    native.rows[T.AUTH_ROOT_SUITE] = {"id": T.AUTH_ROOT_SUITE, "suiteType": "staticTestSuite",
        "inheritDefaultConfigurations": False, "parentSuite": None, "defaultConfigurations": [{"id": 84}]}
    assert M._configs({"plan_id": T.AUTH_PLAN, "parent_suite_id": sid}, 1) == [84]


@pytest.mark.parametrize("damage", ["object", "string", "false", "truthy", "parent", "parent_bool",
                                   "cycle", "root_empty", "duplicate", "invalid_id", "bool_id"])
def test_native_config_guards_fail_closed(native, damage):
    native.rows[111] = {"id": 111, "suiteType": "staticTestSuite", "parentSuite": {"id": 222},
                        "inheritDefaultConfigurations": True, "defaultConfigurations": None}
    root = {"id": 222, "suiteType": "staticTestSuite", "parentSuite": None,
            "inheritDefaultConfigurations": False, "defaultConfigurations": [{"id": 84}]}
    native.rows[222] = root
    row = native.rows[111]
    if damage in ("object", "string"):
        row["defaultConfigurations"] = {} if damage == "object" else "84"
    elif damage in ("false", "truthy"):
        row["inheritDefaultConfigurations"] = False if damage == "false" else 1
    elif damage.startswith("parent"):
        row["parentSuite"] = None if damage == "parent" else {"id": True}
    elif damage == "cycle":
        row["parentSuite"] = {"id": 111}
    elif damage == "root_empty":
        root["defaultConfigurations"] = None
    else:
        root["defaultConfigurations"] = [{"id": 84}, {"id": 84}] if damage == "duplicate" else [
            {"id": "+84" if damage == "invalid_id" else True}]
    with pytest.raises(ValueError):
        M._configs({"plan_id": T.AUTH_PLAN, "parent_suite_id": 111}, 1)


@pytest.mark.skipif(not os.environ.get("RELEASE_AGENT_MONTHLY_OWNED_STATE"),
                    reason="immutable owned capture supplied explicitly")
def test_real_owned_synthetic_release_has_exact_17_new_investigations_without_provider_calls():
    from orchestrator.state import ReleaseState
    path = Path(os.environ["RELEASE_AGENT_MONTHLY_OWNED_STATE"])
    before = path.read_bytes()
    assert hashlib.sha256(before).hexdigest() == "6f568ab04f6472566bc72b6fadbddc42f60284e2b63ee102c0b6413c90241292"
    st = ReleaseState.load(str(path))
    base = E.base_clone(st)
    # Supplied native observation: inherited parent NULL, explicit root configuration 84.
    plan = E.make_plan(st, {"offline_proof": True}, {"categories": [], "configuration_ids": [84]})
    assert len(plan["source"]["rows"]) == len(plan["payload"]["cases"]) == 17
    assert plan["identity"]["release_id"] == "2026-10-auth-categories-a5a98dd93670b8e4-checked"
    assert plan["payload"]["suite"]["parentSuite"] == {"id": 3794450}
    assert plan["base_clone"] == base and path.read_bytes() == before
    assert len(base["data"]["classification"]["mapped_case_ids"]) == 26
    assert not plan["payload"]["results"] and not plan["payload"]["testers"]
