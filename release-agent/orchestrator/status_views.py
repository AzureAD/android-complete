"""Status view-model builder — the presentation half of the Orchestrator.

Extracted from engine.py (which owns the state machine) so the report-model builder is a
separate responsibility. This is a MIXIN on Orchestrator: the methods read engine
internals via self (self.state, self.config, self._phase_due, ...) and return the plain
dict that render.py turns into a view. Behaviour is identical to the in-engine version.
"""
from __future__ import annotations

from typing import Optional
from orchestrator import schedule


class StatusViewMixin:
    def _active_phase_report(self, selection=None) -> Optional[dict]:
        """The first incomplete included phase, with its outstanding steps and
        whether its time-window is open (due). This is what the daily phase
        notification reports on — independent of state.current_phase (which is
        only set once the release has been advanced)."""
        selection = selection or self.scheduling()
        hold = selection.focus_hold
        for phase in (selection.frontier.raw,) if selection.frontier else ():
            steps = phase["steps"]
            done = sum(selection.step(phase["id"], s["id"]).complete for s in steps)
            outstanding = [
                {"id": s["id"], "name": s["name"],
                 "gate": self._step_kind(s) == "gate",
                 "reminder": self._is_reminder(s),
                 "owner": "human" if self._is_reminder(s) else "agent"}
                for s in steps if not selection.step(phase["id"], s["id"]).complete
            ]
            completed = [s["name"] for s in steps
                         if selection.step(phase["id"], s["id"]).complete]
            cur = hold.step_id if hold and hold.phase_id == phase["id"] else None
            steps_view = []
            for s in steps:
                sid = s["id"]
                stp = self.state.get_step(phase["id"], sid)
                s_done = selection.step(phase["id"], sid).complete
                s_blocked = stp.status == "blocked"
                s_inflight = stp.status == "in_flight"
                kind = self._step_kind(s)
                is_gate = kind == "gate"
                is_rem = self._is_reminder(s)
                is_scout = kind == "scout"
                is_attest = kind == "attest"
                if s_done:
                    status = "done"
                elif s_blocked:
                    status = "blocked"
                elif stp.status == "running":
                    status = "running"
                elif s_inflight:
                    status = "in_flight"       # pipeline run still executing — Scout polling
                elif is_gate:
                    status = "approval"
                elif is_attest:
                    status = "confirm"
                elif is_rem:
                    status = "action"          # a human to-do — the user must act
                elif is_scout:
                    status = "scout"           # Scout runs it automatically (scrape/send via MCP)
                else:
                    status = "auto"
                # needs_owner = a genuine USER task. A pending scout step is Scout's
                # automatic work (not the user's) until it BLOCKS (s_blocked), so it is
                # NOT flagged — only gates, reminders, attests, and blocks are.
                needs = bool((is_gate or is_rem or is_attest or s_blocked) and not s_done)
                steps_view.append({
                    "id": sid, "name": s["name"], "status": status,
                    "needs_owner": needs,
                    "time_ready": selection.step(phase["id"], sid).time_ready,
                    "note": stp.note,          # agent result / block reason / detail
                    "execution": self.step_execution(phase["id"], sid) if not s_done else {},
                    "links": list(getattr(stp, "links", None) or []),  # durable refs to items evaluated
                    "now": bool(sid == cur and not s_done and (is_gate or is_rem or is_attest or s_blocked)),
                })
            opens = selection.phase(phase["id"]).opens
            return {
                "id": phase["id"], "name": phase["name"],
                "num": phase.get("checklist_phase"),
                "show_pipeline_runs": bool(phase.get("show_pipeline_runs")),
                "done": done, "total": len(steps),
                "due": selection.phase(phase["id"]).due, "started": done > 0,
                "opens": opens.isoformat() if opens else None,
                "opens_in_days": (opens - self.as_of).days if opens else None,
                "outstanding": outstanding,
                "completed": completed,
                "steps": steps_view,
            }
        return None

    def _phase_map(self, selection=None):
        """Build the phase overview + running totals. Returns
        (phases, total, done, current_phase_name, current_phase_obj, current_step_name)."""
        phases = []
        total = done = 0
        current_phase_name = current_step_name = None
        current_phase_obj = None
        selection = selection or self.scheduling()
        hold = selection.focus_hold
        # The authoritative CURRENT phase is the FRONTIER — the first included phase with
        # incomplete steps — NOT merely "any phase with progress". Deriving it here (rather
        # than trusting p_done > 0) means exactly ONE phase shows as current, even when an
        # upstream reopen left stale progress in a later phase (which would otherwise render
        # as a confusing second "in progress" phase).
        frontier = selection.frontier
        frontier_id = frontier.id if frontier else None
        for idx, phase in enumerate(self.config["phases"]):
            phase_readiness = selection.phase(phase["id"])
            if not phase_readiness.included:
                continue
            p_total = len(phase["steps"])
            p_done = sum(selection.step(phase["id"], s["id"]).complete for s in phase["steps"])
            total += p_total
            done += p_done
            is_current = phase["id"] == frontier_id
            due = phase_readiness.due
            opens = phase_readiness.opens
            if p_total and p_done == p_total:
                state = "done"
            elif not due and p_done == 0:
                state = "scheduled"        # not open yet — even if it's the frontier
            elif is_current:
                state = "current"
            else:
                state = "pending"          # not the frontier — a later phase, even if it has
                                           # stale partial progress, is not "in progress" now
            if is_current:
                current_phase_name = phase["name"]
                current_phase_obj = phase
            phases.append({
                "id": phase["id"], "name": phase["name"],
                "num": phase.get("checklist_phase", idx),
                "done": p_done, "total": p_total, "state": state,
                "current": is_current,
                "anchor": phase.get("anchor"),
                "opens": opens.isoformat() if opens else None,
                "opens_in_days": (opens - self.as_of).days if opens else None,
            })
            for s in phase["steps"]:
                if (hold and s["id"] == hold.step_id
                        and phase["id"] == hold.phase_id == frontier_id):
                    current_step_name = s["name"]
        return phases, total, done, current_phase_name, current_phase_obj, current_step_name

    def _current_steps(self, current_phase_obj, selection=None) -> list:
        """The current phase's steps, each tagged with a display state."""
        if not current_phase_obj:
            return []
        selection = selection or self.scheduling()
        phase_due = selection.phase(current_phase_obj["id"]).due
        hold = selection.focus_hold
        projected_status = selection.status
        out = []
        for s in current_phase_obj["steps"]:
            rec = self.state.steps.get(self.state.key(current_phase_obj["id"], s["id"]), {}) or {}
            kind = self._step_kind(s)
            is_scout = kind == "scout"
            step_readiness = selection.step(current_phase_obj["id"], s["id"])
            if rec.get("status") == "skipped" and step_readiness.complete:
                s_state = "skipped"
            elif step_readiness.complete:
                s_state = "done"
            elif rec.get("status") == "running":
                s_state = "running"
            elif rec.get("status") == "blocked":
                s_state = "blocked"          # a step hit a real problem — needs the owner
            elif rec.get("status") == "in_flight":
                s_state = "in_flight"        # pipeline run still executing — Scout is polling
            elif not phase_due or not step_readiness.time_ready:
                # The phase hasn't opened yet — show EVERY not-yet-run step uniformly as
                # "Not open yet" (this check precedes gate/scout/auto so scout steps don't
                # mislabel as "automatic" in an unopened phase — that mix is confusing).
                s_state = "scheduled"
            elif (hold and s["id"] == hold.step_id
                  and current_phase_obj["id"] == hold.phase_id
                  and hold.kind in ("gate", "denied")):
                s_state = "blocked" if hold.kind == "denied" else "gate"
            elif is_scout:
                s_state = "scout"            # Scout's automatic work — never a user "do this"
            elif (hold and s["id"] == hold.step_id
                  and current_phase_obj["id"] == hold.phase_id
                  and projected_status == "awaiting_action"):
                s_state = "reminder"
            elif self._is_reminder(s):
                s_state = "pending"          # a human step queued behind a dependency
            else:
                s_state = "auto"             # an agent step Scout runs itself — no user action
            out.append({
                "id": s["id"], "name": s["name"],
                "gate": kind == "gate",
                "reminder": self._is_reminder(s),
                "owner": "human" if kind in ("gate", "attest", "reminder") else "agent",
                "state": s_state,
                "note": rec.get("note"),          # agent result / block reason / detail
                "execution": self.step_execution(current_phase_obj["id"], s["id"])
                             if s_state not in ("done", "skipped") else {},
                "links": rec.get("links") or [],  # durable refs (wiki page, CG alerts)
            })
        return out

    def _hold_view(self, phase_name, step_name, selection=None) -> dict:
        """Detail of the current hold (gate or action-needed) — same shape for both."""
        current = (selection or self.scheduling()).focus_hold
        hold = {
            "phase": current.phase_id,
            "phase_name": phase_name,
            "step": current.step_id,
            "step_name": step_name,
            "kind": current.kind,
            "reason": current.reason,
        }
        execution = self.step_execution(current.phase_id, current.step_id) if current.step_id else {}
        if execution:
            hold["execution"] = execution
        if current.kind in ("gate", "denied") and current.step_id:
            definition = self._workflow_definition().step(
                current.phase_id, current.step_id
            )
            approval_command = definition.approval_command if definition else None
            if approval_command:
                hold["approval_command"] = approval_command
        return hold

    def _scheduled_view(self, selection=None) -> Optional[dict]:
        """The phase we're waiting on the clock for — derived from the first
        incomplete phase's due-ness, so `status` shows it even before `next`."""
        selection = selection or self.scheduling()
        frontier = selection.frontier
        if (frontier is None or selection.phase(frontier.id).due
                or selection.suspension):
            return None
        opens = selection.phase(frontier.id).opens
        return {
            "phase": frontier.id,
            "phase_name": frontier.name,
            "opens": opens.isoformat() if opens else None,
            "opens_in_days": (opens - self.as_of).days if opens else None,
        }

    def status_report(self) -> dict:
        """Structured status — presentation layer (render.py) turns this into a view.
        Deterministic; no formatting baked in. Assembled from focused builders:
        phase map, current-phase steps, current hold, scheduled window, active phase."""
        violations = self.invariant_violations()
        if any(v.severity == "error" for v in violations):
            return {
                "release_id": self.state.release_id, "status": "blocked",
                "owner_email": self.state.owner_email, "owner_name": self.state.owner_name,
                "ccd": self.state.ccd, "target_month": self.state.target_month,
                "target_month_label": schedule.target_month_label(self.state),
                "as_of": self.as_of.isoformat(), "skip_release": bool(self.state.cancellation),
                "readiness_signed": False, "readiness_pending": [],
                "blocked": True, "blocked_items": [],
                "blocked_message": "Invalid release state; inspect invariant_violations before recovery.",
                "halted": bool(self.state.halt),
                "halt_reason": self.state.halt.get("reason") if isinstance(self.state.halt, dict) else None,
                "done": 0, "total": len(self._workflow_definition().step_by_key), "percent": 0,
                "phases": [], "current_phase": None, "current_phase_name": None,
                "current_step": None, "current_step_name": None, "current_steps": [],
                "gate": None, "action": None, "scheduled": None, "active_phase": None,
                "scout_pending": [], "pending_human": [],
                "gate_decisions": len(self.state.gate_decisions) if isinstance(self.state.gate_decisions, list) else 0,
                "pipeline_runs": {}, "updated_at": self.state.updated_at,
                "invariant_violations": [v.as_dict() for v in violations],
            }
        selection = self.scheduling()
        from orchestrator.revision import mismatch_reason, revision_id
        revision_problem = mismatch_reason(self)
        (phases, total, done, current_phase_name,
         current_phase_obj, current_step_name) = self._phase_map(selection)
        current_steps = self._current_steps(current_phase_obj, selection)
        projected_status = selection.status
        current_hold = selection.focus_hold
        current_phase = current_phase_obj["id"] if current_phase_obj else None
        current_step = current_hold.step_id if current_hold else None

        gate = action = None
        if current_hold and current_hold.kind in ("gate", "denied"):
            gate = self._hold_view(current_phase_name, current_step_name, selection)
        elif projected_status == "awaiting_action" and current_hold:
            # A scout step is the SKILL's work (run via step-action), not a USER action —
            # never surface it as `action` (which the digest reads as "Action needed now").
            phase = next((p for p in self.config["phases"]
                          if p["id"] == current_hold.phase_id), None)
            cur = next((s for s in (phase or {}).get("steps", [])
                        if s["id"] == current_hold.step_id), None) if current_hold.step_id else None
            is_scout_focus = bool(cur and self._step_kind(cur) == "scout")
            cur_blocked = (self.state.get_step(current_hold.phase_id, current_hold.step_id).status
                           == "blocked") if current_hold.step_id else False
            if not is_scout_focus or cur_blocked:
                action = self._hold_view(current_phase_name, current_step_name, selection)
        scheduled = self._scheduled_view(selection)

        chk = self.gate.checklist()
        active_phase = self._active_phase_report(selection)
        # Scout steps ready for the SKILL to execute (perform the MCP send/scrape, then
        # record-step). They are NOT user holds — the skill drains these itself; only if
        # a scout step records 'attention' does it become a blocked user task.
        # GATED ON PHASE DUE: a phase that hasn't reached its anchor (e.g. Code Complete
        # Day before the CCD) must expose NO pending scout work — otherwise the autonomous
        # automation would drain those steps early, running CCD-day comms ahead of the CCD.
        # ALSO GATED ON fire_at_local: a timed step (e.g. the 09:00 CCD comms) is excluded
        # until its wall-clock time arrives, so the every-hour worker doesn't fire it early
        # — its dedicated cron automation runs it at the pinned time.
        scout_pending = list(selection.scout_pending)
        return {
            "release_id": self.state.release_id,
            "status": projected_status,
            "workflow_revision": revision_id(self.state.workflow_revision),
            "workflow_revision_problem": revision_problem or None,
            "owner_email": self.state.owner_email,
            "owner_name": self.state.owner_name,
            "ccd": self.state.ccd,
            "target_month": self.state.target_month,
            "target_month_label": schedule.target_month_label(self.state),
            "ccd_source": self.state.ccd_source,
            "ccd_conflict": self.state.ccd_conflict,
            "as_of": self.as_of.isoformat(),
            "skip_release": bool(self.state.cancellation),
            "readiness_signed": chk["signed"],
            "readiness_pending": [i["id"] for i in chk["items"] if not i["satisfied"]],
            "blocked": chk["blocked"] or bool(revision_problem),
            "blocked_items": list(chk["blocked_items"]),
            "blocked_message": revision_problem or chk.get("blocked_message", ""),
            "halted": bool(self.state.halt),
            "halt_reason": (self.state.halt or {}).get("reason"),
            "done": done, "total": total,
            "percent": round(100 * done / total) if total else 0,
            "phases": phases,
            "current_phase": current_phase,
            "current_phase_name": current_phase_name,
            "current_step": current_step,
            "current_step_name": current_step_name,
            "current_steps": current_steps,
            "gate": gate,
            "action": action,
            "scheduled": scheduled,
            "active_phase": active_phase,
            "scout_pending": scout_pending,
            "pending_human": list(selection.pending_human),
            "gate_decisions": len(self.state.gate_decisions),
            "pipeline_runs": dict(getattr(self.state, "pipeline_runs", {}) or {}),
            "invariant_violations": [
                violation.as_dict() for violation in self.invariant_violations()
            ],
            "updated_at": self.state.updated_at,
        }
