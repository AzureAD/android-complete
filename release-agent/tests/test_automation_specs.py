"""Canonical worker specs, permission policy and owner/host clock boundaries."""
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from orchestrator import automations as A, cli, cli_common as C
from orchestrator.registry import AutomationRegistry, provider_spec
from orchestrator.state import ReleaseState
from tests._automation import observed


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def plan(**kwargs):
    return A.plan(C.DEFAULT_CONFIG, "2026-09", "2026-09-09",
                  owner_timezone="America/Los_Angeles", scheduler_timezone="UTC", now=NOW, **kwargs)


def test_all_specs_are_complete_and_exact_tool_kwargs(tmp_path):
    result = plan()
    assert not result["problems"]
    assert {worker["slug"] for worker in result["automations"]} == {"release-drainer"}
    reg = AutomationRegistry(str(tmp_path))
    for worker in result["automations"]:
        assert not worker["problems"]
        assert worker["optional"] is True
        spec = provider_spec(worker["provider_spec"])
        assert spec["oneShot"] is False
        assert spec["teamsNotify"] == "never"
        assert spec["prompt"] == worker["prompt"]
        assert not ({"kind", "scope", "release", "slug", "cleanup_when"} & set(spec))
        entry = reg.prepare(**worker["registration"], spec=spec)
        result = reg.reconcile_create(entry["key"], observed(), spec=spec, claim=True, executor="test")
        assert result["permission_to_create"] and result["spec"] == spec
        reg.create_result(entry["key"], result["attempt_id"], "not_created", "simulated no create", spec=spec)
        assert "STOP the cleanup loop" in spec["prompt"]
        assert "never recreate or re-enable" in spec["prompt"]
    raw = (tmp_path / "2026-09" / "_automations.json").read_text(encoding="utf-8")
    assert '"prompt"' not in raw and '"provider_spec"' not in raw


@pytest.mark.parametrize("owner,host,hhmm,expected", [
    ("America/Los_Angeles", "America/Los_Angeles", "07:00", "every day at 7am"),
    ("America/Los_Angeles", "America/Los_Angeles", "07:30", "every day at 7:30am"),
    ("America/Los_Angeles", "UTC", "07:00", "every day at 3pm"),
    ("UTC", "UTC", "00:00", "every day at 12am"),
    ("UTC", "UTC", "12:00", "every day at 12pm"),
])
def test_daily_schedule_formats_canonical_scout_nl_and_converts_timezone(owner, host, hhmm, expected):
    assert A._daily_schedule(hhmm, owner_timezone=owner, scheduler_timezone=host, now=NOW) == expected


@pytest.mark.parametrize("owner,host,ccd,fire,expected", [
    ("America/Los_Angeles", "UTC", date(2026, 9, 9), "09:00", "cron: 0 16 9 9 *"),
    ("America/Los_Angeles", "UTC", date(2026, 11, 4), "09:00", "cron: 0 17 4 11 *"),
    ("Asia/Tokyo", "America/Los_Angeles", date(2026, 9, 9), "09:00", "cron: 0 17 8 9 *"),
    ("America/Los_Angeles", "Asia/Tokyo", date(2026, 9, 9), "12:00", "cron: 0 4 10 9 *"),
    ("UTC", "America/Los_Angeles", date(2026, 3, 7), "12:00", "cron: 0 4 7 3 *"),
    ("UTC", "America/Los_Angeles", date(2026, 3, 9), "12:00", "cron: 0 5 9 3 *"),
])
def test_ccd_cron_converts_owner_instant_across_day_and_dst(owner, host, ccd, fire, expected):
    assert A._ccd_cron(ccd, fire, owner_timezone=owner, scheduler_timezone=host, now=NOW) == expected


@pytest.mark.parametrize("owner,host,ccd,fire,now", [
    (None, "UTC", date(2026, 9, 9), "09:00", NOW),
    ("UTC", None, date(2026, 9, 9), "09:00", NOW),
    ("UTC", "invalid", date(2026, 9, 9), "09:00", NOW),
    ("UTC", "UTC", date(2026, 1, 1), "00:00", NOW),
    ("UTC", "UTC", date(2025, 9, 9), "09:00", NOW),
    ("UTC", "UTC", date(2027, 9, 9), "09:00", NOW),
    ("America/Los_Angeles", "UTC", date(2026, 3, 8), "02:30", NOW),
    ("America/Los_Angeles", "UTC", date(2026, 11, 1), "01:30", NOW),
    ("UTC", "America/Los_Angeles", date(2026, 11, 1), "09:30", NOW),
])
def test_unsafe_or_unknown_cron_time_fails_clearly(owner, host, ccd, fire, now):
    with pytest.raises(ValueError):
        A._ccd_cron(ccd, fire, owner_timezone=owner, scheduler_timezone=host, now=now)


def test_canonical_cli_allows_optional_morning_drain_and_enforces_on_demand(tmp_path, monkeypatch, capsys):
    from tests._context import fresh_orchestrator
    original = A.plan
    monkeypatch.setattr(A, "plan", lambda config, release, ccd, **kw:
                        original(config, release, ccd, **{**kw, "scheduler_timezone": "UTC", "now": NOW}))
    state = ReleaseState(release_id="2026-09", ccd="2026-09-09", timezone="America/Los_Angeles")
    fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    C.save_state(state, str(tmp_path), state.release_id)
    base = ["--runs-root", str(tmp_path), "automation"]
    common = ["--release", state.release_id, "--json"]
    assert cli.main(base + ["plan"] + common) == 0
    planned_out = json.loads(capsys.readouterr().out)
    assert {w["slug"] for w in planned_out["automations"]} == {"release-drainer"}
    assert cli.main(base + ["plan", "--slug", "release-drainer"] + common) == 0
    assert len(json.loads(capsys.readouterr().out)["automations"]) == 1
    workers = {w["slug"]: w for w in plan()["automations"]}

    def prepare(worker, extra=()):
        meta = worker["registration"]
        argv = base + ["prepare"] + common + [
            "--slug", meta["slug"], "--name", meta["name"], "--schedule", meta["schedule"],
            "--purpose", meta["purpose"], "--spec-json", json.dumps(worker["provider_spec"])]
        rules = meta["cleanup_when"]
        for rule in rules if isinstance(rules, list) else [rules]:
            argv += ["--cleanup-when", rule]
        for step in meta["steps"]:
            argv += ["--step", step]
        return cli.main(argv + list(extra))

    assert prepare(workers["release-drainer"]) == 0
    capsys.readouterr()
    assert cli.main(base + ["sync"] + common) == 0
    sync = json.loads(capsys.readouterr().out)
    assert sync["permission_to_update"] is False


def test_cli_invalid_canonical_config_fails_without_permission(tmp_path, capsys):
    (tmp_path / "automations.yaml").write_text(
        "version: 2\nprovider_defaults: {}\nautomations:\n  - slug: broken\n",
        encoding="utf-8",
    )
    C.save_state(ReleaseState(release_id="2026-09"), str(tmp_path), "2026-09")
    assert cli.main([
        "--config", str(tmp_path / "phases.yaml"), "--runs-root", str(tmp_path),
        "automation", "plan", "--release", "2026-09", "--json",
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["permission_to_create"]
    assert "provider_defaults" in result["error"]
