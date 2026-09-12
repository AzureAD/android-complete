"""Cadence is configuration-owned; the first post does not wait for a poll tick."""
from argparse import Namespace
from copy import deepcopy
import json

import pytest

from orchestrator import automations as A, cli_common as C
from orchestrator.commands import bugbash_update
from steps.bug_bash import bugbash_updates as U
from steps.lib import mockctx
from tools import bugbash as B
from tests.test_bugbash_mentions import state, progress, people


def test_interval_and_provisioned_prompt_share_one_config_value(monkeypatch):
    assert U.poll_interval_hours(C.DEFAULT_CONFIG) == 3
    definitions = deepcopy(A.load_defs(C.DEFAULT_CONFIG))
    next(d for d in definitions if d["slug"] == "bug-bash-update-poller")["every"] = "6 hours"
    monkeypatch.setattr(A, "load_defs", lambda *_: definitions)
    spec = next(s for s in A.plan(C.DEFAULT_CONFIG, "2026-09", "2026-09-09")["automations"]
                if s["slug"] == "bug-bash-update-poller")
    assert U.poll_interval_hours(C.DEFAULT_CONFIG) == 6
    assert spec["schedule"] == "every 6 hours" and "every 6 hours" in spec["prompt"]


@pytest.mark.parametrize("interval", [None, "", "0 hours", "5 hours", "48 hours", "30 minutes", True])
def test_invalid_interval_cannot_silently_fall_back_to_two_hours(monkeypatch, interval):
    monkeypatch.setattr(A, "load_defs", lambda *_: [{"slug": "bug-bash-update-poller", "every": interval}])
    with pytest.raises(ValueError, match="automations.yaml"):
        U.poll_interval_hours(C.DEFAULT_CONFIG)


def test_periodic_checkpoints_and_expiry_follow_three_hour_intervals(tmp_path, capsys):
    st = state()
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=C.DEFAULT_CONFIG,
                     force=False, now=None)
    snapshots = []
    with mockctx.active({"progress": progress(), "people": people()}):
        for hour in (9, 11, 12, 14, 15):
            args.now = f"2026-09-11T{hour:02}:00:00-07:00"
            assert bugbash_update.cmd_post_bugbash_update(args) == 0
            out = json.loads(capsys.readouterr().out)
            assert out["decision"] == "post"
            snapshots.append(out["notifications"][0])
        args.now = "2026-09-11T18:00:00-07:00"
        assert bugbash_update.cmd_post_bugbash_update(args) == 0
        assert json.loads(capsys.readouterr().out)["decision"] == "off_hours"
    assert snapshots[0]["id"] == snapshots[1]["id"]
    assert snapshots[2]["id"] == snapshots[3]["id"] != snapshots[0]["id"]
    assert snapshots[4]["id"] != snapshots[3]["id"]
    assert [s["scope"]["expires_at"] for s in snapshots] == [
        "2026-09-11T12:00:00-07:00", "2026-09-11T12:00:00-07:00",
        "2026-09-11T15:00:00-07:00", "2026-09-11T15:00:00-07:00", "2026-09-11T18:00:00-07:00"]


def test_first_post_is_ready_without_waiting_for_polling_window(monkeypatch):
    monkeypatch.setattr(B, "is_working_time", lambda _: False)
    st = state()
    before = deepcopy(st.notification_deliveries)
    with mockctx.active({"progress": progress(), "people": people()}):
        out = U.build(st)
    assert out.kind == "needs_skill"
    assert out.payload["_automation"] == {"on_demand": "bug-bash-update-poller"}
    assert st.notification_deliveries == before  # Preparing never sends or provisions the worker.
