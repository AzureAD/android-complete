"""Status view-model builder — the presentation half of the Orchestrator.

Extracted from engine.py (which owns the state machine) so the report-model builder is a
separate responsibility. This is a MIXIN on Orchestrator: the methods read engine
internals via self (self.state, self.config, self._phase_due, ...) and return the plain
dict that render.py turns into a view. Behaviour is identical to the in-engine version.
"""
from __future__ import annotations

from typing import Optional


class StatusViewMixin:
    def _active_phase_report(self) -> Optional[dict]:
        """The first incomplete included phase, with its outstanding steps and
        whether its time-window is open (due). This is what the daily phase
        notification reports on — independent of state.current_phase (which is
        only set once the release has been advanced)."""
        for phase in self.config["phases"]:
            if not self._phase_included(phase):
                continue
            steps = phase["steps"]
            done = sum(1 for s in steps if self.state.is_done(phase["id"], s["id"]))
            if done == len(steps):
                continue                      # phase complete — look at the next one
            outstanding = [
                {"id": s["id"], "name": s["name"], "gate": bool(s.get("gate")),
                 "reminder": self._is_reminder(s), "owner": s.get("owner", "agent")}
                for s in steps if not self.state.is_done(phase["id"], s["id"])
            ]
            completed = [s["name"] for s in steps
                         if self.state.is_done(phase["id"], s["id"])]
            cur = self.state.current_step
            steps_view = []
            for s in steps:
                sid = s["id"]
                stp = self.state.get_step(phase["id"], sid)
                s_done = self.state.is_done(phase["id"], sid)
                s_blocked = stp.status == "blocked"
                is_gate = bool(s.get("gate"))
                is_rem = self._is_reminder(s)
                is_scout = s.get("source") == "scout"
                is_attest = bool(s.get("attest"))
                if s_done:
                    status = "done"
                elif s_blocked:
                    status = "blocked"
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
                    "time_ready": self._step_time_ready(phase, s),   # False = waits for its fire_at_local
                    "note": stp.note,          # agent result / block reason / detail
                    "links": list(getattr(stp, "links", None) or []),  # durable refs to items evaluated
                    "now": bool(sid == cur and not s_done and (is_gate or is_rem or is_attest or s_blocked)),
                })
            opens = self._phase_anchor_date(phase)
            return {
                "id": phase["id"], "name": phase["name"],
                "num": phase.get("checklist_phase"),
                "show_pipeline_runs": bool(phase.get("show_pipeline_runs")),
                "done": done, "total": len(steps),
                "due": self._phase_due(phase), "started": done > 0,
                "opens": opens.isoformat() if opens else None,
                "opens_in_days": (opens - self.as_of).days if opens else None,
                "outstanding": outstanding,
                "completed": completed,
                "steps": steps_view,
            }
        return None

    def _phase_map(self):
        """Build the phase overview + running totals. Returns
        (phases, total, done, current_phase_name, current_phase_obj, current_step_name)."""
        phases = []
        total = done = 0
        current_phase_name = current_step_name = None
        current_phase_obj = None
        for idx, phase in enumerate(self.config["phases"]):
            if not self._phase_included(phase):
                continue
            p_total = len(phase["steps"])
            p_done = sum(1 for s in phase["steps"] if self.state.is_done(phase["id"], s["id"]))
            total += p_total
            done += p_done
            is_current = self.state.current_phase == phase["id"]
            due = self._phase_due(phase)
            opens = self._phase_anchor_date(phase)
            if p_total and p_done == p_total:
                state = "done"
            elif not due and p_done == 0:
                state = "scheduled"
            elif is_current or p_done > 0:
                state = "current"
            else:
                state = "pending"
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
                if s["id"] == self.state.current_step and phase["id"] == self.state.current_phase:
                    current_step_name = s["name"]
        return phases, total, done, current_phase_name, current_phase_obj, current_step_name

    def _current_steps(self, current_phase_obj) -> list:
        """The current phase's steps, each tagged with a display state."""
        if not current_phase_obj:
            return []
        phase_due = self._phase_due(current_phase_obj)
        out = []
        for s in current_phase_obj["steps"]:
            rec = self.state.steps.get(self.state.key(current_phase_obj["id"], s["id"]), {}) or {}
            is_scout = s.get("source") == "scout" and not s.get("attest")
            if rec.get("status") == "skipped":
                s_state = "skipped"
            elif self.state.is_done(current_phase_obj["id"], s["id"]):
                s_state = "done"
            elif rec.get("status") == "blocked":
                s_state = "blocked"          # a step hit a real problem — needs the owner
            elif s["id"] == self.state.current_step and self.state.status == "holding_gate":
                s_state = "gate"
            elif is_scout:
                s_state = "scout"            # Scout's automatic work — never a user "do this"
            elif s["id"] == self.state.current_step and self.state.status == "awaiting_action":
                s_state = "reminder"
            elif not phase_due:
                s_state = "scheduled"
            elif s.get("owner") == "human" or s.get("attest") or self._is_reminder(s):
                s_state = "pending"          # a human step queued behind a dependency
            else:
                s_state = "auto"             # an agent step Scout runs itself — no user action
            out.append({
                "id": s["id"], "name": s["name"],
                "gate": bool(s.get("gate")),
                "reminder": self._is_reminder(s),
                "owner": s.get("owner", "agent"),
                "state": s_state,
                "note": rec.get("note"),          # agent result / block reason / detail
                "links": rec.get("links") or [],  # durable refs (wiki page, CG alerts)
            })
        return out

    def _hold_view(self, phase_name, step_name) -> dict:
        """Detail of the current hold (gate or action-needed) — same shape for both."""
        return {
            "phase": self.state.current_phase,
            "phase_name": phase_name,
            "step": self.state.current_step,
            "step_name": step_name,
        }

    def _scheduled_view(self) -> Optional[dict]:
        """The phase we're waiting on the clock for — derived from the first
        incomplete phase's due-ness, so `status` shows it even before `next`."""
        first_incomplete = next(
            (p for p in self.config["phases"]
             if self._phase_included(p)
             and not all(self.state.is_done(p["id"], s["id"]) for s in p["steps"])),
            None)
        if (first_incomplete is None or self._phase_due(first_incomplete)
                or self.state.status in ("complete", "halted", "blocked")):
            return None
        opens = self._phase_anchor_date(first_incomplete)
        return {
            "phase": first_incomplete["id"],
            "phase_name": first_incomplete["name"],
            "opens": opens.isoformat() if opens else None,
            "opens_in_days": (opens - self.as_of).days if opens else None,
        }

    def status_report(self) -> dict:
        """Structured status — presentation layer (render.py) turns this into a view.
        Deterministic; no formatting baked in. Assembled from focused builders:
        phase map, current-phase steps, current hold, scheduled window, active phase."""
        (phases, total, done, current_phase_name,
         current_phase_obj, current_step_name) = self._phase_map()
        current_steps = self._current_steps(current_phase_obj)

        gate = action = None
        if self.state.status == "holding_gate" and self.state.current_phase:
            gate = self._hold_view(current_phase_name, current_step_name)
        elif self.state.status == "awaiting_action" and self.state.current_phase:
            # A scout step is the SKILL's work (run via step-action), not a USER action —
            # never surface it as `action` (which the digest reads as "Action needed now").
            phase = next((p for p in self.config["phases"]
                          if p["id"] == self.state.current_phase), None)
            cur = next((s for s in (phase or {}).get("steps", [])
                        if s["id"] == self.state.current_step), None) if self.state.current_step else None
            is_scout_focus = bool(cur and cur.get("source") == "scout" and not cur.get("attest"))
            cur_blocked = (self.state.get_step(self.state.current_phase, self.state.current_step).status
                           == "blocked") if self.state.current_step else False
            if not is_scout_focus or cur_blocked:
                action = self._hold_view(current_phase_name, current_step_name)
        scheduled = self._scheduled_view()

        chk = self.gate.checklist()
        active_phase = self._active_phase_report()
        # Scout steps ready for the SKILL to execute (perform the MCP send/scrape, then
        # record-step). They are NOT user holds — the skill drains these itself; only if
        # a scout step records 'attention' does it become a blocked user task.
        # GATED ON PHASE DUE: a phase that hasn't reached its anchor (e.g. Code Complete
        # Day before the CCD) must expose NO pending scout work — otherwise the autonomous
        # automation would drain those steps early, running CCD-day comms ahead of the CCD.
        # ALSO GATED ON fire_at_local: a timed step (e.g. the 09:00 CCD comms) is excluded
        # until its wall-clock time arrives, so the every-hour worker doesn't fire it early
        # — its dedicated cron automation runs it at the pinned time.
        scout_pending = ([s["id"] for s in (active_phase or {}).get("steps", [])
                          if s.get("status") == "scout" and s.get("time_ready", True)]
                         if (active_phase and active_phase.get("due")) else [])
        return {
            "release_id": self.state.release_id,
            "status": self.state.status,
            "owner_email": self.state.owner_email,
            "owner_name": self.state.owner_name,
            "ccd": self.state.ccd,
            "ccd_source": self.state.ccd_source,
            "ccd_conflict": self.state.ccd_conflict,
            "as_of": self.as_of.isoformat(),
            "skip_release": self.state.skip_release,
            "readiness_signed": self.state.readiness_signed,
            "readiness_pending": [i["id"] for i in chk["items"] if not i["satisfied"]],
            "blocked": self.state.blocked,
            "blocked_items": list(self.state.blocked_items),
            "blocked_message": chk.get("blocked_message", ""),
            "halted": self.state.halted,
            "halt_reason": self.state.halt_reason,
            "done": done, "total": total,
            "percent": round(100 * done / total) if total else 0,
            "phases": phases,
            "current_phase": self.state.current_phase,
            "current_phase_name": current_phase_name,
            "current_step": self.state.current_step,
            "current_step_name": current_step_name,
            "current_steps": current_steps,
            "gate": gate,
            "action": action,
            "scheduled": scheduled,
            "active_phase": active_phase,
            "scout_pending": scout_pending,
            "pending_human": list(self.state.pending_human),
            "gate_decisions": len(self.state.gate_decisions),
            "pipeline_runs": dict(getattr(self.state, "pipeline_runs", {}) or {}),
            "updated_at": self.state.updated_at,
        }
