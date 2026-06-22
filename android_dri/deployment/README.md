# DRI MCP Server — Deployment Template

Deploy a lightweight MCP server that plugs into GitHub Copilot (VS Code) to search your team's TSGs and past incidents.

## What You Get

- **MCP Server** — 4 tools (`search_tsgs`, `get_incident`, `batch_search`, `post_icm_discussion`)
- **TSG Indexer** — Clones your ADO wiki, chunks markdown, generates embeddings, writes to blob → ACS pull indexer
- **ICM Indexer** — Queries Kusto for your team's incidents, GPT-4o summarizes, embeds, pushes to search index
- **Zero-secret auth** — Managed Identity for backend, Entra ID OAuth for users, OBO for restricted CRI enforcement

## Prerequisites (Manual Setup)

Complete these before running `deploy.ps1`:

| # | Task | Details |
|---|------|---------|
| 1 | **Entra ID App Registration** | Public client, add `groupMembershipClaims = "SecurityGroup"` to manifest. Redirect URIs: `http://localhost`, `https://vscode.dev/redirect` |
| 2 | **Security Group** | Create SG, add team members, note the Object ID |
| 3 | **Azure AI Search** | Provision service (Standard tier). Note endpoint URL |
| 4 | **Azure OpenAI** | Provision resource + deploy `text-embedding-3-large` (3072 dims) + `gpt-4o` |
| 5 | **Storage Account** | Create or use existing. Need a blob container for TSG chunks |
| 6 | **Key Vault** | Upload your ICM OData certificate (for live incident access) |
| 7 | **ADO Wiki Access** | Ensure the MSI (created by deploy script) can clone your wiki repo |
| 8 | **Kusto Access** | Grant MSI read access to `icmcluster.kusto.windows.net / IcMDataWarehouse` |

## Deploy

### 1. Fill in your config

Copy `config_template.json` and fill in your team's values:

```powershell
Copy-Item deployment-template\config_template.json deployment-template\my_config.json
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

| Component | Monthly |
|-----------|---------|
| Container App (MCP server, scale to zero) | ~$5 |
| Container App Jobs (indexers, few min/day) | ~$2 |
| ACR (Basic) | ~$5 |
| Log Analytics | ~$2 |
| Azure OpenAI embeddings (query-time + indexing) | ~$1-5 |
| Azure OpenAI GPT-4o (ICM summarization, keywords) | ~$5 |
| **Total new costs** | **~$20-25/month** |

*Azure AI Search and Azure OpenAI resource costs are separate (shared or new).*

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

## Source Code

The MCP server and indexer code is in this repo:
- `android_dri_mcp_server/` — MCP server (Python, FastMCP)
- `android_dri_indexer/` — TSG + ICM indexer (Python)
