"""Configuration loaded from a JSON config file with environment-variable overrides.

Usage:
    # At startup (typically in main.py):
    from android_dri_indexer import config
    config.load("configs/config_android.json")

    # Then import module-level attributes as before:
    from android_dri_indexer.config import SEARCH_ENDPOINT, TSG_GIT_SOURCES
"""

import json
import os
from pathlib import Path


def _env(key: str, default):
    """Return env-var value if set, otherwise *default*."""
    val = os.environ.get(key)
    if val is None:
        return default
    # Coerce to the same type as the default
    if isinstance(default, int):
        return int(val)
    return val


def load(config_path: str) -> None:
    """Load a JSON config file and populate module-level attributes.

    Environment variables override specific fields (useful for secrets
    and per-environment endpoints).
    """
    global SEARCH_ENDPOINT, ICM_INDEX_NAME, TSG_INDEX_NAME
    global AOAI_ENDPOINT, EMBEDDING_DEPLOYMENT, CHAT_DEPLOYMENT
    global AOAI_API_VERSION, EMBEDDING_DIMENSIONS
    global ICM_KUSTO_CLUSTER, ICM_KUSTO_DATABASE, ICM_LOOKBACK_HOURS
    global ICM_TEAM_GROUPS, ICM_TEAM_TO_SERVICE_ID
    global ICM_BLOB_CONTAINER_URL, ICM_BLOB_PREFIX
    global WIKI_BASE_URL, TSG_GIT_SOURCES
    global SERVICE_ID, CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS, UPLOAD_BATCH_SIZE

    path = Path(config_path)
    if not path.is_absolute():
        # Try CWD first, then fall back to package directory
        cwd_path = Path.cwd() / path
        pkg_path = Path(__file__).parent / path
        path = cwd_path if cwd_path.exists() else pkg_path
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    search = cfg.get("azure_search", {})
    oai = cfg.get("azure_openai", {})
    icm = cfg.get("icm", {})
    tsg = cfg.get("tsg", {})
    chunking = cfg.get("chunking", {})

    # Azure AI Search
    SEARCH_ENDPOINT = _env("AZURE_SEARCH_ENDPOINT", search.get("endpoint", ""))
    ICM_INDEX_NAME = search.get("icm_index_name", "icm-index")
    TSG_INDEX_NAME = search.get("tsg_index_name", "tsg-index")

    # Azure OpenAI
    AOAI_ENDPOINT = _env("AZURE_OPENAI_ENDPOINT", oai.get("endpoint", ""))
    EMBEDDING_DEPLOYMENT = _env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", oai.get("embedding_deployment", "text-embedding-3-large"))
    CHAT_DEPLOYMENT = _env("AZURE_OPENAI_CHAT_DEPLOYMENT", oai.get("chat_deployment", "gpt-4o"))
    AOAI_API_VERSION = oai.get("api_version", "2025-04-01-preview")
    EMBEDDING_DIMENSIONS = oai.get("embedding_dimensions", 3072)

    # Kusto / ICM
    ICM_KUSTO_CLUSTER = _env("ICM_KUSTO_CLUSTER", icm.get("kusto_cluster", "https://icmcluster.kusto.windows.net"))
    ICM_KUSTO_DATABASE = _env("ICM_KUSTO_DATABASE", icm.get("kusto_database", "IcMDataWarehouse"))
    override = icm.get("lookback_hours_override")
    env_val = os.environ.get("ICM_LOOKBACK_HOURS")
    if env_val is not None:
        ICM_LOOKBACK_HOURS = int(env_val)
    elif override is not None:
        ICM_LOOKBACK_HOURS = int(override)
    else:
        ICM_LOOKBACK_HOURS = None
    ICM_TEAM_GROUPS = icm.get("team_groups", [])
    ICM_TEAM_TO_SERVICE_ID = icm.get("team_to_service_id", {})
    ICM_BLOB_CONTAINER_URL = _env("ICM_BLOB_CONTAINER_URL", icm.get("blob_container_url", ""))
    ICM_BLOB_PREFIX = icm.get("blob_prefix", "ACS_prep/")

    # TSG wiki sources
    WIKI_BASE_URL = tsg.get("wiki_base_url", "")
    TSG_GIT_SOURCES = tsg.get("git_sources", [])

    # Chunking / general
    SERVICE_ID = _env("SERVICE_ID", cfg.get("service_id", ""))
    CHUNK_SIZE_TOKENS = chunking.get("chunk_size_tokens", 700)
    CHUNK_OVERLAP_TOKENS = chunking.get("chunk_overlap_tokens", 125)
    UPLOAD_BATCH_SIZE = cfg.get("upload_batch_size", 100)


# ── Module-level defaults (overwritten by load()) ───────────────────────
# These exist so that imports don't break before load() is called.

SEARCH_ENDPOINT: str = ""
ICM_INDEX_NAME: str = ""
TSG_INDEX_NAME: str = ""

AOAI_ENDPOINT: str = ""
EMBEDDING_DEPLOYMENT: str = "text-embedding-3-large"
CHAT_DEPLOYMENT: str = "gpt-4o"
AOAI_API_VERSION: str = "2025-04-01-preview"
EMBEDDING_DIMENSIONS: int = 3072

ICM_KUSTO_CLUSTER: str = ""
ICM_KUSTO_DATABASE: str = "IcMDataWarehouse"
ICM_LOOKBACK_HOURS: int | None = None
ICM_TEAM_GROUPS: list[dict] = []
ICM_TEAM_TO_SERVICE_ID: dict[str, str] = {}
ICM_BLOB_CONTAINER_URL: str = ""
ICM_BLOB_PREFIX: str = "ACS_prep/"

WIKI_BASE_URL: str = ""
TSG_GIT_SOURCES: list[dict] = []

SERVICE_ID: str = ""
CHUNK_SIZE_TOKENS: int = 700
CHUNK_OVERLAP_TOKENS: int = 125
UPLOAD_BATCH_SIZE: int = 100
