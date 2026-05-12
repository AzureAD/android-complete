"""TSG indexing pipeline: Git wiki → markdown chunking → embeddings → Azure Search."""

import hashlib
import logging
import os
import base64
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

from android_dri_indexer import config
from android_dri_indexer.embeddings import generate_embedding, chat_completion, count_tokens

logger = logging.getLogger(__name__)

# Azure DevOps resource scope for token exchange
_ADO_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"


# ── Git helpers ──────────────────────────────────────────────────────────

def _get_ado_token() -> str:
    """Acquire an Azure DevOps-scoped token via managed identity.

    Uses the same mechanism as DRICopilot: exchange a managed-identity
    token for one scoped to Azure DevOps.
    """
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
    )
    token = credential.get_token(_ADO_SCOPE)
    return token.token


def _clone_wiki(
    source: str, branch: str, dest: Path, sparse_paths: list[str] | None = None,
) -> None:
    """Shallow-clone a Git wiki repo, authenticating via managed identity.

    When *sparse_paths* is provided, uses sparse-checkout so only those
    directories are materialised on disk — dramatically reducing memory
    and bandwidth for large mono-repos like IdentityWiki.
    """
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
        logger.info(
            "Sparse-cloning %s (branch=%s, %d paths) …",
            source, branch, len(sparse_paths),
        )
        # 1. Clone without checking out files
        _run([
            "git", "clone", "--no-checkout",
            "--single-branch", "--branch", branch,
            "--depth", "1",
            auth_url, str(dest),
        ])
        # 2. Enable sparse-checkout in cone mode
        _run(["git", "sparse-checkout", "init", "--cone"], cwd=dest)
        # 3. Set the folders we actually need
        _run(["git", "sparse-checkout", "set", *sparse_paths], cwd=dest)
        # 4. Checkout — only materialises the sparse paths
        _run(["git", "checkout"], cwd=dest)
    else:
        logger.info("Cloning %s (branch=%s) …", source, branch)
        _run([
            "git", "clone",
            "--single-branch", "--branch", branch,
            "--depth", "1",
            auth_url, str(dest),
        ])


def _find_markdown_files(
    root: Path, folder: str, extensions: list[str],
) -> list[Path]:
    search_dir = root / folder
    if not search_dir.exists():
        logger.warning("Folder not found in clone: %s", search_dir)
        return []
    files: list[Path] = []
    for ext in extensions:
        files.extend(search_dir.rglob(f"*.{ext}"))
    return sorted(files)


# ── Markdown chunking ────────────────────────────────────────────────────

def _chunk_markdown(
    text: str,
    chunk_size: int = config.CHUNK_SIZE_TOKENS,
    chunk_overlap: int = config.CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """Split markdown into token-bounded chunks, preferring header boundaries.

    Strategy:
      1. Split on H1/H2/H3 headers.
      2. If a section exceeds *chunk_size*, split further on blank lines.
      3. Carry forward *chunk_overlap* tokens of context between chunks.
    """
    # Split keeping the header line with its section
    sections = re.split(r"(?=^#{1,3}\s)", text, flags=re.MULTILINE)

    chunks: list[str] = []
    buf = ""
    buf_tokens = 0

    def _flush(force: bool = False) -> None:
        nonlocal buf, buf_tokens
        stripped = buf.strip()
        if stripped and (force or count_tokens(stripped) >= 30):
            chunks.append(stripped)
        buf = ""
        buf_tokens = 0

    def _overlap_prefix() -> str:
        """Return the last ~chunk_overlap tokens of the most recent chunk."""
        if not chunks or chunk_overlap <= 0:
            return ""
        # Approximate: 1 token ≈ 4 chars
        tail = chunks[-1][-(chunk_overlap * 4) :]
        return tail

    for section in sections:
        section = section.strip()
        if not section:
            continue

        sec_tokens = count_tokens(section)

        # Fits in the current buffer?
        if buf_tokens + sec_tokens <= chunk_size:
            buf = f"{buf}\n\n{section}" if buf else section
            buf_tokens = count_tokens(buf)
            continue

        # Flush whatever we have
        _flush()

        # If section itself is within budget, start a new buffer
        if sec_tokens <= chunk_size:
            prefix = _overlap_prefix()
            buf = f"{prefix}\n\n{section}" if prefix else section
            buf_tokens = count_tokens(buf)
            continue

        # Section too large → split on blank lines
        paragraphs = section.split("\n\n")
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            para_tokens = count_tokens(para)
            if buf_tokens + para_tokens <= chunk_size:
                buf = f"{buf}\n\n{para}" if buf else para
                buf_tokens = count_tokens(buf)
            else:
                _flush()
                prefix = _overlap_prefix()
                buf = f"{prefix}\n\n{para}" if prefix else para
                buf_tokens = count_tokens(buf)

    _flush(force=True)
    return chunks


# ── Keyword generation ───────────────────────────────────────────────────

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


# ── Image extraction ─────────────────────────────────────────────────────

_IMAGE_REF_RE = re.compile(
    r'!\[.*?\]\((.+?\.(?:png|jpg|jpeg|gif))(?:\s".*?")?\)',
    re.IGNORECASE,
)


def _extract_images(md_file: Path) -> dict[str, str]:
    """Extract base64-encoded images referenced in a markdown file.

    Returns a dict of ``{image_path: base64_string}`` for images that
    exist on disk relative to *md_file*.
    """
    text = md_file.read_text(encoding="utf-8", errors="replace")
    refs = _IMAGE_REF_RE.findall(text)
    if not refs:
        return {}

    images: dict[str, str] = {}
    parent = md_file.parent
    for ref in refs:
        # Handle relative paths (most common in wikis)
        img_path = parent / ref
        if not img_path.exists():
            # Try from repo root — some wikis use absolute-style paths
            continue
        try:
            raw = img_path.read_bytes()
            images[ref] = base64.b64encode(raw).decode("ascii")
        except Exception:
            logger.debug("Could not read image %s", img_path)
    return images


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_chunk_id(filepath: str, chunk_index: int) -> str:
    """Deterministic, URL-safe document key."""
    raw = f"{filepath}::chunk{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _extract_title(filepath: Path) -> str:
    """Human-readable title for embedding (used for semantic search)."""
    stem = filepath.stem
    return stem.replace("-", " ").replace("_", " ")


def _build_wiki_url(rel_path: str) -> str:
    """Construct a browsable ADO wiki URL from a repo-relative file path.

    *rel_path* looks like:
        IdentityWiki/Services/Microsoft-Authenticator/Android/Some-Page.md

    The first segment ("IdentityWiki") is the wiki root folder in the
    git repo and must be stripped — ADO wiki page paths start from the
    level below it.  The .md extension is also removed.
    """
    # Normalise separators
    posix = rel_path.replace("\\", "/")
    # Strip wiki root folder (first segment)
    parts = posix.split("/", 1)
    page_path = parts[1] if len(parts) > 1 else parts[0]
    # Remove .md / .markdown extension
    page_path = re.sub(r"\.(md|markdown)$", "", page_path, flags=re.IGNORECASE)
    # URL-encode (spaces, special chars) but keep slashes readable
    encoded = "/".join(
        urllib.parse.quote(segment, safe="") for segment in page_path.split("/")
    )
    return f"{config.WIKI_BASE_URL}?pagePath=/{encoded}"


# ── Pipeline ─────────────────────────────────────────────────────────────

def run_tsg_indexer() -> None:
    """Clone wiki repos, chunk markdown, embed, and upload to Azure Search."""
    logger.info("TSG indexer starting (%d sources)", len(config.TSG_GIT_SOURCES))

    search = SearchClient(
        endpoint=config.SEARCH_ENDPOINT,
        index_name=config.TSG_INDEX_NAME,
        credential=DefaultAzureCredential(),
    )

    documents: list[dict] = []
    errors = 0
    work_dir = tempfile.mkdtemp(prefix="tsg_indexer_")

    try:
        # Clone each unique repo once, using sparse-checkout for only the
        # folders we need — avoids OOM on large wikis like IdentityWiki.
        repo_clones: dict[str, Path] = {}

        # Pre-collect the sparse paths per (repo_url, branch)
        sparse_map: dict[str, list[str]] = {}
        for src in config.TSG_GIT_SOURCES:
            sparse_map.setdefault(src["source"], []).append(src["folder"])

        for src in config.TSG_GIT_SOURCES:
            repo_url = src["source"]
            branch = src["branch"]

            if repo_url not in repo_clones:
                clone_dest = Path(work_dir) / f"repo_{len(repo_clones)}"
                try:
                    _clone_wiki(
                        repo_url, branch, clone_dest,
                        sparse_paths=sparse_map.get(repo_url),
                    )
                    repo_clones[repo_url] = clone_dest
                except Exception:
                    logger.exception("Failed to clone %s", repo_url)
                    errors += 1
                    continue

            clone_root = repo_clones[repo_url]
            md_files = _find_markdown_files(
                clone_root, src["folder"], src["extensions"],
            )
            logger.info(
                "Found %d markdown files in %s", len(md_files), src["folder"],
            )

            for md_file in md_files:
                try:
                    text = md_file.read_text(encoding="utf-8", errors="replace")
                    if count_tokens(text) < 30:
                        continue

                    rel_path = str(md_file.relative_to(clone_root))
                    readable_title = _extract_title(md_file)
                    wiki_url = _build_wiki_url(rel_path)
                    description = src.get("description", "")
                    images = _extract_images(md_file)
                    images_json = json.dumps(images) if images else ""

                    # Embed the human-readable title for semantic search
                    title_vec = generate_embedding(readable_title)

                    chunks = _chunk_markdown(text)
                    for idx, chunk_text in enumerate(chunks):
                        content_vec = generate_embedding(chunk_text)
                        keywords = _generate_keywords(chunk_text)
                        documents.append({
                            "id": _make_chunk_id(rel_path, idx),
                            "service_id": config.SERVICE_ID,
                            "title": wiki_url,
                            "filepath": rel_path,
                            "content": chunk_text,
                            "keywords": keywords,
                            "tsg_description": description,
                            "base64_images": images_json,
                            "title_vector": title_vec,
                            "content_vector": content_vec,
                        })

                    logger.info(
                        "Chunked %s → %d chunks", rel_path, len(chunks),
                    )
                except Exception:
                    logger.exception("Error processing %s", md_file)
                    errors += 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # Upload
    if documents:
        for i in range(0, len(documents), config.UPLOAD_BATCH_SIZE):
            batch = documents[i : i + config.UPLOAD_BATCH_SIZE]
            result = search.merge_or_upload_documents(batch)
            ok = sum(1 for r in result if r.succeeded)
            logger.info(
                "Uploaded TSG batch %d–%d: %d/%d succeeded",
                i, i + len(batch), ok, len(batch),
            )

    logger.info(
        "TSG indexer done: %d chunks uploaded, %d errors",
        len(documents), errors,
    )
