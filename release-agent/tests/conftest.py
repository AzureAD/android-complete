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
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # release-agent/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Block real ADO/az calls in tests. Any un-mocked network access raises with a hint
    naming what to patch — this is what turns an accidental live call into a fast, clear
    failure instead of a multi-minute hang."""
    from tools import pipelines as P

    def _blocked(*_a, **_k):
        raise RuntimeError(
            "test attempted a REAL ADO/az network call — mock it "
            "(patch tools.pipelines._ado_rest_get / _ado_rest_get_text / _az_json, "
            "or inject the step's input mocks).")

    monkeypatch.setattr(P, "_ado_rest_get", _blocked)
    monkeypatch.setattr(P, "_ado_rest_get_text", _blocked)
    monkeypatch.setattr(P, "_az_json", _blocked)
    yield
