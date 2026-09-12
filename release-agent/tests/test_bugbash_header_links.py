"""Header pipeline links come from the saved current RC, not discovery or agent text."""
from argparse import Namespace
from copy import deepcopy
from html import escape
import json

import pytest

from orchestrator import cli_common as C
from orchestrator.commands import bugbash_update
from steps.bug_bash import bugbash_updates as U
from steps.build_verify._common import build_url
from steps.lib import mockctx
from tools import testplans as T
from tools.coordinates import _Coords, coords
from tools.pipelines.auth_app import auth_build_url
from tests.test_bugbash_mentions import people, progress, state


def test_header_contains_run_destinations_and_test_account_portal():
    st = state()
    current = st.pipeline_runs["rcs"][-1]
    older = deepcopy(current)
    older["ecs"]["run_id"], older["local"]["run_id"], older["auth"]["test"]["run_id"] = 11, 12, 13
    st.pipeline_runs["rcs"].insert(0, older)
    before = deepcopy(st.pipeline_runs)
    assert U.plan_links(st) == [
        {"name": "Broker test plan", "url": T.plan_web_url(3730001)},
        {"name": "Authenticator suite", "url": T.plan_web_url(T.AUTH_PLAN, 3730002)},
        {"name": "MRWP · ECS run", "url": build_url(201)},
        {"name": "MRWP · Local run", "url": build_url(202)},
        {"name": "Auth pipeline", "url": auth_build_url(302)},
        {"name": "Get test accounts", "url": coords.link("test_accounts")},
    ]
    assert st.pipeline_runs == before
    # The Auth link is the test execution pipeline, not its upstream APK build.
    assert "buildId=301" not in str(U.plan_links(st))


def test_account_portal_is_read_from_config_and_missing_key_fails_clearly(tmp_path, monkeypatch):
    config = tmp_path / "coordinates.yaml"
    config.write_text("links:\n  test_accounts: https://example.test/accounts\n", encoding="utf-8")
    monkeypatch.setattr(U, "coords", _Coords(str(config)))
    assert U.plan_links(state())[-1] == {"name": "Get test accounts", "url": "https://example.test/accounts"}
    with pytest.raises(KeyError, match="links.missing_portal"):
        U.coords.link("missing_portal")
    config.write_text("links: {}\n", encoding="utf-8")
    monkeypatch.setattr(U, "coords", _Coords(str(config)))
    with pytest.raises(KeyError, match="links.test_accounts"):
        U.plan_links(state())


@pytest.mark.parametrize("path", [("ecs",), ("local",), ("auth", "test")])
@pytest.mark.parametrize("bad_id", [None, "", 0, True, "not-an-id"])
def test_missing_run_metadata_blocks_without_sending_a_guessed_link(path, bad_id):
    st = state()
    node = st.pipeline_runs["rcs"][-1]
    for key in path:
        node = node[key]
    node["run_id"] = bad_id
    with mockctx.active({"progress": progress(), "people": people()}):
        outcome = U.build(st)
    assert outcome.kind == "blocked" and "current RC run ID" in outcome.reason


def test_empty_rc_list_cannot_reuse_an_old_header():
    st = state()
    st.pipeline_runs = {}
    with pytest.raises(ValueError, match="current RC run ID"):
        U.plan_links(st)


@pytest.mark.parametrize("complete", [False, True])
def test_initial_periodic_and_final_reports_render_pipeline_links(complete, tmp_path, capsys):
    st, gathered = state(), progress()
    if complete:
        gathered["done"], gathered["remaining"] = gathered["total"], 0
        for owner in gathered["owners"].values():
            owner["done"], owner["remaining"] = owner["total"], 0
            for test in owner["tests"]:
                test["state"] = "passed"
    spec = {"progress": gathered, "people": people()}
    with mockctx.active(spec):
        if not complete:
            initial = U.build(st)
            assert initial.kind == "needs_skill"
            for link in U.plan_links(st):
                assert f'href="{escape(link["url"], quote=True)}">{link["name"]}</a>' in initial.payload["content"]
        C.save_state(st, str(tmp_path), st.release_id)
        args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=C.DEFAULT_CONFIG,
                         now="2026-09-11T12:00:00-07:00", force=True)
        assert bugbash_update.cmd_post_bugbash_update(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == ("complete" if complete else "post")
    for link in U.plan_links(st):
        assert f'href="{escape(link["url"], quote=True)}">{link["name"]}</a>' in out["content"]
    assert out["notifications"][0]["payload"]["content"] == out["content"]
