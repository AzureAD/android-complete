# MCP Server — Build & Deploy Runbook

## Azure Resources

| Resource | Value |
|----------|-------|
| **Resource Group** | `rg-android-dri-mcp` |
| **Container App** | `android-dri-mcp` |
| **Container Apps Env** | `android-dri-mcp-env` |
| **ACR** | `androiddrimcp.azurecr.io` |
| **Managed Identity** | `android-dri-mcp-identity` (client ID: `ef52deba-2e45-4dbf-a6d8-7251242354b4`) |

## SSL / TLS Workaround

The local `az` CLI requires this env var due to OpenSSL CA cert issues:

```powershell
$env:AZURE_CLI_DISABLE_CONNECTION_VERIFICATION = "1"
```

VS Code Electron needs `NODE_EXTRA_CA_CERTS` user env var pointing to certifi's `cacert.pem`.

## Build Container Image (ACR)

> **IMPORTANT:** Always use `android_dri_mcp_server/` as the build context, NOT the workspace root.
> Running from workspace root causes massive tar uploads because of `wheels/`, `src/`, logs, etc.

```powershell
$env:AZURE_CLI_DISABLE_CONNECTION_VERIFICATION = "1"
az acr build --registry androiddrimcp --image android-dri-mcp:<tag> C:\Users\somalaya\android-complete\android_dri_mcp_server\
```

## Deploy to Container App

```powershell
az containerapp update `
  --name android-dri-mcp `
  --resource-group rg-android-dri-mcp `
  --image androiddrimcp.azurecr.io/android-dri-mcp:<tag> `
  --set-env-vars "AUTH_ENABLED=true" "MCP_TRANSPORT=streamable-http"
```

## Check Current Deployment

```powershell
az containerapp show --name android-dri-mcp --resource-group rg-android-dri-mcp `
  --query "{image:properties.template.containers[0].image, env:properties.template.containers[0].env}" -o json
```

## View App Logs (streaming)

```powershell
az containerapp logs show --name android-dri-mcp --resource-group rg-android-dri-mcp --follow
```
