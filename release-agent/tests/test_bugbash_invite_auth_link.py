"""Invite Auth pipeline resolves the current ECS build, never TBD or the UI-test run."""
from copy import deepcopy
from html import escape

import pytest

from steps.bug_bash import send_invite as S
from steps.lib import mockctx
from tools import invite as I
from tools.pipelines.auth_app import auth_build_url
from tests._harness import _invite_state


def render(state):
    with mockctx.active({"now": "2026-09-11T10:00:00-07:00", "flags": "{}"}):
        return S.build(state)


def test_invite_uses_latest_rc_ecs_build_not_previous_or_post_build_tests():
    state = _invite_state()
    previous = deepcopy(state.pipeline_runs["rcs"][-1])
    current = deepcopy(previous)
    current["rc"] = 2
    current["auth"]["build"] = {"run_id": "401", "rc": 2}
    current["auth"]["test"] = {"run_id": "402"}
    state.pipeline_runs["rcs"] = [previous, current]
    before = deepcopy(state)
    result = render(state)
    assert result.kind == "needs_skill"
    body = result.payload["body"]
    assert f'href="{escape(auth_build_url(401), quote=True)}"' in body
    assert "buildId=301" not in body and "buildId=402" not in body
    assert "TBD" not in body and "{{AUTH_PIPELINE_URL}}" not in body
    assert state == before


@pytest.mark.parametrize("build", [
    {}, {"run_id": None, "rc": 1}, {"run_id": 0, "rc": 1}, {"run_id": True, "rc": 1},
    {"run_id": '123" onclick="oops', "rc": 1}, {"run_id": 301}, {"run_id": 301, "rc": 2},
])
def test_missing_or_stale_auth_build_blocks_before_flags_read(monkeypatch, build):
    state = _invite_state()
    state.pipeline_runs["rcs"][-1]["auth"]["build"] = build
    monkeypatch.setattr(I, "local_flights", lambda *a: pytest.fail("No flags read for invalid Auth metadata"))
    result = S.build(state)
    assert result.kind == "blocked" and "auth_ecs" in result.reason


def test_missing_current_auth_does_not_fall_back_to_previous_rc():
    state = _invite_state()
    state.pipeline_runs["rcs"].append({"rc": 2, "ecs": {"run_id": 201}, "local": {"run_id": 202}})
    result = render(state)
    assert result.kind == "blocked" and not hasattr(result, "payload")


def test_standalone_invitation_declares_utf8_before_non_ascii_header():
    with mockctx.active({"now": "2026-09-11T14:59:38.059-07:00", "flags": "{}"}):
        result = S.build(_invite_state())
    assert result.kind == "needs_skill"
    body = result.payload["body"]
    assert body.encode("utf-8")[:1024].startswith(b'<meta charset="utf-8">')
    assert "Friday, Sep 11 \u00b7 5:00 PM\u20137:00 PM (America/Los_Angeles, UTC-07:00)" in body
    assert "\u00c2\u00b7" not in body and "\u00e2\u20ac\u201c" not in body
