# Android DRI MCP Server — Deployment & Connection Guide

## Overview

The `android_dri_mcp_server` is deployed as a centrally-hosted MCP server on **Azure Container Apps**. It provides three tools for querying MSAL Android / Broker / Authenticator DRI knowledge:

| Tool | Purpose |
|------|---------|
| `search_tsgs` | Search troubleshooting guides (TSGs) |
| `batch_search` | Run multiple targeted TSG and/or ICM searches in parallel |
| `get_incident` | Fetch a specific incident by ID |
| `post_icm_discussion` | Post investigation report to IcM discussion thread |

---

## Deployed Endpoint

| Property | Value |
|----------|-------|
| **MCP URL** | `https://android-dri-mcp.proudbeach-7e7ce77d.eastus.azurecontainerapps.io/mcp` |
| **Transport** | Streamable HTTP |
| **Resource Group** | `rg-android-dri-mcp` |
| **Subscription** | `cde31ea7-d66a-4743-af52-1d2c0940779c` |
| **Container App** | `android-dri-mcp` |
| **Container Registry** | `androiddrimcp.azurecr.io` (admin disabled, MSI pull) |
| **Image** | `androiddrimcp.azurecr.io/android-dri-mcp:v27` |
| **Managed Identity** | `android-dri-mcp-identity` (client ID: `ef52deba-2e45-4dbf-a6d8-7251242354b4`) |

---

## Authentication

The server implements a **zero-secret** design. No client secrets or certificates exist anywhere in the system. Authentication uses two complementary mechanisms:

1. **In-app JWT validation** — validates Bearer tokens on every `/mcp` request using Entra ID's public JWKS keys.
2. **OAuth metadata proxy** — enables VS Code's native MCP OAuth flow by acting as an OAuth discovery proxy in front of Entra ID.

### Access Control — Security Group Restriction

Access is restricted to members of the **Android Auth Client SDK** security group (`28a15b06-8a5d-4e0d-b696-b686c8b29eab`). A Microsoft employee who is **not** in this SG will authenticate successfully via Entra ID but receive a **401** from the MCP server.

How it works:

1. The app registration has `groupMembershipClaims = "SecurityGroup"`, which tells Entra ID to include the user's security group IDs in the `groups` claim of the JWT token.
2. When a user authenticates, Entra ID issues a token containing a `groups` array with the object IDs of every SG the user belongs to.
3. The MCP server's `EntraAuthMiddleware` checks that at least one group ID in the token matches the allowed list (`AUTH_ALLOWED_GROUP_IDS` env var).
4. If the user isn't in the SG → 401 rejected. If they are → request proceeds.

To add more allowed SGs, append their object IDs (comma-separated) to the `AUTH_ALLOWED_GROUP_IDS` env var on the Container App.

### Design Constraints

The Microsoft tenant policy blocks **all** client secrets and certificates on app registrations. Azure Container Apps Easy Auth requires a client secret to validate Bearer tokens, so Easy Auth cannot be used. The solution is fully in-app JWT validation using only public keys from Entra ID's OIDC discovery endpoint.

### App Registration

| Property | Value |
|----------|-------|
| **Name** | `msalandroiddricopilot` |
| **Application (client) ID** | `49b5a60c-3719-4444-8805-be7880a928c3` |
| **Object ID** | `e7835b6f-ecd5-41d5-8c24-784b2bd1c1ce` |
| **Tenant** | `72f988bf-86f1-41af-91ab-2d7cd011db47` (Microsoft) |
| **Public client** | `isFallbackPublicClient = true` (no client secret required) |
| **Client secret** | None (blocked by tenant policy) |
| **Group claims** | `groupMembershipClaims = "SecurityGroup"` |

**Redirect URIs:**

| Platform | URIs |
|----------|------|
| **Public client (Mobile/Desktop)** | `http://localhost`, `http://127.0.0.1` |
| **SPA** | `https://vscode.dev/redirect` |

> The public client platform supports loopback URIs with **any port**, which is required because VS Code uses `http://127.0.0.1:<random_port>/` as the OAuth callback.

### In-App JWT Validation (`auth.py`)

The `EntraAuthMiddleware` (Starlette `BaseHTTPMiddleware`) runs on every request:

1. **Bypass paths** — `/`, `/health`, `/ready`, `/.well-known/*`, `/oauth/*` pass through without auth.
2. **Token extraction** — Reads the `Authorization: Bearer <token>` header.
3. **OIDC discovery** — Fetches `https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration` (with MISE-compliant headers).
4. **JWKS key fetch** — Downloads public keys from the `jwks_uri` in the OIDC config. Keys are cached for 1 hour.
5. **JWT decode** — Validates signature (RS256), audience (`aud`), issuer (`iss`), and expiry (`exp`).
6. **Tenant check** — Verifies `tid` claim matches the expected Microsoft tenant.
7. **App ID check** — Verifies `azp` (v2 tokens) or `appid` (v1 tokens) is in the allowed list.
8. **Security group check** — Verifies the `groups` claim contains at least one allowed group ID (e.g., `Android Auth Client SDK` SG).
9. **Result** — Valid tokens: claims stored on `request.state.auth_claims`. Invalid/unauthorized tokens: HTTP 401.

### MISE Compliance

All OIDC/JWKS requests to Entra ID include MISE-compliant headers:

| Header | Value |
|--------|-------|
| `x-client-reqingappid` | The app registration client ID |
| `x-client-sku` | `MISE_Python` |
| `x-client-ver` | `1.0.0` |
| `x-client-brkrver` | `MISE_Python;1.0.0` |

### Auth Environment Variables (set on Container App)

| Variable | Value | Purpose |
|----------|-------|---------|
| `AUTH_ENABLED` | `true` | Enables JWT validation middleware |
| `AUTH_TENANT_ID` | `72f988bf-86f1-41af-91ab-2d7cd011db47` | Microsoft tenant ID |
| `AUTH_CLIENT_ID` | `49b5a60c-3719-4444-8805-be7880a928c3` | Token audience (app registration) |
| `AUTH_ALLOWED_APP_IDS` | `04b07795...,49b5a60c...` | Allowed `azp`/`appid` claim values |
| `AUTH_ALLOWED_GROUP_IDS` | `28a15b06-8a5d-4e0d-b696-b686c8b29eab` | Required SG membership (`Android Auth Client SDK`) |

### OAuth Metadata Proxy (VS Code Integration)

VS Code's MCP client implements the [MCP OAuth spec (RFC 9728)](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization) which expects the server to advertise OAuth metadata and support dynamic client registration. Since Entra ID does not support dynamic client registration, the MCP server acts as an **OAuth metadata proxy**:

| Endpoint | Purpose |
|----------|---------|
| `GET /.well-known/oauth-protected-resource` | RFC 9728 metadata. Advertises the server itself as the authorization server with scopes `openid`, `profile`, `email`. |
| `GET /.well-known/openid-configuration` | OIDC discovery. Returns Entra's real `token_endpoint` but substitutes our own `authorization_endpoint` (`/oauth/authorize`) and `registration_endpoint` (`/oauth/register`). |
| `POST /oauth/register` | Fake dynamic client registration. Always returns the pre-configured app registration client ID (`49b5a60c...`) regardless of what the client sends. |
| `GET /oauth/authorize` | Authorization proxy. Strips the `resource` parameter (VS Code sends it per RFC 8707 but Entra v2.0 rejects it) and 302 redirects to Entra's real `/authorize` endpoint. |

**OAuth flow sequence:**

```
VS Code                          MCP Server                         Entra ID
  │                                  │                                  │
  │ GET /.well-known/                │                                  │
  │   oauth-protected-resource       │                                  │
  │─────────────────────────────────►│                                  │
  │◄─────────────────────────────────│ {authorization_servers: [self]}  │
  │                                  │                                  │
  │ GET /.well-known/                │                                  │
  │   openid-configuration           │                                  │
  │─────────────────────────────────►│                                  │
  │◄─────────────────────────────────│ {token_endpoint: Entra,          │
  │                                  │  authorization_endpoint: /oauth, │
  │                                  │  registration_endpoint: /oauth}  │
  │                                  │                                  │
  │ POST /oauth/register             │                                  │
  │─────────────────────────────────►│                                  │
  │◄─────────────────────────────────│ {client_id: "49b5a60c..."}      │
  │                                  │                                  │
  │ GET /oauth/authorize?            │                                  │
  │   client_id=...&resource=...     │                                  │
  │─────────────────────────────────►│                                  │
  │                                  │ 302 → Entra /authorize           │
  │                                  │   (resource param stripped)       │
  │◄─────────────────────────────────┼─────────────────────────────────►│
  │                                  │                                  │
  │ ◄──── Browser login ────────────────────────────────────────────────│
  │                                  │                                  │
  │ POST Entra /token                │                                  │
  │   (authorization_code + PKCE)    │                                  │
  │──────────────────────────────────┼─────────────────────────────────►│
  │◄─────────────────────────────────┼──────────────────────────────────│
  │   {access_token: ...}            │                                  │
  │                                  │                                  │
  │ POST /mcp                        │                                  │
  │   Authorization: Bearer <token>  │                                  │
  │─────────────────────────────────►│ validate JWT (public JWKS keys)  │
  │◄─────────────────────────────────│ ✓ tool results                   │
```

---

## Connecting from VS Code

Add the following to your `.vscode/mcp.json` (or user-level `settings.json`):

```json
{
  "servers": {
    "android-dri-search-hosted": {
      "type": "http",
      "url": "https://android-dri-mcp.proudbeach-7e7ce77d.eastus.azurecontainerapps.io/mcp"
    }
  }
}
```

That's it — no `headers`, `inputs`, or manual token configuration needed. VS Code discovers the OAuth flow automatically via the `/.well-known/oauth-protected-resource` endpoint.

### Prerequisites

1. You must be signed in to a Microsoft corporate account (tenant `72f988bf...`)
2. VS Code with GitHub Copilot extension installed

### What happens on first connect

1. VS Code fetches `/.well-known/oauth-protected-resource` → discovers the auth server.
2. VS Code fetches `/.well-known/openid-configuration` → gets OAuth endpoints.
3. VS Code calls `POST /oauth/register` → gets the client ID.
4. VS Code opens a browser for Entra ID login via `/oauth/authorize`.
5. After login, VS Code exchanges the authorization code for an access token directly with Entra.
6. VS Code sends authenticated requests to `/mcp` with the Bearer token.
7. Tools appear in GitHub Copilot's tool list as `search_tsgs`, `batch_search`, and `get_incident`.

---

## Hosting Architecture

### Infrastructure

```
┌──────────────┐   HTTPS + Bearer JWT  ┌─────────────────────────────────┐
│  VS Code     │   (Streamable HTTP)   │  Azure Container Apps            │
│  MCP Client  │ ─────────────────────►│  android-dri-mcp                 │
└──────────────┘                       │                                   │
                                       │  ┌─────────────────────────────┐ │
                                       │  │ OAuth Metadata Proxy        │ │
                                       │  │ /.well-known/*, /oauth/*    │ │
                                       │  └─────────────┬───────────────┘ │
                                       │                │                  │
                                       │  ┌─────────────▼───────────────┐ │
                                       │  │ EntraAuthMiddleware         │ │
                                       │  │ JWT validation (JWKS keys)  │ │
                                       │  └─────────────┬───────────────┘ │
                                       │                │                  │
                                       │  ┌─────────────▼───────────────┐ │
                                       │  │ FastMCP + uvicorn           │ │
                                       │  │ search_tsgs, batch_search,  │ │
                                       │  │ get_incident                │ │
                                       │  └─────────────┬───────────────┘ │
                                       └────────────────┼──────────────────┘
                                                        │
                                  Managed Identity (DefaultAzureCredential)
                                                        │
                                       ┌────────────────┼────────────────┐
                                       │                │                │
                                 ┌─────▼──────┐  ┌─────▼──────────┐  ┌─▼─────────────┐
                                 │ Azure AI   │  │ Azure OpenAI   │  │ ICM OData     │
                                 │ Search     │  │ (embeddings)   │  │ (cert auth)   │
                                 │ 2 indexes  │  │ text-embedding │  │               │
                                 └────────────┘  │ -3-large       │  └───────────────┘
                                                 └────────────────┘
```

### Container App Configuration

| Setting | Value |
|---------|-------|
| **Ingress** | External (HTTPS, port 8080) |
| **Min replicas** | 0 (scale to zero when idle) |
| **Max replicas** | 1 |
| **CPU / Memory** | 0.5 vCPU / 1 Gi |
| **Image pull** | Managed Identity (ACR admin disabled) |
| **Transport** | `MCP_TRANSPORT=streamable-http` |

### Zero-Secret Design

No secrets exist anywhere in the system:

| Component | How it authenticates |
|-----------|---------------------|
| **Client → MCP server** | Entra ID public client OAuth (PKCE, no secret) |
| **MCP server validates tokens** | Public JWKS keys from Entra OIDC discovery |
| **MCP server → Azure AI Search** | Managed Identity (`DefaultAzureCredential`) |
| **MCP server → Azure OpenAI** | Managed Identity (`DefaultAzureCredential`) |
| **MCP server → ICM OData** | Certificate (loaded from Key Vault via MSI) |
| **ACR image pull** | Managed Identity (ACR admin disabled) |

### Search Indexes

| Index | Content |
|-------|---------|
| `android-dri-icm-index` | All ICMs (Broker, Authenticator, AndroidShield) |
| `android-dri-tsg-index` | All TSGs from IdentityWiki |

---

## Build & Deploy Process

### How the build works

The server is built as a Docker image using **Azure Container Registry (ACR) Tasks** — no local Docker daemon required. The build runs entirely in ACR's cloud infrastructure.

**Key detail:** The build context is scoped to `android_dri_mcp_server/` (not the repo root `.`). This prevents uploading the entire DRICopilot repository (~600 MB including `wheels/`, `src/`, etc.) to ACR on every build.

### Dockerfile (`android_dri_mcp_server/Dockerfile`)

```dockerfile
FROM mcr.microsoft.com/azurelinux/base/python:3.12
RUN tdnf update -y && tdnf clean all           # Patch all OS packages
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./android_dri_mcp_server/                # Copy package source
ENV PYTHONPATH=/app
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080
EXPOSE 8080
CMD ["python3", "-m", "android_dri_mcp_server"]
```

- **Base image**: `mcr.microsoft.com/azurelinux/base/python:3.12` (MCR, avoids Docker Hub rate limits)
- **OS patching**: `tdnf update -y` patches all OS-level packages (eliminates chasing individual CVEs)
- **Entrypoint**: `python3 -m android_dri_mcp_server` (Azure Linux does not symlink `python` → `python3`)
- **Layer caching**: Dependencies installed before source copy so `pip install` is cached unless `requirements.txt` changes

### 1. Build and push a new image

```powershell
az acr build --registry androiddrimcp `
  --image android-dri-mcp:v27 `
  --file android_dri_mcp_server/Dockerfile `
  android_dri_mcp_server/
```

> **Important:** The last argument `android_dri_mcp_server/` is the build context. This must point to the `android_dri_mcp_server/` directory, **not** the repo root.

### 2. Update the container app

```powershell
az containerapp update `
  --name android-dri-mcp `
  --resource-group rg-android-dri-mcp `
  --image androiddrimcp.azurecr.io/android-dri-mcp:v27
```

> **Note:** Always use a new tag (v25, v26, ...) to force a new revision. The `:latest` tag won't trigger a new revision pull.

### 3. Verify

```powershell
az containerapp revision list `
  --name android-dri-mcp `
  --resource-group rg-android-dri-mcp `
  --query "[0].{name:name, runningState:properties.runningState, healthState:properties.healthState}" `
  --output table
```

---

## Role Assignments

The managed identity (`3ce927dc-a726-462e-97a4-b870e7d7e0a9`) has these roles:

| Role | Scope |
|------|-------|
| **Search Index Data Reader** | `msalandroiddricopilotsearch` |
| **Cognitive Services OpenAI User** | `msal-android-dri-copilot-oai` |

If re-provisioning from scratch, run:

```powershell
.\android_dri_mcp_server\infra\assign-roles.ps1 `
  -PrincipalId "<identityPrincipalId from bicep output>" `
  -SearchResourceGroup "MsalAndroidDriCopilot1" `
  -SearchServiceName "msalandroiddricopilotsearch" `
  -OpenAIResourceGroup "MsalAndroidDriCopilot1" `
  -OpenAIAccountName "msal-android-dri-copilot-oai"
```

---

## Troubleshooting

### Check revision health

```powershell
az containerapp revision list --name android-dri-mcp --resource-group rg-android-dri-mcp --output table
```

### View system logs (crash reasons)

```powershell
az containerapp logs show --name android-dri-mcp --resource-group rg-android-dri-mcp --type system --tail 20
```

### View application logs

```powershell
az containerapp logs show --name android-dri-mcp --resource-group rg-android-dri-mcp --type console --tail 50
```

### Auth debugging

If VS Code shows **"Dynamic Client Registration not supported"** — the `/.well-known/oauth-protected-resource` or `/oauth/register` endpoint isn't reachable. Check that the Container App revision is running and healthy.

If you get **`AADSTS50011`** (redirect URI mismatch) — VS Code uses `http://127.0.0.1:<random_port>/` as the callback. Ensure the app registration has `http://localhost` and `http://127.0.0.1` as **publicClient (Mobile/Desktop)** redirect URIs (this platform supports loopback with any port).

If you get **`AADSTS9010010`** (resource/scope mismatch) — the `resource` parameter isn't being stripped. Check that the `/oauth/authorize` proxy endpoint is working.

### Verify OAuth metadata endpoints

```powershell
# Should return JSON with authorization_servers pointing to the server itself
Invoke-RestMethod https://android-dri-mcp.proudbeach-7e7ce77d.eastus.azurecontainerapps.io/.well-known/oauth-protected-resource

# Should return OIDC config with Entra token endpoint and server's own authorize/register endpoints
Invoke-RestMethod https://android-dri-mcp.proudbeach-7e7ce77d.eastus.azurecontainerapps.io/.well-known/openid-configuration
```

### Verify app registration redirect URIs

```powershell
az ad app show --id 49b5a60c-3719-4444-8805-be7880a928c3 `
  --query "{publicClient: publicClient.redirectUris, spa: spa.redirectUris}"
```

Expected output:

```json
{
  "publicClient": ["http://127.0.0.1", "http://localhost"],
  "spa": ["https://vscode.dev/redirect"]
}
```

---

## How Authentication Works (Plain English)

### Who can use the MCP server?

Only people who are:
1. A **Microsoft employee** (in the Microsoft tenant), **AND**
2. A member of the **Android Auth Client SDK** security group

Anyone else gets rejected.

### First time connecting (one-time login)

1. You open VS Code and it sees the MCP server URL in your config.
2. VS Code asks the server "how do I log in?" — the server says "use Microsoft login."
3. VS Code opens your browser → Microsoft login page.
4. You sign in with your @microsoft.com account.
5. Microsoft gives VS Code a **token** — think of it like a digital badge that says:
   - "This is somalaya"
   - "They work at Microsoft"
   - "They belong to these security groups: [Android Auth Client SDK, ...]"
   - "This badge expires in 1 hour"
6. VS Code saves this token and attaches it to every request.

### Every time you call a tool (search_tsgs, get_incident, etc.)

1. VS Code sends your request + token to the MCP server.
2. The server checks the token:
   - **Is it real?** → Verifies Microsoft's digital signature using their public keys (like checking a hologram on an ID card).
   - **Is it fresh?** → Checks it hasn't expired.
   - **Is it for us?** → Checks the audience matches our app.
   - **Right tenant?** → Checks it's from Microsoft, not some random company.
   - **Right group?** → Looks at the `groups` list in the token and checks if the Android Auth Client SDK SG ID is in there.
3. All checks pass → tool runs. Any check fails → **401 rejected**.

### Why no passwords or secrets anywhere?

- **You** log in through Microsoft's login page — the MCP server never sees your password.
- **The server** checks tokens using Microsoft's **public** signing keys — no secret needed to verify a signature (like verifying a passport without being the passport office).
- **Backend calls** (to Azure Search, OpenAI) use managed identity — Azure handles it automatically, no keys stored.
