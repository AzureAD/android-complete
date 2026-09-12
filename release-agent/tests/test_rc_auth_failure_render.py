"""Auth failure details belong in the Auth card, not only the general evidence appendix."""
from copy import deepcopy
from html import unescape

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
    return R.rc_report_model(st)


def auth_section(m):
    html = H.rc_email_html(m, {}, R.rc_ui_gate(m), R.auth_report_gate(m), R.rc_next_action(m))
    return html.split("Authenticator ECS <span", 1)[1].split("UI tests ", 1)[0]


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
    assert "no unresolved failing titles after same-title retry reconciliation" in card
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
    assert ">Broker UI-automation results</p>" in html
    assert ">UI-automation results</p>" not in html
    assert "Source evidence / release-owner investigation" not in html
    for failure in m["ui_evidence"]["failures"]:
        assert unescape(html).count(failure["title"]) == 1
        product_section = html.split("Authenticator ECS <span", 1)[failure["product"] == "Authenticator"]
        for link in failure["links"]:
            assert link["url"] in unescape(product_section)
