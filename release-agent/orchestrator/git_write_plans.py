"""Complete transient plans and checked executors for the two finalization Git writers."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import PurePosixPath

from orchestrator.write_review import WriteOperation, WritePlan
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
        if not isinstance(value, Mapping) or not str(value.get(id_key, "")).isdigit():
            raise ValueError("Incomplete existing-PR result; no complete review possible")
        value = dict(value)
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


def integration_plan(context, args):
    injected = context.input("repos", MISSING)
    available = I.REPO_ORDER if injected is MISSING or not injected else list(injected)
    requested = list(getattr(args, "repos", None) or available)
    if (set(available) - set(I.REPO_ORDER) or set(requested) - set(available)
            or not requested):
        raise ValueError("Unknown, excluded, or empty repository selection")
    selected = [key for key in I.REPO_ORDER if key in requested]
    versions, pbi = I._versions(context), _pbi(context, args)
    repos, operations = [], []
    work_item = {"output": "pbi.id"} if pbi["mode"] == "create" else pbi["id"]
    for key in selected:
        version = versions.get(key)
        if not version:
            raise ValueError("Missing version: " + key)
        cfg = deepcopy(I.CONFIG[key])
        if cfg["tool"] not in ("gh", "ado"):
            raise ValueError("Unsupported PR provider")
        root = G.clean_repository(PR.repo_dir(cfg["dir"]))
        target = {"key": key, "root": str(root), "remote": G.remote_url(root),
                  "tool": cfg["tool"]}
        target.update({"gh_repo": cfg["gh_repo"]} if cfg["tool"] == "gh" else {"ado": cfg["ado"]})
        _remote_matches_provider(target)
        branches = I._branches(context, key, cfg, version)
        tips = {name: _tip(target, G.branch(name)) for name in sorted(set(branches.values()))}
        record = {"target": target, "tips": tips, "prs": []}
        for kind, head, base in (("freeze", branches["wr"], branches["r"]),
                                 ("integration", branches["ri"], branches["target"])):
            if head == base:
                raise ValueError("PR source and target branches must differ")
            existing = _find(target, head, base)
            guard = {"head": head, "base": base, "existing": existing}
            record["prs"].append(guard)
            op_target = {**target, "head": head, "base": base}
            expected_head = tips[head]
            if kind == "integration" and existing is None:
                ri = G.plan_ri(root, tips[head], tips[base], base)
                record["ri"] = ri
                if ri["commit"]:
                    operations.append(WriteOperation(
                        "integration.push", op_target, ri,
                        {"head_tip": tips[head], "base_tip": tips[base], "existing": existing}))
                    expected_head = ri["commit_id"]
            if pbi["mode"] == "create":
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
                 "labels": sorted(set(cfg.get("labels", []))),
                 "work_item": None if existing else work_item,
                 "kind": kind, "existing": existing},
                {"head_tip": expected_head, "base_tip": tips[base], "existing": existing}))
        repos.append(record)
    if pbi["mode"] == "create" and any(op.kind == "integration.create" for op in operations):
        operations.insert(0, WriteOperation(
            "pbi.create", {"org": pbi["org"], "project": pbi["project"]},
            {"title": pbi["title"], "type": "Product Backlog Item", "output": "pbi.id"}))
    plan = WritePlan("create-integration-prs",
                     {"repos": selected, "versions": {k: versions[k] for k in selected}, "pbi": pbi},
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


def execute_integration(authorization):
    plan = authorization.plan.as_dict()
    repos, completed, outputs, results = plan["preconditions"]["repos"], set(), {}, []

    def check(*, clean=True):
        authorization.validate()
        _check_integration(repos, completed, clean=clean)

    for operation in plan["operations"]:
        check()
        kind, target, content = operation["kind"], operation["target"], operation["content"]
        authorization.validate()
        if kind == "pbi.create":
            ok, wid, _url, detail = PR.create_pbi(target["org"], target["project"], content["title"])
            if not ok or not str(wid).isdigit() or int(wid) <= 0:
                raise ValueError("PBI creation failed or uncertain: " + str(detail))
            outputs["pbi.id"] = str(wid)
            results.append("PBI AB#" + str(wid))
            continue
        if kind == "integration.push":
            G.push_reviewed(target["root"], target["remote"], target["head"],
                            operation["preconditions"]["head_tip"], content,
                            lambda: check(clean=False))
            for repo in repos:
                if repo["target"]["key"] == target["key"]:
                    repo["tips"][target["head"]] = content["commit_id"]
            continue
        if kind == "integration.reuse":
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
            observed = _find(target, target["head"], target["base"])
            if (not observed or observed["url"] != url
                    or observed["title"] != content["title"] or observed["body"] != body
                    or (target["tool"] == "gh"
                        and not set(content["labels"]).issubset(observed["labels"]))):
                raise ValueError("Created PR identity/content uncertain; owner must inspect provider")
        else:
            raise ValueError("Unsupported reviewed operation: " + kind)
        completed.add((target["key"], target["head"], target["base"]))
        results.append(kind.rsplit(".", 1)[1] + " " + str(url))
    check()
    return "integ_prs: " + "; ".join(results)


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


def oneauth_plan(context, args):
    common, msal = O._versions(context)
    if not all(isinstance(v, str) and v and v == v.strip()
               and all(part.isascii() and part.isdigit() for part in v.split("."))
               for v in (common, msal)):
        raise ValueError("Exact final numeric Common and MSAL versions are required")
    repository = {"org": OA.ORG, "project": OA.PROJECT, "repository": OA.REPO}
    head, base = G.branch(OA.INGEST_BRANCH), G.branch(OA.TARGET_BRANCH)
    if head == base:
        raise ValueError("OneAuth source and target branches must differ")
    root = G.clean_repository(OA.review_repo_dir(repository, getattr(args, "repo_dir", None)))
    remote = G.remote_url(root)
    if G.remote_identity(remote) != G.remote_identity(OA.repository_remote_url(repository)):
        raise ValueError("OneAuth origin does not match the reviewed provider repository remote URI")
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
    content = G.plan_merge_edits(
        root, tips[head], tips[base], relative_paths,
        lambda merged: OA.apply_edits(merged, common, msal, paths=relative_paths), comment)
    if files != content["files_before"] or any(content[k] != counts[k] for k in ("ahead", "behind")):
        raise ValueError("OneAuth local Git objects and provider content/ancestry disagree")
    existing = _oa_find(repository, head, base)
    target = {**repository, "head": head, "base": base, "root": str(root), "remote": remote}
    preconditions = {"tips": tips, "paths": paths, "files": files, "existing": existing}
    operations = []
    expected_head = tips[head]
    if content["commit"]:
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
                     {"common": common, "msal": msal, "merge": content["merge"], "ancestry": dict(counts),
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
    p, pre = plan["parameters"], plan["preconditions"]
    repository, head = p["repository"], p["head"]
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
            observed_tip = G.push_reviewed(p["repo_dir"], p["remote"], head, tip, content,
                                          lambda: check(clean=False))
            if G.object_id(observed_tip) != content["commit_id"]:
                raise ValueError("OneAuth push returned an unreviewed commit; owner must inspect provider")
            tip, files = observed_tip, dict(content["final_files"])
        elif kind == "oneauth.create":
            pr = _existing(_read(OA.create_pr(head, p["base"], content["title"], content["body"],
                                            repository=repository), "OneAuth create PR"), "id")
            if pr is None or not pr.get("url"):
                raise ValueError("OneAuth PR result uncertain; owner must inspect provider")
            observed = _oa_find(repository, head, p["base"])
            if (not observed or observed["id"] != pr["id"] or observed["title"] != content["title"]
                    or observed["description"] != content["body"]):
                raise ValueError("Created OneAuth PR content uncertain; owner must inspect provider")
            url = pr["url"]
        elif kind == "oneauth.reuse":
            url = content["existing"].get("url") or str(content["existing"]["id"])
        else:
            raise ValueError("Unsupported reviewed operation: " + kind)
    authorization.validate()
    _check_oneauth(plan, tip, files, check_pr=False)
    return f"oneauth_common_pr: AndroidCommon {p['common']} ingested — PR {url}"
