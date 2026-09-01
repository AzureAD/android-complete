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
(or is N/A). Failed AUTOMATED Authenticator cases (pre-assigned to the release owner by
`ui_test_status`) are shown distinctly as 'triage' — an investigation the owner already owns,
not a manual test to run.

Depends on: clone_plans_broker (Broker plan id), clone_plans_auth (Auth suite id),
activate_chat (meeting chat id). Blocks if the chat hasn't been activated.

Mock knobs (mocks.local.yaml / tests):
  progress : inject the gathered progress dict (skip the live ADO reads).
  send_to  : redirect the post to this chat id for testing.
"""
from __future__ import annotations

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked, Done
from steps.lib.mockctx import mock_input, MISSING
from tools import bugbash as BB
from tools import testplans as T
from steps.bug_bash.activate_chat import stored_chat_id

ID = "bugbash_updates"
KIND = "scout"

BROKER_SUITE_NAME = "Manual Tests (Android Broker)"

MOCKABLE = {
    "progress": {"kind": "input", "desc": "Inject the gathered progress dict (skip ADO reads)."},
    "send_to": {"kind": "payload", "sets": "chatId",
                "desc": "Post to this chat id instead of the resolved meeting chat (test)."},
}


def _broker_plan(state):
    return (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _auth_suite(state):
    return (state.get_step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")


def _auto_failed_ids(state):
    """The Authenticator cases that FAILED in automation and were pre-assigned to the release
    owner by `ui_test_status` — flagged distinctly in the update (triage, not a manual run)."""
    return ((state.get_step("bug_bash", "ui_test_status").data or {})
            .get("auth") or {}).get("failed_case_ids") or []


def gather(state):
    """(ok, progress, detail) — live progress, or the injected `progress` mock."""
    inj = mock_input("progress", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    bp, asuite = _broker_plan(state), _auth_suite(state)
    if not bp or not asuite:
        return (False, None, "the Broker plan / Auth suite aren't ready (run the clone steps).")
    return BB.gather_progress(bp, BROKER_SUITE_NAME, T.AUTH_PLAN, asuite,
                              auto_failed_ids=_auto_failed_ids(state))


def plan_links(state):
    return [
        {"name": "Broker test plan", "url": T.plan_web_url(_broker_plan(state))},
        {"name": "Authenticator suite", "url": T.plan_web_url(T.AUTH_PLAN, _auth_suite(state))},
    ]


def build(state):
    if not state.ccd:
        return Blocked("bugbash_updates: no CCD set — can't title the Bug Bash.")
    chat_id = stored_chat_id(state)
    if not chat_id:
        return Blocked("bugbash_updates: the Bug Bash meeting chat isn't activated yet "
                       "(run activate_chat first).")

    ok, progress, detail = gather(state)
    if not ok:
        return Blocked(f"bugbash_updates: couldn't read test progress ({detail}).")

    month_year = schedule.parse_date(state.ccd).strftime("%B %Y")
    if BB.all_complete(progress):
        return Done(f"All {progress['total']} bug-bash tests are already complete — "
                    f"nothing to poll; ready for {month_year} bug bash sign-off.")

    content, mentions = BB.render_update(progress, month_year, plan_links(state))
    return NeedsSkill(
        tool="workiq_send_chat_message",
        payload={
            "chatId": chat_id,
            "content": content,
            "contentType": "html",
            "_mentions": mentions,     # [{id,upn,name}] — skill builds the <at> mention array
        },
        record_as=ID,
        summary=(f"Post the first {month_year} bug-bash update ({progress['done']}/"
                 f"{progress['total']} done) to the meeting chat, then provision the 2h "
                 f"update poller"),
        note=f"{progress['remaining']} test(s) remaining across {len(progress['owners'])} owner(s)",
        outbound=True,
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
        f"  • post → send decision.content (HTML) to decision.chatId via "
        f"`workiq_send_chat_message`. If decision.mentions is non-empty, include the "
        f"@mentions (contentType html, <at id=\"i\">Name</at> tags matching the mentions "
        f"array) so owners with remaining tests are pinged.\n"
        f"  • complete → every test is done: send decision.content (the completion summary) "
        f"to decision.chatId, then DEREGISTER this poller (`automation deregister --id "
        f"<this automation's id>`) and tell the owner the bash is ready to sign off "
        f"(bugbash_complete).\n"
        f"  • no_chat / error → surface briefly; nothing to send.\n"
        f"Silently journal: `journal --release {release} --source scout --kind automation "
        f"--text \"bugbash-poller: <decision>\"`.")
