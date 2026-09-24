"""Checked SharePoint publication for Phase-2 RC verification reports."""
from __future__ import annotations

import json
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


def publish_report(target, content):
    """Upload the HTML report and return provider evidence."""
    token = _graph_token()
    drive = _drive_for_site(target["site_url"], target["library"], token)
    folder = str(target["folder"]).strip("/")
    file_name = str(target["file_name"]).strip("/\\")
    if not file_name.endswith(".html") or "/" in file_name or "\\" in file_name:
        raise ValueError("Report file name must be a single .html file")
    path = quote(f"{folder}/{file_name}", safe="/")
    html = content["html"].encode("utf-8")
    item = _graph_json(
        "PUT",
        f"https://graph.microsoft.com/v1.0/drives/{quote(drive['id'], safe='')}/root:/{path}:/content",
        body=html,
        content_type="text/html; charset=utf-8",
        token=token,
    )
    if int(item.get("size") or -1) != len(html):
        raise ValueError("Uploaded report size does not match expected HTML size")
    link = _graph_json(
        "POST",
        f"https://graph.microsoft.com/v1.0/drives/{quote(drive['id'], safe='')}/items/{quote(item['id'], safe='')}/createLink",
        body={"type": "view", "scope": "organization"},
        token=token,
    )
    return {
        "drive_id": drive["id"],
        "item_id": item["id"],
        "web_url": item.get("webUrl"),
        "report_link": ((link.get("link") or {}).get("webUrl") or item.get("webUrl")),
        "size": item.get("size"),
    }


def plan_rc_report_publish(orch):
    subject, html, plain, model = R.rc_email(orch.context(PHASE, S.ID))
    target = S.target_config()
    file_name = S.report_file_name(orch.context(PHASE, S.ID), model)
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
    )


def _record(orch, args, authorization, status, summary, evidence=None):
    orch.record_scout_step(PHASE, S.ID, status, summary, execution_id=authorization.execution_id)
    if evidence:
        orch.annotate_step(
            PHASE,
            S.ID,
            data=evidence,
            links=[{"name": "RC verification report", "url": evidence["report_link"]}],
            by="scout",
        )
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[rc_report_publish] {summary}", kind="step", log_text=summary)


def cmd_publish_rc_report(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    authorization = None
    try:
        if not args.execute and not getattr(args, "reserve", False):
            print(json.dumps(W.preview(orch, PHASE, S.ID, lambda: plan_rc_report_publish(orch)), indent=2))
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
        evidence = publish_report(operation.target, operation.content)
        authorization.validate()
    except Exception as exc:  # noqa: BLE001
        summary = f"rc_report_publish: {exc}"
        if authorization is not None and not authorization.reserved_only:
            summary += " Inspect SharePoint before retrying this owned execution."
            _record(orch, args, authorization, "attention", summary)
            print(json.dumps({"error": summary, "permission_to_execute": False}))
            return 2
        print(json.dumps({"error": summary, "permission_to_execute": False}))
        return 1
    summary = f"Published RC verification report: {evidence['report_link']}"
    _record(orch, args, authorization, "pass", summary, evidence)
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
