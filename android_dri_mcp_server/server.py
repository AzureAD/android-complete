"""
Direct Azure AI Search MCP Server for Android DRI.

Provides get_incident, search_tsgs, and batch_search tools that query
Azure AI Search indexes directly, without routing through the
DRICopilot bot/PromptFlow middleware.

Usage (stdio, default):
    python -m android_dri_mcp_server

Usage (HTTP/hosted, for central deployment):
    MCP_TRANSPORT=streamable-http python -m android_dri_mcp_server
    MCP_HOST=0.0.0.0 MCP_PORT=8080 MCP_TRANSPORT=streamable-http python -m android_dri_mcp_server
"""

import os
import logging
from mcp.server.fastmcp import FastMCP

from android_dri_mcp_server.search_tools import (
    search_tsgs,
    get_incident,
    batch_search,
    post_icm_discussion,
)
from android_dri_mcp_server.icm_odata import icm_client
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from android_dri_mcp_server.auth import EntraAuthMiddleware, build_auth_middleware_args

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("android_dri_mcp_server")

# Read host/port from env so FastMCP uses them for streamable-http transport
_host = os.environ.get("MCP_HOST", "0.0.0.0")
_port = int(os.environ.get("MCP_PORT", "8080"))

mcp = FastMCP(
    "android-dri-search",
    instructions=(
        "MCP server for querying MSAL Android / Broker / Authenticator DRI knowledge. "
        "Four tools are available:\n"
        "1. get_incident — fetch full details of a specific incident by ID.\n"
        "2. search_tsgs — search troubleshooting guides for known solutions.\n"
        "3. batch_search — run multiple targeted TSG and/or ICM searches in a single call.\n"
        "4. post_icm_discussion — post an investigation report to an IcM discussion thread.\n\n"
        "Recommended workflow when investigating an incident: call get_incident first "
        "to read the symptoms, then call batch_search with multiple targeted queries "
        "derived from those symptoms. batch_search runs all searches in parallel, "
        "which is faster and produces better results than separate calls.\n"
        "After completing your investigation, use post_icm_discussion to post the report."
    ),
    host=_host,
    port=_port,
)

# Register tools
mcp.tool()(get_incident)
mcp.tool()(search_tsgs)
mcp.tool()(batch_search)
mcp.tool()(post_icm_discussion)


def main():
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    # Eagerly download cert and create HTTP session so first request is fast
    try:
        icm_client.warm_up()
    except Exception as e:
        logger.warning("ICM OData warm-up failed (will retry on first request): %s", e)

    if transport == "streamable-http":
        logger.info("Starting android-dri-search MCP server (streamable-http) on %s:%d", _host, _port)

        # Build the Starlette app and optionally add auth middleware
        auth_kwargs = build_auth_middleware_args()
        if auth_kwargs:
            import uvicorn

            _tenant = auth_kwargs["tenant_id"]
            _client = auth_kwargs["client_id"]

            _entra_authz_server = f"https://login.microsoftonline.com/{_tenant}/v2.0"

            async def _oauth_protected_resource(request: Request):
                """RFC 9728 OAuth Protected Resource Metadata.
                Matches DRICopilot's WebChatController pattern: points to
                Entra as the authorization server and embeds client_registrations
                so MCP clients can authenticate without dynamic registration."""
                host = request.headers.get("host", request.url.hostname)
                return JSONResponse({
                    "resource": f"https://{host}/mcp",
                    "scopes_supported": [
                        f"api://{_client}/user_impersonation",
                    ],
                    "authorization_servers": [
                        _entra_authz_server,
                    ],
                    "bearer_methods_supported": ["header"],
                    "client_registrations": {
                        _entra_authz_server: {
                            "client_id": _client,
                        },
                    },
                })

            async def _oauth_authorization_server(request: Request):
                """OAuth 2.1 Authorization Server Metadata.
                Points directly to Entra endpoints (matching DRICopilot pattern).
                No dynamic registration — clients use the client_id from
                the protected resource metadata."""
                return JSONResponse({
                    "issuer": _entra_authz_server,
                    "authorization_endpoint": f"https://login.microsoftonline.com/{_tenant}/oauth2/v2.0/authorize",
                    "token_endpoint": f"https://login.microsoftonline.com/{_tenant}/oauth2/v2.0/token",
                    "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo",
                    "jwks_uri": f"https://login.microsoftonline.com/{_tenant}/discovery/v2.0/keys",
                    "scopes_supported": [
                        "openid",
                        "profile",
                        f"api://{_client}/user_impersonation",
                    ],
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"],
                })

            async def _oauth_authorize(request: Request):
                """Authorize proxy.
                Strips the 'resource' parameter that VS Code sends (per RFC 8707)
                because Entra v2.0 rejects it when it doesn't match the scopes.
                Redirects to Entra's real authorize endpoint."""
                from urllib.parse import urlencode
                params = dict(request.query_params)
                params.pop("resource", None)
                return RedirectResponse(
                    f"https://login.microsoftonline.com/{_tenant}/oauth2/v2.0/authorize?{urlencode(params)}",
                    status_code=302,
                )

            starlette_app = mcp.streamable_http_app()
            starlette_app.routes.insert(0, Route(
                "/.well-known/oauth-protected-resource",
                endpoint=_oauth_protected_resource,
            ))
            starlette_app.routes.insert(1, Route(
                "/.well-known/oauth-protected-resource/{path:path}",
                endpoint=_oauth_protected_resource,
            ))
            starlette_app.routes.insert(2, Route(
                "/.well-known/openid-configuration",
                endpoint=_oauth_authorization_server,
            ))
            starlette_app.routes.insert(3, Route(
                "/.well-known/oauth-authorization-server",
                endpoint=_oauth_authorization_server,
            ))
            starlette_app.routes.insert(4, Route(
                "/oauth/authorize",
                endpoint=_oauth_authorize,
            ))
            starlette_app.add_middleware(EntraAuthMiddleware, **auth_kwargs)

            async def _serve():
                config = uvicorn.Config(starlette_app, host=_host, port=_port, log_level="info")
                server = uvicorn.Server(config)
                await server.serve()

            import anyio
            anyio.run(_serve)
        else:
            mcp.run(transport="streamable-http")
    elif transport == "sse":
        logger.info("Starting android-dri-search MCP server (SSE) on %s:%d", _host, _port)
        mcp.run(transport="sse")
    else:
        logger.info("Starting android-dri-search MCP server (stdio)")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
