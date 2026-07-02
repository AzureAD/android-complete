# Android DRI lite MCP Server — Deployment Template

Deploy a lightweight MCP server that plugs into GitHub Copilot (VS Code) to search your team's TSGs and past incidents.

## What You Get

- **MCP Server (Hosted on a container app)** — 4 tools (`search_tsgs`, `get_incident_details`, `batch_search`, `post_icm_discussion`)
- **TSG Indexer (Container job)** — Clones your ADO wiki, chunks markdown, generates embeddings, writes to blob → Azure search service/Azure AI search pull indexer
- **ICM Indexer (Container job)** — Queries Kusto for your team's incidents, GPT-4.1 summarizes, embeds, pushes to search index
- **Zero-secret auth** — Managed Identity for backend, Entra ID OAuth for users, OBO for restricted CRI enforcement

## Prerequisites (Manual Setup)

Complete these before running `deploy.ps1`:

| # | Task | Details |
|---|------|---------|
| 1 | **Entra ID App Registration** | See [App Registration Setup](#app-registration-setup) below |
| 2 | **Security Group** | Create SG in Azure AD, add team members, note the Object ID |
| 3 | **Azure AI Search** | Provision service (**Basic tier** is sufficient — a typical deployment uses ~2 GB storage and ~400 MB vector index, well within Basic's limits). Note endpoint URL |
| 4 | **Azure OpenAI** | Provision resource + deploy two models: `text-embedding-3-large` (3072 dims) + `gpt-4.1`. Note endpoint URL and deployment names |
| 5 | **Storage Account** | Create or use existing. Need a blob container for TSG chunks. Note the container URL |
| 6 | **Key Vault + ICM Certificate** | See [ICM Certificate Setup](#icm-certificate-setup) below |
| 7 | **ADO Wiki Access** | Grant the MSI (created by deploy script) access to clone your wiki. See [ADO Wiki Access](#ado-wiki-access) below |
| 8 | **Kusto Access** | Grant MSI read access to `icmcluster.kusto.windows.net / IcMDataWarehouse`. See [Kusto Access](#kusto-access) below |

---

### App Registration Setup

1. Go to [Azure Portal → App registrations → New registration](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Name: `<your-team>-dri-copilot`
3. Supported account types: **Single tenant** (this org only)
4. Register
5. Note the **Application (client) ID** — this goes in `auth.app_registration_client_id`
6. Go to **Authentication** → Add platforms:
   - **Mobile and desktop applications**: add `http://localhost` and `http://127.0.0.1`
   - **Single-page application**: add `https://vscode.dev/redirect`
   - Under "Advanced settings", set **Allow public client flows** = Yes
7. Go to **Manifest** → find `"groupMembershipClaims"` → change from `null` to `"SecurityGroup"`:
   ```json
   "groupMembershipClaims": "SecurityGroup",
   ```
8. Save the manifest

> **Why `groupMembershipClaims`?** This tells Entra ID to include the user's security group IDs in the JWT `groups` claim. The MCP server checks that at least one group matches your team's SG — this is how access control works without a client secret.

### ICM Certificate Setup

The ICM OData API uses **client certificate authentication** (`/api/cert/` endpoint). This is NOT a self-signed cert — it must be issued by a trusted CA and registered in IcM.

**Step 1: Obtain a certificate**

If your team already uses DRICopilot, you can reuse the existing certificate (`DRICopilotOAuthCertificate` in your Key Vault). Skip to Step 3.

For a new certificate:
- Request via your org's certificate provisioning process (e.g., AME/OneCert, ServiceTree cert request)
- The certificate must be signed by a CA that IcM trusts (typically Microsoft internal PKI)
- Export as `.pfx` (PKCS#12) format

**Step 2: Register the certificate in IcM**

1. Go to [IcM Portal](https://portal.microsofticm.com) → **Administration** → **Connectors**
2. Register the certificate's subject name for your team's tenant
3. This grants `/api/cert/` access to incidents owned by your teams

**Step 3: Upload to Key Vault**

```powershell
# Create Key Vault (if you don't have one)
az keyvault create --name <your-keyvault> --resource-group <your-rg> --location eastus

# Import the certificate
az keyvault certificate import `
    --vault-name <your-keyvault> `
    --name DRICopilotOAuthCertificate `
    --file <path-to-your-cert.pfx>

# Grant the MSI access to download the cert (run after deploy.ps1 creates the MSI)
az keyvault set-policy `
    --name <your-keyvault> `
    --object-id <msi-principal-id> `
    --secret-permissions get `
    --certificate-permissions get
```

> **Note:** The MCP server downloads the cert at startup, extracts the PEM key pair, and presents it as a TLS client certificate to ICM. No secrets are stored — the cert is fetched from Key Vault via MSI every time the container starts.

### ADO Wiki Access

The TSG indexer needs to `git clone` your wiki repo. Authentication uses the Managed Identity with an Azure DevOps token exchange.

**Option A: FIC (Federated Identity Credential) — recommended for MSIT tenant**

Your org may require FIC for ADO access. Follow your org's process to configure the MSI as a federated identity for ADO.

**Option B: Direct MSI token exchange — simpler**

1. The MSI requests a token scoped to Azure DevOps (`499b84ac-1321-427f-aa17-267ca6975798/.default`)
2. This works if the MSI has been granted access to the ADO organization
3. Go to your ADO org → **Organization Settings** → **Users** → add the MSI's service principal
4. Grant **Reader** access to the project containing your wiki

**Option C: PAT (Personal Access Token) — quick but not recommended**

Store a PAT in Key Vault and configure the indexer to use it. Not recommended for production (PATs expire, tied to individual users).

### Kusto Access

The ICM indexer and OBO restricted CRI checker query `icmcluster.kusto.windows.net / IcMDataWarehouse`.

1. Go to [Kusto Explorer](https://dataexplorer.azure.com) or contact the IcM data team
2. Request **Viewer** access for your MSI's principal ID to the `IcMDataWarehouse` database
3. The MSI principal ID is output by the deploy script (or find it via `az identity show --name <team>-mcp-identity -g <rg> --query principalId -o tsv`)

## Deploy

### 1. Fill in your config

Copy `config_template.json` and fill in your team's values:

```powershell
Copy-Item android_dri\deployment\config_template.json android_dri\deployment\my_config.json
# Edit my_config.json with your values
```

### 2. Preview (dry run)

```powershell
.\deployment-template\deploy.ps1 -ConfigFile deployment-template\my_config.json -DryRun
```

### 3. Deploy

```powershell
.\deployment-template\deploy.ps1 -ConfigFile deployment-template\my_config.json
```

### 4. Post-deploy manual steps

1. Grant the MSI access to your ADO wiki repo (for TSG git clone)
2. Grant the MSI access to Kusto IcMDataWarehouse
3. Grant the MSI `Storage Blob Data Contributor` on your storage account
4. Upload ICM certificate to Key Vault
5. Run initial indexing:
   ```powershell
   az containerapp job start --name <team>-icm-indexer -g <rg>
   az containerapp job start --name <team>-tsg-indexer -g <rg>
   ```
6. Connect from VS Code (add to `.vscode/mcp.json`):
   ```json
   {"servers": {"<team>-dri-search": {"type": "http", "url": "https://<your-app>.eastus.azurecontainerapps.io/mcp"}}}
   ```
7. **Enable OBO for restricted CRI enforcement** (optional, after cert is in Key Vault):
   ```powershell
   az containerapp update --name <team>-mcp -g <rg> --set-env-vars "OBO_ENABLED=true"
   ```
   Prerequisites for OBO:
   - ICM certificate uploaded to Key Vault
   - App registration configured for SNI certificate validation
   - MSI granted Key Vault access (to download the cert)
   - MSI granted Kusto access (for restricted incident team membership checks)

## What the Script Creates

```
Resource Group
├── Container Apps Environment + Log Analytics
├── Container Registry (Basic, admin disabled)
├── User-Assigned Managed Identity
├── Container App: <team>-mcp (MCP server, 0.5 vCPU / 1 GiB)
├── Container App Job: <team>-icm-indexer (daily at 06:00 UTC)
└── Container App Job: <team>-tsg-indexer (every 12h)

Azure AI Search (existing service)
├── Index: <team>-tsg-index (TSG chunks with vectors)
├── Index: <team>-icm-index (ICM summaries with vectors)
├── Data Source: <team>-tsg-blob (blob connection)
└── Indexer: <team>-tsg-indexer (P1D pull schedule)
```

## Cost Estimate

### Per-deployment costs (always new)

| Component | Monthly |
|-----------|---------|
| Container App (MCP server, scale to zero) | ~$5 |
| Container App Jobs (indexers, few min/day) | ~$2 |
| ACR (Basic) | ~$5 |
| Log Analytics | ~$2 |
| Azure OpenAI embeddings (query-time + indexing) | ~$1-5 |
| Azure OpenAI GPT-4o (ICM summarization, keywords) | ~$5 |
| **Subtotal (per-deployment)** | **~$20-25/month** |

### Base infrastructure (shared or new)

These resources are often **shared** with an existing DRICopilot deployment (incremental cost ≈ $0). If you provision them **dedicated** for this MCP, add:

| Component | Monthly | Notes |
|-----------|---------|-------|
| Azure AI Search (**Basic**) | ~$75 | Basic is sufficient (~2 GB storage / ~400 MB vectors). Standard S1 (~$245) is **not** required for this workload. |
| Storage Account (blob, LRS) | ~$0-1 | A few MB of TSG chunks |
| Key Vault (standard) | ~$0-1 | Holds the ICM cert; per-operation pricing is negligible |
| Azure OpenAI resource | $0 base | Pay-per-token only (already counted above) |
| **Subtotal (dedicated base infra)** | **~$75-80/month** | $0 if shared |

### Total

| Scenario | Monthly |
|----------|---------|
| **Shared** Search/Storage/Key Vault (recommended) | **~$20-25** |
| **Fully dedicated** (new Basic Search + Storage + Key Vault) | **~$95-105** |

> If a team follows this guide literally and provisions a **dedicated Standard S1** Search service instead of Basic, the dedicated total jumps to **~$265-285/month** — avoid this unless you actually exceed Basic's limits.

## Architecture

```
VS Code + GitHub Copilot
    │ Streamable HTTP + OAuth
    ▼
MCP Server (Container App)
    ├── search_tsgs → Azure AI Search (hybrid: text + vector)
    ├── get_incident → ICM OData API (cert auth) + Search fallback
    ├── batch_search → parallel searches across TSG + ICM indexes
    └── post_icm_discussion → ICM OData API (write)
    
    Auth: Entra ID JWT validation + Security Group check
    Backend auth: Managed Identity → Search, OpenAI, Key Vault
    OBO: Certificate-based token exchange → Kusto (restricted CRI checks)

TSG Indexer (Container App Job, every 12h)
    ADO Git wiki → sparse clone → chunk markdown (700 tokens)
    → embed (text-embedding-3-large) → keywords (GPT-4o)
    → write JSON to blob → ACS pull indexer → search index

ICM Indexer (Container App Job, daily)
    Kusto IcMDataWarehouse → GPT-4o summarize
    → embed (4 vectors) → direct push to search index
```

## Customization

### Add more TSG wiki folders

Edit `tsg.git_sources` in your config file — add more source entries:

```json
{
  "source": "https://your-org.visualstudio.com/..._git/YourWiki.wiki",
  "branch": "wikiMaster",
  "folder": "WikiRoot/NewFolder",
  "extensions": ["md"],
  "description": "My new TSG folder"
}
```

### Add more ICM teams

Edit `icm.team_groups[].teams` in your config file.

### Change indexing schedule

Update the Container App Job's cron expression:
```powershell
az containerapp job update --name <team>-tsg-indexer -g <rg> --cron-expression "0 6 * * *"
```

## Monitoring & Visualization

### Azure Portal (quickest — no setup)

Bookmark these URLs to check job status:

- **TSG Indexer:** Azure Portal → Resource Group `rg-<team>-dri-mcp` → Container App Jobs → `<team>-tsg-indexer` → **Execution History**
- **ICM Indexer:** Azure Portal → Resource Group `rg-<team>-dri-mcp` → Container App Jobs → `<team>-icm-indexer` → **Execution History**
- **MCP Server:** Azure Portal → Resource Group `rg-<team>-dri-mcp` → Container Apps → `<team>-mcp` → **Log stream**

### CLI (quick health check)

```powershell
$env:AZURE_CLI_DISABLE_CONNECTION_VERIFICATION = "1"

# Check last execution status for both indexers
az containerapp job execution list --name <team>-tsg-indexer -g rg-<team>-dri-mcp `
    --query "[0].{name:name, start:properties.startTime, status:properties.status}" -o table

az containerapp job execution list --name <team>-icm-indexer -g rg-<team>-dri-mcp `
    --query "[0].{name:name, start:properties.startTime, status:properties.status}" -o table

# Check index document counts (verifies indexers are populating data)
foreach ($idx in @("<team>-tsg-index","<team>-icm-index")) {
  $count = az rest --method get `
    --url "<search-endpoint>/indexes/$idx/docs/`$count?api-version=2024-07-01" `
    --resource "https://search.azure.com" 2>$null
  Write-Host "$idx : $count docs"
}
```

### Alerting (optional — get notified on failure)

Set up an Azure Monitor alert to get an email/Teams notification when a job fails:

1. Go to Azure Portal → **Monitor** → **Alerts** → **Create alert rule**
2. Scope: your Container Apps Environment (`<team>-mcp-env`)
3. Condition: **Custom log search** with query:
   ```kusto
   ContainerAppSystemLogs_CL
   | where Reason_s == "Error" or Reason_s == "BackOff"
   | where ContainerGroupName_s contains "indexer"
   | project TimeGenerated, ContainerGroupName_s, Log_s
   ```
4. Actions: Create action group → Email/Teams webhook
5. Alert rule name: `DRI Indexer Job Failure`

Alternatively, use the built-in **metric alert**:
1. Scope: Container Apps Environment
2. Signal: `Jobs Failed Executions`
3. Condition: Greater than 0
4. Check every: 1 hour

### Dashboard (persistent visual overview)

To create an Azure Dashboard showing everything on one page:

1. Go to Azure Portal → **Dashboard** → **New dashboard**
2. Add tiles:
   - **Container App Job execution history** (pin from each job's Execution History page)
   - **Index document count** (pin from Azure AI Search → Indexes)
   - **MCP server request count** (pin from Container App → Metrics → Requests)
3. Share the dashboard URL with your team

---

## Source Code

The MCP server and indexer code is in this repo:
- `android_dri_mcp_server/` — MCP server (Python, FastMCP)
- `android_dri_indexer/` — TSG + ICM indexer (Python)
