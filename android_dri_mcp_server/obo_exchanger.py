"""
OBO (On-Behalf-Of) token exchanger for the MCP server.

Mirrors the pattern from DRICopilot's OBOTokenExchanger.cs:
fetches a certificate from Key Vault, builds a ConfidentialClientApplication
with that certificate, and exchanges the user's access token for a
downstream-resource token (e.g., Kusto).

Environment variables:
    OBO_ENABLED             – "true" to enable OBO exchange (default: "false")
    OBO_CLIENT_ID           – App registration client ID (confidential client)
    OBO_CERT_NAME           – Certificate name in Key Vault
    OBO_KEYVAULT_URI        – Key Vault URI (default: same as ICM cert vault)
    OBO_TENANT_ID           – Tenant ID (default: Microsoft tenant)
"""

import base64
import logging
import os
import threading
from typing import Optional

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
from azure.keyvault.certificates import CertificateClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
import msal

logger = logging.getLogger("android_dri_mcp_server.obo_exchanger")

_DEFAULT_TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
_DEFAULT_KEYVAULT = "https://msalandroidamlkeyvault.vault.azure.net/"
_DEFAULT_CLIENT_ID = "49b5a60c-3719-4444-8805-be7880a928c3"
_DEFAULT_CERT_NAME = "DRICopilotOAuthCertificate"

# Kusto scope for IcM Data Warehouse access
KUSTO_SCOPE = "https://kusto.kusto.windows.net/.default"


class OBOTokenExchanger:
    """
    Exchanges a user's Bearer token for a downstream token via OBO flow.

    Uses a certificate from Key Vault as the confidential client credential,
    matching DRICopilot's C# implementation (WithCertificate + sendX5C: true).
    """

    def __init__(self):
        self._enabled = os.environ.get("OBO_ENABLED", "false").lower() == "true"
        self._tenant_id = os.environ.get("OBO_TENANT_ID", _DEFAULT_TENANT)
        self._client_id = os.environ.get("OBO_CLIENT_ID", _DEFAULT_CLIENT_ID)
        self._cert_name = os.environ.get("OBO_CERT_NAME", _DEFAULT_CERT_NAME)
        self._keyvault_uri = os.environ.get("OBO_KEYVAULT_URI", _DEFAULT_KEYVAULT)

        self._credential: Optional[DefaultAzureCredential] = None
        self._private_key_pem: Optional[bytes] = None
        self._cert_thumbprint: Optional[str] = None
        self._msal_app: Optional[msal.ConfidentialClientApplication] = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _get_credential(self):
        if self._credential is None:
            client_id = os.environ.get("AZURE_CLIENT_ID")
            if client_id:
                self._credential = ManagedIdentityCredential(client_id=client_id)
            else:
                self._credential = DefaultAzureCredential(
                    additionally_allowed_tenants=["*"],
                    exclude_shared_token_cache_credential=True,
                )
        return self._credential

    def _ensure_cert(self) -> tuple[bytes, str]:
        """Download certificate from Key Vault and extract private key + thumbprint."""
        if self._private_key_pem and self._cert_thumbprint:
            return self._private_key_pem, self._cert_thumbprint

        with self._lock:
            # Double-check after acquiring lock
            if self._private_key_pem and self._cert_thumbprint:
                return self._private_key_pem, self._cert_thumbprint

            logger.info("Downloading certificate '%s' from Key Vault for OBO", self._cert_name)
            credential = self._get_credential()
            secret_client = SecretClient(vault_url=self._keyvault_uri, credential=credential)
            cert_client = CertificateClient(vault_url=self._keyvault_uri, credential=credential)

            # Get cert metadata (for thumbprint) and secret (for private key)
            cert_ref = cert_client.get_certificate(self._cert_name)
            secret = secret_client.get_secret(cert_ref.properties.name)
            pfx_data = base64.b64decode(secret.value)

            # Extract private key and certificate from PFX
            private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, None)

            self._private_key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )

            # Get thumbprint from the cert metadata
            self._cert_thumbprint = cert_ref.properties.x509_thumbprint.hex()

            # Also get the public cert PEM for MSAL
            self._public_cert_pem = certificate.public_bytes(serialization.Encoding.PEM)

            logger.info("OBO certificate loaded (thumbprint: %s...)", self._cert_thumbprint[:8])
            return self._private_key_pem, self._cert_thumbprint

    def _get_msal_app(self) -> msal.ConfidentialClientApplication:
        """Build or return cached MSAL ConfidentialClientApplication."""
        if self._msal_app is not None:
            return self._msal_app

        private_key_pem, thumbprint = self._ensure_cert()

        authority = f"https://login.microsoftonline.com/{self._tenant_id}"

        self._msal_app = msal.ConfidentialClientApplication(
            client_id=self._client_id,
            authority=authority,
            client_credential={
                "private_key": private_key_pem.decode("utf-8"),
                "thumbprint": thumbprint,
                "public_certificate": self._public_cert_pem.decode("utf-8"),
            },
        )
        logger.info("MSAL ConfidentialClientApplication created for OBO")
        return self._msal_app

    def exchange_token(self, user_token: str, scopes: list[str] | None = None) -> Optional[str]:
        """
        Exchange a user's access token for a downstream token via OBO.

        Args:
            user_token: The user's Bearer token (validated by auth middleware).
            scopes: Target resource scopes. Defaults to Kusto scope.

        Returns:
            The exchanged access token, or None if OBO is disabled or fails.
        """
        if not self._enabled:
            logger.debug("OBO disabled — skipping token exchange")
            return None

        if not user_token:
            logger.warning("OBO: no user token provided")
            return None

        target_scopes = scopes or [KUSTO_SCOPE]

        try:
            app = self._get_msal_app()
            result = app.acquire_token_on_behalf_of(
                user_assertion=user_token,
                scopes=target_scopes,
            )

            if "access_token" in result:
                logger.info("OBO exchange succeeded for scopes %s", target_scopes)
                return result["access_token"]
            else:
                error = result.get("error", "unknown")
                error_desc = result.get("error_description", "")
                logger.error("OBO exchange failed: %s — %s", error, error_desc)
                return None

        except Exception as e:
            logger.error("OBO exchange exception: %s", e, exc_info=True)
            return None


# Module-level singleton
obo_exchanger = OBOTokenExchanger()
