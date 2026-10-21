"""Single-attempt Scout handoff for immutable RC report publication.

No provider calls are made here. --upload-request-file may select workiq_upload_file
only for an existing, resolved item whose downloaded bytes already match the report.
Its JSON contains version:1, route:"existing_item", local_path, item_url, resolve and
download (fresh orchestrator.m365 observations). For NEW filenames use route:
"browser_new_file", local_path and library_context:{site_url,library,folder,folder_url,
owner_confirmed:true,confirmed_by:<release owner>,confirmed_at:<actual UTC time>}.
These confirmation fields must record actual owner confirmation, never an agent default.
Alternatively library_context.resolve may contain a fresh exact folder resolution
observation instead of an owner confirmation. Browser selectors must come from the
real page snapshot; only the frozen local file may be uploaded, once, without overwrite.

--receipt-file is a separate, read-only result action with --execution-id. Its JSON
contains the returned receipt_binding plus item_url, resolve, optional requested
metadata and download observations.
It cannot issue another upload, including after interrupted or stale completion.
Resolution supplies canonical drive/item IDs. Missing metadata requests an exact
structured fetch via the discovered MCP or native WorkIQ CLI route.
Downloads may be native text/base64 or
structured content with explicit encoding; only the exact frozen bytes are accepted.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlparse

from orchestrator import cli_common as C, m365, revision, structured_fetch as F, write_review as W
from orchestrator.step_context import thaw
from steps.build_verify import rc_report as R
from steps.build_verify import rc_report_publish as S

PHASE = "build_verify"
HANDOFF = "publication_handoff"


class PublicationReadRequired(ValueError):
    def __init__(self, key, request):
        super().__init__(f"Publication requires the exact {key} connector read")
        self.key, self.request = key, request


def _read_json(path):
    with open(path, encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Publication request/receipt must be a JSON object")
    return value


def _digest(content):
    return sha256(content).hexdigest()


def _url(value):
    if not isinstance(value, str):
        raise ValueError("Publication requires an exact HTTPS SharePoint URL")
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.port):
        raise ValueError("Publication requires an exact HTTPS SharePoint URL")
    return parsed.hostname.casefold(), unquote(parsed.path).rstrip("/")


def _destination_url(target, item_url):
    host, site = _url(target["site_url"])
    actual_host, path = _url(item_url)
    library = target["library"]
    # The standard Documents library's SharePoint URL uses Shared Documents.
    libraries = {library, "Shared Documents"} if library == "Documents" else {library}
    expected = {
        f"{site}/{name}/{target['folder'].strip('/')}/{target['file_name']}"
        for name in libraries
    }
    if actual_host != host or path not in expected:
        raise ValueError("Provider destination differs from the reviewed SharePoint target")
    return item_url


def _provider_item(result, target, item_url):
    if not isinstance(result, dict):
        raise ValueError("SharePoint resolution requires provider identity and item metadata")
    item = result.get("item") or result.get("driveItem") or result
    if not isinstance(item, dict):
        raise ValueError("SharePoint resolution requires item metadata")
    parent = item.get("parentReference") or {}
    if not isinstance(parent, dict):
        raise ValueError("SharePoint parent identity must be structured provider metadata")
    drive_id = result.get("driveId") or parent.get("driveId")
    item_id = result.get("itemId") or item.get("id")
    web_url = item.get("webUrl") or result.get("webUrl")
    if not all(isinstance(value, str) and value.strip()
               for value in (drive_id, item_id, web_url)):
        raise ValueError("Uploaded report requires provider identity and access URL")
    if ((parent.get("driveId") and parent["driveId"] != drive_id)
            or (item.get("id") and item["id"] != item_id)
            or (result.get("webUrl") and _url(result["webUrl"]) != _url(web_url))):
        raise ValueError("Conflicting SharePoint provider identity")
    _destination_url(target, web_url)
    if _url(web_url) != _url(item_url) or item.get("name") != target["file_name"]:
        raise ValueError("Resolved item differs from the exact immutable report filename")
    if item.get("folder") is not None or type(item.get("size")) is not int:
        raise ValueError("Provider receipt must describe a file with its exact size")
    return {
        "drive_id": drive_id, "item_id": item_id, "web_url": web_url,
        "report_link": web_url, "size": item["size"], "e_tag": item.get("eTag"),
    }


def _download_bytes(result, *, digest, size):
    if isinstance(result, str):
        content = result.encode("utf-8")
        if len(content) == size and _digest(content) == digest:
            return content
        if re.fullmatch(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?", result):
            decoded = base64.b64decode(result, validate=True)
            if len(decoded) == size and _digest(decoded) == digest:
                return decoded
        raise ValueError("Native text/base64 download differs from the frozen report bytes")
    if not isinstance(result, dict):
        raise ValueError("Report receipt requires complete downloaded provider bytes")
    if isinstance(result.get("contentBase64"), str):
        return base64.b64decode(result["contentBase64"], validate=True)
    content = result.get("content")
    encoding = result.get("encoding")
    if isinstance(content, str) and encoding == "base64":
        return base64.b64decode(content, validate=True)
    if isinstance(content, str) and encoding in ("text", "utf-8"):
        return content.encode("utf-8")
    raise ValueError("Download response lacks explicit text/base64 content; owner review required")


def _observed_item(value, target, owner, digest, size, *, not_before=None):
    item_url = _destination_url(target, value.get("item_url"))
    resolve_args = {"url": item_url}
    resolved = m365.validate_observation(
        value.get("resolve"), tool="workiq_resolve_m365_link",
        arguments=resolve_args, owner=owner)
    if not isinstance(resolved, dict):
        raise ValueError("Report link resolution must return canonical provider identifiers")
    item = resolved.get("item") or resolved.get("driveItem") or resolved
    parent = item.get("parentReference") or {} if isinstance(item, dict) else {}
    if not isinstance(parent, dict):
        raise ValueError("Malformed resolved provider parent")
    drive_id = resolved.get("driveId") or parent.get("driveId")
    item_id = resolved.get("itemId") or (item.get("id") if isinstance(item, dict) else None)
    if not all(isinstance(identifier, str) and identifier.strip() for identifier in (drive_id, item_id)):
        raise ValueError("Report link resolution requires canonical driveId and itemId")
    metadata_args = {"entityUrls": [
        f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}?"
        "$select=id,name,size,webUrl,eTag,parentReference,file,folder",
    ]}
    if value.get("metadata") is not None:
        metadata = m365.validate_observation(
            value["metadata"], tool="workiq.fetch", arguments=metadata_args, owner=owner)
        if not isinstance(metadata, dict):
            raise ValueError("Report metadata must be a structured DriveItem")
        provider = {"driveId": drive_id, "itemId": item_id, "item": metadata}
    elif isinstance(item, dict) and all(key in item for key in ("name", "size", "webUrl")):
        provider = resolved
    else:
        raise PublicationReadRequired("metadata", F.request(metadata_args["entityUrls"], owner))
    receipt = _provider_item(provider, target, item_url)
    download_args = {
        "fileId": f"{receipt['drive_id']},{receipt['item_id']}",
        "maxBytes": size + 1,
    }
    if value.get("download") is None:
        raise PublicationReadRequired("download", m365.observation_request(
            "workiq_download_file", download_args, owner))
    downloaded = m365.validate_observation(
        value.get("download"), tool="workiq_download_file",
        arguments=download_args, owner=owner, allow_text=True)
    if not_before:
        issued = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
        for key in ("resolve", "metadata", "download"):
            if key not in value:
                continue
            observed = datetime.fromisoformat(value[key]["observed_at"].replace("Z", "+00:00"))
            if observed < issued:
                raise ValueError("Receipt readback predates the owned publication handoff")
    content = _download_bytes(downloaded, digest=digest, size=size)
    if receipt["size"] != size or len(content) != size or _digest(content) != digest:
        raise ValueError("Provider report size/SHA-256 differs from the reviewed HTML")
    return receipt


def _upload_action(request, operation, owner):
    if type(request.get("version")) is not int or request["version"] != 1:
        raise ValueError("Upload request requires version 1")
    html = operation.content["html"].encode("utf-8")
    local_path = request.get("local_path")
    if (not isinstance(local_path, str) or not Path(local_path).is_absolute()
            or Path(local_path).name != operation.target["file_name"]):
        raise ValueError("Upload requires an absolute local path with the exact immutable filename")
    if Path(local_path).read_bytes() != html:
        raise ValueError("Local report bytes differ from the exact reviewed HTML")
    if request.get("route") == "browser_new_file":
        return _browser_action(request, operation.target, owner, local_path, html)
    if request.get("route") != "existing_item":
        raise ValueError("Upload route must be existing_item or browser_new_file; no native create tool is released")
    receipt = _observed_item(request, operation.target, owner, _digest(html), len(html))
    return {
        "route": "existing_item",
        "tool": "workiq_upload_file",
        "arguments": {"localPath": local_path, "driveId": receipt["drive_id"],
                      "itemId": receipt["item_id"]},
        "account": owner,
        "existing_item": receipt,
        "preconditions": {"content_sha256": _digest(html), "size": len(html),
                          "replace_exact_existing_item_only": True,
                          "recheck_local_bytes_immediately_before_tool": True},
    }


def _browser_action(request, target, owner, local_path, html):
    context = request.get("library_context")
    if not isinstance(context, dict) or any(
            context.get(key) != target[key] for key in ("site_url", "library", "folder")):
        raise ValueError("Browser upload requires the exact reviewed site/library/folder context")
    folder_url = context.get("folder_url")
    _url(folder_url)
    item_url = f"{folder_url.rstrip('/')}/{target['file_name']}"
    _destination_url(target, item_url)
    folder_identity = None
    if context.get("resolve") is not None:
        resolved = m365.validate_observation(
            context["resolve"], tool="workiq_resolve_m365_link",
            arguments={"url": folder_url}, owner=owner)
        if not isinstance(resolved, dict):
            raise ValueError("Browser context requires structured provider folder metadata")
        item = resolved.get("item") or resolved.get("driveItem") or resolved
        parent = (item.get("parentReference") or {}) if isinstance(item, dict) else {}
        if not isinstance(parent, dict):
            raise ValueError("Browser folder requires structured parent identity")
        drive_id = resolved.get("driveId") or parent.get("driveId")
        item_id = resolved.get("itemId") or (item.get("id") if isinstance(item, dict) else None)
        if (not isinstance(item, dict) or not isinstance(item.get("folder"), dict)
                or _url(item.get("webUrl") or resolved.get("webUrl")) != _url(folder_url)
                or item.get("name") != target["folder"].strip("/").split("/")[-1]
                or not all(isinstance(value, str) and value.strip()
                           for value in (drive_id, item_id))
                or parent.get("driveId") not in (None, drive_id)
                or item.get("id") not in (None, item_id)):
            raise ValueError("Browser context must resolve the exact provider folder, not a file")
        folder_identity = {"drive_id": drive_id, "item_id": item_id}
    else:
        if context.get("owner_confirmed") is not True or context.get("confirmed_by") != owner:
            raise ValueError("Browser context requires fresh folder observation or explicit release-owner confirmation")
        confirmed = datetime.fromisoformat(str(context.get("confirmed_at", "")).replace("Z", "+00:00"))
        if confirmed.tzinfo is None or confirmed.utcoffset() is None:
            raise ValueError("Browser owner confirmation requires an offset-aware timestamp")
        age = (datetime.now(timezone.utc) - confirmed).total_seconds()
        if age < 0 or age > 900:
            raise ValueError("Browser owner confirmation is stale or future-dated")
    return {
        "route": "browser_new_file",
        "tool": "playwright-browser_file_upload",
        "arguments": {"paths": [local_path]},
        "account": owner,
        "item_url": item_url,
        "library_context": deepcopy(context),
        "folder_identity": folder_identity,
        "navigate": {"tool": "playwright-browser_navigate", "arguments": {"url": folder_url}},
        "snapshot": {"tool": "playwright-browser_snapshot", "arguments": {}},
        "preconditions": {
            "content_sha256": _digest(html), "size": len(html),
            "recheck_local_bytes_immediately_before_tool": True,
            "create_exact_new_filename_only": True, "never_overwrite": True,
        },
        "instructions": [
            "Navigate only to the frozen site/library/folder URL; confirm the signed-in account is the frozen release owner.",
            "Inspect the real page snapshot and verify the exact library and folder before selecting any upload control. Never invent selectors or use a different destination.",
            "If the exact filename already exists, do NOT upload or replace it. Read its exact native resolve/download proof and submit the same execution's receipt only if every byte matches.",
            "Recheck the local file's exact absolute path, filename, UTF-8 byte size and SHA-256 against this authorization immediately before the one permitted file upload.",
            "Using only controls observed in the real snapshot, select Upload then Files and open its file chooser. Upload ONLY the single frozen local HTML path with the listed file-upload arguments, at most once.",
            "Cancel any Replace/Overwrite prompt. Never widen sharing, rename a file, create a different filename, or navigate to another library/folder.",
            "Wait for provider acknowledgement, then gather fresh native workiq_resolve_m365_link and workiq_download_file observations for the exact item URL and submit the execution-bound receipt.",
            "For any unknown outcome, interruption or mismatch: hold this execution, inspect/read back the exact target, and use result-only recovery. Never repeat the upload or request another permission.",
        ],
    }


def plan_rc_report_publish(orch, action=None):
    context = orch.context(PHASE, S.ID)
    subject, html, plain, model = R.rc_email(context)
    lineage = R.report_lineage(context, html)
    target = S.target_config()
    file_name = S.report_file_name(context, model, content_sha256=lineage["content_sha256"])
    parameters = {**target, "file_name": file_name, "subject": subject}
    if action is not None:
        parameters["upload_handoff"] = deepcopy(action)
    return W.WritePlan(
        S.WRITE_COMMAND,
        parameters,
        (W.WriteOperation(
            "upload_sharepoint_html", {**target, "file_name": file_name},
            {"html": html, "plain": plain, "subject": subject},
        ),),
        preconditions=lineage,
    )


def _save_data(orch, args, data, *, links=None):
    step = orch.state.get_step(PHASE, S.ID)
    if any(step.data.get(key) != value for key, value in data.items()) or (
            links is not None and step.links != links):
        result = orch.annotate_step(PHASE, S.ID, data=data, links=links, by="scout")
        if not result.changed:
            raise ValueError(result.message)
    C.save_state(orch.state, args.runs_root, args.release)


def _record(orch, args, execution_id, status, summary):
    result = orch.record_scout_step(PHASE, S.ID, status, summary, execution_id=execution_id)
    if result.kind not in ("ran", "reminder"):
        raise ValueError(result.message)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[rc_report_publish] {summary}", kind="step", log_text=summary)


def _require_current_plan(orch, authorization):
    action = authorization.plan.parameters.get("upload_handoff")
    if plan_rc_report_publish(orch, action).as_dict() != authorization.plan.as_dict():
        raise ValueError("RC report source/content changed; publish the current report")


def _handoff(orch, authorization, action):
    plan = authorization.plan
    operation = plan.operations[0]
    execution = orch.step_execution(PHASE, S.ID)
    binding = {
        "version": 1, "release": orch.state.release_id,
        "execution_id": authorization.execution_id,
        "review_hash": execution["write_review"]["hash"],
        "approved_by": execution["write_review"]["approved_by"],
        "executor": execution["owner"],
        "workflow_revision": revision.revision_id(orch.state.workflow_revision),
        "account": orch.state.owner_email,
        "target": thaw(operation.target),
        "content_sha256": plan.preconditions["content_sha256"],
        "size": len(operation.content["html"].encode("utf-8")),
        "lineage": thaw(plan.preconditions),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "action": deepcopy(action),
    }
    binding["authorization_id"] = revision.digest(binding)
    return binding


def _receipt_binding(handoff):
    return {key: deepcopy(handoff[key]) for key in (
        "version", "release", "execution_id", "review_hash", "authorization_id",
        "approved_by", "executor", "account", "target", "content_sha256", "size",
    )}


def _result(args, orch):
    if not args.execution_id:
        raise ValueError("Receipt recovery requires the exact --execution-id")
    if args.execute or args.auto_approve or args.upload_request_file:
        raise ValueError("--receipt-file is a result-only action; do not combine it with upload authorization")
    step = orch.state.get_step(PHASE, S.ID)
    handoff = step.data.get(HANDOFF)
    execution = step.execution or {}
    closed = step.data.get("last_write_review") or {}
    if not isinstance(handoff, dict) or handoff.get("execution_id") != args.execution_id:
        raise ValueError("Receipt does not own an issued publication handoff")
    unsigned = {key: value for key, value in handoff.items() if key != "authorization_id"}
    if (revision.digest(unsigned) != handoff.get("authorization_id")
            or handoff.get("release") != orch.state.release_id
            or handoff.get("workflow_revision") != revision.revision_id(orch.state.workflow_revision)):
        raise ValueError("Publication authorization identity changed; owner review required")
    if execution:
        if (execution.get("id") != args.execution_id or step.status not in ("in_flight", "blocked")
                or execution.get("owner") != handoff["executor"]
                or execution.get("write_review") != {
                    "hash": handoff["review_hash"], "approved_by": handoff["approved_by"]}):
            raise ValueError("Receipt requires the exact already-started reviewed execution")
    elif not (step.status == "done" and closed.get("execution_id") == args.execution_id
              and closed.get("hash") == handoff["review_hash"]
              and closed.get("approved_by") == handoff["approved_by"]):
        raise ValueError("Receipt recovery cannot authorize or reopen publication")
    value = _read_json(args.receipt_file)
    if value.get("receipt_binding") != _receipt_binding(handoff):
        raise ValueError("Receipt binding differs from the frozen publication authorization")
    if not execution and revision.digest(value) == step.data.get("publication_receipt_hash"):
        print(json.dumps({"published": True, "already_recorded": True,
                          "permission_to_execute": False, "report_link": step.data["report_link"]}))
        return 0
    if not execution:
        raise ValueError("Completed publication cannot accept a different receipt")
    receipt = _observed_item(
        value, handoff["target"], handoff["account"], handoff["content_sha256"],
        handoff["size"], not_before=handoff["issued_at"])
    action = handoff["action"]
    if action and action.get("route") == "browser_new_file":
        folder_identity = action.get("folder_identity")
        if (_url(receipt["web_url"]) != _url(action["item_url"])
                or folder_identity and receipt["drive_id"] != folder_identity["drive_id"]):
            raise ValueError("Browser receipt destination differs from the frozen folder and filename")
    elif action and receipt != action["existing_item"]:
        # eTag can change after replacing the same bytes, but target identity cannot.
        if any(receipt[key] != action["existing_item"][key] for key in (
                "drive_id", "item_id", "web_url", "report_link", "size")):
            raise ValueError("Receipt provider identity differs from the single permitted upload")
    previous = step.data.get("item_id")
    if (step.data.get("publication_receipt_execution") == args.execution_id and previous
            and (previous != receipt["item_id"] or step.data.get("drive_id") != receipt["drive_id"])):
        raise ValueError("A different provider item is already recorded for this execution")
    evidence = S.publication_data(receipt, handoff["lineage"], handoff["target"]["file_name"])
    evidence.update(publication_receipt_execution=args.execution_id,
                    publication_receipt_hash=revision.digest(value))
    # Persist real provider identity before any current-source or completion fence.
    _save_data(orch, args, evidence,
               links=[{"name": "RC verification report", "url": receipt["report_link"]}])
    try:
        plan = plan_rc_report_publish(orch, handoff["action"])
        if (thaw(plan.preconditions) != handoff["lineage"]
                or thaw(plan.operations[0].target) != handoff["target"]
                or W.review_hash(orch, PHASE, S.ID, plan) != handoff["review_hash"]):
            raise ValueError("RC report source/content changed; receipt retained for the original report")
        _record(orch, args, args.execution_id, "pass",
                f"Published RC verification report: {receipt['report_link']}")
    except (ValueError, TypeError, KeyError) as exc:
        recovery_error = None
        try:
            _record(orch, args, args.execution_id, "attention", str(exc))
        except ValueError as record_error:
            recovery_error = str(record_error)
        print(json.dumps({"error": str(exc), "receipt_recorded": True,
                          "permission_to_execute": False,
                          **({"recovery_error": recovery_error} if recovery_error else {})}))
        return 2
    print(json.dumps({"published": True, "permission_to_execute": False, **evidence}, indent=2))
    return 0


def cmd_publish_rc_report(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    authorization = None
    try:
        if args.receipt_file:
            return _result(args, orch)
        if not args.execute:
            plan = plan_rc_report_publish(orch)
            if args.upload_request_file:
                action = _upload_action(_read_json(args.upload_request_file),
                                        plan.operations[0], orch.state.owner_email)
                plan = plan_rc_report_publish(orch, action)
            preview = W.preview(orch, PHASE, S.ID, plan)
            preview["publication_capability"] = S.PUBLICATION_CAPABILITY
            print(json.dumps(preview, indent=2))
            return 0
        plan = plan_rc_report_publish(orch)
        owner = orch.state.owner_email
        if not isinstance(owner, str) or "@" not in owner:
            raise ValueError("Publication requires the exact release owner's connector account")
        request = _read_json(args.upload_request_file) if args.upload_request_file else None
        action = (_upload_action(request, plan.operations[0], owner)
                  if request is not None else None)
        planner = lambda: plan_rc_report_publish(orch, action)
        authorization = W.authorize(
            args, orch, PHASE, S.ID, planner,
            auto_approved_by="rc-report-publish-automation")
        authorization.validate()
        _require_current_plan(orch, authorization)
        if action and _upload_action(request, authorization.plan.operations[0], owner) != action:
            raise ValueError("Checked upload target/context changed during authorization; no permission issued")
        handoff = _handoff(orch, authorization, action)
        _save_data(orch, args, {HANDOFF: handoff})
        authorization.validate()
        _require_current_plan(orch, authorization)
        if not action:
            note = ("No checked local-file/browser folder context was supplied. New immutable "
                    "SharePoint filenames have no released native create uploader. Owner-assisted upload "
                    "of the exact reviewed HTML only; inspect SharePoint before any recovery. "
                    "Do not replace another item, widen sharing, or replay --execute.")
            _record(orch, args, authorization.execution_id, "attention", note)
        item_url = (action["item_url"] if action and action.get("route") == "browser_new_file"
                    else action["existing_item"]["web_url"] if action else "<exact item_url>")
        file_id = (f"{action['existing_item']['drive_id']},{action['existing_item']['item_id']}"
                   if action and action.get("route") == "existing_item" else "<resolved driveId,itemId>")
        print(json.dumps({
            "kind": "connector_action" if action else "owner_assisted_upload",
            "permission_to_execute": bool(action),
            "execution_id": authorization.execution_id,
            "receipt_binding": _receipt_binding(handoff),
            "action": action,
            "html": authorization.plan.operations[0].content["html"],
            "limitations": S.PUBLICATION_CAPABILITY,
            "followup": (f"{S.WRITE_COMMAND} --release {args.release} "
                         f"--execution-id {authorization.execution_id} --receipt-file <receipt.json>"),
            "receipt_reads": [
                m365.observation_request("workiq_resolve_m365_link", {"url": item_url}, owner),
                {"tool": "workiq_download_file", "account": owner, "read_only": True,
                 "arguments": {"fileId": file_id, "maxBytes": handoff["size"] + 1}},
            ],
            "recovery": "Result retries are read-only. Never repeat an uncertain upload.",
        }, indent=2))
        return 0 if action else 2
    except PublicationReadRequired as exc:
        print(json.dumps({
            "kind": "needs-connector-evidence", "completed": False,
            "permission_to_execute": False, "observation_key": exc.key,
            "requests": [exc.request],
            "next": "Collect this exact structured observation and rerun the same result/preview action; never replay an upload.",
        }, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        summary = f"rc_report_publish: {exc}"
        recovery_error = None
        if authorization is not None:
            try:
                _record(orch, args, authorization.execution_id, "attention", summary)
            except ValueError as record_error:
                recovery_error = str(record_error)
        print(json.dumps({"error": summary, "permission_to_execute": False,
                          **({"recovery_error": recovery_error} if recovery_error else {})}))
        return 2 if authorization is not None else 1


def register(sub):
    p = sub.add_parser(S.WRITE_COMMAND,
                       help="Review an immutable RC report; authorize one checked Scout handoff")
    p.add_argument("--release", required=True)
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.add_argument("--execute", action="store_true", help="Authorize one exact connector action, not a provider call")
    p.add_argument("--execution-id", help="Owning reservation or result execution id")
    p.add_argument("--upload-request-file", help="Existing-item proof or checked browser folder context and exact local HTML path JSON")
    p.add_argument("--receipt-file", help="Result-only frozen receipt binding and fresh provider readbacks JSON")
    W.add_auto_approve_argument(
        p, help_text="Checkpoint the current report review before one checked Scout handoff")
    W.add_arguments(p)
    p.set_defaults(func=cmd_publish_rc_report)
