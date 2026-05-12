"""Entrypoint for the DRI search indexer cron job."""

import argparse
import logging
import sys
import time
import warnings

from android_dri_indexer import config
from android_dri_indexer.index_schemas import ensure_indexes, delete_index
from android_dri_indexer.icm_indexer import run_icm_indexer
from android_dri_indexer.tsg_indexer import run_tsg_indexer


def main() -> None:
    parser = argparse.ArgumentParser(description="DRI Search Indexer")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to JSON config file (e.g. configs/config_android.json). "
             "Can also be set via INDEXER_CONFIG env var.",
    )
    parser.add_argument("--icm", action="store_true", help="Run ICM indexer only")
    parser.add_argument("--tsg", action="store_true", help="Run TSG indexer only")
    parser.add_argument("--all", action="store_true", help="Run both indexers")
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

    run_icm = args.icm or args.all
    run_tsg = args.tsg or args.all
    # Default when no flags given: run both
    if not run_icm and not run_tsg:
        run_icm = True
        run_tsg = True

    if run_tsg:
        warnings.warn(
            "The --tsg / --all TSG indexer is DEPRECATED. "
            "TSGs are now indexed via the AML + ACS blob pipeline. "
            "See docs/operations-runbook.md for details.",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning("TSG indexer is DEPRECATED — use the AML blob pipeline instead")

    t0 = time.time()
    logger.info("=== Indexer starting (icm=%s, tsg=%s) ===", run_icm, run_tsg)

    total_errors = 0
    try:
        if args.clean:
            if run_icm:
                logger.info("Deleting ICM index for clean rebuild …")
                delete_index(config.ICM_INDEX_NAME)
            if run_tsg:
                logger.info("Deleting TSG index for clean rebuild …")
                delete_index(config.TSG_INDEX_NAME)

        if not args.skip_index_setup:
            logger.info("Ensuring search indexes …")
            ensure_indexes(icm=run_icm, tsg=run_tsg)

        if run_icm:
            logger.info("--- ICM indexer ---")
            total_errors += run_icm_indexer(fresh=args.fresh)

        if run_tsg:
            logger.info("--- TSG indexer ---")
            run_tsg_indexer()

    except Exception:
        logger.exception("Indexer failed")
        sys.exit(1)

    if total_errors:
        logger.error("Indexer finished with %d errors", total_errors)
        sys.exit(1)

    logger.info("=== Indexer complete in %.1fs ===", time.time() - t0)
