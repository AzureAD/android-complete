"""ICM indexing pipeline: Kusto → GPT-4 summary → embeddings → Azure Search."""

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.search.documents import SearchClient
from azure.storage.blob import ContainerClient

from android_dri_indexer import config
from android_dri_indexer.embeddings import generate_embedding, chat_completion, count_tokens
from android_dri_indexer.pii_sanitizer import sanitize_text as _sanitize_pii

logger = logging.getLogger(__name__)

# Parallelism: keep low to avoid Kusto gRPC flooding and OpenAI 429s
_MAX_WORKERS = int(os.environ.get("ICM_INDEXER_WORKERS", "3"))

# Limit concurrent Kusto queries to avoid gRPC connection exhaustion
_KUSTO_SEMAPHORE = threading.Semaphore(2)

# Max tokens to send to GPT-4o (leave room for system prompt + response)
_MAX_CONTEXT_TOKENS = 120_000

# ── Kusto query templates ────────────────────────────────────────────────

_KQL_ALL_ICMS = """\
IncidentsSnapshotV2
| where Status == "RESOLVED" or Status == "ACTIVE" or Status == "MITIGATED"
| where ModifiedDate > ago({lookback}h)
{max_age_filter}| where MitigatedBy != "healthmanagesvc"
| where {team_filter}
| summarize arg_max(ModifiedDate, RootCauseId) by IncidentId
"""

_KQL_ICM_DETAIL = """\
IncidentsSnapshotV2
| where IncidentId == {icm_id}
| project IncidentId, SourceName, OwningTenantName, OwningTeamName,
          CreateDate, ResolveDate, ModifiedDate,
          TTM = (MitigateDate - CreateDate) / 60m,
          Title, Severity, IncidentType, RootCauseId,
          Mitigation, Summary, Tags, HowFixed
| take 1
"""

_KQL_ICM_DESCRIPTIONS = """\
IncidentDescriptions
| where IncidentId == {icm_id}
| where ChangedBy <> "gautosvc"
| summarize arg_max(Lens_IngestionTime, Text, ChangedBy) by HistoryId
| project Text
"""

_KQL_ROOT_CAUSE = """\
RootCauses
| where RootCauseId == {root_cause_id}
| summarize arg_max(ModifiedDate, Category, SubCategory, Description) by RootCauseId
| project Category, SubCategory, Description
"""

# ── GPT-4 summarisation prompt ───────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert at summarising incident tickets for MSAL Android, Broker, \
and Microsoft Authenticator.

TASK:
Summarise the incident to extract:
1. Issues the customer ran into (timestamps, error codes/messages).
2. Final solution / mitigation / root-cause analysis.
3. Key investigation steps and findings.
4. Root cause if available.
5. Names of any TSGs used.

RESPONSE FORMAT — valid JSON only, no markdown fences:
{
  "error_summary": "...",
  "investigation_steps": "...",
  "mitigation": "...",
  "root_cause": "...",
  "tsg": "..."
}"""

# Regex for stripping HTML tags from ICM description entries
_HTML_TAG_RE = re.compile(r"<[^>]+>")


# ── Helpers ──────────────────────────────────────────────────────────────

def _build_team_filter(teams: list[str]) -> str:
    clauses = [f'OwningTeamName =~ "{t.replace(chr(92), chr(92)*2)}"' for t in teams]
    return " or ".join(clauses)


def _query_kusto(client: KustoClient, query: str, use_semaphore: bool = True) -> list[dict]:
    if use_semaphore:
        with _KUSTO_SEMAPHORE:
            response = client.execute(config.ICM_KUSTO_DATABASE, query)
    else:
        response = client.execute(config.ICM_KUSTO_DATABASE, query)
    table = response.primary_results[0]
    columns = [col.column_name for col in table.columns]
    return [dict(zip(columns, row)) for row in table.rows]


def _fmt_dt(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    return str(val)


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text)


# ── Pipeline ─────────────────────────────────────────────────────────────

def _upload_batch(
    search: SearchClient, batch: list[dict], errors: int,
) -> int:
    """Upload a batch to ACS, log failures, return updated error count."""
    result = search.merge_or_upload_documents(batch)
    ok = sum(1 for r in result if r.succeeded)
    failed = [(r.key, r.error_message) for r in result if not r.succeeded]
    for key, err in failed:
        logger.error("Upload failed for %s: %s", key, err)
    if failed:
        logger.warning("Batch upload: %d/%d succeeded, %d failed", ok, len(batch), len(failed))
    else:
        logger.info("Uploaded batch of %d: %d succeeded", len(batch), ok)
    return errors + len(failed)


def run_icm_indexer(*, fresh: bool = False) -> int:
    """Fetch recent ICMs from Kusto, summarise, embed, and upload.

    Returns the number of errors encountered.
    """
    logger.info("ICM indexer starting (%d team groups)", len(config.ICM_TEAM_GROUPS))

    # Blob storage client for backup output
    blob_container: ContainerClient | None = None
    if config.ICM_BLOB_CONTAINER_URL:
        blob_container = ContainerClient.from_container_url(
            config.ICM_BLOB_CONTAINER_URL,
            credential=DefaultAzureCredential(),
        )
        logger.info("Blob output enabled: %s/%s", config.ICM_BLOB_CONTAINER_URL, config.ICM_BLOB_PREFIX)

    # Kusto client — managed identity in cloud, az-cli locally
    client_id = os.environ.get("AZURE_CLIENT_ID")
    if client_id:
        kcsb = KustoConnectionStringBuilder.with_aad_managed_service_identity_authentication(
            config.ICM_KUSTO_CLUSTER,
            client_id=client_id,
        )
    else:
        kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(
            config.ICM_KUSTO_CLUSTER,
        )
        logger.info("No AZURE_CLIENT_ID — using az-cli auth for Kusto")
    kusto = KustoClient(kcsb)

    # Azure Search client
    search = SearchClient(
        endpoint=config.SEARCH_ENDPOINT,
        index_name=config.ICM_INDEX_NAME,
        credential=DefaultAzureCredential(),
    )

    # Step 1 — list ICMs across all team groups (each may have a different lookback)
    icm_rows: list[dict] = []
    for group in config.ICM_TEAM_GROUPS:
        if config.ICM_LOOKBACK_HOURS is not None:
            lookback = config.ICM_LOOKBACK_HOURS
        elif fresh or "scheduled_lookback_hours" not in group:
            lookback = group["lookback_hours"]
        else:
            lookback = group["scheduled_lookback_hours"]
        max_age = group.get("max_age_hours")
        max_age_filter = f"| where ModifiedDate < ago({max_age}h)\n" if max_age else ""
        query = _KQL_ALL_ICMS.format(
            lookback=lookback,
            max_age_filter=max_age_filter,
            team_filter=_build_team_filter(group["teams"]),
        )
        rows = _query_kusto(kusto, query, use_semaphore=False)
        logger.info("Group '%s' (lookback=%dh, max_age=%s): %d ICMs", group["label"], lookback, max_age or 'none', len(rows))
        icm_rows.extend(rows)

    # Deduplicate by IncidentId (teams could theoretically overlap)
    seen = set()
    unique_rows = []
    for r in icm_rows:
        if r["IncidentId"] not in seen:
            seen.add(r["IncidentId"])
            unique_rows.append(r)
    icm_rows = unique_rows

    logger.info("Total unique ICMs to process: %d", len(icm_rows))
    if not icm_rows:
        return 0

    # Skip ICMs whose ModifiedDate hasn't changed since last indexing.
    # This avoids re-processing the same content within the lookback window.
    # ICMs not yet in the index, or with a newer ModifiedDate, are always processed.
    indexed_modified: dict[int, str | None] = {}
    all_ids = [str(r["IncidentId"]) for r in icm_rows]

    def _get_modified_date(tid: str) -> tuple[int, str | None]:
        try:
            doc = search.get_document(tid, selected_fields=["ticket_modified_date"])
            return (int(tid), doc.get("ticket_modified_date"))
        except Exception:
            return (int(tid), None)

    with ThreadPoolExecutor(max_workers=10) as pool:
        for icm_id, mod_date in pool.map(_get_modified_date, all_ids):
            if mod_date is not None:
                indexed_modified[icm_id] = mod_date

    # Build a lookup from Kusto rows: IncidentId → ModifiedDate from Kusto
    kusto_modified: dict[int, str | None] = {}
    for r in icm_rows:
        kusto_modified[r["IncidentId"]] = _fmt_dt(r.get("ModifiedDate"))

    # Skip only if: (a) already in index, AND (b) ModifiedDate matches
    skip_ids: set[int] = set()
    for icm_id, idx_mod in indexed_modified.items():
        kusto_mod = kusto_modified.get(icm_id)
        if idx_mod and kusto_mod and idx_mod == kusto_mod:
            skip_ids.add(icm_id)

    if skip_ids:
        logger.info(
            "Skipping %d ICMs with unchanged ModifiedDate: %s",
            len(skip_ids), sorted(skip_ids),
        )
        icm_rows = [r for r in icm_rows if r["IncidentId"] not in skip_ids]
        logger.info("%d ICMs remaining to process", len(icm_rows))

    if not icm_rows:
        logger.info("All ICMs already up-to-date — nothing to do")
        return 0

    documents: list[dict] = []
    errors = 0

    def _process_one(row: dict) -> dict | None:
        """Process a single ICM: Kusto detail → GPT summary → embeddings → doc."""
        icm_id = row["IncidentId"]
        root_cause_id = row.get("RootCauseId")

        # Fetch detail, descriptions, root cause
        details = _query_kusto(kusto, _KQL_ICM_DETAIL.format(icm_id=icm_id))
        if not details:
            logger.warning("No detail for ICM %s — skipping", icm_id)
            return None
        d = details[0]

        desc_rows = _query_kusto(
            kusto, _KQL_ICM_DESCRIPTIONS.format(icm_id=icm_id),
        )
        descriptions = "\n---\n".join(
            _strip_html(r["Text"]) for r in desc_rows if r.get("Text")
        )
        descriptions, _ = _sanitize_pii(descriptions)

        rc_text = ""
        if root_cause_id:
            rc_rows = _query_kusto(
                kusto, _KQL_ROOT_CAUSE.format(root_cause_id=root_cause_id),
            )
            if rc_rows:
                rc = rc_rows[0]
                rc_text = (
                    f"Category: {rc.get('Category', '')}\n"
                    f"SubCategory: {rc.get('SubCategory', '')}\n"
                    f"Description: {rc.get('Description', '')}"
                )

        # Build context for GPT-4
        title = d.get("Title", "")
        title, _ = _sanitize_pii(title)
        context = (
            f"Title: {title}\n"
            f"Severity: {d.get('Severity', '')}\n"
            f"Type: {d.get('IncidentType', '')}\n"
            f"Team: {d.get('OwningTeamName', '')}\n"
            f"Mitigation: {d.get('Mitigation', '')}\n"
            f"Summary: {d.get('Summary', '')}\n"
            f"HowFixed: {d.get('HowFixed', '')}\n"
            f"Tags: {d.get('Tags', '')}\n\n"
            f"--- Description History ---\n{descriptions}\n\n"
            f"--- Root Cause ---\n{rc_text}"
        )

        # Truncate context if too long for GPT-4o
        ctx_tokens = count_tokens(context)
        if ctx_tokens > _MAX_CONTEXT_TOKENS:
            # Rough truncation: cut context proportionally
            ratio = _MAX_CONTEXT_TOKENS / ctx_tokens
            context = context[: int(len(context) * ratio)]
            logger.info("Truncated ICM %s context from %d to ~%d tokens", icm_id, ctx_tokens, _MAX_CONTEXT_TOKENS)

        # GPT-4 summarisation
        raw = chat_completion(_SYSTEM_PROMPT, context)
        try:
            clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            logger.warning("Bad JSON from GPT for ICM %s — using raw text", icm_id)
            parsed = {
                "error_summary": raw[:3000],
                "investigation_steps": "",
                "mitigation": "",
                "root_cause": "",
                "tsg": "",
            }

        summary_text = json.dumps(parsed)
        mitigation = parsed.get("mitigation", "")
        tags = (d.get("Tags") or "").strip()

        # Embeddings
        embed_summary = (
            f"{parsed.get('error_summary', '')} "
            f"{mitigation} "
            f"{parsed.get('investigation_steps', '')}"
        )
        summary_vec = generate_embedding(embed_summary)
        title_vec = generate_embedding(title)
        mit_vec = (
            generate_embedding(mitigation)
            if mitigation
            else [0.0] * config.EMBEDDING_DIMENSIONS
        )
        tags_vec = (
            generate_embedding(tags)
            if tags
            else [0.0] * config.EMBEDDING_DIMENSIONS
        )

        logger.info("Processed ICM %s: %s", icm_id, title[:80])
        owning_team = d.get("OwningTeamName", "")
        service_id = config.ICM_TEAM_TO_SERVICE_ID.get(owning_team, config.SERVICE_ID)
        return {
            "ticket_id": str(icm_id),
            "service_id": service_id,
            "ticket_title": title,
            "ticket_type": d.get("IncidentType", ""),
            "ticket_owning_team": d.get("OwningTeamName", ""),
            "ticket_create_date": _fmt_dt(d.get("CreateDate")),
            "ticket_resolve_date": _fmt_dt(d.get("ResolveDate")),
            "ticket_modified_date": _fmt_dt(d.get("ModifiedDate")),
            "ticket_summary": summary_text,
            "ticket_tags": tags,
            "ticket_summary_vector": summary_vec,
            "ticket_title_vector": title_vec,
            "ticket_mitigation_vector": mit_vec,
            "ticket_tags_vector": tags_vec,
        }

    # Process ICMs in parallel
    logger.info("Processing %d ICMs with %d workers", len(icm_rows), _MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_process_one, row): row for row in icm_rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                doc = future.result()
                if doc:
                    # Write to blob backup
                    if blob_container:
                        blob_name = f"{config.ICM_BLOB_PREFIX}{doc['ticket_id']}.json"
                        blob_container.upload_blob(
                            name=blob_name,
                            data=json.dumps(doc, default=str),
                            overwrite=True,
                        )
                    documents.append(doc)
                    # Upload in batches as we go to avoid losing progress on timeout
                    if len(documents) >= config.UPLOAD_BATCH_SIZE:
                        batch = documents[:config.UPLOAD_BATCH_SIZE]
                        errors = _upload_batch(search, batch, errors)
                        documents[:config.UPLOAD_BATCH_SIZE] = []
            except Exception:
                logger.exception("Error processing ICM %s", row["IncidentId"])
                errors += 1

    # Upload remaining documents
    if documents:
        for i in range(0, len(documents), config.UPLOAD_BATCH_SIZE):
            batch = documents[i : i + config.UPLOAD_BATCH_SIZE]
            errors = _upload_batch(search, batch, errors)

    logger.info(
        "ICM indexer done: %d documents, %d errors", len(documents), errors,
    )
    return errors
