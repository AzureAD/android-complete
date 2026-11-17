"""Release readiness entry gate (logic only — no presentation).

All defined readiness items are mandatory (optional `required: false` items are
deprecated and forbidden). Each item declares `depends_on` (empty list = runs
immediately) and `required_tools`. If a check fails due to a missing or
misconfigured tool/connector, ONLY that specific check fails with its explicit
reason while all other runnable checks continue executing to completion.
"""
from __future__ import annotations

from . import schedule
from .state import ReleaseState, _now


def _validate_readiness_config(config: dict | None) -> None:
    if not config:
        return
    items = config.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Readiness config items must be a list.")
    by_id: dict[str, dict] = {}
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError(f"Readiness item #{idx + 1} requires a non-empty string id.")
        iid = item["id"]
        if "required" in item:
            raise ValueError(
                f"Readiness item '{iid}' uses deprecated field 'required'; "
                "optional items are removed and all defined items are mandatory."
            )
        if iid in by_id:
            raise ValueError(f"Duplicate readiness item id: {iid}")
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or any(
            not isinstance(dep, str) or not dep.strip() or dep != dep.strip()
            for dep in deps
        ):
            raise ValueError(f"Readiness item '{iid}' has invalid depends_on.")
        tools = item.get("required_tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, str) or not tool.strip() or tool != tool.strip()
            for tool in tools
        ):
            raise ValueError(f"Readiness item '{iid}' has invalid required_tools.")
        by_id[iid] = item

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(iid: str, path: tuple[str, ...]) -> None:
        if iid in visiting:
            raise ValueError(f"Readiness dependency cycle: {' -> '.join((*path, iid))}")
        if iid in visited:
            return
        visiting.add(iid)
        for dep in by_id[iid].get("depends_on", []):
            if dep not in by_id:
                raise ValueError(f"Readiness item '{iid}' depends on unknown item '{dep}'.")
            if dep == iid:
                raise ValueError(f"Readiness item '{iid}' cannot depend on itself.")
            visit(dep, (*path, iid))
        visiting.remove(iid)
        visited.add(iid)

    for iid in by_id:
        visit(iid, ())


def dispatch_plan(chk: dict, release_id: str, *, suspension: str = "") -> dict:
    """One host-executed readiness queue for every CLI/presentation surface."""
    by_id = {item["id"]: item for item in chk["items"]}

    def deps_met(item: dict) -> bool:
        return all(by_id[dep]["satisfied"] for dep in item.get("depends_on", ()))

    auto = [item for item in chk["items"] if item["verify"] == "auto" and not item["satisfied"]]
    # A previously passing read with an invalidated binding needs a fresh probe;
    # only items whose listed dependencies have completed are runnable now.
    pending = [
        item for item in auto
        if deps_met(item) and item["status"] in ("pending", "pass", "degraded")
    ]
    failed = [item["id"] for item in auto if item["status"] == "fail"]
    inputs = [item["id"] for item in auto
              if item["status"] not in ("pending", "pass", "degraded", "fail")]
    if suspension:
        action, instruction = "resolve_release_hold", suspension
    elif pending:
        action = "run_auto_checks"
        instruction = (
            "Scout must exhaust all runnable readiness checks before returning to the user. "
            "Run every pending probe whose dependencies are met and record its actual result "
            "(if a required tool/connector is missing or misconfigured, record fail on ONLY that "
            "item with the exact reason and continue executing remaining checks). "
            "Never halt early on a single check failure or invent a pass.")
    elif chk.get("blocked"):
        action, instruction = "resolve_readiness_block", "Readiness entry gate is blocked; resolve the recorded decline with the owner."
    elif failed:
        action, instruction = "resolve_auto_checks", "All runnable automatic checks finished; show comprehensive results (successes, failures, and specific failure reasons) and request repair or explicit owner resolution."
    elif inputs:
        action, instruction = "resolve_auto_input", "Ask for the recorded input or consent; no automatic checks remain executable."
    elif chk.get("signed"):
        action, instruction = "complete", "Readiness is complete; continue the normal release flow."
    else:
        action, instruction = "attest", "Automatic checks are complete; request only the outstanding human attestations."
    return {
        "next_action": action,
        "continue_automatically": bool(pending) and not suspension,
        "may_return_to_user": not pending or bool(suspension),
        "instruction": instruction,
        "checklist_command": f"checklist --release {release_id} --attest-prompt --json",
        "handoff_command": f"checklist --release {release_id} --attest-prompt --handoff --json",
        "pending_auto_checks": [_auto_check_action(item, release_id) for item in pending],
        "failed_auto_checks": failed,
        "input_required_auto_checks": inputs,
    }


def _auto_check_action(item: dict, release_id: str) -> dict:
    iid = item["id"]
    source = item.get("source") or "python"
    verifier = item.get("verifier")
    command = f"record-check --release {release_id} --item {iid}"
    action = {
        "id": iid, "source": source, "verifier": verifier,
        "depends_on": list(item.get("depends_on") or ()),
        "required_tools": list(item.get("required_tools") or ()),
        "instructions": item.get("detail") or item.get("text") or "",
    }
    if source == "python":
        action["probe"] = {"command": f"verify --release {release_id} --pending-only"}
        return action
    action["record_command"] = command + " --status <actual-result> --detail <actual-evidence>"
    action["input_command"] = command + " --status needs_input --detail <specific-input-or-consent>"
    if verifier == "ccd_confirmed":
        action["probe"] = {"command": f"check-ccd --release {release_id} --json"}
    elif verifier == "m365_capabilities":
        action["probe"] = {"command": f"m365-capabilities --release {release_id}"}
        action["record_command"] += " --evidence-file <fresh-validated-observations.json>"
    elif verifier == "teams_notify":
        action["probe"] = {"tool": "m_relay_status", "arguments": {}}
        action["instructions"] += (
            " Follow reference/readiness-gate.md for the configured channel, interactive "
            "connection/handshake and opt-out. Missing required consent is needs_input, not a pass.")
    elif verifier == "kusto_access":
        action["probe"] = {
            "tool": "kusto_-_adx-kusto_query",
            "arguments": {"cluster_uri": item.get("cluster_uri"),
                          "database": item.get("database"), "query": "print 1"},
        }
    elif verifier == "oncall":
        action["probe"] = {
            "tool": "icm-get_on_call_schedule_by_team_id",
            "arguments": {"teamIds": [item.get("team_id")]},
        }
        action["identity_probe"] = {"tool": "icm-get_my_icm_context", "arguments": {}}
        action["instructions"] += " Only current primary duty blocks; do not fail a backup. Ask if the role is ambiguous."
    else:
        action["probe"] = {"command": f"gate-info --item {iid}"}
        action["instructions"] += " Resolve the configured verifier; if unavailable, record its real failure, never silently skip it."
    return action


class ReadinessGate:
    def __init__(self, config: dict, state: ReleaseState):
        # config is the parsed readiness.yaml (or None if not configured)
        _validate_readiness_config(config)
        self.config = config
        self.state = state
        # Local test mocks (set by the engine). `readiness.<item>` entries force an
        # AUTO item pass/fail without the real ADO/config check.
        self.mocks = {}

    def _window(self, it: dict):
        """Compute a CCD-relative window {start,end} for an item that declares
        window_start_anchor / window_end_anchor, using the release CCD. Returns
        None when the item has no window or CCD isn't set yet."""
        sa, ea = it.get("window_start_anchor"), it.get("window_end_anchor")
        ccd = schedule.parse_date(self.state.ccd)
        if not (sa and ea and ccd):
            return None
        try:
            return {"start": schedule.anchor_date(ccd, sa).isoformat(),
                    "end": schedule.anchor_date(ccd, ea).isoformat()}
        except ValueError:
            return None

    # ---- queries ----
    def checklist(self) -> dict:
        """The entry checklist as structured data (no formatting)."""
        if not self.config:
            chk = {
                "items": [],
                "auto_items": [],
                "attest_items": [],
                "signed": True,
                "signed_at": None,
                "blocked": False,
                "blocked_items": [],
                "all_satisfied": True,
                "title": "",
                "instructions": "",
                "blocked_message": "",
            }
            return {**chk, "dispatch": self._dispatch(chk)}
        items = []
        for it in self.config.get("items", []):
            rec = self.state.readiness_items.get(it["id"], {}) or {}
            links = []
            for ln in it.get("links", []):
                if isinstance(ln, dict):
                    links.append({"name": ln.get("name", ln.get("url")), "url": ln.get("url")})
                else:
                    links.append({"name": ln, "url": ln})
            items.append({
                "id": it["id"], "text": it["text"],
                "label": it.get("label", it["id"]),
                "detail": it.get("detail"),
                "links": links,
                "verify": it.get("verify", "attest"),   # auto | attest (who resolves it)
                "depends_on": list(it.get("depends_on") or ()),
                "required_tools": list(it.get("required_tools") or ()),
                "source": it.get("source"),              # None (python) | "scout" (skill runs it via MCP)
                "verifier": it.get("verifier"),
                "team_id": it.get("team_id"),            # for scout-assisted checks (e.g. on-call team)
                "team_name": it.get("team_name"),
                "cluster_uri": it.get("cluster_uri"),    # for scout-assisted Kusto checks
                "database": it.get("database"),
                "required_servers": it.get("required_servers"),  # for scout-assisted silent-perms check
                "opt_out": it.get("opt_out", False),      # soft item: may be waived ("degraded") and still satisfy
                "window": self._window(it),              # {start,end} for windowed attest items
                "status": rec.get("status", "pending"),  # pending | pass | fail | attested | unable
                "message": rec.get("message"),
                "checks": rec.get("checks", []),         # per-check results for auto items
                "satisfied": self._item_satisfied(it),
            })
        chk = {
            "title": self.config.get("title", "Release readiness"),
            "owner_email": self.state.owner_email,
            "owner_name": self.state.owner_name,
            "instructions": self.config.get("instructions", ""),
            "blocked_message": self.config.get("blocked_message", ""),
            "items": items,
            "auto_items": [i for i in items if i["verify"] == "auto"],
            "attest_items": [i for i in items if i["verify"] == "attest"],
            "signed": self.signed,
            "signed_at": self.signed_at,
            "blocked": self.blocked,
            "blocked_items": self.blocked_item_ids,
            "all_satisfied": all(i["satisfied"] for i in items) if items else True,
        }
        return {**chk, "dispatch": self._dispatch(chk)}

    def _dispatch(self, chk):
        suspension = ("Release cancelled; no readiness dispatch is authorized." if self.state.cancellation else
                      "Release halted; resume explicitly before readiness dispatch." if self.state.halt else "")
        return dispatch_plan(chk, self.state.release_id, suspension=suspension)

    @property
    def signed(self) -> bool:
        items = self.config.get("items", []) if self.config else []
        return all(self._item_satisfied(item) for item in items)

    @property
    def signed_at(self):
        if not self.signed:
            return None
        times = [
            (self.state.readiness_items.get(item["id"], {}) or {}).get("at")
            for item in (self.config or {}).get("items", [])
        ]
        return max((value for value in times if value), default=None)

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_item_ids)

    @property
    def blocked_item_ids(self) -> list:
        return [
            item["id"]
            for item in (self.config or {}).get("items", [])
            if (self.state.readiness_items.get(item["id"], {}) or {}).get("status")
            == "unable"
        ]

    # ---- mutations ----
    def verify(self, *, pending_only=False) -> dict:
        """Run the AUTO verifiers. Each returns pass or fail — no half-measures.
        Items with source: scout are skipped here — the skill runs those via its
        MCP tools and records the result with record_check()."""
        from phases.readiness_verifiers import get_verifier
        from . import mocks as mocks_mod
        if not self.config:
            return self.checklist()
        items = self.config.get("items", [])
        by_id = {it["id"]: it for it in items}
        for it in items:
            if it.get("verify") != "auto":
                continue
            if any(not self._item_satisfied(by_id[dep]) for dep in it.get("depends_on", ())):
                continue
            if pending_only and (
                    (self.state.readiness_items.get(it["id"]) or {}).get("status", "pending")
                    not in ("pending", "pass", "degraded") or self._item_satisfied(it)):
                continue
            # Local test mock: force ANY auto item (python OR scout-assisted) pass/fail,
            # skipping the real ADO/config/MCP check — lets a test clear the gate offline.
            mock = mocks_mod.readiness_result(self.mocks, it["id"])
            if mock is not None:
                status, message = mock
                rec = {"status": status, "message": message, "at": _now()}
                if it["id"] == "m365_access" and status == "pass":
                    from .m365_capabilities import binding
                    rec["capability_binding"] = binding(self.state)
                self.state.readiness_items[it["id"]] = rec
                continue
            if it.get("source") == "scout":
                continue                     # skill-run (MCP) — not executable here
            vf = get_verifier(it.get("verifier"))
            if vf is None:
                self.state.readiness_items[it["id"]] = {"status": "fail",
                    "message": f"no verifier '{it.get('verifier')}' registered", "at": _now()}
                continue
            try:
                import inspect
                params = inspect.signature(vf).parameters
                res = vf(it, self.state) if len(params) >= 2 else vf(it)
            except Exception as exc:
                self.state.readiness_items[it["id"]] = {
                    "status": "fail",
                    "message": f"Required tool/connector error: {exc}",
                    "at": _now(),
                }
                continue
            rec = {"status": res.status, "message": res.message, "at": _now()}
            if getattr(res, "details", None):
                rec["checks"] = res.details
            if getattr(res, "extra", None):
                rec.update(res.extra)
            self.state.readiness_items[it["id"]] = rec
        self._refresh_signed()
        return self.checklist()

    def record_check(self, item_id: str, status: str, message: str = "", *, evidence=None) -> dict:
        """Record the result of a SCOUT-ASSISTED auto check (source: scout), which
        the skill performs via its MCP tools (e.g. the ICM on-call lookup). Only
        valid for auto items marked source: scout — a Python-verified auto item
        (e.g. build_access) cannot be hand-recorded, and attest items use sign().

        `needs_input` records an actual consent/input boundary without satisfying
        the check or preventing independent pending probes from running.
        Status is normally 'pass' | 'fail'. Items marked `opt_out: true` (soft items
        the user may proceed WITHOUT — e.g. silent_perms) also accept 'degraded',
        which SATISFIES the gate while recording that they chose to proceed without
        the capability (the downside is captured in `message`)."""
        by_id = {it["id"]: it for it in (self.config or {}).get("items", [])}
        it = by_id.get(item_id)
        if it is None:
            return {"error": f"no such readiness item '{item_id}'"}
        if not (it.get("verify") == "auto" and (it.get("source") == "scout" or item_id == "m365_access")):
            return {"error": f"'{item_id}' is not a scout-assisted auto item (cannot record a result for it)"}
        valid = ("pass", "fail", "needs_input") + (("degraded",) if it.get("opt_out") else ())
        if status not in valid:
            return {"error": f"status must be one of: {', '.join(valid)}"}
        if status == "needs_input" and not message.strip():
            return {"error": "needs_input requires the specific missing input or consent"}
        proof = {}
        if item_id == "m365_access" and status == "pass":
            from .m365_capabilities import validate
            try:
                proof = validate(self.state, evidence)
            except (ValueError, TypeError, KeyError) as exc:
                return {"error": str(exc)}
        elif evidence is not None:
            return {"error": "Structured M365 evidence is only accepted for a passing m365_access check"}
        self.state.readiness_items[item_id] = {
            "status": status, "message": message, "at": _now(), "source": "scout", **proof}
        self._refresh_signed()
        return self.checklist()

    def sign(self, item_ids=None, note=None) -> dict:
        """Record human facts without probing integrations. item_ids=None
        attests every attest item (library convenience — the CLI never does this;
        it requires explicit item ids). Auto items are only set by verification —
        they cannot be hand-waved through. `note` records the human's confirmation
        as evidence on each attested item. Signs when all items are satisfied."""
        if not self.config:
            return self.checklist()
        all_items = self.config.get("items", [])
        attest_ids = [it["id"] for it in all_items if it.get("verify", "attest") == "attest"]
        targets = attest_ids if item_ids is None else [i for i in item_ids if i in attest_ids]
        for iid in targets:
            rec = {"status": "attested", "at": _now()}
            if note:
                rec["note"] = note
            self.state.readiness_items[iid] = rec
        self._refresh_signed()
        return self.checklist()

    def decline(self, item_ids: list) -> dict:
        """Record inability to satisfy items; only required items block progression.
        Unknown IDs reject the whole request without changing readiness facts."""
        by_id = {it["id"]: it for it in (self.config or {}).get("items", [])}
        targets = item_ids or []
        unknown = [iid for iid in targets if iid not in by_id]
        if unknown:
            return {"error": f"No such readiness item(s): {', '.join(unknown)}"}
        for iid in targets:
            self.state.readiness_items[iid] = {"status": "unable", "at": _now()}
        return self.checklist()

    def blocked_labels(self) -> list:
        """Human labels for the currently-blocked item ids."""
        by_id = {it["id"]: it.get("label", it["id"]) for it in (self.config or {}).get("items", [])}
        return [by_id.get(item_id, item_id) for item_id in self.blocked_item_ids]

    def _release_started(self) -> bool:
        return bool(
            self.state.gate_decisions
            or any(
                (rec.get("status") if isinstance(rec, dict) else getattr(rec, "status", "pending")) != "pending"
                for rec in (self.state.steps or {}).values()
            )
        )

    def override_oncall(self, confirmed_by: str, reason: str) -> dict:
        """Allow an already-started release to continue if the owner rotates onto primary on-call."""
        if not isinstance(confirmed_by, str) or not confirmed_by.strip():
            return {"error": "An owner confirmation identity is required."}
        if not self.state.owner_email or confirmed_by.strip().casefold() != self.state.owner_email.strip().casefold():
            return {"error": "Only the configured release owner can approve a continuation-only on-call override."}
        if not isinstance(reason, str) or not reason.strip():
            return {"error": "A non-empty reason is required for an audited on-call override."}
        if not self._release_started():
            return {"error": "On-call override is only allowed for an already-started release; new releases require a non-on-call owner."}
        rec = dict(self.state.readiness_items.get("oncall_now") or {})
        if rec.get("status") != "fail":
            return {"error": "oncall_now override is only needed when oncall_now recorded fail."}
        rec["override"] = {
            "scope": "continuation",
            "release": self.state.release_id,
            "owner": self.state.owner_email,
            "by": confirmed_by.strip(),
            "reason": reason.strip(),
            "at": _now(),
        }
        self.state.readiness_items["oncall_now"] = rec
        self._refresh_signed()
        return self.checklist()

    def resolve_failed_item(self, item_id: str, *, mode: str, confirmed_by: str, reason: str) -> dict:
        """Audited owner resolution ('waived' or 'manual') for a readiness item after a tool/connector failure."""
        by_id = {it["id"]: it for it in (self.config or {}).get("items", [])}
        it = by_id.get(item_id)
        if it is None:
            return {"error": f"No such readiness item '{item_id}'."}
        if mode not in ("waived", "manual"):
            return {"error": f"Unsupported resolution mode '{mode}'."}
        if not isinstance(confirmed_by, str) or not confirmed_by.strip():
            return {"error": "An owner confirmation identity is required."}
        if self.state.owner_email and confirmed_by.strip().casefold() != self.state.owner_email.strip().casefold():
            return {"error": "Only the configured release owner can resolve a readiness item manually."}
        if not isinstance(reason, str) or not reason.strip():
            return {"error": "A non-empty reason/note is required."}
        rec = dict(self.state.readiness_items.get(item_id) or {})
        rec.update({
            "status": "skipped" if mode == "waived" else "attested",
            "message": reason.strip(),
            "owner_resolution": {
                "mode": mode,
                "by": confirmed_by.strip(),
                "reason": reason.strip(),
                "at": _now(),
            },
            "at": _now(),
        })
        if item_id == "m365_access":
            from .m365_capabilities import binding
            rec["capability_binding"] = binding(self.state)
        self.state.readiness_items[item_id] = rec
        self._refresh_signed()
        return self.checklist()

    # ---- internals ----
    def _item_satisfied(self, item: dict) -> bool:
        record = self.state.readiness_items.get(item["id"], {}) or {}
        status = record.get("status")
        owner_res = record.get("owner_resolution")
        if (isinstance(owner_res, dict)
                and owner_res.get("mode") in ("waived", "manual")
                and isinstance(owner_res.get("reason"), str)
                and owner_res["reason"].strip()):
            return True
        if item["id"] == "m365_access":
            from .m365_capabilities import binding
            from . import mocks as mocks_mod
            mocked = mocks_mod.readiness_result(self.mocks, item["id"])
            if mocked is None and record.get("capability_binding") != binding(self.state):
                return False
        if item["id"] == "oncall_now" and status == "fail":
            override = record.get("override")
            if (isinstance(override, dict)
                    and override.get("scope") == "continuation"
                    and override.get("release") == self.state.release_id
                    and override.get("owner") == self.state.owner_email
                    and isinstance(override.get("reason"), str) and override["reason"].strip()
                    and self._release_started()):
                return True
        if item.get("verify", "attest") == "auto":
            if item.get("opt_out"):
                # soft item: fully verified (pass) OR user chose to proceed (degraded)
                return status in ("pass", "degraded")
            return status == "pass"          # auto is fully verified: pass or nothing
        return status == "attested"          # attest is human-confirmed

    def _refresh_signed(self) -> None:
        """Readiness is derived; retained as a semantic checkpoint for callers."""
