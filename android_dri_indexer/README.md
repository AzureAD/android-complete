# android_dri_indexer

Indexer modules for populating the `android-dri-tsg-index` and `android-dri-icm-index` Azure Cognitive Search indexes used by the Android DRI MCP server.

## TSG Index Pipeline

The `android-dri-tsg-index` is populated via a two-stage pipeline:

### Stage 1: AML Processing (blob generation)

AML scheduled jobs clone the wiki repos, chunk markdown files, generate embeddings, and write JSON docs to blob storage.

| AML Schedule | Blob Output Path |
|---|---|
| `TSGProcessingSchedule-android-auth-app-1` | `outputs_TSGProcessing-android-auth-app-1/ACS_prep` |
| `TSGProcessingSchedule-android-auth-lib` | `outputs_TSGProcessing-android-auth-lib/ACS_prep` |

Storage account: `msalandroidamlstorage`, container: `azureml-blobstore-dfc7eead-ae80-4f7a-83a1-f39ffe94c2e3`

The AML pipeline code lives in `src/core/indexers/tsgs/`.

### Stage 2: ACS Indexers (blob → search index)

ACS indexers run on a daily schedule (`P1D`), read the JSON blobs, and push documents into `android-dri-tsg-index`.

| ACS Indexer | Data Source | Target Index |
|---|---|---|
| `android-dri-tsg-auth-app-indexer` | `tsg-index-android-auth-app-1-blob` | `android-dri-tsg-index` |
| `android-dri-tsg-auth-lib-indexer` | `tsg-index-android-auth-lib-blob` | `android-dri-tsg-index` |

Field mappings (core fields only, no rhyde/feedback):

| Source (blob JSON) | Target (index field) |
|---|---|
| `title` | `title` |
| `metadata_storage_name` | `filepath` |
| `content` | `content` |
| `keywords` | `keywords` |
| `base64_images` | `base64_images` |
| `title_vector` | `title_vector` |
| `content_vector` | `content_vector` |
| `tsg_description` | `tsg_description` |

### Legacy indexers (old per-service indexes)

The same blob data sources also feed the older per-service indexes via separate indexers. These are still active but are not used by the MCP server.

| ACS Indexer | Target Index |
|---|---|
| `tsg-index-android-auth-app-1-indexer` | `tsg-index-android-auth-app-1` |
| `tsg-index-android-auth-lib-indexer` | `tsg-index-android-auth-lib` |

## ICM Index Pipeline

The `android-dri-icm-index` follows a similar blob-backed pattern via `ICMProcessingSchedule` AML jobs.

## Index Schemas

Defined in `index_schemas.py`. The TSG index fields:

- `id` (key)
- `service_id`
- `title`
- `filepath`
- `content`
- `keywords`
- `tsg_description`
- `base64_images`
- `title_vector` (1536-dim)
- `content_vector` (1536-dim)

## Auth

The ACS search service (`msalandroiddricopilotsearch`) has `disableLocalAuth: true` — API keys are disabled. All access uses Azure AD RBAC:

- **Search Index Data Reader** — read/search operations
- **Search Index Data Contributor** — write/delete operations
- **Search Service Contributor** — manage service and indexers

## Files

| File | Purpose |
|---|---|
| `config.py` | Loads JSON config (index names, ACS endpoint, etc.) |
| `configs/config_android.json` | Config for Android auth wikis (6 git sources, chunk params) |
| `index_schemas.py` | Creates/updates ACS index schemas |
| `tsg_indexer.py` | Direct-upload TSG indexer (deprecated — use blob pipeline instead) |
| `icm_indexer.py` | ICM indexer module |
| `main.py` | CLI entry point |
| `pii_sanitizer.py` | PII scrubbing for indexed content |
