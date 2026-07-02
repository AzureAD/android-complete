"""
Restricted CRI (Critical/Restricted Incident) access checker.

Mirrors DRICopilot's RestrictedCRIAccessChecker:
- Checks if an incident is restricted via Kusto
- Verifies user has access via team membership ACL
- Uses OBO-exchanged Kusto token for user-delegated queries

When OBO is disabled, falls back to allowing access (same as current behavior).
"""

import logging
import re
from enum import Enum
from typing import Optional

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.response import KustoResponseDataSet

logger = logging.getLogger("android_dri_mcp_server.restricted_cri_checker")

_ICM_CLUSTER = "https://icmcluster.kusto.windows.net"
_ICM_DATABASE = "IcMDataWarehouse"


class AccessResult(str, Enum):
    """Outcome of an incident access check."""

    ALLOWED = "allowed"        # OBO disabled, non-restricted, or user authorized
    DENIED = "denied"          # verified: incident restricted and user not authorized
    UNVERIFIED = "unverified"  # could not verify access (Kusto/permission failure)


def normalize_incident_id(incident_id: str) -> str:
    """Extract the numeric incident ID.

    Callers may pass a sub-resource suffix (e.g. "12345/attachments") or other
    decoration. Only the leading path segment's digits identify the incident, so
    everything else is stripped. Returns "" if no digits are present. Because the
    result is digits-only, it is also safe to interpolate directly into KQL.
    """
    if not incident_id:
        return ""
    head = str(incident_id).split("/", 1)[0].strip()
    return re.sub(r"\D", "", head)


def _escape_kql_string(value: str) -> str:
    """Escape a value for safe use inside a double-quoted KQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _is_old_format_id(incident_id: str) -> bool:
    """IDs with <14 digits use the old format and cannot be restricted."""
    normalized = incident_id.lstrip("0") or "0"
    return len(normalized) < 14


def _build_kusto_client(token: str, is_app_token: bool = False) -> KustoClient:
    """Build a Kusto client from a pre-acquired Bearer token.

    User (OBO) tokens use user-token auth; service-identity tokens use
    application-token auth.
    """
    if is_app_token:
        kcsb = KustoConnectionStringBuilder.with_aad_application_token_authentication(
            connection_string=_ICM_CLUSTER,
            application_token=token,
        )
    else:
        kcsb = KustoConnectionStringBuilder.with_aad_user_token_authentication(
            connection_string=_ICM_CLUSTER,
            user_token=token,
        )
    return KustoClient(kcsb)


def _get_service_kusto_token() -> Optional[str]:
    """Return a Kusto token for the server's service identity, or None.

    Fallback only — see _run_kusto_query.
    """
    try:
        from android_dri_mcp_server.obo_exchanger import obo_exchanger
        return obo_exchanger.get_service_kusto_token()
    except Exception as e:
        logger.error("Failed to obtain service Kusto token for fallback: %s", e)
        return None


def _run_kusto_query(query: str, user_kusto_token: str) -> KustoResponseDataSet:
    """Execute a query against IcMDataWarehouse.

    OBO (the user's delegated token) is always the primary path. Only if the
    user's query fails — e.g. the user lacks IcMDataWarehouse access — do we fall
    back to the server's own service identity, which is granted read access. The
    access decision is still computed from the real user's email/team membership,
    so this changes only *which identity runs the lookup*, not *what is checked*.
    Raises the original error if the fallback is unavailable or also fails.
    """
    try:
        client = _build_kusto_client(user_kusto_token)
        return client.execute(_ICM_DATABASE, query)
    except Exception as user_err:
        service_token = _get_service_kusto_token()
        if not service_token:
            raise
        logger.warning(
            "OBO Kusto query failed (%s); falling back to service identity", user_err
        )
        client = _build_kusto_client(service_token, is_app_token=True)
        return client.execute(_ICM_DATABASE, query)


def is_restricted(incident_id: str, kusto_token: str) -> bool:
    """
    Check if an incident is restricted using user-delegated Kusto access.

    Args:
        incident_id: The incident ID to check.
        kusto_token: OBO-exchanged Kusto token for the user.

    Returns:
        True if the incident is restricted, False otherwise.
    """
    incident_id = normalize_incident_id(incident_id)
    if not incident_id or _is_old_format_id(incident_id):
        return False

    query = f"""
        IncidentsSnapshotV2
        | where IncidentId == {incident_id}
        | where IsRestricted
        | project IncidentId
        | take 1
    """

    # Let query failures propagate — check_incident_access maps them to UNVERIFIED.
    response = _run_kusto_query(query, kusto_token)
    return response.primary_results[0].rows_count > 0


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
    incident_id = normalize_incident_id(incident_id)
    if not incident_id or _is_old_format_id(incident_id):
        return True

    query = f"""
        let myUser = "{_escape_kql_string(user_email)}";
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

    # Let query failures propagate — check_incident_access maps them to UNVERIFIED.
    response = _run_kusto_query(query, kusto_token)
    has_access = response.primary_results[0].rows_count > 0
    if not has_access:
        logger.warning(
            "User %s denied access to restricted incident %s",
            user_email, incident_id,
        )
    return has_access


def check_incident_access(
    incident_id: str,
    user_email: Optional[str],
    kusto_token: Optional[str],
) -> AccessResult:
    """
    High-level access check for an incident.

    Returns an AccessResult:
    - ALLOWED     — OBO not enabled, incident non-restricted, or the user is
                    authorized.
    - DENIED      — the incident is restricted and the membership check ran
                    successfully but the user is not authorized.
    - UNVERIFIED  — access could not be verified (e.g. the Kusto restriction/ACL
                    query failed on both the user's OBO token and the service
                    fallback). Treated as no-access, but distinguished so callers
                    don't present an infra failure as an authorization denial.

    Args:
        incident_id: The incident ID.
        user_email: The user's email from JWT claims.
        kusto_token: OBO-exchanged Kusto token, or None if OBO is disabled.
    """
    # If OBO is not available, fall back to current behavior (allow all)
    if not kusto_token or not user_email:
        return AccessResult.ALLOWED

    # Normalize once up front (strips sub-resource suffixes like "/attachments")
    incident_id = normalize_incident_id(incident_id)

    # Non-numeric / old-format IDs cannot be restricted
    if not incident_id or _is_old_format_id(incident_id):
        return AccessResult.ALLOWED

    # Check if the incident is restricted
    try:
        restricted = is_restricted(incident_id, kusto_token)
    except Exception as e:
        logger.error("Could not verify restriction status for %s: %s", incident_id, e)
        return AccessResult.UNVERIFIED

    if not restricted:
        return AccessResult.ALLOWED

    # Incident is restricted — verify the user's access
    try:
        authorized = user_has_access(incident_id, user_email, kusto_token)
    except Exception as e:
        logger.error("Could not verify ACL for %s on %s: %s", user_email, incident_id, e)
        return AccessResult.UNVERIFIED

    return AccessResult.ALLOWED if authorized else AccessResult.DENIED
