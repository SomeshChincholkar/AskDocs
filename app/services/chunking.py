"""Recursive, page-preserving text chunking."""

import re


def _split_by_separator(text: str, separator: str) -> list[str]:
    if not separator:
        return list(text)
    if separator.startswith("(?<="):
        parts = re.split(f"({separator})", text)
        return [
            parts[index] + (parts[index + 1] if index + 1 < len(parts) else "")
            for index in range(0, len(parts), 2)
            if parts[index]
        ]
    parts = text.split(separator)
    return [
        (part + separator if index < len(parts) - 1 else part)
        for index, part in enumerate(parts)
        if part
    ]


def _recursive_pieces(text: str, size: int, separators: tuple[str, ...]) -> list[str]:
    if len(text) <= size:
        return [text]
    if not separators:
        return [text[index : index + size] for index in range(0, len(text), size)]

    separator = separators[0]
    parts = _split_by_separator(text, separator)
    if len(parts) == 1:
        return _recursive_pieces(text, size, separators[1:])

    output: list[str] = []
    current = ""
    for part in parts:
        if len(part) > size:
            if current:
                output.append(current)
                current = ""
            output.extend(_recursive_pieces(part, size, separators[1:]))
        elif current and len(current) + len(part) > size:
            output.append(current)
            current = part
        else:
            current += part
    if current:
        output.append(current)
    return output


def split_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """Split text by paragraphs, lines, sentences, then words."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not normalized:
        return []

    pieces = _recursive_pieces(
        normalized,
        chunk_size,
        ("\n\n", "\n", r"(?<=[.!?])\s+", " ", ""),
    )
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) > chunk_size:
            piece = piece[:chunk_size]
        if current and len(current) + 1 + len(piece) > chunk_size:
            chunks.append(current)
            current = current[-overlap:] if overlap else ""
        current = f"{current} {piece}".strip()
        while len(current) > chunk_size:
            chunks.append(current[:chunk_size].strip())
            current = current[chunk_size - overlap :] if overlap else current[chunk_size:]
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def chunk_pages(
    pages: list[tuple[int, str]],
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[tuple[int, str]]:
    """Split each page independently and retain its page number."""
    return [
        (page_number, chunk)
        for page_number, text in pages
        for chunk in split_text(text, chunk_size, overlap)
    ]
