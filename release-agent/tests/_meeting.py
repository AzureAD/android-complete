"""Synthetic acknowledged invitation and verified chat fixtures; no network."""
from orchestrator import delivery as D, schedule
from orchestrator.state import StepState
from steps.bug_bash.send_invite import delivered_invite


def seed_invite(st, chat_id=None):
    st.owner_email = st.owner_email or "owner@example.test"
    item = D.descriptor(st, "step:bug_bash.send_invite:once",
                        {"kind": "step", "phase": "bug_bash", "step": "send_invite",
                         "release_matches": {"owner_email": st.owner_email, "ccd": st.ccd}},
                        "workiq_create_event",
                        {"subject": f"{schedule.target_month_label(st)} Release Bug Bash",
                         "attendees": ["team@example.test"], "start": "2026-09-11T09:00:00",
                         "end": "2026-09-11T11:00:00", "timeZone": "America/Los_Angeles"},
                        {"kind": "step", "record_as": "send_invite"})
    st.notification_deliveries[item["id"]] = {
        "descriptor": item, "status": "sent", "completion": {"status": "applied"},
        "attempts": [{"id": "create-execution", "owner": "test-worker", "status": "sent",
                      "hash": item["hash"], "receipt": {"id": "event-1"}}]}
    st.set_step("bug_bash", "send_invite", StepState(status="done", data={
        "_execution": {"id": "create-execution", "notification_id": item["id"]}}))
    invite = delivered_invite(st)
    if chat_id:
        st.set_step("bug_bash", "activate_chat", StepState(status="done", data={
            "chat_id": chat_id, "invite": invite, "meeting": meeting(invite, chat_id)}))
    return invite


def meeting(invite, chat_id="19:meeting_X@thread.v2"):
    return {"chat_id": chat_id, "event_id": invite["event_id"],
            "join_url": "https://teams.microsoft.com/l/meetup-join/test",
            "online_meeting_id": "online-1", "subject": invite["subject"]}
