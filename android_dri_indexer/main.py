"""Entrypoint for the DRI search indexer cron job."""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from android_dri_indexer import config
from android_dri_indexer.index_schemas import ensure_indexes, delete_index
from android_dri_indexer.icm_indexer import run_icm_indexer

_ANDROID_SHIELD_TEAM = "CLOUDIDENTITYAUTHNCLIENT\\AndroidShield"
_ARIA_RETENTION_DAYS = 60


def _cleanup_aria_incidents(logger: logging.Logger) -> None:
    """Delete AndroidShield ICM documents older than 2 months from the index."""
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient

    search = SearchClient(
        endpoint=config.SEARCH_ENDPOINT,
        index_name=config.ICM_INDEX_NAME,
        credential=DefaultAzureCredential(),
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=_ARIA_RETENTION_DAYS)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    odata_filter = (
        f"ticket_owning_team eq '{_ANDROID_SHIELD_TEAM}' "
        f"and ticket_modified_date lt {cutoff_iso}"
    )

    logger.info(
        "Searching for AndroidShield ICMs older than %s (%d days) …",
        cutoff_iso, _ARIA_RETENTION_DAYS,
    )

    results = search.search(
        search_text="*", filter=odata_filter, select=["ticket_id", "ticket_modified_date"],
    )
    to_delete = [{"ticket_id": doc["ticket_id"]} for doc in results]

    if not to_delete:
        logger.info("No AndroidShield ICMs older than %d days found", _ARIA_RETENTION_DAYS)
        return

    logger.info("Found %d AndroidShield ICMs to delete", len(to_delete))
    batch_size = config.UPLOAD_BATCH_SIZE or 10
    deleted = 0
    for i in range(0, len(to_delete), batch_size):
        batch = to_delete[i : i + batch_size]
        result = search.delete_documents(batch)
        ok = sum(1 for r in result if r.succeeded)
        failed = [(r.key, r.error_message) for r in result if not r.succeeded]
        for key, err in failed:
            logger.error("Delete failed for %s: %s", key, err)
        deleted += ok
    logger.info("Deleted %d/%d AndroidShield ICMs", deleted, len(to_delete))


def main() -> None:
    parser = argparse.ArgumentParser(description="DRI Search Indexer")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to JSON config file (e.g. configs/config_android.json). "
             "Can also be set via INDEXER_CONFIG env var.",
    )
    parser.add_argument("--icm", action="store_true", help="Run ICM indexer")
    parser.add_argument(
        "--skip-index-setup", action="store_true",
        help="Skip index creation/update",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Delete and recreate the target index(es) before indexing",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Use full lookback_hours instead of scheduled_lookback_hours",
    )
    parser.add_argument(
        "--cleanupAriaIncidents", action="store_true",
        help="Delete AndroidShield ICMs older than 2 months from the index",
    )
    args = parser.parse_args()

    import os
    config_path = os.environ.get("INDEXER_CONFIG") or args.config
    if not config_path:
        parser.error("--config is required (or set INDEXER_CONFIG env var)")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("dri_indexer")

    # Load config before anything touches config module attributes
    config.load(config_path)
    logger.info("Loaded config: %s (service_id=%s)", config_path, config.SERVICE_ID)

    t0 = time.time()
    logger.info("=== Indexer starting ===")

    if args.cleanupAriaIncidents:
        _cleanup_aria_incidents(logger)
        logger.info("=== Cleanup complete in %.1fs ===", time.time() - t0)
        return

    total_errors = 0
    try:
        if args.clean:
            logger.info("Deleting ICM index for clean rebuild …")
            delete_index(config.ICM_INDEX_NAME)

        if not args.skip_index_setup:
            logger.info("Ensuring search indexes …")
            ensure_indexes(icm=True, tsg=False)

        logger.info("--- ICM indexer ---")
        total_errors += run_icm_indexer(fresh=args.fresh)

    except Exception:
        logger.exception("Indexer failed")
        sys.exit(1)

    if total_errors:
        logger.error("Indexer finished with %d errors", total_errors)
        sys.exit(1)

    logger.info("=== Indexer complete in %.1fs ===", time.time() - t0)
