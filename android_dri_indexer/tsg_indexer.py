"""
TSG indexing pipeline: Git wiki → chunk → embed → blob storage.

Writes one JSON file per chunk to Azure Blob Storage. An ACS pull indexer
then reads the blobs and pushes documents into the search index.

Key features:
  - Sparse git clone (only materializes configured folders)
  - DRICopilot-compatible chunking (MarkdownRecursiveSplitter, 700/125)
  - Per-chunk blob write (crash-resilient — partial progress survives)
  - Dedup: reads existing blob, skips if content unchanged (saves $380+/mo)
  - Stale file cleanup: deletes blobs for wiki pages that no longer exist
  - Keyword extraction via GPT-4o
  - Image extraction (base64-encoded inline images)
"""

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

from android_dri_indexer import config
from android_dri_indexer.embeddings import generate_embedding, chat_completion, count_tokens
from android_dri_indexer.markdown_splitter import MarkdownRecursiveSplitter

logger = logging.getLogger(__name__)

# Azure DevOps resource scope for managed identity token exchange
_ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"

# Parallelism for embedding + keyword generation
_EMBED_WORKERS = int(os.environ.get("TSG_EMBED_WORKERS", "3"))

# ── Git helpers ──────────────────────────────────────────────────────────────


def _get_ado_token() -> str:
    """Acquire an Azure DevOps-scoped token via managed identity."""
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
    )
    token = credential.get_token(_ADO_SCOPE)
    return token.token


def _clone_wiki(
    source: str, branch: str, dest: Path, sparse_paths: list[str] | None = None,
) -> None:
    """Shallow-clone a Git wiki repo with sparse-checkout, authenticating via MSI."""
    token = _get_ado_token()
    auth_url = source.replace("https://", f"https://oauth2:{token}@")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> None:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
        )
        if result.returncode != 0:
            safe_err = (result.stderr + result.stdout).replace(token, "***")[:500]
            raise RuntimeError(f"git command failed: {safe_err}")

    if sparse_paths:
        logger.info("Sparse-cloning %s (branch=%s, %d paths)", source, branch, len(sparse_paths))
        _run(["git", "clone", "--no-checkout", "--single-branch", "--branch", branch, "--depth", "1", auth_url, str(dest)])
        _run(["git", "sparse-checkout", "init", "--cone"], cwd=dest)
        _run(["git", "sparse-checkout", "set", *sparse_paths], cwd=dest)
        _run(["git", "checkout"], cwd=dest)
    else:
        logger.info("Cloning %s (branch=%s)", source, branch)
        _run(["git", "clone", "--single-branch", "--branch", branch, "--depth", "1", auth_url, str(dest)])


def _find_markdown_files(root: Path, folder: str, extensions: list[str]) -> list[Path]:
    """Find all markdown files in a specific folder, respecting .indexignore."""
    search_dir = root / folder
    if not search_dir.exists():
        logger.warning("Folder not found in clone: %s", search_dir)
        return []

    # Load .indexignore patterns (check folder, then repo root)
    ignore_patterns = _load_indexignore(search_dir) or _load_indexignore(root)

    files: list[Path] = []
    for ext in extensions:
        for f in search_dir.rglob(f"*.{ext}"):
            if ignore_patterns and _should_ignore(f, search_dir, ignore_patterns):
                logger.debug("Skipping (indexignore): %s", f)
                continue
            files.append(f)
    return sorted(files)


def _load_indexignore(directory: Path) -> list[str] | None:
    """Load .indexignore patterns from a directory. Returns None if not found."""
    ignore_file = directory / ".indexignore"
    if not ignore_file.exists():
        return None
    patterns = []
    for line in ignore_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    if patterns:
        logger.info("Loaded %d .indexignore patterns from %s", len(patterns), ignore_file)
    return patterns or None


def _should_ignore(file_path: Path, base_dir: Path, patterns: list[str]) -> bool:
    """Check if a file matches any .indexignore pattern."""
    rel = str(file_path.relative_to(base_dir)).replace("\\", "/")
    for pattern in patterns:
        if fnmatch(rel, pattern) or fnmatch(file_path.name, pattern):
            return True
    return False


# ── Blob helpers ─────────────────────────────────────────────────────────────


def _get_blob_client() -> ContainerClient:
    """Create a blob ContainerClient for writing TSG chunks."""
    container_url = config.TSG_BLOB_CONTAINER_URL
    if not container_url:
        raise ValueError("TSG blob_container_url not configured")
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
    )
    return ContainerClient.from_container_url(container_url=container_url, credential=credential)


def _blob_path(rel_path: str, chunk_index: int) -> str:
    """Construct the blob path for a chunk: prefix/service_id/filepath_chunkN.json"""
    prefix = config.TSG_BLOB_PREFIX or "ACS_prep/"
    # Sanitize path for blob storage
    safe_path = rel_path.replace("\\", "/").replace(".md", "").replace(".markdown", "")
    return f"{prefix}{config.SERVICE_ID}/{safe_path}_chunk{chunk_index}.json"


def _read_existing_blob(client: ContainerClient, path: str) -> Optional[dict]:
    """Read an existing blob JSON. Returns None if blob doesn't exist."""
    try:
        blob = client.download_blob(path)
        return json.loads(blob.readall())
    except Exception:
        return None


def _preload_existing_blobs(client: ContainerClient) -> dict[str, str]:
    """Preload all existing blob content fields for dedup.

    Instead of reading blobs one-by-one during processing, this lists all
    blobs under the TSG prefix and downloads their 'content' field in
    parallel. Returns a dict of {blob_path: content_text}.

    This turns ~1,500 sequential blob reads into one list + parallel downloads,
    saving ~2 min on incremental runs.
    """
    prefix = (config.TSG_BLOB_PREFIX or "ACS_prep/") + (config.SERVICE_ID or "")
    blob_names = [b.name for b in client.list_blobs(name_starts_with=prefix)]

    if not blob_names:
        return {}

    logger.info("Preloading %d existing blobs for dedup...", len(blob_names))
    content_map: dict[str, str] = {}

    def _download_content(name: str) -> tuple[str, str]:
        try:
            data = json.loads(client.download_blob(name).readall())
            return name, data.get("content", "")
        except Exception:
            return name, ""

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_download_content, name) for name in blob_names]
        for future in as_completed(futures):
            name, content = future.result()
            if content:
                content_map[name] = content

    logger.info("Preloaded %d blobs with content", len(content_map))
    return content_map


def _upload_blob(client: ContainerClient, path: str, data: dict) -> None:
    """Upload a JSON document to blob, overwriting if exists."""
    content = json.dumps(data, indent=2, ensure_ascii=False)
    client.upload_blob(path, content, overwrite=True)


def _list_existing_blobs(client: ContainerClient) -> set[str]:
    """List all existing blob paths under the TSG prefix."""
    prefix = (config.TSG_BLOB_PREFIX or "ACS_prep/") + (config.SERVICE_ID or "")
    blobs = set()
    for blob in client.list_blobs(name_starts_with=prefix):
        blobs.add(blob.name)
    return blobs


def _delete_stale_blobs(client: ContainerClient, written_paths: set[str]) -> int:
    """Delete blobs that are no longer produced by the indexer (stale wiki pages)."""
    existing = _list_existing_blobs(client)
    stale = existing - written_paths
    deleted = 0
    for path in stale:
        try:
            client.delete_blob(path)
            deleted += 1
        except Exception as e:
            logger.warning("Failed to delete stale blob %s: %s", path, e)
    if deleted:
        logger.info("Deleted %d stale blobs", deleted)
    return deleted


# ── Keyword extraction ───────────────────────────────────────────────────────

_KEYWORD_SYSTEM_PROMPT = """\
CONTEXT:
You are given troubleshooting guides written by engineers to assist in debugging problems.
Troubleshooting guides are written without clear structure and may contain superfluous text.
You will be given parts of a troubleshooting guide.

TASK:
Provide a list of keywords or phrases that represent the topics or subject of the given troubleshooting guide.
The order of the keywords should be based on its priority.
Write those keywords first which are more relevant or important to the troubleshooting guide.
The keywords must be separated by commas.

Note:
1. DO NOT include common words such as "the", "and", "is", etc.
2. DO NOT include proper nouns such as names of subscriptions, clusters, workspaces.
3. DO NOT include generic phrases such as "troubleshooting".
4. Include special entity names discussed in the guide.
5. ONLY include words from the guide."""


def _generate_keywords(chunk: str) -> str:
    """Generate comma-separated keywords for a TSG chunk via GPT-4o."""
    try:
        return chat_completion(_KEYWORD_SYSTEM_PROMPT, chunk[:12_000])
    except Exception:
        logger.warning("Keyword generation failed, using empty string")
        return ""


# ── Image extraction ─────────────────────────────────────────────────────────

_IMAGE_REF_RE = re.compile(
    r'!\[.*?\]\((.+?\.(?:png|jpg|jpeg|gif))(?:\s".*?")?\)',
    re.IGNORECASE,
)


def _extract_images(md_file: Path) -> dict[str, str]:
    """Extract base64-encoded images referenced in a markdown file."""
    text = md_file.read_text(encoding="utf-8", errors="replace")
    refs = _IMAGE_REF_RE.findall(text)
    if not refs:
        return {}
    images: dict[str, str] = {}
    parent = md_file.parent
    for ref in refs:
        img_path = parent / ref
        if not img_path.exists():
            continue
        try:
            raw = img_path.read_bytes()
            images[ref] = base64.b64encode(raw).decode("ascii")
        except Exception:
            logger.debug("Could not read image %s", img_path)
    return images


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_chunk_id(filepath: str, chunk_index: int) -> str:
    """Deterministic, URL-safe document key."""
    raw = f"{filepath}::chunk{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _build_wiki_url(rel_path: str) -> str:
    """Construct a browsable ADO wiki URL from a repo-relative file path."""
    posix = rel_path.replace("\\", "/")
    parts = posix.split("/", 1)
    page_path = parts[1] if len(parts) > 1 else parts[0]
    page_path = re.sub(r"\.(md|markdown)$", "", page_path, flags=re.IGNORECASE)
    encoded = "/".join(
        urllib.parse.quote(segment, safe="") for segment in page_path.split("/")
    )
    return f"{config.WIKI_BASE_URL}?pagePath=/{encoded}"


# ── Main pipeline ────────────────────────────────────────────────────────────


def run_tsg_indexer() -> int:
    """Clone wiki repos, chunk markdown, embed, and upload to blob storage.

    Returns the number of errors encountered.
    """
    logger.info("TSG indexer starting (%d sources)", len(config.TSG_GIT_SOURCES))

    blob_client = _get_blob_client()
    splitter = MarkdownRecursiveSplitter(
        chunk_size=config.CHUNK_SIZE_TOKENS,
        chunk_overlap=config.CHUNK_OVERLAP_TOKENS,
    )

    # Preload existing blob content for fast dedup (parallel download)
    existing_content = _preload_existing_blobs(blob_client)

    written_blob_paths: set[str] = set()
    errors = 0
    stats = {"processed": 0, "skipped_dedup": 0, "new_or_updated": 0}
    work_dir = tempfile.mkdtemp(prefix="tsg_indexer_")

    try:
        # Pre-collect sparse paths per repo for efficient cloning
        sparse_map: dict[str, list[str]] = {}
        for src in config.TSG_GIT_SOURCES:
            sparse_map.setdefault(src["source"], []).append(src["folder"])

        repo_clones: dict[str, Path] = {}

        for src in config.TSG_GIT_SOURCES:
            repo_url = src["source"]
            branch = src["branch"]
            folder = src["folder"]
            extensions = src.get("extensions", ["md", "markdown"])
            description = src.get("description", "")

            # Clone repo (once per unique URL)
            if repo_url not in repo_clones:
                clone_dest = Path(work_dir) / f"repo_{len(repo_clones)}"
                try:
                    _clone_wiki(repo_url, branch, clone_dest, sparse_paths=sparse_map.get(repo_url))
                    repo_clones[repo_url] = clone_dest
                except Exception:
                    logger.exception("Failed to clone %s", repo_url)
                    errors += 1
                    continue

            clone_root = repo_clones[repo_url]
            md_files = _find_markdown_files(clone_root, folder, extensions)
            logger.info("Found %d markdown files in %s", len(md_files), folder)

            # Collect chunks to process for this folder
            chunks_to_process: list[tuple[str, str, int, str, str]] = []  # (blob_path, chunk_text, idx, wiki_url, description, images_json, rel_path)

            for md_file in md_files:
                try:
                    text = md_file.read_text(encoding="utf-8", errors="replace")
                    if count_tokens(text) < 30:
                        continue

                    rel_path = str(md_file.relative_to(clone_root))
                    wiki_url = _build_wiki_url(rel_path)
                    images = _extract_images(md_file)
                    images_json = json.dumps(images) if images else ""

                    chunks = splitter.split_text(text)

                    for idx, chunk_text in enumerate(chunks):
                        bp = _blob_path(rel_path, idx)
                        written_blob_paths.add(bp)
                        stats["processed"] += 1

                        # ── DEDUP: check preloaded content cache ──
                        if bp in existing_content and existing_content[bp] == chunk_text:
                            stats["skipped_dedup"] += 1
                            continue

                        # Queue for parallel processing
                        chunks_to_process.append((bp, chunk_text, idx, wiki_url, description, images_json, rel_path))

                    logger.info("Chunked %s → %d chunks", rel_path, len(chunks))

                except Exception:
                    logger.exception("Error processing %s", md_file)
                    errors += 1

            # ── Process queued chunks in parallel (embed + keywords + blob write) ──
            if chunks_to_process:
                logger.info("Processing %d new/updated chunks with %d workers", len(chunks_to_process), _EMBED_WORKERS)

                def _process_chunk(item):
                    bp, chunk_text, idx, wiki_url, desc, img_json, rel_p = item
                    title_vec = generate_embedding(wiki_url)
                    content_vec = generate_embedding(chunk_text)
                    keywords = _generate_keywords(chunk_text)
                    doc = {
                        "id": _make_chunk_id(rel_p, idx),
                        "service_id": config.SERVICE_ID,
                        "title": wiki_url,
                        "filepath": rel_p,
                        "content": chunk_text,
                        "keywords": keywords,
                        "tsg_description": desc,
                        "base64_images": img_json,
                        "title_vector": title_vec,
                        "content_vector": content_vec,
                    }
                    _upload_blob(blob_client, bp, doc)
                    return bp

                with ThreadPoolExecutor(max_workers=_EMBED_WORKERS) as executor:
                    futures = {executor.submit(_process_chunk, item): item[0] for item in chunks_to_process}
                    for future in as_completed(futures):
                        try:
                            future.result()
                            stats["new_or_updated"] += 1
                        except Exception as e:
                            logger.error("Failed to process chunk %s: %s", futures[future], e)
                            errors += 1

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # ── Cleanup stale blobs (wiki pages that were deleted/renamed) ──
    deleted = _delete_stale_blobs(blob_client, written_blob_paths)

    logger.info(
        "TSG indexer complete: %d processed, %d skipped (unchanged), %d new/updated, %d stale deleted, %d errors",
        stats["processed"], stats["skipped_dedup"], stats["new_or_updated"], deleted, errors,
    )
    return errors
