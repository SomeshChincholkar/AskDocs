"""Gemini embedding helpers with bounded retry and async wrappers."""

import asyncio
import time

from google import genai
from google.genai import types

from app.config import Settings

EMBEDDING_DIMENSION = 768
BATCH_SIZE = 50


def _is_retryable(error: Exception) -> bool:
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    return code in (429, 503, "429", "503")


def _embed_batch(client: genai.Client, model: str, texts: list[str], task_type: str) -> list[list[float]]:
    for attempt in range(4):
        try:
            result = client.models.embed_content(
                model=model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=EMBEDDING_DIMENSION,
                ),
            )
            vectors = [embedding.values for embedding in result.embeddings or []]
            if len(vectors) != len(texts) or any(
                len(vector) != EMBEDDING_DIMENSION for vector in vectors
            ):
                raise RuntimeError("The embedding service returned an unexpected vector shape.")
            return vectors
        except Exception as error:
            if attempt == 3 or not _is_retryable(error):
                raise
            time.sleep(2**attempt)
    raise RuntimeError("Embedding request exhausted its retries.")


async def embed_documents(texts: list[str], settings: Settings) -> list[list[float]]:
    """Embed document chunks in batches without blocking the event loop."""
    return await _embed(texts, settings, "RETRIEVAL_DOCUMENT")


async def embed_query(text: str, settings: Settings) -> list[float]:
    """Embed a single user question for retrieval."""
    vectors = await _embed([text], settings, "RETRIEVAL_QUERY")
    return vectors[0]


async def _embed(texts: list[str], settings: Settings, task_type: str) -> list[list[float]]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    client = genai.Client(api_key=settings.gemini_api_key)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        vectors.extend(
            await asyncio.to_thread(
                _embed_batch,
                client,
                settings.embedding_model,
                texts[start : start + BATCH_SIZE],
                task_type,
            )
        )
    return vectors
