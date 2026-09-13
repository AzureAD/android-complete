"""Localization launch planning and ADO receipts; no queued payload is persisted."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import quote, urlparse

from orchestrator import write_review as W
from orchestrator.step_context import thaw
from tools import pipelines as P


def _positive(value, name):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise ValueError(f"Missing/invalid localization {name}")
    return int(value)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing localization {name}")
    return value.strip()


def branch(value):
    value = _text(value, "source branch")
    if not value.startswith("refs/"):
        value = "refs/heads/" + value
    if not value.startswith("refs/heads/") or any(c.isspace() for c in value):
        raise ValueError("Localization source must be an explicit branch")
    return value


def _sha(value):
    value = _text(value, "source version").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Localization source version must be a full Git commit")
    return value


def organization(value):
    value = urlparse(_text(value, "organization"))
    if value.scheme != "https" or value.query or value.fragment or value.username or value.password:
        raise ValueError("Localization organization must be an HTTPS Azure DevOps collection")
    if value.hostname == "dev.azure.com" and len(value.path.strip("/").split("/")) == 1:
        name = value.path.strip("/")
    elif (value.hostname or "").endswith(".visualstudio.com") and value.path.rstrip("/") in ("", "/DefaultCollection"):
        name = value.hostname[:-len(".visualstudio.com")]
    else:
        raise ValueError("Unsupported localization Azure DevOps organization")
    if not re.fullmatch(r"[A-Za-z0-9-]+", name):
        raise ValueError("Invalid localization organization name")
    return "https://dev.azure.com/" + name.lower()


def variables(values):
    if not isinstance(values, dict):
        raise ValueError("Localization variables must be an object")
    result = {}
    for key, value in values.items():
        key = _text(key, "variable name").casefold()
        if not re.fullmatch(r"[a-z_][a-z0-9_.-]*", key) or key in result:
            raise ValueError("Invalid or duplicate localization variable")
        if isinstance(value, bool):
            value = "true" if value else "false"
        if not isinstance(value, (str, int, float)) or value is None:
            raise ValueError("Localization variables must have observable scalar values")
        result[key] = str(value)
    return dict(sorted(result.items()))


def _read(url):
    ok, result, detail = P._ado_rest_get(url, 60)
    if not ok or not isinstance(result, dict):
        raise ValueError(f"Localization provider read failed: {detail}")
    return result


def _project_url(org, project):
    return f"{organization(org)}/{quote(_text(project, 'project'), safe='')}"


def _launch_plan(orch, target, repository, revision, source_branch, source_version, values, parameters):
    if repository["type"] != "TfsGit":
        raise ValueError("Localization checked launch currently requires an Azure Repos Git source")
    values = variables(values)
    if values.get("iscreateprselected", "").lower() not in ("true", "false"):
        raise ValueError("isCreatePrSelected must explicitly be true or false")
    values["iscreateprselected"] = values["iscreateprselected"].lower()
    if not isinstance(parameters, dict):
        raise ValueError("Localization template parameters must be an object")
    source_branch, source_version = branch(source_branch), _sha(source_version)
    content = {
        "definition": {"id": target["definition_id"], "revision": revision},
        "repository": repository, "sourceBranch": source_branch, "sourceVersion": source_version,
        "parameters": json.dumps(values, sort_keys=True, separators=(",", ":")),
        "templateParameters": parameters,
    }
    return W.WritePlan(
        "launch-localization",
        {"branch": source_branch, "source_version": source_version, "variables": values,
         "template_parameters": parameters},
        (W.WriteOperation("queue_localization_build", target, content),),
        {"ccd": orch.state.ccd, "owner": orch.state.owner_email})


def plan_launch(orch, cfg, *, source_branch=None, source_version=None, overrides=()):
    if not orch.state.ccd:
        raise ValueError("Localization launch requires a CCD")
    org = organization(cfg["org"])
    definition_id = _positive(cfg["pipeline_id"], "definition id")
    definition = _read(f"{_project_url(org, cfg['project'])}/_apis/build/definitions/{definition_id}?api-version=7.1")
    if _positive(definition.get("id"), "definition id") != definition_id:
        raise ValueError("Localization definition read returned a different pipeline")
    project = definition.get("project") or {}
    project_id = _text(project.get("id"), "project id").lower()
    if cfg["project"].casefold() not in (project_id, str(project.get("name", "")).casefold()):
        raise ValueError("Localization definition returned a different project")
    repo = definition.get("repository") or {}
    repository = {"id": _text(repo.get("id"), "repository id").lower(),
                  "type": _text(repo.get("type"), "repository type")}
    selected_branch = branch(source_branch or cfg.get("branch") or repo.get("defaultBranch"))
    if repository["type"] != "TfsGit":
        raise ValueError("Localization checked launch requires Azure Repos Git")
    repo_url = f"{_project_url(org, project_id)}/_apis/git/repositories/{quote(repository['id'], safe='')}"
    refs = _read(f"{repo_url}/refs?filter={quote(selected_branch[5:], safe='')}&api-version=7.1")
    matches = [row for row in refs.get("value", []) if row.get("name") == selected_branch]
    if len(matches) != 1:
        raise ValueError("Localization source branch could not be resolved exactly")
    head = _sha(matches[0].get("objectId"))
    selected_version = _sha(source_version) if source_version else head
    if selected_version != head:
        raise ValueError("Localization source version is not the current reviewed branch head")
    values = dict(cfg.get("variables", {}))
    seen = set()
    for entry in overrides or ():
        key, separator, value = entry.partition("=")
        canonical = key.strip().casefold()
        if not separator or not canonical or canonical in seen:
            raise ValueError("Use unique --variable NAME=VALUE entries")
        seen.add(canonical)
        values = {k: v for k, v in values.items() if k.strip().casefold() != canonical}
        values[key.strip()] = value
    return _launch_plan(
        orch, {"org": org, "project": project_id, "definition_id": definition_id},
        repository, _positive(definition.get("revision"), "definition revision"),
        selected_branch, selected_version, values, cfg.get("template_parameters", {}))


def trigger(operation):
    """Exactly one POST. Read the actual Build receipt before any state attachment."""
    target = operation.target
    ok, receipt, detail = P._ado_rest_send(
        f"{_project_url(target['org'], target['project'])}/_apis/build/builds?api-version=7.1",
        "POST", thaw(operation.content), 90)
    if not ok or not isinstance(receipt, dict):
        raise ValueError(f"Localization trigger result is uncertain: {detail}")
    build_id = _positive(receipt.get("id"), "queued build id")
    try:
        return read_build(target["org"], target["project"], build_id)
    except ValueError as exc:
        raise ValueError(f"Queue response named build {build_id}; receipt unverified: {exc}") from exc


def read_build(org, project, build_id):
    build_id = _positive(build_id, "build id")
    result = _read(f"{_project_url(org, project)}/_apis/build/builds/{build_id}?api-version=7.1")
    if _positive(result.get("id"), "build id") != build_id:
        raise ValueError("Localization provider returned a different build")
    return result


def receipt_plan(orch, cfg, build):
    """Reconstruct reviewed semantics from provider evidence, never from recorder arguments."""
    org = organization(cfg["org"])
    project = build.get("project") or {}
    project_id = _text(project.get("id"), "receipt project id").lower()
    if cfg["project"].casefold() not in (project_id, str(project.get("name", "")).casefold()):
        raise ValueError("Localization receipt is from a different project")
    definition = build.get("definition") or {}
    definition_id = _positive(definition.get("id"), "receipt definition id")
    if definition_id != _positive(cfg["pipeline_id"], "configured definition id"):
        raise ValueError("Localization receipt is from a different definition")
    build_id = _positive(build.get("id"), "build id")
    provider_url = urlparse(_text(build.get("url"), "receipt provider URL"))
    if (provider_url.scheme != "https" or provider_url.username or provider_url.password
            or provider_url.fragment):
        raise ValueError("Invalid localization receipt provider URL")
    expected_url = urlparse(f"{_project_url(org, project_id)}/_apis/build/builds/{build_id}")
    # ADO's legacy collection URL is semantically the same provider.
    path = provider_url.path.replace("/DefaultCollection/", "/")
    if provider_url.hostname == "dev.azure.com":
        parts = path.strip("/").split("/", 1)
        observed_org = organization(f"https://dev.azure.com/{parts[0]}")
        path = "/" + (parts[1] if len(parts) == 2 else "")
    else:
        observed_org = organization(f"{provider_url.scheme}://{provider_url.netloc}")
    expected_path = "/" + expected_url.path.strip("/").split("/", 1)[1]
    if observed_org != org or path.casefold() != expected_path.casefold():
        raise ValueError("Localization receipt provider URL does not match the launch target")
    raw_values = build.get("parameters")
    if isinstance(raw_values, str):
        try:
            raw_values = json.loads(raw_values)
        except ValueError:
            raise ValueError("Localization build parameters are unreadable") from None
    repo = build.get("repository") or {}
    return _launch_plan(
        orch, {"org": org, "project": project_id, "definition_id": definition_id},
        {"id": _text(repo.get("id"), "receipt repository id").lower(),
         "type": _text(repo.get("type"), "receipt repository type")},
        _positive(definition.get("revision"), "receipt definition revision"),
        build.get("sourceBranch"), build.get("sourceVersion"), raw_values,
        build.get("templateParameters") or {})


def receipt_time(build, step, *, now=None):
    def parse(value):
        parsed = datetime.fromisoformat(_text(value, "receipt time").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Localization receipt timestamps must include a timezone")
        return parsed
    queued = parse(build.get("queueTime"))
    execution_start = parse((step.execution or {}).get("started_at"))
    # Queuing has a bounded 90s request timeout; a later matching run is not our receipt.
    attempt_start = parse(step.data.get("in_flight_since") or (step.execution or {}).get("started_at"))
    if (queued < execution_start or queued < attempt_start
            or queued > attempt_start + timedelta(minutes=5)
            or queued > (now or datetime.now(timezone.utc)) + timedelta(minutes=1)):
        raise ValueError("Localization build is outside this execution's launch window")
    return queued.isoformat()
