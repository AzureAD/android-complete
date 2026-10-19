"""Stress is a source category, not a new gate or a rewrite of frozen evidence."""
from copy import deepcopy
from html import unescape

import pytest

from orchestrator.commands.rc_report import _format
from orchestrator.state import ReleaseState
from orchestrator.status_email import _rc_ui_summary
from steps.build_verify import rc_report as R, _rc_report_rendering as H
from tests._auth_evidence import auth_snapshot
from tests._mrwp_evidence import PROD, snapshot
from tools import pipelines as P
from tools.pipelines.test_evidence import report_snapshot, validate_snapshot_tests


STRESS = "Stress Tests - RC MSAL with RC Broker (API 32)"
TITLE = "test_concurrentAcquireTokenSilent_withBroker"


def legacy_snapshot(value):
    value = deepcopy(value)
    summary = value["tests"]
    summary.pop("category_basis")
    categories = {cat: {"total": 0, "passed": 0, "failed": 0, "na": 0, "recovered": []}
                  for cat in ("unit", "instrumented", "ui")}
    for suite in summary["suites"]:
        cat = suite["category"] = P.classify_test_run(suite["name"], legacy=True)
        for key in ("total", "passed", "failed", "na"):
            categories[cat][key] += suite[key]
        categories[cat]["recovered"].extend(
            {"suite": suite["name"], "title": test["title"]}
            for test in suite["test_results"] if test["recovered"])
    summary["categories"] = categories
    return value


def model(*, legacy=False, ui_passes=2, stress_outcome="Failed"):
    runs = {}
    for flight, build in (("ECS", 201), ("Local", 202)):
        rows = {PROD: [(f"test_{100+i}_UI", "Passed") for i in range(ui_passes)],
                STRESS: [(TITLE, stress_outcome), (TITLE, stress_outcome),
                         ("stress_na", "NotApplicable")]}
        value = snapshot(build, rows)
        runs[flight] = legacy_snapshot(value) if legacy else value
    result = P.assemble_rc_model("2026-10", {"fired": True, "run_id": "1", "when": "2026-10-17"},
                                 {"found": True, "healthy": True, "run_id": "2"},
                                 runs, rc=2, auth=auth_snapshot(rc=2))
    return R.prepare_report_evidence(result)


@pytest.mark.parametrize("name", [STRESS, "Stress Tests", "  stress tests - RC Broker # 123.1",
                                 "Stress Tests - common_UnitTests"])
def test_explicit_stress_source_names(name):
    assert P.classify_test_run(name) == "stress"


@pytest.mark.parametrize("name,category", [
    ("distress tests", "ui"), ("stressful tests", "ui"), ("Stress TestsUnexpected", "ui"),
    ("test_stress_concurrentAcquireTokenSilent", "ui"), ("Other Stress Tests", "ui"),
    ("common4j_UnitTests", "unit"), ("common_InstrumentedTests", "instrumented"),
    ("Lab Api Tests", "ui"), ("Native Auth E2E Tests", "ui"), (PROD, "ui"),
])
def test_classification_does_not_guess_from_titles_or_unrelated_names(name, category):
    assert P.classify_test_run(name) == category


@pytest.mark.parametrize("legacy", [False, True])
def test_validation_and_normalization_preserve_retry_identity_counts_and_source(legacy):
    raw = snapshot(201, {PROD: [("test_stress_is_only_a_title", "Passed")],
                         STRESS: [(TITLE, "Failed"), (TITLE, "Failed"),
                                  ("recovered", "Failed"), ("recovered", "Passed"),
                                  ("explicit_na", "NotExecuted")]})
    if legacy:
        raw = legacy_snapshot(raw)
    before = deepcopy(raw)
    assert len(validate_snapshot_tests(raw)[1]) == 4
    view = report_snapshot(raw)
    assert view == report_snapshot(view)
    assert raw == before
    assert view["tests"]["total"] == raw["tests"]["total"] == 3
    assert view["tests"]["result_entries"] == raw["tests"]["result_entries"] == 6
    assert P.ui_stress_counts(view["tests"]) == P.ui_stress_counts(raw["tests"])
    assert view["tests"]["categories"]["ui"]["total"] == 1
    assert view["tests"]["categories"]["stress"] == {
        "total": 2, "passed": 1, "failed": 1, "na": 1,
        "recovered": [{"suite": STRESS, "title": "recovered"}]}
    failure = view["failed_suites"][0]
    assert failure["name"] == STRESS and failure["category"] == "stress"
    assert failure["tests"] == [TITLE]
    assert len(next(t for t in failure["test_results"] if t["title"] == TITLE)["attempts"]) == 2


@pytest.mark.parametrize("suffix,category", [
    ("common_UnitTests", "unit"), ("common_InstrumentedTests", "instrumented"),
])
def test_legacy_stress_prefix_preserves_historical_non_ui_precedence_and_gate(suffix, category):
    name = "Stress Tests - " + suffix
    assert P.classify_test_run(name) == "stress"
    assert P.classify_test_run(name, legacy=True) == category
    captured = snapshot(201, {PROD: [("test_100_UI", "Passed")], name: [("failed", "Failed")]})
    raw = legacy_snapshot(captured)
    assert raw["tests"]["categories"][category]["failed"] == 1
    before = deepcopy(raw)
    assert len(validate_snapshot_tests(raw, include_all=True)[1]) == 2
    assert len(validate_snapshot_tests(raw)[1]) == 1
    view = report_snapshot(raw)
    assert view == report_snapshot(view) and raw == before
    validate_snapshot_tests(view, include_all=True)
    assert view["failed_suites"][0]["category"] == category
    assert view["tests"]["categories"]["stress"]["total"] == 0
    assert P.ui_stress_counts(view["tests"]) == P.ui_stress_counts(raw["tests"])
    assert P.ui_stress_counts(captured["tests"])["failed"] == 1


@pytest.mark.parametrize("tamper", ["missing", "counts", "suite", "basis", "legacy_suite",
                                  "outer_failure"])
def test_missing_or_corrupt_stress_is_unavailable_not_success(tamper):
    raw = snapshot(201, {PROD: [("test_100_UI", "Passed")], STRESS: [(TITLE, "Failed")]})
    if tamper == "missing":
        del raw["tests"]["categories"]["stress"]
    elif tamper == "counts":
        raw["tests"]["categories"]["stress"].update(total=0, failed=0)
    elif tamper == "suite":
        raw["tests"]["suites"][-1]["category"] = "ui"
    elif tamper == "basis":
        raw["tests"]["category_basis"] = "guessed"
    elif tamper == "outer_failure":
        del raw["failed_suites"]
    else:
        raw = legacy_snapshot(raw)
        raw["tests"]["suites"][0]["category"] = "instrumented"
    with pytest.raises(ValueError):
        validate_snapshot_tests(raw)
    m = P.assemble_rc_model("2026-10", {}, {}, {"ECS": raw}, rc=2)
    assert m["mrwp"]["ECS"]["tests"] is None
    assert R.rc_ui_gate(m)["blocking"]
    assert "Invalid source category evidence" in " ".join(m["problems"])


@pytest.mark.parametrize("legacy", [False, True])
def test_every_report_surface_separates_stress_but_preserves_gate_and_auth(legacy):
    m = model(legacy=legacy)
    gate, auth = R.rc_ui_gate(m), R.auth_report_gate(m)
    assert gate["coverage"] == "UI+Stress"
    assert (gate["ui_total"], gate["ui_passed"], gate["ui_failed"]) == (6, 4, 2)
    assert gate["blocking"] and not auth["blocking"]
    assert m["mrwp"]["Local"]["tests"]["categories"]["ui"]["failed"] == 0
    assert all(f["category"] == "stress" for f in m["ui_evidence"]["failures"])
    action = R.rc_next_action(m)
    plain = H.rc_email_plain(m, {}, gate, auth, action)
    html = unescape(H.rc_email_html(m, {}, gate, auth, action))
    cli = _format(m)
    for text in (plain, html, cli):
        assert "UI+Stress" in text and "Stress Tests" in text and TITLE in text
        assert "100.00% pass" in text
        assert "[UI automation] Stress Tests" not in text
    assert "[Stress Tests] " + STRESS in plain
    assert "[Stress Tests] " + STRESS in cli
    st = ReleaseState(release_id="2026-10")
    st.pipeline_runs = {"rcs": [{"ecs": m["mrwp"]["ECS"], "local": m["mrwp"]["Local"]}]}
    assert "UI+Stress 66.7%" in _rc_ui_summary(st)["text"]
    assert "UI+Stress" in H.rc_email_subject(m, gate, auth)


def test_threshold_edge_is_old_ui_plus_stress_not_new_ui_only():
    for passes, verdict in ((8, "attention"), (9, "warn")):
        m = model(ui_passes=passes)
        gate = R.rc_ui_gate(m)
        assert gate["verdict"] == verdict
        assert gate["pass_pct"] == round(100 * passes / (passes + 1), 1)
        assert not R.auth_report_gate(m)["blocking"]
    recovered = model(stress_outcome="Passed")
    assert R.rc_ui_gate(recovered)["verdict"] == "clean"


def test_stress_only_evidence_and_legacy_projection_remain_readable():
    raw = snapshot(201, {STRESS: [(TITLE, "Failed"), (TITLE, "Failed")]})
    rc = {"rc": 2, "ecs": raw, "local": snapshot(202, {STRESS: [("other", "Passed")]})}
    ok, projection, detail = P.project_mrwp_ui_results(rc)
    assert ok, detail
    assert len(projection["failures"]) == 1
    assert projection["failures"][0]["title"] == TITLE
    m = P.assemble_rc_model("2026-10", {}, {}, {"ECS": raw, "Local": rc["local"]}, rc=2)
    assert R.rc_ui_gate(m)["ui_total"] == 2
    assert R.rc_ui_gate(m)["verdict"] == "attention"


@pytest.mark.parametrize("tamper", [None, "missing", "counts"])
def test_capture_keeps_stress_coverage_required_and_failures_reportable(tamper):
    from steps.build_verify._mrwp import verify_mrwp
    from tests._context import context
    raw = snapshot(201, {STRESS: [(TITLE, "Failed"), (TITLE, "Failed")]})
    if tamper == "missing":
        del raw["tests"]["categories"]["stress"]
    elif tamper == "counts":
        raw["tests"]["categories"]["stress"].update(total=0, failed=0)
    ctx = context(ReleaseState(release_id="2026-10"), inputs={
        "rc": 2, "mrwp_id": "201", "tests": raw["tests"],
        "stages": [{"name": "Tests", "state": "completed", "result": "succeeded"}]})
    outcome = verify_mrwp(ctx, "ECS")
    assert outcome.kind == ("blocked" if tamper else "done")
    if tamper:
        assert "invalid UI+Stress results" in outcome.reason


def test_na_only_stress_stays_visible_without_changing_the_gate():
    m = model(stress_outcome="NotApplicable")
    gate, auth = R.rc_ui_gate(m), R.auth_report_gate(m)
    assert gate["verdict"] == "clean" and gate["ui_total"] == 4
    for text in (H.rc_email_plain(m, {}, gate, auth, R.rc_next_action(m)),
                 H.rc_email_html(m, {}, gate, auth, R.rc_next_action(m)), _format(m)):
        assert "Stress Tests" in text and "0 evaluated · 2 NA · rate unavailable" in text


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("corrupt", [None, "missing_failures", "lost_failure"])
def test_release_report_lightweight_only_omits_outer_failure_presentation(monkeypatch, legacy, corrupt):
    summaries = {}
    for build in (201, 202):
        captured = snapshot(build, {PROD: [("test_100_UI", "Passed")],
                                    STRESS: [(TITLE, "Failed"), (TITLE, "Failed")]})
        if legacy:
            captured = legacy_snapshot(captured)
        summaries[build] = captured["tests"]
        if corrupt == "missing_failures":
            del summaries[build]["failed_suites"]
        elif corrupt == "lost_failure":
            summaries[build]["failed_suites"] = []
    before = deepcopy(summaries)
    monkeypatch.setattr(P, "find_checker_runs", lambda *a: (
        True, [{"id": 10, "queueTime": "2026-10-17T01:00:00Z"}], ""))
    monkeypatch.setattr(P, "get_timeline", lambda *a: (
        True, [{"name": P.TRIGGER_JOB, "type": "Job", "result": "succeeded"}], ""))
    monkeypatch.setattr(P, "find_orchestrator_run", lambda *a: (True, {"id": 20, "tags": []}, ""))
    monkeypatch.setattr(P, "get_stages", lambda org, project, build, timeout: (
        True, [{"name": name, "state": "completed", "result": "succeeded"}
               for name in (P.ORCH_REQUIRED_STAGES if build == 20 else ("Tests",))], ""))
    monkeypatch.setattr(P, "mrwp_run_ids", lambda *a: (
        True, {"ECS": 201, "Local": 202, "rc": 2}, "", "tags"))
    reads = []
    def summary(org, project, build, timeout):
        reads.append(build)
        return True, deepcopy(summaries[build]), ""
    def forbidden_refetch(*a, **k):
        raise AssertionError("Failure evidence must come from the same complete summary acquisition")
    monkeypatch.setattr(P, "get_test_summary", summary)
    monkeypatch.setattr(P, "get_failed_tests", forbidden_refetch)
    full = P.release_report("O", "P", "2026-10", with_failed_tests=True)
    light = P.release_report("O", "P", "2026-10", with_failed_tests=False)
    assert reads == [201, 202, 201, 202]
    assert summaries == before
    for provider in ("ECS", "Local"):
        assert "failed_suites" in full["mrwp"][provider]
        assert "failed_suites" not in light["mrwp"][provider]
        assert full["mrwp"][provider]["tests"] == light["mrwp"][provider]["tests"]
        if corrupt:
            assert light["mrwp"][provider]["tests"] is None
            assert "Invalid source category evidence" in light["mrwp"][provider]["tests_error"]
        else:
            tests = light["mrwp"][provider]["tests"]
            assert tests["categories"]["stress"]["failed"] == 1
            assert tests["categories"]["ui"]["failed"] == 0
            assert tests["failed_suites"] == full["mrwp"][provider]["failed_suites"]
            validate_snapshot_tests({**light["mrwp"][provider],
                                     "failed_suites": tests["failed_suites"]})
    full_gate, light_gate = R.rc_ui_gate(full), R.rc_ui_gate(light)
    for key in ("coverage", "ui_total", "ui_passed", "ui_failed", "threshold", "pass_pct",
                "verdict", "blocking"):
        assert full_gate.get(key) == light_gate.get(key)
    if corrupt:
        assert light_gate["verdict"] == "unavailable"
    else:
        assert light_gate["coverage"] == "UI+Stress" and light_gate["ui_total"] == 4
        assert light_gate["ui_failed"] == 2 and light_gate["pass_pct"] == 50
