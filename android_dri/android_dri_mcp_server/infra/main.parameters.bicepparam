using './main.bicep'

// ── Required: update these before deploying ────────────────────────────────
param appName = 'android-dri-mcp'
param location = 'eastus'

// Full image path after you've built and pushed to ACR
param imageName = 'androiddrimcp.azurecr.io/android-dri-mcp-server:latest'
param acrLoginServer = 'androiddrimcp.azurecr.io'

// ── Optional: only change if endpoints differ ──────────────────────────────
param azureSearchEndpoint = 'https://msalandroiddricopilotsearch.search.windows.net'
param azureOpenAIEndpoint = 'https://msal-android-dri-copilot-oai.openai.azure.com/'
