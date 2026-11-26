"""Owner configuration command (`set-owner`)."""
from __future__ import annotations

from orchestrator import cli_common as C
from tools import checks


def cmd_set_owner(args):
    """Set/change the release owner (who reminders are emailed to)."""
    st = C.load_state(args.runs_root, args.release)
    previous_owner = st.owner_email
    st.owner_email = (getattr(args, "owner_email", None) or checks.current_az_user())
    if (previous_owner or "").casefold() != (st.owner_email or "").casefold():
        st.readiness_items = {}
    if getattr(args, "owner_name", None):
        st.owner_name = args.owner_name
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log("owner_set", owner=st.owner_email)
    if not st.owner_email:
        print("Couldn't resolve an owner (no --owner-email and az user unavailable).")
        return 1
    who = f"{st.owner_name + ' ' if st.owner_name else ''}{st.owner_email}"
    print(f"Release {args.release} owner set to {who}.")
    return 0


def register(sub):
    so = sub.add_parser("set-owner", help="Set/change the release owner (who reminders are emailed to)")
    so.add_argument("--release", required=True)
    so.add_argument("--owner-email", default=None, help="Owner email (default: signed-in az user)")
    so.add_argument("--owner-name", default=None, help="Owner display name (optional)")
    so.set_defaults(func=cmd_set_owner)
