# Design Doc: Direct Azure Search MCP Server — Replacing DRICopilot Middleware

**Author:** somalaya  
**Date:** 2026-03-31  
**Status:** Draft  

---

## 1. Problem Statement

Today, VS Code GitHub Copilot queries DRICopilot's TSG and ICM knowledge via an indirect path:

```
VS Code Copilot Agent
    → DRICopilot MCP Server (C# bot, msalandroiddricopilot.azurewebsites.net)
        → PromptFlow endpoint (android-auth-dri-endpoint-gpt41)
            → Azure AI Search (msalandroiddricopilotsearch.search.windows.net)
            → Azure OpenAI (msal-android-dri-copilot-oai) for embeddings
            → GPT-4.1 for answer generation
    ← synthesized answer (or raw JSON) returned
```

This introduces **three unnecessary hops** (Bot → PromptFlow → AOAI answer gen) when VS Code Copilot already has a powerful LLM that can synthesize answers itself. The DRICopilot bot layer adds authentication overhead, PromptFlow cold-starts, and a second LLM call whose output is then re-interpreted by Copilot.

**Goal:** Eliminate the middleman. Build a lightweight MCP server that queries the existing Azure AI Search indexes directly, returning raw search results for VS Code Copilot to synthesize.

---

## 2. Current Architecture (What We're Replacing)

### 2.1 MCP Modes Exposed Today

From `Common.McpMode.json`, the current DRICopilot MCP exposes 6 tools:

| Mode ID | Description | GetRawData | Prefix |
|---|---|---|---|
| `getTSGs` | Search TSGs for a question | `true` | `[QA][TSG SEARCH REQUEST]` |
| `getICMs` | Search historical incidents | `true` | `[QA][INCIDENT SEARCH REQUEST]` |
| `summarizeIncident` | Summarize a specific incident by ID | `true` | `[QA][INCIDENT SUMMARY REQUEST]` |
| `getCode` | Search code snippets | `true` | `[Code][CODE SEARCH REQUEST]` |
| `autoExecuteTsg` | Multi-turn TSG execution | `false` | `[AUTO-TSG EXECUTION REQUEST]` |
| `autoExecuteActionRecommender` | Action recommendations | `false` | `[ACTION RECOMMENDER REQUEST]` |

Even in `GetRawData: true` modes, the request still traverses: Bot → PromptFlow → skill → Azure Search → back through all layers.

### 2.2 Azure Search Indexes

All indexes live on `msalandroiddricopilotsearch.search.windows.net`:

| Index Name | Content | Embedding Dimensions | Vector Fields |
|---|---|---|---|
| `tsg-index-android-auth-lib` | MSAL/Broker TSGs from IdentityWiki | 3072 | `title_vector`, `content_vector` |
| `tsg-index-android-auth-app-1` | Authenticator app TSGs from IdentityWiki | 3072 | `title_vector`, `content_vector` |
| `icm-index-android-auth-lib` | MSAL/Broker/ADAL/Shield ICMs | 3072 | `ticket_summary_vector`, `ticket_title_vector`, `ticket_property_vector`, `ticket_mitigation_vector` |
| `icm-index-android-auth-app` | Authenticator app ICMs | 3072 | `ticket_summary_vector`, `ticket_title_vector`, `ticket_property_vector`, `ticket_mitigation_vector` |

### 2.3 TSG Index Schema (key fields)

| Field | Type | Purpose |
|---|---|---|
| `id` | Edm.String (key) | Unique chunk ID |
| `title` | Edm.String (searchable, filterable) | TSG document title |
| `content` | Edm.String (searchable) | TSG markdown content |
| `keywords` | Edm.String (searchable) | Extracted keywords |
| `filepath` | Edm.String | Source file path |
| `tsg_description` | Edm.String (searchable) | TSG summary/description |
| `title_vector` | Collection(Single), dim=3072 | Embedding of title |
| `content_vector` | Collection(Single), dim=3072 | Embedding of content |
| `base64_images` | ComplexType | Inline images from TSG |

### 2.4 ICM Index Schema (key fields)

| Field | Type | Purpose |
|---|---|---|
| `ticket_id` | Edm.String (key) | Incident ID |
| `ticket_title` | Edm.String (searchable) | Incident title |
| `ticket_type` | Edm.String (filterable) | Incident type |
| `ticket_owning_team` | Edm.String (filterable) | Owning team |
| `ticket_create_date` | DateTimeOffset (filterable) | Creation date |
| `ticket_resolve_date` | DateTimeOffset (filterable) | Resolution date |
| `ticket_summary` | Edm.String (searchable) | GPT-generated summary |
| `ticket_summary_vector` | Collection(Single), dim=3072 | Summary embedding |
| `ticket_title_vector` | Collection(Single), dim=3072 | Title embedding |
| `ticket_property_vector` | Collection(Single), dim=3072 | Properties embedding |
| `ticket_mitigation_vector` | Collection(Single), dim=3072 | Mitigation embedding |

### 2.5 Embedding Model

All embeddings use **`text-embedding-3-large`** (3072 dimensions) from:
- Endpoint: `https://msal-android-dri-copilot-oai.openai.azure.com/`
- Deployment: `text-embedding-3-large`
- API version: `2025-04-01-preview`

### 2.6 Indexer Pipelines (Remain Unchanged)

These AML pipelines must continue running regardless of which frontend consumes the indexes:

| Pipeline | Source | Schedule | Index Target |
|---|---|---|---|
| TSG (MSAL/Broker) | IdentityWiki git → markdown chunking → embedding | Every 24h | `tsg-index-android-auth-lib` |
| TSG (Auth App) | IdentityWiki git (6 folders: Android, On-Call, Common/TSG-Guides, Release, Telemetry-Monitoring, Telemetry-Infrastructure) → chunking → embedding | Every 24h | `tsg-index-android-auth-app-1` |
| ICM (MSAL) | Kusto `icmcluster.kusto.windows.net/IcMDataWarehouse` → GPT summarization → embedding | Every 12h | `icm-index-android-auth-lib` |
| ICM (Auth App) | Kusto → GPT summarization → embedding | Every 24h | `icm-index-android-auth-app` |

### 2.7 Kusto Access (ICM Insight)

The `icm_insight` skill queries live IcM data:
- **Cluster:** `https://icmcluster.kusto.windows.net`
- **Database:** `IcMDataWarehouse`
- **Auth:** User identity (OBO) or Managed Identity fallback

---

## 3. Proposed Architecture

```
VS Code Copilot Agent (Claude/GPT-4.1)
    → Direct Azure Search MCP Server (Python, local stdio process)
        → Azure AI Search (msalandroiddricopilotsearch.search.windows.net)
        → Azure OpenAI (msal-android-dri-copilot-oai) for query embedding only
    ← raw search results returned
    [Copilot synthesizes the answer with full editor context]
```

### 3.1 MCP Server: Tools

The new MCP server exposes **4 tools** (mapped from the 6 current modes):

#### Tool 1: `search_tsgs`

```
Description: Search troubleshooting guides (TSGs) for MSAL Android, Broker, and 
             Authenticator. Returns relevant TSG chunks with title, content, and source path.
Parameters:
  - query (string, required): The issue or topic to search for.
  - max_results (integer, optional, default=12): Maximum results to return.
  - index (string, optional, default="all"): Which index to search. 
      Options: "all", "msal-broker", "auth-app"
```

**Implementation:** Hybrid search (text + vector) across `tsg-index-android-auth-lib` and `tsg-index-android-auth-app-1`. Embed query using `text-embedding-3-large`, search `content_vector` and `title_vector` with text fallback on `title,content`.

#### Tool 2: `search_icms`

```
Description: Search historical incidents (ICMs) for similar past issues. Returns incident 
             summaries with root cause, mitigation, and resolution.
Parameters:
  - query (string, required): Describe the symptoms or issue.
  - max_results (integer, optional, default=10): Maximum results.
  - index (string, optional, default="all"): "all", "msal-broker", "auth-app"
  - date_from (string, optional): Filter incidents after this date (ISO 8601).
  - date_to (string, optional): Filter incidents before this date (ISO 8601).
```

**Implementation:** Hybrid search across `icm-index-android-auth-lib` and `icm-index-android-auth-app`. Embed query, search `ticket_summary_vector` and `ticket_title_vector`. Apply date filters on `ticket_create_date`.

#### Tool 3: `get_incident`

```
Description: Get full details of a specific incident by ID.
Parameters:
  - incident_id (string, required): The 9-digit incident ID.
```

**Implementation:** Direct lookup by `ticket_id` key in ICM indexes.

#### Tool 4: `query_icm_kusto`

```
Description: Run a live IcM Kusto query for real-time incident data. Use for recent 
             incidents not yet in the search index.
Parameters:
  - query (string, required): Natural language description of what to look up.
  - time_range (string, optional, default="7d"): How far back to search.
```

**Implementation:** Translate natural language to KQL against `icmcluster.kusto.windows.net / IcMDataWarehouse`. Requires user identity delegation (AAD token passthrough) or managed identity.

### 3.2 Authentication

| Resource | Auth Method |
|---|---|
| Azure AI Search | Managed Identity ( or API key from Key Vault |
| Azure OpenAI (embeddings) | Managed Identity or API key from Key Vault |
| Kusto (IcM) | User identity via `DefaultAzureCredential` (user is already authed to corp network) |

For local development (stdio MCP server running on user's machine), `DefaultAzureCredential` covers all three—no API keys needed if the user has the right RBAC roles.

### 3.3 MCP Server Transport

**stdio** (local process, no network exposure):
- VS Code launches the Python process directly
- No web server, no CORS, no TLS, no port conflicts
- User's own Azure identity is used automatically
- Zero deployment—just `pip install` and configure `.vscode/mcp.json`

```jsonc
// .vscode/mcp.json
{
  "servers": {
    "android-dri-search": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "android_dri_mcp_server"],
      "env": {
        "AZURE_SEARCH_ENDPOINT": "https://msalandroiddricopilotsearch.search.windows.net",
        "AZURE_OPENAI_ENDPOINT": "https://msal-android-dri-copilot-oai.openai.azure.com/",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
        "KUSTO_CLUSTER": "https://icmcluster.kusto.windows.net",
        "KUSTO_DATABASE": "IcMDataWarehouse"
      }
    }
  }
}
```

---

## 4. What We Gain

| Aspect | Before (DRICopilot MCP) | After (Direct Search MCP) |
|---|---|---|
| **Latency** | ~8-15s (Bot auth + PromptFlow cold start + AOAI answer gen + search) | ~1-3s (embed query + search) |
| **LLM calls per query** | 2 (DRICopilot's GPT-4.1 + Copilot's LLM) | 1 (Copilot's LLM only; embedding doesn't count) |
| **Cost** | AOAI tokens for answer gen + Copilot tokens | Copilot tokens only + embedding tokens (~$0.0001/query) |
| **Context** | DRICopilot has no editor context | Copilot has full file, selection, terminal context |
| **Code search** | Separate `getCode` mode | GitHub Copilot has native code search — not needed |
| **Maintenance** | Full C# bot + PromptFlow deployment pipeline | Single Python file, ~200 lines |
| **Auth** | EasyAuth + OBO token exchange chain | `DefaultAzureCredential` (user's own identity) |
| **Availability** | Depends on App Service + PromptFlow endpoint uptime | Local process—always available when VS Code is open |

## 5. What We Lose

| Capability | Impact | Mitigation |
|---|---|---|
| **autoExecuteTsg** (multi-turn TSG orchestration) | Can't auto-run Kusto queries step-by-step | Copilot can follow TSG steps manually with `query_icm_kusto` tool; future: add Kusto query execution tool |
| **autoExecuteActionRecommender** | Loses pre-built action recommendation prompts | Copilot prompt instructions can encode the same guidance |
| **Conversation persistence** (Cosmos DB) | No history saved across sessions | VS Code Copilot has its own conversation history; audit logging can be added to MCP server if needed |
| **Online evaluation pipeline** | No automatic quality scoring | Can add App Insights telemetry to MCP server later |
| **1CS audit logging** | No compliance logging | Add audit logging to MCP server if required |
| **Teams bot accessibility** | Other team members without VS Code can't use it | Out of scope—team already uses VS Code exclusively |
| **Image analysis** | DRICopilot's `image.image_analyzer` skill | Copilot has native image support via multimodal models |

## 6. What Stays the Same

1. **Indexer pipelines** — AML pipelines continue crawling IdentityWiki and IcM Kusto every 12-24h. No changes needed.
2. **Azure AI Search indexes** — Same indexes, same data, same embeddings. The new MCP server is a read-only consumer.
3. **Azure OpenAI** — Same embedding deployment used for query vectorization.
4. **Kusto access** — Same cluster, same database, same auth patterns.

## 7. Implementation Plan

### Phase 1: Core MCP Server (Week 1)

1. Create Python MCP server package (`android_dri_mcp_server/`)
2. Implement `search_tsgs` tool — hybrid search across TSG indexes
3. Implement `search_icms` tool — hybrid search across ICM indexes
4. Implement `get_incident` tool — direct incident lookup
5. Auth via `DefaultAzureCredential` for both Search and AOAI
6. VS Code MCP config (`.vscode/mcp.json`)
7. Testing against live indexes

### Phase 2: Kusto Integration (Week 2)

1. Implement `query_icm_kusto` tool — live IcM queries
2. KQL template library (reuse existing `kql_auth_app_v3` patterns)
3. User identity passthrough for Kusto auth

### Phase 3: Polish & Rollout (Week 3)

1. Copilot system prompt / `.github/copilot-instructions.md` with DRI-specific guidance
2. Error handling, retry logic, telemetry
3. Team rollout guide (pip install + mcp.json)
4. Side-by-side testing vs DRICopilot MCP

### Phase 4: Decommission (After Validation)

1. Confirm feature parity for getTSGs, getICMs, summarizeIncident
2. Disable DRICopilot MCP server (`IsMcpServerEnabled: false`)
3. Keep bot running if Teams/web frontend still needed by anyone
4. Keep indexer pipelines running (they are independent)

## 8. Dependencies

| Dependency | Required | Notes |
|---|---|---|
| Python 3.10+ | Yes | Already in team's dev environment |
| `azure-search-documents` SDK | Yes | pip install |
| `azure-identity` SDK | Yes | For `DefaultAzureCredential` |
| `openai` Python SDK | Yes | For embedding queries |
| `azure-kusto-data` SDK | Phase 2 | For live Kusto queries |
| `mcp` Python SDK | Yes | MCP server framework |
| RBAC: Search Index Data Reader | Yes | On `msalandroiddricopilotsearch` for team members |
| RBAC: Cognitive Services OpenAI User | Yes | On `msal-android-dri-copilot-oai` for team members |
| Kusto access | Phase 2 | Team members already have IcM cluster access |

## 9. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Search results quality differs without DRICopilot's reranking logic | Medium | Medium | DRICopilot reranking uses simple weighted scoring — implement same logic in MCP server (~20 lines) |
| Team members missing RBAC on Search/AOAI | Low | High | One-time RBAC setup; document in rollout guide |
| Kusto auth fails without OBO chain | Medium | Medium | `DefaultAzureCredential` works for corp-joined machines; fallback to managed identity |
| Copilot synthesizes worse answers without DRICopilot's tuned prompts | Low | Medium | Encode key instructions in `copilot-instructions.md` (e.g. "never suggest escalating to Broker team") |
| Indexer pipelines break with no one watching | Low | High | Keep existing AML pipeline monitoring; add alerts |

## 10. Success Criteria

- [ ] `search_tsgs` returns equivalent results to DRICopilot `getTSGs` mode (compare top-5 overlap ≥ 80%)
- [ ] `search_icms` returns equivalent results to DRICopilot `getICMs` mode
- [ ] End-to-end latency < 3 seconds for search queries
- [ ] Team members can install and use in < 15 minutes
- [ ] No new Azure resources needed (reuses existing Search, AOAI, Kusto)

---

## 11. Security & Access Control

### 11.1 Authentication Model

The MCP server runs as a **local stdio process** on each developer's machine. There is no shared service account or API key. All Azure resource access uses [`DefaultAzureCredential`](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential), which resolves to the developer's own AAD identity (via `az login`, VS Code Azure account, or managed identity).

This means:
- **Per-user identity** — Azure activity logs show exactly who queried what
- **No shared secrets** — no API keys to rotate or leak
- **No network exposure** — stdio transport, no listening port

### 11.2 RBAC-Based Access Restriction

Access is controlled via **Azure RBAC roles** assigned to a single **AAD security group**. Anyone not in the group receives a `403 Forbidden` from Azure — no code-level enforcement needed.

| AAD Security Group | `sg-android-dri-mcp-users` |
|---|---|
| **Role on Azure AI Search** | `Search Index Data Reader` on `msalandroiddricopilotsearch` |
| **Role on Azure OpenAI** | `Cognitive Services OpenAI User` on `msal-android-dri-copilot-oai` |
| **Role on Kusto (Phase 2)** | Users already have IcM cluster access via existing team permissions |

**To grant access:** Add user to the AAD group.  
**To revoke access:** Remove user from the AAD group.  
**Code changes required:** None.

> **Relationship to DRICopilot.Users:** The existing DRICopilot bot uses a custom app role (`DRICopilot.Users`, AppRoleId `e37a0457-...`) on the AAD enterprise app (`4aec4422-...`), enforced in C# middleware. The MCP approach uses Azure built-in RBAC roles enforced by Azure itself. Both can share the same underlying AAD group for membership management.

### 11.3 Setup Instructions

```powershell
# 1. Create security group (or reuse existing group)
az ad group create --display-name "sg-android-dri-mcp-users" --mail-nickname "sg-android-dri-mcp-users"
$GROUP_ID = (az ad group show --group "sg-android-dri-mcp-users" --query id -o tsv)

# 2. Get resource IDs
$SEARCH_ID = az resource show `
  --resource-group MsalAndroidDriCopilot1 `
  --resource-type "Microsoft.Search/searchServices" `
  --name "msalandroiddricopilotsearch" `
  --query "id" -o tsv

$AOAI_ID = az resource show `
  --resource-group MsalAndroidDriCopilot1 `
  --resource-type "Microsoft.CognitiveServices/accounts" `
  --name "msal-android-dri-copilot-oai" `
  --query "id" -o tsv

# 3. Assign RBAC roles
az role assignment create `
  --assignee-object-id $GROUP_ID `
  --assignee-principal-type Group `
  --role "Search Index Data Reader" `
  --scope $SEARCH_ID

az role assignment create `
  --assignee-object-id $GROUP_ID `
  --assignee-principal-type Group `
  --role "Cognitive Services OpenAI User" `
  --scope $AOAI_ID

# 4. Add team members
az ad group member add --group $GROUP_ID `
  --member-id $(az ad user show --id "user@microsoft.com" --query id -o tsv)

# 5. Verify
az ad group member list --group $GROUP_ID `
  --query "[].{name:displayName, upn:userPrincipalName}" -o table
```

### 11.4 Access Control Summary

| Layer | Mechanism | What It Restricts |
|---|---|---|
| **Azure AI Search RBAC** | `Search Index Data Reader` on security group | Who can query ICM + TSG indexes |
| **Azure OpenAI RBAC** | `Cognitive Services OpenAI User` on security group | Who can generate query embeddings |
| **Code distribution** | Git repo permissions (ADO/GitHub) | Who has the MCP server code + `.vscode/mcp.json` |
| **Network (optional)** | Azure Search IP firewall / private endpoint | Where queries can originate from |

### 11.5 Optional Hardening (Future)

| Enhancement | When to Add |
|---|---|
| Azure Search IP firewall — restrict to corp network CIDR ranges | If network-level restriction needed beyond RBAC |
| Private endpoint for Search + AOAI | If data classification requires traffic off public internet |
| Audit logging via App Insights | If compliance requires query-level logging (who searched what, when) |
| Key Vault API key fallback | Only if `DefaultAzureCredential` causes issues (e.g., guest accounts) |

---
## Appendix A: Key Azure Resource Reference

| Resource | Endpoint / Name |
|---|---|
| Azure AI Search | `https://msalandroiddricopilotsearch.search.windows.net` |
| Azure OpenAI | `https://msal-android-dri-copilot-oai.openai.azure.com/` |
| Embedding model | `text-embedding-3-large` (3072 dim) |
| Key Vault | `https://msalandroidamlkeyvault.vault.azure.net/` |
| Kusto (IcM) | `https://icmcluster.kusto.windows.net` / `IcMDataWarehouse` |
| AML Workspace | `msal-android-dri-copilot-aml` (eastus) |
| Managed Identity | `34b22be7-460d-4da1-b826-3ac92846bef6` |
| Subscription | `cde31ea7-d66a-4743-af52-1d2c0940779c` |
| Resource Group | `MsalAndroidDriCopilot1` |


## Appendix B: Search Index Names

| Index | Content Source |
|---|---|
| `tsg-index-android-auth-lib` | MSAL Android / Broker TSGs |
| `tsg-index-android-auth-app-1` | Authenticator Android TSGs (6 wiki folders) |
| `icm-index-android-auth-lib` | MSAL Android / ADAL Android / AndroidShield ICMs |
| `icm-index-android-auth-app` | Authenticator App ICMs (AZUREMFA + CLOUDIDENTITYAUTHNCLIENT teams) |

## Appendix C: ICM Team Mappings

| Index | IcM Teams |
|---|---|
| `icm-index-android-auth-lib` | `CLOUDIDENTITYAUTHNCLIENT\CloudIdentityAuthNMSALAndroid`, `CLOUDIDENTITYAUTHNCLIENT\CloudIdentityAuthNADALAndroid`, `CLOUDIDENTITYAUTHNCLIENT\AndroidShield` |
| `icm-index-android-auth-app` | `AZUREMFA\MicrosoftIdentityApps(WindowsPhone,iOS,AndroidApps)`, `CLOUDIDENTITYAUTHNCLIENT\AndroidMicrosoftAuthenticatorApp` |
