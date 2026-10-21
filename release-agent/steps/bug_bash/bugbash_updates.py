"""Step: `bugbash_updates` — post the FIRST bug-bash progress update and start the recurring
poller (Phase 3, bug_bash).

Fire-and-continue (like the localization noon trigger): reaching this step posts the first
progress update to the Bug Bash meeting chat and provisions the `bugbash-update-poller`
automation; the step itself completes so the phase can proceed. The poller then posts an
update every 3 hours (config/automations.yaml) during working hours (09:00–18:00 America/Los_Angeles, weekdays,
skipping US holidays) via `post-bugbash-update`, and tears itself down when either every
test is complete OR the owner attests `bugbash_complete`.
The first post is prepared when this step is reached, not after an interval or at the
meeting start. It is sent through the normal approved delivery flow; only confirmed
delivery/completion provisions the poller. The working-window gate applies to its ticks,
not this first post. If all work is already complete, no first post/poller is needed.

Each update is grouped by the release test-point Tester (set by distribute_tests):
owners with remaining tests (not-run, failed, or blocked — failed/blocked surfaced, not
hidden) are @mentioned. Always list every workload case, including completed cases, with
its source-plan product ([Broker] / [Authenticator]), link and live state. Unresolved
work comes first; Passed/N/A rows follow. Each row's leading icon alone shows the outcome:
checkmark for Passed, dash for N/A, cross for Failed, stop sign for Blocked, square for
Not run. A single report legend explains the icons; no repeated trailing status text.
Automation triage keeps a short context note, not a second status or product label.
Owners who finished retain their full list and an "all completed" line (no mention).
A test counts as done only when it passed
(or is N/A). Broker and Authenticator cases actually written Failed by `ui_test_status` are shown
distinctly as 'triage', not a manual test to run. Live assignments identify the current
owner; a failed reassignment must not invent an owner change.

The header includes the Broker plan, Authenticator suite, MRWP ECS/Local runs, the
Authenticator UI-test pipeline and the test-account portal. Run links use the latest recorded RC's run_id fields
(auth.test, not the upstream APK build), with the existing organization-specific URL
helpers. Missing run IDs block preparation rather than produce guessed/stale links.

The denominator covers manual/triage work, not every case in the Authenticator suite.
Use the completed fill's automated-case classification (the same source as distribution)
to exclude automation-only Auth cases, regardless of their current ADO owner/outcome.
Keep applied automation failures as explicit triage; keep genuine manual Passed/N/A
results in the done count. No internal assignment list or cached owner totals are used.
Broker UI triage reads the completed fill's originally failed points in its exact UI
suite, matching point/case/configuration IDs. Multiple configurations count as one case;
all tracked failing configurations must resolve. Successful automation and unexecuted
configurations outside this triage set do not affect totals or resolution.

Depends on: clone_plans_broker (Broker plan id), clone_plans_auth (Auth suite id),
ui_test_status (completed automation classification), activate_chat (meeting chat id).
Blocks if the chat hasn't been activated or the completed fill is missing/stale.
Missing member evidence requests a read-only Teams ListChatMembers call before preparing
any notification. members_file must contain its fresh owner-bound M365 observation envelope
(or the native workiq_get_chat envelope for this exact meeting), never bare legacy JSON.
If assigned UPNs differ from member emails, fetch the exact directory users through
the discovered MCP or verified native WorkIQ CLI; bind GUIDs to actual members.

Mock knobs (mocks.local.yaml / tests):
  progress : inject the gathered progress dict (skip the live ADO reads); include every
             owner's tests with state and products (["Broker"], ["Authenticator"], or
             both for a shared case), including all completed rows.
  people   : verified Teams people {upn: {id: Entra GUID, name}} for offline rendering.
  send_to  : redirect the post to this chat id for testing.
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext, thaw

import json
import re

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked, Done
from steps.lib.mockctx import MISSING
from tools import bugbash as BB
from tools.distribution import ConnectorObservationRequired
from tools import testplans as T
from tools.coordinates import coords
from tools.pipelines.auth_app import auth_build_url
from steps.build_verify._common import build_url, latest_rc, valid_id
from steps.bug_bash.activate_chat import stored_chat_id, chat_state_matches
from steps.bug_bash.ui_results import completed_result

ID = "bugbash_updates"
KIND = "scout"
NOTIFICATION = True

BROKER_SUITE_NAME = "Manual Tests (Android Broker)"

MOCKABLE = {
    "progress": {"kind": "input", "desc": "Full progress including completed test rows with products/state (skip ADO reads)."},
    "people": {"kind": "input", "desc": "Verified Teams people {upn:{id: AAD GUID,name}} for offline mention tests."},
    "send_to": {"kind": "payload", "sets": "chatId",
                "desc": "Post to this chat id instead of the resolved meeting chat (test)."},
}


def _broker_plan(context):
    return (context.evidence.step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _auth_suite(context):
    return (context.evidence.step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")


def _auto_failed_ids(context):
    """Applied automation failures, independent of whether owner reassignment succeeded."""
    return completed_result(context)["auth"]["failed_case_ids"]


def gather(context):
    """(ok, progress, detail) — live progress, or the injected `progress` mock."""
    inj = context.input("progress", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    bp, asuite = _broker_plan(context), _auth_suite(context)
    if not bp or not asuite:
        return (False, None, "the Broker plan / Auth suite aren't ready (run the clone steps).")
    try:
        result = completed_result(context)
        if result["auth"]["target"].get("layout"):
            from tools import distribution as D
            context.services.testplans.validate_release_targets(
                context.release.release_id, context.release.ccd, result["binding"],
                expected_names=D.release_target_names(
                    context.evidence.step("bug_bash", "clone_plans_broker").data,
                    context.evidence.step("bug_bash", "clone_plans_auth").data))
    except ValueError as exc:
        return False, None, str(exc)
    return context.services.testplans.gather_progress(bp, BROKER_SUITE_NAME, T.AUTH_PLAN, asuite,
                              auto_failed_ids=result["auth"]["failed_case_ids"],
                              auth_automated_ids=result["auth"]["automated_case_ids"],
                              broker_ui_result=result["broker"], auth_ui_result=result["auth"],
                              **({"monthly_result": result["monthly"]} if "monthly" in result else {}))


def plan_links(context):
    """Header destinations from the current release targets and recorded RC run IDs."""
    rc = latest_rc(context)
    runs = [
        ("MRWP · ECS run", (rc.get("ecs") or {}).get("run_id"), build_url),
        ("MRWP · Local run", (rc.get("local") or {}).get("run_id"), build_url),
        ("Auth pipeline", ((rc.get("auth") or {}).get("test") or {}).get("run_id"), auth_build_url),
    ]
    for name, run_id, _ in runs:
        if not valid_id(run_id):
            raise ValueError(f"Missing/invalid current RC run ID for {name}; refresh the owning verification step")
    from tools import auth_partition as A
    auth_data = thaw(context.evidence.step("bug_bash", "clone_plans_auth").data)
    if A.is_partition(auth_data):
        A.target(auth_data)
    monthly_links = []
    if "monthly" in auth_data:
        from tools import auth_monthly as M
        monthly_links = M.links(completed_result(context)["monthly"]["target"])
    return [
        {"name": "Broker test plan", "url": T.plan_web_url(_broker_plan(context))},
        {"name": "Authenticator suite", "url": T.plan_web_url(T.AUTH_PLAN, _auth_suite(context))},
    ] + [{"name": name, "url": url_for(run_id)} for name, run_id, url_for in runs] + [
        {"name": "Get test accounts", "url": coords.link("test_accounts")},
    ] + monthly_links


def poll_interval_hours(context, config_path):
    """Use the provisioning interval for notification checkpoints/expiry too."""
    specs = [spec for spec in context.services.assets.automation_definitions(config_path) if spec.get("slug") == "bug-bash-update-poller"]
    interval = specs[0].get("every") if len(specs) == 1 else None
    match = re.fullmatch(r"([1-9][0-9]*) hours?", interval) if isinstance(interval, str) else None
    hours = int(match[1]) if match else 0
    if not hours or hours > 24 or 24 % hours:
        raise ValueError("bug-bash-update-poller every must be a whole-hour interval dividing 24 in automations.yaml")
    return hours


def prepare_update(context, progress, members_file=None):
    """One resolved transport payload shared by initial and periodic progress posts."""
    chat_id = stored_chat_id(context)
    if not chat_id:
        return False, None, "Missing/stale meeting binding"
    people = context.input("people", MISSING) if members_file is None else MISSING
    if people is MISSING:
        observation = None
        if members_file is not None:
            try:
                observation = context.services.assets.json_file(members_file)
            except (OSError, ValueError) as exc:
                return False, None, f"Cannot read meeting-member observation: {exc}"
        ok, people, detail = context.services.identities.resolve_mention_people(
            chat_id, progress.get("owners") or {}, member_observation=observation,
            owner=context.release.owner_email)
        if not ok:
            return False, None, detail
    try:
        content, mentions = BB.render_update(progress, schedule.target_month_label(context.release) or "Bug Bash",
                                             plan_links(context), people)
    except ValueError as exc:
        return False, None, str(exc)
    return True, {"chatId": chat_id, "content": content, "contentType": "html", "mentions": mentions}, ""


@dataclass(frozen=True)
class BuildParameters:
    members_file: str | None = None


PARAMETERS = {"build": BuildParameters}


def build(context: StepContext[BuildParameters]):
    members_file = context.parameters.members_file
    if not context.release.ccd:
        return Blocked("bugbash_updates: no CCD set — can't title the Bug Bash.")
    chat_id = stored_chat_id(context)
    if not chat_id:
        return Blocked("bugbash_updates: meeting chat binding is missing/stale "
                       "(run activate_chat against the current invitation).")

    ok, progress, detail = gather(context)
    if not ok:
        return Blocked(f"bugbash_updates: couldn't read test progress ({detail}).")

    month_year = schedule.target_month_label(context.release)
    if BB.all_complete(progress):
        if not progress["total"]:
            return Done(f"No manual or triage bug-bash work remains for {month_year}; nothing to poll.")
        return Done(f"All {progress['total']} bug-bash tests are already complete — "
                    f"nothing to poll; ready for {month_year} bug bash sign-off.")

    try:
        ok, payload, detail = prepare_update(context, progress, members_file)
    except ConnectorObservationRequired as exc:
        return NeedsSkill(
            tool=exc.tool,
            payload={**exc.arguments, "_connector_reads": exc.requests,
                     "_followup": "Append the requested fresh owner-bound directory envelopes "
                                  "as directory_observations on the members-file envelope; rerun "
                                  "step-action with --param members_file=<path>. Do not record completion."},
            summary="Resolve exact pending tester UPNs to verified meeting-member user IDs",
            note=str(exc))
    if not ok:
        if members_file is None and context.input("people", MISSING) is MISSING:
            request = BB.mention_read_request(chat_id, context.release.owner_email)
            return NeedsSkill(
                tool="microsoft_teams-ListChatMembers",
                payload={"chatId": chat_id, "_connector_reads": [request],
                         "_followup": "Save the fresh owner-bound observation envelope; rerun "
                                      "step-action with --param members_file=<path>. Do not record completion."},
                summary="Gather complete meeting-member identities before preparing the first update",
                note=detail)
        return Blocked(f"bugbash_updates: {detail}")
    return NeedsSkill(
        tool="workiq_send_chat_message",
        payload={
            **payload,
            "_automation": {"on_demand": "bug-bash-update-poller"},
        },
        record_as=ID,
        summary=(f"Post the first {month_year} bug-bash update ({progress['done']}/"
                 f"{progress['total']} done) to the meeting chat, then provision the configured "
                 f"update poller"),
        note=f"{progress['remaining']} test(s) remaining across {len(progress['owners'])} owner(s)",
        outbound=True,
        notification={"state_matches": chat_state_matches(context)},
    )


def automation_prompt(release: str, spec: dict) -> str:
    """Bespoke instruction using the provisioning spec's configured interval."""
    if not spec.get("interval"):
        return ""
    return (
        f"Release {release} — bug-bash update poller (every {spec['interval']}, working hours).\n"
        f"Run `post-bugbash-update --release {release}` and act on the printed decision:\n"
        f"  • off_hours / weekend / holiday → send nothing (outside 09:00–18:00 LA on a "
        f"working day).\n"
        f"  • post / complete → use source pending for the staged notifications and the "
        f"shared claim/result protocol. Never send raw decision.content. Only confirmed "
        f"delivery of the final summary records poll_complete; known failed sends remain "
        f"retryable. Initial trigger-step completion does not end this phase-owned poller.\n"
        f"  • no_chat / error / stopped → nothing to send; always perform cleanup.\n"
        f"If member evidence is needed, execute the printed connector_reads request through "
        f"microsoft_teams-ListChatMembers (or workiq_get_chat with chatId). Save the actual "
        f"complete response in a version:1 observation envelope with the exact tool/arguments, "
        f"account equal to the release owner, and an offset observed_at timestamp; rerun "
        f"with --members-file <path> within 15 minutes. Bare legacy member JSON is not fresh evidence. "
        f"If an assigned UPN differs from the member email (or email is missing), execute the exact "
        f"directory requests through the verified structured fetch route and append their fresh "
        f"envelopes as directory_observations. Discover the current user's MCP/native CLI; "
        f"do not use Ask or assume a removed custom remote registration removed all fetch support. "
        f"Never invent member identities or omit mentions to bypass a resolution failure.\n"
        f"Silently journal: `journal --release {release} --source scout --kind automation "
        f"--text \"bugbash-poller: <decision>\"`.")
