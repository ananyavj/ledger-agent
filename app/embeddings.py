"""Turns text into embeddings (lists of 384 numbers that capture meaning).

Model: BAAI/bge-small-en-v1.5, run locally by fastembed (ONNX, no PyTorch).
It downloads once (~70 MB) into ./models and then works offline.
"""
from functools import lru_cache
from pathlib import Path

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384   # must match VECTOR(384) in the schema
CACHE_DIR = Path(__file__).resolve().parent.parent / "models"


@lru_cache(maxsize=1)
def _model():
    """Load the model once per process (loading takes a second or two)."""
    from fastembed import TextEmbedding   # imported here so other tools don't pay the import cost
    return TextEmbedding(MODEL_NAME, cache_dir=str(CACHE_DIR))


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embed document chunks. bge models embed documents and queries slightly differently."""
    return [v.tolist() for v in _model().passage_embed(texts)]


def embed_query(text: str) -> list[float]:
    """Embed a search question."""
    return next(iter(_model().query_embed(text))).tolist()


def to_pgvector(vector: list[float]) -> str:
    """pgvector accepts vectors written as text like '[0.12,-0.03,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
