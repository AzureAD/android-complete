"""Immutable RC snapshots; state/status contain summaries, handlers see original evidence."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile


SCHEMA_VERSION = 1
LANES = {"ecs": "mrwp-ecs", "local": "mrwp-local", "auth": "auth-ecs"}
_COUNTS = ("total", "passed", "failed", "na", "count_basis", "build_id", "result_entries")


class EvidenceError(ValueError):
    """A referenced snapshot is unavailable or no longer matches its checkpoint."""


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")


def _select(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _summary(snapshot, lane):
    if lane == "auth":
        result = _select(snapshot, ("build", "verdict", "resolved_at", "error"))
        test = snapshot.get("test")
        if isinstance(test, dict):
            result["test"] = _select(test, ("run_id", "complete", "error"))
            suites = test.get("suites")
            if isinstance(suites, dict):
                result["test"]["suites"] = {
                    name: _select(suite, ("present", "total", "passed", "failed", "pct"))
                    for name, suite in suites.items() if isinstance(suite, dict)
                }
        elif "test" in snapshot:
            result["test"] = test
        return result
    result = _select(snapshot, (
        "run_id", "id_source", "complete", "ran", "total", "resolved_at",
        "error", "tests_error", "failed_suites_error",
    ))
    for key in ("failed_stages", "yellow_stages", "never_ran"):
        if isinstance(snapshot.get(key), list):
            result[key + "_count"] = len(snapshot[key])
    tests = snapshot.get("tests")
    if isinstance(tests, dict):
        result["tests"] = _select(tests, _COUNTS)
        if isinstance(tests.get("categories"), dict):
            result["tests"]["categories"] = {
                name: _select(counts, _COUNTS)
                for name, counts in tests["categories"].items() if isinstance(counts, dict)
            }
    elif "tests" in snapshot:
        result["tests"] = tests
    return result


def _pack(snapshot, lane):
    """Keep repeated failing-suite structures only once, without changing logical inputs."""
    packed = deepcopy(snapshot)
    aliases = []
    if lane != "auth" and isinstance(packed.get("tests"), dict):
        tests = packed["tests"]
        failed = tests.get("failed_suites")
        suites = tests.get("suites")
        if isinstance(failed, list) and isinstance(suites, list):
            suite_bytes = [_json_bytes(suite) for suite in suites]
            failed_bytes = [_json_bytes(suite) for suite in failed]
            indices = [suite_bytes.index(suite) for suite in failed_bytes if suite in suite_bytes]
            if len(indices) == len(failed):
                tests["failed_suites"] = {"suite_indices": indices}
                aliases.append("tests.failed_suites")
                if ("failed_suites" in packed
                        and _json_bytes(packed["failed_suites"]) == _json_bytes(failed)):
                    del packed["failed_suites"]
                    aliases.append("failed_suites")
    return packed, aliases


def _unpack(document):
    snapshot = deepcopy(document["snapshot"])
    aliases = document["aliases"]
    if aliases not in ([], ["tests.failed_suites"], ["tests.failed_suites", "failed_suites"]):
        raise EvidenceError("RC evidence has invalid snapshot aliases")
    if aliases:
        tests = snapshot.get("tests")
        if not isinstance(tests, dict):
            raise EvidenceError("RC evidence has invalid test summary")
        reference, suites = tests.get("failed_suites"), tests.get("suites")
        if (not isinstance(reference, dict) or set(reference) != {"suite_indices"}
                or not isinstance(suites, list)
                or not isinstance(reference["suite_indices"], list)
                or any(type(i) is not int or not 0 <= i < len(suites)
                       for i in reference["suite_indices"])):
            raise EvidenceError("RC evidence has invalid failing-suite references")
        tests["failed_suites"] = [deepcopy(suites[i]) for i in reference["suite_indices"]]
        if "failed_suites" in aliases:
            if "failed_suites" in snapshot:
                raise EvidenceError("RC evidence has conflicting failing-suite aliases")
            snapshot["failed_suites"] = deepcopy(tests["failed_suites"])
    return snapshot


def _rc_folder(rc):
    if (not isinstance(rc, bool) and isinstance(rc, (str, int))
            and re.fullmatch(r"[0-9]+", str(rc)) and int(rc) > 0):
        return f"rc{int(rc)}"
    # Preserve incomplete legacy observations without inventing an authoritative RC.
    return "unresolved"


def _parts(rc, lane, digest):
    return ("evidence", _rc_folder(rc), f"{LANES[lane]}-{digest}.json")


def _describe(snapshot, release_id, rc, lane):
    packed, aliases = _pack(snapshot, lane)
    document = {
        "schema_version": SCHEMA_VERSION, "release_id": release_id,
        "rc": rc, "lane": lane, "snapshot": packed, "aliases": aliases,
    }
    content = _json_bytes(document)
    digest = sha256(content).hexdigest()
    summary = _summary(snapshot, lane)
    summary["evidence_ref"] = {
        "schema_version": SCHEMA_VERSION,
        "path": "\\".join(_parts(rc, lane, digest)),
        "sha256": digest,
    }
    return summary, content


def _contained_path(directory, parts):
    root = Path(directory).resolve()
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root):
        raise EvidenceError(f"RC evidence path escapes release directory: {target}")
    return target


def _read(path):
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EvidenceError(
            f"RC evidence unavailable: {path}. Restore the original evidence file; "
            "ADO will not be refetched automatically."
        ) from exc


def _write(directory, parts, content):
    target = _contained_path(directory, parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _read(target) != content:
            raise EvidenceError(f"RC evidence changed on disk: {target}; restore the original file")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".evidence-", delete=False) as fh:
            temporary = Path(fh.name)
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            # Publish without replacing an existing immutable checkpoint, even across writers.
            os.link(temporary, target)
        except FileExistsError:
            if _read(target) != content:
                raise EvidenceError(f"RC evidence changed on disk: {target}; restore the original file")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _hydrate(snapshot, release_id, rc, lane, directory):
    reference = snapshot["evidence_ref"]
    if (not isinstance(reference, dict)
            or set(reference) != {"schema_version", "path", "sha256"}
            or type(reference.get("schema_version")) is not int
            or reference["schema_version"] != SCHEMA_VERSION
            or not isinstance(reference.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])
            or not isinstance(reference.get("path"), str)):
        raise EvidenceError(f"Invalid RC evidence reference for RC{rc} {lane}")
    parts = _parts(rc, lane, reference["sha256"])
    if reference["path"].replace("\\", "/") != "/".join(parts):
        raise EvidenceError(f"Invalid RC evidence path for RC{rc} {lane}")
    path = _contained_path(directory, parts)
    content = _read(path)
    if sha256(content).hexdigest() != reference["sha256"]:
        raise EvidenceError(f"RC evidence SHA-256 mismatch: {path}; restore the original file")
    try:
        document = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError(f"RC evidence is not valid JSON: {path}") from exc
    if (not isinstance(document, dict)
            or set(document) != {"schema_version", "release_id", "rc", "lane", "snapshot", "aliases"}
            or type(document["schema_version"]) is not int
            or document["schema_version"] != SCHEMA_VERSION
            or document["release_id"] != release_id
            or type(document["rc"]) is not type(rc) or document["rc"] != rc
            or document["lane"] != lane or not isinstance(document["snapshot"], dict)
            or "evidence_ref" in document["snapshot"]):
        raise EvidenceError(f"RC evidence schema/identity mismatch: {path}")
    restored = _unpack(document)
    if _json_bytes(_summary(restored, lane)) != _json_bytes(
            {k: v for k, v in snapshot.items() if k != "evidence_ref"}):
        raise EvidenceError(f"RC evidence summary mismatch: {path}")
    return restored


def _transform(runs, transform):
    result = deepcopy(runs)
    rcs = result.get("rcs")
    if rcs is None:
        return result
    if not isinstance(rcs, list) or any(not isinstance(rc, dict) for rc in rcs):
        raise EvidenceError("RC evidence requires an array of RC records")
    for rc in rcs:
        for lane in LANES:
            snapshot = rc.get(lane)
            if snapshot is not None:
                if not isinstance(snapshot, dict):
                    raise EvidenceError(f"Invalid RC{rc.get('rc')} {lane} snapshot")
                rc[lane] = transform(snapshot, rc.get("rc"), lane)
    return result


def externalize(runs, release_id, directory):
    """Write all referenced files before the caller atomically replaces release-state."""
    def store(snapshot, rc, lane):
        if "evidence_ref" in snapshot:
            snapshot = _hydrate(snapshot, release_id, rc, lane, directory)
        summary, content = _describe(snapshot, release_id, rc, lane)
        _write(directory, _parts(rc, lane, summary["evidence_ref"]["sha256"]), content)
        return summary
    return _transform(runs, store)


def hydrate(runs, release_id, directory):
    """Read/verify referenced snapshots; legacy inline snapshots remain valid until saved."""
    return _transform(runs, lambda snapshot, rc, lane:
                      _hydrate(snapshot, release_id, rc, lane, directory)
                      if "evidence_ref" in snapshot else snapshot)


def summarize(runs, release_id):
    """Compact status projection without persisting data or exposing per-test evidence."""
    return _transform(runs, lambda snapshot, rc, lane:
                      snapshot if "evidence_ref" in snapshot else
                      _describe(snapshot, release_id, rc, lane)[0])
