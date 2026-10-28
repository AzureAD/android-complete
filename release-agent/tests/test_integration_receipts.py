"""Provider contract tests for exhaustive recovery reads (no credentials/network)."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tools import integration_receipts as R


STARTED = "2026-10-21T17:38:00+00:00"
GH = {"tool": "gh", "gh_repo": "AzureAD/common", "head": "working/release/1", "base": "release/1"}
ADO = {"tool": "ado", "ado": {"org": "https://msazure.visualstudio.com/",
                             "project": "One", "repository": "android"},
       "head": "ri/1", "base": "dev"}


def github():
    return {"number": 3319, "html_url": "https://github.com/AzureAD/common/pull/3319",
            "title": "Working/release/1", "body": "AB#101", "labels": [{"name": "skip"}],
            "head": {"ref": GH["head"], "sha": "a" * 40, "repo": {"full_name": "AzureAD/common"}},
            "base": {"ref": GH["base"], "repo": {"full_name": "AzureAD/common"}},
            "state": "open", "merged_at": None, "draft": False, "created_at": STARTED}


def azure():
    return {"pullRequestId": 123, "sourceRefName": "refs/heads/ri/1", "targetRefName": "refs/heads/dev",
            "repository": {"name": "android", "project": {"name": "One"}},
            "status": "active", "isDraft": False, "title": "Integration", "description": "AB#101",
            "lastMergeSourceCommit": {"commitId": "a" * 40}, "creationDate": STARTED}


def test_github_receipts_paginate_all_states_and_pin_source_repository(monkeypatch):
    calls = []
    def read(args):
        calls.append(args)
        return [[github()], []]
    monkeypatch.setattr(R, "_json", read)
    receipt = R.pr_receipt(GH)
    assert receipt["head_tip"] == "a" * 40 and receipt["labels"] == ["skip"]
    assert "--paginate" in calls[0] and "--slurp" in calls[0]
    assert "state=all" in calls[0][-1] and "per_page=100" in calls[0][-1]


@pytest.mark.parametrize("change", ["closed", "merged", "draft", "fork", "base", "head", "ambiguous"])
def test_github_wrong_identity_state_or_ambiguous_history_holds(monkeypatch, change):
    value = github()
    if change == "closed":
        value["state"] = "closed"
    elif change == "merged":
        value["merged_at"] = STARTED
    elif change == "draft":
        value["draft"] = True
    elif change == "fork":
        value["head"]["repo"]["full_name"] = "attacker/common"
    elif change in ("head", "base"):
        value[change]["ref"] = "other"
    values = [[value, deepcopy(value)]] if change == "ambiguous" else [[value]]
    monkeypatch.setattr(R, "_json", lambda args: values)
    with pytest.raises(ValueError):
        R.pr_receipt(GH)


def test_github_merged_receipt_retains_exact_merge_identity(monkeypatch):
    value = github()
    value.update(state="closed", merged_at=STARTED, merge_commit_sha="b" * 40)
    monkeypatch.setattr(R, "_json", lambda args: [[value]])
    receipt = R.pr_receipt(GH)
    assert receipt["state"] == "merged"
    assert receipt["merge_tip"] == "b" * 40
    assert receipt["merged_at"] == STARTED
    value["merge_commit_sha"] = None
    with pytest.raises(ValueError):
        R.pr_receipt(GH)


def test_azure_receipt_checks_active_source_and_work_item_link(monkeypatch):
    calls = []
    def read(args):
        calls.append(args)
        return [{"id": 101}] if "work-item" in args else [azure()]
    monkeypatch.setattr(R, "_json", read)
    receipt = R.pr_receipt(ADO)
    assert receipt["work_items"] == ["101"] and receipt["repository"] == ADO["ado"]
    assert "--status" in calls[0] and "all" in calls[0]
    assert "--top" in calls[0] and "1000" in calls[0]
    assert calls[1][:6] == ["az", "repos", "pr", "work-item", "list", "--org"]


@pytest.mark.parametrize("change", ["abandoned", "draft", "project", "repo", "source", "ambiguous", "cap"])
def test_azure_history_and_identity_mismatches_hold(monkeypatch, change):
    value = azure()
    if change == "abandoned":
        value["status"] = "abandoned"
    elif change == "draft":
        value["isDraft"] = True
    elif change == "project":
        value["repository"]["project"]["name"] = "Other"
    elif change == "repo":
        value["repository"]["name"] = "other"
    elif change == "source":
        value["sourceRefName"] = "refs/heads/other"
    values = [value] * (1000 if change == "cap" else 2 if change == "ambiguous" else 1)
    monkeypatch.setattr(R, "_json", lambda args: values)
    with pytest.raises(ValueError):
        R.pr_receipt(ADO)


def test_missing_pr_is_absence_only_after_successful_complete_provider_read(monkeypatch):
    monkeypatch.setattr(R, "_json", lambda args: [[]])
    assert R.pr_receipt(GH) is None
    monkeypatch.setattr(R, "_json", lambda args: [])
    assert R.pr_receipt(ADO) is None
    def failed(args):
        raise ValueError("permission denied")
    monkeypatch.setattr(R, "_json", failed)
    with pytest.raises(ValueError, match="permission denied"):
        R.pr_receipt(GH)


@pytest.mark.parametrize("change", [None, "title", "type", "project", "old", "ambiguous", "missing"])
def test_pbi_requires_exact_type_title_project_creation_and_unique_source(monkeypatch, change):
    target = {"org": "https://identitydivision.visualstudio.com", "project": "Engineering"}
    fields = {"System.Title": "Android release — integration", "System.WorkItemType": "Product Backlog Item",
              "System.TeamProject": "Engineering", "System.CreatedDate": STARTED}
    value = {"id": 101, "url": target["org"] + "/_apis/wit/workItems/101", "fields": fields}
    if change in ("title", "type", "project"):
        fields[{"title": "System.Title", "type": "System.WorkItemType",
                "project": "System.TeamProject"}[change]] = "other"
    elif change == "old":
        fields["System.CreatedDate"] = "2026-10-20T00:00:00+00:00"
    matches = [{"id": 101}, {"id": 102}] if change == "ambiguous" else [] if change == "missing" else [{"id": 101}]
    def read(args):
        assert target["org"] in args
        return value if "show" in args else matches
    monkeypatch.setattr(R, "_json", read)
    if change:
        with pytest.raises(ValueError):
            R.pbi_receipt(target, "101", "Android release — integration", STARTED)
    else:
        receipt = R.pbi_receipt(target, "101", "Android release — integration", STARTED)
        assert receipt["id"] == "101" and receipt["title"] == fields["System.Title"]


def test_provider_reader_forces_utf8_and_never_replaces_unreadable_bytes(monkeypatch):
    monkeypatch.setattr(R.sys, "platform", "linux")
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"title":"release — integration"}'.encode(), stderr=b"")
    monkeypatch.setattr(R, "find_azure_cli", lambda: "az.cmd")
    monkeypatch.setattr(R.subprocess, "run", run)
    assert R._json(["az", "boards", "query"])["title"] == "release — integration"
    assert calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"
    monkeypatch.setattr(R.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b'{"title":"\x97"}'))
    with pytest.raises(UnicodeDecodeError):
        R._json(["az", "boards", "query"])


def test_windows_azure_cli_uses_strict_native_encoding_when_utf8_is_unavailable(monkeypatch):
    monkeypatch.setattr(R.sys, "platform", "win32")
    monkeypatch.setattr(R.locale, "getencoding", lambda: "cp1252")
    monkeypatch.setattr(R, "find_azure_cli", lambda: "az.cmd")
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=b'{"title":"release \x97 integration"}', stderr=b""))
    assert R._json(["az", "boards", "query"])["title"] == "release — integration"
    with pytest.raises(UnicodeDecodeError):
        R._json(["gh", "api", "example"])
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=b'{"title":"\x81"}', stderr=b""))
    with pytest.raises(UnicodeDecodeError):
        R._json(["az", "boards", "query"])
