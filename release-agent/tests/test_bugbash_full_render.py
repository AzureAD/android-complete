"""Every workload case stays visible with product attribution and live resolved state."""
from copy import deepcopy
import re

import pytest

from tools import bugbash as B
from tests.test_bugbash_broker_triage import receipt, row, stub, gather

OWNER = "owner@example.test"
PEOPLE = {OWNER: {"id": "11111111-1111-1111-1111-111111111111", "name": "Praveen"}}


def test_one_of_seven_includes_all_seven_rows_with_the_pass_checked(monkeypatch):
    manual = [row(i, i, outcome="Unspecified") for i in range(1, 7)]
    # The title deliberately says Broker; attribution must use the Auth source plan.
    auth = [{**row(7, 7, outcome="Passed"), "name": "Broker-related registration"}]
    stub(monkeypatch, [], auth=auth, manual=manual)
    ok, progress, _ = gather(receipt([]))
    assert ok and (progress["done"], progress["total"], progress["remaining"]) == (1, 7, 6)
    html, mentions = B.render_update(progress, "October 2026", [], PEOPLE)
    rows = re.findall(r"<li>(.*?)</li>", html)
    assert len(rows) == 7
    assert sum(r.startswith("✅") for r in rows) == 1
    assert all("[Broker]" in r for r in rows[:6])
    assert "[Authenticator]" in rows[6] and "Passed — resolved" not in rows[6]
    assert "Broker-related registration" in rows[6]
    assert "1/7 done" in html and len(mentions) == 1


def test_completed_owner_keeps_all_rows_without_a_mention(monkeypatch):
    stub(monkeypatch, [], manual=[row(1, 1, outcome="Passed")],
         auth=[row(2, 2, outcome="NotApplicable")])
    ok, progress, _ = gather(receipt([]))
    assert ok
    html, mentions = B.render_update(progress, "October", [], {})
    rows = re.findall(r"<li>(.*?)</li>", html)
    assert len(rows) == 2 and rows[0].startswith("✅") and rows[1].startswith("➖")
    assert all("(Passed" not in r and "(N/A" not in r for r in rows)
    assert "2/2 done" in html and "all 2 tests completed" in html
    assert not mentions and "<at" not in html


def test_resolved_triage_stays_visible_but_is_not_rendered_as_an_open_failure(monkeypatch):
    original = [row(1, 10), row(2, 20)]
    live = [row(1, 10, outcome="Passed"), row(2, 20)]
    stub(monkeypatch, live, manual=[row(3, 30, outcome="Unspecified")])
    ok, progress, _ = gather(receipt(original))
    assert ok
    html, _ = B.render_update(progress, "October", [], PEOPLE)
    rows = re.findall(r"<li>(.*?)</li>", html)
    assert len(rows) == 3
    assert rows[0].startswith("❌") and ">20</a>" in rows[0]
    assert rows[1].startswith("⬜") and ">30</a>" in rows[1]
    assert rows[2].startswith("✅") and ">10</a>" in rows[2]
    assert "[Broker]" in rows[2] and "(Automation triage)" in rows[2]
    assert "Passed" not in rows[2] and "resolved" not in rows[2]
    assert progress["auto_failed_remaining"] == 1


def test_shared_case_labels_both_sources_without_double_counting(monkeypatch):
    stub(monkeypatch, [], manual=[row(1, 10, outcome="Passed")],
         auth=[row(2, 10, outcome="Passed")])
    ok, progress, _ = gather(receipt([]))
    assert ok and progress["total"] == 1
    test = progress["owners"][OWNER]["tests"][0]
    assert test["products"] == ["Authenticator", "Broker"]
    html, _ = B.render_update(progress, "October", [], {})
    assert html.count("<li>") == 1 and "[Authenticator / Broker]" in html


@pytest.mark.parametrize("product", [None, [], ["Unknown"], "Broker"])
def test_missing_source_never_guesses_product_from_title_or_id(monkeypatch, product):
    stub(monkeypatch, [], manual=[row(1, 10, outcome="Passed")])
    ok, progress, _ = gather(receipt([]))
    assert ok
    progress["owners"][OWNER]["tests"][0]["products"] = product
    with pytest.raises(ValueError, match="source product"):
        B.render_update(progress, "October", [], {})


def test_full_row_render_escapes_title_and_preserves_input(monkeypatch):
    live = [{**row(1, 10, outcome="Passed"), "name": '<script>alert("Broker")</script>'}]
    stub(monkeypatch, [], manual=live)
    _, progress, _ = gather(receipt([]))
    before = deepcopy(progress)
    html, _ = B.render_update(progress, "October", [], {})
    assert "<script>" not in html and "&lt;script&gt;" in html and "[Broker]" in html
    assert progress == before


@pytest.mark.parametrize("outcome,icon", [
    ("Passed", "✅"), ("NotApplicable", "➖"), ("Failed", "❌"), ("Blocked", "⛔"), ("Unspecified", "⬜")])
def test_status_appears_only_as_leading_icon_with_a_single_report_legend(monkeypatch, outcome, icon):
    stub(monkeypatch, [], manual=[row(1, 10, outcome=outcome)])
    _, progress, _ = gather(receipt([]))
    html, _ = B.render_update(progress, "October", [], PEOPLE)
    rows = re.findall(r"<li>(.*?)</li>", html)
    assert rows == [f'{icon} <b>[Broker]</b> <a href="{B.ORG}/{B.PROJECT}/_workitems/edit/10">10</a> — Case 10']
    assert html.count("✅ Passed · ➖ N/A · ❌ Failed · ⛔ Blocked · ⬜ Not run") == 1


def test_status_suffix_removal_does_not_strip_parentheses_from_ado_title(monkeypatch):
    stub(monkeypatch, [], manual=[{**row(1, 10, outcome="Passed"), "name": "Login (Blocked)"}])
    _, progress, _ = gather(receipt([]))
    html, _ = B.render_update(progress, "October", [], {})
    assert re.findall(r"<li>(.*?)</li>", html)[0].endswith("Login (Blocked)")
