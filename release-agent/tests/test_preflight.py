"""Release-agent tests — preflight. Shared harness in tests/_harness.py."""
from tests._context import context as _context, invoke as _invoke, invoke_effect as _invoke_effect
from tests._harness import *  # noqa: F401,F403
from orchestrator.outcomes import Done, Blocked
import pytest


@pytest.mark.parametrize("text", [None, "", "   ", {}, "<html>Sign in</html>",
                                "Version 1.0.0\n- [MAJOR] historical change"])
def test_breaking_incomplete_source_is_not_a_clean_release(text):
    from steps.preflight import breaking

    out = breaking.build(_context(ReleaseState(release_id="2026-10"), inputs={"changelog": text}))
    assert isinstance(out, Blocked) and "invalid changelog" in out.reason


def test_breaking_draft_uses_release_display_month():
    from steps.preflight import breaking

    out = breaking.build(_context(
        ReleaseState(release_id="2026-10", target_month="2026-11"),
        inputs={"changelog": "vNext\n---\n- [MAJOR] Fixture change\nVersion 1.0\n"},
    ))
    assert isinstance(out, Done) and "November 2026" in out.note
    assert "2026-10" not in out.note


@pytest.mark.parametrize("alerts", [
    None, {}, [None], [{}], [{"alertState": "mystery"}],
    [{"alertState": "active"}],
    [{"alertState": "active", "severity": "unknown"}],
    [{"alertState": "active", "severity": "high", "component": []}],
    [{"alertState": "active", "severity": "high", "_links": []}],
    [{"alertState": "active", "severity": "high", "_links": {"web": []}}],
    [{"alertState": "active", "severity": "high", "url": []}],
])
def test_cg_incomplete_source_cannot_clear_the_step(alerts):
    from steps.preflight import cg

    out = cg.build(_context(ReleaseState(), inputs={"alerts": alerts}))
    assert isinstance(out, Blocked) and "invalid alerts" in out.reason


def test_cg_normalizes_active_severity_without_hiding_high_alerts():
    from steps.preflight import cg

    out = cg.build(_context(ReleaseState(), inputs={"alerts": [{
        "alertState": " Active ", "severity": " HIGH ", "title": "Fixture issue",
        "component": None, "actionItems": None,
    }]}))
    assert isinstance(out, Blocked) and "1 high" in out.reason


@pytest.mark.parametrize("run", [
    "not a run", {"result": "succeeded"}, {"queueTime": "unknown"},
    {"queueTime": 123}, {"queueTime": "2026-10-17T12:00:00Z"},
])
def test_cron_unverifiable_or_future_timestamps_hold(run):
    from datetime import datetime, timezone
    from steps.preflight import cron

    out = cron.build(_context(
        ReleaseState(), inputs={"run": run},
        now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
    ))
    assert isinstance(out, Blocked)


@pytest.mark.parametrize("queued,blocked", [
    ("2026-10-14T12:00:00Z", False),
    ("2026-10-14T11:59:59Z", True),
])
def test_cron_staleness_limit_uses_elapsed_time_not_rounded_days(queued, blocked):
    from datetime import datetime, timezone
    from steps.preflight import cron

    out = cron.build(_context(
        ReleaseState(), inputs={"run": {"queueTime": queued, "result": "succeeded"}},
        now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
    ))
    assert isinstance(out, Blocked) == blocked


@pytest.mark.parametrize("source", ["cg", "cron"])
@pytest.mark.parametrize("response", [{}, None, [None], {"value": None}])
def test_preflight_read_adapters_reject_missing_collections(monkeypatch, source, response):
    import json
    from types import SimpleNamespace
    from tools import checks

    monkeypatch.setattr(checks.shutil, "which", lambda _: "az")
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(response), stderr=""))
    if source == "cg":
        ok, _, detail = checks.fetch_cg_alerts("resource", "https://fixture.invalid", "p", 1, "working")
    else:
        ok, _, detail = checks.latest_scheduled_build("https://fixture.invalid", "p", 1)
    assert not ok and "invalid" in detail


def test_cg_partial_collection_is_not_a_clean_result(monkeypatch):
    import json
    from types import SimpleNamespace
    from tools import checks

    monkeypatch.setattr(checks.shutil, "which", lambda _: "az")
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({"value": [], "continuationToken": "next"}), stderr=""))
    ok, _, detail = checks.fetch_cg_alerts("resource", "https://fixture.invalid", "p", 1, "working")
    assert not ok and "incomplete" in detail


@pytest.mark.parametrize("mode", [
    "clean", "cleanup_transport_failure", "cleanup_rejected", "stale_same_tip",
    "stale_changed_tip", "prefix_master", "probe_read_failed", "wrong_create_receipt",
    "prefix_probe",
])
def test_oneauth_probe_verifies_exact_refs_and_cleanup(monkeypatch, mode):
    from tools import checks, pipelines as P

    tip = "a" * 40
    ref = "refs/heads/user/fixture/scout-oneauth-access-check"
    writes = []

    def get(url, timeout):
        if "filter=heads/master" in url:
            name = "refs/heads/master-old" if mode == "prefix_master" else "refs/heads/master"
            return True, {"value": [{"name": name, "objectId": tip}]}, ""
        if mode == "probe_read_failed":
            return False, None, "fixture read denied"
        if mode.startswith("stale_"):
            return True, {"value": [{
                "name": ref, "objectId": tip if mode == "stale_same_tip" else "b" * 40,
            }]}, ""
        if mode == "prefix_probe":
            return True, {"value": [{"name": ref + "-other", "objectId": "b" * 40}]}, ""
        return True, {"value": []}, ""

    def send(url, method, body, timeout):
        change, = body
        writes.append(change)
        assert change["name"] == ref and method == "POST"
        removing = change["newObjectId"] == "0" * 40
        if removing and mode == "cleanup_transport_failure":
            return False, None, "fixture cleanup unavailable"
        name = ref + "-wrong" if mode == "wrong_create_receipt" else ref
        return True, {"value": [{
            "name": name, "success": not (removing and mode == "cleanup_rejected"),
        }]}, ""

    monkeypatch.setattr(P, "_ado_rest_get", get)
    monkeypatch.setattr(P, "_ado_rest_send", send)
    ok, detail = checks.oneauth_write_access("fixture")
    assert ok == (mode in ("clean", "stale_same_tip", "prefix_probe")), detail
    if mode in ("stale_changed_tip", "prefix_master", "probe_read_failed"):
        assert writes == []
    if ok:
        assert "created and deleted" in detail
        assert writes[-1]["oldObjectId"] == tip and writes[-1]["newObjectId"] == "0" * 40
    if mode.startswith("cleanup_"):
        assert "cleanup failed" in detail


@pytest.mark.parametrize("alias", ["../owner", "owner/other", "owner@tenant", 1])
def test_oneauth_rejects_invalid_probe_alias_before_any_write(alias):
    from steps.preflight import oneauth_access

    out = oneauth_access.prepare_effect(_context(ReleaseState(), inputs={"alias": alias}))
    assert isinstance(out, Blocked) and "invalid alias" in out.reason


@pytest.mark.parametrize("step", ["notice", "flight_reminder"])
def test_preflight_notifications_block_invalid_ccd_without_mutating(step):
    from steps.preflight import notice, flight_reminder

    state = ReleaseState(release_id="2026-10", ccd="not-a-date")
    if step == "notice":
        out = notice.build(_context(state, model=notice.BuildParameters))
    else:
        out = flight_reminder.build(_context(state))
    assert isinstance(out, Blocked) and "CCD" in out.reason
    assert state.steps == {} and out.updates == ()


@pytest.mark.parametrize("asset", [None, [], {}, {"subject": "wrong", "body": "wrong"}])
def test_notice_blocks_invalid_template_assets(asset):
    from dataclasses import replace
    from steps.preflight import notice

    ctx = _context(ReleaseState(release_id="2026-10", ccd="2026-10-21"), model=notice.BuildParameters)
    ctx = replace(ctx, services=replace(ctx.services, assets=replace(
        ctx.services.assets, template=lambda _: asset)))
    out = notice.build(ctx)
    assert isinstance(out, Blocked) and "template" in out.reason


@pytest.mark.parametrize("interrupt_notice", [False, True])
def test_complete_preflight_real_handlers_and_persisted_cli(
        tmp_path, monkeypatch, capsys, interrupt_notice):
    from datetime import datetime, timezone
    import json
    from unittest.mock import Mock
    from orchestrator import cli, delivery, mocks, schedule, service_adapters
    from steps.preflight import cron
    from tools import checks, pipelines as P

    root, rid = str(tmp_path), "2026-10"
    now = datetime(2026, 10, 16, 21, tzinfo=timezone.utc)
    controls = {"alerts": [{"alertState": "active", "severity": "high", "title": "Fixture CG"}]}
    monkeypatch.setattr(schedule, "now_local", lambda _tz=None: now)
    monkeypatch.setattr(cron, "_trusted_utc_now", lambda: now)
    monkeypatch.setattr(delivery, "now_iso", lambda: now.isoformat())
    monkeypatch.setattr(mocks, "load_mocks", lambda: {
        "preflight.notice": {"send_to": ["owner@example.test"]},
        "preflight.flight_reminder": {"send_to": "19:phase-fixture@thread.v2"},
    })
    monkeypatch.setattr(service_adapters, "_changelog",
                        lambda _: "vNext\n---\n- [MAJOR] Fixture API change\nVersion 1.0\n")
    monkeypatch.setattr(checks, "fetch_cg_alerts", lambda *a: (True, controls["alerts"], "fixture"))
    monkeypatch.setattr(checks, "latest_scheduled_build", lambda *a: (
        True, {"queueTime": now.isoformat(), "result": "succeeded"}, "fixture"))
    monkeypatch.setattr(checks, "current_az_user", lambda: "owner@example.test")
    refs = {"refs/heads/master": "a" * 40}
    writes = []

    def read_refs(url, timeout):
        return True, {"value": [{"name": name, "objectId": value} for name, value in refs.items()
                                if name == "refs/heads/master" or "scout-oneauth" in name]}, ""

    def write_ref(url, method, body, timeout):
        change, = body
        record = C.load_state(root, rid).get_step("preflight", "oneauth_access")
        assert record.status == "running" and record.execution["effect_input"]["alias"] == "owner"
        assert record.execution["owner"] == "engine"
        old = refs.get(change["name"], "0" * 40)
        assert old == change["oldObjectId"]
        writes.append(change)
        if change["newObjectId"] == "0" * 40:
            refs.pop(change["name"])
        else:
            refs[change["name"]] = change["newObjectId"]
        return True, {"value": [{"name": change["name"], "success": True}]}, ""

    monkeypatch.setattr(P, "_ado_rest_get", read_refs)
    monkeypatch.setattr(P, "_ado_rest_send", write_ref)
    state = ReleaseState(
        release_id=rid, target_month="2026-11", ccd="2026-10-21",
        ccd_source="confirmed", owner_name="Fixture owner", owner_email="owner@example.test",
        timezone="America/Los_Angeles",
    )
    _active_phase(state, "preflight")
    C.save_state(state, root, rid)
    from tests._automation import seed_driver
    seed_driver(root, state)

    def call(*args, expected=0):
        code = cli.main(["--runs-root", root, *args, "--release", rid])
        output = capsys.readouterr().out
        assert code == expected, output
        return output

    report = json.loads(call("next", "--json"))
    saved = C.load_state(root, rid)
    assert saved.get_step("preflight", "cg").status == "blocked"
    for sid in ("breaking", "oneauth_access", "cron"):
        assert saved.is_done("preflight", sid)
    assert "DRAFT COMMS" in saved.get_step("preflight", "breaking").note
    assert len(writes) == 2 and refs == {"refs/heads/master": "a" * 40}
    assert set(report["scout_pending"]) == {"notice", "flight_reminder", "lockdown"}
    assert {item["step"] for item in report["work_dispatch"]["pending_work"]} == {
        "notice", "flight_reminder", "lockdown"}
    assert cli.main(["--runs-root", root, "status", "--release", rid,
                     "--handoff", "--json", "--no-pipeline-check"]) == 2
    assert not json.loads(capsys.readouterr().out)["work_dispatch"]["may_return_to_user"]
    email_provider, teams_provider = Mock(return_value={"accepted": True}), Mock(return_value={"id": "fixture"})
    for sid, provider in (("notice", email_provider), ("flight_reminder", teams_provider)):
        item, = json.loads(call(
            "notification", "prepare", "--source", "step", "--phase", "preflight", "--step", sid,
        ))["notifications"]
        claim = json.loads(call("notification", "claim", "--id", item["id"],
                                "--hash", item["hash"], "--executor", "fixture-worker"))
        assert claim["permission_to_send"] and not C.load_state(root, rid).is_done("preflight", sid)
        receipt = provider(**claim["payload"])
        if sid == "notice":
            assert claim["payload"]["isHtml"] and "<table" in claim["payload"]["body"]
            receipt = delivery.exact_payload_receipt(item, receipt)
        receipt_path = tmp_path / f"{sid}-receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        args = ("notification", "result", "--id", item["id"], "--execution-id", claim["execution_id"],
                "--outcome", "sent", "--evidence", "synthetic provider accepted exact payload",
                "--receipt-file", str(receipt_path))
        if sid == "notice" and interrupt_notice:
            save, count = C.save_state, 0

            def interrupted_save(*a, **k):
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError("fixture interruption after notice receipt")
                return save(*a, **k)

            with monkeypatch.context() as interrupted:
                interrupted.setattr(C, "save_state", interrupted_save)
                call(*args, expected=1)
            assert not C.load_state(root, rid).is_done("preflight", sid)
            duplicate = json.loads(call("notification", "claim", "--id", item["id"],
                                        "--hash", item["hash"], "--executor", "recovery-worker"))
            assert not duplicate["permission_to_send"]
            call("notification", "finalize", "--id", item["id"])
        else:
            call(*args)
        assert C.load_state(root, rid).is_done("preflight", sid)
        provider.assert_called_once_with(**claim["payload"])
    call("check-lockdown", "--periods-json", "[]")
    settled = json.loads(call("status", "--handoff", "--json", "--no-pipeline-check"))
    assert settled["work_dispatch"]["may_return_to_user"]
    assert not C.load_state(root, rid).is_done("preflight", "cg")
    for sid in ("confirm_reminders", "vitals"):
        prompt = json.loads(call("step-action", "--phase", "preflight", "--step", sid))
        assert prompt["kind"] == "needs_human" and prompt["attest"]
        assert "done --step" not in prompt["prompt"]
        assert not C.load_state(root, rid).is_done("preflight", sid)
        call("done", "--phase", "preflight", "--step", sid,
             "--note", f"Explicit simulated owner confirmation for {sid}")
    assert not C.load_state(root, rid).is_done("preflight", "cg")
    controls["alerts"] = []
    report = json.loads(call("next", "--json"))
    completed = C.load_state(root, rid)
    phase = next(p for p in Orchestrator(CONFIG, completed, mocks={}).config["phases"]
                 if p["id"] == "preflight")
    assert all(completed.is_done("preflight", item["id"]) for item in phase["steps"])
    assert report["current_phase"] == "ccd" and report["status"] == "scheduled"
    assert not completed.is_done("ccd", "final_reminder")
    assert all(record["status"] == "sent" and len(record["attempts"]) == 1
               for record in completed.notification_deliveries.values())
    assert completed.resources == {} and completed.pipeline_runs == {} and completed.versions == {}
    assert len(writes) == 2
    call("next", "--json")
    assert len(writes) == 2




def test_paths_command_reports_self_located_roots(capsys):
    """`paths --json` reports the engine's self-located roots (agent_root/repo_root/runs_root),
    all derived from the engine's own file location — NO hardcoded C:\\repos. This is what the
    skill's FIRST-RUN onboarding uses to confirm the clone + cd correctly on any machine."""
    import json as _json, os
    from orchestrator import cli as _cli
    from orchestrator.commands import paths_cmd
    # helper: agent_root is release-agent/, repo_root its parent, runs_root default = repo/.release-runs
    p = paths_cmd.resolve_paths()
    assert os.path.basename(p["agent_root"]) == "release-agent"
    assert p["repo_root"] == os.path.dirname(p["agent_root"])
    assert p["runs_root"] == os.path.join(p["repo_root"], ".release-runs")
    assert p["repo_name"] == os.path.basename(p["repo_root"])
    # --runs-root override is honored
    over = paths_cmd.resolve_paths("D:\\somewhere\\.runs")
    assert over["runs_root"] == os.path.abspath("D:\\somewhere\\.runs")
    assert over["agent_root"] == p["agent_root"]         # agent/repo still self-located
    # CLI emits valid JSON with all keys
    rc = _cli.main(["paths", "--json"])
    assert rc == 0
    rep = _json.loads(capsys.readouterr().out)
    assert set(rep) >= {"agent_root", "repo_root", "runs_root", "repo_name"}


def test_next_json_emits_status_report_with_scout_pending(capsys, monkeypatch):
    """`next --json` advances THEN prints the status report as JSON (same shape as
    `status --json`, carrying `scout_pending`) — so a caller advances + reads the pending
    scout steps in ONE call. Plain `next` still prints the human advance block, not JSON."""
    import json as _json, tempfile
    from orchestrator import cli as _cli
    from tools import checks
    monkeypatch.setattr(checks, "read_pipeline_variable", lambda *a, **k: (False, None, "offline"))
    with tempfile.TemporaryDirectory() as rr:
        R = "2099-05"
        _cli.main(["--runs-root", rr, "init", "--release", R,
                   "--owner-email", "t@example.com", "--owner-name", "T"])
        capsys.readouterr()                                   # drop init output
        rc = _cli.main(["--runs-root", rr, "next", "--release", R, "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        rep = _json.loads(out)                                # must be valid JSON
        assert rep["release_id"] == R
        assert "scout_pending" in rep                         # the field the push-loop reads
        # plain `next` (no --json) prints the advance block, NOT json
        _cli.main(["--runs-root", rr, "next", "--release", R])
        plain = capsys.readouterr().out
        try:
            _json.loads(plain)
            assert False, "plain `next` should not emit JSON"
        except _json.JSONDecodeError:
            pass


def test_unopened_phase_shows_all_steps_scheduled_uniformly():
    """REGRESSION: before a phase opens, its scout steps must NOT render 'Scout runs this —
    automatic' while its agent/human steps render 'Not open yet' — that mix confused the
    reader. An unopened phase shows EVERY not-yet-run step uniformly as 'Not open yet'
    (state 'scheduled'). Preflight opens CCD-7; as-of CCD-14 it's the current phase but not due."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-06-24")           # CCD-14 → preflight is current but NOT yet open
    r = orch.status_report()
    assert r["active_phase"]["id"] == "preflight"
    assert r["active_phase"]["due"] is False
    states = {s["state"] for s in r["current_steps"]}
    # uniform — no 'scout'/'auto'/'pending' mix, everything is 'scheduled'
    assert states == {"scheduled"}, states
    view = render.status_view(r)
    assert "Not open yet" in view
    assert "Scout runs this" not in view          # the confusing early label is gone


def test_open_phase_still_differentiates_step_states():
    """The uniform 'Not open yet' only applies BEFORE a phase opens. Once open, states still
    differentiate: scout steps → 'scout' (Scout runs this), so the fix didn't flatten a live phase."""
    st, orch = _ccd_orch("2026-07-02")            # Phase 0 open
    r = orch.status_report()
    assert r["active_phase"]["id"] == "preflight"
    assert r["active_phase"]["due"] is True
    states = {s["state"] for s in r["current_steps"]}
    assert states != {"scheduled"}                # no longer uniform once open
    assert "scout" in states                      # scout steps now labelled as scout


def test_ccd_cron_pins_to_exact_date():
    """_ccd_cron builds a cron 'M H D Mo *' targeting the CCD's day+month+time, so a
    one-shot fires ON the CCD — never the next matching weekday (the early-fire bug)."""
    from orchestrator import automations as A
    from datetime import date, datetime, timezone
    import pytest

    clock = {
        "owner_timezone": "America/Los_Angeles",
        "scheduler_timezone": "America/Los_Angeles",
        "now": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }
    assert A._ccd_cron(date(2026, 8, 26), "09:00", **clock) == "cron: 0 9 26 8 *"
    assert A._ccd_cron(date(2026, 8, 26), "12:00", **clock) == "cron: 0 12 26 8 *"
    assert A._ccd_cron(date(2026, 12, 9), "13:30", **clock) == "cron: 30 13 9 12 *"
    for target, fire in ((None, "09:00"), (date(2026, 8, 26), ""), (date(2026, 8, 26), "nonsense")):
        with pytest.raises(ValueError):
            A._ccd_cron(target, fire, **clock)




def test_status_surfaces_agent_result_notes_and_wiki_link():
    """Agent results are stored as the step note and surfaced in status: the
    Details column shows each step's outcome; multi-line reports expand below."""
    from orchestrator import render
    st, orch = _ccd_orch("2026-07-02")          # Phase 0 open
    orch.run_until_gate()                        # runs breaking/cg/cron (set notes)
    r = orch.status_report()
    steps = {s["id"]: s for s in r["current_steps"]}
    assert steps["cg"].get("note")
    view = render.status_view(r)
    assert "| Details |" in view                 # the third column exists
    assert "Component Governance" in view        # cg's real report note surfaces




def test_parse_breaking_only_scans_vnext_section():
    from steps.preflight.breaking import parse_breaking
    hits = parse_breaking(_SAMPLE_CHANGELOG, section="vNext", tag="[MAJOR]")
    assert len(hits) == 1
    assert "(#2)" in hits[0] and "(#0)" not in hits[0]
    assert hits[0].startswith("[MAJOR]")          # leading "- " bullet stripped (no doubling)




def test_parse_breaking_none_when_no_major():
    from steps.preflight.breaking import parse_breaking
    txt = "vNext\n----------\n- [MINOR] x (#1)\nVersion 1.0.0\n----------\n"
    assert parse_breaking(txt) == []




def test_breaking_agent_detects_and_drafts():
    from steps.preflight import breaking, cg, cron
    from orchestrator import service_adapters as _bk
    orig = _bk._changelog
    _bk._changelog = lambda *a, **k: _SAMPLE_CHANGELOG
    try:
        st = ReleaseState(release_id="2026-08")
        r = _invoke(breaking.build, st)
        assert isinstance(r, Done)
        assert "Detected 1 breaking" in r.note
        assert "(#2)" in r.note and "DRAFT COMMS" in r.note
    finally:
        _bk._changelog = orig




def test_breaking_agent_none_found_passes():
    from steps.preflight import breaking, cg, cron
    from orchestrator import service_adapters as _bk
    orig = _bk._changelog
    _bk._changelog = lambda *a, **k: "vNext\n----------\n- [MINOR] x (#1)\nVersion 1.0.0\n"
    try:
        r = _invoke(breaking.build, ReleaseState(release_id="2026-08"))
        assert isinstance(r, Done) and "No breaking" in r.note
    finally:
        _bk._changelog = orig




def test_breaking_agent_fetch_error_holds():
    from steps.preflight import breaking, cg, cron
    from orchestrator import service_adapters as _bk
    orig = _bk._changelog

    def _boom(*a, **k):
        raise RuntimeError("network down")
    _bk._changelog = _boom
    try:
        r = _invoke(breaking.build, ReleaseState(release_id="2026-08"))
        assert isinstance(r, Blocked) and "could not fetch" in r.reason
    finally:
        _bk._changelog = orig




def test_build_verify_mrwp_in_flight_when_run_still_executing():
    """An MRWP run whose OVERALL status is still inProgress is NOT a failure — verify
    returns in_progress (Scout polls + re-evaluates on completion) instead of blocking it
    as an aborted pipeline. A pending stage on an in-flight run is 'not run YET'."""
    st, orch = _bv_state({"build_verify.mrwp_ecs": {
        "mrwp_id": "999", "build_status": "inProgress"}})
    out = _bv_build(orch, st, "mrwp_ecs")
    assert out["kind"] == "in_progress"
    assert "still running" in out["note"] and "poll" in out["note"].lower()
    assert out["poll_in_min"] == 30
    # a completed run still runs the normal rule (control): injected stages, no in-flight
    st2, orch2 = _bv_state({})
    assert _bv_build(orch2, st2, "mrwp_ecs")["kind"] == "done"




def test_engine_in_flight_step_holds_without_flagging_owner():
    """The engine records an in-flight agent step as status 'in_flight' (not blocked):
    it is NOT added to pending_human, the release stays 'running' (no user action), the
    drain returns a 'waiting' action, and first-seen time is stamped for the 6h nudge."""
    st, orch = _bv_state({"build_verify.mrwp_ecs": {
        "mrwp_id": "999", "build_status": "inProgress"}})
    _active_step(st, "build_verify", "mrwp_ecs")
    phase = next(p for p in orch.config["phases"] if p["id"] == "build_verify")
    step = next(s for s in phase["steps"] if s["id"] == "mrwp_ecs")
    act = orch._run_auto_step(phase, step, block_holds=True)
    assert act.kind == "waiting"
    rec = st.get_step("build_verify", "mrwp_ecs")
    assert rec.status == "in_flight"
    report = orch.status_report()
    assert "build_verify.mrwp_ecs" not in report["pending_human"]
    assert report["status"] == "running"
    assert rec.data.get("in_flight_since") and rec.data.get("poll_in_min") == 30
    assert not st.is_done("build_verify", "mrwp_ecs")     # not done → re-runs on next poll




def test_verify_auth_ecs_in_flight_build_holds():
    st, orch = _bv_state({"build_verify.auth_ecs": {
        "auth_build": {"build_id": 900010, "rc": 1, "version": "0.0.02468-rc-RC1-ecs",
                       "status": "inProgress", "result": None}}})
    out = _bv_build(orch, st, "auth_ecs")
    assert out["kind"] == "in_progress" and "still running" in out["note"]




def test_msal_variant_and_flight_markers():
    from tools import pipelines as P
    assert P._msal_variant("PROD MSAL - RC Broker (API 32)") == "prod"
    assert P._msal_variant("PROD MSAL - RC BrokerHost (API 32)") == "prod"
    assert P._msal_variant("RC MSAL - PROD Broker (API 32)") == "rc"
    assert P._msal_variant("LTW, RC MSAL - RC Broker (API 32)") == "rc"
    assert P._msal_variant("Stress Tests - RC MSAL with RC Broker") == "rc"
    assert P._msal_variant("Lab Api Tests") is None




def test_lockdown_overlap_only_production():
    """Overlap rule: only Production-env periods that intersect the window count;
    Banner-only advisories are ignored even if they overlap."""
    from orchestrator.commands.lockdown import overlapping_periods
    from datetime import date
    ws, we = date(2026, 8, 5), date(2026, 8, 26)
    periods = [
        {"name": "FIFA", "environment": "Banner", "start": date(2026, 8, 1), "end": date(2026, 8, 10)},
        {"name": "YearEnd", "environment": "Production", "start": date(2026, 8, 20), "end": date(2026, 9, 5)},
        {"name": "Old", "environment": "Production", "start": date(2026, 6, 1), "end": date(2026, 7, 1)},
    ]
    hits = overlapping_periods(ws, we, periods, "Production")
    assert [h["name"] for h in hits] == ["YearEnd"]




def test_engine_holds_scout_assisted_lockdown():
    """Scout steps are the SKILL's work: they surface in `scout_pending` (for the
    skill to run via step-action), NOT as the user's `current_step`/`action`. Clearing
    them drains the list; an attest step (confirm_reminders) becomes a real user hold."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()                 # NOTE: no scout steps cleared yet
    orch.run_until_gate()
    rep = orch.status_report()
    assert rep["status"] == "awaiting_action"
    # the three scout steps are pending FOR THE SKILL — not surfaced as a user action
    assert set(rep["scout_pending"]) == {"notice", "flight_reminder", "lockdown"}
    assert (rep["action"] or {}).get("step") not in ("notice", "flight_reminder", "lockdown")
    assert "notice" in rep["scout_pending"]
    # the skill runs each scout step (record_scout_step = its step-action + record-step)
    _clear_notice(orch)
    orch.run_until_gate()
    assert "notice" not in orch.status_report()["scout_pending"]
    orch.record_scout_step("preflight", "flight_reminder", "pass", "posted")
    orch.run_until_gate()
    assert "flight_reminder" not in orch.status_report()["scout_pending"]
    # confirm_reminders (attest, dep on flight_reminder now done) is a genuine user hold
    assert "preflight.confirm_reminders" in orch.status_report()["pending_human"]
    orch.complete_step("preflight", "confirm_reminders", "owner confirmed")
    orch.record_scout_step("preflight", "lockdown", "pass", "no overlap")
    orch.run_until_gate()
    assert orch.status_report()["scout_pending"] == []   # all scout work drained




def test_check_lockdown_pass_and_attention():
    """check-lockdown records pass when nothing overlaps and holds (attention)
    when a Production CCOA overlaps the release window."""
    import tempfile as _tf, json as _json
    from orchestrator.commands import lockdown as lk
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-12", ccd_source="default")
        orch = Orchestrator(CONFIG, st)
        _pass_scout_checks(orch); orch.gate.sign()
        C.save_state(st, d, rid)

        class A:
            runs_root = d; release = rid; config = CONFIG; as_of = None
            periods_json = _json.dumps([
                {"name": "Banner Only Thing", "environment": "Banner",
                 "start": "2026-08-10", "end": "2026-08-14"}])
        assert lk.cmd_check_lockdown(A) == 0
        assert C.load_state(d, rid).is_done("preflight", "lockdown")   # banner ignored → pass

        # now a Production overlap → attention (held, not done)
        from orchestrator.state import StepState
        st2 = C.load_state(d, rid)
        st2.set_step("preflight", "lockdown", StepState())   # reset to pending
        C.save_state(st2, d, rid)

        class B(A):
            periods_json = _json.dumps([
                {"name": "IDNA Year-End", "environment": "Production",
                 "start": "2026-08-11", "end": "2026-12-31"}])
        assert lk.cmd_check_lockdown(B) == 0
        st3 = C.load_state(d, rid)
        assert not st3.is_done("preflight", "lockdown")
        assert Orchestrator(CONFIG, st3).status_report()["status"] == "awaiting_action"




def _ccoa_period(**changes):
    return {
        "name": "Fixture CCOA", "environment": "Production",
        "start": "2026-10-20", "end": "2026-10-22", **changes,
    }


@pytest.fixture
def lockdown_cli(tmp_path, monkeypatch, capsys):
    from copy import deepcopy
    from datetime import datetime
    from types import SimpleNamespace
    from orchestrator import cli, mocks, schedule

    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    now = datetime.fromisoformat("2026-10-16T14:00:00-07:00")
    monkeypatch.setattr(schedule, "now_local", lambda _tz=None: now)
    st = ReleaseState(
        release_id="2026-10", ccd="2026-10-21", ccd_source="confirmed",
        timezone="America/Los_Angeles", owner_email="owner@example.test",
    )
    _active_phase(st, "preflight")
    C.save_state(st, str(tmp_path), st.release_id)
    base = ["--runs-root", str(tmp_path)]

    def call(raw_json, *, expected=0):
        code = cli.main(base + [
            "check-lockdown", "--release", st.release_id, "--periods-json", raw_json,
        ])
        output = capsys.readouterr().out
        assert code == expected, output
        return output

    return SimpleNamespace(
        call=call, load=lambda: C.load_state(str(tmp_path), st.release_id),
        root=tmp_path, release=st.release_id, base=base, before=deepcopy(st),
    )


@pytest.mark.parametrize("raw", [
    None, {}, "not an array", [None],
    [_ccoa_period(start=None)],
    [_ccoa_period(start="not-a-date")],
    [_ccoa_period(end="2026-02-30")],
    [_ccoa_period(start="2026-10-22", end="2026-10-20")],
    [_ccoa_period(environment=None)],
    [_ccoa_period(environment=["Production"])],
    [_ccoa_period(name="")],
    [_ccoa_period(start="2026-1-01")],
    [_ccoa_period(), _ccoa_period(end="")],
])
def test_lockdown_rejects_incomplete_evidence_without_recording_pass(lockdown_cli, raw):
    from copy import deepcopy
    import json
    from steps.preflight import lockdown

    client = lockdown_cli
    before = deepcopy(client.before)
    with pytest.raises(ValueError, match="CCOA"):
        lockdown.decide(_context(before), raw)
    assert before == client.before
    state_path = client.root / client.release / "release-state.json"
    saved = state_path.read_bytes()
    output = client.call(json.dumps(raw), expected=1)
    assert "Cannot check lockdown:" in output
    assert state_path.read_bytes() == saved
    assert not (client.root / client.release / "events.jsonl").exists()
    assert not client.load().is_done("preflight", "lockdown")


@pytest.mark.parametrize("raw_json", ["", "   ", "["])
def test_lockdown_rejects_invalid_json_without_empty_array_fallback(lockdown_cli, raw_json):
    client = lockdown_cli
    state_path = client.root / client.release / "release-state.json"
    before = state_path.read_bytes()
    assert "Could not parse --periods-json" in client.call(raw_json, expected=1)
    assert state_path.read_bytes() == before
    assert not (client.root / client.release / "events.jsonl").exists()


@pytest.mark.parametrize("period,expected", [
    (_ccoa_period(start="2026-10-13", end="2026-10-14"), "attention"),
    (_ccoa_period(start="2026-11-04", end="2026-11-05"), "attention"),
    (_ccoa_period(start="2026-10-13", end="2026-10-13"), "pass"),
    (_ccoa_period(start="2026-11-05", end="2026-11-06"), "pass"),
    (_ccoa_period(environment="Banner"), "pass"),
    (_ccoa_period(environment="production, Banner"), "attention"),
])
def test_lockdown_decision_preserves_inclusive_window_and_environment(period, expected):
    from steps.preflight import lockdown

    st = ReleaseState(release_id="2026-10", ccd="2026-10-21")
    status, detail = lockdown.decide(_context(st), [period])
    assert status == expected
    assert "2026-10-14..2026-11-04" in detail


@pytest.mark.parametrize("ccd", [None, "not-a-date", "2026-02-30"])
def test_lockdown_builder_blocks_invalid_release_date(ccd):
    from steps.preflight import lockdown

    st = ReleaseState(release_id="2026-10", ccd=ccd)
    out = lockdown.build(_context(st))
    assert isinstance(out, Blocked) and "CCD" in out.reason
    assert out.updates == () and st.steps == {}


def test_lockdown_gather_covers_cross_year_window_without_missing_evidence_fallback():
    from steps.preflight import lockdown

    st = ReleaseState(release_id="2027-01", ccd="2027-01-03")
    out = lockdown.build(_context(st))
    gather = out.payload["_gather"]
    assert gather["window"] == "2026-12-27..2027-01-17"
    assert "every calendar year" in gather["instructions"]
    assert "do not substitute an empty array" in gather["instructions"]
    assert out.tool == "check-lockdown" and not out.outbound
    assert out.updates == () and st.steps == {}


def test_lockdown_builder_blocks_reversed_configured_window(monkeypatch):
    from steps.preflight import lockdown

    monkeypatch.setitem(lockdown.CONFIG, "window_start_anchor", "CCD+15")
    st = ReleaseState(release_id="2026-10", ccd="2026-10-21")
    out = lockdown.build(_context(st))
    assert isinstance(out, Blocked) and "starts after it ends" in out.reason
    assert out.updates == () and st.steps == {}


def test_lockdown_cli_records_overlap_then_resolves_without_other_state_changes(
        lockdown_cli, capsys):
    from dataclasses import fields
    import json
    from orchestrator import cli

    client = lockdown_cli
    assert cli.main(client.base + [
        "step-action", "--release", client.release, "--phase", "preflight", "--step", "lockdown",
    ]) == 0
    directive = json.loads(capsys.readouterr().out)
    assert directive["tool"] == "check-lockdown"
    assert directive["payload"]["periods_json"] is None
    assert directive["payload"]["_gather"]["window"] == "2026-10-14..2026-11-04"
    assert client.load().steps == client.before.steps
    raw = [_ccoa_period(), _ccoa_period(name="Banner notice", environment="Banner")]
    assert "[attention]" in client.call(json.dumps(raw))
    held = client.load().get_step("preflight", "lockdown")
    assert held.status == "blocked" and held.by == "scout" and held.execution is None
    assert "Fixture CCOA" in held.note and "2026-10-14..2026-11-04" in held.note
    assert held.data == {} and held.links == []
    path = client.root / client.release / "release-state.json"
    held_before = path.read_bytes()
    client.call(json.dumps([_ccoa_period(start="unknown")]), expected=1)
    assert path.read_bytes() == held_before
    assert "[ok]" in client.call(json.dumps([raw[1]]))
    settled = client.load()
    step = settled.get_step("preflight", "lockdown")
    assert step.status == "done" and step.completed_at and step.by == "scout"
    assert step.execution is None and step.data == {} and step.links == []
    assert set(settled.steps) == set(client.before.steps) | {"preflight.lockdown"}
    for field in fields(ReleaseState):
        if field.name not in ("steps", "updated_at"):
            assert getattr(settled, field.name) == getattr(client.before, field.name)
    path = client.root / client.release / "release-state.json"
    before_replay = path.read_bytes()
    client.call(json.dumps([raw[1]]))
    assert path.read_bytes() == before_replay


def test_lockdown_completed_result_does_not_hide_malformed_new_input(lockdown_cli):
    import json

    client = lockdown_cli
    client.call("[]")
    state_path = client.root / client.release / "release-state.json"
    events_path = client.root / client.release / "events.jsonl"
    state_before, events_before = state_path.read_bytes(), events_path.read_bytes()
    client.call(json.dumps([_ccoa_period(start="unknown")]), expected=1)
    assert state_path.read_bytes() == state_before
    assert events_path.read_bytes() == events_before


def test_notice_build_real_recipients_and_html():
    """Real send: notice.build targets the REAL DL and renders a proper <table> +
    clean hotfix anchor. (Redirect to yourself is the send_to mock knob, not automatic.)"""
    from steps.preflight import notice
    from steps.lib import templating as T
    st = ReleaseState(release_id="2026-08", ccd="2026-08-12", ccd_source="default",
                      owner_email="pedroro@microsoft.com", owner_name="Pedro")
    out = _invoke(notice.build, st)
    html = out.payload["body"]
    assert "<table" in html and "</table>" in html
    assert ">the hotfix cherry-pick guide</a>" in html          # clean anchor, no raw URL
    assert "August" in html and "08/12/2026" in html and "@pedroro" in html
    assert "androididentity@microsoft.com" in out.payload["to"]  # real DL by default
    assert not out.payload["subject"].startswith("[TEST")
    assert T.ordinal(12) == "12th" and T.ordinal(21) == "21st"




@pytest.mark.parametrize("sent_at", ["2026-10-14T09:00:00-07:00", "2026-10-16T14:00:00-07:00"])
def test_flight_reminder_keeps_original_deadline_on_late_delivery(sent_at):
    from copy import deepcopy
    from datetime import datetime
    from steps.preflight import flight_reminder

    st = ReleaseState(
        release_id="2026-10", target_month="2026-11", ccd="2026-10-21",
        owner_name="<Owner & reviewer>", owner_email="owner@example.test",
    )
    before = deepcopy(st)
    out = flight_reminder.build(_context(st, now=datetime.fromisoformat(sent_at)))
    assert out.kind == "needs_skill" and out.tool == "workiq_send_chat_message"
    assert out.record_as == "flight_reminder" and out.outbound
    assert set(out.payload) == {"chatId", "content", "contentType"}
    assert out.payload["chatId"] == flight_reminder.CONFIG["live_chat_id"]
    assert out.payload["contentType"] == "html"
    html = out.payload["content"]
    assert "November" in html and "10/14/2026" in html
    assert "today" not in html.lower()
    assert "&lt;Owner &amp; reviewer&gt;" in html
    assert html.count("<li>") == 4
    for reminder in ("Update local flights", "Flight pre-mortem docs",
                     "Merge user-facing strings", "Feature-flag freeze"):
        assert reminder in html
    for url in flight_reminder.CONFIG["links"].values():
        assert url in html
    assert st == before and out.updates == ()


def test_flight_reminder_without_ccd_blocks_without_state_changes():
    from copy import deepcopy
    from steps.preflight import flight_reminder

    st = ReleaseState(release_id="2026-10")
    before = deepcopy(st)
    out = flight_reminder.build(_context(st))
    assert isinstance(out, Blocked) and out.reason == "no CCD set for this release"
    assert st == before and out.updates == ()


@pytest.fixture
def flight_reminder_cli(tmp_path, monkeypatch, capsys):
    from copy import deepcopy
    from datetime import datetime, timezone
    import json
    from types import SimpleNamespace
    from orchestrator import cli, delivery, mocks, schedule

    now = datetime.fromisoformat("2026-10-16T14:00:00-07:00")
    chat = "19:flight-reminder-fixture@thread.v2"
    monkeypatch.setattr(schedule, "now_local", lambda _tz=None: now)
    monkeypatch.setattr(delivery, "now_iso", lambda: now.astimezone(timezone.utc).isoformat())
    monkeypatch.setattr(mocks, "load_mocks",
                        lambda: {"preflight.flight_reminder": {"send_to": chat}})
    st = ReleaseState(
        release_id="2026-10", target_month="2026-11", ccd="2026-10-21",
        ccd_source="confirmed", timezone="America/Los_Angeles",
        owner_name="Fixture owner", owner_email="owner@example.test",
    )
    _active_phase(st, "preflight")
    C.save_state(st, str(tmp_path), st.release_id)

    def call(operation, *arguments, expected=0):
        code = cli.main([
            "--runs-root", str(tmp_path), "notification", operation,
            "--release", st.release_id, *arguments,
        ])
        result = json.loads(capsys.readouterr().out)
        assert code == expected, result
        return result

    return SimpleNamespace(
        call=call, load=lambda: C.load_state(str(tmp_path), st.release_id),
        before=deepcopy(st), root=tmp_path, chat=chat,
    )


def test_flight_reminder_cli_recovers_sent_post_without_duplicate(
        flight_reminder_cli, monkeypatch):
    from dataclasses import fields
    import json
    from unittest.mock import Mock
    from orchestrator.commands import delivery_cmd

    client = flight_reminder_cli
    key = "preflight.flight_reminder"
    prepared = client.call("prepare", "--source", "step", "--phase", "preflight",
                           "--step", "flight_reminder")
    item, = prepared["notifications"]
    assert item["tool"] == "workiq_send_chat_message"
    assert item["payload"]["chatId"] == client.chat
    assert item["payload"]["contentType"] == "html"
    assert "10/14/2026" in item["payload"]["content"]
    assert not item["permission_to_send"]
    assert client.load().steps == client.before.steps
    assert not client.load().is_done("preflight", "flight_reminder")

    claim = client.call("claim", "--id", item["id"], "--hash", item["hash"],
                        "--executor", "fixture-worker")
    assert claim["permission_to_send"] and claim["payload"] == item["payload"]
    claimed = client.load()
    step = claimed.get_step("preflight", "flight_reminder")
    assert step.status == "running"
    assert step.execution["id"] == claim["execution_id"]
    assert step.execution["notification_id"] == item["id"]
    transport = Mock(return_value={"id": "synthetic-teams-receipt", "chatId": client.chat})
    receipt = transport(**claim["payload"])
    receipt_file = client.root / "teams-receipt.json"
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")

    def interrupted_finalization(*_args):
        raise OSError("synthetic interruption after receipt persistence")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(delivery_cmd, "finish", interrupted_finalization)
        error = client.call(
            "result", "--id", item["id"], "--execution-id", claim["execution_id"],
            "--outcome", "sent", "--evidence", "synthetic Teams provider accepted post",
            "--receipt-file", str(receipt_file), expected=1,
        )
        assert "synthetic interruption" in error["error"]
    saved = client.load()
    assert saved.notification_deliveries[item["id"]]["status"] == "sent"
    assert not saved.is_done("preflight", "flight_reminder")
    assert saved.get_step("preflight", "flight_reminder").execution["id"] == claim["execution_id"]
    repeated = client.call("claim", "--id", item["id"], "--hash", item["hash"],
                           "--executor", "replacement-worker")
    assert repeated["status"] == "already_sent" and not repeated["permission_to_send"]
    assert client.call("finalize", "--id", item["id"])["finalized"]
    settled = client.load()
    step = settled.get_step("preflight", "flight_reminder")
    assert step.status == "done" and step.completed_at and step.execution is None
    assert step.data == {
        "notification_id": item["id"], "notification_execution_id": claim["execution_id"],
    }
    record = settled.notification_deliveries[item["id"]]
    assert record["completion"]["status"] == "applied"
    assert len(record["attempts"]) == 1 and record["attempts"][0]["receipt"] == receipt
    assert not settled.is_done("preflight", "confirm_reminders")
    assert set(settled.steps) == set(client.before.steps) | {key}
    for field in fields(ReleaseState):
        if field.name not in ("updated_at", "steps", "notification_deliveries"):
            assert getattr(settled, field.name) == getattr(client.before, field.name)
    assert client.call("prepare", "--source", "step", "--phase", "preflight",
                       "--step", "flight_reminder")["notifications"] == []
    assert not client.call("finalize", "--id", item["id"])["finalized"]
    transport.assert_called_once_with(**item["payload"])


@pytest.mark.parametrize("outcome", ["not_sent", "uncertain"])
def test_flight_reminder_cli_retry_requires_proven_not_sent(flight_reminder_cli, outcome):
    client = flight_reminder_cli
    item, = client.call("prepare", "--source", "step", "--phase", "preflight",
                        "--step", "flight_reminder")["notifications"]
    claim = client.call("claim", "--id", item["id"], "--hash", item["hash"],
                        "--executor", "fixture-worker")
    client.call("result", "--id", item["id"], "--execution-id", claim["execution_id"],
                "--outcome", outcome, "--evidence", f"synthetic transport outcome: {outcome}")
    saved = client.load()
    assert saved.notification_deliveries[item["id"]]["status"] == outcome
    assert not saved.is_done("preflight", "flight_reminder")
    assert not saved.is_done("preflight", "confirm_reminders")
    retry = client.call("claim", "--id", item["id"], "--hash", item["hash"],
                        "--executor", "replacement-worker",
                        expected=0 if outcome == "not_sent" else 1)
    if outcome == "not_sent":
        assert retry["permission_to_send"]
        assert retry["execution_id"] != claim["execution_id"]
        assert retry["payload"] == claim["payload"]
    else:
        assert not retry["permission_to_send"]
        assert "uncertain" in retry["error"]
        assert len(client.load().notification_deliveries[item["id"]]["attempts"]) == 1


def test_confirm_reminders_is_attestation_hold():
    """In the parallel Phase 0, confirm_reminders is a human attestation that becomes
    ready only after flight_reminder is sent; it surfaces as a hold with a confirm
    pill and clears via `done`."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()
    _clear_notice(orch)
    orch.record_scout_step("preflight", "flight_reminder", "pass", "sent")
    orch.run_until_gate()
    assert orch.status_report()["status"] == "awaiting_action"
    # confirm_reminders (dep on flight_reminder, now done) is among the pending holds
    assert "preflight.confirm_reminders" in orch.status_report()["pending_human"]
    ap = orch.status_report()["active_phase"]
    step = next(s for s in ap["steps"] if s["id"] == "confirm_reminders")
    assert step["status"] == "confirm" and step["needs_owner"]
    # owner attests → advances
    orch.complete_step("preflight", "confirm_reminders", "verified with feature owners")
    assert st.is_done("preflight", "confirm_reminders")




def test_confirm_reminders_gated_by_flight_send():
    """confirm_reminders must NOT be offered until flight_reminder is sent (dependency)."""
    st, orch = _orch(signed=False)
    _pass_scout_checks(orch)
    orch.gate.sign()
    _clear_notice(orch)
    # flight_reminder NOT yet recorded → confirm_reminders is not ready
    orch.run_until_gate()
    report = orch.status_report()
    assert "preflight.confirm_reminders" not in report["pending_human"]
    assert "flight_reminder" in report["scout_pending"]




def test_cg_report_summarizes_and_flags_high():
    """The CG report groups active alerts by severity and lists High/Critical."""
    from steps.preflight.cg import _cg_summary, _cg_report
    alerts = [
        {"alertState": "active", "severity": "high", "title": "CVE-1",
         "component": {"displayName": "io.netty:x", "displayVersion": "4.2.15"},
         "actionItems": "Upgrade to 4.2.16"},
        {"alertState": "active", "severity": "medium", "title": "CVE-2"},
        {"alertState": "autoDismissed", "severity": "high", "title": "CVE-OLD"},
        {"alertState": "fixed", "severity": "critical", "title": "CVE-FIXED"},
    ]
    active, high = _cg_summary(alerts, ["critical", "high"])
    assert len(active) == 2 and len(high) == 1
    rep = _cg_report(active, high)
    assert "2 active alert(s)" in rep and "1 high" in rep
    assert "CVE-1" in rep and "io.netty:x" in rep and "Upgrade to 4.2.16" in rep
    assert "CVE-OLD" not in rep and "CVE-FIXED" not in rep   # non-active excluded




def test_cg_agent_blocks_on_high():
    """High/Critical active alerts return Blocked with a fix-and-rerun reason."""
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (True, [
        {"alertState": "active", "severity": "high", "title": "CVE-9",
         "component": {"displayName": "pkg", "displayVersion": "1.0"}},
    ], "ok")
    try:
        r = _invoke(cg.build, None)
        assert isinstance(r, Blocked)         # High → blocks
        assert "CVE-9" in r.reason and "RERUN" in r.reason
    finally:
        checks.fetch_cg_alerts = orig




def test_cg_agent_passes_when_no_high():
    """Only Medium/Low active alerts → the step passes (report captured)."""
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (True, [
        {"alertState": "active", "severity": "medium", "title": "CVE-M"},
    ], "ok")
    try:
        r = _invoke(cg.build, None)
        assert isinstance(r, Done) and "1 active" in r.note
    finally:
        checks.fetch_cg_alerts = orig




def test_oneauth_access_granted():
    """Injected access='granted' → the step passes and links the repo + access package."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import ReleaseState
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"alias": "pedroro", "access": "granted"}):
        out = as_dict(_invoke(_steps.get_step("preflight", "oneauth_access").build, st))
    assert out["kind"] == "done"
    assert "pedroro" in out["note"] and "write access confirmed" in out["note"]
    assert any("access-packages" in l["url"] for l in out["links"])


def test_oneauth_effect_freezes_alias_and_repository():
    from orchestrator.state import ReleaseState, StepState
    from steps.preflight import oneauth_access as step
    from steps.lib import mockctx
    from tools import checks

    state = ReleaseState(release_id="2026-08")
    with mockctx.active({"alias": "first"}):
        frozen = step.prepare_effect(_context(state))
    state.set_step(
        "preflight",
        step.ID,
        StepState(
            status="running",
            execution={
                "effect_input": frozen,
                "effect_mode": "idempotent",
                "effect_recovery": "frozen",
                "operation_key": "key",
                "id": "effect",
                "owner": "engine",
                "started_at": "2026-09-12T00:00:00Z",
                "refresh": False,
            },
        ),
    )
    seen = {}
    original = checks.oneauth_write_access

    def access(alias, timeout=60, **coordinates):
        seen.update(alias=alias, **coordinates)
        return True, "ok"

    checks.oneauth_write_access = access
    try:
        with mockctx.active({"alias": "second"}):
            assert step.prepare_effect(_context(state)) == frozen
            assert _invoke_effect(step.execute,
                state, state.get_step("preflight", step.ID).execution
            ).kind == "done"
    finally:
        checks.oneauth_write_access = original

    assert seen == {
        "alias": "first",
        "org": checks.ONEAUTH_ORG,
        "project": checks.ONEAUTH_PROJECT,
        "repo": checks.ONEAUTH_REPO,
    }




def test_oneauth_access_denied_blocks():
    """Injected access='denied' → BLOCKED, pointing at the access package + a rerun instruction."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import ReleaseState
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"alias": "pedroro", "access": "denied"}):
        out = as_dict(_invoke(_steps.get_step("preflight", "oneauth_access").build, st))
    assert out["kind"] == "blocked"
    assert "no write access" in out["reason"] and "RERUN" in out["reason"]
    assert any("access-packages" in l["url"] for l in out["links"])




def test_oneauth_access_blocks_without_alias():
    """No resolvable alias → BLOCKED (can't form the user/<alias>/… probe branch)."""
    import steps as _steps
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    from orchestrator.state import ReleaseState
    from tools import checks
    st = ReleaseState(release_id="2026-08")
    o = checks.current_az_user
    checks.current_az_user = lambda *a, **k: None
    try:
        with mockctx.active({}):
            out = as_dict(_invoke(_steps.get_step("preflight", "oneauth_access").build, st))
    finally:
        checks.current_az_user = o
    assert out["kind"] == "blocked" and "alias" in out["reason"]




def test_cg_blocked_step_reruns_and_clears_when_fixed():
    """A CG block holds the step; fixing (alerts now clean) + rerunning `next`
    re-checks and lets the flow continue."""
    st, orch = _mock_orch({"preflight.cg": {"outcome": "blocked",
                          "reason": "CG: 1 critical active\n→ Fix and RERUN or skip."}})
    _clear_phase0_scout(orch)          # clear the earlier scout/human holds
    orch.run_until_gate()
    report = orch.status_report()
    assert report["status"] == "awaiting_action"
    assert report["current_step"] == "cg"
    assert st.get_step("preflight", "cg").status == "blocked"
    # the digest shows it blocked / needs owner
    step = next(s for s in orch.status_report()["active_phase"]["steps"] if s["id"] == "cg")
    assert step["status"] == "blocked" and step["needs_owner"]
    # FIX: alerts now clean → RERUN (next) re-checks and passes
    orch.mocks["preflight.cg"] = {"outcome": "done", "note": "CG: 0 active alerts."}
    orch.run_until_gate()
    assert st.is_done("preflight", "cg")
    assert "preflight.cg" not in orch.status_report()["pending_human"]




def test_cg_blocked_step_skip_override():
    """The owner can override a CG block by skipping the step (with a reason)."""
    st, orch = _mock_orch({"preflight.cg": {"outcome": "blocked", "reason": "CG: 1 high active"}})
    _clear_phase0_scout(orch)
    orch.run_until_gate()
    assert orch.status_report()["current_step"] == "cg"
    assert st.get_step("preflight", "cg").status == "blocked"
    orch.skip_step("preflight", "cg", "accepted risk; tracked separately")
    assert st.is_done("preflight", "cg")   # skipped counts as done
    assert st.get_step("preflight", "cg").status == "skipped"
    assert "preflight.cg" not in orch.status_report()["pending_human"]


def test_cli_skip_surfaces_the_next_parallel_human_hold(tmp_path, capsys, monkeypatch):
    from orchestrator import cli, mocks
    from orchestrator.state import StepState

    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    rid = "2026-08"
    st, orch = _orch()
    st.release_id = rid
    for step in orch.config["phases"][0]["steps"]:
        if step["id"] not in ("cg", "vitals"):
            st.set_step("preflight", step["id"], StepState(status="done"))
    st.set_step("preflight", "cg", StepState(status="blocked", note="CG alert"))
    st.set_step("preflight", "vitals", StepState(status="pending"))
    C.save_state(st, str(tmp_path), rid)

    rc = cli.main([
        "--runs-root", str(tmp_path),
        "skip", "--release", rid,
        "--phase", "preflight", "--step", "cg",
        "--reason", "accepted risk",
    ])
    capsys.readouterr()
    saved = C.load_state(str(tmp_path), rid)
    assert rc == 0
    report = Orchestrator(CONFIG, saved, mocks={}).status_report()
    assert report["status"] == "awaiting_action"
    assert report["current_step"] == "vitals"
    assert report["pending_human"] == ["preflight.vitals"]


def test_cg_agent_fetch_error_holds():
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.fetch_cg_alerts
    checks.fetch_cg_alerts = lambda *a, **k: (False, [], "403 forbidden")
    try:
        r = _invoke(cg.build, None)
        assert isinstance(r, Blocked) and "could not read alerts" in r.reason
    finally:
        checks.fetch_cg_alerts = orig




def test_cron_check_passes_on_recent_scheduled_run():
    from steps.preflight import breaking, cg, cron
    from tools import checks
    from datetime import datetime, timezone
    orig = checks.latest_scheduled_build
    now_iso = datetime.now(timezone.utc).isoformat()
    checks.latest_scheduled_build = lambda *a, **k: (True, {
        "queueTime": now_iso, "result": "succeeded", "status": "completed"}, "ok")
    try:
        r = _invoke(cron.build, None)
        assert isinstance(r, Done) and "scheduled and firing" in r.note
    finally:
        checks.latest_scheduled_build = orig




def test_cron_check_blocks_when_stale():
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.latest_scheduled_build
    checks.latest_scheduled_build = lambda *a, **k: (True, {
        "queueTime": "2026-01-01T06:00:00Z", "result": "succeeded", "status": "completed"}, "ok")
    try:
        r = _invoke(cron.build, None)
        assert isinstance(r, Blocked) and "stale" in r.reason
    finally:
        checks.latest_scheduled_build = orig




def test_cron_check_blocks_when_no_scheduled_run():
    from steps.preflight import breaking, cg, cron
    from tools import checks
    orig = checks.latest_scheduled_build
    checks.latest_scheduled_build = lambda *a, **k: (True, None, "no scheduled runs in recent history")
    try:
        r = _invoke(cron.build, None)
        assert isinstance(r, Blocked) and "no scheduled run" in r.reason
    finally:
        checks.latest_scheduled_build = orig


def test_cron_live_freshness_ignores_simulated_release_date(monkeypatch):
    """Live provider timestamps use trusted current UTC, not a future --as-of clock."""
    from datetime import datetime, timezone
    from steps.preflight import cron
    from tools import checks

    monkeypatch.setattr(checks, "latest_scheduled_build", lambda *a, **k: (True, {
        "queueTime": "2026-09-14T06:00:00Z",
        "result": "succeeded",
        "status": "completed",
    }, "ok"))
    monkeypatch.setattr(
        cron, "_trusted_utc_now",
        lambda: datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc),
    )
    result = cron.build(_context(
        None, now=datetime(2026, 10, 8, 23, 59, tzinfo=timezone.utc)))
    assert isinstance(result, Done)
    assert "2026-09-14T06:00" in result.note


def test_cron_mock_freshness_uses_simulated_release_date():
    """Injected historical evidence remains deterministic for offline simulations."""
    from datetime import datetime, timezone
    from steps.preflight import cron

    result = cron.build(_context(
        None,
        inputs={"run": {
            "queueTime": "2026-10-05T06:00:00Z",
            "result": "succeeded",
            "status": "completed",
        }},
        now=datetime(2026, 10, 8, 23, 59, tzinfo=timezone.utc),
    ))
    assert isinstance(result, Blocked)
    assert "3d ago" in result.reason


def test_vitals_is_attestation_hold():
    """Play Console vitals/policy is an attest step (no API for policy): it HOLDS
    for the owner to confirm they reviewed it, and clears via `done`."""
    st, orch = _orch()   # clears the earlier holds (incl. vitals via _clear_phase0_scout)
    # re-open vitals to observe its natural hold
    orch.reopen_step("preflight", "vitals")
    orch.run_until_gate()
    report = orch.status_report()
    assert report["status"] == "awaiting_action"
    assert report["current_step"] == "vitals"
    step = next(s for s in orch.status_report()["active_phase"]["steps"] if s["id"] == "vitals")
    assert step["status"] == "confirm" and step["needs_owner"]
    orch.complete_step("preflight", "vitals", "reviewed vitals + policy status in Play Console")
    assert st.is_done("preflight", "vitals")




def test_create_payload_wiki_dry_run_and_execute(capsys):
    """create-payload-wiki --dry-run previews (no write); --execute creates the page and
    records the step done with the page link."""
    import tempfile as _tf
    from orchestrator.commands import payload_wiki_cmd as PW
    from tools import checks
    with _tf.TemporaryDirectory() as d:
        rid = "2026-08"
        _stub_build_defs("pass")
        st = ReleaseState(release_id=rid, ccd="2026-08-13", owner_email="dev@microsoft.com")
        st.versions = {"authenticator": "release/2026/08/13", "broker": "16.5.0",
                       "common": "24.6.0", "msal": "8.4.2"}
        _active_step(st, "rollout_start", "wiki_payload")
        orch = Orchestrator(CONFIG, st)
        C = __import__("orchestrator.cli_common", fromlist=["x"])
        C.save_state(st, d, rid)
        # the step's version/prs mocks so compose_payload runs offline (no ADO)
        orch_mocks = {"rollout_start.wiki_payload": {
            "version": {"version": "6.2608.5658", "build_url": "https://x/y"},
            "prs": [{"id": 1, "title": "Feature"}]}}

        # patch the checks helpers so no network/az
        oe, oc, og = checks.wiki_page_exists, checks.create_wiki_page, checks.get_wiki_page
        created = {}
        checks.wiki_page_exists = lambda *a, **k: False        # absent → create
        def _create(org, project, wiki, path, content, timeout=60, *, require_absent=False):
            assert require_absent
            created["path"] = path
            created["content"] = content
            return checks.CheckResult(True, True, "created")
        checks.create_wiki_page = _create
        checks.get_wiki_page = lambda **kwargs: (True, created["content"], "etag", "")

        class A:
            runs_root = d; release = rid; config = CONFIG; as_of = None
            execute = False; dry_run = True; reserve = False
            execution_id = None; approved_by = "test-reviewer"; executor = "test-executor"

        # surface the step mocks on the orch the command loads (so compose runs offline)
        import orchestrator.cli_common as _CC
        real_load = _CC.load_orch
        def fake_load(runs_root, release, config, as_of=None):
            s, o = real_load(runs_root, release, config, as_of)
            o.mocks = orch_mocks
            return s, o
        _CC.load_orch = fake_load
        try:
            assert PW.cmd_create_payload_wiki(A) == 0        # dry-run
            out = capsys.readouterr().out
            preview = __import__("json").loads(out)
            assert "#App Version" in preview["plan"]["operations"][0]["content"]["content"]
            assert preview["permission_to_execute"] is False
            assert not created                                # nothing written on dry-run

            A.execute = True; A.dry_run = False
            A.review_hash = preview["review_hash"]
            with C.state_lock(d, rid):
                assert PW.cmd_create_payload_wiki(A) == 0
            assert created["path"].endswith("September 2026 Release")
            s2 = C.load_state(d, rid)
            assert s2.is_done("rollout_start", "wiki_payload")
            step = s2.get_step("rollout_start", "wiki_payload")
            assert step.links and "pagePath=" in step.links[0]["url"]
        finally:
            _CC.load_orch = real_load
            checks.wiki_page_exists, checks.create_wiki_page, checks.get_wiki_page = oe, oc, og
