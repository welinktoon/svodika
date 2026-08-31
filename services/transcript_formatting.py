"""Stable, readable layout for raw and AI-polished transcripts."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Optional


REPLICA_HEADER_PATTERN = re.compile(
    r"^\[(?P<timestamp>(?:\d{1,2}:)?\d{2}:\d{2})\]"
    r"(?:\s+(?P<speaker>[^\n]{1,80}))?\s*$"
)

AI_DIALOGUE_FORMAT_INSTRUCTION = (
    "Оформи сам текст расшифровки как диалог: каждая реплика — отдельным "
    "абзацем. Если в исходнике есть метка времени в формате [ММ:СС] или "
    "[ЧЧ:ММ:СС], сохрани её на отдельной строке перед текстом реплики. "
    "После метки можно указать имя или роль говорящего только если это "
    "однозначно следует из исходного текста; иначе оставь просто метку "
    "времени. Не придумывай участников, роли или различия между голосами."
)


def format_timestamp(seconds: Optional[float]) -> str:
    """Return an elapsed-time label suitable for a transcript replica."""
    total_seconds = max(0, int(float(seconds or 0)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _clean_segment_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def format_segmented_transcript(
    segments: Iterable[tuple[Optional[float], str]],
) -> str:
    """Keep model segments as timestamped, separately readable replicas.

    Faster-Whisper gives a start time for every emitted segment.  Older test
    doubles and a few third-party adapters do not; in that case we retain the
    former single-line behaviour instead of inventing a timestamp.
    """
    cleaned_segments = []
    for start, text in segments:
        cleaned_text = _clean_segment_text(text)
        if cleaned_text:
            cleaned_segments.append((start, cleaned_text))
    if not cleaned_segments:
        return ""
    if not any(start is not None for start, _text in cleaned_segments):
        return " ".join(text for _start, text in cleaned_segments)
    return "\n\n".join(
        (
            f"[{format_timestamp(start)}]\n{text}"
            if start is not None
            else text
        )
        for start, text in cleaned_segments
    )


def has_replica_headers(text: str) -> bool:
    """Return whether text already has timestamped dialogue blocks."""
    return any(
        REPLICA_HEADER_PATTERN.fullmatch(line.strip())
        for line in (text or "").splitlines()
    )


def make_plain_transcript_readable(text: str) -> str:
    """Add breathable paragraphs to ASR text that has no segment timings."""
    value = (text or "").strip()
    if not value or has_replica_headers(value):
        return value

    existing_paragraphs = [
        re.sub(r"\s+", " ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n+", value)
        if paragraph.strip()
    ]
    if len(existing_paragraphs) > 1:
        return "\n\n".join(existing_paragraphs)

    # Single newlines often already encode speakers or manually separated
    # replicas. Preserve that human structure instead of flattening it back
    # into one ASR-like wall of text.
    existing_lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in value.splitlines()
        if line.strip()
    ]
    if len(existing_lines) > 1:
        return "\n\n".join(existing_lines)

    sentences = re.split(
        r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ0-9])",
        existing_paragraphs[0] if existing_paragraphs else value,
    )
    paragraphs = []
    current = []
    current_length = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        projected_length = current_length + len(sentence) + (1 if current else 0)
        if current and (len(current) >= 2 or projected_length > 280):
            paragraphs.append(" ".join(current))
            current = []
            current_length = 0
        current.append(sentence)
        current_length += len(sentence) + (1 if current_length else 0)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs) or value


def append_ai_dialogue_instruction(prompt: str) -> str:
    """Ensure every configured AI cleanup path follows the dialogue layout."""
    base = (prompt or "").strip()
    if AI_DIALOGUE_FORMAT_INSTRUCTION in base:
        return base
    return f"{base}\n\n{AI_DIALOGUE_FORMAT_INSTRUCTION}".strip()
