"""Generic dispatcher for co-located step modules (`step-action`).

This is the ONE command the skill calls to resolve any migrated step into its
uniform outcome. It replaces the old per-step `prepare-X` commands: instead of a
bespoke `prepare-notice`, `prepare-flight-reminder`, … the skill runs

    python -m orchestrator.cli step-action --release <id> --phase preflight --step notice

The command resolves the workflow's validated handler, calls its bound `build(context)`
(validating `--param k=v` against its module-owned build parameter model),
and prints the outcome as JSON. The skill reads `kind` and reacts uniformly:

    done         → already complete, nothing to run.
    blocked      → surface `reason` to the owner.
    needs_human  → show `prompt` (attestation or reminder).
    needs_skill  → run `tool` with `payload`, then `record-step --step <record_as>`.

Adding a scout step is now: write ONE module under steps/<phase>/ (auto-discovered)
— no CLI command, no registry, no skill-reference edits.
"""
from __future__ import annotations
from dataclasses import MISSING, asdict
import json as _json

from orchestrator import cli_common as C, delivery as D
from orchestrator import mocks as mocks_mod
from orchestrator import knowledge as kb
from orchestrator.handlers import HandlerCatalog
from orchestrator.outcomes import Blocked, Done, InProgress, as_dict
from orchestrator.step_context import thaw
from orchestrator.transitions import TransitionIntent
from orchestrator.workflow import WorkflowDefinition
import steps

AUTO_APPROVED_WRITE_COMMANDS = {
    "launch-localization",
    "create-integration-prs",
    "create-oneauth-common-pr",
    "create-payload-wiki",
    "start-release-signoff",
    "start-upload-whats-new",
    "start-upload-alpha",
}


def _apply_overrides(out: dict, mockable: dict, spec: dict) -> None:
    """Apply local-test payload overrides a step DECLARES via its MOCKABLE spec.

    Each MOCKABLE entry maps a mock-file key → a payload rewrite:
      sets         payload field to overwrite
      as: "list"   coerce a scalar to [scalar]
      aliases      value shortcuts (e.g. {"me": SELF_CHAT_ID})
      tag_subject  prefix payload.subject with "[TEST → me]"
    Keys in `spec` that aren't declared (and aren't the engine-level reserved
    keys) are surfaced as `unknown_overrides` so typos are visible."""
    pl = out.get("payload") or {}
    reserved = {"outcome", "note", "reason"}          # handled by the engine, not here
    applied = {}
    for key, rule in (mockable or {}).items():
        if rule.get("kind") != "payload":            # input → build(); post → check-localization
            continue
        if key not in spec:
            continue
        val = spec[key]
        if "aliases" in rule and not isinstance(val, list) and val in rule["aliases"]:
            val = rule["aliases"][val]
        if rule.get("as") == "list" and not isinstance(val, list):
            val = [val]
        pl[rule["sets"]] = val
        if rule.get("tag_subject") and pl.get("subject") and not pl["subject"].startswith("[TEST"):
            pl["subject"] = f"[TEST → me] {pl['subject']}"
        applied[key] = val
    if applied:
        out["test_redirect"] = applied
        out["note"] = f"[test-redirect] {out.get('note', '')}".rstrip()
    unknown = [k for k in spec if k not in (mockable or {}) and k not in reserved]
    if unknown:
        out["unknown_overrides"] = unknown


def _parse_params(pairs) -> dict:
    """Turn ['variant=update', 'x=y'] into {'variant': 'update', 'x': 'y'}."""
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise ValueError(f"--param must be KEY=VALUE, got: {item!r}")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k or k in out:
            raise ValueError(f"--param requires a non-empty, unique name: {k!r}")
        out[k] = v
    return out


def prepare_step(args, st, orch):
    handler = orch.handler(args.phase, args.step)
    force_reopen = bool(getattr(args, "owner_force_rerun", False))
    forced_reopen = False
    if force_reopen:
        reopen_reason = (getattr(args, "owner_force_reason", "") or "").strip()
        reopen_result = orch.rerun_task(
            args.phase, args.step, reason=reopen_reason,
            confirmed_by=getattr(args, "confirmed_by", None) or st.owner_email,
            parameters=_parse_params(getattr(args, "param", None)), cli=True,
            prior_writer_stopped=bool(getattr(args, "prior_writer_stopped", False)),
        )
        if not reopen_result.changed:
            raise ValueError(reopen_result.message)
        forced_reopen = reopen_result.changed
    from orchestrator.write_review import continuation
    resumed = continuation(orch, args.phase, args.step)
    if resumed is not None:
        payload = {**as_dict(resumed), "release": args.release, "phase": args.phase,
                   "step": args.step, "permission_to_execute": False, "reservable": False}
        if forced_reopen:
            payload["state_changed"] = True
        return payload

    # Agent steps run IN-PROCESS inside the engine's `next` (they perform the real
    # deterministic action). Executing their build() here would run that action a
    # second time, out of band — refuse and point to `next`.
    definition = handler.definition
    if definition.kind.value == "auto":
        raise ValueError("Agent steps run in-process via next, not step-action")
    intent = orch.step_action_intent(args.phase, args.step)
    refreshing = intent == TransitionIntent.REFRESH
    pairs = getattr(args, "param", None)
    params = handler.parse_parameters(values=_parse_params(pairs), cli=True) if pairs and not force_reopen else None
    spec = mocks_mod.load_mocks().get(f"{args.phase}.{args.step}") or {}
    outcome = orch.step_action_guard(args.phase, args.step)
    built = outcome is None
    if outcome is None:
        permit = orch.authorize_outcome(
            intent, args.phase, args.step,
            execution_id=getattr(args, "execution_id", None),
        )
        outcome = handler.build(orch.context(
            args.phase, args.step, permit=permit, parameters=params, inputs=spec))
    transition = None
    if built and isinstance(outcome, (Done, Blocked, InProgress)):
        if definition.is_human_action and isinstance(outcome, Blocked):
            orch.validate_outcome_permit(permit)
            orch.apply_evidence(permit, outcome)
        else:
            transition = orch.apply_outcome(permit, outcome)
    elif built:
        orch.validate_outcome_permit(permit)
        orch.apply_evidence(permit, outcome)
    out = as_dict(outcome)
    out["phase"] = args.phase
    out["step"] = definition.id
    out["release"] = args.release
    if refreshing:
        out["refresh"] = True
    if transition or (built and outcome.updates):
        out["state_changed"] = True
    if out["kind"] == "done" and handler.notification:
        out["no_delivery_required"] = True

    # Local-test payload overrides: a mocks.local.yaml entry may set knobs the step
    # DECLARES via its MOCKABLE spec (e.g. `send_to` on notice) — keeps the send
    # real but redirects it. See `mock-spec` for what each step exposes.
    if out.get("kind") == "needs_skill" and spec:
        _apply_overrides(out, handler.mockable_spec(), spec)
    if out.get("kind") == "needs_skill":
        out["reservable"] = orch.supports_step_reservation(outcome)
        if (out.get("outbound") and handler.notification
                and out["tool"] not in D.TRANSPORTS):
            raise ValueError("Notification transport has no delivery contract")
        if out["tool"] in D.TRANSPORTS and out.get("outbound"):
            if out.get("record_as") != args.step:
                raise ValueError("Notification record_as must match the requested owning step")
            metadata = out.get("notification") or {}
            record = st.get_step(args.phase, args.step)
            scope = {"kind": "refresh" if refreshing else "step",
                     "phase": args.phase, "step": args.step,
                     "generation": record.invalidated_at or "initial",
                     "release_matches": {"ccd": st.ccd, "owner_email": st.owner_email},
                     "state_matches": metadata.get("state_matches", [])}
            for key in ("not_before", "expires_at"):
                if key in metadata:
                    scope[key] = metadata[key]
            completion = {
                "note": out.get("note", ""),
                **metadata.get("completion", {}),
                "kind": "step_result" if refreshing else "step",
                "record_as": out["record_as"],
                "refresh": refreshing,
            }
            if out["payload"].get("_automation"):
                completion["automation"] = out["payload"]["_automation"]
            payload = {k: v for k, v in out["payload"].items()
                       if not k.startswith("_") and k not in ("followup_command", "links")}
            if out["payload"].get("_mentions"):
                payload["mentions"] = D.chat_mentions(out["payload"]["_mentions"])
            generation = D.fingerprint(
                record.invalidated_at or "initial")[:12]
            item = D.descriptor(
                st,
                f"step:{args.phase}.{args.step}:"
                f"{metadata.get('checkpoint', 'once')}:{generation}",
                scope, out["tool"], payload, completion)
            notif_items = [item] if D.available(orch, item) else []
            also_email = out["payload"].get("_also_email")
            if isinstance(also_email, dict) and also_email.get("to"):
                email_item = D.descriptor(
                    st,
                    f"step:{args.phase}.{args.step}:"
                    f"{metadata.get('checkpoint', 'once')}:{generation}:email",
                    scope, "workiq_send_email", also_email, {})
                if D.available(orch, email_item):
                    notif_items.append(email_item)
            out["notifications"] = notif_items
            out["mcp_arguments"] = payload
            out["ready_to_execute"] = not definition.requires_approval
            out["complete_command"] = (
                f"record-step --release {args.release} --phase {args.phase} "
                f"--step {args.step} --status pass --detail \"Sent via {out['tool']}\""
            )
            out["reservable"] = False
            if out["notifications"]:
                n0 = out["notifications"][0]
                if not definition.requires_approval:
                    out["execute_command"] = (
                        f"deliver-step --release {args.release} --phase {args.phase} "
                        f"--step {args.step} --json"
                    )
                out["execution_protocol"] = (
                    {
                        "contract": "two_step",
                        "preauthorized_by_user": False,
                        "step_1_send": (
                            f"Preview `mcp_arguments` in chat and ask the user via `m_ask_user` before calling "
                            f"`deliver-step --release {args.release} --phase {args.phase} --step {args.step} --approved --json` "
                            f"(or `{n0['tool']}` + `record-step`)."
                        ),
                        "step_2_record": out["complete_command"],
                    }
                    if definition.requires_approval
                    else {
                        "contract": "python_direct_delivery",
                        "preauthorized_by_user": True,
                        "execute_command": out["execute_command"],
                        "step_1_send": (
                            f"PYTHON AUTO-DELIVERY (`requires_approval: false`): Run `{out['execute_command']}` "
                            f"in PowerShell right now. Python delivers and records this notification automatically "
                            f"via Agency MCP — NEVER call `{n0['tool']}` or `m_ask_user`!"
                        ),
                    }
                )
            if getattr(args, "reserve", False):
                raise ValueError("Use notification prepare/claim with the approved hash, not step-action --reserve")
        elif out.get("outbound"):
            out["permission_to_execute"] = False
            if definition.write_command:
                out["reservable"] = False
                out["review_command"] = definition.write_command
                if definition.write_command in AUTO_APPROVED_WRITE_COMMANDS:
                    out["execute_command"] = (
                        f"{definition.write_command} --release {args.release} "
                        "--execute --auto-approve --executor interactive"
                    )
                    out["note"] = (
                        out.get("note", "") + " Run `execute_command` directly (`--execute --auto-approve --executor interactive`)."
                    ).strip()
                else:
                    out["preview_command"] = f"{definition.write_command} --release {args.release}"
                    out["note"] = (
                        out.get("note", "") + " Preview the checked write command and approve its exact "
                        "--review-hash with --approved-by. A generic reservation cannot authorize writes."
                    ).strip()
                if getattr(args, "reserve", False):
                    if definition.write_command in AUTO_APPROVED_WRITE_COMMANDS:
                        raise ValueError(
                            f"Use {definition.write_command} --execute --auto-approve --executor <automation-id>; "
                            "step-action --reserve cannot authorize provider writes")
                    raise ValueError(f"Use {definition.write_command} --execute with its exact reviewed hash")
                return out
            if getattr(args, "reserve", False):
                outcome = orch.reserve_step(
                    args.phase, args.step, outcome, getattr(args, "executor", None))
                if outcome.kind != "needs_skill":
                    return as_dict(outcome)
                out["execution_id"] = orch.step_execution(args.phase, args.step)["id"]
                out["permission_to_execute"] = True
        elif getattr(args, "reserve", False):
            raise ValueError("Read-only external work does not require a reservation")
    if forced_reopen:
        out["state_changed"] = True
        out["owner_forced_rerun"] = True
    definition = orch.workflow.step(args.phase, args.step)
    if definition is not None:
        out["requires_approval"] = definition.requires_approval
        out["depends_on"] = list(definition.depends_on)
        out["required_tools"] = list(definition.required_tools)
        if not definition.is_gate:
            out["manual_escape_commands"] = {
                "complete_manually": (
                    f"complete-manually --release {args.release} --phase {args.phase} "
                    f"--step {args.step} --note \"Completed manually outside Scout\""
                ),
                "skip": (
                    f"skip --release {args.release} --phase {args.phase} "
                    f"--step {args.step} --reason \"Handled outside orchestrator\""
                ),
            }
    if out.get("kind") == "blocked":
        from orchestrator.remediation import step_remediation
        out["remediation"] = step_remediation(
            args.release, args.phase, args.step, out.get("reason", ""),
        )
    if isinstance(out.get("payload"), dict) and out["payload"].get("_gather"):
        out["gather_protocol"] = {
            "note": (
                "This is a 2-hop gather-then-decide task (`status: waiting` means Scout must "
                "perform `_gather` right now, NOT that the step failed!). Execute `_gather` "
                "(e.g. Playwright browser navigate+snapshot, Kusto query, or MCP read), then "
                "run `payload.followup_command` with the gathered data."
            ),
            "gather": out["payload"]["_gather"],
            "followup_command": out["payload"].get("followup_command"),
        }
    return out


def cmd_step_action(args):
    try:
        st, orch = C.load_orch(args.runs_root, args.release, args.config)
        out = prepare_step(args, st, orch)
        if out.get("execution_id") or out.get("state_changed"):
            C.save_state(st, args.runs_root, args.release)
        out.update(C.guided_followup(orch))
        out["status"] = {
            "done": "done", "blocked": "failed", "in_progress": "waiting",
            "needs_human": "needs_input", "needs_skill": "waiting",
        }[out["kind"]]
        print(_json.dumps(out))
        return 0
    except ValueError as exc:
        print(_json.dumps({"status": "failed", "error": str(exc), "permission_to_execute": False}))
        return 1


def cmd_task(args):
    state, orch = C.load_orch(args.runs_root, args.release, args.config)
    handler = orch.handler(args.phase, args.step)
    definition = handler.definition
    rerun = getattr(args, "rerun", False) or args.cmd == "rerun"
    if rerun:
        if args.retry:
            raise ValueError("--retry reconciles the same attempt; do not combine it with --rerun")
        transition = orch.rerun_task(
            args.phase, args.step, reason=args.reason or "",
            confirmed_by=args.confirmed_by or state.owner_email,
            parameters=_parse_params(args.param), cli=True,
            prior_writer_stopped=args.prior_writer_stopped,
        )
        if not transition.changed:
            raise ValueError(transition.message)
        state.touch_owner_interaction()
        C.save_state(state, args.runs_root, args.release)
        C.elog(args.runs_root, args.release).log(
            "task_rerun", phase=args.phase, step=args.step, driver=args.reason)
        args.param = []
    if definition.kind.value != "auto":
        return cmd_step_action(args)
    parameters = (handler.parse_parameters(
        handler.task_parameter_role, values=_parse_params(args.param), cli=True) if args.param else None)
    action = orch.run_task(args.phase, args.step, retry=args.retry, parameters=parameters)
    C.save_state(state, args.runs_root, args.release)
    C.log_actions(C.elog(args.runs_root, args.release), [action], state=state)
    status = ("done" if action.kind == "ran" else "needs_input"
              if definition.implementation.value == "dummy" else "waiting"
              if action.kind in ("waiting", "scheduled", "in_flight") else "failed")
    print(_json.dumps({
        "status": status, "summary": action.message,
        "phase": args.phase, "step": args.step,
        "attempt_id": (state.task_attempts.get(definition.key) or {}).get("id"),
        "links": state.get_step(args.phase, args.step).links,
        **C.guided_followup(orch),
    }))
    return 1 if status == "failed" else 0


def _classify(step: dict) -> str:
    return {
        "approval_gate": "gate",
        "external": "scout",
        "attestation": "attest",
        "human_action": "reminder",
        "auto": "agent",
    }[step["kind"]]


def _catalog(config_path: str) -> dict:
    """Every step across every phase, with its mock-ability. Engine-level
    (outcome: done|blocked) applies to any non-gate step; payload overrides only
    exist where a migrated step declares MOCKABLE."""
    import yaml
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    catalog = HandlerCatalog.compile(WorkflowDefinition.compile(cfg), steps.get_step)
    out = {}
    for key, handler in catalog.handler_by_key.items():
        step = handler.definition
        kind = _classify(step.raw)
        out[key] = {
            "phase": step.phase_id, "name": step.name, "kind": kind,
            "implementation": step.implementation.value,
            "outcome_mockable": kind != "gate", "overrides": handler.mockable_spec(),
        }
    return out


def _readiness_auto_items(config_path: str):
    """Readiness AUTO items (build_access, mcp_servers, oncall_now, …) — mockable
    via `readiness.<id>: {outcome: pass|fail}` to clear/fail the entry gate offline."""
    import os
    import yaml
    rp = os.path.join(os.path.dirname(config_path), "readiness.yaml")
    if not os.path.exists(rp):
        return []
    with open(rp, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    out = []
    for it in cfg.get("items", []):
        if it.get("verify") == "auto":
            out.append({"id": it["id"], "source": it.get("source", "python")})
    return out


def cmd_mock_spec(args):
    """List what each step (across ALL phases) exposes to mocks.local.yaml."""
    config_path = getattr(args, "config", None) or C.DEFAULT_CONFIG
    catalog = _catalog(config_path)
    readiness = _readiness_auto_items(config_path)
    if getattr(args, "json", False):
        print(_json.dumps({"steps": catalog, "readiness": readiness}))
        return 0

    print("mocks.local.yaml — what you can put under each \"<phase>.<step>\":\n")
    print("  Engine-level (works for EVERY non-gate step, any phase):")
    print("    outcome: done            # mark complete, skip its real work")
    print("    outcome: blocked         # hold for the owner  (+ reason: \"...\")\n")
    print("  Per-step properties (declared by the step; input = feeds real logic):")
    any_ov = False
    for key, e in catalog.items():
        for name, rule in (e.get("overrides") or {}).items():
            any_ov = True
            kind = rule.get("kind", "payload")
            print(f"    {key}: {{ {name}: <value> }}   [{kind}] — {rule.get('desc', '')}")
    if not any_ov:
        print("    (none declared yet)")

    if readiness:
        print("\n  Readiness entry-gate AUTO checks (real ADO/config/MCP — mock to clear offline):")
        for it in readiness:
            print(f"    readiness.{it['id']}: {{ outcome: pass|fail }}   [{it['source']}]")

    print("\n  Every step — exactly what you can mock (🚦 gate = NOT mockable):")
    cur = None
    for key, e in catalog.items():
        if e["phase"] != cur:
            cur = e["phase"]
            print(f"    [{cur}]")
        if e["kind"] == "gate":
            knobs = "🚦 not mockable (gate needs a real human decision)"
        else:
            parts = ["outcome: done|blocked"] + list((e.get("overrides") or {}).keys())
            knobs = "  ·  ".join(parts)
        print(f"      {key:30} [{e['kind']:6}] → {knobs}")
    return 0


def cmd_step_info(args):
    """Answer a user's question about a step from the knowledge base — what it does,
    where to look, how to resolve it, links, FAQs. Consult this before answering
    step questions so the info is accurate (not guessed)."""
    k = kb.get_knowledge(args.phase, args.step)
    if getattr(args, "json", False):
        handler = _handler_info(args.config, args.phase, args.step)
        print(_json.dumps({"phase": args.phase, "step": args.step, "knowledge": k,
                           "owner_controls": handler}))
        return 0
    controls = _handler_info(args.config, args.phase, args.step)
    if not k:
        print(f"No knowledge entry yet for {args.phase}.{args.step}. "
              f"Add KNOWLEDGE = {{...}} to steps/{args.phase}/{args.step}.py (or config/knowledge.yaml for YAML-only steps).")
    else:
        print(kb.render_knowledge(args.phase, args.step, k))
    print("Requires approval: " + ("true (human confirmation before execution)" if controls["approval_required"]
                                   else "false (runs automatically)"))
    print("Owner controls: rerun" + (", skip, complete-manually" if controls["skip"]
                                    else "; approval gates still require their genuine decision"))
    print("Public task inputs: " + (", ".join(item["name"] for item in controls["parameters"]) or "(none)"))
    return 0


def _handler_info(config_path, phase, step):
    import yaml
    with open(config_path, "r", encoding="utf-8") as fh:
        workflow = WorkflowDefinition.compile(yaml.safe_load(fh))
    handler = HandlerCatalog.compile(workflow, steps.get_step).get(phase, step)
    schema = handler.parameters[handler.task_parameter_role]
    return {
        "rerun": True, "skip": not handler.definition.is_gate,
        "complete_manually": not handler.definition.is_gate,
        "approval_required": handler.definition.requires_approval,
        "depends_on": list(handler.definition.depends_on),
        "required_tools": list(handler.definition.required_tools),
        "parameters": [
            {"name": item.name, "type": str(item.annotation),
             "required": item.default is MISSING,
             **({"default": thaw(item.default)} if item.default is not MISSING else {})}
            for item in schema.fields
        ],
        "input_role": handler.task_parameter_role.value,
        "commands": {"rerun": "rerun", "skip": "skip", "complete_manually": "complete-manually"},
        "manual_outputs": "Owner-attested context only; provider bindings use the owning adoption/readback command.",
    }

def cmd_task_history(args):
    state = C.load_state(args.runs_root, args.release)
    key = state.key(args.phase, args.step)
    attempt = state.task_attempts.get(key)
    if not attempt and not any(
            key in phase["step_keys"] for phase in (state.workflow_revision or {}).get("phases", [])):
        raise ValueError(f"No such task in this release: {key}")
    record = state.get_step(args.phase, args.step)
    if args.details:
        payload = {"attempt": attempt, "step": asdict(record)}
    else:
        def brief(value):
            if not value:
                return None
            result = value.get("result")
            return {
                **{name: item for name, item in value.items() if name not in ("history", "result")},
                "result": ({name: result[name] for name in ("verification", "machine_verified", "links")}
                           if result else None),
            }
        payload = {
            "attempt": brief(attempt),
            "step": {"status": record.status, "note": record.note, "links": record.links},
            "history": [
                {"attempt": brief(entry.get("attempt")), "at": entry["at"],
                 "status": entry["step"]["status"], "note": entry["step"]["note"],
                 "links": entry["step"]["links"], "writer_stopped": entry["writer_stopped"],
                 "unsettled_notifications": entry["notifications"],
                 "execution_id": (entry["step"].get("execution") or {}).get("id")}
                for entry in (attempt or {}).get("history", [])
            ],
        }
    print(_json.dumps({"release": args.release, "phase": args.phase, "step": args.step, **payload,
                       "permission_to_execute": False, "permission_to_send": False}))
    return 0


def cmd_gate_info(args):
    """Answer a user's question about an ENTRY-GATE readiness item from the knowledge
    base — what it verifies, who resolves it, where to look, how to satisfy/clear it,
    links, FAQs. Gate items live under the `readiness.<id>` key. Consult this before
    answering gate questions so the info is accurate (not guessed)."""
    k = kb.get_knowledge("readiness", args.item)
    if getattr(args, "json", False):
        print(_json.dumps({"item": args.item, "knowledge": k}))
        return 0
    if not k:
        print(f"No knowledge entry yet for readiness.{args.item}. "
              f"Add one to config/knowledge.yaml under 'readiness.{args.item}'.")
        return 0
    print(kb.render_knowledge("readiness", args.item, k))
    return 0


def register(sub):
    task = sub.add_parser("task", aliases=["rerun"], help="Run one task; rerun starts a fresh, audited attempt")
    task.add_argument("--release", required=True)
    task.add_argument("--phase", required=True)
    task.add_argument("--step", required=True)
    task.add_argument("--param", action="append", default=[])
    task.add_argument("--execution-id")
    task.add_argument("--retry", action="store_true", help="Explicitly retry this task after repair")
    task.add_argument("--rerun", action="store_true", help="Start a fresh attempt, retaining prior inputs/results")
    task.add_argument("--reason", help="Owner's reason for a new attempt (required for rerun)")
    task.add_argument("--confirmed-by", help="Confirming release owner; defaults to the configured owner")
    task.add_argument("--prior-writer-stopped", action="store_true",
                      help="Owner confirms the previous runner stopped; uncertain results remain in history")
    task.set_defaults(func=cmd_task)

    history = sub.add_parser("task-history", help="Inspect owner requests and preserved attempt results")
    history.add_argument("--release", required=True)
    history.add_argument("--phase", required=True)
    history.add_argument("--step", required=True)
    history.add_argument("--details", action="store_true", help="Include original outputs and receipts")
    history.set_defaults(func=cmd_task_history)

    sp = sub.add_parser(
        "step-action",
        help="Resolve a migrated step into its uniform outcome JSON "
             "(done|blocked|needs_human|needs_skill)")
    sp.add_argument("--release", required=True)
    sp.add_argument("--phase", default="preflight")
    sp.add_argument("--step", required=True)
    sp.add_argument("--param", action="append", default=[],
                    help="Optional KEY=VALUE passed to the step's build() "
                         "(e.g. --param variant=update). Repeatable.")
    sp.add_argument("--reserve", action="store_true", help="Reserve standard record-step work after approval")
    sp.add_argument("--executor", help="Automation/session identifier for the reservation")
    sp.add_argument("--execution-id", help="Exact active execution ID when polling reserved work")
    sp.add_argument("--owner-force-rerun", action="store_true",
                    help="Owner override: force-reopen the step before resolving action output")
    sp.add_argument("--owner-force-reason", default="",
                    help="Audit reason recorded when --owner-force-rerun is used")
    sp.add_argument("--confirmed-by")
    sp.add_argument("--prior-writer-stopped", action="store_true")
    sp.set_defaults(func=cmd_step_action)

    ms = sub.add_parser(
        "mock-spec",
        help="List what every step (all phases) exposes to mocks.local.yaml")
    ms.add_argument("--json", action="store_true", help="Emit the catalog as JSON")
    ms.set_defaults(func=cmd_mock_spec)

    si = sub.add_parser(
        "step-info",
        help="Show a step's knowledge (what it does, where to look, how to resolve, links, FAQs)")
    si.add_argument("--phase", default="preflight")
    si.add_argument("--step", required=True)
    si.add_argument("--json", action="store_true", help="Emit the knowledge as JSON")
    si.set_defaults(func=cmd_step_info)

    gi = sub.add_parser(
        "gate-info",
        help="Show an entry-gate readiness item's knowledge (what it verifies, who resolves it, how to clear it, links, FAQs)")
    gi.add_argument("--item", required=True, help="Readiness item id, e.g. build_access, oncall_now, yubikey")
    gi.add_argument("--json", action="store_true", help="Emit the knowledge as JSON")
    gi.set_defaults(func=cmd_gate_info)
