"""RC evidence stays immutable on disk and logically unchanged for its consumers."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import PureWindowsPath
import shutil

import pytest

from orchestrator import delivery, evidence_store
from orchestrator.state import ReleaseState, StepState
from tests._auth_evidence import auth_snapshot
from tests._context import context, fresh_orchestrator as Orchestrator
from tests._harness import CONFIG, _bv_build, _bv_state, _ready_for_rc_report, _seed_rc_pipeline
from tests._mrwp_evidence import PROD, RC, current_rc, snapshot
from tools import pipelines as P


NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
LANES = {"ecs": "mrwp-ecs", "local": "mrwp-local", "auth": "auth-ecs"}


def _state(count=3):
    rows = [(f"test_{1000 + i}_Storage_\u00e9", "Failed" if i % 3 == 0 else "Passed")
            for i in range(count)]
    rc = current_rc(ecs={PROD: rows}, local={RC: rows}, rc=1)
    rc["auth"] = auth_snapshot(rows={
        P.AUTH_UI_SUITES[0]: rows,
        P.AUTH_UI_SUITES[1]: [("monthly_storage_failure", "Failed")],
    }, rc=1)
    return ReleaseState(
        release_id="2026-08", ccd="2026-08-26", owner_email="owner@example.test",
        pipeline_runs={
            "checker": {"run_id": "101", "when": "2026-08-26T06:00"},
            "orchestrator": {"run_id": "102", "parked": True},
            "rcs": [rc],
        },
    )


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _artifact(path, lane):
    return path.parent.joinpath(*PureWindowsPath(lane["evidence_ref"]["path"]).parts)


def _files(directory):
    return {p.relative_to(directory): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()}


def _assert_no_temporary(directory):
    assert not [p for p in directory.rglob("*")
                if p.name.startswith(".evidence-") or p.name.endswith(".tmp")]


def _replace_envelope(path, raw, lane, document):
    summary = raw["pipeline_runs"]["rcs"][0][lane]
    old_path = _artifact(path, summary)
    content = json.dumps(document, sort_keys=True).encode("utf-8")
    digest = sha256(content).hexdigest()
    new_path = old_path.with_name(f"{LANES[lane]}-{digest}.json")
    new_path.write_bytes(content)
    summary["evidence_ref"].update(
        sha256=digest, path=str(PureWindowsPath(*new_path.relative_to(path.parent).parts)))
    _write(path, raw)


@pytest.fixture
def saved(tmp_path):
    state = _state()
    path = tmp_path / state.release_id / "release-state.json"
    state.save(path)
    return state, path, _read(path)


def test_all_lanes_roundtrip_exactly_with_small_state_and_deduplicated_failures(tmp_path):
    state = _state(count=300)
    path = tmp_path / "release-state.json"
    logical = deepcopy(state.pipeline_runs)
    state.save(path)
    raw = _read(path)
    assert raw["schema_version"] == 3
    assert state.pipeline_runs == logical
    assert asdict(ReleaseState.load(path)) == asdict(state)
    assert path.stat().st_size < 12_000
    assert path.stat().st_size < len(json.dumps(logical).encode("utf-8")) // 5
    assert raw["pipeline_runs"]["checker"] == logical["checker"]
    assert raw["pipeline_runs"]["orchestrator"] == logical["orchestrator"]

    for lane, prefix in LANES.items():
        summary = raw["pipeline_runs"]["rcs"][0][lane]
        reference = summary["evidence_ref"]
        artifact = _artifact(path, summary)
        content = artifact.read_bytes()
        envelope = json.loads(content)
        assert reference["schema_version"] == 1
        assert reference["sha256"] == sha256(content).hexdigest()
        assert reference["path"] == f"evidence\\rc1\\{prefix}-{reference['sha256']}.json"
        assert set(envelope) == {"schema_version", "release_id", "rc", "lane", "snapshot", "aliases"}
        assert (envelope["schema_version"], envelope["release_id"], envelope["rc"], envelope["lane"]) == (
            1, state.release_id, 1, lane)
        assert "test_1000_Storage" not in json.dumps(summary)
        if lane != "auth":
            assert "suites" not in summary["tests"]
            assert "failed_suites" not in summary
            assert envelope["aliases"] == ["tests.failed_suites", "failed_suites"]
            assert "failed_suites" not in envelope["snapshot"]
            indices = envelope["snapshot"]["tests"]["failed_suites"]["suite_indices"]
            assert indices
            assert [envelope["snapshot"]["tests"]["suites"][i] for i in indices] == (
                logical["rcs"][0][lane]["failed_suites"])
        else:
            assert envelope["aliases"] == []
            assert envelope["snapshot"] == logical["rcs"][0][lane]


@pytest.mark.parametrize("alias", ["different_top_level", "unmatched_suite", "absent_top_level"])
def test_nonidentical_or_absent_failure_aliases_are_not_lost(tmp_path, alias):
    state = _state()
    lane = state.pipeline_runs["rcs"][0]["ecs"]
    if alias == "different_top_level":
        lane["failed_suites"] = [{"name": "separate legacy detail", "tests": []}]
    elif alias == "unmatched_suite":
        lane["tests"]["failed_suites"].append({"name": "legacy suite not in suites"})
    else:
        lane.pop("failed_suites")
    original = deepcopy(state.pipeline_runs)
    path = tmp_path / "release-state.json"
    state.save(path)
    assert ReleaseState.load(path).pipeline_runs == original


@pytest.mark.parametrize("location", ["tests.failed_suites", "failed_suites"])
@pytest.mark.parametrize("json_distinct", [1.0, True], ids=["float", "bool"])
def test_failure_aliases_require_exact_json_not_python_equality(tmp_path, location, json_distinct):
    state = _state()
    lane = state.pipeline_runs["rcs"][0]["ecs"]
    suite = deepcopy(lane["tests"]["suites"][0])
    suite["storage_identity"] = {"value": 1}
    lane["tests"]["suites"] = [suite]
    lane["tests"]["failed_suites"] = [deepcopy(suite)]
    lane["failed_suites"] = [deepcopy(suite)]
    target = lane["tests"] if location == "tests.failed_suites" else lane
    target["failed_suites"][0]["storage_identity"]["value"] = json_distinct
    assert target["failed_suites"] == [suite]
    assert delivery.fingerprint(target["failed_suites"]) != delivery.fingerprint([suite])
    fingerprint = delivery.fingerprint(state.pipeline_runs)

    path = tmp_path / "release-state.json"
    state.save(path)
    summary = _read(path)["pipeline_runs"]["rcs"][0]["ecs"]
    envelope = _read(_artifact(path, summary))
    assert envelope["aliases"] == ([] if location == "tests.failed_suites"
                                   else ["tests.failed_suites"])
    loaded = ReleaseState.load(path)
    assert delivery.fingerprint(loaded.pipeline_runs) == fingerprint
    assert delivery.fingerprint(state.pipeline_runs) == fingerprint


@pytest.mark.parametrize("json_distinct", [1.0, True], ids=["float", "bool"])
def test_failure_alias_chooses_json_exact_suite_among_python_equal_candidates(tmp_path, json_distinct):
    state = _state()
    lane = state.pipeline_runs["rcs"][0]["ecs"]
    exact = deepcopy(lane["tests"]["suites"][0])
    exact["storage_identity"] = {"value": 1}
    other = deepcopy(exact)
    other["storage_identity"]["value"] = json_distinct
    lane["tests"]["suites"] = [other, exact]
    lane["tests"]["failed_suites"] = [deepcopy(exact)]
    lane["failed_suites"] = [deepcopy(exact)]
    assert other == exact
    fingerprint = delivery.fingerprint(state.pipeline_runs)

    path = tmp_path / "release-state.json"
    state.save(path)
    summary = _read(path)["pipeline_runs"]["rcs"][0]["ecs"]
    envelope = _read(_artifact(path, summary))
    assert envelope["aliases"] == ["tests.failed_suites", "failed_suites"]
    assert envelope["snapshot"]["tests"]["failed_suites"] == {"suite_indices": [1]}
    assert delivery.fingerprint(ReleaseState.load(path).pipeline_runs) == fingerprint


@pytest.mark.parametrize("rc_value", ["absent", None, True, 0, -1, "unknown"])
def test_incomplete_legacy_observation_without_valid_rc_migrates_losslessly(tmp_path, rc_value):
    from steps.build_verify import rc_report

    state = _state()
    rc = state.pipeline_runs["rcs"][0]
    if rc_value == "absent":
        rc.pop("rc")
    else:
        rc["rc"] = rc_value
    rc["ecs"].update(complete=False, tests_error="Legacy capture did not complete")
    rc["auth"]["test"]["complete"] = False
    state.set_step("build_verify", "mrwp_ecs", StepState(
        status="blocked", note="Legacy capture did not complete"))
    original = delivery.fingerprint(state.pipeline_runs)
    path = tmp_path / "release-state.json"
    _write(path, asdict(state))
    inline = path.read_bytes()

    loaded = ReleaseState.load(path)
    assert path.read_bytes() == inline
    assert not (tmp_path / "evidence").exists()
    assert delivery.fingerprint(loaded.pipeline_runs) == original
    loaded.save(path)
    raw = _read(path)
    for lane in LANES:
        summary = raw["pipeline_runs"]["rcs"][0][lane]
        assert summary["evidence_ref"]["path"].startswith("evidence\\unresolved\\")
        envelope = _read(_artifact(path, summary))
        assert delivery.fingerprint(envelope["rc"]) == delivery.fingerprint(
            None if rc_value == "absent" else rc_value)

    hydrated = ReleaseState.load(path)
    assert delivery.fingerprint(hydrated.pipeline_runs) == original
    assert hydrated.steps == state.steps
    assert hydrated.pipeline_runs["rcs"][0]["ecs"]["complete"] is False
    assert hydrated.pipeline_runs["rcs"][0]["auth"]["test"]["complete"] is False
    assert not P.project_mrwp_ui_results(hydrated.pipeline_runs["rcs"][0])[0]
    assert not P.project_auth_ui_results(hydrated.pipeline_runs["rcs"][0])[0]
    assert rc_report.build(context(hydrated, now=NOW)).kind == "blocked"


def test_real_capture_handlers_save_and_reload_each_lane(tmp_path):
    ecs = snapshot(201, {PROD: [("test_1001_ECS", "Failed"), ("test_1002_ECS", "Passed")]})
    local = snapshot(202, {RC: [("test_1001_Local", "Passed")]})
    stages = [{"name": "UI Automation", "state": "completed", "result": "succeeded"}]
    state, orch = _bv_state({
        "build_verify.mrwp_ecs": {
            "mrwp_id": "201", "rc": 1, "tests": ecs["tests"], "stages": stages},
        "build_verify.mrwp_local": {
            "mrwp_id": "202", "rc": 1, "tests": local["tests"], "stages": stages},
    })
    path = tmp_path / "release-state.json"
    for step in ("mrwp_ecs", "mrwp_local", "auth_ecs"):
        result = _bv_build(orch, state, step)
        assert result["kind"] == "done", result
        expected = deepcopy(state.pipeline_runs)
        state.save(path)
        state = ReleaseState.load(path)
        assert state.pipeline_runs == expected
    assert set(_read(path)["pipeline_runs"]["rcs"][0]) >= {"ecs", "local", "auth"}
    assert len(list((path.parent / "evidence").rglob("*.json"))) == 3
    assert state.pipeline_runs["rcs"][0]["ecs"]["tests"]["suites"]
    assert state.pipeline_runs["rcs"][0]["auth"]["test"]["suites"]


def _consumer_outputs(state):
    from orchestrator import write_review
    from orchestrator.commands.rc_report_publish import plan_rc_report_publish
    from steps.build_verify import rc_report

    ctx = context(state, now=NOW)
    email = rc_report.rc_email(ctx)
    lineage = rc_report.report_lineage(ctx, email[1])
    descriptor = delivery.descriptor(
        state, "storage-regression", {
            "kind": "release", "state_matches": rc_report.report_source_matches(ctx),
        }, "workiq_send_email", {"to": [state.owner_email], "subject": email[0], "body": email[1]})
    orch = Orchestrator(CONFIG, state, mocks={}, as_of=date(2026, 9, 11), now=NOW)
    plan = plan_rc_report_publish(orch)
    return {
        "email": email, "lineage": lineage, "descriptor": descriptor,
        "write_plan": plan,
        "write_hash": write_review.review_hash(orch, "build_verify", "rc_report_publish", plan),
        "mrwp_gate": rc_report.rc_ui_gate(email[3]),
        "auth_gate": rc_report.auth_report_gate(email[3]),
    }


def test_legacy_inline_read_is_pure_then_save_preserves_lineage_and_delivery(tmp_path):
    state = ReleaseState(release_id="2026-08", ccd="2026-08-26", owner_email="owner@example.test")
    _ready_for_rc_report(state)
    _seed_rc_pipeline(state, {"total": 10, "passed": 9, "failed": 1},
                      {"total": 10, "passed": 10, "failed": 0})
    state.set_step("build_verify", "rc_report_gate", StepState(
        status="blocked", by="human", note="Owner reviewing exact evidence", data={"review": "retained"}))
    state.set_step("bug_bash", "native_auth_signoff", StepState(status="skipped", by="human"))
    state.gate_decisions.append({
        "step": "build_verify.rc_report_gate", "decision": "denied",
        "at": NOW.isoformat(), "by": "human", "comment": "Awaiting rerun",
    })
    before = _consumer_outputs(state)
    item = before["descriptor"]
    state.notification_deliveries[item["id"]] = {
        "descriptor": item, "status": "prepared", "attempts": [],
    }
    path = tmp_path / "release-state.json"
    _write(path, asdict(state))
    original = _files(tmp_path)

    loaded = ReleaseState.load(path)
    assert _files(tmp_path) == original
    assert not (tmp_path / "evidence").exists()
    assert asdict(loaded) == asdict(state)
    assert _consumer_outputs(loaded) == before

    loaded.save(path)
    assert len(list((tmp_path / "evidence").rglob("*.json"))) == 3
    hydrated = ReleaseState.load(path)
    assert hydrated.steps == state.steps
    assert hydrated.gate_decisions == state.gate_decisions
    assert hydrated.notification_deliveries == state.notification_deliveries
    assert _consumer_outputs(hydrated) == before
    assert "evidence_ref" not in json.dumps(hydrated.pipeline_runs)


def test_recapture_changes_only_one_lane_and_retains_old_rc_and_removed_evidence(saved):
    state, path, raw = saved
    previous_rc = deepcopy(state.pipeline_runs["rcs"][0])
    next_rc = deepcopy(previous_rc)
    next_rc["rc"] = 2
    next_rc["auth"] = auth_snapshot(rc=2)
    state.pipeline_runs["rcs"].append(next_rc)
    state.save(path)
    before = _files(path.parent / "evidence")
    refs = _read(path)["pipeline_runs"]["rcs"]
    assert len(before) == 6
    state.pipeline_runs["rcs"][1]["ecs"]["resolved_at"] = "2026-09-11T12:00:00Z"
    state.save(path)
    after = _files(path.parent / "evidence")
    updated = _read(path)["pipeline_runs"]["rcs"]
    assert len(after) == 7
    assert all(after[key] == content for key, content in before.items())
    assert updated[0] == refs[0] == raw["pipeline_runs"]["rcs"][0]
    assert updated[1]["local"] == refs[1]["local"]
    assert updated[1]["auth"] == refs[1]["auth"]
    assert updated[1]["ecs"]["evidence_ref"] != refs[1]["ecs"]["evidence_ref"]
    state.pipeline_runs["rcs"][1].pop("local")
    state.pipeline_runs["rcs"].pop(0)
    state.save(path)
    assert _files(path.parent / "evidence") == after
    assert ReleaseState.load(path).pipeline_runs == state.pipeline_runs


def test_identical_saves_do_not_rewrite_immutable_files(saved):
    state, path, raw = saved
    evidence = path.parent / "evidence"
    for artifact in evidence.rglob("*.json"):
        os.utime(artifact, ns=(946684800000000000, 946684800000000000))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in evidence.rglob("*.json")}
    state.save(path)
    ReleaseState.load(path).save(path)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in evidence.rglob("*.json")} == before
    assert _read(path)["pipeline_runs"] == raw["pipeline_runs"]
    _assert_no_temporary(path.parent)


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("damage", ["missing", "hash"])
def test_missing_or_changed_snapshot_fails_closed_without_refetch(saved, lane, damage):
    _, path, raw = saved
    artifact = _artifact(path, raw["pipeline_runs"]["rcs"][0][lane])
    if damage == "missing":
        artifact.unlink()
    else:
        artifact.write_bytes(artifact.read_bytes() + b" ")
    before = _files(path.parent)
    with pytest.raises(evidence_store.EvidenceError, match="unavailable|SHA-256"):
        ReleaseState.load(path)
    assert _files(path.parent) == before


@pytest.mark.parametrize("lane", LANES)
def test_tampered_compact_summary_is_not_trusted(saved, lane):
    _, path, raw = saved
    summary = raw["pipeline_runs"]["rcs"][0][lane]
    if lane == "auth":
        summary["verdict"] = "changed verdict"
    else:
        summary["tests"]["categories"]["ui"]["passed"] += 1
    _write(path, raw)
    with pytest.raises(evidence_store.EvidenceError, match="summary"):
        ReleaseState.load(path)


@pytest.mark.parametrize("change", ["bool_to_int", "int_to_float"])
def test_python_equal_but_json_distinct_compact_summary_is_rejected(saved, change):
    _, path, raw = saved
    summary = raw["pipeline_runs"]["rcs"][0]["ecs"]
    if change == "bool_to_int":
        summary["complete"] = 1
    else:
        summary["tests"]["total"] = float(summary["tests"]["total"])
    _write(path, raw)
    with pytest.raises(evidence_store.EvidenceError, match="summary"):
        ReleaseState.load(path)


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("schema_version", True), ("release_id", "2026-09"),
    ("rc", 2), ("rc", "1"), ("lane", "local"), ("snapshot", []),
    ("aliases", ["unknown"]), ("aliases", ["failed_suites"]),
])
def test_hash_valid_but_invalid_envelope_is_rejected(saved, field, value):
    _, path, raw = saved
    document = _read(_artifact(path, raw["pipeline_runs"]["rcs"][0]["ecs"]))
    document[field] = value
    _replace_envelope(path, raw, "ecs", document)
    with pytest.raises(evidence_store.EvidenceError):
        ReleaseState.load(path)


@pytest.mark.parametrize("damage", ["missing_field", "extra_field", "nested_reference", "bad_indices", "json"])
def test_hash_valid_malformed_snapshot_is_rejected(saved, damage):
    _, path, raw = saved
    document = _read(_artifact(path, raw["pipeline_runs"]["rcs"][0]["ecs"]))
    if damage == "missing_field":
        document.pop("snapshot")
    elif damage == "extra_field":
        document["unexpected"] = True
    elif damage == "nested_reference":
        document["snapshot"]["evidence_ref"] = {}
    elif damage == "bad_indices":
        document["snapshot"]["tests"]["failed_suites"]["suite_indices"] = [True]
    else:
        summary = raw["pipeline_runs"]["rcs"][0]["ecs"]
        content = b"not a JSON document"
        digest = sha256(content).hexdigest()
        artifact = _artifact(path, summary).with_name(f"mrwp-ecs-{digest}.json")
        artifact.write_bytes(content)
        summary["evidence_ref"].update(
            sha256=digest, path=f"evidence\\rc1\\{artifact.name}")
        _write(path, raw)
    if damage != "json":
        _replace_envelope(path, raw, "ecs", document)
    with pytest.raises(evidence_store.EvidenceError):
        ReleaseState.load(path)


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("schema_version", True), ("sha256", "not-a-digest"),
    ("sha256", "A" * 64), ("path", "..\\outside.json"),
    ("path", "evidence\\rc1\\..\\outside.json"), ("path", "/outside.json"),
    ("path", "C:\\outside.json"), ("path", "\\\\server\\share\\outside.json"),
    ("path", "evidence\\rc2\\snapshot.json"),
])
def test_invalid_or_escaping_reference_fails_closed(saved, field, value):
    _, path, raw = saved
    raw["pipeline_runs"]["rcs"][0]["ecs"]["evidence_ref"][field] = value
    _write(path, raw)
    with pytest.raises(evidence_store.EvidenceError):
        ReleaseState.load(path)


def test_cross_lane_reference_cannot_substitute_another_valid_snapshot(saved):
    _, path, raw = saved
    rc = raw["pipeline_runs"]["rcs"][0]
    rc["ecs"]["evidence_ref"] = deepcopy(rc["local"]["evidence_ref"])
    _write(path, raw)
    with pytest.raises(evidence_store.EvidenceError, match="path|identity"):
        ReleaseState.load(path)


@pytest.mark.parametrize("operation", ["load", "save"])
def test_symlink_escape_is_rejected(saved, tmp_path, operation):
    state, path, _ = saved
    evidence = path.parent / "evidence"
    external = tmp_path / "outside-release"
    shutil.move(str(evidence), str(external))
    try:
        evidence.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        shutil.move(str(external), str(evidence))
        pytest.skip(f"Directory symlinks unavailable: {exc}")
    original = path.read_bytes()
    external_files = _files(external)
    with pytest.raises(evidence_store.EvidenceError, match="escapes"):
        ReleaseState.load(path) if operation == "load" else state.save(path)
    assert path.read_bytes() == original
    assert _files(external) == external_files


def test_corrupt_existing_immutable_file_is_not_overwritten(saved):
    state, path, raw = saved
    original = path.read_bytes()
    artifact = _artifact(path, raw["pipeline_runs"]["rcs"][0]["ecs"])
    artifact.write_bytes(b"corrupted immutable file")
    with pytest.raises(evidence_store.EvidenceError, match="changed|mismatch"):
        state.save(path)
    assert path.read_bytes() == original
    assert artifact.read_bytes() == b"corrupted immutable file"
    _assert_no_temporary(path.parent)


@pytest.mark.parametrize("failure", ["artifact_link", "artifact_fsync", "state_replace", "state_serialize"])
def test_failed_artifact_or_state_publish_preserves_original_state(saved, monkeypatch, failure):
    from orchestrator import state as state_module

    state, path, raw = saved
    original = path.read_bytes()
    original_evidence = _files(path.parent / "evidence")
    state.pipeline_runs["rcs"][0]["ecs"]["resolved_at"] = "recaptured"

    def fail(*args, **kwargs):
        raise OSError("injected persistence failure")

    if failure == "artifact_link":
        monkeypatch.setattr(evidence_store.os, "link", fail)
    elif failure == "artifact_fsync":
        monkeypatch.setattr(evidence_store.os, "fsync", fail)
    elif failure == "state_replace":
        monkeypatch.setattr(state_module.os, "replace", fail)
    else:
        monkeypatch.setattr(state_module.json, "dump", fail)
    with pytest.raises(OSError, match="injected persistence failure"):
        state.save(path)
    assert path.read_bytes() == original
    after = _files(path.parent / "evidence")
    assert all(after[key] == content for key, content in original_evidence.items())
    assert ReleaseState.load(path).pipeline_runs["rcs"][0]["ecs"].get("resolved_at") != "recaptured"
    _assert_no_temporary(path.parent)


def test_status_remains_bounded_without_evidence_io_or_detail(monkeypatch):
    sizes = []
    for count in (3, 300):
        state = _state(count)
        orch = Orchestrator(CONFIG, state, mocks={}, as_of=date(2026, 9, 11), now=NOW)
        before = deepcopy(state.pipeline_runs)

        def no_io(*args, **kwargs):
            pytest.fail("Status must not read or persist evidence")

        monkeypatch.setattr(evidence_store, "_read", no_io)
        monkeypatch.setattr(evidence_store, "_write", no_io)
        report = orch.status_report()
        projected = report["pipeline_runs"]
        assert projected == evidence_store.summarize(before, state.release_id)
        text = json.dumps(projected)
        assert "test_1000_Storage" not in text
        assert "monthly_storage_failure" not in text
        assert "suite_indices" not in text
        assert len(text) < 5_000
        assert state.pipeline_runs == before
        sizes.append(len(text))
    assert abs(sizes[1] - sizes[0]) < 200


def test_phase3_projections_and_effect_source_hash_are_identical_after_hydration(saved, monkeypatch):
    from steps.bug_bash import clone_plans_broker, ui_results, ui_test_status
    from tools import distribution
    monkeypatch.setattr(distribution, "validate_release_targets", lambda *a, **kw: None)

    state, path, _ = saved
    Orchestrator(CONFIG, state, mocks={}, now=NOW)
    state.set_step("bug_bash", "clone_plans_broker", StepState(
        status="done", data={"plan_id": 501, "ui_suite_id": 502}))
    state.set_step("bug_bash", "clone_plans_auth", StepState(
        status="done", data={"suite_id": 503}))

    def projections(value):
        rc = value.pipeline_runs["rcs"][0]
        broker = P.project_mrwp_ui_results(rc)
        auth = P.project_auth_ui_results(rc)
        assert broker[0], broker[2]
        assert auth[0], auth[2]
        ctx = context(value, now=NOW, inputs={"plan_id": 501})
        effect = ui_test_status.prepare_effect(ctx)
        assert isinstance(effect, dict), effect
        return (broker, auth, ui_results.current_binding(ctx), effect,
                clone_plans_broker.prepare_effect(ctx))

    before = projections(state)
    state.save(path)
    loaded = ReleaseState.load(path)
    assert projections(loaded) == before
    assert Orchestrator(CONFIG, loaded, mocks={}, now=NOW).status_report()["pipeline_runs"] == (
        _read(path)["pipeline_runs"])


@pytest.mark.parametrize("separator", ["windows", "portable"])
def test_evidence_bundle_can_move_without_original_directory(saved, tmp_path, separator):
    state, path, raw = saved
    if separator == "portable":
        for lane in LANES:
            ref = raw["pipeline_runs"]["rcs"][0][lane]["evidence_ref"]
            ref["path"] = ref["path"].replace("\\", "/")
        _write(path, raw)
    destination = tmp_path / "copied-release"
    shutil.copytree(path.parent, destination)
    shutil.rmtree(path.parent)
    loaded = ReleaseState.load(destination / path.name)
    assert asdict(loaded) == asdict(state)
    loaded.save(destination / path.name)
    assert all("\\" in lane["evidence_ref"]["path"]
               for lane in _read(destination / path.name)["pipeline_runs"]["rcs"][0].values()
               if isinstance(lane, dict))


def test_broken_evidence_is_discoverable_as_blocked_and_cli_never_grants_permission(saved, capsys):
    from orchestrator import cli, discovery

    _, path, raw = saved
    _artifact(path, raw["pipeline_runs"]["rcs"][0]["ecs"]).unlink()
    result = discovery.resolve(str(path.parent.parent), config_path=CONFIG)
    assert result["resolution"] == "one"
    assert result["release"]["status"] == "blocked"
    assert "evidence" in result["release"]["error"].lower()
    assert cli.main([
        "--config", CONFIG, "--runs-root", str(path.parent.parent),
        "status", "--release", "2026-08", "--json",
    ]) == 1
    response = json.loads(capsys.readouterr().out)
    assert response["permission_to_execute"] is False
    assert "evidence" in response["error"].lower()
