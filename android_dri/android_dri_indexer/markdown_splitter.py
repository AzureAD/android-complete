"""
Markdown recursive splitter — standalone port of DRICopilot's MarkdownRecursiveSplitter.

Uses the same splitting algorithm and separator hierarchy:
  1. Horizontal rules (--- or ***)
  2. Markdown headers (# ## ###)
  3. Code fences (```)
  4. Paragraph breaks (\\n\\n)
  5. Sentence boundaries
  6. Whitespace fallback

Differences from DRICopilot's version:
  - No llama-index dependency (uses tiktoken directly for token counting)
  - Returns List[str] instead of TextNode objects
  - No NodeParser base class / metadata propagation

Original: DRICopilot/src/core/common/utils/markdown_recursive_splitter.py
Copyright (c) Microsoft Corporation. All rights reserved.
"""

from __future__ import annotations

import re
from typing import List, Sequence

import tiktoken

# Tokenizer (same as DRICopilot uses under the hood via SentenceSplitter)
_encoding: tiktoken.Encoding | None = None


def _token_size(text: str) -> int:
    """Count tokens using cl100k_base (GPT-4 / text-embedding-3 tokenizer)."""
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return len(_encoding.encode(text))


def _regex_sentence_splitter(text: str) -> List[str]:
    """Simple regex-based sentence splitter (no NLTK dependency)."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s for s in sentences if s.strip()]


# Separator hierarchy (same as DRICopilot)
_MD_SEPARATORS: Sequence[str] = [
    r"(\n(?:-{3,}|\*{3,})\n)",             # horizontal rule '---' or '***'
    r"(\n#+[^\n]*)",                       # markdown heading '#', '##', …
    r"(\n```[\s\S]*?\n```)",               # code fences
    r"(\n\n)",                             # paragraph / blank line
    r"((?<=[\.\?\!])\s+)",                 # sentence boundary
    r"(\s+)",                              # whitespace fallback
]


class MarkdownRecursiveSplitter:
    """
    Standalone markdown-aware recursive text splitter.

    Replicates DRICopilot's MarkdownRecursiveSplitter algorithm exactly,
    without the llama-index dependency.

    Usage:
        splitter = MarkdownRecursiveSplitter(chunk_size=700, chunk_overlap=125)
        chunks = splitter.split_text(markdown_text)
    """

    def __init__(self, chunk_size: int = 700, chunk_overlap: int = 125):
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._separators = _MD_SEPARATORS

    def _fallback_split(self, text: str) -> List[str]:
        """Ultimate fallback: split by sentences respecting chunk_size."""
        sentences = _regex_sentence_splitter(text)
        chunks: List[str] = []
        current = ""

        for sentence in sentences:
            test = (current + " " + sentence).strip() if current else sentence
            if _token_size(test) <= self._chunk_size:
                current = test
            else:
                if current:
                    chunks.append(current)
                # Apply overlap
                if self._chunk_overlap > 0 and chunks:
                    overlap = self._get_overlap_suffix(chunks[-1])
                    current = (overlap + " " + sentence).strip() if overlap else sentence
                else:
                    current = sentence

        if current.strip():
            chunks.append(current.strip())

        return chunks if chunks else [text]

    def _get_overlap_suffix(self, text: str) -> str:
        """Get the last ~chunk_overlap tokens of text for overlap."""
        sentences = _regex_sentence_splitter(text)
        overlap = ""
        for s in reversed(sentences):
            test = (s + " " + overlap).strip() if overlap else s
            if _token_size(test) <= self._chunk_overlap:
                overlap = test
            else:
                break
        return overlap

    def _recursive_split(self, text: str, sep_idx: int) -> List[str]:
        """Recursively split text using separator list starting at sep_idx."""
        if _token_size(text) <= self._chunk_size:
            return [text]

        if sep_idx >= len(self._separators):
            return self._fallback_split(text)

        pattern = self._separators[sep_idx]
        splits = re.split(pattern, text)

        # Process splits: attach separator to following content
        blocks: List[str] = []
        i = 0
        while i < len(splits):
            if i == 0:
                if splits[i]:
                    blocks.append(splits[i])
                i += 1
            elif i % 2 == 1:
                separator = splits[i]
                content = splits[i + 1] if i + 1 < len(splits) else ""
                combined = (separator + content).strip()
                if combined:
                    blocks.append(combined)
                i += 2
            else:
                # Shouldn't happen with capturing groups
                if splits[i]:
                    blocks.append(splits[i])
                i += 1

        # Combine blocks to maximize chunk size while respecting boundaries
        units: List[str] = []
        current_chunk = ""

        for block in blocks:
            if not block:
                continue

            test_chunk = current_chunk + ("\n\n" if current_chunk else "") + block

            if not current_chunk:
                current_chunk = block
            elif _token_size(test_chunk) <= self._chunk_size:
                current_chunk = test_chunk
            else:
                if current_chunk:
                    units.append(current_chunk)
                current_chunk = block

        if current_chunk:
            units.append(current_chunk)

        # Apply overlap between structural chunks
        if self._chunk_overlap > 0 and len(units) > 1:
            units = self._apply_overlap(units)

        # Recursively split any units still too large
        final_units = []
        for unit in units:
            if unit:
                final_units.extend(self._recursive_split(unit, sep_idx + 1))

        return final_units

    def _apply_overlap(self, chunks: List[str]) -> List[str]:
        """Apply overlap between adjacent chunks."""
        if not chunks or len(chunks) <= 1:
            return chunks

        overlapped = [chunks[0]]

        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            overlap_text = self._get_overlap_suffix(prev)

            if overlap_text:
                combined = overlap_text + "\n\n" + chunks[i]
                if _token_size(combined) <= self._chunk_size + self._chunk_overlap:
                    overlapped.append(combined)
                else:
                    overlapped.append(chunks[i])
            else:
                overlapped.append(chunks[i])

        return overlapped

    def split_text(self, text: str) -> List[str]:
        """Split markdown text into chunks.

        Args:
            text: The full markdown document text.

        Returns:
            List of text chunks, each within chunk_size tokens (approximately).
        """
        if not text or text.strip() == "":
            return []

        chunks = self._recursive_split(text, 0)
        return [c for c in chunks if c.strip()]
