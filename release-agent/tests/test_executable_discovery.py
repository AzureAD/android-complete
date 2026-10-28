"""Background hosts can retain the old x86 CLI directory after a 64-bit upgrade."""
from types import SimpleNamespace

import pytest

from tools import executables as E


@pytest.fixture
def no_azure_path(monkeypatch):
    monkeypatch.setattr(E, "WINDOWS", True)
    monkeypatch.setattr(E.shutil, "which", lambda name: None)
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.delenv(variable, raising=False)


def install(root):
    cli = root / "Microsoft SDKs" / "Azure" / "CLI2" / "wbin" / "az.cmd"
    cli.parent.mkdir(parents=True)
    cli.write_text("@echo off", encoding="ascii")
    return str(cli)


@pytest.mark.parametrize("variable", ["ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"])
def test_standard_windows_install_without_path(no_azure_path, monkeypatch, tmp_path, variable):
    expected = install(tmp_path)
    monkeypatch.setenv(variable, str(tmp_path))
    assert E.find_azure_cli() == expected


def test_stale_x86_path_does_not_hide_64bit_install(no_azure_path, monkeypatch, tmp_path):
    x64 = tmp_path / "Program Files"
    expected = install(x64)
    monkeypatch.setenv("ProgramFiles", str(x64))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "missing x86"))
    assert E.find_azure_cli() == expected


def test_path_executable_retains_precedence(no_azure_path, monkeypatch, tmp_path):
    install(tmp_path)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(E.shutil, "which", lambda name: "preferred-cli" if name == "az" else None)
    assert E.find_azure_cli() == "preferred-cli"


def test_az_cmd_on_windows_path_is_supported(no_azure_path, monkeypatch):
    monkeypatch.setattr(E.shutil, "which", lambda name: "path-cli.cmd" if name == "az.cmd" else None)
    assert E.find_azure_cli() == "path-cli.cmd"


def test_missing_install_returns_none(no_azure_path, monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert E.find_azure_cli() is None


def test_directory_named_az_cmd_is_not_executable(no_azure_path, monkeypatch, tmp_path):
    candidate = tmp_path / "Microsoft SDKs" / "Azure" / "CLI2" / "wbin" / "az.cmd"
    candidate.mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert E.find_azure_cli() is None


def test_non_windows_does_not_guess_windows_paths(no_azure_path, monkeypatch, tmp_path):
    install(tmp_path)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(E, "WINDOWS", False)
    assert E.find_azure_cli() is None


def test_pipeline_commands_use_resolved_path_without_changing_arguments(no_azure_path, monkeypatch, tmp_path):
    from tools.pipelines import _rest as R

    expected = install(tmp_path)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"id": 1705640}', stderr="")

    monkeypatch.setattr(R.subprocess, "run", run)
    ok, result, detail = R._az_json(["pipelines", "build", "show", "--id", "1705640"], 30)
    assert ok and result["id"] == 1705640 and detail == ""
    assert calls == [[expected, "pipelines", "build", "show", "--id", "1705640", "-o", "json"]]


def test_pr_helpers_resolve_literal_az_but_preserve_gh(no_azure_path, monkeypatch, tmp_path):
    from tools import prs

    expected = install(tmp_path)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    calls = []
    monkeypatch.setattr(prs.subprocess, "run", lambda args, **kwargs: (
        calls.append(args) or SimpleNamespace(returncode=0, stdout="[]", stderr="")))
    az_args = ["az", "repos", "pr", "list", "--project", "One"]
    assert prs._run(az_args)[0] == 0
    assert az_args[0] == "az"
    assert calls[-1] == [expected, *az_args[1:]]
    gh_args = ["gh", "pr", "list"]
    assert prs._run(gh_args)[0] == 0
    assert calls[-1] == gh_args


def test_missing_cli_is_reported_without_starting_pr_process(no_azure_path, monkeypatch):
    from tools import prs

    monkeypatch.setattr(prs.subprocess, "run", lambda *a, **k: pytest.fail("Missing CLI must not execute"))
    assert prs._run(["az", "repos", "pr", "list"]) == (1, "", "az CLI not found")
