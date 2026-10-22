"""Release readiness entry gate (logic only — no presentation).

A self-contained subsystem: given the readiness config (data) + the release
run-state, it computes the checklist, runs auto verifiers, records attestations
and declines, and decides whether the gate is signed/blocked.

Returns structured data only. Rendering lives in render.py so a different
interface (web UI, other frontend) can present the same data its own way.

Model: every item is equally required. The only distinction is WHO resolves it:
  auto   -> Scout verifies it programmatically (pass/fail)
  attest -> the engineer confirms it
If any item is unsatisfied the gate is not signed. If the engineer declares they
cannot satisfy an item (decline), the gate is blocked until resolved / handed off.
"""
from __future__ import annotations

from . import schedule
from .state import ReleaseState, _now


def dispatch_plan(chk: dict, release_id: str, *, suspension: str = "") -> dict:
    """One host-executed readiness queue for every CLI/presentation surface."""
    auto = [item for item in chk["items"] if item["verify"] == "auto" and not item["satisfied"]]
    # A previously passing read with an invalidated binding needs a fresh probe.
    pending = [item for item in auto if item["status"] in ("pending", "pass", "degraded")]
    failed = [item["id"] for item in auto if item["status"] == "fail"]
    inputs = [item["id"] for item in auto
              if item["status"] not in ("pending", "pass", "degraded", "fail")]
    if suspension:
        action, instruction = "resolve_release_hold", suspension
    elif pending:
        action = "run_auto_checks"
        instruction = (
            "Scout must exhaust all executable readiness checks before returning to the user. "
            "Run every pending probe and record its actual result, then re-read the checklist. "
            "Defer optional permission/input questions until independent checks are exhausted; "
            "never bypass a host consent prompt, retry a failure blindly, or invent a pass.")
    elif chk.get("blocked"):
        action, instruction = "resolve_readiness_block", "Readiness entry gate is blocked; resolve the recorded decline with the owner."
    elif failed:
        action, instruction = "resolve_auto_checks", "Show the recorded failures and request the actual repair."
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
    elif verifier == "silent_perms":
        action["probe"] = {"tool": "m_get_settings", "arguments": {}}
        action["required_servers"] = item.get("required_servers") or []
        action["instructions"] += (
            " Read settings only first. If approval is needed, record needs_input, continue "
            "independent probes, then ask. Never change permissions or record degraded without consent.")
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
        for it in self.config.get("items", []):
            if it.get("verify") != "auto":
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
                self.state.readiness_items[it["id"]] = {
                    "status": status, "message": message, "at": _now()}
                continue
            if it.get("source") == "scout":
                continue                     # skill-run (MCP) — not executable here
            vf = get_verifier(it.get("verifier"))
            if vf is None:
                self.state.readiness_items[it["id"]] = {"status": "fail",
                    "message": f"no verifier '{it.get('verifier')}' registered", "at": _now()}
                continue
            res = vf(it)
            rec = {"status": res.status, "message": res.message, "at": _now()}
            if getattr(res, "details", None):
                rec["checks"] = res.details
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
        if not (it.get("verify") == "auto" and it.get("source") == "scout"):
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
        """Attest human (attest) items and run auto verifiers. item_ids=None
        attests every attest item (library convenience — the CLI never does this;
        it requires explicit item ids). Auto items are only set by verification —
        they cannot be hand-waved through. `note` records the human's confirmation
        as evidence on each attested item. Signs when all items are satisfied."""
        if not self.config:
            return self.checklist()
        self.verify()  # auto items (pass/fail)
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
        """The engineer declares they CANNOT satisfy one or more items. Every item
        is required, so any declined item blocks the gate until resolved / handed off.
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

    # ---- internals ----
    def _item_satisfied(self, item: dict) -> bool:
        record = self.state.readiness_items.get(item["id"], {}) or {}
        status = record.get("status")
        if item["id"] == "m365_access":
            from .m365_capabilities import binding
            from . import mocks as mocks_mod
            mocked = mocks_mod.readiness_result(self.mocks, item["id"])
            if mocked is None and record.get("capability_binding") != binding(self.state):
                return False
        if item.get("verify", "attest") == "auto":
            if item.get("opt_out"):
                # soft item: fully verified (pass) OR user chose to proceed (degraded)
                return status in ("pass", "degraded")
            return status == "pass"          # auto is fully verified: pass or nothing
        return status == "attested"          # attest is human-confirmed

    def _refresh_signed(self) -> None:
        """Readiness is derived; retained as a semantic checkpoint for callers."""
