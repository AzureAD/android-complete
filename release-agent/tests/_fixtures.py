"""Isolated unit-test step-input fixture context (`tests/` only — never imported in production)."""
from __future__ import annotations

import contextlib
import contextvars

from orchestrator.step_context import MISSING

_current: contextvars.ContextVar[dict] = contextvars.ContextVar("test_step_inputs", default={})


@contextlib.contextmanager
def active(spec: dict | None):
    """Bind `spec` as the active unit-test input map for `tests._context.invoke(...)`."""
    token = _current.set(dict(spec or {}))
    try:
        yield
    finally:
        _current.reset(token)


def mock_input(name: str, default=None):
    return _current.get().get(name, default)
