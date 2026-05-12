"""Shared helpers for Azure OpenAI embeddings and chat completions."""

import logging
import tiktoken
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from android_dri_indexer import config

logger = logging.getLogger(__name__)

_token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)

_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            azure_endpoint=config.AOAI_ENDPOINT,
            azure_ad_token_provider=_token_provider,
            api_version=config.AOAI_API_VERSION,
            max_retries=5,
        )
    return _client


_encoding: tiktoken.Encoding | None = None


def count_tokens(text: str) -> int:
    """Count tokens using cl100k_base (GPT-4 / text-embedding-3 tokeniser)."""
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return len(_encoding.encode(text))


_MAX_EMBEDDING_TOKENS = 8191  # text-embedding-3-large token limit


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to at most *max_tokens* tokens."""
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    tokens = _encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _encoding.decode(tokens[:max_tokens])


def generate_embedding(text: str) -> list[float]:
    """Generate an embedding vector via Azure OpenAI."""
    client = _get_client()
    resp = client.embeddings.create(
        input=_truncate_to_tokens(text, _MAX_EMBEDDING_TOKENS),
        model=config.EMBEDDING_DEPLOYMENT,
        dimensions=config.EMBEDDING_DIMENSIONS,
    )
    return resp.data[0].embedding


def chat_completion(system_prompt: str, user_content: str) -> str:
    """Call GPT-4o for summarisation / keyword extraction."""
    client = _get_client()
    resp = client.chat.completions.create(
        model=config.CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        max_completion_tokens=2048,
    )
    return resp.choices[0].message.content or ""
