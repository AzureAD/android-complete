# Android DRI Indexer — Operations Runbook

## Overview

The Android DRI search infrastructure consists of **two indexes** populated by **separate pipelines**:

| Index | Pipeline | Schedule |
|-------|----------|----------|
| `android-dri-icm-index` | Container App Job (`android-dri-icm-indexer`) | `0 6 * * *` (daily at 06:00 UTC) |
| `android-dri-tsg-index` | AML + ACS native indexers (blob pipeline) | Daily (`P1D` ACS schedule) |

The ICM indexer fetches incidents from Kusto, generates GPT-4o summaries + embeddings, and uploads to Azure AI Search. The TSG index is populated by a separate AML-based blob pipeline — **not** by this container app job.

---

## ICM Indexer — Container App Job

### Azure Resources

| Resource | Value |
|----------|-------|
| **Job Name** | `android-dri-icm-indexer` |
| **Resource Group** | `rg-android-dri-mcp` |
| **Container Apps Env** | `android-dri-mcp-env` |
| **ACR** | `androiddrimcp.azurecr.io` |
| **Image** | `androiddrimcp.azurecr.io/android-dri-icm-indexer:latest` |
| **Schedule** | `0 6 * * *` (daily at 06:00 UTC) |
| **Replica Timeout** | 7200s |
| **Managed Identity** | `msal-android-dri-copilot-identity` (client ID: `34b22be7-460d-4da1-b826-3ac92846bef6`) |
| **Log Analytics Workspace** | `c657f1e5-ba2c-4bff-a707-25f6c41890e8` |

### Dockerfile & CMD

The deployed image is built from `C:\Users\somalaya\DRICopilot\android_dri_indexer\Dockerfile`:

```dockerfile
FROM androiddrimcp.azurecr.io/android-dri-mcp:latest
COPY requirements-extra.txt .
RUN pip install --no-cache-dir -r requirements-extra.txt
COPY . ./android_dri_indexer/
ENTRYPOINT ["python3", "-m", "android_dri_indexer"]
CMD ["--config", "android_dri_indexer/configs/config_android.json", "--icm", "--skip-index-setup"]
```

Key points:
- Extends the MCP server image (shares base dependencies and S360 compliance)
- Runs **ICM only** (`--icm`), skips index schema creation (`--skip-index-setup`)
- No command/args override on the job — uses the Dockerfile CMD

### CLI Args (main.py)

| Arg | Effect |
|-----|--------|
| `--config <path>` | Path to JSON config file (required, or set `INDEXER_CONFIG` env var) |
| `--icm` | Run ICM indexer |
| `--skip-index-setup` | Skip index schema creation/update |
| `--clean` | Delete and recreate the ICM index before indexing |
| `--fresh` | Use full `lookback_hours` instead of `scheduled_lookback_hours` |
| `--cleanupAriaIncidents` | Delete AndroidShield ICMs older than 60 days from the index (runs instead of indexing) |

### ICM Indexer Behavior

1. Queries Kusto (`IcMDataWarehouse.IncidentsSnapshotV2`) for incidents in the configured team groups
2. Uses `scheduled_lookback_hours: 72` (3 days) for daily runs
3. **Deduplication**: Checks each incident ID against the index using `get_document(ticket_id)` — skips already-indexed ICMs
4. For new ICMs: generates GPT-4o summary → text-embedding-3-large (3072-dim) → uploads to ACS
5. Uploads in batches of 10

### ICM Team Groups (from config_android.json)

| Group | Teams | Scheduled Lookback |
|-------|-------|-------------------|
| Broker + Authenticator | `CloudIdentityAuthNMSALAndroid`, `CloudIdentityAuthNADALAndroid`, `AndroidMicrosoftAuthenticatorApp`, `MicrosoftIdentityApps(WindowsPhone,iOS,AndroidApps)` | 72h |
| AndroidShield | `AndroidShield` | 72h |

Full lookback (used with `--fresh`): 8760h (1 year) for Broker group, 672h (4 weeks) for AndroidShield.

---

## TSG Index — AML + ACS Blob Pipeline

The TSG index is **not managed by the container app job**. It uses a two-stage pipeline:

### Stage 1: AML Scheduled Jobs (blob generation)

AML pipelines clone IdentityWiki repos, chunk markdown, generate embeddings, and write JSON to blob storage.

| AML Schedule | Blob Output Path |
|---|---|
| `TSGProcessingSchedule-android-auth-app-1` | `outputs_TSGProcessing-android-auth-app-1/ACS_prep` |
| `TSGProcessingSchedule-android-auth-lib` | `outputs_TSGProcessing-android-auth-lib/ACS_prep` |

Storage: `msalandroidamlstorage`, container: `azureml-blobstore-dfc7eead-ae80-4f7a-83a1-f39ffe94c2e3`

### Stage 2: ACS Native Indexers (blob → search index)

ACS indexers run on a daily `P1D` schedule, read JSON blobs, and push docs into `android-dri-tsg-index`.

| ACS Indexer | Data Source | Target Index |
|---|---|---|
| `android-dri-tsg-auth-app-indexer` | `tsg-index-android-auth-app-1-blob` | `android-dri-tsg-index` |
| `android-dri-tsg-auth-lib-indexer` | `tsg-index-android-auth-lib-blob` | `android-dri-tsg-index` |

The `tsg_indexer.py` in this codebase is **deprecated** — it was the old direct-upload approach before the blob pipeline was set up.

---

## Commands to Run

All commands assume the config file is at `android_dri_indexer/configs/config_android.json`.

### Daily ICM indexing (production — what the Container App Job runs)

```bash
python3 -m android_dri_indexer --config android_dri_indexer/configs/config_android.json --icm --skip-index-setup
```

### Full re-index (backfill all ICMs within the full lookback window)

```bash
python3 -m android_dri_indexer --config android_dri_indexer/configs/config_android.json --icm --fresh
```

### Clean rebuild (delete index, recreate schema, re-index)

```bash
python3 -m android_dri_indexer --config android_dri_indexer/configs/config_android.json --icm --clean --fresh
```

### Cleanup stale AndroidShield ICMs (delete docs older than 60 days)

```bash
python3 -m android_dri_indexer --config android_dri_indexer/configs/config_android.json --cleanupAriaIncidents --skip-index-setup
```

---

## Schedules

| Job | Cron Expression | Frequency | Description |
|-----|-----------------|-----------|-------------|
| ICM indexer (`android-dri-icm-indexer`) | `0 6 * * *` | Daily at 06:00 UTC | Indexes new/updated ICMs from the last 72h for all 5 teams |
| AndroidShield cleanup | `0 7 * * 0` (recommended) | Weekly on Sunday at 07:00 UTC | Deletes AndroidShield ICMs older than 60 days from the index |
| TSG AML pipeline (Stage 1) | AML scheduled | Daily | Clones wikis, chunks markdown, generates embeddings → blob |
| TSG ACS indexer (Stage 2) | `P1D` | Daily | Reads JSON blobs → pushes to `android-dri-tsg-index` |

> **Note:** The AndroidShield cleanup is not yet deployed as a separate Container App Job. To deploy it, use `deploy-job.ps1` with a different job name and the `--cleanupAriaIncidents` CMD args.

---

## Common Operations

### Check ICM job execution history

```powershell
az containerapp job execution list `
  --name android-dri-icm-indexer `
  --resource-group rg-android-dri-mcp `
  --query "[].{name:name, startTime:properties.startTime, status:properties.status}" `
  -o table
```

### View current job configuration

```powershell
az containerapp job show `
  --name android-dri-icm-indexer `
  --resource-group rg-android-dri-mcp `
  --query "{cron:properties.configuration.scheduleTriggerConfig.cronExpression, image:properties.template.containers[0].image, args:properties.template.containers[0].args}" `
  -o json
```

### Trigger a manual ICM indexer run

```powershell
az containerapp job start --name android-dri-icm-indexer --resource-group rg-android-dri-mcp
```

### Build and deploy a new ICM indexer image

> **Important:** Always use the component folder as the build context, not the workspace root.

```powershell
# Build from DRICopilot Dockerfile (the deployed one)
az acr build `
  --registry androiddrimcp `
  --image android-dri-icm-indexer:latest `
  -f C:\Users\somalaya\DRICopilot\android_dri_indexer\Dockerfile `
  C:\Users\somalaya\DRICopilot\android_dri_indexer\ `
  --no-logs

# Trigger a manual run to verify
az containerapp job start --name android-dri-icm-indexer --resource-group rg-android-dri-mcp
```

The job always pulls `:latest` — no need to update the job image reference after building.

---

## Checking Index Health

> **Note:** Log Analytics queries are unreliable from local CLI. Prefer querying the search index directly.

```python
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

cred = DefaultAzureCredential()
ENDPOINT = "https://msalandroiddricopilotsearch.search.windows.net"

# ICM index total
icm = SearchClient(endpoint=ENDPOINT, index_name="android-dri-icm-index", credential=cred)
r = icm.search(search_text="*", select=["ticket_id"], top=0, include_total_count=True)
print(f"Total ICMs: {r.get_count()}")

# TSG index total
tsg = SearchClient(endpoint=ENDPOINT, index_name="android-dri-tsg-index", credential=cred)
r = tsg.search(search_text="*", select=["id"], top=0, include_total_count=True)
print(f"Total TSGs: {r.get_count()}")

# Check a specific ICM exists
try:
    doc = icm.get_document("21000001016227")
    print(f"Found: {doc['ticket_title']}")
except Exception:
    print("Not indexed")
```

### Verify ICMs by date range

```python
r = icm.search(
    search_text="*",
    filter="ticket_create_date ge 2026-05-01T00:00:00Z",
    select=["ticket_id", "ticket_title", "ticket_create_date", "ticket_owning_team"],
    top=50,
    include_total_count=True,
)
print(f"ICMs since May 1: {r.get_count()}")
for doc in r:
    print(f"  {doc['ticket_id']}  {doc.get('ticket_owning_team','')}")
```

---

## Search Index Gotchas

- `ticket_create_date` is **NOT sortable** in the ICM index schema. Use date-range filters (`ge`, `le`) instead of `order_by`.
- The ICM indexer is **incremental** — uses `get_document(ticket_id)` to check existence. Does NOT use filter-based search for dedup (that approach had false-positive issues with ACS).
- ACS has `disableLocalAuth: true` — API keys are disabled. All access uses Azure AD RBAC.
- Team names stored in the index (as of May 2026):
  - `MicrosoftIdentityApps(WindowsPhone,iOS,AndroidApps)` — ~786 docs
  - `AndroidMicrosoftAuthenticatorApp` — ~32 docs
  - `AndroidShield` — ~18 docs
  - `CloudIdentityAuthNMSALAndroid` — ~177 docs

---

## Failure Patterns

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Job succeeds but 0 new docs | All ICMs in lookback window already indexed | Normal for incremental runs |
| Job fails immediately | Bad image or missing deps | Check ACR build logs; ensure base MCP image is up to date |
| `get_document` raising unexpected errors | ACS service issue or auth problem | Verify MSI has `Search Index Data Contributor` role |
| Missing ICMs in index | Kusto query returned 0 rows for that team | Check team name spelling in config matches Kusto data |
| TSG index stale | AML schedule or ACS indexer paused | Check ACS indexer status in Azure Portal (not this job) |

---

## Source Code Locations

| Component | Path |
|-----------|------|
| ICM indexer (canonical) | `C:\Users\somalaya\android-complete\android_dri_indexer\icm_indexer.py` |
| Dockerfile (deployed) | `C:\Users\somalaya\DRICopilot\android_dri_indexer\Dockerfile` |
| Config | `android_dri_indexer/configs/config_android.json` |
| Deploy script | `android_dri_indexer/deploy/deploy-job.ps1` |
