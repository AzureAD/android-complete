"""Create or update the android-dri-icm-index and android-dri-tsg-index."""

import logging

from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    SemanticConfiguration,
    SemanticSearch,
    SemanticPrioritizedFields,
    SemanticField,
)

from android_dri_indexer import config

logger = logging.getLogger(__name__)


def _vector_field(name: str) -> SearchField:
    """Create a 3072-dimension HNSW vector field."""
    return SearchField(
        name=name,
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=config.EMBEDDING_DIMENSIONS,
        vector_search_profile_name="myHnswProfile",
    )


def _vector_search() -> VectorSearch:
    return VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="myHnsw")],
        profiles=[
            VectorSearchProfile(
                name="myHnswProfile",
                algorithm_configuration_name="myHnsw",
            )
        ],
    )


# ── ICM index ────────────────────────────────────────────────────────────

def _icm_index() -> SearchIndex:
    fields = [
        SimpleField(
            name="ticket_id", type=SearchFieldDataType.String,
            key=True, filterable=True,
        ),
        SimpleField(
            name="service_id", type=SearchFieldDataType.String, filterable=True,
        ),
        SearchableField(name="ticket_title", type=SearchFieldDataType.String),
        SearchableField(
            name="ticket_type", type=SearchFieldDataType.String, filterable=True,
        ),
        SearchableField(
            name="ticket_owning_team", type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="ticket_create_date", type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
        ),
        SimpleField(
            name="ticket_resolve_date", type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
        ),
        SimpleField(
            name="ticket_modified_date", type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
        ),
        SearchableField(name="ticket_summary", type=SearchFieldDataType.String),
        SearchableField(name="ticket_tags", type=SearchFieldDataType.String),
        _vector_field("ticket_summary_vector"),
        _vector_field("ticket_title_vector"),
        _vector_field("ticket_mitigation_vector"),
        _vector_field("ticket_tags_vector"),
    ]

    semantic = SemanticConfiguration(
        name="my-semantic-config",
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="ticket_title"),
            content_fields=[SemanticField(field_name="ticket_summary")],
            keywords_fields=[SemanticField(field_name="ticket_tags")],
        ),
    )

    return SearchIndex(
        name=config.ICM_INDEX_NAME,
        fields=fields,
        vector_search=_vector_search(),
        semantic_search=SemanticSearch(configurations=[semantic]),
    )


# ── TSG index ────────────────────────────────────────────────────────────

def _tsg_index() -> SearchIndex:
    fields = [
        SimpleField(
            name="id", type=SearchFieldDataType.String,
            key=True, filterable=True,
        ),
        SimpleField(
            name="service_id", type=SearchFieldDataType.String, filterable=True,
        ),
        SearchableField(
            name="title", type=SearchFieldDataType.String, filterable=True,
        ),
        SearchableField(
            name="filepath", type=SearchFieldDataType.String, sortable=True,
        ),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchableField(name="keywords", type=SearchFieldDataType.String),
        SearchableField(
            name="tsg_description", type=SearchFieldDataType.String,
            filterable=True,
        ),
        SearchableField(
            name="base64_images", type=SearchFieldDataType.String,
            retrievable=True, searchable=False,
        ),
        _vector_field("title_vector"),
        _vector_field("content_vector"),
    ]

    semantic = SemanticConfiguration(
        name="my-semantic-config",
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="title"),
            content_fields=[SemanticField(field_name="content")],
            keywords_fields=[SemanticField(field_name="keywords")],
        ),
    )

    return SearchIndex(
        name=config.TSG_INDEX_NAME,
        fields=fields,
        vector_search=_vector_search(),
        semantic_search=SemanticSearch(configurations=[semantic]),
    )


# ── Public API ───────────────────────────────────────────────────────────

def ensure_indexes(*, icm: bool = True, tsg: bool = True) -> None:
    """Create or update search indexes (idempotent)."""
    credential = DefaultAzureCredential()
    client = SearchIndexClient(
        endpoint=config.SEARCH_ENDPOINT, credential=credential,
    )
    indexes = []
    if icm:
        indexes.append(_icm_index())
    if tsg:
        indexes.append(_tsg_index())
    for defn in indexes:
        client.create_or_update_index(defn)
        logger.info("Ensured index: %s", defn.name)


def delete_index(index_name: str) -> None:
    """Delete a search index if it exists."""
    credential = DefaultAzureCredential()
    client = SearchIndexClient(
        endpoint=config.SEARCH_ENDPOINT, credential=credential,
    )
    try:
        client.delete_index(index_name)
        logger.info("Deleted index: %s", index_name)
    except Exception:
        logger.info("Index %s did not exist or could not be deleted", index_name)
