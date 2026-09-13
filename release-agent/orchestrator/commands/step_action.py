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
import json as _json

from orchestrator import cli_common as C, delivery as D
from orchestrator import mocks as mocks_mod
from orchestrator import knowledge as kb
from orchestrator.handlers import HandlerCatalog
from orchestrator.outcomes import Blocked, Done, InProgress, as_dict
from orchestrator.transitions import TransitionIntent
from orchestrator.workflow import WorkflowDefinition
import steps


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

    # Agent steps run IN-PROCESS inside the engine's `next` (they perform the real
    # deterministic action). Executing their build() here would run that action a
    # second time, out of band — refuse and point to `next`.
    definition = handler.definition
    if definition.kind.value == "auto":
        raise ValueError("Agent steps run in-process via next, not step-action")
    intent = orch.step_action_intent(args.phase, args.step)
    refreshing = intent == TransitionIntent.REFRESH
    params = handler.parse_parameters(values=_parse_params(getattr(args, "param", None)), cli=True)
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
            out["notifications"] = [item] if D.available(orch, item) else []
            out["permission_to_send"] = False
            out["reservable"] = False
            if getattr(args, "reserve", False):
                raise ValueError("Use notification prepare/claim with the approved hash, not step-action --reserve")
        elif out.get("outbound"):
            out["permission_to_execute"] = False
            if definition.write_command:
                out["reservable"] = False
                out["review_command"] = definition.write_command
                out["note"] = (
                    out.get("note", "") + " Preview the checked write command and approve its exact "
                    "--review-hash with --approved-by. A generic reservation cannot authorize writes."
                ).strip()
                if getattr(args, "reserve", False):
                    raise ValueError(f"Use {definition.write_command} --reserve with its exact reviewed hash")
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
    return out


def cmd_step_action(args):
    try:
        st, orch = C.load_orch(args.runs_root, args.release, args.config)
        out = prepare_step(args, st, orch)
        if out.get("execution_id") or out.get("state_changed"):
            C.save_state(st, args.runs_root, args.release)
        print(_json.dumps(out))
        return 0
    except ValueError as exc:
        print(_json.dumps({"error": str(exc)}))
        return 1


def cmd_reserve_step(args):
    try:
        st, orch = C.load_orch(args.runs_root, args.release, args.config)
        definition = orch.handler(args.phase, args.step).definition
        if definition.write_command:
            raise ValueError(
                f"Use {definition.write_command} --reserve --review-hash <hash> --approved-by <reviewer> "
                "with the reviewed command parameters; reserve-step cannot authorize provider writes.")
        args.param = []
        args.reserve = True
        out = prepare_step(args, st, orch)
        if not out.get("permission_to_execute"):
            print(_json.dumps({
                "kind": "blocked",
                "reason": "Step did not produce a reservable outbound action.",
            }))
            return 1
        C.save_state(st, args.runs_root, args.release)
        print(_json.dumps({
            "kind": "reserved",
            "release": args.release,
            "phase": args.phase,
            "step": args.step,
            "execution_id": orch.step_execution(args.phase, args.step)["id"],
            "permission_to_execute": True,
        }))
        return 0
    except ValueError as exc:
        print(_json.dumps({"error": str(exc)}))
        return 1


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
        print(_json.dumps({"phase": args.phase, "step": args.step, "knowledge": k}))
        return 0
    if not k:
        print(f"No knowledge entry yet for {args.phase}.{args.step}. "
              f"Add one to config/knowledge.yaml.")
        return 0
    print(kb.render_knowledge(args.phase, args.step, k))
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
    sp.set_defaults(func=cmd_step_action)

    reserve = sub.add_parser(
        "reserve-step",
        help="Reserve an eligible external step before any non-idempotent write",
    )
    reserve.add_argument("--release", required=True)
    reserve.add_argument("--phase", required=True)
    reserve.add_argument("--step", required=True)
    reserve.add_argument("--executor", required=True)
    reserve.set_defaults(func=cmd_reserve_step)

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
