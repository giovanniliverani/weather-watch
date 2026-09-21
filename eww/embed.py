"""Embeddings (M3): sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 on the CPU.

    embed_documents(conn) -> EmbedStats        one float32 little-endian BLOB per new document
    vectors_for(conn, document_ids) -> {id: np.ndarray}
    encode(texts) -> np.ndarray                unit-length rows, so cosine is a dot product
    similarity_groups(vectors, threshold)       syndicated copies (cosine >= 0.95) for the sidebar
    title_similarity(a, b)                      the eww.matching hook when config.TITLE_SIMILARITY == "embedding"

The model is loaded lazily on first use and cached under config.MODEL_DIR (data/models). Tests install a
deterministic stand-in with `set_encoder()`, so the suite never needs torch.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Protocol

import numpy as np

from eww import config

log = logging.getLogger(__name__)

DTYPE = np.dtype("<f4")


class Encoder(Protocol):
    model: str
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEncoder:
    """The real model; imports torch on first `encode`, not at import time."""

    def __init__(self, model: str | None = None, cache_dir: str | None = None):
        self.model = model or config.EMBED_MODEL
        self.dim = config.EMBED_DIM
        self.cache_dir = cache_dir or str(config.MODEL_DIR)
        self._model = None

    def _load(self):
        if self._model is None:
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("sentence-transformers is not installed: run `uv sync --all-groups` (the 'enrich' dependency group)") from exc
            os.makedirs(self.cache_dir, exist_ok=True)
            log.info("loading embedding model=%s cache=%s", self.model, self.cache_dir)
            self._model = SentenceTransformer(self.model, device="cpu", cache_folder=self.cache_dir)
            self.dim = int(self._model.get_sentence_embedding_dimension() or self.dim)
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=DTYPE)
        model = self._load()
        vectors = model.encode(list(texts), batch_size=config.EMBED_BATCH_SIZE, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        return np.ascontiguousarray(vectors, dtype=DTYPE)


_encoder: Encoder | None = None


def encoder() -> Encoder:
    global _encoder
    if _encoder is None:
        _encoder = SentenceTransformerEncoder()
    return _encoder


def set_encoder(enc: Encoder | None) -> None:
    global _encoder
    _encoder = enc
    _title_vector.cache_clear()


def encode(texts: Iterable[str]) -> np.ndarray:
    return encoder().encode([t or "" for t in texts])


# ----------------------------------------------------------------------------- BLOBs and maths
def to_blob(vector: np.ndarray) -> bytes:
    return np.ascontiguousarray(vector, dtype=DTYPE).tobytes()


def from_blob(blob: bytes, dim: int | None = None) -> np.ndarray:
    vector = np.frombuffer(blob, dtype=DTYPE)
    if dim and vector.size != dim:
        raise ValueError(f"vector has {vector.size} values, expected {dim}")
    return vector


def unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator > 0 else 0.0


def mean_vector(vectors: list[np.ndarray]) -> np.ndarray | None:
    if not vectors:
        return None
    return unit(np.mean(np.stack(vectors), axis=0).astype(DTYPE))


# ----------------------------------------------------------------------------- documents
@dataclass
class EmbedStats:
    documents: int = 0
    model: str = ""
    dim: int = 0


def document_text(row: sqlite3.Row) -> str:
    title = (row["title"] or "").strip()
    excerpt = (row["text_excerpt"] or "").strip()[: config.EMBED_EXCERPT_CHARS]
    return f"{title}. {excerpt}" if title and excerpt else (title or excerpt)


def pending_documents(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = """
        SELECT d.document_id, d.title, d.text_excerpt FROM document d
        WHERE d.removed_at IS NULL AND (d.title IS NOT NULL OR d.text_excerpt IS NOT NULL)
          AND NOT EXISTS (SELECT 1 FROM document_embedding e WHERE e.document_id = d.document_id)
        ORDER BY d.document_id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def embed_documents(conn: sqlite3.Connection, *, limit: int | None = None, batch_size: int | None = None) -> EmbedStats:
    """Encode every document without a vector, in batches, oldest first. Idempotent."""
    enc = encoder()
    stats = EmbedStats(model=enc.model, dim=enc.dim)
    rows = pending_documents(conn, limit)
    size = batch_size or config.EMBED_BATCH_SIZE
    for start in range(0, len(rows), size):
        batch = rows[start : start + size]
        vectors = enc.encode([document_text(r) for r in batch])
        stats.dim = int(vectors.shape[1]) if vectors.ndim == 2 and vectors.shape[0] else stats.dim
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO document_embedding (document_id, model, dim, vector) VALUES (?, ?, ?, ?)",
                [(r["document_id"], enc.model, int(vectors.shape[1]), to_blob(vectors[i])) for i, r in enumerate(batch)],
            )
        stats.documents += len(batch)
    log.info("embed documents=%d model=%s dim=%d", stats.documents, stats.model, stats.dim)
    return stats


def vectors_for(conn: sqlite3.Connection, document_ids: Iterable[str]) -> dict[str, np.ndarray]:
    ids = list(document_ids)
    out: dict[str, np.ndarray] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ", ".join("?" * len(chunk))
        for row in conn.execute(f"SELECT document_id, dim, vector FROM document_embedding WHERE document_id IN ({marks})", chunk):
            out[row["document_id"]] = from_blob(row["vector"], row["dim"])
    return out


# ----------------------------------------------------------------------------- events and titles
@lru_cache(maxsize=4096)
def _title_vector(text: str) -> tuple[float, ...]:
    return tuple(float(v) for v in encode([text])[0])


def title_vector(text: str) -> np.ndarray:
    return np.asarray(_title_vector(text or ""), dtype=DTYPE)


def title_similarity(a: str | None, b: str | None) -> float:
    """Cosine of the two titles' embeddings, clipped to [0, 1] (the eww.matching hook)."""
    if not a or not b:
        return 0.0
    return round(max(0.0, min(1.0, cosine(title_vector(a), title_vector(b)))), 4)


# ----------------------------------------------------------------------------- syndication
def similarity_groups(vectors: dict[str, np.ndarray], threshold: float | None = None) -> list[list[str]]:
    """Group ids whose vectors are within `threshold` cosine of a group member (single linkage), ids sorted."""
    threshold = config.ATTACHMENT["syndication_cosine"] if threshold is None else threshold
    ids = sorted(vectors)
    if not ids:
        return []
    matrix = np.stack([unit(vectors[i]) for i in ids])
    sims = matrix @ matrix.T
    parent = list(range(len(ids)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if sims[i, j] >= threshold:
                parent[find(j)] = find(i)
    groups: dict[int, list[str]] = {}
    for index, doc_id in enumerate(ids):
        groups.setdefault(find(index), []).append(doc_id)
    return sorted(groups.values(), key=lambda g: g[0])
