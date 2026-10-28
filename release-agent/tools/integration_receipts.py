"""Read-only, exhaustive provider observations for integration-write recovery."""
from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
from urllib.parse import quote

from tools import prs as PR
from tools.executables import find_azure_cli
from tools.git_review import object_id


def _json(args):
    azure_cli = args[0] == "az"
    if azure_cli:
        executable = find_azure_cli()
        if executable is None:
            raise ValueError("az CLI not found")
        args = [executable, *args[1:]]
    # Azure CLI otherwise uses the Windows console encoding for non-ASCII titles.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(args, capture_output=True, env=env, timeout=120)
    if result.returncode:
        raise ValueError("Recovery provider read failed: " + result.stderr.decode("utf-8", "replace"))
    try:
        text = result.stdout.decode("utf-8", "strict")
    except UnicodeDecodeError:
        if not azure_cli or sys.platform != "win32":
            raise
        # Bundled Windows Azure CLI can ignore PYTHONIOENCODING and emit ANSI.
        native_encoding = locale.getencoding() if hasattr(locale, "getencoding") else "mbcs"
        text = result.stdout.decode(native_encoding, "strict")
    return json.loads(text)


def pbi_receipt(target, wid, title, started_at):
    from datetime import datetime

    if not str(wid).isdigit() or int(wid) <= 0:
        raise ValueError("Recovery requires an explicit positive existing PBI ID")
    wid = str(int(wid))
    org, project = target["org"], target["project"]
    value = _json(["az", "boards", "work-item", "show", "--org", org,
                   "--id", wid, "--output", "json"])
    fields = value.get("fields", {})
    if (str(value.get("id")) != wid or fields.get("System.Title") != title
            or fields.get("System.WorkItemType") != "Product Backlog Item"
            or fields.get("System.TeamProject") != project
            or datetime.fromisoformat(fields["System.CreatedDate"].replace("Z", "+00:00"))
            < datetime.fromisoformat(started_at.replace("Z", "+00:00"))):
        raise ValueError("Existing PBI identity/content predates or differs from this execution")
    escaped = lambda text: text.replace("'", "''")
    query = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '"
             + escaped(project) + "' AND [System.WorkItemType] = 'Product Backlog Item'"
             + " AND [System.Title] = '" + escaped(title) + "'")
    matches = _json(["az", "boards", "query", "--org", org, "--project", project,
                     "--wiql", query, "--output", "json"])
    if not isinstance(matches, list) or [str(item.get("id")) for item in matches] != [wid]:
        raise ValueError("PBI source is missing or ambiguous; no recovery authorization")
    url = value.get("url", "")
    # The show request is explicitly scoped to the reviewed organization.
    if not url or not isinstance(fields.get("System.CreatedDate"), str):
        raise ValueError("Incomplete PBI provider receipt")
    return {"id": wid, "org": org, "project": project, "title": title,
            "type": fields["System.WorkItemType"], "created_at": fields["System.CreatedDate"],
            "url": url}


def pr_receipt(target, *, number=None):
    """Read the reviewed PR number, or exhaustive source/base history for a new PR."""
    if number is not None:
        if not str(number).isdigit() or int(number) <= 0:
            raise ValueError("Reviewed PR number must be positive")
        number = int(number)
    head, base = target["head"], target["base"]
    if target["tool"] == "gh":
        host, slug = PR._gh_repo_parts(target["gh_repo"])
        args = (["gh", "api", f"repos/{slug}/pulls/{number}"] if number is not None else
                ["gh", "api", "--paginate", "--slurp",
                f"repos/{slug}/pulls?state=all&head={quote(slug.split('/')[0] + ':' + head, safe='')}"
                f"&base={quote(base, safe='')}&per_page=100"])
        if host:
            args += ["--hostname", host]
        if number is not None:
            value = _json(args)
            if not isinstance(value, dict) or value.get("number") != number:
                raise ValueError("Provider PR number differs from the approved reuse")
            values = [value]
        else:
            pages = _json(args)
            if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
                raise ValueError("Incomplete paginated PR history")
            values = [value for page in pages for value in page]
        if not values:
            return None
        if len(values) != 1:
            raise ValueError("Ambiguous PR history; no blind replay")
        value = values[0]
        merged = value["state"] == "closed" and bool(value.get("merged_at"))
        if (value["head"]["ref"] != head or value["base"]["ref"] != base
                or value["head"]["repo"]["full_name"].casefold() != slug.casefold()
                or value["base"]["repo"]["full_name"].casefold() != slug.casefold()
                or not (value["state"] == "open" and value.get("merged_at") is None or merged)
                or value.get("draft") is not False):
            raise ValueError("PR source/repository/state differs from the reviewed operation")
        receipt = {"number": value["number"], "url": value["html_url"], "title": value["title"],
                "body": value["body"], "labels": sorted(label["name"] for label in value["labels"]),
                "head": head, "base": base, "head_tip": object_id(value["head"]["sha"]),
                "state": "merged" if merged else "open", "repository": target["gh_repo"],
                "created_at": value["created_at"]}
        if merged:
            receipt.update(merge_tip=object_id(value["merge_commit_sha"]),
                           merged_at=value["merged_at"])
        return receipt
    a = target["ado"]
    if number is not None:
        value = _json(["az", "repos", "pr", "show", "--org", a["org"],
                       "--id", str(number), "--output", "json"])
        if not isinstance(value, dict) or value.get("pullRequestId") != number:
            raise ValueError("Provider PR number differs from the approved reuse")
        values = [value]
    else:
        values = _json(["az", "repos", "pr", "list", "--org", a["org"], "--project", a["project"],
                        "--repository", a["repository"], "--source-branch", head,
                        "--target-branch", base, "--status", "all", "--top", "1000", "--output", "json"])
    if not isinstance(values, list) or len(values) >= 1000:
        raise ValueError("Incomplete ADO PR history")
    if not values:
        return None
    if len(values) != 1:
        raise ValueError("Ambiguous ADO PR history")
    value = values[0]
    if (value["sourceRefName"] != "refs/heads/" + head
            or value["targetRefName"] != "refs/heads/" + base
            or value["repository"]["name"] != a["repository"]
            or value["repository"]["project"]["name"] != a["project"]
            or value["status"] != "active" or value.get("isDraft") is not False):
        raise ValueError("ADO PR source/repository/state differs from review")
    number = value["pullRequestId"]
    linked = _json(["az", "repos", "pr", "work-item", "list", "--org", a["org"],
                    "--id", str(number), "--output", "json"])
    return {"number": number,
            "url": f"{a['org'].rstrip('/')}/{a['project']}/_git/{a['repository']}/pullrequest/{number}",
            "title": value["title"], "body": value.get("description", ""),
            "head": head, "base": base,
            "head_tip": object_id(value["lastMergeSourceCommit"]["commitId"]),
            "state": "open", "repository": dict(a), "created_at": value["creationDate"],
            "work_items": sorted(str(item["id"]) for item in linked)}
