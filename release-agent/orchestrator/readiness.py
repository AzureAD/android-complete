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


class ReadinessGate:
    def __init__(self, config: dict, state: ReleaseState):
        # config is the parsed readiness.yaml (or None if not configured)
        self.config = config
        self.state = state

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
            return {"items": [], "signed": True, "title": "", "instructions": ""}
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
        return {
            "title": self.config.get("title", "Release readiness"),
            "instructions": self.config.get("instructions", ""),
            "blocked_message": self.config.get("blocked_message", ""),
            "items": items,
            "auto_items": [i for i in items if i["verify"] == "auto"],
            "attest_items": [i for i in items if i["verify"] == "attest"],
            "signed": self.state.readiness_signed,
            "signed_at": self.state.readiness_signed_at,
            "blocked": self.state.blocked,
            "blocked_items": list(self.state.blocked_items),
            "all_satisfied": all(i["satisfied"] for i in items) if items else True,
        }

    @property
    def signed(self) -> bool:
        return self.state.readiness_signed

    @property
    def blocked(self) -> bool:
        return self.state.blocked

    # ---- mutations ----
    def verify(self) -> dict:
        """Run the AUTO verifiers. Each returns pass or fail — no half-measures.
        Items with source: scout are skipped here — the skill runs those via its
        MCP tools and records the result with record_check()."""
        from phases.readiness_verifiers import get_verifier
        if not self.config:
            return self.checklist()
        for it in self.config.get("items", []):
            if it.get("verify") != "auto":
                continue
            if it.get("source") == "scout":
                continue                     # skill-run (MCP) — not executable here
            vf = get_verifier(it.get("verifier"))
            if vf is None:
                self.state.readiness_items[it["id"]] = {"status": "fail",
                    "message": f"no verifier '{it.get('verifier')}' registered", "at": _now()}
                continue
            res = vf(it, self.state.dry_run)
            rec = {"status": res.status, "message": res.message, "at": _now()}
            if getattr(res, "details", None):
                rec["checks"] = res.details
            self.state.readiness_items[it["id"]] = rec
        self._refresh_signed()
        return self.checklist()

    def record_check(self, item_id: str, status: str, message: str = "") -> dict:
        """Record the result of a SCOUT-ASSISTED auto check (source: scout), which
        the skill performs via its MCP tools (e.g. the ICM on-call lookup). Only
        valid for auto items marked source: scout — a Python-verified auto item
        (e.g. build_access) cannot be hand-recorded, and attest items use sign().

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
        valid = ("pass", "fail") + (("degraded",) if it.get("opt_out") else ())
        if status not in valid:
            return {"error": f"status must be one of: {', '.join(valid)}"}
        self.state.readiness_items[item_id] = {
            "status": status, "message": message, "at": _now(), "source": "scout"}
        self._refresh_signed()
        return self.checklist()

    def sign(self, item_ids=None) -> dict:
        """Attest human (attest) items and run auto verifiers. item_ids=None
        attests every attest item. Auto items are only set by verification —
        they cannot be hand-waved through. Signs when all items are satisfied."""
        if not self.config:
            self.state.readiness_signed = True
            self.state.readiness_signed_at = _now()
            return self.checklist()
        self.verify()  # auto items (pass/fail)
        all_items = self.config.get("items", [])
        attest_ids = [it["id"] for it in all_items if it.get("verify", "attest") == "attest"]
        targets = attest_ids if item_ids is None else [i for i in item_ids if i in attest_ids]
        for iid in targets:
            self.state.readiness_items[iid] = {"status": "attested", "at": _now()}
        self._refresh_signed()
        return self.checklist()

    def decline(self, item_ids: list) -> dict:
        """The engineer declares they CANNOT satisfy one or more items. Every item
        is required, so any declined item blocks the gate until resolved / handed off."""
        if not self.config:
            return self.checklist()
        by_id = {it["id"]: it for it in self.config.get("items", [])}
        for iid in item_ids or []:
            if iid not in by_id:
                continue
            self.state.readiness_items[iid] = {"status": "unable", "at": _now()}
            self.state.blocked = True
            if iid not in self.state.blocked_items:
                self.state.blocked_items.append(iid)
        self.state.readiness_signed = False
        return self.checklist()

    def blocked_labels(self) -> list:
        """Human labels for the currently-blocked item ids."""
        by_id = {it["id"]: it.get("label", it["id"]) for it in (self.config or {}).get("items", [])}
        return [by_id.get(b, b) for b in self.state.blocked_items]

    # ---- internals ----
    def _item_satisfied(self, item: dict) -> bool:
        status = (self.state.readiness_items.get(item["id"], {}) or {}).get("status")
        if item.get("verify", "attest") == "auto":
            if item.get("opt_out"):
                # soft item: fully verified (pass) OR user chose to proceed (degraded)
                return status in ("pass", "degraded")
            return status == "pass"          # auto is fully verified: pass or nothing
        return status == "attested"          # attest is human-confirmed

    def _refresh_signed(self) -> None:
        items = self.config.get("items", []) if self.config else []
        if items and all(self._item_satisfied(it) for it in items):
            if not self.state.readiness_signed:
                self.state.readiness_signed = True
                self.state.readiness_signed_at = _now()
