"""Private progress remains available without weakening the outbound chat contract."""
from copy import deepcopy
import json

import pytest

from orchestrator import cli
from orchestrator.revision import digest
from orchestrator.state import StepState
from steps.bug_bash import bugbash_updates as U
from tests._context import command_inputs
from tests._harness import _active_phase
from tests._meeting import seed_invite
from tests._ui_results import publish
from tests.test_bugbash_mentions import progress
from tests.test_ui_result_ownership import state as fill_state
from tools import bugbash as B


def state():
    st = fill_state()
    st.release_id, st.ccd = "2026-09", "2026-09-09"
    publish(st)
    _active_phase(st, "bug_bash")
    seed_invite(st)
    return st


@pytest.mark.parametrize("runtime_mismatch", [False, True])
@pytest.mark.parametrize("complete", [False, True])
def test_manual_preview_preserves_skips_receipts_and_human_reviews(
        tmp_path, monkeypatch, capsys, runtime_mismatch, complete):
    st = state()
    for step in ("activate_chat", "notify_native_auth", "bugbash_updates"):
        st.set_step("bug_bash", step, StepState(status="skipped", note="Owner chose manual delivery"))
    st.set_step("bug_bash", "ui_failures", StepState(status="pending", note="Owner review required"))
    if runtime_mismatch:
        st.workflow_revision["runtime_hash"] = digest("previous runtime")
    path = tmp_path / st.release_id / "release-state.json"
    st.save(str(path))
    before = path.read_bytes()
    gathered = progress()
    if complete:
        gathered.update(done=2, remaining=0)
        owner = gathered["owners"]["a@example.test"]
        owner.update(done=1, remaining=0)
        owner["tests"][0]["state"] = "passed"
    monkeypatch.setattr(B, "resolve_mention_people", lambda *a, **kw: pytest.fail("No Graph lookup"))
    monkeypatch.setattr(U, "stored_chat_id", lambda *a: pytest.fail("No chat required"))
    with command_inputs("bug_bash.bugbash_updates", {"progress": gathered}):
        assert cli.main(["--runs-root", str(tmp_path), "bugbash-progress",
                         "--release", st.release_id]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "preview_only"
    assert out["remaining"] == (0 if complete else 1)
    assert out["total"] == 2
    assert out["content"].count("<li>") == 2
    assert "[Broker]" in out["content"] and "[Authenticator]" in out["content"]
    assert "Login &lt;test&gt;" in out["content"]
    assert "<at" not in out["content"] and "chatId" not in out
    assert out["notifications"] == [] and out["permission_to_send"] is False
    assert out["investigations"] == st.get_step("bug_bash", "ui_test_status").data["result"]["investigations"]
    assert path.read_bytes() == before
    assert not (path.parent / "events.jsonl").exists()
    assert not (path.parent / "_automations.json").exists()


@pytest.mark.parametrize("failure", ["missing_fill", "stale_fill", "missing_run", "provider_error"])
def test_manual_preview_fails_closed_without_mutation(tmp_path, monkeypatch, capsys, failure):
    st = state()
    if failure == "missing_fill":
        st.steps.pop("bug_bash.ui_test_status")
    elif failure == "stale_fill":
        st.steps["bug_bash.ui_test_status"]["data"]["result"]["binding"]["rc"] += 1
    elif failure == "missing_run":
        st.pipeline_runs["rcs"][-1]["auth"]["test"]["run_id"] = None
    else:
        monkeypatch.setattr(U, "gather", lambda *a: (False, None, "ADO unavailable"))
    path = tmp_path / st.release_id / "release-state.json"
    st.save(str(path))
    before = path.read_bytes()
    with command_inputs("bug_bash.bugbash_updates", {"progress": progress()}):
        assert cli.main(["--runs-root", str(tmp_path), "bugbash-progress",
                         "--release", st.release_id]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "error" and out["detail"]
    assert "content" not in out and not out["permission_to_send"]
    assert path.read_bytes() == before


def test_manual_render_does_not_relax_default_outbound_mention_requirements():
    gathered = progress()
    before = deepcopy(gathered)
    content, mentions = B.render_update(gathered, "October", [], mention_pending=False)
    assert "<at" not in content and mentions == []
    assert gathered == before
    with pytest.raises(ValueError, match="verified Teams user ID"):
        B.render_update(gathered, "October", [])
