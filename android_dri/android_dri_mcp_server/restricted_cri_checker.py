"""
Restricted CRI (Critical/Restricted Incident) access checker.

Mirrors DRICopilot's RestrictedCRIAccessChecker:
- Checks if an incident is restricted via Kusto
- Verifies user has access via team membership ACL
- Uses OBO-exchanged Kusto token for user-delegated queries

When OBO is disabled, falls back to allowing access (same as current behavior).
"""

import logging
from typing import Optional

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.response import KustoResponseDataSet

logger = logging.getLogger("android_dri_mcp_server.restricted_cri_checker")

_ICM_CLUSTER = "https://icmcluster.kusto.windows.net"
_ICM_DATABASE = "IcMDataWarehouse"


def _is_old_format_id(incident_id: str) -> bool:
    """IDs with <14 digits use the old format and cannot be restricted."""
    normalized = incident_id.lstrip("0") or "0"
    return len(normalized) < 14


def _build_kusto_client(kusto_token: str) -> KustoClient:
    """Build a Kusto client using a pre-acquired Bearer token."""
    kcsb = KustoConnectionStringBuilder.with_aad_user_token_authentication(
        connection_string=_ICM_CLUSTER,
        user_token=kusto_token,
    )
    return KustoClient(kcsb)


def is_restricted(incident_id: str, kusto_token: str) -> bool:
    """
    Check if an incident is restricted using user-delegated Kusto access.

    Args:
        incident_id: The incident ID to check.
        kusto_token: OBO-exchanged Kusto token for the user.

    Returns:
        True if the incident is restricted, False otherwise.
    """
    if _is_old_format_id(incident_id):
        return False

    query = f"""
        IncidentsSnapshotV2
        | where IncidentId == {incident_id}
        | where IsRestricted
        | project IncidentId
        | take 1
    """

    try:
        client = _build_kusto_client(kusto_token)
        response: KustoResponseDataSet = client.execute(_ICM_DATABASE, query)
        return response.primary_results[0].rows_count > 0
    except Exception as e:
        logger.error("Failed to check restriction for %s: %s", incident_id, e)
        # Fail closed — treat as restricted if we can't verify
        return True


def user_has_access(incident_id: str, user_email: str, kusto_token: str) -> bool:
    """
    Check if the user has access to a restricted incident via team membership.

    Mirrors DRICopilot's RestrictedCRIAccessChecker.has_access():
    - Checks if user is the owner
    - Checks if user is a team member (including virtual team mapping)
    - Checks if user is the creator
    - If incident is not actually restricted, always grants access

    Args:
        incident_id: The incident ID.
        user_email: The user's email (from JWT claims).
        kusto_token: OBO-exchanged Kusto token.

    Returns:
        True if the user has access, False otherwise.
    """
    if _is_old_format_id(incident_id):
        return True

    query = f"""
        let myUser = "{user_email}";
        let myIncidentId = {incident_id};
        let myUserName = tostring(split(myUser, "@")[0]);
        let IncidentMaterialized = materialize(
            IncidentsSnapshotV2 | where IncidentId == myIncidentId
        );
        let CurrentTeamId = toscalar(
            IncidentMaterialized | project OwningTeamId
        );
        let CurrentOwnerId = toscalar(
            IncidentMaterialized
            | where isnotempty(OwningContactId)
            | project ContactId = tolong(OwningContactId)
        );
        let CurrentOwner = Contacts
            | where ContactId == CurrentOwnerId
            | top 1 by ModifiedDate desc
            | summarize arg_max(ModifiedDate, EmailAddress) by ContactId;
        let Creator = IncidentMaterialized
            | where SourceCreatedBy =~ myUser or SourceCreatedBy =~ myUserName
            | project ModifiedDate, EmailAddress = myUser, ContactId = long(null);
        let NonRCriAccess = IncidentMaterialized
            | where not(IsRestricted) or isnull(IsRestricted)
            | project EmailAddress = myUser
            | lookup Contacts on $left.EmailAddress == $right.EmailAddress
            | summarize arg_max(ModifiedDate, EmailAddress) by ContactId;
        let StandardTeam = TeamsDedupView
            | where TeamId == CurrentTeamId
            | summarize by ReferencedTeamId;
        let TeamMembershipInner = TeamMembership
            | where TeamId == CurrentTeamId or TeamId in (StandardTeam)
            | summarize arg_max(TeamModifiedDate = ModifiedDate, Members)
            | extend Members = todynamic(Members)
            | mv-expand Members
            | extend ContactId = tolong(Members.ContactId)
            | lookup Contacts on $left.ContactId == $right.ContactId
            | summarize arg_max(ModifiedDate, EmailAddress) by ContactId;
        TeamMembershipInner
        | union (CurrentOwner)
        | union (NonRCriAccess)
        | union (Creator)
        | summarize arg_max(ModifiedDate, EmailAddress) by ContactId
        | where EmailAddress == myUser
    """

    try:
        client = _build_kusto_client(kusto_token)
        response: KustoResponseDataSet = client.execute(_ICM_DATABASE, query)
        has_access = response.primary_results[0].rows_count > 0
        if not has_access:
            logger.warning(
                "User %s denied access to restricted incident %s",
                user_email, incident_id,
            )
        return has_access
    except Exception as e:
        logger.error("Failed ACL check for %s on %s: %s", user_email, incident_id, e)
        # Fail closed — deny access if we can't verify
        return False


def check_incident_access(
    incident_id: str,
    user_email: Optional[str],
    kusto_token: Optional[str],
) -> bool:
    """
    High-level access check for an incident.

    If OBO is not available (no kusto_token), allows access (backward-compatible).
    If OBO is available, checks restriction status and user ACL.

    Args:
        incident_id: The incident ID.
        user_email: The user's email from JWT claims.
        kusto_token: OBO-exchanged Kusto token, or None if OBO is disabled.

    Returns:
        True if access is allowed, False if denied.
    """
    # If OBO is not available, fall back to current behavior (allow all)
    if not kusto_token or not user_email:
        return True

    # Old-format IDs cannot be restricted
    if _is_old_format_id(incident_id):
        return True

    # Check if incident is restricted
    if not is_restricted(incident_id, kusto_token):
        return True

    # Incident is restricted — check user access
    return user_has_access(incident_id, user_email, kusto_token)
