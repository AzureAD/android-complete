"""Direct Python notification delivery commands (`deliver-step`, auto-drain helper)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from orchestrator import cli_common as C, delivery as D
from orchestrator.agency_mcp import (
    AgencyAmbiguousDeliveryError,
    MCPCaller,
    call_mcp_tool,
    send_notification,
)
from orchestrator.commands.delivery_cmd import finish
from orchestrator.commands.step_action import prepare_step


def deliver_descriptor_items(
    runs_root: str,
    release: str,
    config: str,
    items: list[dict],
    *,
    executor: str = "engine-agency-mcp",
    mcp_caller: MCPCaller | None = None,
    as_of=None,
) -> list[dict]:
    """Durably offer, claim-before-IO, send via Agency MCP, and finalize notification descriptors."""
    if mcp_caller is None:
        mcp_caller = call_mcp_tool
    results: list[dict] = []
    if not items:
        return results
    st, orch = C.load_orch(runs_root, release, config, as_of)
    for item in items:
        D.offer(orch, item)
        try:
            claimed = D.claim(orch, item["id"], item["hash"], executor)
        except ValueError as exc:
            results.append({
                "status": "skipped",
                "notification_id": item["id"],
                "reason": str(exc),
            })
            continue
        C.save_state(st, runs_root, release)
        if claimed.get("status") == "already_sent":
            results.append({"status": "already_sent", "notification_id": item["id"]})
            continue
        tool_name = claimed["tool"]
        try:
            sent_info = send_notification(
                tool_name,
                claimed["payload"],
                owner_email=st.owner_email,
                mcp_caller=mcp_caller,
            )
        except AgencyAmbiguousDeliveryError as exc:
            err_text = str(exc).strip() or exc.__class__.__name__
            D.result(
                orch,
                item["id"],
                claimed["execution_id"],
                "uncertain",
                f"Agency MCP outcome uncertain after dispatch (fenced against resend): {err_text}",
            )
            C.save_state(st, runs_root, release)
            results.append({
                "status": "uncertain",
                "notification_id": item["id"],
                "tool": tool_name,
                "error": err_text,
            })
            continue
        except Exception as exc:
            err_text = str(exc).strip() or exc.__class__.__name__
            D.result(
                orch,
                item["id"],
                claimed["execution_id"],
                "not_sent",
                f"Agency MCP transport failed before confirmation: {err_text}",
            )
            C.save_state(st, runs_root, release)
            results.append({
                "status": "failed",
                "notification_id": item["id"],
                "tool": tool_name,
                "error": err_text,
            })
            continue

        via = f"agency:{sent_info['server']}.{sent_info['mcp_tool']}"
        resp_obj = sent_info.get("response") if isinstance(sent_info.get("response"), dict) else {}
        if tool_name in D.EXACT_PAYLOAD_RECEIPT_TOOLS:
            receipt = D.exact_payload_receipt(claimed, {"via": via, **resp_obj})
        else:
            receipt = {"via": via, **resp_obj}
        evidence = f"Delivered silently via {via}"
        D.result(orch, item["id"], claimed["execution_id"], "sent", evidence, receipt=receipt)
        C.save_state(st, runs_root, release)
        finish(orch, item["id"])
        D.prune_progress(orch)
        C.save_state(st, runs_root, release)
        results.append({
            "status": "done",
            "notification_id": item["id"],
            "tool": tool_name,
            "via": via,
            "target": claimed.get("target"),
        })
    return results


def deliver_one_step(
    runs_root: str,
    release: str,
    config: str,
    phase: str,
    step: str,
    *,
    approved: bool = False,
    param: list[str] | None = None,
    executor: str = "engine-agency-mcp",
    mcp_caller: MCPCaller | None = None,
) -> dict:
    """Prepare, claim, send via Agency MCP, and finalize a single notification step."""
    if mcp_caller is None:
        mcp_caller = call_mcp_tool
    st, orch = C.load_orch(runs_root, release, config)
    definition = orch.workflow.step(phase, step)
    if definition is None:
        raise ValueError(f"Unknown workflow step: {phase}.{step}")
    handler = orch.handler(phase, step)
    if not handler.notification:
        raise ValueError(f"Step {phase}.{step} is not a notification step")
    if definition.requires_approval and not approved:
        return {
            "status": "needs_approval",
            "release": release,
            "phase": phase,
            "step": step,
            "requires_approval": True,
            "error": (
                f"Step {phase}.{step} requires explicit human approval (`requires_approval: true`). "
                "Preview first, obtain owner confirmation, then pass --approved."
            ),
        }

    record_before = st.get_step(phase, step)
    if record_before.status in ("done", "skipped"):
        return {
            "status": "already_done",
            "release": release,
            "phase": phase,
            "step": step,
            "step_status": record_before.status,
            "note": record_before.note,
        }

    prep_args = SimpleNamespace(
        runs_root=runs_root,
        release=release,
        config=config,
        phase=phase,
        step=step,
        param=param,
        execution_id=None,
        reserve=False,
        owner_force_rerun=False,
        owner_force_reason="",
        confirmed_by=st.owner_email,
        prior_writer_stopped=False,
    )
    out = prepare_step(prep_args, st, orch)
    if out.get("state_changed"):
        C.save_state(st, runs_root, release)

    if out.get("kind") == "done" and out.get("no_delivery_required"):
        C.save_state(st, runs_root, release)
        C.elog(runs_root, release).log(
            "step_recorded", phase=phase, step=step, status="pass",
            driver=out.get("summary") or "no delivery required",
        )
        return {
            "status": "done",
            "release": release,
            "phase": phase,
            "step": step,
            "no_delivery_required": True,
            "summary": out.get("summary") or out.get("note") or "No delivery required",
        }

    if out.get("kind") == "blocked":
        C.save_state(st, runs_root, release)
        return {
            "status": "blocked",
            "release": release,
            "phase": phase,
            "step": step,
            "summary": out.get("summary") or out.get("reason") or "Step blocked",
        }

    items = out.get("notifications") or []
    if not items:
        return {
            "status": "needs_gather",
            "release": release,
            "phase": phase,
            "step": step,
            "summary": out.get("summary") or "Step requires gather inputs before sending",
        }

    item = items[0]
    if item["tool"] == "m_send_teams_message":
        companion_deliveries = []
        if len(items) > 1:
            companion_deliveries = deliver_descriptor_items(
                runs_root,
                release,
                config,
                items[1:],
                executor=executor,
                mcp_caller=mcp_caller,
            )
            st, orch = C.load_orch(runs_root, release, config)
        offered = D.offer(orch, item)
        C.save_state(st, runs_root, release)
        nid = offered["id"]
        nhash = offered["hash"]
        msg_body = (offered.get("payload") or {}).get("message") or ""
        return {
            "status": "awaiting_scout_bot",
            "release": release,
            "phase": phase,
            "step": step,
            "tool": "m_send_teams_message",
            "notification_id": nid,
            **({"companion_deliveries": companion_deliveries} if companion_deliveries else {}),
            "scout_bot_delivery": {
                "action_required": True,
                "notification_id": nid,
                "hash": nhash,
                "tool": "m_send_teams_message",
                "message": msg_body,
                "claim_command": (
                    f"python -m orchestrator.cli --scheduled notification claim --release {release} "
                    f"--id \"{nid}\" --hash \"{nhash}\" --executor {executor} --json"
                ),
                "ack_sent_command": (
                    f"python -m orchestrator.cli --scheduled notification result --release {release} "
                    f"--id \"{nid}\" --execution-id <execution_id> --outcome sent "
                    f"--evidence \"Scout bot DM sent via m_send_teams_message\""
                ),
            },
            "summary": "Companion email sent (if any); claim and send Scout bot DM via m_send_teams_message.",
        }

    D.offer(orch, item)
    claimed = D.claim(orch, item["id"], item["hash"], executor)
    C.save_state(st, runs_root, release)
    if claimed.get("status") == "already_sent":
        return {
            "status": "already_sent",
            "release": release,
            "phase": phase,
            "step": step,
            "notification_id": item["id"],
        }

    tool_name = claimed["tool"]
    connector = "agency_mcp"
    try:
        sent_info = send_notification(
            tool_name,
            claimed["payload"],
            owner_email=st.owner_email,
            mcp_caller=mcp_caller,
        )
    except AgencyAmbiguousDeliveryError as exc:
        err_text = str(exc).strip() or exc.__class__.__name__
        D.result(
            orch,
            item["id"],
            claimed["execution_id"],
            "uncertain",
            f"Agency MCP outcome uncertain after dispatch (fenced against resend): {err_text}",
        )
        C.save_state(st, runs_root, release)
        C.elog(runs_root, release).log(
            "step_failed", phase=phase, step=step, tool=connector, detail=f"uncertain: {err_text}"
        )
        return {
            "status": "uncertain",
            "release": release,
            "phase": phase,
            "step": step,
            "tool": tool_name,
            "notification_id": item["id"],
            "error": (
                f"Agency MCP outcome uncertain after dispatch ({err_text}); "
                "delivery is fenced against automatic resend until owner review."
            ),
        }
    except Exception as exc:
        err_text = str(exc).strip() or exc.__class__.__name__
        D.result(
            orch,
            item["id"],
            claimed["execution_id"],
            "not_sent",
            f"Agency MCP transport failed: {err_text}",
        )
        act = orch.fail_step(phase, step, err_text, tool=connector)
        C.save_state(st, runs_root, release)
        C.elog(runs_root, release).log(
            "step_failed", phase=phase, step=step, tool=connector, detail=err_text
        )
        return {
            "status": "failed",
            "release": release,
            "phase": phase,
            "step": step,
            "tool": tool_name,
            "error": act.message,
        }

    via = f"agency:{sent_info['server']}.{sent_info['mcp_tool']}"
    resp_obj = sent_info.get("response") if isinstance(sent_info.get("response"), dict) else {}
    if tool_name in D.EXACT_PAYLOAD_RECEIPT_TOOLS:
        receipt = D.exact_payload_receipt(claimed, {"via": via, **resp_obj})
    else:
        receipt = {"via": via, **resp_obj}

    evidence = f"Delivered silently via {via}"
    D.result(orch, item["id"], claimed["execution_id"], "sent", evidence, receipt=receipt)
    C.save_state(st, runs_root, release)
    finish(orch, item["id"])
    if D.prune_progress(orch):
        pass
    C.save_state(st, runs_root, release)
    companion_deliveries = []
    if len(items) > 1:
        companion_deliveries = deliver_descriptor_items(
            runs_root,
            release,
            config,
            items[1:],
            executor=executor,
            mcp_caller=mcp_caller,
        )
    C.elog(runs_root, release).log(
        "notification_delivered",
        phase=phase,
        step=step,
        tool=tool_name,
        via=via,
        target=claimed.get("target"),
    )
    return {
        "status": "done",
        "release": release,
        "phase": phase,
        "step": step,
        "tool": tool_name,
        "via": via,
        "target": claimed.get("target"),
        "notification_id": item["id"],
        **({"companion_deliveries": companion_deliveries} if companion_deliveries else {}),
        "summary": out.get("note") or out.get("summary") or evidence,
    }


def drain_auto_notifications(
    runs_root: str,
    release: str,
    config: str,
    *,
    executor: str = "engine-agency-mcp",
    mcp_caller: MCPCaller | None = None,
) -> list[dict]:
    """Deliver all currently eligible `requires_approval: false` notification steps."""
    if mcp_caller is None:
        mcp_caller = call_mcp_tool
    delivered: list[dict] = []
    attempted: set[str] = set()
    while True:
        st, orch = C.load_orch(runs_root, release, config)
        if not orch.gate.checklist().get("signed"):
            break
        selection = orch.scheduling()
        if not selection.frontier:
            break
        progressed = False
        for sid in list(selection.scout_pending):
            step_def = selection.frontier.step(sid)
            if step_def is None or step_def.key in attempted or step_def.requires_approval:
                continue
            record = selection.step(step_def.phase_id, sid)
            if record.status != "pending" or record.reserved:
                continue
            handler = orch.handler(step_def.phase_id, sid)
            if not handler.notification:
                continue
            if "scout_teams_relay" in (step_def.required_tools or ()):
                # Scout bot (`m_send_teams_message`) steps run via `pending_host_work`
                # so Scout can synthesize AI failure diagnosis first and call `m_send_teams_message`.
                continue
            attempted.add(step_def.key)
            res = deliver_one_step(
                runs_root,
                release,
                config,
                step_def.phase_id,
                sid,
                approved=False,
                executor=executor,
                mcp_caller=mcp_caller,
            )
            delivered.append(res)
            if res.get("status") in ("done", "failed", "blocked"):
                progressed = True
        if not progressed:
            break
    return delivered


def cmd_deliver_step(args) -> int:
    if getattr(args, "as_of", None):
        msg = "deliver-step requires the trusted current clock; --as-of is preview-only"
        if getattr(args, "json", False):
            print(json.dumps({"status": "failed", "error": msg}))
        else:
            print(msg)
        return 1
    try:
        res = deliver_one_step(
            args.runs_root,
            args.release,
            args.config,
            args.phase,
            args.step,
            approved=bool(getattr(args, "approved", False)),
            param=getattr(args, "param", None),
            executor=getattr(args, "executor", None) or "engine-agency-mcp",
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "failed", "error": str(exc)}))
        else:
            print(f"deliver-step failed: {exc}")
        return 1

    ok = res.get("status") in ("done", "already_done", "already_sent")
    if getattr(args, "json", False):
        _, orch = C.load_orch(args.runs_root, args.release, args.config)
        print(json.dumps({**res, **C.guided_followup(orch)}))
    else:
        if ok:
            print(f"  [OK] {args.phase}.{args.step}: {res.get('summary') or res.get('status')}")
        else:
            print(f"  [{res.get('status', 'failed').upper()}] {args.phase}.{args.step}: "
                  f"{res.get('error') or res.get('summary')}")
    return 0 if ok else 1


def register(sub) -> None:
    p = sub.add_parser(
        "deliver-step",
        help="Send and record a notification step directly in Python via Agency MCP",
    )
    p.add_argument("--release", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--approved", action="store_true",
                   help="Confirm owner approval when delivering a requires_approval=true step")
    p.add_argument("--param", action="append", default=None, metavar="KEY=VALUE")
    p.add_argument("--executor", default="engine-agency-mcp")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_deliver_step)
