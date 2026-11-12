"""Local embedding model (no paid API). Model name comes from EMBED_MODEL."""
from __future__ import annotations

from functools import lru_cache

from foodlabel.common.config import get_settings


@lru_cache(maxsize=2)
def get_embeddings(model_name: str | None = None):
    from langchain_huggingface import HuggingFaceEmbeddings

    name = model_name or get_settings().embed_model
    if not name:
        get_settings().require("embed_model")
    return HuggingFaceEmbeddings(model_name=name, encode_kwargs={"normalize_embeddings": True})
