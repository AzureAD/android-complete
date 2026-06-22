"""
Lightweight ICM OData API client for the MCP server.

Uses certificate-based client auth (``/api/cert`` endpoint).
The certificate ``DRICopilotOAuthCertificate`` is fetched from
Azure Key Vault ``msalandroidamlkeyvault`` using Managed Identity,
then presented as a TLS client certificate to the ICM OData API.
"""

import base64
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.keyvault.certificates import CertificateClient
from azure.keyvault.secrets import SecretClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from android_dri_mcp_server.content_sanitizer import sanitize_discussion_entry

logger = logging.getLogger("android_dri_mcp_server.icm_odata")

_ICM_ENDPOINT = "https://prod.microsofticm.com"
_KEYVAULT_URI = "https://msalandroidamlkeyvault.vault.azure.net/"
_CERT_NAME = "DRICopilotOAuthCertificate"

# Shared thread pool for parallel OData calls
_odata_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="icm-odata")


class IcmODataClient:
    """Fetches live incident data from the ICM OData ``/api/cert`` endpoint."""

    def __init__(self):
        self._credential = None
        self._cert_pem_path: Optional[str] = None
        self._key_pem_path: Optional[str] = None
        self._session: Optional[requests.Session] = None

    @property
    def credential(self):
        if self._credential is None:
            client_id = os.environ.get("AZURE_CLIENT_ID")
            if client_id:
                self._credential = ManagedIdentityCredential(client_id=client_id)
                logger.info("Using ManagedIdentityCredential (client_id=%s)", client_id)
            else:
                self._credential = DefaultAzureCredential(
                    additionally_allowed_tenants=["*"],
                    exclude_shared_token_cache_credential=True,
                )
                logger.info("Using DefaultAzureCredential")
        return self._credential

    def _ensure_cert(self) -> tuple[str, str]:
        """Download cert from Key Vault once and cache PEM paths."""
        if self._cert_pem_path and self._key_pem_path:
            if os.path.exists(self._cert_pem_path) and os.path.exists(self._key_pem_path):
                return self._cert_pem_path, self._key_pem_path

        logger.info("Downloading certificate '%s' from Key Vault", _CERT_NAME)
        secret_client = SecretClient(vault_url=_KEYVAULT_URI, credential=self.credential)
        cert_client = CertificateClient(vault_url=_KEYVAULT_URI, credential=self.credential)

        cert_ref = cert_client.get_certificate(_CERT_NAME)
        secret = secret_client.get_secret(cert_ref.properties.name)
        pfx_data = base64.b64decode(secret.value)

        private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, None)

        key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cert_pem = certificate.public_bytes(serialization.Encoding.PEM)

        cert_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem", prefix="icm_cert_")
        cert_file.write(cert_pem)
        cert_file.close()

        key_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem", prefix="icm_key_")
        key_file.write(key_pem)
        key_file.close()

        self._cert_pem_path = cert_file.name
        self._key_pem_path = key_file.name
        logger.info("Certificate written to %s, key to %s", self._cert_pem_path, self._key_pem_path)
        return self._cert_pem_path, self._key_pem_path

    def _get_session(self) -> requests.Session:
        """Return a persistent session with the client cert configured."""
        if self._session is None:
            cert_path, key_path = self._ensure_cert()
            self._session = requests.Session()
            self._session.cert = (cert_path, key_path)
            self._session.verify = True
        return self._session

    def warm_up(self):
        """Eagerly download cert and create the HTTP session (call at startup)."""
        self._get_session()
        logger.info("ICM OData client warmed up")

    def _request(self, uri: str, timeout: int = 15) -> Optional[requests.Response]:
        """Make a GET request with client certificate auth via persistent session."""
        session = self._get_session()
        resp = session.get(uri, timeout=timeout)
        return resp

    def _post(self, uri: str, json_body: dict, timeout: int = 30) -> requests.Response:
        """Make a POST request with client certificate auth via persistent session."""
        session = self._get_session()
        resp = session.post(uri, json=json_body, timeout=timeout)
        return resp

    def _patch(self, uri: str, json_body: dict, timeout: int = 30) -> requests.Response:
        """Make a PATCH request with client certificate auth via persistent session."""
        session = self._get_session()
        resp = session.patch(uri, json=json_body, timeout=timeout)
        return resp

    def post_discussion_entry(
        self, incident_id: str, text: str, submitted_by: str = "DRI Copilot"
    ) -> dict:
        """Post a new discussion entry to an IcM incident.

        Args:
            incident_id: The IcM incident ID.
            text: The discussion entry text (plain text or HTML).
            submitted_by: Display name for the author.

        Returns:
            A dict with 'success' (bool) and 'message' or 'error'.
        """
        if not incident_id or not incident_id.strip():
            return {"success": False, "error": "incident_id is required"}
        if not text or not text.strip():
            return {"success": False, "error": "text is required"}

        uri = f"{_ICM_ENDPOINT}/api/cert/incidents({incident_id})"
        body = {
            "NewDescriptionEntry": {
                "Text": text.strip(),
                "RenderType": "Plaintext",
            }
        }
        try:
            logger.info("ICM OData PATCH discussion entry to incident %s (%d chars)", incident_id, len(text))
            resp = self._patch(uri, body)
            logger.info("ICM OData PATCH response: status=%d", resp.status_code)
            if resp.status_code in (200, 201, 204):
                return {"success": True, "message": f"Discussion entry posted to IcM {incident_id}"}
            else:
                error_text = resp.text[:500] if resp.text else "(empty)"
                logger.error("ICM OData POST failed: status=%d body=%s", resp.status_code, error_text)
                return {"success": False, "error": f"HTTP {resp.status_code}: {error_text}"}
        except Exception as e:
            logger.error("ICM OData post_discussion_entry(%s) failed: %s", incident_id, e)
            return {"success": False, "error": str(e)}

    def get_incident(self, incident_id: str) -> Optional[dict]:
        """Fetch incident data by ID. Returns None on failure."""
        uri = f"{_ICM_ENDPOINT}/api/cert/incidents({incident_id})"
        try:
            logger.info("ICM OData calling %s", uri)
            resp = self._request(uri)
            logger.info("ICM OData response: status=%d", resp.status_code)
            if resp.status_code != 200:
                logger.error("ICM OData response body: %s", resp.text[:1000])
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("ICM OData get_incident(%s) failed: %s", incident_id, e)
            return None

    def get_incident_discussion(self, incident_id: str) -> Optional[dict]:
        """Fetch discussion/description entries for an incident."""
        uri = f"{_ICM_ENDPOINT}/api/cert/incidents({incident_id})/DescriptionEntries?/$inlinecount=allpages"
        try:
            resp = self._request(uri)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("ICM OData get_incident_discussion(%s) failed: %s", incident_id, e)
            return None

    def get_incident_rca(self, incident_id: str) -> Optional[dict]:
        """Fetch root cause analysis for an incident."""
        uri = f"{_ICM_ENDPOINT}/api/cert/incidents({incident_id})/RootCause"
        try:
            resp = self._request(uri)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("ICM OData get_incident_rca(%s) failed: %s", incident_id, e)
            return None

    def get_full_incident(self, incident_id: str) -> Optional[dict]:
        """Fetch incident data, discussion, and RCA in parallel.

        Returns a combined dict with keys: incident, discussion, rca.
        Returns None if the base incident fetch fails.
        """
        futures = {
            _odata_pool.submit(self.get_incident, incident_id): "incident",
            _odata_pool.submit(self.get_incident_discussion, incident_id): "discussion",
            _odata_pool.submit(self.get_incident_rca, incident_id): "rca",
        }

        results = {}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.error("get_full_incident: %s failed: %s", key, e)
                results[key] = None

        if results.get("incident") is None:
            return None

        return {
            "incident": results["incident"],
            "discussion": _extract_discussion_texts(results.get("discussion")),
            "rca": results.get("rca"),
        }


def _extract_discussion_texts(discussion_resp: Optional[dict]) -> list[str]:
    """Pull plain-text entries from the OData discussion response.

    Each entry goes through the 3-step sanitisation pipeline:
    1. HTML → text (preserving links as Markdown).
    2. Base64 inline images → ``[IMAGE_N]`` placeholders.
    3. Truncate to 10 000 characters.
    """
    if not discussion_resp:
        return []
    entries = discussion_resp.get("value", [])
    texts = []
    for entry in entries:
        raw = entry.get("Text") or entry.get("text") or ""
        if raw:
            text = sanitize_discussion_entry(raw)
            texts.append(text)
    return texts


def _format_live_incident(data: dict) -> dict:
    """Format the combined live incident data to a flat summary dict.

    The ICM OData ``/api/cert/`` endpoint returns PascalCase field names
    (e.g. ``Title``, ``Severity``, ``Status``).  This function maps them
    to the snake_case keys used elsewhere in the MCP server.
    """
    inc = data["incident"]

    # Custom fields are nested: CustomFieldGroups[*].CustomFields[*]
    custom_fields = {}
    for group in inc.get("CustomFieldGroups", []):
        for cf in group.get("CustomFields", []):
            name = cf.get("Name", "")
            val = cf.get("Value", "")
            if name and val not in (None, ""):
                custom_fields[name] = val

    # Source is a nested dict with CreatedBy
    source = inc.get("Source") or {}
    # AcknowledgementData is a nested dict
    ack_data = inc.get("AcknowledgementData") or {}

    return {
        "ticket_id": str(inc.get("Id", "")),
        "title": inc.get("Title", ""),
        "severity": inc.get("Severity"),
        "state": inc.get("Status", ""),
        "owning_team": inc.get("OwningTeamName", ""),
        "owning_tenant": inc.get("OwningTenantName", ""),
        "created": inc.get("CreateDate", ""),
        "created_by": source.get("CreatedBy", ""),
        "assigned_to": inc.get("OwningContactAlias", ""),
        "acknowledged": ack_data.get("IsAcknowledged", False),
        "impact_start": inc.get("ImpactStartDate", ""),
        "environment": (inc.get("IncidentLocation") or {}).get("Environment", ""),
        "keywords": inc.get("Keywords", ""),
        "type": inc.get("IncidentType", ""),
        "is_customer_impacting": inc.get("IsCustomerImpacting", False),
        "custom_fields": custom_fields,
        "discussion": data.get("discussion", []),
        "rca": data.get("rca"),
        "source": "icm-odata-live",
    }


# Module-level singleton
icm_client = IcmODataClient()
