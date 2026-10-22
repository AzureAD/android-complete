"""Complete transient plans and checked executors for the two finalization Git writers."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from orchestrator import revision
from orchestrator.write_review import WriteOperation, WritePlan, VerifiedProgress
from steps.finalize import integ_prs as I, oneauth_common_pr as O
from steps.lib.mockctx import MISSING
from tools import git_review as G, oneauth as OA, prs as PR


def _read(result, label):
    ok, value, detail = result
    if not ok:
        raise ValueError(f"{label}: {detail}")
    return value


def _existing(value, id_key):
    if value is not None:
        if (not isinstance(value, Mapping) or not str(value.get(id_key, "")).isdigit()
                or int(value[id_key]) <= 0):
            raise ValueError("Incomplete existing-PR result; no complete review possible")
        value = dict(value)
    return value


def _safe_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Unsafe provider receipt URL; held without persisting it")
    return value


def _find(target, head, base):
    if target["tool"] == "gh":
        result = PR.gh_find_open_pr(target["gh_repo"], head, base)
    else:
        a = target["ado"]
        result = PR.az_find_open_pr(a["org"], a["project"], a["repository"], head, base)
    value = _existing(_read(result, "PR lookup"), "number")
    if value is not None and (not all(isinstance(value.get(k), str) for k in ("title", "url", "body"))
                              or target["tool"] == "gh" and not isinstance(value.get("labels"), list)):
        raise ValueError("Incomplete existing PR content")
    if value is not None and target["tool"] == "ado":
        a = target["ado"]
        value["work_items"] = _read(
            PR.read_pr_work_items(a["org"], a["project"], value["number"]), "PR work items")
        if (not isinstance(value["work_items"], list)
                or any(not isinstance(identity, str) or not identity.isdigit()
                       or int(identity) <= 0 for identity in value["work_items"])):
            raise ValueError("Incomplete PR work-item readback")
    return value


def _tip(target, name):
    local = G.remote_tip(target["root"], target["remote"], name)
    if local != PR.provider_branch_object_id(target, name):
        raise ValueError("Origin and reviewed hosting repository tips disagree: " + name)
    return local


def _remote_matches_provider(target):
    if G.remote_identity(target["remote"]) not in {
            G.remote_identity(value) for value in PR.provider_repository_urls(target)}:
        raise ValueError("Origin does not match the reviewed hosting repository clone URI")


def _pbi(context, args):
    mode, value = I._pbi_ref(context)
    if getattr(args, "pbi", None) is not None:
        mode, value = "existing", str(args.pbi).strip()
    if mode == "existing":
        if not str(value).isdigit() or int(value) <= 0:
            raise ValueError("PBI must be a positive work-item ID")
        value = str(int(value))
    title = getattr(args, "pbi_title", None) or f"Android {args.release} release — integration PRs"
    return {"mode": mode, "id": value, "title": title,
            "org": I.PL.ENGINEERING_ORG, "project": I.PL.ENGINEERING_PROJECT}


def _integration_scope(context, args):
    injected = context.input("repos", MISSING)
    available = I.REPO_ORDER if injected is MISSING or not injected else list(injected)
    requested = list(getattr(args, "repos", None) or available)
    if (set(available) - set(I.REPO_ORDER) or set(requested) - set(available)
            or not requested):
        raise ValueError("Unknown, excluded, or empty repository selection")
    selected = [key for key in I.REPO_ORDER if key in requested]
    versions, pbi = I._versions(context), _pbi(context, args)
    targets = []
    for key in selected:
        version = versions.get(key)
        if not version:
            raise ValueError("Missing version: " + key)
        cfg = deepcopy(I.CONFIG[key])
        if cfg["tool"] not in ("gh", "ado"):
            raise ValueError("Unsupported PR provider")
        root = Path(PR.repo_dir(cfg["dir"])).resolve()
        target = {"key": key, "root": str(root), "remote": G.remote_url(root),
                  "tool": cfg["tool"]}
        target.update({"gh_repo": cfg["gh_repo"]} if cfg["tool"] == "gh" else {"ado": cfg["ado"]})
        branches = I._branches(context, key, cfg, version)
        targets.append({"target": target, "branches": branches, "labels": cfg.get("labels", [])})
    return {"repos": selected, "versions": {k: versions[k] for k in selected}, "pbi": pbi,
            "targets": targets, "owner": getattr(context.release, "owner_email", None),
            "target_month": getattr(context.release, "target_month", None)}


def _operation_key(kind, target, output=None):
    family = "push" if kind.endswith((".push", ".bump")) else (
        "pbi" if kind == "pbi.create" else "pr")
    return revision.digest({"family": family, "target": target, "output": output})


def integration_progress(orch, context, args):
    return VerifiedProgress(orch, "finalize", I.ID, _integration_scope(context, args),
                            reconcile_operation, resolve_operation)


def integration_plan(context, args, progress=None):
    scope = _integration_scope(context, args)
    if progress and progress.scope != revision.digest(scope):
        raise ValueError("Compound write scope changed during planning")
    selected, versions, pbi = scope["repos"], scope["versions"], scope["pbi"]
    repos, operations = [], []
    outputs = progress.outputs() if progress else {}
    work_item = outputs.get("pbi.id", {"output": "pbi.id"}) if pbi["mode"] == "create" else pbi["id"]
    for item in scope["targets"]:
        target, branches, labels = item["target"], item["branches"], item["labels"]
        key, root = target["key"], target["root"]
        version = versions[key]
        pending = [(kind, head, base) for kind, head, base in (
            ("freeze", branches["wr"], branches["r"]),
            ("integration", branches["ri"], branches["target"]))
            if not progress or not progress.has(_operation_key(
                "integration.create", {**target, "head": head, "base": base}))]
        if not pending:
            continue
        names = {name for _kind, head, base in pending for name in (head, base)}
        tips = {name: _tip(target, G.branch(name)) for name in sorted(names)}
        record = {"target": target, "tips": tips, "prs": []}
        for kind, head, base in pending:
            if head == base:
                raise ValueError("PR source and target branches must differ")
            existing = _find(target, head, base)
            guard = {"head": head, "base": base, "existing": existing}
            record["prs"].append(guard)
            op_target = {**target, "head": head, "base": base}
            expected_head = tips[head]
            if (kind == "integration" and existing is None
                    and not (progress and progress.manual_push(op_target, tips[head], tips[base]))):
                ri = G.plan_ri(root, tips[head], tips[base], base)
                record["ri"] = ri
                if ri["commit"]:
                    operations.append(WriteOperation(
                        "integration.push", op_target, ri,
                        {"head_tip": tips[head], "base_tip": tips[base], "existing": existing}))
                    expected_head = ri["commit_id"]
            if isinstance(work_item, dict):
                body = I.pr_body(key, kind, branches, "__REVIEWED_PBI_ID__")
                pieces = body.split("__REVIEWED_PBI_ID__")
                if len(pieces) != 2:
                    raise ValueError("Ambiguous PBI output placeholder")
                body = [pieces[0], work_item, pieces[1]]
            else:
                body = I.pr_body(key, kind, branches, work_item)
            operations.append(WriteOperation(
                "integration.reuse" if existing else "integration.create", op_target,
                {"title": existing["title"] if existing else I._title(kind, version),
                 "body": existing["body"] if existing else body,
                 "labels": sorted(set(labels)),
                 "work_item": None if existing else work_item,
                 "kind": kind, "existing": existing},
                {"head_tip": expected_head, "base_tip": tips[base], "existing": existing}))
        repos.append(record)
    if (pbi["mode"] == "create" and "pbi.id" not in outputs
            and any(op.kind == "integration.create" for op in operations)):
        operations.insert(0, WriteOperation(
            "pbi.create", {"org": pbi["org"], "project": pbi["project"]},
            {"title": pbi["title"], "type": "Product Backlog Item", "output": "pbi.id"}))
    plan = WritePlan("create-integration-prs",
                     {"repos": selected, "versions": versions, "pbi": pbi},
                     tuple(operations), {"repos": repos})
    # Check again after every repo has been fully planned, before emitting any review.
    _check_integration(plan.as_dict()["preconditions"]["repos"], set())
    return plan


def _check_integration(repos, completed, *, clean=True):
    for repo in repos:
        target = repo["target"]
        if clean:
            G.clean_repository(target["root"])
        if G.remote_url(target["root"]) != target["remote"]:
            raise ValueError("Origin changed after review")
        _remote_matches_provider(target)
        for name, expected in repo["tips"].items():
            if _tip(target, name) != expected:
                raise ValueError("Branch changed after review: " + name)
        for pr in repo["prs"]:
            key = (target["key"], pr["head"], pr["base"])
            if key not in completed and _find(target, pr["head"], pr["base"]) != pr["existing"]:
                raise ValueError("Existing/new PR choice or content changed after review")


def _output(value, outputs):
    if isinstance(value, dict):
        if set(value) != {"output"} or value["output"] not in outputs:
            raise ValueError("Missing reviewed operation output")
        return outputs[value["output"]]
    return value


def _pr_content(target, observed, *, oneauth=False):
    value = {"title": observed["title"],
             "body": observed["description" if oneauth else "body"]}
    if not oneauth and target["tool"] == "gh":
        value["labels"] = sorted(set(observed["labels"]))
    elif not oneauth:
        value["work_items"] = sorted(set(observed["work_items"]))
    return value


def _push_tip(target, *, base=False):
    name = target["base" if base else "head"]
    if G.remote_url(target["root"]) != target["remote"]:
        raise ValueError("Branch update origin changed; held")
    if "tool" in target:
        _remote_matches_provider(target)
        return _tip(target, name)
    repository = {key: target[key] for key in ("org", "project", "repository")}
    if G.remote_identity(OA.repository_remote_url(repository)) != G.remote_identity(target["remote"]):
        raise ValueError("OneAuth hosting repository changed")
    tip = _oa_tip(repository, name)
    if G.remote_tip(target["root"], target["remote"], name) != tip:
        raise ValueError("OneAuth provider and origin disagree")
    return tip


def _pr_identity(value):
    if isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0:
        return str(int(value))
    if isinstance(value, str):
        identity = urlsplit(_safe_url(value)).path.rstrip("/").rsplit("/", 1)[-1]
        if identity.isascii() and identity.isdigit() and int(identity) > 0:
            return str(int(identity))
    raise ValueError("Supply an exact PR ID or its provider URL")


def _read_pr_result(target, identity, *, oneauth):
    coordinates = ({"tool": "ado", "ado": {k: target[k] for k in ("org", "project", "repository")},
                    "head": target["head"], "base": target["base"]} if oneauth else target)
    result = _read(PR.read_pr_result(coordinates, _pr_identity(identity)), "Exact PR result")
    if not oneauth:
        return result
    return {"id": result["number"], "url": result["url"],
            "title": result["title"], "description": result["body"]}


def resolve_operation(operation, manual_result):
    """Read only: verify the owner's explicit result at the original hosting target."""
    descriptor = operation["descriptor"]
    kind, target = descriptor["kind"], descriptor["target"]
    push = kind in ("integration.push", "oneauth.bump")
    if manual_result is None:
        if (not push or operation["receipt"] is not None or operation["resolutions"]
                or not descriptor["before"] or _push_tip(target) != descriptor["before"]):
            raise ValueError("Non-application requires an unchanged original source branch; "
                             "otherwise supply the manually completed result")
        return None
    if push:
        commit = G.object_id(manual_result)
        if _push_tip(target) != commit or commit == descriptor["before"]:
            raise ValueError("Manual commit must be the updated tip of this exact source branch")
        return {"commit": commit, "content": revision.digest({"commit": commit}), "outputs": {},
                "base_tip": _push_tip(target, base=True)}
    if kind == "pbi.create":
        if not manual_result.isascii() or not manual_result.isdigit() or int(manual_result) <= 0:
            raise ValueError("Supply the exact manually created PBI ID")
        identity = str(int(manual_result))
        observed = _read(PR.read_pbi(target["org"], target["project"], identity), "Manual PBI readback")
        if (not isinstance(observed, Mapping) or set(observed) != {"id", "title", "type", "project"}
                or str(observed["id"]) != identity or observed["project"] != target["project"]
                or observed["type"] != "Product Backlog Item" or not isinstance(observed["title"], str)):
            raise ValueError("Manual PBI must exist in the original project with the correct work-item type")
        content = {key: observed[key] for key in ("title", "type", "project")}
        return {"id": identity, "content": revision.digest(content), "outputs": {"pbi.id": identity}}
    if kind not in ("integration.create", "integration.reuse", "oneauth.create", "oneauth.reuse"):
        raise ValueError("This operation does not support a manual result")
    oneauth = kind.startswith("oneauth.")
    observed = _read_pr_result(target, manual_result, oneauth=oneauth)
    if not observed or manual_result not in (
            str(observed["id" if oneauth else "number"]), observed["url"]):
        raise ValueError("Manual PR must match this exact repository and source/target branch pair")
    return {"id": str(observed["id" if oneauth else "number"]), "url": _safe_url(observed["url"]),
            "content": revision.digest(_pr_content(target, observed, oneauth=oneauth)), "outputs": {}}


def reconcile_operation(operation):
    """Exact provider reads only; absent/ambiguous/mismatched results never permit replay."""
    descriptor = operation["descriptor"]
    kind, target, expected = descriptor["kind"], descriptor["target"], descriptor["expected"]
    manual = operation["resolutions"] and operation["resolutions"][-1]["mode"] == "manual"
    if manual:
        expected = operation["receipt"]["content"]
    repository = ({key: target[key] for key in ("org", "project", "repository")}
                  if kind.startswith("oneauth.") else None)
    if kind == "pbi.create":
        identity = operation["receipt"]["id"] if manual else operation["candidate"]
        if not identity:
            raise ValueError("PBI creation outcome unknown (no ID); held, never guessed or recreated")
        observed = _read(PR.read_pbi(target["org"], target["project"], identity), "PBI receipt")
        if not isinstance(observed, Mapping) or set(observed) != {"id", "title", "type", "project"}:
            raise ValueError("PBI receipt missing or mismatched; held")
        value = {"title": observed["title"], "type": observed["type"], "project": observed["project"]}
        if str(observed["id"]) != identity or revision.digest(value) != expected:
            raise ValueError("PBI receipt missing or mismatched; held")
        return {"id": identity, "content": expected, "outputs": {"pbi.id": identity}}
    if kind in ("integration.push", "oneauth.bump"):
        tip = _push_tip(target)
        if revision.digest({"commit": tip}) != expected:
            raise ValueError("Branch update receipt missing or mismatched; held")
        receipt = {"commit": tip, "content": expected, "outputs": {}}
        if manual:
            receipt["base_tip"] = operation["receipt"]["base_tip"]
        return receipt
    if kind not in ("integration.create", "integration.reuse", "oneauth.create", "oneauth.reuse"):
        raise ValueError("Unsupported compound operation receipt; held")
    oneauth = repository is not None
    observed = (_oa_find(repository, target["head"], target["base"]) if oneauth
                else _find(target, target["head"], target["base"]))
    if observed is None:
        identity = (operation["receipt"] or {}).get("id") or operation["candidate"]
        if identity:
            observed = _read_pr_result(target, identity, oneauth=oneauth)
    if not observed or revision.digest(_pr_content(target, observed, oneauth=oneauth)) != expected:
        raise ValueError("PR identity/content uncertain or mismatched; held")
    identity = str(observed["id" if oneauth else "number"])
    candidate = operation["receipt"]["id"] if manual else operation["candidate"]
    if candidate and candidate not in (identity, observed["url"]):
        raise ValueError("PR identity/content uncertain; held")
    return {"id": identity, "url": _safe_url(observed["url"]), "content": expected, "outputs": {}}


def _attempt(progress, kind, target, expected, *, output=None, before=None):
    key = _operation_key(kind, target, output)
    return progress.attempt(key, {"kind": kind, "target": target,
                                  "expected": revision.digest(expected), "before": before})


def _check_operation(operation, *, clean=True):
    target, pre = operation["target"], operation["preconditions"]
    if operation["kind"] == "pbi.create":
        return
    if clean:
        G.clean_repository(target["root"])
    if G.remote_url(target["root"]) != target["remote"]:
        raise ValueError("Origin changed after review")
    _remote_matches_provider(target)
    if (_tip(target, target["head"]) != pre["head_tip"]
            or _tip(target, target["base"]) != pre["base_tip"]):
        raise ValueError("Branch changed after review; rerun the same checked command for the remainder")
    if _find(target, target["head"], target["base"]) != pre["existing"]:
        raise ValueError("Existing/new PR choice or content changed after review")


def execute_integration(authorization):
    plan = authorization.plan.as_dict()
    progress = authorization.progress
    outputs, results = progress.outputs(), []
    for operation in plan["operations"]:
        authorization.validate()
        _check_operation(operation)
        kind, target, content = operation["kind"], operation["target"], operation["content"]
        authorization.validate()
        if kind == "pbi.create":
            op_key = _attempt(progress, kind, target, {
                "title": content["title"], "type": content["type"], "project": target["project"]})
            ok, wid, _url, detail = PR.create_pbi(target["org"], target["project"], content["title"])
            if not ok or not str(wid).isdigit() or int(wid) <= 0:
                raise ValueError("PBI creation failed or uncertain: " + str(detail))
            progress.candidate(op_key, wid)
            outputs.update(progress.acknowledge(op_key)["outputs"])
            results.append("PBI AB#" + str(wid))
            continue
        if kind == "integration.push":
            op_key = None
            def before_push():
                nonlocal op_key
                authorization.validate()
                _check_operation(operation, clean=False)
                op_key = _attempt(progress, kind, target, {"commit": content["commit_id"]},
                                  output=content["commit_id"],
                                  before=operation["preconditions"]["head_tip"])
            G.push_reviewed(target["root"], target["remote"], target["head"],
                            operation["preconditions"]["head_tip"], content,
                            before_push)
            if op_key is None:
                raise ValueError("Push did not cross the checked attempt boundary")
            progress.acknowledge(op_key)
            continue
        if kind == "integration.reuse":
            expected = _pr_content(target, content["existing"])
            if target["tool"] == "gh":
                expected["labels"] = sorted(set(expected["labels"]) | set(content["labels"]))
            op_key = _attempt(progress, kind, target, expected)
            progress.candidate(op_key, content["existing"]["number"])
            if target["tool"] == "gh" and content["labels"]:
                ok, detail = PR.gh_ensure_labels(
                    target["gh_repo"], content["existing"]["number"], content["labels"])
                if not ok:
                    raise ValueError("Label update failed or uncertain: " + detail)
                observed = _find(target, target["head"], target["base"])
                expected = {**content["existing"], "labels": sorted(set(
                    content["existing"]["labels"]) | set(content["labels"]))}
                if observed != expected:
                    raise ValueError("Label update result/content changed; owner must inspect provider")
            url = content["existing"].get("url") or str(content["existing"]["number"])
        elif kind == "integration.create":
            value = content["body"]
            body = "".join(str(_output(v, outputs)) for v in value) if isinstance(value, list) else value
            wid = _output(content["work_item"], outputs)
            expected = {"title": content["title"], "body": body}
            if target["tool"] == "gh":
                expected["labels"] = content["labels"]
            else:
                expected["work_items"] = [str(wid)]
            op_key = _attempt(progress, kind, target, expected)
            if target["tool"] == "gh":
                result = PR.gh_create_pr(target["gh_repo"], target["head"], target["base"],
                                         content["title"], body, labels=content["labels"])
            else:
                a = target["ado"]
                result = PR.az_create_pr(a["org"], a["project"], a["repository"],
                                         target["head"], target["base"], content["title"], body,
                                         work_items=wid)
            url = _read(result, "PR creation failed or uncertain")
            if not url:
                raise ValueError("PR creation returned no result; owner must inspect provider")
            progress.candidate(op_key, _safe_url(url))
            observed = _find(target, target["head"], target["base"])
            if (not observed or observed["url"] != url
                    or observed["title"] != content["title"] or observed["body"] != body
                    or (target["tool"] == "gh"
                        and not set(content["labels"]).issubset(observed["labels"]))):
                raise ValueError("Created PR identity/content uncertain; owner must inspect provider")
        else:
            raise ValueError("Unsupported reviewed operation: " + kind)
        progress.acknowledge(op_key)
        results.append(kind.rsplit(".", 1)[1] + " " + str(url))
    authorization.validate()
    progress.finish()
    return "integ_prs: " + "; ".join(results) + progress.result_note()


def _oa_tip(repository, name):
    return G.object_id(_read(OA.branch_object_id(name, repository=repository), "OneAuth branch tip"))


def _oa_files(repository, paths, commit):
    files = {}
    for key, path in paths.items():
        if (not path.startswith("/") or str(PurePosixPath(path)) != path
                or ".." in PurePosixPath(path).parts or "\\" in path):
            raise ValueError("Unsupported OneAuth file path")
        text = _read(OA.read_text(path, commit, ref_type="commit", repository=repository),
                     "OneAuth content")
        if not isinstance(text, str):
            raise ValueError("Missing exact OneAuth content")
        files[key] = text
    return files


def _oa_find(repository, head, base):
    value = _existing(_read(OA.find_open_pr(head, base, repository=repository),
                           "OneAuth PR lookup"), "id")
    if value is not None and not all(isinstance(value.get(k), str) for k in ("title", "url", "description")):
        raise ValueError("Incomplete OneAuth existing PR content")
    return value


def _oneauth_scope(context, args):
    common, msal = O._versions(context)
    if not all(isinstance(v, str) and v and v == v.strip()
               and all(part.isascii() and part.isdigit() for part in v.split("."))
               for v in (common, msal)):
        raise ValueError("Exact final numeric Common and MSAL versions are required")
    repository = {"org": OA.ORG, "project": OA.PROJECT, "repository": OA.REPO}
    head, base = G.branch(OA.INGEST_BRANCH), G.branch(OA.TARGET_BRANCH)
    if head == base:
        raise ValueError("OneAuth source and target branches must differ")
    root = Path(OA.review_repo_dir(repository, getattr(args, "repo_dir", None))).resolve()
    remote = G.remote_url(root)
    return {"common": common, "msal": msal, "repository": repository, "head": head, "base": base,
            "repo_dir": str(root), "remote": remote, "paths": dict(OA.FILES),
            "owner": getattr(context.release, "owner_email", None),
            "target_month": getattr(context.release, "target_month", None)}


def oneauth_progress(orch, context, args):
    return VerifiedProgress(orch, "finalize", O.ID, _oneauth_scope(context, args),
                            reconcile_operation, resolve_operation)


def oneauth_plan(context, args, progress=None):
    scope = _oneauth_scope(context, args)
    if progress and progress.scope != revision.digest(scope):
        raise ValueError("Compound write scope changed during planning")
    common, msal = scope["common"], scope["msal"]
    repository, head, base = scope["repository"], scope["head"], scope["base"]
    root, remote = scope["repo_dir"], scope["remote"]
    target = {**repository, "head": head, "base": base, "root": str(root), "remote": remote}
    if progress and progress.has(_operation_key("oneauth.create", target)):
        return WritePlan("create-oneauth-common-pr", scope, (), {})
    tips = {name: _oa_tip(repository, name) for name in sorted({head, base})}
    if any(G.remote_tip(root, remote, name) != tip for name, tip in tips.items()):
        raise ValueError("OneAuth origin and provider branch tips disagree")
    counts = _read(OA.ahead_behind(tips[base], tips[head], repository=repository,
                                  ref_type="commit"), "OneAuth ancestry")
    if (not isinstance(counts, Mapping) or any(type(counts.get(k)) is not int or counts[k] < 0
                                             for k in ("ahead", "behind"))):
        raise ValueError("Unknown OneAuth ancestry; no complete review possible")
    paths = dict(OA.FILES)
    if set(paths) != {"toml", "cgmanifest", "readme", "changelog"} or len(set(paths.values())) != 4:
        raise ValueError("Incomplete or overlapping OneAuth file mapping")
    files = _oa_files(repository, paths, tips[head])
    relative_paths = {key: path[1:] for key, path in paths.items()}
    comment = (
        (f"Merge {base} into {head}; " if counts["behind"] else "")
        + f"Ingest AndroidCommon {common} (bump libs.versions.toml, cgmanifest.json, README, CHANGELOG)")
    manual = progress and progress.manual_push(target, tips[head], tips[base])
    content = None if manual else G.plan_merge_edits(
        root, tips[head], tips[base], relative_paths,
        lambda merged: OA.apply_edits(merged, common, msal, paths=relative_paths), comment)
    if content and (files != content["files_before"]
                    or any(content[k] != counts[k] for k in ("ahead", "behind"))):
        raise ValueError("OneAuth local Git objects and provider content/ancestry disagree")
    existing = _oa_find(repository, head, base)
    preconditions = {"tips": tips, "paths": paths, "files": files, "existing": existing}
    operations = []
    expected_head = tips[head]
    if content and content["commit"]:
        operations.append(WriteOperation(
            "oneauth.bump", target, content,
            {"head_tip": tips[head], "base_tip": tips[base]}))
        expected_head = content["commit_id"]
    operations.append(WriteOperation(
        "oneauth.reuse" if existing else "oneauth.create", target,
        {"title": existing["title"] if existing else f"Merge latest common {common} to {base}",
         "body": existing["description"] if existing else (
                  f"Automated (release-agent · oneauth_common_pr): ingest AndroidCommon {common} "
                  f"into `{base}`.\nAny apps that still use MSAL.Android must update to {msal}."),
         "existing": existing},
        {"head_tip": expected_head, "base_tip": tips[base]}))
    plan = WritePlan("create-oneauth-common-pr",
                     {"common": common, "msal": msal, "merge": "owner-confirmed" if manual else content["merge"],
                      "ancestry": dict(counts),
                      "repository": repository, "head": head, "base": base,
                      "repo_dir": str(root), "remote": remote},
                     tuple(operations), preconditions)
    _check_oneauth(plan.as_dict(), tips[head], files, check_pr=True)
    return plan


def _check_oneauth(plan, head_tip, files, *, check_pr, clean=True):
    p, pre = plan["parameters"], plan["preconditions"]
    repository, head, base = p["repository"], p["head"], p["base"]
    if clean:
        G.clean_repository(p["repo_dir"])
    if (G.remote_url(p["repo_dir"]) != p["remote"]
            or G.remote_identity(OA.repository_remote_url(repository)) != G.remote_identity(p["remote"])):
        raise ValueError("OneAuth origin/provider remote URI changed after review")
    if (_oa_tip(repository, head) != head_tip
            or _oa_tip(repository, base) != pre["tips"][base]
            or G.remote_tip(p["repo_dir"], p["remote"], head) != head_tip
            or G.remote_tip(p["repo_dir"], p["remote"], base) != pre["tips"][base]):
        raise ValueError("OneAuth branch changed after review")
    if _oa_files(repository, pre["paths"], head_tip) != files:
        raise ValueError("OneAuth content changed after review")
    if check_pr and _oa_find(repository, head, base) != pre["existing"]:
        raise ValueError("OneAuth existing/new PR choice or content changed after review")


def execute_oneauth(authorization):
    plan = authorization.plan.as_dict()
    progress = authorization.progress
    p, pre = plan["parameters"], plan["preconditions"]
    repository, head = p["repository"], p["head"]
    if not plan["operations"]:
        progress.finish()
        return "oneauth_common_pr: previously recorded PR retained" + progress.result_note()
    tip, files, url = pre["tips"][head], dict(pre["files"]), None

    def check(*, clean=True):
        authorization.validate()
        _check_oneauth(plan, tip, files, check_pr=True, clean=clean)

    for operation in plan["operations"]:
        check()
        if tip != operation["preconditions"]["head_tip"]:
            raise ValueError("OneAuth operation output does not match its reviewed precondition")
        content, kind = operation["content"], operation["kind"]
        authorization.validate()
        if kind == "oneauth.bump":
            op_key = None
            def before_push():
                nonlocal op_key
                check(clean=False)
                op_key = _attempt(progress, kind, operation["target"], {"commit": content["commit_id"]},
                                  output=content["commit_id"], before=tip)
            observed_tip = G.push_reviewed(p["repo_dir"], p["remote"], head, tip, content,
                                          before_push)
            if G.object_id(observed_tip) != content["commit_id"]:
                raise ValueError("OneAuth push returned an unreviewed commit; owner must inspect provider")
            tip, files = observed_tip, dict(content["final_files"])
            if op_key is None:
                raise ValueError("Push did not cross the checked attempt boundary")
            progress.acknowledge(op_key)
        elif kind == "oneauth.create":
            op_key = _attempt(progress, kind, operation["target"],
                              {"title": content["title"], "body": content["body"]})
            pr = _existing(_read(OA.create_pr(head, p["base"], content["title"], content["body"],
                                            repository=repository), "OneAuth create PR"), "id")
            if pr is None or not pr.get("url"):
                raise ValueError("OneAuth PR result uncertain; owner must inspect provider")
            progress.candidate(op_key, pr["id"])
            observed = _oa_find(repository, head, p["base"])
            if (not observed or observed["id"] != pr["id"] or observed["title"] != content["title"]
                    or observed["description"] != content["body"]):
                raise ValueError("Created OneAuth PR content uncertain; owner must inspect provider")
            url = pr["url"]
            progress.acknowledge(op_key)
        elif kind == "oneauth.reuse":
            op_key = _attempt(progress, kind, operation["target"],
                              _pr_content(operation["target"], content["existing"], oneauth=True))
            progress.candidate(op_key, content["existing"]["id"])
            url = content["existing"].get("url") or str(content["existing"]["id"])
            progress.acknowledge(op_key)
        else:
            raise ValueError("Unsupported reviewed operation: " + kind)
    authorization.validate()
    _check_oneauth(plan, tip, files, check_pr=False)
    progress.finish()
    return f"oneauth_common_pr: AndroidCommon {p['common']} ingested — PR {url}" + progress.result_note()
