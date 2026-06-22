"""
Azure AI Search + OpenAI embedding client used by all search tools.

Provides a thin wrapper around the Azure SDKs, handling authentication
via DefaultAzureCredential and query embedding via Azure OpenAI.
"""

import os
import logging
from typing import List, Optional

import requests as _requests
import urllib3.util.retry as _urllib3_retry
from azure.identity import DefaultAzureCredential
from azure.core.pipeline.transport import RequestsTransport
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.models import VectorizedQuery
from openai import AzureOpenAI

logger = logging.getLogger("android_dri_mcp_server.client")

# ── Defaults (overridable via env vars) ──────────────────────────────────

_SEARCH_ENDPOINT = "https://msalandroiddricopilotsearch.search.windows.net"
_AOAI_ENDPOINT = "https://msal-android-dri-copilot-oai.openai.azure.com/"
_EMBEDDING_DEPLOYMENT = "text-embedding-3-large"
_EMBEDDING_DIMENSIONS = 3072
_AOAI_API_VERSION = "2025-04-01-preview"

# Index names
TSG_INDEX = "android-dri-tsg-index-v2"
ICM_INDEX = "android-dri-icm-index"

# Fields to select from TSG indexes
TSG_SELECT_FIELDS = ["id", "title", "content", "keywords", "filepath", "tsg_description"]

# Fields to select from ICM index
ICM_SELECT_FIELDS = [
    "ticket_id", "ticket_title", "ticket_summary", "ticket_owning_team",
    "ticket_create_date", "ticket_resolve_date", "ticket_tags",
]


class SearchClients:
    """Lazy-initialized Azure AI Search + OpenAI embedding clients."""

    def __init__(self):
        # Each SearchClient gets its own DefaultAzureCredential to avoid
        # SSL session corruption in the Azure SDK's requests transport.
        self._openai_credential: Optional[DefaultAzureCredential] = None
        self._search_clients: dict[str, SearchClient] = {}
        self._openai_client: Optional[AzureOpenAI] = None
        self._embedding_cache: dict[str, List[float]] = {}

    @property
    def openai_credential(self) -> DefaultAzureCredential:
        if self._openai_credential is None:
            self._openai_credential = DefaultAzureCredential()
        return self._openai_credential

    @property
    def search_endpoint(self) -> str:
        return os.environ.get("AZURE_SEARCH_ENDPOINT", _SEARCH_ENDPOINT)

    @property
    def aoai_endpoint(self) -> str:
        return os.environ.get("AZURE_OPENAI_ENDPOINT", _AOAI_ENDPOINT)

    @property
    def embedding_deployment(self) -> str:
        return os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", _EMBEDDING_DEPLOYMENT)

    def get_search_client(self, index_name: str) -> SearchClient:
        """Return a cached SearchClient for the given index.

        Each client gets its own DefaultAzureCredential and a requests
        transport with retry to work around intermittent SSL EOF errors
        with OpenSSL 1.1.1 and corporate proxies.
        """
        if index_name not in self._search_clients:
            cred = DefaultAzureCredential()

            # Custom transport: fresh session per client, no connection
            # pooling, urllib3 retry on SSL and connection errors.
            retry = _urllib3_retry.Retry(
                total=5,
                backoff_factor=0.5,
                status_forcelist=[502, 503, 504],
                allowed_methods=["POST", "GET"],
                raise_on_status=False,
            )
            session = _requests.Session()
            adapter = _requests.adapters.HTTPAdapter(
                pool_connections=1,
                pool_maxsize=1,
                max_retries=retry,
                pool_block=False,
            )
            session.mount("https://", adapter)
            transport = RequestsTransport(
                session=session,
                connection_timeout=30,
                read_timeout=120,
            )

            self._search_clients[index_name] = SearchClient(
                endpoint=self.search_endpoint,
                index_name=index_name,
                credential=cred,
                transport=transport,
                retry_total=0,  # disable Azure SDK retry, let urllib3 handle it
            )
            logger.info("Created SearchClient for index %s", index_name)
        return self._search_clients[index_name]

    def get_openai_client(self) -> AzureOpenAI:
        """Return a cached AzureOpenAI client for embeddings."""
        if self._openai_client is None:
            self._openai_client = AzureOpenAI(
                azure_endpoint=self.aoai_endpoint,
                api_version=_AOAI_API_VERSION,
                azure_ad_token_provider=self._get_token_provider(),
            )
            logger.info("Created AzureOpenAI client at %s", self.aoai_endpoint)
        return self._openai_client

    def embed_query(self, text: str) -> List[float]:
        """Embed a query string, returning a vector of floats. Results are cached."""
        cache_key = text[:500]  # truncate for cache key
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        client = self.get_openai_client()
        response = client.embeddings.create(
            input=text,
            model=self.embedding_deployment,
        )
        embedding = response.data[0].embedding
        self._embedding_cache[cache_key] = embedding
        return embedding

    def _get_token_provider(self):
        """Return a callable that provides an AAD token for Azure OpenAI."""
        from azure.identity import get_bearer_token_provider
        return get_bearer_token_provider(self.openai_credential, "https://cognitiveservices.azure.com/.default")


# Module-level singleton
clients = SearchClients()
