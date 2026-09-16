"""Mock-input context — lets a step's build() read canned test inputs.

A step DECLARES the properties worth mocking in its `MOCKABLE` spec. Two kinds:

  kind: payload  → rewrites a field of the NeedsSkill payload (applied generically
                   by `step-action`, e.g. notice `send_to` → payload.to).
  kind: input    → a value the step's build() reads to REPLACE a real input, so the
                   step's genuine decision logic runs on your data (e.g. cg `alerts`).

For input knobs, whoever runs the step (the engine for agent steps, `step-action`
for scout steps) wraps the call in `active(spec)`, and the step reads a value with
`mock_input("name")`. Uses a contextvar so nothing leaks onto the release state.
"""
from __future__ import annotations

import contextlib
import contextvars

_current: contextvars.ContextVar = contextvars.ContextVar("mock_spec", default={})

# Engine-level / applier-level keys that are NOT step inputs.
RESERVED = {"outcome", "note", "reason"}


@contextlib.contextmanager
def active(spec: dict | None):
    """Make `spec` (the mock entry for the step about to run) the current source of
    mocked inputs for the duration of the block."""
    token = _current.set(spec or {})
    try:
        yield
    finally:
        _current.reset(token)


def mock_input(name: str, default=None):
    """Return the mocked value for `name` from the active mock entry, or `default`
    when it isn't set. A `MISSING` sentinel lets callers detect 'not mocked'."""
    return _current.get().get(name, default)


class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING = _Missing()
