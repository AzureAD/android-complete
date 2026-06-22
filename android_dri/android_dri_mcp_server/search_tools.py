"""
MCP tool implementations for search_tsgs, get_incident, and batch_search.

Each tool queries the existing Azure AI Search indexes directly using
hybrid search (text + vector) and returns raw results for the LLM to
synthesize.  When an incident_id is supplied, the tool also fetches
live incident data from the ICM OData API and includes it in the
response so that callers get everything in a single round-trip.

Performance: index searches and OData fetches run in parallel via
asyncio.to_thread to avoid blocking the event loop and to overlap I/O.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from azure.search.documents.models import VectorizedQuery

from android_dri_mcp_server.client import (
    clients,
    TSG_INDEX,
    ICM_INDEX,
    TSG_SELECT_FIELDS,
    ICM_SELECT_FIELDS,
)
from android_dri_mcp_server.icm_odata import icm_client, _format_live_incident
from android_dri_mcp_server.user_context import get_user_context
from android_dri_mcp_server.restricted_cri_checker import check_incident_access

_POST_DISCUSSION_MAX_LENGTH = 8000  # IcM discussion entries have a size limit

logger = logging.getLogger("android_dri_mcp_server.tools")


# ── Helpers ──────────────────────────────────────────────────────────────

def _resolve_tsg_indexes(index: str) -> list[str]:
    """Return the single TSG index."""
    return [TSG_INDEX]


def _resolve_icm_indexes(index: str) -> list[str]:
    """Map user-facing index name to actual index names."""
    return [ICM_INDEX]


def _format_tsg_result(item: dict) -> dict:
    """Extract the relevant fields from a TSG search result."""
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "content": item.get("content", ""),
        "filepath": item.get("filepath", ""),
        "tsg_description": item.get("tsg_description", ""),
        "keywords": item.get("keywords", ""),
        "score": item.get("@search.score"),
    }


def _format_icm_result(item: dict) -> dict:
    """Extract the relevant fields from an ICM search result."""
    create_date = item.get("ticket_create_date")
    resolve_date = item.get("ticket_resolve_date")
    return {
        "ticket_id": item.get("ticket_id", ""),
        "title": item.get("ticket_title", ""),
        "summary": item.get("ticket_summary", ""),
        "owning_team": item.get("ticket_owning_team", ""),
        "created": str(create_date) if create_date else "",
        "resolved": str(resolve_date) if resolve_date else "",
        "tags": item.get("ticket_tags", ""),
        "score": item.get("@search.score"),
    }


def _fetch_live_incident(incident_id: str) -> Optional[dict]:
    """Fetch live incident details from ICM OData. Returns formatted dict or None."""
    try:
        data = icm_client.get_full_incident(incident_id)
        if data is None:
            logger.warning("_fetch_live_incident: OData returned None for %s", incident_id)
            return None
        return _format_live_incident(data)
    except Exception as e:
        logger.error("_fetch_live_incident: unexpected error for %s: %s", incident_id, e, exc_info=True)
        return None


def _build_query_from_incident(live: dict) -> str:
    """Build a search query string from live incident fields."""
    parts = []
    if live.get("title"):
        parts.append(live["title"])
    if live.get("keywords"):
        parts.append(live["keywords"])
    return " ".join(parts) if parts else ""


def _hybrid_search_tsg(index_name: str, query: str, query_vector: list, max_results: int) -> list[dict]:
    """Run hybrid (text + vector) search on a TSG index."""
    search_client = clients.get_search_client(index_name)

    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=max_results,
        fields="title_vector,content_vector",
        exhaustive=True,
    )

    response = search_client.search(
        search_text=query,
        vector_queries=[vector_query],
        select=TSG_SELECT_FIELDS,
        top=max_results,
        search_fields=["title", "content"],
    )

    results = []
    for item in response:
        results.append(_format_tsg_result(item))
    return results


def _hybrid_search_icm(
    index_name: str,
    query: str,
    query_vector: list,
    max_results: int,
    date_filter: Optional[str] = None,
) -> list[dict]:
    """Run hybrid (text + vector) search on an ICM index."""
    search_client = clients.get_search_client(index_name)

    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=max_results,
        fields="ticket_summary_vector,ticket_title_vector",
        exhaustive=True,
    )

    select_fields = ICM_SELECT_FIELDS

    kwargs = {
        "search_text": query,
        "vector_queries": [vector_query],
        "select": select_fields,
        "top": max_results,
        "search_fields": ["ticket_summary", "ticket_title"],
    }
    if date_filter:
        kwargs["filter"] = date_filter

    response = search_client.search(**kwargs)

    results = []
    for item in response:
        results.append(_format_icm_result(item))
    return results


# ── MCP Tools ────────────────────────────────────────────────────────────

async def search_tsgs(
    query: str,
    max_results: int = 5,
    incident_id: Optional[str] = None,
) -> str:
    """Search troubleshooting guides (TSGs) for MSAL Android, Broker, and Authenticator.

    Returns relevant TSG chunks with title, content, and source path.
    Use this when investigating an incident, debugging an issue, or looking
    for known solutions.

    When incident_id is provided, the tool first fetches the live incident
    details from ICM and includes them alongside the TSG search results
    so everything is returned in a single call.

    Args:
        query: The issue or topic to search for.
        max_results: Maximum results to return (default 5).
        incident_id: Optional incident ID to fetch live details for and use as additional search context.
    """
    logger.info("search_tsgs: query=%r, max_results=%d, incident_id=%s", query, max_results, incident_id)

    response: dict = {}

    # Fetch live incident context if requested
    if incident_id:
        live = _fetch_live_incident(incident_id)
        if live:
            response["current_incident"] = live
            # Enrich the search query with incident context
            extra = _build_query_from_incident(live)
            if extra:
                query = f"{query} {extra}"

    query_vector = clients.embed_query(query)
    indexes = _resolve_tsg_indexes("")

    # Per-index result budget
    per_index = max(1, max_results // len(indexes))

    # Search all indexes in parallel
    async def _search_one(idx_name: str) -> list[dict]:
        results = await asyncio.to_thread(_hybrid_search_tsg, idx_name, query, query_vector, per_index)
        for r in results:
            r["index"] = idx_name
        return results

    index_results = await asyncio.gather(*[_search_one(idx) for idx in indexes])
    all_results = [r for batch in index_results for r in batch]

    # Sort by score descending, take top max_results
    all_results.sort(key=lambda x: x.get("score") or 0, reverse=True)
    all_results = all_results[:max_results]

    # Trim content to keep payload small
    for r in all_results:
        if r.get("content") and len(r["content"]) > 500:
            r["content"] = r["content"][:500] + "..."

    response["tsg_results"] = all_results
    logger.info("search_tsgs: returning %d results", len(all_results))
    return json.dumps(response, indent=2, default=str)


async def get_incident(incident_id: str) -> str:
    """Get full details of a specific incident by its ID.

    Fetches live data from the ICM OData API first.  Falls back to the
    pre-indexed Azure AI Search data if the live call fails.

    Args:
        incident_id: The incident ID (e.g. "503941234").
    """
    logger.info("get_incident: id=%s", incident_id)

    # Check restricted CRI access (if OBO is enabled)
    user_ctx = get_user_context()
    kusto_token = user_ctx.kusto_token if user_ctx else None
    user_email = user_ctx.email if user_ctx else None

    if kusto_token:
        logger.info("get_incident: OBO active, checking restricted CRI access for user=%s, incident=%s", user_email, incident_id)
    else:
        logger.info("get_incident: OBO not available (disabled or no token), skipping restricted CRI check")

    if not check_incident_access(incident_id, user_email, kusto_token):
        logger.warning("get_incident: ACCESS DENIED for user=%s on incident=%s", user_email, incident_id)
        return json.dumps({
            "error": f"Access denied: incident {incident_id} is restricted and you are not authorized to view it."
        })

    # Try live OData first
    live = _fetch_live_incident(incident_id)
    if live:
        logger.info("get_incident: returning live data for %s", incident_id)
        return json.dumps(live, indent=2, default=str)

    logger.info("get_incident: live fetch failed, falling back to index for %s", incident_id)

    # Fallback: try ICM index by document key
    search_client = clients.get_search_client(ICM_INDEX)
    try:
        result = search_client.get_document(key=incident_id)
        formatted = _format_icm_result(result)
        formatted["index"] = ICM_INDEX
        logger.info("get_incident: found in %s", ICM_INDEX)
        return json.dumps(formatted, indent=2, default=str)
    except Exception:
        pass

    # Fallback: search by ticket_id filter
    response = search_client.search(
        search_text=incident_id,
        filter=f"ticket_id eq '{incident_id}'",
        select=ICM_SELECT_FIELDS,
        top=1,
    )
    for item in response:
        formatted = _format_icm_result(item)
        formatted["index"] = ICM_INDEX
        logger.info("get_incident: found via search in %s", ICM_INDEX)
        return json.dumps(formatted, indent=2, default=str)

    return json.dumps({"error": f"Incident {incident_id} not found in any index."})


async def batch_search(
    searches: str,
    max_results_per_search: int = 5,
) -> str:
    """Run multiple targeted searches in a single call.

    Accepts a JSON array of search specs, embeds all queries in parallel,
    then runs all searches in parallel.  This lets the LLM craft precise
    per-symptom queries while avoiding multiple MCP round-trips.

    Typical workflow: call get_incident first to read the symptoms, then
    call batch_search with targeted queries derived from those symptoms.

    Args:
        searches: JSON array of search objects. Each object has:
            - "type": "tsg" or "icm"
            - "query": the search query string
            - "index": "all", "msal-broker", or "auth-app" (default "all")
        max_results_per_search: Max results per individual search (default 5).

    Example:
        searches = '[
            {"type": "icm", "query": "Authenticator push notification not received"},
            {"type": "tsg", "query": "MFA code not delivered mobile device"}
        ]'
    """
    try:
        search_specs = json.loads(searches)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON in searches parameter: {e}"})

    if not isinstance(search_specs, list) or not search_specs:
        return json.dumps({"error": "searches must be a non-empty JSON array"})

    logger.info("batch_search: %d searches, max_results_per_search=%d", len(search_specs), max_results_per_search)

    # 1. Embed all unique queries in parallel
    unique_queries = list({s.get("query", "") for s in search_specs if s.get("query")})
    embed_futures = [asyncio.to_thread(clients.embed_query, q) for q in unique_queries]
    vectors = await asyncio.gather(*embed_futures)
    query_to_vector = dict(zip(unique_queries, vectors))

    # 2. Build date filter for ICM searches (exclude > 2 years old)
    two_years_ago = (datetime.utcnow() - timedelta(days=730)).strftime("%Y-%m-%dT00:00:00Z")
    date_filter = f"ticket_create_date ge {two_years_ago}"

    # 3. Fire all searches in parallel
    async def _run_one(spec: dict, spec_idx: int) -> dict:
        search_type = spec.get("type") or spec.get("search_type", "tsg")
        query = spec.get("query", "")
        index = spec.get("index", "all")
        vector = query_to_vector.get(query, [])

        if search_type == "icm":
            indexes = _resolve_icm_indexes(index)
            per_index = max(1, max_results_per_search // len(indexes))

            async def _icm_one(idx: str) -> list[dict]:
                results = await asyncio.to_thread(
                    _hybrid_search_icm, idx, query, vector, per_index, date_filter
                )
                for r in results:
                    r["index"] = idx
                return results

            batches = await asyncio.gather(*[_icm_one(idx) for idx in indexes])
            all_results = [r for batch in batches for r in batch]
            all_results.sort(key=lambda x: x.get("score") or 0, reverse=True)
            all_results = all_results[:max_results_per_search]

            # Filter out restricted ICMs the user can't access (if OBO enabled)
            user_ctx = get_user_context()
            if user_ctx and user_ctx.kusto_token:
                pre_filter_count = len(all_results)
                all_results = [
                    r for r in all_results
                    if check_incident_access(
                        r.get("ticket_id", ""),
                        user_ctx.email,
                        user_ctx.kusto_token,
                    )
                ]
                filtered = pre_filter_count - len(all_results)
                if filtered:
                    logger.info("batch_search: filtered %d restricted ICMs for user=%s", filtered, user_ctx.email)

            # Trim summaries
            for r in all_results:
                if r.get("summary") and len(r["summary"]) > 500:
                    r["summary"] = r["summary"][:500] + "..."

            return {
                "search_index": spec_idx,
                "type": "icm",
                "query": query,
                "index": index,
                "results": all_results,
            }
        else:  # tsg
            indexes = _resolve_tsg_indexes(index)
            per_index = max(1, max_results_per_search // len(indexes))

            async def _tsg_one(idx: str) -> list[dict]:
                results = await asyncio.to_thread(
                    _hybrid_search_tsg, idx, query, vector, per_index
                )
                for r in results:
                    r["index"] = idx
                return results

            batches = await asyncio.gather(*[_tsg_one(idx) for idx in indexes])
            all_results = [r for batch in batches for r in batch]
            all_results.sort(key=lambda x: x.get("score") or 0, reverse=True)
            all_results = all_results[:max_results_per_search]

            # Trim content
            for r in all_results:
                if r.get("content") and len(r["content"]) > 500:
                    r["content"] = r["content"][:500] + "..."

            return {
                "search_index": spec_idx,
                "type": "tsg",
                "query": query,
                "index": index,
                "results": all_results,
            }

    search_results = await asyncio.gather(*[_run_one(s, i) for i, s in enumerate(search_specs)])

    total_results = sum(len(s["results"]) for s in search_results)
    logger.info("batch_search: returning %d total results across %d searches", total_results, len(search_results))
    return json.dumps({"searches": list(search_results)}, indent=2, default=str)


async def post_icm_discussion(
    incident_id: str,
    text: str,
) -> str:
    """Post an investigation report or note to an IcM incident's discussion thread.

    Use this after completing an investigation to record your findings
    directly on the IcM incident so the on-call DRI can see them.

    The text should be a concise investigation report (under 2500 characters).
    It will be posted as a plain-text discussion entry attributed to
    "DRI Copilot".

    Args:
        incident_id: The IcM incident ID (e.g. "781733064").
        text: The discussion entry text to post. Keep under 2500 chars.
    """
    logger.info("post_icm_discussion: incident_id=%s, text_len=%d", incident_id, len(text))

    if len(text) > _POST_DISCUSSION_MAX_LENGTH:
        text = text[:_POST_DISCUSSION_MAX_LENGTH] + "\n\n[Truncated — exceeded character limit]"

    result = await asyncio.to_thread(
        icm_client.post_discussion_entry, incident_id, text
    )
    return json.dumps(result, indent=2)
