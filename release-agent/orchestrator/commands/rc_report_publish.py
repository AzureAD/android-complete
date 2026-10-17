"""Checked SharePoint publication for Phase-2 RC verification reports."""
from __future__ import annotations

import json
from hashlib import sha256
import shutil
import subprocess
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from orchestrator import cli_common as C, write_review as W
from steps.build_verify import rc_report as R
from steps.build_verify import rc_report_publish as S

PHASE = "build_verify"


def _graph_token():
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        raise ValueError("Azure CLI was not found on PATH (expected az or az.cmd)")
    raw = subprocess.check_output([
        az, "account", "get-access-token",
        "--resource", "https://graph.microsoft.com",
        "--output", "json",
    ], text=True, timeout=60)
    token = (json.loads(raw) or {}).get("accessToken")
    if not token:
        raise ValueError("Could not get a Microsoft Graph token (run `az login`)")
    return token


def _graph_json(method, url, *, body=None, content_type="application/json", token=None):
    data = None
    headers = {"Authorization": f"Bearer {token or _graph_token()}"}
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        headers["Content-Type"] = content_type
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=120) as response:  # nosec - trusted Microsoft Graph endpoint
        payload = response.read()
    return json.loads(payload.decode("utf-8")) if payload else {}


def _site_graph_path(site_url: str) -> str:
    parsed = urlparse(site_url)
    if not parsed.scheme.startswith("http") or not parsed.netloc or not parsed.path:
        raise ValueError("Invalid SharePoint site URL")
    return f"{parsed.netloc}:{parsed.path.rstrip('/')}"


def _drive_for_site(site_url: str, library: str, token: str) -> dict:
    site = _graph_json(
        "GET",
        f"https://graph.microsoft.com/v1.0/sites/{_site_graph_path(site_url)}",
        token=token,
    )
    drives = _graph_json(
        "GET",
        f"https://graph.microsoft.com/v1.0/sites/{quote(site['id'], safe=',-')}/drives",
        token=token,
    ).get("value") or []
    drive = next((item for item in drives if item.get("name") == library), None)
    if not drive:
        raise ValueError(f"SharePoint library {library!r} not found at {site_url}")
    return drive


def _validated_receipt(evidence, content):
    if not isinstance(evidence, dict) or not all(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ("drive_id", "item_id", "web_url", "report_link")):
        raise ValueError("Uploaded report requires provider identity and access URL")
    for key in ("web_url", "report_link"):
        parsed = urlparse(evidence[key])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Uploaded report requires an HTTPS provider URL")
    if (type(evidence.get("size")) is not int
            or evidence["size"] != len(content["html"].encode("utf-8"))):
        raise ValueError("Uploaded report size does not match expected HTML size")
    return dict(evidence)


def publish_report(target, content):
    """Upload immutable HTML; use its existing site access, without widening sharing."""
    folder = str(target["folder"]).strip("/")
    file_name = str(target["file_name"]).strip("/\\")
    html = content["html"].encode("utf-8")
    digest = sha256(html).hexdigest()
    if not file_name.endswith(f"-{digest}.html") or "/" in file_name or "\\" in file_name:
        raise ValueError("Report file name must be a single content-addressed .html file")
    path = quote(f"{folder}/{file_name}", safe="/")
    token = _graph_token()
    drive = _drive_for_site(target["site_url"], target["library"], token)
    item = _graph_json(
        "PUT",
        f"https://graph.microsoft.com/v1.0/drives/{quote(drive['id'], safe='')}/root:/{path}:/content",
        body=html,
        content_type="text/html; charset=utf-8",
        token=token,
    )
    if not isinstance(item, dict):
        raise ValueError("SharePoint upload did not return an item receipt")
    return _validated_receipt({
        "drive_id": drive["id"],
        "item_id": item.get("id"),
        "web_url": item.get("webUrl"),
        "report_link": item.get("webUrl"),
        "e_tag": item.get("eTag"),
        "size": item.get("size"),
    }, content)


def plan_rc_report_publish(orch):
    context = orch.context(PHASE, S.ID)
    subject, html, plain, model = R.rc_email(context)
    lineage = R.report_lineage(context, html)
    target = S.target_config()
    file_name = S.report_file_name(context, model, content_sha256=lineage["content_sha256"])
    return W.WritePlan(
        S.WRITE_COMMAND,
        {
            "site_url": target["site_url"],
            "library": target["library"],
            "folder": target["folder"],
            "file_name": file_name,
            "subject": subject,
        },
        (W.WriteOperation(
            "upload_sharepoint_html",
            {**target, "file_name": file_name},
            {"html": html, "plain": plain, "subject": subject},
        ),),
        preconditions=lineage,
    )


def _save_receipt(orch, args, evidence):
    result = orch.annotate_step(
        PHASE, S.ID, data=evidence,
        links=[{"name": "RC verification report", "url": evidence["report_link"]}],
        by="scout",
    )
    if not result.changed:
        raise ValueError(result.message)
    C.save_state(orch.state, args.runs_root, args.release)


def _record(orch, args, authorization, status, summary):
    result = orch.record_scout_step(
        PHASE, S.ID, status, summary, execution_id=authorization.execution_id)
    if result.kind not in ("ran", "reminder"):
        raise ValueError(result.message)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[rc_report_publish] {summary}", kind="step", log_text=summary)


def _require_current_plan(orch, authorization):
    if plan_rc_report_publish(orch).as_dict() != authorization.plan.as_dict():
        raise ValueError("RC report source/content changed; publish the current report")


def cmd_publish_rc_report(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    authorization = None
    try:
        if not args.execute and not getattr(args, "reserve", False):
            print(json.dumps(W.preview(orch, PHASE, S.ID, plan_rc_report_publish(orch)), indent=2))
            return 0
        W.apply_auto_approval(
            args,
            orch,
            PHASE,
            S.ID,
            lambda: plan_rc_report_publish(orch),
            approved_by="rc-report-publish-automation",
        )
        authorization = W.authorize(args, orch, PHASE, S.ID, lambda: plan_rc_report_publish(orch))
        if authorization.reserved_only:
            W.print_reservation(authorization)
            return 0
        operation = authorization.plan.operations[0]
        authorization.validate()
        _require_current_plan(orch, authorization)
        receipt = _validated_receipt(publish_report(operation.target, operation.content), operation.content)
        lineage = dict(authorization.plan.preconditions)
        evidence = S.publication_data(receipt, lineage, operation.target["file_name"])
        # Retain exact provider evidence before any later fence/completion can fail.
        _save_receipt(orch, args, evidence)
        authorization.validate()
        _require_current_plan(orch, authorization)
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        summary = f"rc_report_publish: {exc}"
        if authorization is not None and not authorization.reserved_only:
            summary += " Inspect SharePoint before retrying this owned execution."
            _record(orch, args, authorization, "attention", summary)
            print(json.dumps({"error": summary, "permission_to_execute": False}))
            return 2
        print(json.dumps({"error": summary, "permission_to_execute": False}))
        return 1
    summary = f"Published RC verification report: {evidence['report_link']}"
    _record(orch, args, authorization, "pass", summary)
    print(json.dumps({"published": True, **evidence}, indent=2))
    return 0


def register(sub):
    p = sub.add_parser(
        S.WRITE_COMMAND,
        help="Publish the RC verification HTML report to SharePoint (preview by default)",
    )
    p.add_argument("--release", required=True)
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.add_argument("--execute", action="store_true", help="Execute the approved upload")
    p.add_argument("--execution-id", help="Active reviewed reservation execution id")
    W.add_auto_approve_argument(
        p,
        help_text="RC-report automation only: compute/checkpoint the current report plan "
                  "without human approval, then upload through the normal fenced write path",
    )
    W.add_arguments(p)
    p.set_defaults(func=cmd_publish_rc_report)
