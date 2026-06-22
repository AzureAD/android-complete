// ── android-dri-mcp-server: Azure Container Apps deployment ────────────────
// Deploys the android_dri_mcp_server as a centrally-hosted MCP server so any
// team member can use it without local Python setup.
//
// After deploying, run infra/assign-roles.ps1 to grant the managed identity
// access to Azure Search and Azure OpenAI.

@description('Name prefix for all resources.')
param appName string = 'android-dri-mcp'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Full container image reference, e.g. myacr.azurecr.io/android-dri-mcp-server:latest')
param imageName string

@description('ACR login server used to pull the image, e.g. myacr.azurecr.io')
param acrLoginServer string

@description('ACR admin username for image pull.')
param acrUsername string

@secure()
@description('ACR admin password for image pull.')
param acrPassword string

@description('Azure AI Search endpoint URL.')
param azureSearchEndpoint string = 'https://msalandroiddricopilotsearch.search.windows.net'

@description('Azure OpenAI endpoint URL.')
param azureOpenAIEndpoint string = 'https://msal-android-dri-copilot-oai.openai.azure.com/'

// ── Log Analytics workspace ────────────────────────────────────────────────
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${appName}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ── Container Apps Environment ─────────────────────────────────────────────
resource appEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: '${appName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── User-assigned Managed Identity ─────────────────────────────────────────
// Used by the container app to authenticate to Azure Search and Azure OpenAI
// without storing any credentials or API keys.
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-identity'
  location: location
}

// ── Container App ──────────────────────────────────────────────────────────
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: appEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
      }
      // Pull image from ACR using admin credentials
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
      ]
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp-server'
          image: imageName
          env: [
            { name: 'MCP_TRANSPORT', value: 'sse' }
            { name: 'MCP_HOST', value: '0.0.0.0' }
            { name: 'MCP_PORT', value: '8080' }
            { name: 'AZURE_SEARCH_ENDPOINT', value: azureSearchEndpoint }
            { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAIEndpoint }
            // Tell DefaultAzureCredential which managed identity to use
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1   // always-on — avoids cold start for team use
        maxReplicas: 3
      }
    }
  }
}

// ── Outputs ────────────────────────────────────────────────────────────────
@description('FQDN of the deployed Container App. Use this in your .vscode/mcp.json.')
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn

@description('Principal ID of the managed identity. Pass this to assign-roles.ps1.')
output identityPrincipalId string = identity.properties.principalId

@description('Client ID of the managed identity.')
output identityClientId string = identity.properties.clientId
