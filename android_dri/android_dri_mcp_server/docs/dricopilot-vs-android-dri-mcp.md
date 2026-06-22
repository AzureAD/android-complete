# DRICopilot vs Android DRI MCP + GitHub Copilot

## Executive Summary

DRICopilot is the legacy PromptFlow-based DRI assistant deployed on AML endpoints.
Android DRI MCP is a lightweight MCP server that plugs directly into GitHub Copilot,
replacing DRICopilot's functionality with a fraction of the infrastructure.

---

## Dependency Comparison

### Python Packages

| | DRICopilot | Android DRI MCP + Indexer |
|---|---|---|
| **Direct packages** | ~400 | **15** (10 server + 5 indexer) |
| **requirements.txt lines** | 1,350+ (compiled) | **17** |
| **Azure SDK packages** | 23+ | **5** (identity, search-documents, keyvault-certs, keyvault-secrets, kusto-data) |
| **LLM frameworks** | 4 (PromptFlow, LlamaIndex, Semantic Kernel, OpenAI) | **1** (OpenAI — used only for embeddings) |
| **Web frameworks** | 3 (Flask, FastAPI, Gunicorn) | **1** (uvicorn via MCP SDK) |
| **ML/Data science** | 10+ (pandas, numpy, scikit-learn, mlflow, scipy) | **0** |
| **Document processing** | 10+ (docling, llama-parse, rapidocr, pypdf) | **1** (beautifulsoup4) |
| **Observability** | 25+ (OpenTelemetry, Azure Monitor) | **0** (Container Apps provides logs natively) |

### Azure Infrastructure

| Resource | DRICopilot | Android DRI MCP |
|---|---|---|
| **AML Workspace** | ✅ Required (PromptFlow host) | ❌ Not used |
| **AML Compute Cluster** | ✅ Required (indexer jobs) | ❌ Not used |
| **PromptFlow Endpoint** | ✅ Required (inference) | ❌ Not used |
| **App Service** | ✅ Required (web frontend) | ❌ Not used |
| **Container App** | ❌ | ✅ 1 (MCP server, ~$5/mo) |
| **Container App Job** | ❌ | ✅ 1 (indexer, cron every 12h) |
| **ACR** | ✅ Shared | ✅ Own registry (androiddrimcp) |
| **Azure AI Search** | ✅ 3–5 indexes | ✅ 2 indexes |
| **Azure OpenAI** | ✅ GPT-4.1 + embeddings | ✅ Embeddings only |
| **Key Vault** | ✅ Secrets + certs | ✅ 1 cert (ICM OAuth) |
| **Cosmos DB (Gremlin)** | ✅ Knowledge graph | ❌ Not used |
| **Azure Front Door** | ✅ CDN/LB | ❌ Not used |
| **VNet / NSP** | ✅ Network isolation | ❌ Not needed (MSI auth only) |
| **Storage Account** | ✅ AML blob store | ❌ Not used |
| **Azure Monitor / OpenTelemetry** | ✅ Full pipeline | ❌ Container Apps built-in logs |
| **EV2 deployment** | ✅ ARM templates | ❌ `az containerapp update` |
| **OneBranch CI/CD** | ✅ Complex multi-stage | ❌ Single `az acr build` command |

### Codebase Size

| | DRICopilot | Android DRI MCP + Indexer |
|---|---|---|
| **Python files** | ~770 | **19** (12 server + 7 indexer) |
| **Source code size** | ~4.5 GB (including C# bot) | **~35 KB** |
| **Config files** | 30+ JSON configs | **0** (env vars + hardcoded defaults) |
| **PromptFlow DAGs** | Multiple flow.dag.yaml | **0** |
| **Skills/Agents** | 20+ skill modules | **3 tools** |
| **Test files** | Extensive pytest suite | **4** smoke/unit tests |

---

## Pros and Cons

### DRICopilot

**Pros:**
- Mature platform with 20+ skills (code gen, Kusto queries, Kepler notebooks, image analysis, git, memory, progressive messages)
- Multi-tenant: same platform serves SQL, PostgreSQL, Intelligence Platform, TSS Sign teams
- Built-in knowledge graph (Cosmos DB Gremlin) for relationship-aware investigation
- Full OpenTelemetry observability pipeline with per-request tracing
- Blue/green deployment with traffic splitting on AML endpoints
- Conversation memory and context management across sessions
- Agent orchestration — routes to specialized agents based on query type
- OneBranch CI/CD with SDL security scanning (CredScan, BinSkim, PoliCheck)
- Network-isolated via VNet + NSP

**Cons:**
- ~400 Python package dependencies — high vulnerability surface, frequent S360 CVE fixes
- AML compute cluster always-on cost (~$200+/mo even idle)
- PromptFlow endpoint cold starts (30–60s after scale-to-zero)
- Complex deployment: EV2 ARM templates, OneBranch pipelines, multi-stage rollouts
- Tightly coupled to AML workspace — hard to migrate or replicate
- Requires dedicated App Service for web frontend
- Index pipeline runs on AML jobs (blob → Search indexer pull model) — fragile, slow
- 770+ Python files — high maintenance burden for a team-of-one
- GPT-4.1 token costs for every query (LLM-in-the-loop for routing and response synthesis)
- Shared infrastructure with other teams complicates changes

### Android DRI MCP + GitHub Copilot

**Pros:**
- 15 total Python packages — minimal vulnerability surface
- 19 Python files — entire codebase readable in one sitting
- Container App scales to zero (~$5/mo when idle)
- No LLM costs for query routing — GitHub Copilot handles all orchestration and synthesis
- GitHub Copilot provides the chat UI, context management, and code integration for free
- Combines with other MCP servers (Kusto MCP, ADO MCP) for 100% feature parity
- Direct push indexing (SDK → Search) — no intermediate blob storage
- Simple deployment: `az acr build` + `az containerapp update` (2 commands)
- MSI-only auth — no API keys, no Key Vault secrets for search
- Sub-second cold start (Container App with always-ready replica)
- Sparse git checkout for TSG indexing — handles massive wikis in 4 GiB memory
- Indexer runs as Container App Job — no AML compute cluster needed
- Independent of DRICopilot platform releases and breaking changes

**Cons:**
- Only 3 tools (get_incident, search_tsgs, batch_search) — no code gen, Kepler, or image analysis built in
- No built-in conversation memory across sessions (GitHub Copilot manages its own context)
- No knowledge graph — relies on vector + keyword hybrid search (sufficient for DRI workflows where documents are self-contained TSGs and ICM incidents, not interconnected entities)
- No blue/green deployment (i.e., running two versions side-by-side and gradually shifting traffic) — unnecessary for a single-team MCP server where updates take seconds and rollback is `az containerapp update --image :vPrevious`
- No multi-tenant support — purpose-built for Android DRI
- No OneBranch/SDL pipeline — manual security review
- No OpenTelemetry instrumentation — relies on Container Apps system logs
- Depends on GitHub Copilot availability — if Copilot is down, no DRI assistant
- No VNet isolation — relies on MSI + RBAC instead of network perimeter
- GitHub Copilot's LLM may hallucinate — e.g., if `search_tsgs` returns partial matches, Copilot might synthesize a plausible-sounding mitigation step that isn't in any TSG. DRICopilot's PromptFlow DAGs constrain output to retrieved content via explicit grounding prompts, though hallucination is still possible in both systems

---

## Feature Parity Assessment

| Capability | DRICopilot | MCP + GitHub Copilot | Gap? |
|---|---|---|---|
| TSG search | ✅ | ✅ `search_tsgs` | ✅ Parity |
| ICM incident lookup | ✅ | ✅ `get_incident` | ✅ Parity |
| ICM search (past incidents) | ✅ | ✅ `batch_search` type=icm | ✅ Parity |
| Multi-query parallel search | ❌ Sequential | ✅ `batch_search` | ✅ **MCP better** |
| Kusto queries | ✅ Built-in | ✅ Via Kusto MCP server | ✅ Parity |
| ADO work items | ✅ Limited | ✅ Via ADO MCP server | ✅ Parity |
| Code generation | ✅ Skill | ✅ GitHub Copilot native | ✅ **Copilot better** |
| Code search | ❌ | ✅ GitHub Copilot native | ✅ **Copilot better** |
| Kepler notebooks | ✅ Skill | ❌ No equivalent | ❌ Gap |
| Image analysis | ✅ Skill | ❌ | ❌ Gap |
| Knowledge graph | ✅ Cosmos Gremlin | ❌ (not needed — DRI docs are self-contained) | ⚠️ Acceptable |
| Conversation memory | ✅ Built-in | ⚠️ Copilot session-only | ⚠️ Partial |
| Progressive messages | ✅ Streaming | ✅ Copilot streams natively | ✅ Parity |

**Gaps that matter for Android DRI:** Kepler notebooks (rarely used — Kusto MCP covers most query needs). Image analysis and knowledge graph are unused by the Android DRI workflow.

---

## Cost Comparison

### One-Time Setup / Full Reindex Cost

These costs are incurred during initial setup or when a full reindex is triggered (e.g., backfilling 2 years of ICMs).

| Component | DRICopilot | Android DRI MCP |
|---|---|---|
| Azure OpenAI GPT-4o (summarize ~1,000 ICMs at ~20K tokens each) | ~$70 (GPT-4.1) | ~$70 (GPT-4o) |
| Azure OpenAI embeddings (4 vectors × ~1,000 ICMs + ~600 TSGs) | ~$10 | ~$10 |
| AML Compute Cluster (full reindex job, ~2–4 hours) | ~$5 | $0 (Container App Job) |
| **Total (one-time)** | **~$85** | **~$80** |

*Full reindex is rare — only needed on initial setup, schema changes, or if the index is rebuilt from scratch.*

### Recurring Monthly Cost

| Component | DRICopilot | Android DRI MCP |
|---|---|---|
| AML Compute Cluster (indexer compute, always-on) | ~$200 | $0 (replaced by Container App Job) |
| AML Endpoint (PromptFlow) | ~$150 | $0 |
| App Service (web frontend) | ~$70 | $0 |
| Container App (MCP server) | $0 | ~$5 |
| Container App Job (indexer, cron every 12h) | $0 | ~$2 |
| Azure OpenAI GPT-4 (per-query LLM routing + synthesis) | ~$50–200 | $0 (Copilot handles LLM) |
| Azure OpenAI GPT-4o (incremental ICM summarization, ~50–100 new ICMs/mo) | ~$5 | ~$5 |
| Azure OpenAI embeddings (incremental indexing) | ~$1 | ~$1 |
| Azure AI Search | ~$25 (3–5 indexes) | ~$25 (2 indexes, same service) |
| Cosmos DB | ~$25 | $0 |
| ACR | ~$5 | ~$5 |
| Key Vault | ~$1 | ~$1 |
| **Total (monthly)** | **~$530–730/mo** | **~$44/mo** |

*Note: GitHub Copilot subscription cost ($19/mo individual or included in enterprise license) not counted — it's already provisioned for development work.*

---

## Recommendation

For a single-team Android DRI workflow, **Android DRI MCP + GitHub Copilot** is the clear winner:

1. **90% cost reduction** (~$43/mo vs ~$530–730/mo)
2. **96% fewer dependencies** (15 vs ~400 packages)
3. **97% smaller codebase** (19 vs ~770 Python files)
4. **100% feature parity** for actual Android DRI workflows (TSG, ICM, Kusto, ADO)
5. **Better code assistance** — GitHub Copilot's native code capabilities exceed DRICopilot's code skill
6. **Zero PromptFlow maintenance** — no DAG updates, no endpoint management, no AML compute

DRICopilot's advantages (knowledge graph, multi-tenant, OpenTelemetry, blue/green) are enterprise-scale features that add complexity without value for a team running a focused DRI workflow.
