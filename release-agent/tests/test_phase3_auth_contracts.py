"""Authenticator suite preparation and durable identity recovery, with no live ADO."""
from copy import deepcopy
from dataclasses import replace

import pytest

from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import clone_plans_auth as A
from tests._context import context, invoke, invoke_effect
from tools import testplans as T, pipelines as P


@pytest.mark.parametrize("ccd", ["bad", "2026-02-30", "2026-13-01", "2026-00-01", "2026-1-02"])
def test_invalid_ccd_is_a_preparation_hold(ccd):
    assert A.prepare_effect(context(ReleaseState(ccd=ccd))).kind == "blocked"


@pytest.mark.parametrize("name", [None, 0, [], "", "   ", " TEST "])
def test_invalid_name_is_a_preparation_hold(name):
    assert A.prepare_effect(context(ReleaseState(ccd="2026-08-13"), inputs={"name": name})).kind == "blocked"


def _state(data=None):
    state = ReleaseState(release_id="2026-08", ccd="2026-08-13")
    frozen = {"release": state.release_id, "org": T.ORG, "project": T.PROJECT,
              "plan_id": T.AUTH_PLAN, "parent_suite_id": T.AUTH_ROOT_SUITE,
              "name": T.auth_suite_name(state.ccd), "query": T.auth_bugbash_query()}
    state.set_step("bug_bash", A.ID, StepState(data=data or {}, execution={"effect_input": frozen}))
    state._checkpoint = lambda: None
    return state


@pytest.mark.parametrize("suite", [None, False, 0, "", "bad", []])
def test_invalid_present_suite_id_never_discovers_or_creates(suite, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A corrupt bound suite must never fall through to discovery/create")
    monkeypatch.setattr(T, "find_auth_query_suite", forbidden)
    monkeypatch.setattr(T, "create_auth_query_suite", forbidden)
    assert invoke(A.build, _state({"suite_id": suite})).kind == "blocked"


@pytest.mark.parametrize("creation", [None, [], {}, {"status": "unknown"}])
def test_invalid_creation_receipt_is_not_proof_that_no_post_started(creation, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A malformed creation record must hold before discovery or a new POST")
    monkeypatch.setattr(T, "find_auth_query_suite", forbidden)
    monkeypatch.setattr(T, "create_auth_query_suite", forbidden)
    assert invoke(A.build, _state({"creation": creation})).kind == "blocked"


@pytest.mark.parametrize("receipt", [None, False, 0, "", "bad", []])
def test_unknown_create_id_stays_unresolved_without_a_second_post(receipt, monkeypatch):
    calls = []
    monkeypatch.setattr(T, "find_auth_query_suite", lambda *a, **kw: (True, None, ""))
    monkeypatch.setattr(T, "create_auth_query_suite", lambda *a, **kw:
                        (calls.append(kw) is None, receipt, ""))
    state = _state()
    assert invoke(A.build, state).kind == "blocked"
    assert state.get_step("bug_bash", A.ID).data["creation"]["status"] == "creating"
    assert invoke(A.build, state).kind == "blocked"
    assert len(calls) == 1


def test_created_id_survives_failed_readback_and_recovery_never_rediscovers(monkeypatch):
    state = _state()
    checkpoints = []
    state._checkpoint = lambda: checkpoints.append(deepcopy(state.steps))
    monkeypatch.setattr(T, "find_auth_query_suite", lambda *a, **kw: (True, None, ""))
    monkeypatch.setattr(T, "create_auth_query_suite", lambda *a, **kw: (True, 777, ""))
    monkeypatch.setattr(T, "validate_auth_query_suite", lambda *a, **kw: (False, None, "HTTP 503"))
    outcome = invoke(A.build, state)
    assert outcome.kind == "blocked"
    data = state.get_step("bug_bash", A.ID).data
    assert data["suite_id"] == 777 and data["creation"]["status"] == "created"
    assert any(record["bug_bash.clone_plans_auth"]["data"].get("suite_id") == 777 for record in checkpoints)

    def no_create(*args, **kwargs):
        pytest.fail("An acknowledged suite ID must be validated directly")
    monkeypatch.setattr(T, "find_auth_query_suite", no_create)
    monkeypatch.setattr(T, "create_auth_query_suite", no_create)
    assert invoke(A.build, state).kind == "blocked"
    monkeypatch.setattr(T, "validate_auth_query_suite", lambda *a, **kw: (True, {"name": "suite"}, ""))
    assert invoke(A.build, state).kind == "done"
    assert state.get_step("bug_bash", A.ID).data["creation"]["status"] == "ready"


def test_retry_cannot_recreate_a_recorded_suite(monkeypatch):
    state = _state({"suite_id": 777, "creation": {"status": "created"}})
    frozen = A.prepare_effect(context(state))
    def forbidden(*args, **kwargs):
        pytest.fail("Recorded identity makes absence-based retry inapplicable")
    monkeypatch.setattr(T, "find_auth_query_suite", forbidden)
    outcome = invoke_effect(A.authorize_retry, state, {"effect_input": frozen}, "owner reviewed")
    assert not outcome.allowed


@pytest.mark.parametrize("suites", [None, {}, "bad", [None], [{"name": "suite", "parentSuite": "bad"}]])
def test_malformed_discovery_cannot_prove_absence(suites, monkeypatch):
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a, **kw: (True, suites, ""))
    assert not T.find_auth_query_suite("suite", "SELECT [System.Id]")[0]


@pytest.mark.parametrize("info", [None, [], {"name": []}, {"name": "suite", "parentSuite": "bad"}])
def test_malformed_lookup_is_not_adopted(info, monkeypatch):
    monkeypatch.setattr(T, "get_suite", lambda *a, **kw: (True, info, ""))
    assert not T.validate_auth_query_suite(777, "suite", "query")[0]


def test_wiql_layout_normalization_preserves_quoted_values(monkeypatch):
    query = "SELECT [System.Id] FROM WorkItems WHERE [System.Title] = 'two  spaces'"
    info = {"id": 777, "name": "suite", "parentSuite": {"id": T.AUTH_ROOT_SUITE},
            "suiteType": "dynamicTestSuite", "queryString": query.replace("SELECT ", "SELECT\n  ")}
    monkeypatch.setattr(T, "get_suite", lambda *a, **kw: (True, info, ""))
    assert T.validate_auth_query_suite(777, "suite", query)[0]
    info["queryString"] = query.replace("two  spaces", "two spaces")
    assert not T.validate_auth_query_suite(777, "suite", query)[0]
