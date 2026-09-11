"""Step: `bugbash_updates` — post the FIRST bug-bash progress update and start the 2-hour
poller (Phase 3, bug_bash).

Fire-and-continue (like the localization noon trigger): reaching this step posts the first
progress update to the Bug Bash meeting chat and provisions the `bugbash-update-poller`
automation; the step itself completes so the phase can proceed. The poller then posts an
update every 2 hours during working hours (09:00–18:00 America/Los_Angeles, weekdays,
skipping US holidays) via `post-bugbash-update`, and tears itself down when either every
test is complete OR the owner attests `bugbash_complete`.

Each update is grouped by the test's owner (System.AssignedTo, set by distribute_tests):
owners with remaining tests (not-run, failed, or blocked — failed/blocked surfaced, not
hidden) are @mentioned with those tests (links + state); owners who passed everything appear
by name with an "all completed" line (no mention). A test counts as done only when it passed
(or is N/A). Authenticator cases actually written Failed by `ui_test_status` are shown
distinctly as 'triage', not a manual test to run. Live assignments identify the current
owner; a failed reassignment must not invent an owner change.

The denominator covers manual/triage work, not every case in the Authenticator suite.
Use the completed fill's automated-case classification (the same source as distribution)
to exclude automation-only Auth cases, regardless of their current ADO owner/outcome.
Keep applied automation failures as explicit triage; keep genuine manual Passed/N/A
results in the done count. No internal assignment list or cached owner totals are used.

Depends on: clone_plans_broker (Broker plan id), clone_plans_auth (Auth suite id),
ui_test_status (completed automation classification), activate_chat (meeting chat id).
Blocks if the chat hasn't been activated or the completed fill is missing/stale.

Mock knobs (mocks.local.yaml / tests):
  progress : inject the gathered progress dict (skip the live ADO reads).
  people   : verified Teams people {upn: {id: Entra GUID, name}} for offline rendering.
  send_to  : redirect the post to this chat id for testing.
"""
from __future__ import annotations

import json

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked, Done
from steps.lib.mockctx import mock_input, MISSING
from tools import bugbash as BB
from tools import testplans as T
from steps.bug_bash.activate_chat import stored_chat_id, chat_state_matches
from steps.bug_bash.ui_results import completed_result

ID = "bugbash_updates"
KIND = "scout"
NOTIFICATION = True

BROKER_SUITE_NAME = "Manual Tests (Android Broker)"

MOCKABLE = {
    "progress": {"kind": "input", "desc": "Inject the gathered progress dict (skip ADO reads)."},
    "people": {"kind": "input", "desc": "Verified Teams people {upn:{id: AAD GUID,name}} for offline mention tests."},
    "send_to": {"kind": "payload", "sets": "chatId",
                "desc": "Post to this chat id instead of the resolved meeting chat (test)."},
}


def _broker_plan(state):
    return (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _auth_suite(state):
    return (state.get_step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")


def _auto_failed_ids(state):
    """Applied automation failures, independent of whether owner reassignment succeeded."""
    return completed_result(state)["auth"]["failed_case_ids"]


def gather(state):
    """(ok, progress, detail) — live progress, or the injected `progress` mock."""
    inj = mock_input("progress", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    bp, asuite = _broker_plan(state), _auth_suite(state)
    if not bp or not asuite:
        return (False, None, "the Broker plan / Auth suite aren't ready (run the clone steps).")
    try:
        auth = completed_result(state)["auth"]
    except ValueError as exc:
        return False, None, str(exc)
    return BB.gather_progress(bp, BROKER_SUITE_NAME, T.AUTH_PLAN, asuite,
                              auto_failed_ids=auth["failed_case_ids"],
                              auth_automated_ids=auth["automated_case_ids"])


def plan_links(state):
    return [
        {"name": "Broker test plan", "url": T.plan_web_url(_broker_plan(state))},
        {"name": "Authenticator suite", "url": T.plan_web_url(T.AUTH_PLAN, _auth_suite(state))},
    ]


def prepare_update(state, progress, members_file=None):
    """One resolved transport payload shared by initial and periodic progress posts."""
    chat_id = stored_chat_id(state)
    if not chat_id:
        return False, None, "Missing/stale meeting binding"
    people = mock_input("people", MISSING)
    if people is MISSING:
        observation = None
        if members_file is not None:
            try:
                with open(members_file, encoding="utf-8-sig") as fh:
                    observation = json.load(fh)
            except (OSError, ValueError) as exc:
                return False, None, f"Cannot read meeting-member observation: {exc}"
        ok, people, detail = BB.resolve_mention_people(
            chat_id, progress.get("owners") or {}, member_observation=observation)
        if not ok:
            return False, None, detail
    try:
        content, mentions = BB.render_update(progress, schedule.target_month_label(state) or "Bug Bash",
                                             plan_links(state), people)
    except ValueError as exc:
        return False, None, str(exc)
    return True, {"chatId": chat_id, "content": content, "contentType": "html", "mentions": mentions}, ""


def build(state, members_file=None):
    if not state.ccd:
        return Blocked("bugbash_updates: no CCD set — can't title the Bug Bash.")
    chat_id = stored_chat_id(state)
    if not chat_id:
        return Blocked("bugbash_updates: meeting chat binding is missing/stale "
                       "(run activate_chat against the current invitation).")

    ok, progress, detail = gather(state)
    if not ok:
        return Blocked(f"bugbash_updates: couldn't read test progress ({detail}).")

    month_year = schedule.target_month_label(state)
    if BB.all_complete(progress):
        if not progress["total"]:
            return Done(f"No manual or triage bug-bash work remains for {month_year}; nothing to poll.")
        return Done(f"All {progress['total']} bug-bash tests are already complete — "
                    f"nothing to poll; ready for {month_year} bug bash sign-off.")

    ok, payload, detail = prepare_update(state, progress, members_file)
    if not ok:
        return Blocked(f"bugbash_updates: {detail}")
    return NeedsSkill(
        tool="workiq_send_chat_message",
        payload={
            **payload,
            "_automation": {"on_demand": "bug-bash-update-poller"},
        },
        record_as=ID,
        summary=(f"Post the first {month_year} bug-bash update ({progress['done']}/"
                 f"{progress['total']} done) to the meeting chat, then provision the 2h "
                 f"update poller"),
        note=f"{progress['remaining']} test(s) remaining across {len(progress['owners'])} owner(s)",
        outbound=True,
        notification={"state_matches": chat_state_matches(state)},
    )


def automation_prompt(release: str, spec: dict) -> str:
    """Bespoke instruction for the 2-hour bug-bash update poller (interval automation)."""
    if not spec.get("interval"):
        return ""
    return (
        f"Release {release} — bug-bash update poller (every 2h, working hours).\n"
        f"Run `post-bugbash-update --release {release}` and act on the printed decision:\n"
        f"  • off_hours / weekend / holiday → send nothing (outside 09:00–18:00 LA on a "
        f"working day).\n"
        f"  • post / complete → use source pending for the staged notifications and the "
        f"shared claim/result protocol. Never send raw decision.content. Only confirmed "
        f"delivery of the final summary records poll_complete; known failed sends remain "
        f"retryable. Initial trigger-step completion does not end this phase-owned poller.\n"
        f"  • no_chat / error / stopped → nothing to send; always perform cleanup.\n"
        f"If member resolution lacks Graph access, use workiq_get_chat on the returned "
        f"chatId, save its fresh id/chatType/complete members fields as JSON, then rerun "
        f"with --members-file <path>. "
        f"Never invent member identities or omit mentions to bypass a resolution failure.\n"
        f"Silently journal: `journal --release {release} --source scout --kind automation "
        f"--text \"bugbash-poller: <decision>\"`.")
