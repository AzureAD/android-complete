"""Verify the sent invitation's exact meeting thread, then record activate_chat.

The optional --chat-id is an assertion, never implicit manual approval.
--chat-link previews an explicit owner-attested recovery, without Graph access.
Already-completed bindings cannot be silently replaced; failures leave state unchanged.
"""
from __future__ import annotations

import json

from orchestrator import cli_common as C, delivery
from orchestrator.outcomes import Done
from orchestrator.transitions import TransitionIntent
from steps.bug_bash import activate_chat, send_invite, notify_native_auth
from tools import bugbash_meeting


def cmd_record_bugbash_chat(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    chat_id = (args.chat_id or "").strip()
    chat_link = args.chat_link
    guard = orch.step_action_guard("bug_bash", "activate_chat")
    if isinstance(guard, Done):
        stored = activate_chat.stored_chat_id(orch.context("bug_bash", "activate_chat"))
        binding = orch.context("bug_bash", "activate_chat").evidence.step(
            "bug_bash", "activate_chat").data.get("meeting") or {}
        if (stored and (not chat_id or stored == chat_id)
                and (not chat_link or binding.get("chat_link") == chat_link)):
            print(f"Already bound to meeting chat {stored}; no changes.")
            return 0
        print("Chat step is terminal but this binding is stale/different. Owner-reviewed reopen "
              "of activate_chat is required; never silently replace a completed binding.")
        return 1
    if guard is not None:
        print(guard.reason)
        return 1
    try:
        permit = orch.authorize_outcome(TransitionIntent.PREPARE, "bug_bash", "activate_chat")
        if getattr(args, "as_of", None):
            raise ValueError("Chat recording requires the current clock, not --as-of")
        if chat_id and not bugbash_meeting.meeting_chat_id(chat_id):
            raise ValueError("Invalid meeting chat ID")
        invite = send_invite.delivered_invite(orch.context(
            "bug_bash", "activate_chat", permit=permit))
        if chat_link is not None:
            if any(getattr(args, key, None) is not None for key in (
                    "profile_observation", "event_observation", "chat_observation")):
                raise ValueError("Choose native observations or owner-attested --chat-link, not both")
            meeting = bugbash_meeting.manual_candidate(invite, chat_link)
            if chat_id and chat_id != meeting["chat_id"]:
                raise ValueError("Supplied chat ID differs from the provided chat link")
            if not args.confirm_activated:
                if args.review_hash or args.approved_by or args.reason:
                    raise ValueError("Manual recording requires explicit --confirm-activated")
                print(json.dumps({
                    "mode": "owner_attested", "invitation": invite, **meeting,
                    "permission_to_record": False, "permission_to_send": False,
                    "warning": "Owner must confirm they opened THIS invitation's Chat pane, "
                               "activated its chat and copied this link. Current event/attendees "
                               "and chat access are not independently verified; confirmer identity "
                               "is not independently authenticated. This records no "
                               "message delivery and approves no release gate.",
                }, indent=2))
                return 0
            if (not args.approved_by or args.approved_by.casefold() != invite["owner"].casefold()
                    or not args.reason or not args.reason.strip()):
                raise ValueError("Manual chat binding requires the release owner's explicit confirmation/reason")
            if args.review_hash != meeting["review_hash"]:
                raise ValueError("Manual chat review changed; preview and obtain fresh owner confirmation")
            meeting.update(approved_by=args.approved_by, reason=args.reason.strip(), activated=True)
        else:
            if args.confirm_activated or args.review_hash or args.approved_by or args.reason:
                raise ValueError("Manual confirmation requires --chat-link and its exact reviewed hash")
            meeting = bugbash_meeting.resolve(
                invite, profile_observation=getattr(args, "profile_observation", None),
                event_observation=getattr(args, "event_observation", None),
                chat_observation=getattr(args, "chat_observation", None))
        if not activate_chat.meeting_matches(invite, meeting, meeting.get("chat_id")):
            raise ValueError("Resolved meeting has missing or mismatched event/chat evidence")
        if chat_id and chat_id != meeting["chat_id"]:
            raise ValueError("Supplied chat does not belong to the acknowledged invitation")
    except bugbash_meeting.ConnectorEvidenceRequired as exc:
        print(json.dumps({
            "status": "needs-connector-evidence",
            "event_id": invite["event_id"],
            "requests": exc.requests,
            "followup_args": ["record-bugbash-chat", "--release", args.release],
            "followup_flags": {
                "--profile-observation": getattr(args, "profile_observation", None) or "<profile-envelope.json>",
                "--event-observation": getattr(args, "event_observation", None) or "<event-envelope.json>",
                "--chat-observation": getattr(args, "chat_observation", None) or "<chat-envelope.json>",
                **({"--chat-id": chat_id} if chat_id else {}),
            },
            "next": ("Use the explicit owner-confirmed recovery described by missing_capability, "
                     "or keep this held; do not retry unchanged evidence or mark activate_chat complete."
                     if exc.missing_capability else
                     "Gather the requested structured read observations, save their envelopes, "
                     "then rerun with the follow-up flags. The exact chat request is available "
                     "after validating the current event. Do not mark activate_chat complete yet."),
            "missing_capability": exc.missing_capability,
            "completed": False, "permission_to_record": False, "permission_to_send": False,
            "provenance": "Tool-host-supplied observations, not cryptographic identity or write approval.",
        }, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OSError) as exc:
        print(f"Chat binding blocked: {exc}")
        print("Do not wait/retry unchanged access errors. Offer owner-confirmed --chat-link "
              "recovery now, or owner-reviewed manual posting with bugbash-progress. "
              "Never create another invitation or infer delivery.")
        return 1
    orch.apply_outcome(
        permit,
        Done(f"Bug Bash chat {'owner-confirmed' if chat_link else 'native-connector-verified'} "
             f"for event {invite['event_id']}: {meeting['chat_id']}", by="scout"),
        data={
            "chat_id": meeting["chat_id"],
            "invite": invite,
            "meeting": meeting,
            "confirmed_at" if chat_link else "verified_at": delivery.now_iso(),
            "gate_preconditions": activate_chat.binding_preconditions(
                orch.context("bug_bash", "activate_chat", permit=permit), invite),
        },
    )
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"[ok] activate_chat: {'owner-attested' if chat_link else 'native-connector-verified'} "
           f"event-bound chat stored ({meeting['chat_id']})", kind="step")
    return 0


def cmd_record_nativeauth_notify(args):
    """Legacy terminal replay only; a bare engineer is not delivery evidence."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    completed = orch.completed_step_outcome("bug_bash", "notify_native_auth")
    if completed and notify_native_auth.notified_engineer(
            orch.context("bug_bash", "notify_native_auth")):
        print(completed.note)
        return 0
    print("Use notification prepare/claim/result with a verified engineer; no delivery may be inferred.")
    return 1


def register(sub):
    p = sub.add_parser("record-bugbash-chat",
                       help="Store the resolved Bug Bash meeting chat id (Phase-3 activate_chat)")
    p.add_argument("--release", required=True)
    p.add_argument("--chat-id", default=None, dest="chat_id",
                   help="Optional assertion; must equal the thread resolved from the sent invitation")
    p.add_argument("--chat-link", help="Preview owner-attested recovery using the exact meeting's Teams link")
    p.add_argument("--profile-observation", help="Structured workiq_get_my_profile observation envelope file")
    p.add_argument("--event-observation", help="Structured workiq_get_event observation for the exact invitation")
    p.add_argument("--chat-observation", help="Structured microsoft_teams-GetChat observation for its exact thread")
    p.add_argument("--confirm-activated", action="store_true",
                   help="Record only after the owner explicitly confirms this invitation's chat/link")
    p.add_argument("--review-hash", help="Exact manual candidate hash approved by the owner")
    p.add_argument("--approved-by", help="Confirming release owner's email; not independently authenticated")
    p.add_argument("--reason", help="Owner's explicit activation/link confirmation")
    p.set_defaults(func=cmd_record_bugbash_chat)

    n = sub.add_parser("record-nativeauth-notify",
                       help="Record that the Native Auth release engineer was notified the bug bash is ready")
    n.add_argument("--release", required=True)
    n.add_argument("--engineer", default=None,
                   help="Alias or UPN of the notified Native Auth RE. Omit to hold for the owner.")
    n.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    n.set_defaults(func=cmd_record_nativeauth_notify)
