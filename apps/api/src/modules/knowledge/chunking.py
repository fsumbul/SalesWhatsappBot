"""Paragraph-aware text chunking shared by document and website ingestion."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_WHITESPACE_RE = re.compile("[ \t\u00a0]+")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÇĞİÖŞÜ0-9])")


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    start: int
    end: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_RE.split(paragraph):
        if len(current) + len(sentence) + 1 > max_chars and current:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    # A single sentence longer than the budget is cut hard as a last resort.
    result: list[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            result.append(piece[:max_chars])
            piece = piece[max_chars:]
        result.append(piece)
    return result


def chunk_text(
    text: str,
    *,
    max_chars: int = 1400,
    overlap_chars: int = 150,
    min_chars: int = 40,
) -> list[TextChunk]:
    """Pack paragraphs into chunks of at most ``max_chars`` with a small overlap.

    Offsets refer to the normalized text so a chunk can always be located in
    the snapshot it came from.
    """

    normalized = normalize_whitespace(text)
    if not normalized:
        return []
    paragraphs = [p.strip() for p in _PARAGRAPH_RE.split(normalized) if p.strip()]
    chunks: list[TextChunk] = []
    current = ""
    current_start = 0
    cursor = 0

    def _flush() -> None:
        nonlocal current, current_start
        body = current.strip()
        if len(body) >= min_chars:
            chunks.append(TextChunk(len(chunks), body, current_start, current_start + len(body)))
        current = ""

    for paragraph in paragraphs:
        position = normalized.find(paragraph, cursor)
        if position == -1:
            position = cursor
        cursor = position + len(paragraph)
        for piece in _split_long(paragraph, max_chars):
            if len(current) + len(piece) + 1 > max_chars and current:
                _flush()
                tail = current_end_tail(chunks, overlap_chars)
                current = f"{tail}\n{piece}".strip() if tail else piece
                current_start = position - len(tail) - 1 if tail else position
            else:
                if not current:
                    current_start = position
                current = f"{current}\n{piece}".strip() if current else piece
    _flush()
    return chunks


def current_end_tail(chunks: list[TextChunk], overlap_chars: int) -> str:
    if not chunks or overlap_chars <= 0:
        return ""
    tail = chunks[-1].text[-overlap_chars:]
    # Start the overlap on a word boundary so the reranker sees clean text.
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_whitespace(text).encode("utf-8")).hexdigest()
