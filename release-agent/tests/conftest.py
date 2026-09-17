"""Shared pytest setup for the Release Orchestrator tests.

Adds the package root to sys.path (tests import `orchestrator.*` / `tools.*` / `steps.*`)
and installs an AUTOUSE network guard: the real ADO/az primitives in `tools.pipelines`
are replaced with a raiser, so any test that reaches a live network call FAILS LOUDLY
with a clear message instead of hanging on `az`. Tests that need controlled responses
monkeypatch these primitives themselves (e.g. `P._ado_rest_get = fake`), which overrides
the guard for the duration of that test.
"""
from __future__ import annotations

import os
import shlex
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # release-agent/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from orchestrator.revision import StaticRevisionProvider, use_revision_provider

pytest_plugins = ("tests._timeout", "tests._suite")
_STATIC_REVISION_PROVIDER = StaticRevisionProvider.capture()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_revision: use content-based runtime identity instead of the static unit-test provider",
    )
    config.addinivalue_line(
        "markers",
        "git_integration: builds real local Git repositories and exercises Git subprocesses",
    )


@pytest.fixture(autouse=True)
def _unit_revision_provider(request):
    if request.node.get_closest_marker("real_revision"):
        yield
        return
    with use_revision_provider(_STATIC_REVISION_PROVIDER):
        yield


def _offline_process(run, command, *args, **kwargs):
    tokens = shlex.split(command, posix=False) if isinstance(command, str) else list(command or ())
    if not tokens:
        raise RuntimeError("test attempted a REAL shell command; mock the provider")
    executable = os.path.basename(str(tokens[0]).strip('"')).lower()
    if executable in ("az", "az.cmd", "az.exe", "gh", "gh.exe",
                      "workiq", "workiq.cmd", "workiq.exe", "curl", "curl.exe"):
        raise RuntimeError("test attempted a REAL provider CLI; mock the provider")
    if executable in ("git", "git.exe"):
        kwargs["env"] = {**(kwargs.get("env") or os.environ), "GIT_ALLOW_PROTOCOL": "file"}
    return run(command, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Block real ADO/az calls in tests. Any un-mocked network access raises with a hint
    naming what to patch — this is what turns an accidental live call into a fast, clear
    failure instead of a multi-minute hang."""
    from tools import pipelines as P
    import socket
    import subprocess

    native_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda command, *args, **kwargs:
                        _offline_process(native_run, command, *args, **kwargs))
    import subprocess
    import shlex
    from urllib.parse import urlsplit

    def _blocked(*_a, **_k):
        raise RuntimeError(
            "test attempted a REAL ADO/az network call — mock it "
            "(patch tools.pipelines._ado_rest_get / _ado_rest_get_text / _az_json, "
            "or inject the step's input mocks).")

    monkeypatch.setattr(P, "_ado_rest_get", _blocked)
    monkeypatch.setattr(P, "_ado_rest_get_h", _blocked)
    monkeypatch.setattr(P, "_ado_rest_get_text", _blocked)
    monkeypatch.setattr(P, "_ado_rest_send", _blocked)
    monkeypatch.setattr(P, "_az_json", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    popen = subprocess.Popen

    def network_target(value):
        value = str(value).strip('"')
        if "://" in value:
            url = urlsplit(value)
            return url.scheme != "file" or url.hostname not in (None, "", "localhost")
        return "@" in value or value.startswith(("\\\\", "//"))

    def offline_popen(args, *positional, **kwargs):
        tokens = shlex.split(args, posix=False) if isinstance(args, str) else list(args)
        names = [os.path.basename(str(token).strip('"')).lower()
                 .removesuffix(".exe").removesuffix(".cmd").removesuffix(".bat")
                 for token in tokens]
        if any(name in {"az", "gh", "curl", "wget", "workiq"} for name in names):
            return _blocked()
        if names and names[0] == "git":
            for operation in ("clone", "fetch", "push", "pull", "ls-remote"):
                if operation not in names:
                    continue
                if any(network_target(token) for token in tokens):
                    return _blocked()
                if operation != "clone":
                    cwd = kwargs.get("cwd")
                    prefix = tokens[1:names.index(operation)]
                    with popen(["git", *prefix, "remote", "-v"], cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True) as process:
                        remotes, _ = process.communicate()
                    if any(network_target(line.split("\t", 1)[-1].rsplit(" (", 1)[0])
                           for line in remotes.splitlines()):
                        return _blocked()
        return popen(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", offline_popen)
    # tools.distribution has its own Graph + WIQL + write primitives — block those too.
    try:
        from tools import distribution as Dm
        for fn in ("_graph_get", "_graph_token", "_ado_wiql", "set_assigned_to"):
            monkeypatch.setattr(Dm, fn, _blocked)
    except Exception:
        pass
    yield
