"""Generic dispatcher for co-located step modules (`step-action`).

This is the ONE command the skill calls to resolve any migrated step into its
uniform outcome. It replaces the old per-step `prepare-X` commands: instead of a
bespoke `prepare-notice`, `prepare-flight-reminder`, … the skill runs

    python -m orchestrator.cli step-action --release <id> --phase preflight --step notice

The command looks up the step module (`steps.get_step`), calls its `build(state)`
(passing through any `--param k=v` the module's signature accepts, e.g. variant),
and prints the outcome as JSON. The skill reads `kind` and reacts uniformly:

    done         → already complete, nothing to run.
    blocked      → surface `reason` to the owner.
    needs_human  → show `prompt` (attestation or reminder).
    needs_skill  → run `tool` with `payload`, then `record-step --step <record_as>`.

Adding a scout step is now: write ONE module under steps/<phase>/ (auto-discovered)
— no CLI command, no registry, no skill-reference edits.
"""
from __future__ import annotations
import inspect
import json as _json

from orchestrator import cli_common as C
from orchestrator import mocks as mocks_mod
from orchestrator import knowledge as kb
from orchestrator.outcomes import as_dict
from steps.lib.context import SELF_CHAT_ID
from steps.lib import mockctx
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
        out[k.strip()] = v
    return out


def _accepted_kwargs(build, params: dict) -> dict:
    """Filter params to only the keyword args `build` actually declares, so an
    unrelated --param never crashes a step that doesn't take it."""
    try:
        sig = inspect.signature(build)
    except (TypeError, ValueError):
        return dict(params)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(params)  # build(**kwargs) takes anything
    names = {name for name in sig.parameters if name != "state"}
    return {k: v for k, v in params.items() if k in names}


def cmd_step_action(args):
    mod = steps.get_step(args.phase, args.step)
    if mod is None:
        print(_json.dumps({
            "error": f"step '{args.phase}.{args.step}' is not migrated to the "
                     f"uniform contract; no co-located handler in steps/.",
            "phase": args.phase, "step": args.step,
        }))
        return 1
    if not hasattr(mod, "build"):
        print(_json.dumps({
            "error": f"step module '{args.phase}.{args.step}' has no build()",
        }))
        return 1

    # Agent steps run IN-PROCESS inside the engine's `next` (they perform the real
    # deterministic action). Executing their build() here would run that action a
    # second time, out of band — refuse and point to `next`.
    if getattr(mod, "KIND", None) == "agent":
        print(_json.dumps({
            "error": f"step '{args.phase}.{args.step}' is an agent step — the engine "
                     f"runs it in-process via `next`; don't dispatch it with step-action.",
            "phase": args.phase, "step": args.step, "kind": "agent",
        }))
        return 1

    try:
        params = _parse_params(getattr(args, "param", None))
    except ValueError as e:
        print(_json.dumps({"error": str(e)}))
        return 1

    st = C.load_state(args.runs_root, args.release)
    kwargs = _accepted_kwargs(mod.build, params)
    spec = mocks_mod.load_mocks().get(f"{args.phase}.{getattr(mod, 'ID', args.step)}") or {}
    with mockctx.active(spec):                     # expose `input` knobs to build()
        outcome = mod.build(st, **kwargs)

    out = as_dict(outcome)
    out["phase"] = args.phase
    out["step"] = getattr(mod, "ID", args.step)
    out["release"] = args.release

    # Local-test payload overrides: a mocks.local.yaml entry may set knobs the step
    # DECLARES via its MOCKABLE spec (e.g. `send_to` on notice) — keeps the send
    # real but redirects it. See `mock-spec` for what each step exposes.
    if out.get("kind") == "needs_skill" and spec:
        _apply_overrides(out, getattr(mod, "MOCKABLE", {}), spec)

    print(_json.dumps(out))
    return 0


def _classify(step: dict) -> str:
    if step.get("gate"):
        return "gate"
    if step.get("source") == "scout":
        return "scout"
    if step.get("attest"):
        return "attest"
    if step.get("owner") == "human":
        return "reminder"
    return "agent"


def _catalog(config_path: str) -> dict:
    """Every step across every phase, with its mock-ability. Engine-level
    (outcome: done|blocked) applies to any non-gate step; payload overrides only
    exist where a migrated step declares MOCKABLE."""
    import yaml
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    out = {}
    for phase in cfg.get("phases", []):
        pid = phase["id"]
        for s in phase.get("steps", []):
            kind = _classify(s)
            key = f"{pid}.{s['id']}"
            entry = {"phase": pid, "name": s.get("name", s["id"]), "kind": kind,
                     "outcome_mockable": kind != "gate", "overrides": {}}
            mod = steps.get_step(pid, s["id"])            # migrated?
            if mod is not None:
                entry["overrides"] = getattr(mod, "MOCKABLE", {}) or {}
            out[key] = entry
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

