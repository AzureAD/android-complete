"""Auth failure details belong in the Auth card, not only the general evidence appendix."""
from tests._context import context as _context
from copy import deepcopy
from html import unescape
import re

import pytest

from orchestrator.state import ReleaseState
from steps.build_verify import rc_report as R, _rc_report_rendering as H
from tests._auth_evidence import auth_snapshot
from tests._mrwp_evidence import current_rc, PROD
from tools import pipelines as P

E2E, MONTHLY = P.AUTH_UI_SUITES


def model(rows, broker_rows=None):
    st = ReleaseState(release_id="2026-09", owner_email="owner@example.test")
    rc = current_rc(rc=1, ecs=broker_rows)
    rc["auth"] = auth_snapshot(rows=rows)
    st.pipeline_runs = {"rcs": [rc]}
    return R.rc_report_model(_context(st))


def auth_section(m):
    html = H.rc_email_html(m, {}, R.rc_ui_gate(m), R.auth_report_gate(m), R.rc_next_action(m))
    return html.split("Authenticator ECS <span", 1)[1].split("<strong>Next:</strong>", 1)[0]


def test_auth_card_contains_every_failure_under_its_own_suite_with_source_links():
    m = model({E2E: [("test_100_e2e_failed", "Failed"), ("test_200_passed", "Passed")],
               MONTHLY: [("monthly_failed", "Failed")]})
    before = deepcopy(m)
    gates = (R.rc_ui_gate(m), R.auth_report_gate(m))
    card = auth_section(m)
    assert "test_100_e2e_failed" in card and "monthly_failed" in card
    assert "test_200_passed" not in card
    assert card.index("test_100_e2e_failed") < card.index("monthly_failed")
    assert "report-only; no test-plan case map" in card
    for failure in m["ui_evidence"]["failures"]:
        if failure["product"] == "Authenticator":
            for link in failure["links"]:
                assert link["url"] in unescape(card)
    assert m == before and (R.rc_ui_gate(m), R.auth_report_gate(m)) == gates


def test_no_failure_list_truncation_and_html_escaping():
    titles = [f"test_{1000+i}_failed_<value>&" for i in range(55)]
    m = model({E2E: [(t, "Failed") for t in titles], MONTHLY: [("monthly_pass", "Passed")]})
    card = auth_section(m)
    assert all(title in unescape(card) for title in titles)
    assert "55</strong> unresolved failing titles" in card
    assert card.count("<li ") == 55 and "<value>" not in card
    assert "more" not in card


def test_recovered_titles_are_not_mislabeled_as_unresolved_and_gate_rates_stay_execution_based():
    m = model({E2E: [("test_100_retry", "Failed"), ("test_100_retry", "Passed"),
                     ("test_200_failure", "Failed")],
               MONTHLY: [("monthly_retry", "Failed"), ("monthly_retry", "Passed")]})
    card = auth_section(m)
    assert "test_100_retry" not in card and "test_200_failure" in card
    assert "1</strong> unresolved failing titles" in card
    assert "monthly_retry" not in card
    assert "no unresolved failing titles after same-title retry reconciliation" in card.lower()
    assert "33.33%" in card and "50.00%" in card
    assert R.auth_report_gate(m)["blocking"]


def test_extra_captured_auth_suite_is_not_silently_omitted():
    m = model({E2E: [("test_100_passed", "Passed")], MONTHLY: [("monthly_passed", "Passed")],
               "Additional device suite": [("test_300_additional_failure", "Failed")]})
    card = auth_section(m)
    assert "Additional device suite" in card and "test_300_additional_failure" in card


def test_renderer_uses_prepared_facts_without_raw_capture_or_new_queries(monkeypatch):
    m = model({E2E: [("test_100_failed", "Failed")], MONTHLY: [("monthly_failed", "Failed")]})
    gate, auth, next_action = R.rc_ui_gate(m), R.auth_report_gate(m), R.rc_next_action(m)
    def forbidden(*args, **kwargs):
        raise AssertionError("Renderer must not collect, reconcile or project evidence")
    monkeypatch.setattr(P, "inspect_auth_ui_evidence", forbidden)
    monkeypatch.setattr(P, "collect_auth_ui_evidence", forbidden)
    del m["auth"]["test"]["evidence"]
    html = H.rc_email_html(m, {}, gate, auth, next_action)
    card = html.split("Authenticator ECS <span", 1)[1].split("UI tests ", 1)[0]
    assert "test_100_failed" in card and "monthly_failed" in card


def test_missing_prepared_evidence_is_not_presented_as_zero_failures():
    m = model({E2E: [("test_100_failed", "Failed")]})
    m.pop("ui_evidence")
    assert "failure evidence unavailable" in H.auth_failure_details_html(m)


def test_all_passing_suites_do_not_show_failure_groups():
    m = model({E2E: [("test_100_passed", "Passed")], MONTHLY: [("monthly_passed", "Passed")]})
    assert H.auth_failure_details_html(m) == ""


def test_failure_rendering_is_stable_if_fact_or_link_order_changes():
    m = model({E2E: [("test_100_z", "Failed"), ("test_200_a", "Failed"), ("test_100_z", "Failed")],
               MONTHLY: [("monthly_z", "Failed"), ("monthly_a", "Failed")]})
    expected = H.auth_failure_details_html(m)
    m["ui_evidence"]["failures"].reverse()
    for failure in m["ui_evidence"]["failures"]:
        failure["links"].reverse()
    assert H.auth_failure_details_html(m) == expected


def test_html_has_product_specific_header_and_no_duplicate_evidence_panel():
    m = model({E2E: [("test_100_auth_failure", "Failed")], MONTHLY: [("monthly_failure", "Failed")]},
              broker_rows={PROD: [("test_500_broker_failure", "Failed")]})
    html = H.rc_email_html(m, {}, R.rc_ui_gate(m), R.auth_report_gate(m), R.rc_next_action(m))
    assert ">Broker UI and Stress results (separate categories; combined RC gate)</p>" in html
    assert ">UI-automation results</p>" not in html
    assert "Source evidence / release-owner investigation" not in html
    for failure in m["ui_evidence"]["failures"]:
        assert unescape(html).count(failure["title"]) == 1
        product_section = html.split("Authenticator ECS <span", 1)[failure["product"] == "Authenticator"]
        for link in failure["links"]:
            assert link["url"] in unescape(product_section)


def _result_cell(html, leg):
    return html.split(f"data-result='{leg}'", 1)[1].split("</td>", 1)[0]


def test_header_keeps_all_three_legs_separate_without_averaging():
    m = model({
        E2E: [("test_100_pass", "Passed"), ("test_200_fail", "Failed")],
        MONTHLY: [(f"monthly_{i}", "Passed" if i < 9 else "Failed") for i in range(10)],
    }, broker_rows={PROD: [(f"test_{500+i}_broker", "Passed" if i < 3 else "Failed")
                          for i in range(4)]})
    before = deepcopy(m)
    gate, auth = R.rc_ui_gate(m), R.auth_report_gate(m)
    html = H.rc_email_html(m, {}, gate, auth, R.rc_next_action(m))
    ecs, local, auth_cell = (_result_cell(html, leg) for leg in ("mrwp-ecs", "mrwp-local", "auth-ecs"))
    assert "MRWP ECS" in ecs and ">75.0%</div>" in ecs and "1 failed / 4" in ecs
    assert "MRWP Local" in local and ">100.0%</div>" in local and "0 failed / 1" in local
    assert "Authenticator ECS" in auth_cell and ">50.0%</div>" in auth_cell
    assert "Worst suite: UIAutomator E2E Tests" in auth_cell
    assert "Checker" not in ecs + local + auth_cell
    assert "MRWP UI:" not in html
    subject = H.rc_email_subject(m, gate, auth)
    assert "MRWP ECS: 75.0% pass" in subject
    assert "MRWP Local: 100.0% pass" in subject
    assert "Auth ECS: HOLD (50.0% lowest-suite pass)" in subject
    plain = H.rc_email_plain(m, {}, gate, auth, R.rc_next_action(m))
    summary = plain.split("RESULTS (shown separately)", 1)[1].split("NEXT:", 1)[0]
    assert "MRWP ECS: 75.0% pass (UI+Stress); 25.0% fail" in summary
    assert "MRWP Local: 100.0% pass (UI+Stress); 0.0% fail" in summary
    assert "Authenticator ECS: HOLD" in summary
    assert m == before
    assert (R.rc_ui_gate(m), R.auth_report_gate(m)) == (gate, auth)


def test_auth_card_has_a_success_headline_and_both_suite_percentages():
    m = model({
        E2E: [("test_100_pass", "Passed"), ("test_200_fail", "Failed")],
        MONTHLY: [(f"monthly_{i}", "Passed" if i < 9 else "Failed") for i in range(10)],
    })
    card = auth_section(m)
    assert "data-auth-success-rate" in card
    assert ">50.0%</span>" in card
    assert "worst gated suite: UIAutomator E2E Tests" in card
    assert "50.00% pass &middot; 50.0% fail" in card
    assert "90.00% pass &middot; 10.0% fail" in card
    assert "not combined with MRWP or averaged across suites" in card
    assert "1/2" in card and "9/10" in card


@pytest.mark.parametrize("damage", ["missing", "zero", "invalid", "stale"])
def test_missing_provider_percent_is_unavailable_not_a_zero_failure_pass(damage):
    m = model({E2E: [("test_100_pass", "Passed")], MONTHLY: [("monthly_pass", "Passed")]})
    tests = m["mrwp"]["ECS"]["tests"]
    if damage == "missing":
        tests["categories"].pop("ui")
    elif damage == "zero":
        tests["categories"]["ui"] = {"total": 0, "passed": 0, "failed": 0}
    elif damage == "invalid":
        tests["categories"]["ui"]["passed"] = True
    else:
        tests["count_basis"] = "result_entries"
    gate, auth = R.rc_ui_gate(m), R.auth_report_gate(m)
    html = H.rc_email_html(m, {}, gate, auth, R.rc_next_action(m))
    cell = _result_cell(html, "mrwp-ecs")
    assert "unavailable" in cell and ">0.0%" not in cell
    assert "MRWP Local: 100.0% pass" in html
    assert "MRWP ECS: evidence unavailable" in H.rc_email_subject(m, gate, auth)


@pytest.mark.parametrize("damage", ["missing", "empty", "failed_build"])
def test_auth_percent_is_unavailable_when_a_required_suite_has_no_executed_results(damage):
    m = model({E2E: [("test_100_pass", "Passed")], MONTHLY: [("monthly_pass", "Passed")]})
    suite = m["auth"]["test"]["suites"][MONTHLY]
    if damage == "missing":
        suite["present"] = False
    elif damage == "empty":
        suite.update(passed=0, failed=0, total=0)
    else:
        m["auth"]["build"]["result"] = "failed"
        m["auth"]["test"] = None
    gate, auth = R.rc_ui_gate(m), R.auth_report_gate(m)
    html = H.rc_email_html(m, {}, gate, auth, R.rc_next_action(m))
    cell = _result_cell(html, "auth-ecs")
    assert "unavailable" in cell
    assert ">0.0%" not in cell
    assert "UI-automation success rate unavailable" in auth_section(m)


def test_all_passing_auth_has_a_green_full_success_headline():
    m = model({E2E: [("test_100_pass", "Passed")], MONTHLY: [("monthly_pass", "Passed")]})
    html = H.rc_email_html(m, {}, R.rc_ui_gate(m), R.auth_report_gate(m), R.rc_next_action(m))
    cell = _result_cell(html, "auth-ecs")
    assert "color:#067647;'>100.0%" in cell
    card = auth_section(m)
    assert "color:#067647;'>100.0%" in card
    assert card.count("100.00% pass &middot; 0.0% fail") == 2


def test_absent_auth_still_has_an_explicit_unavailable_third_result():
    m = model({E2E: [("test_100_pass", "Passed")], MONTHLY: [("monthly_pass", "Passed")]})
    m.pop("auth")
    gate, auth = R.rc_ui_gate(m), R.auth_report_gate(m)
    html = H.rc_email_html(m, {}, gate, auth, R.rc_next_action(m))
    assert "unavailable" in _result_cell(html, "auth-ecs")
    assert "Auth ECS: unavailable" in H.rc_email_subject(m, gate, auth)
    assert "Authenticator ECS: unavailable" in H.rc_email_plain(
        m, {}, gate, auth, R.rc_next_action(m))


def test_report_metadata_has_three_ordered_rows_with_links_on_the_correct_row():
    m = model({E2E: [("test_100_pass", "Passed")], MONTHLY: [("monthly_pass", "Passed")]})
    m["checker"]["run_id"] = "101"
    m["orchestrator"].update(
        run_id="102", parked=True,
        versions={"Common": "25.0.0", "Msal": "8.6.0", "Broker": "16.7.0"})
    gate, auth = R.rc_ui_gate(m), R.auth_report_gate(m)
    html = H.rc_email_html(m, {}, gate, auth, R.rc_next_action(m))
    metadata = html.split("data-report-metadata", 1)[1].split("</table>", 1)[0]
    rows = re.findall(r'<tr data-metadata="([^"]+)">(.*?)</tr>', metadata, re.DOTALL)
    assert [key for key, _ in rows] == ["checker", "orchestrator", "versions"]
    checker, orchestrator, versions = [text for _, text in rows]
    assert "Fired" in checker and "buildId=101" in checker and "buildId=102" not in checker
    assert "Healthy" in orchestrator and "Remove RC Tags" in orchestrator
    assert "buildId=102" in orchestrator and "buildId=101" not in orchestrator
    assert "Common 25.0.0, Msal 8.6.0, Broker 16.7.0" in versions and "<a " not in versions
    plain = H.rc_email_plain(m, {}, gate, auth, R.rc_next_action(m))
    lines = plain.split("PIPELINE HEALTH\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("  - Checker:") and "buildId=101" in lines[0]
    assert lines[1].startswith("  - Orchestrator:") and "buildId=102" in lines[1]
    assert lines[2] == "  - Versions: Common 25.0.0, Msal 8.6.0, Broker 16.7.0"


def test_success_headlines_and_all_mrwp_categories_use_pass_then_fail_rates():
    m = model({
        E2E: [(f"test_{100+i}_e2e", "Passed" if i < 22 else "Failed") for i in range(29)],
        MONTHLY: [("monthly_pass", "Passed")],
    })
    counts = {
        "unit": {"passed": 4, "failed": 1, "total": 5},
        "instrumented": {"passed": 2, "failed": 1, "total": 3},
        "ui": {"passed": 22, "failed": 7, "total": 29},
        "stress": {"passed": 0, "failed": 0, "total": 0},
    }
    for provider in ("ECS", "Local"):
        m["mrwp"][provider]["tests"]["categories"] = deepcopy(counts)
    before = deepcopy(m)
    gate, auth = R.rc_ui_gate(m), R.auth_report_gate(m)
    html = H.rc_email_html(m, {}, gate, auth, R.rc_next_action(m))
    for leg in ("mrwp-ecs", "mrwp-local", "auth-ecs"):
        cell = _result_cell(html, leg)
        label = "UI-automation" if leg == "auth-ecs" else "UI+Stress"
        assert ">75.9%</div>" in cell and label + " success rate" in cell
    assert "UI-automation failure rate" not in html
    for provider, end in (("ECS", "MRWP Local"), ("Local", "Authenticator ECS <span")):
        card = html.split(f">MRWP {provider}<span", 1)[1].split(end, 1)[0]
        assert ">75.9%</span>" in card
        for text in ("80.00% pass &middot; 20.0% fail",
                     "66.67% pass &middot; 33.3% fail",
                     "75.86% pass &middot; 24.1% fail"):
            assert text in card
    assert ">75.9%</span>" in auth_section(m)
    plain = H.rc_email_plain(m, {}, gate, auth, R.rc_next_action(m))
    for text in ("80.00% pass · 20.0% fail", "66.67% pass · 33.3% fail",
                 "75.86% pass · 24.1% fail"):
        assert text in plain
    assert m == before
    assert (R.rc_ui_gate(m), R.auth_report_gate(m)) == (gate, auth)
