"""
Entra ID JWT validation middleware for the MCP server.

Validates Bearer tokens using Entra ID's OIDC discovery and JWKS public keys.
No client secret required — uses public key validation only.

Environment variables:
    AUTH_ENABLED        – "true" to enable (default: "false")
    AUTH_TENANT_ID      – Azure AD tenant ID (default: Microsoft tenant)
    AUTH_CLIENT_ID      – App registration client (audience) ID
    AUTH_ALLOWED_APP_IDS – Comma-separated list of allowed client app IDs (azp claim)
"""

import asyncio
import functools
import os
import time
import logging
from typing import Any

import jwt
import httpx
from starlette.responses import JSONResponse

from android_dri_mcp_server.user_context import UserContext, set_user_context

logger = logging.getLogger("android_dri_mcp_server.auth")

# Defaults
_DEFAULT_TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
_JWKS_CACHE_SECONDS = 3600  # 1 hour
_MISE_SKU = "MISE_Python"
_MISE_VER = "1.0.0"


def _build_mise_headers(client_id: str) -> dict[str, str]:
    """Build MISE-compliant headers for eSTS key-discovery requests."""
    return {
        "x-client-reqingappid": client_id,
        "x-client-sku": _MISE_SKU,
        "x-client-ver": _MISE_VER,
        "x-client-brkrver": f"{_MISE_SKU};{_MISE_VER}",
    }


class _JWKSCache:
    """Caches JWKS keys fetched from the Entra ID OIDC endpoint with MISE headers."""

    def __init__(self) -> None:
        self._jwks_client: jwt.PyJWKClient | None = None
        self._jwks_uri: str | None = None
        self._client_id: str | None = None
        self._last_refresh: float = 0

    def get_signing_key(self, token: str, jwks_uri: str, client_id: str) -> jwt.PyJWK:
        now = time.time()
        if (
            self._jwks_client is None
            or self._jwks_uri != jwks_uri
            or self._client_id != client_id
            or (now - self._last_refresh) > _JWKS_CACHE_SECONDS
        ):
            self._jwks_client = jwt.PyJWKClient(
                jwks_uri,
                headers=_build_mise_headers(client_id),
            )
            self._jwks_uri = jwks_uri
            self._client_id = client_id
            self._last_refresh = now
        return self._jwks_client.get_signing_key_from_jwt(token)


_jwks_cache = _JWKSCache()


def _get_oidc_config(tenant_id: str, client_id: str) -> dict[str, Any]:
    """Fetch the OIDC discovery document for the given tenant with MISE headers."""
    url = f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    resp = httpx.get(url, headers=_build_mise_headers(client_id), timeout=10)
    resp.raise_for_status()
    return resp.json()


def validate_token(
    token: str,
    tenant_id: str,
    client_id: str,
    allowed_app_ids: set[str] | None,
    required_roles: set[str] | None = None,
) -> dict[str, Any]:
    """
    Validate a Bearer JWT token against Entra ID.

    Returns the decoded claims dict on success.
    Raises jwt.PyJWTError (or subclass) on failure.
    """
    oidc_config = _get_oidc_config(tenant_id, client_id)
    jwks_uri = oidc_config["jwks_uri"]
    issuer = oidc_config["issuer"]

    signing_key = _jwks_cache.get_signing_key(token, jwks_uri, client_id)

    claims = jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        audience=[client_id, f"api://{client_id}"],
        issuer=issuer,
        options={"require": ["exp", "iss", "aud"]},
    )

    # Verify tenant
    token_tid = claims.get("tid", "")
    if token_tid != tenant_id:
        raise jwt.InvalidTokenError(f"Token tenant {token_tid} does not match expected {tenant_id}")

    # Verify calling application (azp for v2, appid for v1)
    if allowed_app_ids:
        caller = claims.get("azp") or claims.get("appid") or ""
        if caller not in allowed_app_ids:
            raise jwt.InvalidTokenError(f"Calling application {caller} is not in the allowed list")

    # Verify app role membership (roles claim populated by Entra
    # when user/group is assigned an app role on the app registration)
    if required_roles:
        token_roles = set(claims.get("roles", []))
        if not token_roles & required_roles:
            raise jwt.InvalidTokenError(
                f"User does not have any required role. Has: {token_roles}, needs one of: {required_roles}"
            )

    return claims


class EntraAuthMiddleware:
    """
    Pure ASGI middleware that enforces Entra ID Bearer token auth.

    Uses raw ASGI interface instead of BaseHTTPMiddleware to avoid
    breaking SSE / streaming responses (required by MCP streamable-http).

    Passes through health-check paths (/) and auth-metadata paths.
    Returns 401 for missing/invalid tokens.
    """

    def __init__(
        self,
        app: Any,
        tenant_id: str,
        client_id: str,
        allowed_app_ids: set[str] | None = None,
        required_roles: set[str] | None = None,
    ) -> None:
        self.app = app
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.allowed_app_ids = allowed_app_ids
        self.required_roles = required_roles

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        method = scope.get("method", "?")

        # Allow health probes, OAuth metadata, and OAuth flow endpoints
        if path in ("/", "/health", "/ready") or path.startswith("/.well-known/") or path.startswith("/oauth/"):
            await self.app(scope, receive, send)
            return

        # Extract headers
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        auth_header = headers.get("authorization", "")

        logger.info("Auth middleware: %s %s (has_token=%s)", method, path, auth_header.startswith("Bearer "))

        resource_metadata = f"https://{host}/.well-known/oauth-protected-resource"
        www_auth = f'Bearer resource_metadata="{resource_metadata}"'

        if not auth_header.startswith("Bearer "):
            # No token — reject with 401 and WWW-Authenticate pointing to
            # the OAuth protected resource metadata (RFC 9728).  This is
            # the same enforcement that Easy Auth provides for DRICopilot:
            # every request to /mcp must carry a valid Bearer token.
            logger.warning("Auth middleware: no Bearer token on %s %s — returning 401", method, path)
            response = JSONResponse(
                {"error": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": www_auth},
            )
            await response(scope, receive, send)
            return

        token = auth_header[7:]  # strip "Bearer "
        try:
            # Run synchronous token validation in a thread to avoid blocking
            # the async event loop (it makes HTTP calls to Entra for OIDC
            # discovery and JWKS key fetching).
            loop = asyncio.get_running_loop()
            claims = await loop.run_in_executor(
                None,
                functools.partial(
                    validate_token,
                    token,
                    self.tenant_id,
                    self.client_id,
                    self.allowed_app_ids,
                    self.required_roles,
                ),
            )
            logger.info("Auth middleware: token validated for %s", claims.get("preferred_username") or claims.get("upn") or claims.get("sub", "?"))
            # Store claims and raw token in scope state for downstream use
            scope.setdefault("state", {})["auth_claims"] = claims
            scope["state"]["auth_token"] = token  # raw Bearer token for OBO exchange

            # Set per-request user context (accessible via contextvars in tool functions)
            user_email = claims.get("preferred_username") or claims.get("upn") or ""
            user_ctx = UserContext(
                email=user_email,
                raw_token=token,
                claims=claims,
            )
            set_user_context(user_ctx)
        except jwt.ExpiredSignatureError:
            response = JSONResponse(
                {"error": "Token has expired"},
                status_code=401,
                headers={"WWW-Authenticate": www_auth},
            )
            await response(scope, receive, send)
            return
        except jwt.InvalidTokenError as e:
            logger.warning("Token validation failed: %s", e)
            response = JSONResponse(
                {"error": "Invalid token"},
                status_code=401,
                headers={"WWW-Authenticate": www_auth},
            )
            await response(scope, receive, send)
            return
        except Exception as e:
            logger.error("Auth error: %s", e)
            response = JSONResponse({"error": "Authentication error"}, status_code=500)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def build_auth_middleware_args() -> dict[str, Any] | None:
    """
    Read auth config from environment. Returns kwargs for EntraAuthMiddleware,
    or None if auth is disabled.
    """
    if os.environ.get("AUTH_ENABLED", "false").lower() != "true":
        return None

    tenant_id = os.environ.get("AUTH_TENANT_ID", _DEFAULT_TENANT)
    client_id = os.environ.get("AUTH_CLIENT_ID", "")
    if not client_id:
        logger.error("AUTH_ENABLED=true but AUTH_CLIENT_ID is not set")
        return None

    allowed_raw = os.environ.get("AUTH_ALLOWED_APP_IDS", "")
    allowed_app_ids = {a.strip() for a in allowed_raw.split(",") if a.strip()} if allowed_raw else None

    required_roles_raw = os.environ.get("AUTH_REQUIRED_ROLES", "")
    required_roles = {r.strip() for r in required_roles_raw.split(",") if r.strip()} if required_roles_raw else None

    logger.info(
        "Auth enabled: tenant=%s, audience=%s, allowed_apps=%s, required_roles=%s",
        tenant_id, client_id, allowed_app_ids or "any", required_roles or "any",
    )
    return {"tenant_id": tenant_id, "client_id": client_id, "allowed_app_ids": allowed_app_ids, "required_roles": required_roles}
