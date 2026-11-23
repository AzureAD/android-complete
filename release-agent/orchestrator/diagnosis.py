# Copyright (c) Microsoft Corporation. All rights reserved.
"""Deterministic pipeline & RC test failure evidence collector and AI diagnosis store.

Python remains 100% authoritative over `Done` / `InProgress` / `Blocked` verdicts and
gate rules. Whenever a release step blocks (pipeline/stage failure, 0-row Kusto check,
localization PR conflict, etc.) or Phase 2 RC suites contain test failures (`failed > 0`),
this module:

  1. Computes a stable `failure_signature` bound to the current build IDs / failure state
     so AI diagnosis runs AT MOST ONCE per distinct failure episode.
  2. Extracts bounded, zero-hallucination evidence from ADO timelines, failing task logs,
     externalized RC test results (`errorMessage` + `stackTrace` clusters + ECS/Local
     overlap), and local `1ES-Pipelines/` YAML coordinates.
  3. Builds a factual `deterministic_brief` fallback immediately so CTA emails, Scout
     alerts, RC reports, and `/release-agent status` ALWAYS surface concrete root-cause
     evidence even before (or without) an AI pass.
  4. Stores/reads enriched AI root-cause briefs (`ai_diagnosis`) written by Scout via
     `record-diagnosis` before `release-drainer` finalizes CTA delivery.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
import hashlib
import json
import os
import re
from typing import Any, Optional

from orchestrator import cli_common as C
from orchestrator import evidence_store, schedule
from tools import pipelines as P
from tools.coordinates import coords


DIAGNOSIS_SCHEMA_VERSION = 2

# Local workspace paths (`AuthClientAndroidPipelines/` + `.github/skills/`) mapped by logical pipeline key.
PIPELINE_WORKSPACE_MAP: dict[str, dict[str, Any]] = {
    "checker": {
        "org_key": "engineering",
        "def_key": "checker",
        "yaml": "AuthClientAndroidPipelines/production/release/code-complete-calendar-checker.yml",
        "scripts": ["AuthClientAndroidPipelines/scripts/"],
        "skills": ["release-helper"],
    },
    "orchestrator": {
        "org_key": "engineering",
        "def_key": "orchestrator",
        "yaml": "AuthClientAndroidPipelines/production/monthly-release/release-orchestrator.yml",
        "scripts": [
            "AuthClientAndroidPipelines/templates/release/validate-branches-and-versions.yml",
            "AuthClientAndroidPipelines/templates/release/create-branches.yml",
            "AuthClientAndroidPipelines/templates/release/trigger-rc-pipelines.yml",
            "AuthClientAndroidPipelines/scripts/get_next_version.py",
        ],
        "skills": ["release-helper", "codebase-researcher"],
    },
    "mrwp": {
        "org_key": "engineering",
        "def_key": "mrwp",
        "yaml": "AuthClientAndroidPipelines/production/monthly-release/monthly-release.yml",
        "scripts": [
            "AuthClientAndroidPipelines/templates/build/libraries/build-and-publish-auth-android-libs.yml",
            "AuthClientAndroidPipelines/templates/build/utilities/build-and-publish-utilities.yml",
            "AuthClientAndroidPipelines/templates/runTests/run-monthly-ui-automation.yml",
            "AuthClientAndroidPipelines/scripts/queue-build.ps1",
        ],
        "skills": ["release-helper", "codebase-researcher", "kusto-analyst"],
    },
    "auth_build": {
        "org_key": "one",
        "def_key": "auth_build",
        "yaml": "authenticator/.pipelines/AndroidBuildBroker1ES.yml",
        "scripts": ["AuthClientAndroidPipelines/templates/build/brokerApps/build-authenticator.yml"],
        "skills": ["release-helper", "codebase-researcher"],
    },
    "auth_test": {
        "org_key": "one",
        "def_key": "auth_test",
        "yaml": "authenticator/.pipelines/PostBuildUITests.yml",
        "scripts": [],
        "skills": ["release-helper", "codebase-researcher", "kusto-analyst"],
    },
    "localization": {
        "org_key": "one",
        "def_key": "localization",
        "yaml": "authenticator/.pipelines/OneLocBuild.yml",
        "scripts": [],
        "skills": ["release-helper"],
    },
}

# Stage displayName / identifier substring -> exact `AuthClientAndroidPipelines` template YAML.
STAGE_TEMPLATE_HINTS: tuple[tuple[str, str], ...] = (
    ("utility", "AuthClientAndroidPipelines/templates/build/utilities/build-and-publish-utilities.yml"),
    ("publishcommonutilities", "AuthClientAndroidPipelines/templates/build/utilities/build-and-publish-utilities.yml"),
    ("flight", "AuthClientAndroidPipelines/templates/verification/flight-verification-for-official-release.yml"),
    ("common4j", "AuthClientAndroidPipelines/templates/build/libraries/build-and-publish-common4j.yml"),
    ("broker4j", "AuthClientAndroidPipelines/templates/build/libraries/build-and-publish-broker4j.yml"),
    ("aadauthenticator", "AuthClientAndroidPipelines/templates/build/libraries/build-and-publish-broker.yml"),
    ("msal", "AuthClientAndroidPipelines/templates/build/libraries/build-and-publish-msal.yml"),
    ("common", "AuthClientAndroidPipelines/templates/build/libraries/build-and-publish-common.yml"),
    ("promote", "AuthClientAndroidPipelines/scripts/promote-packages.ps1"),
    ("instrumented", "AuthClientAndroidPipelines/templates/runTests/run-instrumented-tests-for-all-libraries.yml"),
    ("native auth", "AuthClientAndroidPipelines/templates/runTests/run-native-auth-e2e-tests.yml"),
    ("authenticator broker apk", "AuthClientAndroidPipelines/templates/build/brokerApps/build-authenticator.yml"),
    ("company portal", "AuthClientAndroidPipelines/templates/build/brokerApps/build-companyportal.yml"),
    ("link to windows", "AuthClientAndroidPipelines/templates/build/brokerApps/build-ltw.yml"),
    ("generate test apps", "AuthClientAndroidPipelines/templates/build/testApps/build-test-apps.yml"),
    ("oneauthtestapp", "AuthClientAndroidPipelines/templates/build/testApps/build-one-auth-test-app.yml"),
    ("pre-ui", "AuthClientAndroidPipelines/templates/runTests/run-pre-ui-validation.yml"),
    ("ui automation", "AuthClientAndroidPipelines/templates/runTests/run-monthly-ui-automation.yml"),
    ("validate branch", "AuthClientAndroidPipelines/templates/release/validate-branches-and-versions.yml"),
    ("create release branch", "AuthClientAndroidPipelines/templates/release/create-branches.yml"),
    ("trigger rc", "AuthClientAndroidPipelines/templates/release/trigger-rc-pipelines.yml"),
)

_BUILD_URL_RE = re.compile(
    r"(https?://[^\s\"'<>]+?)/([^/\s\"'<>]+)/_build(?:/results)?\?[^\s\"'<>]*?\bbuildId=(\d+)",
    re.IGNORECASE,
)
_NOTE_RUN_ID_RE = re.compile(r"\b(?:run|build)\s+#?(\d{5,})\b", re.IGNORECASE)

_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]{4,}\b|@[0-9a-fA-F]{5,}\b")
_NUM_TOKEN_RE = re.compile(r"\b\d{4,}\b")
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")
_ERROR_LINE_RE = re.compile(
    r"(##\[error\]|exception|error\b|failed\b|fatal\b|conflict|denied|timeout|timed out|aborted)",
    re.IGNORECASE,
)


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()[:24]}"


def normalize_error_signature(error_message: str, stack_trace: str = "") -> str:
    """Normalize dynamic tokens out of an error/exception line so identical root causes group together."""
    raw = (error_message or "").strip()
    if not raw and stack_trace:
        raw = next((ln.strip() for ln in stack_trace.splitlines() if ln.strip()), "")
    if not raw:
        return "Unspecified test failure (no errorMessage recorded)"
    first_line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), raw)
    cleaned = _ISO_TS_RE.sub("<ts>", first_line)
    cleaned = _UUID_RE.sub("<uuid>", cleaned)
    cleaned = _HEX_RE.sub("<hex>", cleaned)
    cleaned = _NUM_TOKEN_RE.sub("<id>", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:180]


def classify_cluster_hint(signature: str, sample_error: str = "", sample_stack: str = "") -> str:
    """Deterministic bucket hint for a test failure signature."""
    blob = f"{signature}\n{sample_error}\n{sample_stack}".lower()
    if any(tok in blob for tok in ("labapi", "labuser", "keyvault", "secret", "password", "throttl", "429", "lab ")):
        return "lab_or_test_account_infra"
    if any(tok in blob for tok in ("firebase", "ftl", "instrumentation run failed", "process crashed", "device offline", "adb")):
        return "device_or_ftl_infra"
    if any(tok in blob for tok in ("uiautomator", "uiobjectnotfound", "nosuchview", "waittimeout", "timed out waiting", "compose")):
        return "ui_selector_or_timeout"
    if any(tok in blob for tok in ("broker", "ipc", "ests", "invalid_grant", "aadsts", "serviceexception", "clientexception")):
        return "auth_or_broker_flow"
    return "assertion_or_product_regression"


def _latest_rc_entry(pipeline_runs: dict[str, Any]) -> dict[str, Any]:
    rcs = (pipeline_runs or {}).get("rcs") or []
    return rcs[-1] if rcs and isinstance(rcs[-1], dict) else {}


def _numeric_build_id(value: Any) -> Optional[int]:
    if type(value) is int:
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        number = int(value)
        return number if number > 0 else None
    return None


def _default_pipeline_key(phase_id: str, step_id: str) -> str:
    if phase_id == "build_verify":
        if step_id == "checker_fired":
            return "checker"
        if step_id == "orchestrator_health":
            return "orchestrator"
        if step_id in ("mrwp_ecs", "mrwp_local"):
            return "mrwp"
        if step_id == "auth_ecs":
            return "auth_build"
    if phase_id == "ccd" and step_id in ("localization", "localization_pr"):
        return "localization"
    return "auth_build" if phase_id in ("ccd", "sign_off", "play_store") else "orchestrator"


def _resolve_build_targets_for_step(
    state,
    phase_id: str,
    step_id: str,
    step_data: dict[str, Any],
    step_links: Optional[list[dict[str, str]]] = None,
    step_note: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Identify `(label, pipeline_key, org_url, project, build_id)` tuples from data, `pipeline_runs`, `links`, and `note`."""
    pr = state.pipeline_runs or {}
    rc = _latest_rc_entry(pr)
    targets: list[dict[str, Any]] = []
    seen_ids: set[tuple[str, int]] = set()

    def _add(label: str, pkey: str, bid: Any, *, org_override: Optional[str] = None, project_override: Optional[str] = None) -> None:
        num = _numeric_build_id(bid)
        if num is None:
            return
        meta = PIPELINE_WORKSPACE_MAP.get(pkey, {})
        org_key = meta.get("org_key", "engineering")
        org_url = (org_override or coords.org_url(org_key)).rstrip("/")
        project = project_override or coords.project(org_key)
        dedup_key = (project.lower(), num)
        if dedup_key in seen_ids:
            return
        seen_ids.add(dedup_key)
        targets.append({
            "label": label,
            "pipeline_key": pkey,
            "org": org_url,
            "project": project,
            "build_id": num,
            "build_url": f"{org_url}/{project}/_build/results?buildId={num}",
            "yaml_path": meta.get("yaml"),
            "scripts": list(meta.get("scripts") or []),
            "skills": list(meta.get("skills") or ["release-helper"]),
        })

    if phase_id == "build_verify" and step_id == "checker_fired":
        _add("Code Complete Checker", "checker", step_data.get("run_id") or (pr.get("checker") or {}).get("run_id"))
    elif phase_id == "build_verify" and step_id == "orchestrator_health":
        _add("Release Orchestrator", "orchestrator", step_data.get("run_id") or (pr.get("orchestrator") or {}).get("run_id"))
    elif phase_id == "build_verify" and step_id == "mrwp_ecs":
        _add("MRWP ECS", "mrwp", step_data.get("run_id") or ((rc.get("ecs") or {}).get("run_id")))
    elif phase_id == "build_verify" and step_id == "mrwp_local":
        _add("MRWP Local", "mrwp", step_data.get("run_id") or ((rc.get("local") or {}).get("run_id")))
    elif phase_id == "build_verify" and step_id == "auth_ecs":
        auth = rc.get("auth") or {}
        _add("Authenticator ECS Build", "auth_build", step_data.get("build_id") or ((auth.get("build") or {}).get("run_id")))
        _add("Authenticator ECS UI Tests", "auth_test", step_data.get("test_build_id") or ((auth.get("test") or {}).get("run_id")))
    elif phase_id == "ccd" and step_id in ("localization", "localization_pr"):
        _add("OneLocBuild Localization", "localization", step_data.get("build_id") or step_data.get("run_id"))
    elif phase_id == "finalize" and step_id == "orchestrator_finalization":
        final_runs = pr.get("final") or {}
        _add("Release Orchestrator", "orchestrator", step_data.get("run_id") or final_runs.get("orchestrator_run_id") or (pr.get("orchestrator") or {}).get("run_id"))
        _add("Final1 MRWP", "mrwp", step_data.get("mrwp_run_id") or final_runs.get("mrwp_run_id"))
    else:
        for key_name in ("build_id", "run_id", "test_build_id"):
            if step_data.get(key_name):
                _add(f"{phase_id}.{step_id} ({key_name})", _default_pipeline_key(phase_id, step_id), step_data.get(key_name))

    # Extract build IDs from `rec.links` (where Blocked steps like `mrwp_ecs` / `mrwp_local` record their run URLs!).
    default_pkey = _default_pipeline_key(phase_id, step_id)
    for lnk in step_links or []:
        if not isinstance(lnk, dict):
            continue
        url = str(lnk.get("url") or "")
        lnk_name = str(lnk.get("name") or f"{phase_id}.{step_id} run")
        if "view=logs" in url.lower() or "child build" in lnk_name.lower():
            continue
        m = _BUILD_URL_RE.search(url)
        if not m:
            continue
        host_part, proj_part, bid_str = m.group(1), m.group(2), m.group(3)
        pkey = "auth_test" if "ui test" in lnk_name.lower() else default_pkey
        _add(lnk_name, pkey, bid_str, org_override=host_part, project_override=proj_part)

    # Final fallback: parse `run <id>` / `build #<id>` from `step_note` if still empty.
    if not targets and step_note:
        m_note = _NOTE_RUN_ID_RE.search(step_note)
        if m_note:
            _add(f"{phase_id}.{step_id}", default_pkey, m_note.group(1))

    return targets


def _extract_log_tail(log_text: str, max_lines: int = 60) -> list[str]:
    """Return compact high-signal error lines + tail from an ADO task log."""
    if not log_text:
        return []
    raw_lines = [ln.rstrip() for ln in log_text.splitlines() if ln.strip()]
    if not raw_lines:
        return []
    # Strip leading ADO timestamps (`2026-11-20T08:12:34.1234567Z `) to keep snippets concise.
    cleaned = [re.sub(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s?", "", ln) for ln in raw_lines]
    err_indices = [idx for idx, ln in enumerate(cleaned) if _ERROR_LINE_RE.search(ln)]
    if not err_indices:
        return cleaned[-min(25, max_lines):]
    picked: list[str] = []
    seen_idx: set[int] = set()
    for center in err_indices[-8:]:
        for idx in range(max(0, center - 1), min(len(cleaned), center + 3)):
            if idx not in seen_idx:
                seen_idx.add(idx)
                picked.append(cleaned[idx][:280])
                if len(picked) >= max_lines:
                    return picked
    for idx in range(max(0, len(cleaned) - 12), len(cleaned)):
        if idx not in seen_idx and len(picked) < max_lines:
            seen_idx.add(idx)
            picked.append(cleaned[idx][:280])
    return picked


def _stage_template_paths_and_snippets(stage_names: list[str], repo_root: str) -> tuple[list[str], dict[str, str]]:
    """Map failed stage names to exact `AuthClientAndroidPipelines` YAML templates and read compact snippets."""
    matched_paths: list[str] = []
    snippets: dict[str, str] = {}
    for sname in stage_names:
        low = (sname or "").lower()
        for token, rel_path in STAGE_TEMPLATE_HINTS:
            if token in low and rel_path not in matched_paths:
                matched_paths.append(rel_path)
                abs_p = os.path.join(repo_root, rel_path.replace("/", os.sep))
                if os.path.isfile(abs_p) and len(snippets) < 3:
                    try:
                        with open(abs_p, "r", encoding="utf-8", errors="replace") as fh:
                            lines = [ln.rstrip() for ln in fh.readlines()[:75]]
                        snippets[rel_path] = "\n".join(lines)
                    except OSError:
                        pass
                break
    return matched_paths, snippets


def collect_pipeline_timeline_evidence(
    target: dict[str, Any],
    *,
    fetch_live_logs: bool = True,
    max_failed_tasks: int = 4,
    timeout: int = 45,
    depth: int = 0,
    max_depth: int = 1,
) -> dict[str, Any]:
    """Fetch ADO `/timeline` + failing task log snippets + recursive child pipeline failures (`queue-build.ps1`)."""
    out: dict[str, Any] = {
        "label": target["label"],
        "pipeline_key": target["pipeline_key"],
        "org": target["org"],
        "project": target["project"],
        "build_id": target["build_id"],
        "build_url": target["build_url"],
        "yaml_path": target.get("yaml_path"),
        "scripts": target.get("scripts") or [],
        "timeline_fetched": False,
        "succeeded_stages": [],
        "root_failed_stages": [],
        "failed_stages": [],
        "skipped_downstream_stages": [],
        "failed_tasks": [],
        "child_builds": [],
        "timeline_error": None,
    }
    if not fetch_live_logs:
        return out
    try:
        ok, records, detail = P.get_timeline(target["org"], target["project"], target["build_id"], timeout=timeout)
    except Exception as exc:  # pragma: no cover - defensive guard
        out["timeline_error"] = str(exc)
        return out
    if not ok or not isinstance(records, list):
        out["timeline_error"] = detail or "timeline unavailable"
        return out

    out["timeline_fetched"] = True
    by_id = {r.get("id"): r for r in records if isinstance(r, dict) and r.get("id")}

    def _ancestor_name(rec: dict[str, Any], rec_type: str) -> Optional[str]:
        cur = rec
        visited: set[str] = set()
        while isinstance(cur, dict):
            pid = cur.get("parentId")
            if not pid or pid in visited:
                break
            visited.add(pid)
            parent = by_id.get(pid)
            if not isinstance(parent, dict):
                break
            if parent.get("type") == rec_type:
                return parent.get("name")
            cur = parent
        return None

    def _ancestor_id(rec: dict[str, Any], rec_type: str) -> Optional[str]:
        cur = rec
        visited: set[str] = set()
        while isinstance(cur, dict):
            pid = cur.get("parentId")
            if not pid or pid in visited:
                break
            visited.add(pid)
            parent = by_id.get(pid)
            if not isinstance(parent, dict):
                break
            if parent.get("type") == rec_type:
                return parent.get("id")
            cur = parent
        return None

    stage_records = sorted(
        (r for r in records if r.get("type") == "Stage"),
        key=lambda r: r.get("order") or 0,
    )
    for rec in stage_records:
        st_name = rec.get("name") or rec.get("identifier") or "stage"
        res = rec.get("result")
        state_val = rec.get("state")
        issues = [
            iss.get("message", "").strip()
            for iss in (rec.get("issues") or [])
            if isinstance(iss, dict) and iss.get("message")
        ]
        if res in ("succeeded", "succeededWithIssues"):
            out["succeeded_stages"].append(st_name)
        elif res in ("failed", "abandoned"):
            entry = {"name": st_name, "identifier": rec.get("identifier"), "state": state_val, "result": res, "issues": issues[:5]}
            out["root_failed_stages"].append(entry)
            out["failed_stages"].append(entry)
        elif state_val == "pending" or res == "skipped":
            out["skipped_downstream_stages"].append(st_name)
        elif res == "canceled":
            entry = {"name": st_name, "identifier": rec.get("identifier"), "state": state_val, "result": res, "issues": issues[:5]}
            out["failed_stages"].append(entry)

    # Prefer non-housekeeping `failed` tasks first, but KEEP `Checkout` / `Initialize job` if `failed`
    # so service-connection/submodule/1ES pool failures are never silently dropped!
    housekeeping = {"Initialize job", "Finalize Job", "Post-job: Checkout"}
    task_candidates = [
        rec for rec in records
        if rec.get("type") == "Task"
        and (
            rec.get("result") in ("failed", "abandoned")
            or (rec.get("result") == "canceled" and rec.get("name") not in housekeeping)
        )
    ]
    task_candidates.sort(key=lambda r: (
        0 if r.get("result") == "failed" else 1,
        1 if r.get("name") in housekeeping else 0,
        r.get("order") or 0,
    ))

    base = target["org"].rstrip("/")
    proj = target["project"]
    bid = target["build_id"]
    discovered_children: list[dict[str, Any]] = []
    seen_child_ids: set[tuple[str, int]] = {(proj.lower(), int(bid))}

    for trec in task_candidates[:max_failed_tasks]:
        tid = trec.get("id")
        jid = _ancestor_id(trec, "Job")
        stage_name = _ancestor_name(trec, "Stage")
        job_name = _ancestor_name(trec, "Job")
        log_id = (trec.get("log") or {}).get("id") if isinstance(trec.get("log"), dict) else None
        issues = [
            iss.get("message", "").strip()
            for iss in (trec.get("issues") or [])
            if isinstance(iss, dict) and iss.get("message")
        ]
        deep_url = f"{base}/{proj}/_build/results?buildId={bid}&view=logs"
        if jid and tid:
            deep_url += f"&j={jid}&t={tid}"
        elif jid:
            deep_url += f"&j={jid}"

        log_excerpt: list[str] = []
        raw_log = ""
        if log_id is not None:
            log_url = f"{base}/{proj}/_apis/build/builds/{bid}/logs/{log_id}?api-version=7.1"
            try:
                ok_log, txt, _ = P._ado_rest_get_text(log_url, timeout=timeout)
                if ok_log and txt:
                    raw_log = txt
                    log_excerpt = _extract_log_tail(txt, max_lines=50)
            except Exception:
                log_excerpt = []

        # Follow child ADO builds queued by `queue-build.ps1` / `trigger-external-pipeline.ps1`!
        if depth < max_depth and raw_log:
            for cm in _BUILD_URL_RE.finditer(raw_log):
                c_host, c_proj, c_bid_s = cm.group(1).rstrip("/"), cm.group(2), cm.group(3)
                c_bid = _numeric_build_id(c_bid_s)
                if c_bid is None or (c_proj.lower(), c_bid) in seen_child_ids:
                    continue
                seen_child_ids.add((c_proj.lower(), c_bid))
                discovered_children.append({
                    "label": f"Child pipeline of '{trec.get('name')}'",
                    "pipeline_key": target["pipeline_key"],
                    "org": c_host,
                    "project": c_proj,
                    "build_id": c_bid,
                    "build_url": f"{c_host}/{c_proj}/_build/results?buildId={c_bid}",
                    "yaml_path": None,
                    "scripts": [],
                })

        out["failed_tasks"].append({
            "task_name": trec.get("name"),
            "stage_name": stage_name,
            "job_name": job_name,
            "result": trec.get("result"),
            "log_id": log_id,
            "task_log_url": deep_url,
            "issues": issues[:6],
            "log_excerpt": log_excerpt,
        })

    for child_target in discovered_children[:2]:
        out["child_builds"].append(
            collect_pipeline_timeline_evidence(
                child_target,
                fetch_live_logs=fetch_live_logs,
                max_failed_tasks=3,
                timeout=timeout,
                depth=depth + 1,
                max_depth=max_depth,
            )
        )
    return out


def collect_rc_test_clusters(
    runs_root: str,
    release_id: str,
    state,
) -> dict[str, Any]:
    """Cluster failed tests + cross-provider ECS/Local overlap from stored RC evidence."""
    try:
        pr = evidence_store.hydrate(
            state.pipeline_runs, release_id, os.path.join(runs_root, release_id)
        )
    except Exception:
        pr = state.pipeline_runs or {}
    rc_entry = _latest_rc_entry(pr)
    rc_num = rc_entry.get("rc")

    raw_failures: list[dict[str, Any]] = []
    recovered_flaky: list[dict[str, str]] = []
    leg_totals: dict[str, dict[str, Any]] = {}
    ecs_failed_titles: set[str] = set()
    local_failed_titles: set[str] = set()

    for slot, leg_label in (("ecs", "MRWP ECS"), ("local", "MRWP Local")):
        snap = rc_entry.get(slot) or {}
        tests_obj = snap.get("tests") if isinstance(snap, dict) else None
        if not isinstance(tests_obj, dict):
            continue
        bid = snap.get("run_id")
        leg_totals[leg_label] = {
            "build_id": bid,
            "passed": tests_obj.get("passed", 0),
            "failed": tests_obj.get("failed", 0),
            "total": tests_obj.get("total", 0),
        }
        for suite in tests_obj.get("suites") or []:
            if not isinstance(suite, dict):
                continue
            sname = suite.get("name") or "suite"
            cat = suite.get("category") or P.classify_test_run(sname)
            for tr in suite.get("test_results") or []:
                if not isinstance(tr, dict):
                    continue
                title = str(tr.get("title") or "").strip()
                if tr.get("recovered"):
                    recovered_flaky.append({"leg": leg_label, "suite": sname, "title": title})
                if tr.get("verdict") != "Failed":
                    continue
                if slot == "ecs":
                    ecs_failed_titles.add(title)
                else:
                    local_failed_titles.add(title)
                attempts = tr.get("attempts") or []
                last_err = next(
                    (a.get("errorMessage") for a in reversed(attempts) if isinstance(a, dict) and a.get("errorMessage")),
                    "",
                )
                last_stack = next(
                    (a.get("stackTrace") for a in reversed(attempts) if isinstance(a, dict) and a.get("stackTrace")),
                    "",
                )
                raw_failures.append({
                    "leg": leg_label,
                    "suite": sname,
                    "category": cat,
                    "title": title,
                    "error_message": str(last_err or "")[:500],
                    "stack_trace": "\n".join(str(last_stack or "").splitlines()[:6])[:600],
                })

    auth = rc_entry.get("auth") or {}
    auth_ev = ((auth.get("test") or {}).get("evidence") or {}) if isinstance(auth, dict) else {}
    auth_summary = auth_ev.get("summary") if isinstance(auth_ev, dict) else None
    if isinstance(auth_summary, dict):
        leg_totals["Authenticator ECS"] = {
            "build_id": (auth.get("test") or {}).get("run_id"),
            "apk_build_id": (auth.get("build") or {}).get("run_id"),
            "passed": auth_summary.get("passed", 0),
            "failed": auth_summary.get("failed", 0),
            "total": auth_summary.get("total", 0),
        }
        for suite in auth_summary.get("suites") or []:
            if not isinstance(suite, dict):
                continue
            sname = suite.get("name") or "Authenticator UI"
            for tr in suite.get("test_results") or []:
                if not isinstance(tr, dict):
                    continue
                title = str(tr.get("title") or "").strip()
                if tr.get("recovered"):
                    recovered_flaky.append({"leg": "Authenticator ECS", "suite": sname, "title": title})
                if tr.get("verdict") != "Failed":
                    continue
                attempts = tr.get("attempts") or []
                last_err = next(
                    (a.get("errorMessage") for a in reversed(attempts) if isinstance(a, dict) and a.get("errorMessage")),
                    "",
                )
                last_stack = next(
                    (a.get("stackTrace") for a in reversed(attempts) if isinstance(a, dict) and a.get("stackTrace")),
                    "",
                )
                raw_failures.append({
                    "leg": "Authenticator ECS",
                    "suite": sname,
                    "category": "ui",
                    "title": title,
                    "error_message": str(last_err or "")[:500],
                    "stack_trace": "\n".join(str(last_stack or "").splitlines()[:6])[:600],
                })

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in raw_failures:
        sig = normalize_error_signature(item["error_message"], item["stack_trace"])
        grouped.setdefault(sig, []).append(item)

    clusters: list[dict[str, Any]] = []
    for sig, rows in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        first = rows[0]
        legs = sorted({r["leg"] for r in rows})
        cats = sorted({r["category"] for r in rows})
        suites = sorted({r["suite"] for r in rows})
        clusters.append({
            "signature": sig,
            "count": len(rows),
            "hint": classify_cluster_hint(sig, first["error_message"], first["stack_trace"]),
            "legs": legs,
            "categories": cats,
            "suites": suites[:5],
            "sample_tests": [r["title"] for r in rows[:5]],
            "sample_error": first["error_message"],
            "sample_stack": first["stack_trace"],
        })

    both = sorted(ecs_failed_titles & local_failed_titles)
    ecs_only = sorted(ecs_failed_titles - local_failed_titles)
    local_only = sorted(local_failed_titles - ecs_failed_titles)

    return {
        "rc": rc_num,
        "total_failed_tests": len(raw_failures),
        "leg_totals": leg_totals,
        "clusters": clusters[:10],
        "cluster_count": len(clusters),
        "provider_overlap": {
            "both_ecs_and_local_count": len(both),
            "both_ecs_and_local_samples": both[:6],
            "ecs_only_count": len(ecs_only),
            "ecs_only_samples": ecs_only[:6],
            "local_only_count": len(local_only),
            "local_only_samples": local_only[:6],
        },
        "flaky_recovered_count": len(recovered_flaky),
        "flaky_recovered_samples": recovered_flaky[:6],
    }


def compute_step_failure_signature(
    orch,
    runs_root: str,
    phase_id: str,
    step_id: str,
) -> Optional[str]:
    """Return a canonical `sha256:...` signature if `(phase_id, step_id)` currently warrants diagnosis."""
    rec = orch.state.get_step(phase_id, step_id)
    if phase_id == "build_verify" and step_id in ("rc_report_notify", "rc_report_gate"):
        # RC test-failure synthesis applies once the 3 test legs are done and at least 1 failure exists.
        for req_step in ("mrwp_ecs", "mrwp_local", "auth_ecs"):
            if orch.state.get_step("build_verify", req_step).status not in ("done", "skipped"):
                return None
        clusters = collect_rc_test_clusters(runs_root, orch.state.release_id, orch.state)
        if clusters["total_failed_tests"] <= 0:
            return None
        return _canonical_hash({
            "schema": DIAGNOSIS_SCHEMA_VERSION,
            "kind": "rc_test_failures",
            "rc": clusters["rc"],
            "leg_totals": clusters["leg_totals"],
            "top_signatures": [(c["signature"], c["count"]) for c in clusters["clusters"][:5]],
        })

    if rec.status != "blocked":
        return None
    data = rec.data or {}
    targets = _resolve_build_targets_for_step(
        orch.state,
        phase_id,
        step_id,
        data,
        step_links=list(rec.links or []),
        step_note=rec.note,
    )
    # Only trigger pipeline/test/telemetry AI diagnosis on steps with build/pipeline/telemetry surfaces.
    if not targets and phase_id not in ("build_verify", "ccd", "sign_off", "play_store", "publish"):
        return None
    return _canonical_hash({
        "schema": DIAGNOSIS_SCHEMA_VERSION,
        "kind": "blocked_step",
        "step": f"{phase_id}.{step_id}",
        "note": (rec.note or "")[:240],
        "builds": [(t["pipeline_key"], t["build_id"]) for t in targets],
        "rows": data.get("rows"),
        "version": data.get("version"),
        "pr_id": data.get("pr_id"),
    })


def stored_diagnosis(state, phase_id: str, step_id: str, expected_signature: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Read `ai_diagnosis` from the canonical step record (`rc_report_gate` owns shared RC test diagnosis)."""
    target_step = "rc_report_gate" if (phase_id == "build_verify" and step_id == "rc_report_notify") else step_id
    rec = state.get_step(phase_id, target_step)
    diag = (rec.data or {}).get("ai_diagnosis")
    if not isinstance(diag, dict) or not diag.get("headline"):
        return None
    if expected_signature and diag.get("signature") != expected_signature:
        return None
    return diag


def build_deterministic_brief(evidence: dict[str, Any]) -> dict[str, Any]:
    """Synthesize a zero-hallucination deterministic diagnosis brief from collected evidence."""
    step_key = evidence["step"]
    kind = evidence["diagnosis_kind"]
    links: list[dict[str, str]] = []
    bullets: list[str] = []
    actions: list[str] = []

    if kind == "rc_test_failures":
        tc = evidence["test_clusters"]
        total_fail = tc["total_failed_tests"]
        clusters = tc["clusters"]
        ov = tc["provider_overlap"]
        top = clusters[0] if clusters else None
        hint_labels = {
            "lab_or_test_account_infra": "Lab / test-account infrastructure",
            "device_or_ftl_infra": "Firebase / emulator device infrastructure",
            "ui_selector_or_timeout": "UI selector / timeout flake",
            "auth_or_broker_flow": "Broker / eSTS auth flow failure",
            "assertion_or_product_regression": "Product assertion / potential regression",
        }
        if top:
            h_lbl = hint_labels.get(top["hint"], top["hint"])
            headline = (
                f"{total_fail} failed test(s) across {tc['cluster_count']} root-cause cluster(s) "
                f"(top: {top['count']}x {h_lbl})"
            )
            root_cause = (
                f"Top cluster ({top['count']}/{total_fail} failures in {', '.join(top['legs'])}): "
                f"`{top['signature']}`."
            )
        else:
            headline = f"{total_fail} failed RC test(s) recorded across Phase 2 suites"
            root_cause = "Failing test cases were recorded; inspect suite results in ADO."

        for cl in clusters[:3]:
            samples = ", ".join(cl["sample_tests"][:2])
            bullets.append(
                f"{cl['count']}x [{', '.join(cl['legs'])}] `{cl['signature']}` (e.g. {samples})"
            )
        if ov["both_ecs_and_local_count"]:
            bullets.append(
                f"Cross-provider overlap: {ov['both_ecs_and_local_count']} test(s) failed in BOTH MRWP ECS and Local "
                f"(points to shared code or lab account issue, not an ECS flight)."
            )
        elif ov["ecs_only_count"] and not ov["local_only_count"]:
            bullets.append(
                f"ECS-only signal: {ov['ecs_only_count']} Broker test(s) failed ONLY in MRWP ECS (Local passed) — "
                f"inspect active ECS flights/configs."
            )
        if tc["flaky_recovered_count"]:
            bullets.append(
                f"{tc['flaky_recovered_count']} additional test(s) failed once and recovered on retry (flaky)."
            )

        if top and top["hint"] == "lab_or_test_account_infra":
            actions.append(f"Check Lab User health ({coords.link('test_accounts')}) for locked/expired test accounts, then rerun failing suites.")
        elif ov["ecs_only_count"] and not ov["local_only_count"]:
            actions.append("Compare failing ECS test cases against MRWP Local and verify recent ECS flight rollouts before approving.")
        else:
            actions.append("Inspect the top exception cluster above against recent release-branch commits; cherry-pick + run `rc-retriggered` if a product regression is confirmed.")
        actions.append("If failures are confirmed lab/infra noise and pass thresholds are acceptable, approve `rc_report_gate` with rationale.")
        return {
            "source": "deterministic",
            "confidence": "medium",
            "headline": headline,
            "root_cause": root_cause,
            "evidence": bullets[:5],
            "recommended_actions": actions[:4],
            "links": links,
        }

    if step_key == "build_verify.telemetry_verify":
        tv = evidence.get("telemetry") or {}
        ver = tv.get("apk_version") or "current RC APK"
        mins = tv.get("minutes_since_auth_ecs")
        wait_phrase = f" ({mins}m elapsed since `auth_ecs` completed)" if mins is not None else ""
        return {
            "source": "deterministic",
            "confidence": "medium",
            "headline": f"0 Kusto rows in `loadaccountsoperations` for Authenticator `{ver}`{wait_phrase}",
            "root_cause": (
                f"No events with `AppInfo_Version == \"{ver}\"` have landed in `android_auth` over the 30d window yet. "
                "Most common causes right after `auth_ecs` are ADX ingestion delay (15–90m) or zero dogfood installs yet."
            ),
            "evidence": [
                f"Queried `{tv.get('cluster')}` / `{tv.get('database')}` for `AppInfo_Version == \"{ver}\"` -> `0` rows.",
                *([f"Diagnostic KQL to check latest ingested versions: `{tv['diagnostic_kql']}`"] if tv.get("diagnostic_kql") else []),
            ],
            "recommended_actions": [
                "Allow the 2h `release-drainer` to re-query automatically if `auth_ecs` finished recently.",
                "Run the diagnostic KQL via `kusto-analyst` to verify whether `loadaccountsoperations` ingestion is healthy and whether the RC version string has a suffix mismatch.",
            ],
            "links": links,
        }

    # Pipeline / build block evidence (including recursive child pipelines queued via `queue-build.ps1`)
    builds = evidence.get("builds") or []
    failed_tasks = [
        (b, t, None)
        for b in builds
        for t in (b.get("failed_tasks") or [])
    ]
    child_failed_tasks = [
        (b, t, cb)
        for b in builds
        for cb in (b.get("child_builds") or [])
        for t in (cb.get("failed_tasks") or [])
    ]
    root_stages = [
        (b, s)
        for b in builds
        for s in (b.get("root_failed_stages") or b.get("failed_stages") or [])
    ]

    if child_failed_tasks:
        parent_b, child_t, child_b = child_failed_tasks[0]
        parent_stage = (
            failed_tasks[0][1].get("stage_name")
            if failed_tasks else (root_stages[0][1]["name"] if root_stages else "parent stage")
        )
        child_issue = (
            child_t["issues"][0]
            if child_t.get("issues")
            else (child_t["log_excerpt"][-1] if child_t.get("log_excerpt") else "child task failed")
        )
        headline = (
            f"{parent_b['label']} #{parent_b['build_id']} stage '{parent_stage}' failed in "
            f"child build #{child_b['build_id']} at task '{child_t['task_name']}'"
        )
        root_cause = (
            f"Stage `{parent_stage}` queued child pipeline #{child_b['build_id']}, which failed in "
            f"`{child_t.get('stage_name') or 'job'} / {child_t['task_name']}`: {child_issue[:260]}"
        )
        bullets.append(
            f"[Child build #{child_b['build_id']}] `{child_t.get('stage_name') or '?'} / {child_t['task_name']}`: {child_issue[:220]}"
        )
        if child_t.get("task_log_url"):
            links.append({"name": f"Child build #{child_b['build_id']} — {child_t['task_name']} log", "url": child_t["task_log_url"]})
        if failed_tasks and failed_tasks[0][1].get("task_log_url"):
            links.append({"name": f"{parent_b['label']} — {failed_tasks[0][1]['task_name']} log", "url": failed_tasks[0][1]["task_log_url"]})
        for ym in (evidence.get("matched_yaml_files") or [])[:2]:
            bullets.append(f"Pipeline template: `{ym}`")
        actions.append(
            f"Inspect & fix the root error in child build #{child_b['build_id']} (`{child_t['task_name']}`), "
            f"then click 'Retry stage' on `{parent_stage}` in {parent_b['label']} #{parent_b['build_id']}."
        )
    elif failed_tasks:
        b0, t0, _ = failed_tasks[0]
        stage_part = f" in stage '{t0['stage_name']}'" if t0.get("stage_name") else ""
        headline = f"{b0['label']} #{b0['build_id']} failed at task '{t0['task_name']}'{stage_part}"
        first_issue = (
            t0["issues"][0]
            if t0.get("issues")
            else (t0["log_excerpt"][-1] if t0.get("log_excerpt") else (evidence.get("step_note") or "Task failed."))
        )
        root_cause = f"ADO task `{t0['task_name']}`{stage_part} reported: {first_issue[:260]}"
        if b0.get("succeeded_stages"):
            bullets.append(
                f"Stages succeeded before failure ({len(b0['succeeded_stages'])}): {', '.join(b0['succeeded_stages'][:6])}"
            )
        for b, t, _ in failed_tasks[:3]:
            msg = t["issues"][0] if t.get("issues") else (t["log_excerpt"][-1] if t.get("log_excerpt") else t["result"])
            bullets.append(f"[{b['label']} #{b['build_id']}] `{t.get('stage_name') or '?'} / {t['task_name']}`: {msg[:220]}")
            if t.get("task_log_url"):
                links.append({"name": f"{b['label']} — {t['task_name']} log", "url": t["task_log_url"]})
        matched_yamls = evidence.get("matched_yaml_files") or ([b0["yaml_path"]] if b0.get("yaml_path") else [])
        for ym in matched_yamls[:2]:
            bullets.append(f"Pipeline definition source: `{ym}`")
        actions.append(f"Open the failing task log (`{t0['task_name']}`) to verify whether the failure is transient or a branch/version/feed issue.")
        stage_retry_lbl = f"stage '{t0['stage_name']}'" if t0.get("stage_name") else "the failed stage"
        actions.append(f"Once resolved (or if transient 1ES/network blip), click 'Retry stage' on {stage_retry_lbl} in ADO #{b0['build_id']}.")
    elif root_stages:
        b0, s0 = root_stages[0]
        headline = f"{b0['label']} #{b0['build_id']} failed at root stage '{s0['name']}' ({s0['result']})"
        issue = s0["issues"][0] if s0.get("issues") else "Stage failed before downstream stages could run."
        root_cause = f"Upstream stage `{s0['name']}` ended with `{s0['result']}` ({issue[:240]}), causing {len(b0.get('skipped_downstream_stages') or [])} downstream stage(s) to be skipped."
        bullets.append(f"[{b0['label']} #{b0['build_id']}] Root failed stage: `{s0['name']}` -> `{s0['result']}`")
        links.append({"name": f"{b0['label']} #{b0['build_id']}", "url": b0["build_url"]})
        actions.append(f"Open stage `{s0['name']}` in ADO #{b0['build_id']} ({b0['build_url']}), apply corrective action, and click 'Retry stage'.")
    else:
        return {
            "source": "deterministic",
            "confidence": "low",
            "is_redundant_fallback": True,
            "headline": f"{evidence.get('step_name') or step_key} is blocked",
            "root_cause": (evidence.get("step_note") or "Step blocked; inspect linked artifacts.").strip(),
            "evidence": [],
            "recommended_actions": [],
            "links": [{"name": f"{b['label']} #{b['build_id']}", "url": b["build_url"]} for b in builds[:2]],
        }

    return {
        "source": "deterministic",
        "confidence": "high" if child_failed_tasks else ("medium" if failed_tasks else "low"),
        "headline": headline,
        "root_cause": root_cause,
        "evidence": bullets[:5],
        "recommended_actions": actions[:4],
        "links": links[:4],
    }


def collect_step_evidence(
    orch,
    runs_root: str,
    config_path: str,
    phase_id: str,
    step_id: str,
    *,
    fetch_live_logs: bool = True,
) -> dict[str, Any]:
    """Collect complete deterministic diagnostic evidence + AI prompt instructions for `(phase_id, step_id)`."""
    target_step = "rc_report_gate" if (phase_id == "build_verify" and step_id == "rc_report_notify") else step_id
    step_def = orch.workflow.step(phase_id, target_step)
    rec = orch.state.get_step(phase_id, target_step)
    data = dict(rec.data or {})
    sig = compute_step_failure_signature(orch, runs_root, phase_id, target_step)
    existing = stored_diagnosis(orch.state, phase_id, target_step, expected_signature=sig)

    is_rc_failures = phase_id == "build_verify" and target_step == "rc_report_gate"
    diagnosis_kind = "rc_test_failures" if is_rc_failures else "blocked_step"

    builds_evidence: list[dict[str, Any]] = []
    skill_Set: list[str] = []
    local_files: list[str] = []
    matched_yaml_files: list[str] = []
    matched_yaml_snippets: dict[str, str] = {}
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(config_path)))
    if os.path.basename(repo_root).lower() == "release-agent":
        repo_root = os.path.dirname(repo_root)

    if not is_rc_failures:
        for target in _resolve_build_targets_for_step(
            orch.state,
            phase_id,
            target_step,
            data,
            step_links=list(rec.links or []),
            step_note=rec.note,
        ):
            b_ev = collect_pipeline_timeline_evidence(target, fetch_live_logs=fetch_live_logs)
            builds_evidence.append(b_ev)
            for sk in target.get("skills") or []:
                if sk not in skill_Set:
                    skill_Set.append(sk)
            if target.get("yaml_path") and target["yaml_path"] not in local_files:
                local_files.append(target["yaml_path"])
            for sp in target.get("scripts") or []:
                if sp not in local_files:
                    local_files.append(sp)
            failed_stage_names = [
                s["name"] for s in (b_ev.get("root_failed_stages") or b_ev.get("failed_stages") or []) if s.get("name")
            ] + [
                t["stage_name"] for t in (b_ev.get("failed_tasks") or []) if t.get("stage_name")
            ]
            st_paths, st_snips = _stage_template_paths_and_snippets(failed_stage_names, repo_root)
            for pth in st_paths:
                if pth not in matched_yaml_files:
                    matched_yaml_files.append(pth)
                if pth not in local_files:
                    local_files.insert(0, pth)
            matched_yaml_snippets.update(st_snips)

    sibling_context = None
    if phase_id == "build_verify" and target_step in ("mrwp_ecs", "mrwp_local"):
        other_step = "mrwp_local" if target_step == "mrwp_ecs" else "mrwp_ecs"
        other_rec = orch.state.get_step("build_verify", other_step)
        if other_rec.status == "blocked":
            sibling_context = {
                "sibling_step": f"build_verify.{other_step}",
                "sibling_status": "blocked",
                "sibling_links": list(other_rec.links or []),
                "hint": (
                    f"Both `{target_step}` and `{other_step}` are blocked concurrently. "
                    "Check whether both failed in the same shared upstream stage (e.g. `Utility Libraries`, "
                    "`Common4j`, `Common`, `AADAuthenticator`, or `MSAL`) vs different stages!"
                ),
            }

    test_clusters = (
        collect_rc_test_clusters(runs_root, orch.state.release_id, orch.state)
        if (is_rc_failures or (phase_id == "build_verify" and target_step in ("mrwp_ecs", "mrwp_local", "auth_ecs")))
        else None
    )
    if is_rc_failures:
        skill_Set = ["release-helper", "codebase-researcher", "kusto-analyst"]
        local_files = [
            "AuthClientAndroidPipelines/production/monthly-release/monthly-release.yml",
            "AuthClientAndroidPipelines/templates/runTests/run-monthly-ui-automation.yml",
        ]

    telemetry_info = None
    if phase_id == "build_verify" and target_step == "telemetry_verify":
        skill_Set = ["kusto-analyst", "release-helper"]
        auth_done_at = schedule.parse_iso_utc(orch.state.get_step("build_verify", "auth_ecs").completed_at)
        now_utc = orch.now_local.astimezone(timezone.utc) if orch.now_local.tzinfo else datetime.now(timezone.utc)
        mins_since = (
            max(0, int((now_utc - auth_done_at).total_seconds() // 60))
            if auth_done_at is not None else None
        )
        apk_ver = data.get("version") or ""
        telemetry_info = {
            "apk_version": apk_ver,
            "rows": data.get("rows"),
            "minutes_since_auth_ecs": mins_since,
            "cluster": "https://idshareduser.westus2.kusto.windows.net",
            "database": "android_auth",
            "diagnostic_kql": (
                "loadaccountsoperations | where TIMESTAMP > ago(6h) "
                "| summarize rows=count(), latest=max(TIMESTAMP) by tostring(AppInfo_Version) "
                "| top 15 by latest desc"
            ),
        }

    evidence: dict[str, Any] = {
        "release": orch.state.release_id,
        "phase": phase_id,
        "step_id": target_step,
        "step": f"{phase_id}.{target_step}",
        "step_name": step_def.name if step_def else f"{phase_id}.{target_step}",
        "diagnosis_kind": diagnosis_kind,
        "failure_signature": sig,
        "step_status": rec.status,
        "step_note": rec.note,
        "step_links": list(rec.links or []),
        "builds": builds_evidence,
        "matched_yaml_files": matched_yaml_files,
        "matched_yaml_snippets": matched_yaml_snippets,
        "sibling_context": sibling_context,
        "test_clusters": test_clusters,
        "telemetry": telemetry_info,
        "skill_hints": {
            "recommended_skills": skill_Set,
            "workspace_files_to_inspect": local_files,
            "ado_mcp_fallback": (
                "If any entry in `builds` has `timeline_fetched: false` or lacks log lines, IMMEDIATELY query "
                "ADO directly using ADO MCP (`pipelines_build` action='get_status' + `pipelines_build_log` "
                "action='list'/'get_content' for `project` + `build_id`, including any queued child build IDs!) "
                "so you read the actual failing stage and task log before calling `record-diagnosis`."
            ),
            "anti_redundancy_rule": (
                "FORBIDDEN: NEVER restate `step_note` (e.g. '<Step> is blocked' or '15 stages never ran — review "
                "failed stage outputs per TSG'). You MUST identify the EXACT upstream stage + task (and child "
                "pipeline build if queued via `queue-build.ps1`) that failed, quote the specific error/exception/HTTP "
                "code from its log, cross-check `AuthClientAndroidPipelines/` YAML or branch commits if relevant, "
                "and give the concrete remediation."
            ),
            "read_only_rule": (
                "Strictly READ-ONLY diagnosis: NEVER retry builds, mutate git branches, or approve gates during diagnosis."
            ),
        },
    }
    evidence["deterministic_brief"] = build_deterministic_brief(evidence)
    evidence["existing_diagnosis"] = existing
    if sig:
        det_links = (evidence["deterministic_brief"] or {}).get("links") or []
        link_flags = "".join(
            f" --link \"{lnk['name']}={lnk['url']}\""
            for lnk in det_links[:3]
            if lnk.get("name") and lnk.get("url")
        )
        evidence["record_command"] = (
            f"python -m orchestrator.cli --scheduled record-diagnosis --release {orch.state.release_id} "
            f"--phase {phase_id} --step {target_step} --signature \"{sig}\" "
            "--confidence <high|medium|low> --headline \"<concise what failed>\" "
            "--root-cause \"<evidence-backed why it failed>\" "
            f"--evidence \"<key log/cluster fact 1>\" --action \"<concrete next step 1>\"{link_flags} --json"
        )
    return evidence


def pending_diagnosis_targets(
    orch,
    runs_root: str,
    config_path: str,
) -> list[dict[str, Any]]:
    """Return active frontier targets that lack a recorded AI diagnosis for their current `failure_signature`."""
    if orch.state.halt or orch.state.cancellation or not orch.gate.signed:
        return []
    selection = orch.scheduling()
    frontier = selection.frontier
    if frontier is None or not selection.phase(frontier.id).due:
        return []

    targets: list[dict[str, Any]] = []
    seen: set[str] = set()

    for s_ready in selection.steps:
        step = s_ready.definition
        if step.phase_id != frontier.id or s_ready.complete or not s_ready.prerequisites_met:
            continue
        rec = orch.state.get_step(step.phase_id, step.id)
        check_step_id = "rc_report_gate" if (step.phase_id == "build_verify" and step.id == "rc_report_notify") else step.id
        canonical_key = f"{step.phase_id}.{check_step_id}"
        if canonical_key in seen:
            continue
        if check_step_id == "rc_report_gate" and step.phase_id == "build_verify":
            sig = compute_step_failure_signature(orch, runs_root, "build_verify", "rc_report_gate")
        elif rec.status == "blocked":
            sig = compute_step_failure_signature(orch, runs_root, step.phase_id, step.id)
        else:
            sig = None
        if not sig:
            continue
        seen.add(canonical_key)
        step_def = orch.workflow.step(step.phase_id, check_step_id)
        sname = step_def.name if step_def else canonical_key
        existing = stored_diagnosis(orch.state, step.phase_id, check_step_id, expected_signature=sig)
        if (
            existing
            and existing.get("source") == "ai"
            and not is_parroted_diagnosis(existing.get("headline", ""), existing.get("root_cause", ""), sname)
        ):
            continue
        targets.append({
            "scope": "ai_diagnosis",
            "step": canonical_key,
            "phase": step.phase_id,
            "step_id": check_step_id,
            "name": f"Diagnose failure — {sname}",
            "failure_signature": sig,
            "requires_approval": False,
            "instructions": (
                "DEEP ROOT-CAUSE INVESTIGATION (READ-ONLY — NEVER PARROT `step_note`!): "
                "1) Run `diagnose_command` to pull the ADO `/timeline` (`root_failed_stages`, `failed_tasks`, "
                "`log_excerpt`), recursive child builds (`child_builds` from `queue-build.ps1`), and matching "
                "`AuthClientAndroidPipelines/` YAML snippets. "
                "2) If `timeline_fetched` is false or logs need deeper lines, query ADO directly via ADO MCP "
                "(`pipelines_build` action='get_status' + `pipelines_build_log` action='list'/'get_content' on "
                "`project` + `build_id`, following any child build IDs!). "
                "3) Cross-check `AuthClientAndroidPipelines/` YAML/scripts and compare sibling failures (`mrwp_ecs` "
                "vs `mrwp_local`). "
                "4) Persist via `record_command`: `--headline` MUST name the exact failed stage/task/child-build or "
                "exception, `--root-cause` MUST explain WHY it failed from the log error, and `--action` MUST give "
                "the concrete remediation (`record-diagnosis` rejects generic '<Step> is blocked' / 'N stages never ran' text)."
            ),
            "diagnose_command": (
                f"python -m orchestrator.cli diagnose --release {orch.state.release_id} "
                f"--phase {step.phase_id} --step {check_step_id} --json"
            ),
            "command": (
                f"python -m orchestrator.cli diagnose --release {orch.state.release_id} "
                f"--phase {step.phase_id} --step {check_step_id} --json"
            ),
            "record_command": (
                f"python -m orchestrator.cli --scheduled record-diagnosis --release {orch.state.release_id} "
                f"--phase {step.phase_id} --step {check_step_id} --signature \"{sig}\" "
                "--confidence <high|medium|low> --headline \"<concise what failed>\" "
                "--root-cause \"<evidence-backed why it failed>\" "
                "--evidence \"<key log/cluster fact>\" --action \"<concrete next step>\" --json"
            ),
        })
    return targets


def is_parroted_diagnosis(headline: str, root_cause: str, step_name: str = "") -> bool:
    """Detect shallow diagnoses that merely repeat '<Step> is blocked' or 'N stages never ran'."""
    h = (headline or "").strip().lower()
    rc = (root_cause or "").strip().lower()
    sname = (step_name or "").strip().lower()
    if not h or h.endswith(" is blocked") or (sname and h == f"{sname} is blocked"):
        return True
    if ("downstream stages ran" in rc or "stages never ran" in rc) and not any(
        tok in rc for tok in ("task", "stage '", "stage `", "exception", "error", "child", "http", "gradle", "timeout")
    ):
        return True
    return False


def effective_diagnosis_for_step(
    orch,
    runs_root: str,
    config_path: str,
    phase_id: str,
    step_id: str,
    *,
    fetch_live_logs: bool = False,
) -> Optional[dict[str, Any]]:
    """Return the stored `ai_diagnosis` if fresh & non-redundant, else non-redundant deterministic fallback."""
    target_step = "rc_report_gate" if (phase_id == "build_verify" and step_id == "rc_report_notify") else step_id
    sig = compute_step_failure_signature(orch, runs_root, phase_id, target_step)
    if not sig:
        return None
    step_def = orch.workflow.step(phase_id, target_step)
    sname = step_def.name if step_def else ""
    stored = stored_diagnosis(orch.state, phase_id, target_step, expected_signature=sig)
    if stored is not None and not is_parroted_diagnosis(stored.get("headline", ""), stored.get("root_cause", ""), sname):
        return stored
    ev = collect_step_evidence(
        orch, runs_root, config_path, phase_id, target_step, fetch_live_logs=fetch_live_logs
    )
    det = ev.get("deterministic_brief")
    if not isinstance(det, dict) or det.get("is_redundant_fallback"):
        return None
    return det


def record_step_diagnosis(
    runs_root: str,
    release_id: str,
    config_path: str,
    *,
    phase_id: str,
    step_id: str,
    signature: Optional[str],
    headline: str,
    root_cause: str,
    confidence: str = "medium",
    evidence_bullets: Optional[list[str]] = None,
    recommended_actions: Optional[list[str]] = None,
    skills_used: Optional[list[str]] = None,
    links: Optional[list[dict[str, str]]] = None,
    source: str = "ai",
) -> dict[str, Any]:
    """Persist an AI (or deterministic) root-cause diagnosis on `step_rec.data['ai_diagnosis']`."""
    st, orch = C.load_orch(runs_root, release_id, config_path)
    target_step = "rc_report_gate" if (phase_id == "build_verify" and step_id == "rc_report_notify") else step_id
    step_def = orch.workflow.step(phase_id, target_step)
    if step_def is None:
        raise ValueError(f"Unknown workflow step: {phase_id}.{target_step}")
    if source == "ai" and is_parroted_diagnosis(headline, root_cause, step_def.name):
        raise ValueError(
            "Rejected redundant/parroted AI diagnosis: headline/root_cause cannot merely restate "
            "'<Step> is blocked' or 'N stages never ran'. Inspect the ADO build timeline/logs "
            "(via `diagnose` or ADO MCP `pipelines_build` / `pipelines_build_log`) and cite the "
            "actual failed stage, task, child build, or error message."
        )
    live_sig = compute_step_failure_signature(orch, runs_root, phase_id, target_step)
    eff_sig = signature or live_sig
    if not eff_sig:
        raise ValueError(
            f"Step {phase_id}.{target_step} has no active blocked state or RC test failures to diagnose."
        )
    if live_sig and signature and signature != live_sig:
        raise ValueError(
            f"Stale diagnosis signature for {phase_id}.{target_step}: expected {live_sig}, got {signature}."
        )
    conf = confidence.lower().strip() if confidence else "medium"
    if conf not in ("high", "medium", "low"):
        conf = "medium"

    # Carry forward deterministic deep links (e.g. failing task log URL) when caller omits links.
    merged_links = list(links or [])
    if not merged_links:
        det = collect_step_evidence(
            orch, runs_root, config_path, phase_id, target_step, fetch_live_logs=False
        ).get("deterministic_brief") or {}
        merged_links = list(det.get("links") or [])

    payload = {
        "signature": eff_sig,
        "source": source,
        "confidence": conf,
        "headline": headline.strip(),
        "root_cause": root_cause.strip(),
        "evidence": [str(x).strip() for x in (evidence_bullets or []) if str(x).strip()][:6],
        "recommended_actions": [str(x).strip() for x in (recommended_actions or []) if str(x).strip()][:5],
        "skills_used": [str(x).strip() for x in (skills_used or []) if str(x).strip()],
        "links": merged_links[:4],
        "diagnosed_at": datetime.now(timezone.utc).isoformat(),
    }
    rec = st.get_step(phase_id, target_step)
    rec.data = {**(rec.data or {}), "ai_diagnosis": payload}
    # Merge any new task-log deep links onto blocked step links for one-click access.
    if rec.status == "blocked" and merged_links:
        existing_urls = {lnk.get("url") for lnk in (rec.links or []) if isinstance(lnk, dict)}
        for lnk in merged_links:
            if lnk.get("url") and lnk["url"] not in existing_urls:
                rec.links = list(rec.links or []) + [lnk]
                existing_urls.add(lnk["url"])
    st.set_step(phase_id, target_step, rec)
    C.save_state(st, runs_root, release_id)
    C.elog(runs_root, release_id).log(
        "step_diagnosis_recorded",
        phase=phase_id,
        step=target_step,
        source=source,
        confidence=conf,
        headline=payload["headline"],
    )
    return {
        "status": "recorded",
        "release": release_id,
        "phase": phase_id,
        "step": target_step,
        "diagnosis": payload,
    }


def format_diagnosis_markdown(diag: dict[str, Any], *, indent: str = "  ") -> list[str]:
    """Render a compact Markdown triage sub-block for Scout Bot DM / `/release-agent status`."""
    if not isinstance(diag, dict) or not diag.get("headline"):
        return []
    src_badge = "🤖 AI Triage" if diag.get("source") == "ai" else "🔍 Auto-Triage"
    conf = str(diag.get("confidence") or "medium").upper()
    lines = [
        f"{indent}- **{src_badge} ({conf} confidence):** {diag['headline']}",
        f"{indent}  - **Why it failed:** {diag.get('root_cause') or 'See evidence below.'}",
    ]
    for ev in (diag.get("evidence") or [])[:3]:
        lines.append(f"{indent}  - **Evidence:** {ev}")
    actions = diag.get("recommended_actions") or []
    if actions:
        lines.append(f"{indent}  - **What to check/do:** {' -> '.join(actions[:2])}")
    links = [
        f"[{lnk['name']}]({lnk['url']})"
        for lnk in (diag.get("links") or [])
        if isinstance(lnk, dict) and lnk.get("name") and lnk.get("url")
    ]
    if links:
        lines.append(f"{indent}  - **Logs:** {' · '.join(links)}")
    return lines


def format_diagnosis_html(diag: dict[str, Any], *, title_prefix: str = "") -> str:
    """Render an Outlook-safe HTML callout card for CTA emails and the RC Verification email."""
    if not isinstance(diag, dict) or not diag.get("headline"):
        return ""
    src_badge = "AI Root-Cause Triage" if diag.get("source") == "ai" else "Automated Failure Triage"
    conf = escape(str(diag.get("confidence") or "medium").upper())
    prefix = f"{escape(title_prefix)} &mdash; " if title_prefix else ""
    ev_items = "".join(
        f"<li style=\"margin:2px 0;\">{escape(str(ev))}</li>"
        for ev in (diag.get("evidence") or [])[:4]
    )
    act_items = "".join(
        f"<li style=\"margin:2px 0;\">{escape(str(act))}</li>"
        for act in (diag.get("recommended_actions") or [])[:4]
    )
    links_html = " &middot; ".join(
        f'<a href="{escape(str(lnk["url"]), quote=True)}" style="color:#175CD3;">{escape(str(lnk["name"]))}</a>'
        for lnk in (diag.get("links") or [])
        if isinstance(lnk, dict) and lnk.get("name") and lnk.get("url")
    )
    ev_block = (
        f"<div style=\"margin-top:6px;\"><b>Key evidence:</b>"
        f"<ul style=\"margin:4px 0 0 18px;padding:0;\">{ev_items}</ul></div>"
        if ev_items else ""
    )
    act_block = (
        f"<div style=\"margin-top:6px;\"><b>Recommended investigation / fix:</b>"
        f"<ol style=\"margin:4px 0 0 18px;padding:0;\">{act_items}</ol></div>"
        if act_items else ""
    )
    lnk_block = f"<div style=\"margin-top:6px;\"><b>Direct logs:</b> {links_html}</div>" if links_html else ""
    return (
        "<div style=\"margin:8px 0 10px;padding:10px 12px;background:#F8FAFC;"
        "border-left:3px solid #1570EF;font-family:-apple-system,Segoe UI,Arial,sans-serif;"
        "font-size:13px;color:#1D2939;\">"
        f"<div><b>🔍 {prefix}{src_badge}</b> "
        f"<span style=\"color:#475467;font-size:12px;\">({conf} confidence)</span>: "
        f"<b>{escape(str(diag['headline']))}</b></div>"
        f"<div style=\"margin-top:4px;\"><b>Likely cause:</b> {escape(str(diag.get('root_cause') or ''))}</div>"
        f"{ev_block}{act_block}{lnk_block}</div>"
    )
