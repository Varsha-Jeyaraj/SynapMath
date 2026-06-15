"""Embedding provider abstraction for Google and Hugging Face."""

from __future__ import annotations

from typing import Any

from langchain_google_genai import GoogleGenerativeAIEmbeddings

import config
from rag.hf_inference import hf_feature_extraction


class HuggingFaceInferenceEmbeddings:
    """Minimal embedding interface compatible with Chroma."""

    def __init__(self, model_ids: list[str]):
        self.model_ids = model_ids
        self.active_model_id = model_ids[0]

    def _embed_one(self, text: str) -> list[float]:
        errors: list[str] = []
        for model_id in self.model_ids:
            try:
                print(f"Embedding attempt: {model_id}")
                self.active_model_id = model_id
                return hf_feature_extraction(model_id, text)
            except Exception as exc:
                print(f"Embedding failed: {model_id} -> {exc}")
                errors.append(f"{model_id}: {exc}")
                continue

        raise RuntimeError("All HF embedding models failed. " + " | ".join(errors))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


class FallbackEmbeddings:
    """Use primary embeddings, then fallback embeddings on failure."""

    def __init__(self, primary: Any, fallback: Any):
        self.primary = primary
        self.fallback = fallback
        self._using_fallback = False

    def _embed_with_primary(self, text: str, is_query: bool = False):
        if self._using_fallback:
            if is_query:
                return self.fallback.embed_query(text)
            return self.fallback.embed_documents([text])[0]
        try:
            if is_query:
                return self.primary.embed_query(text)
            return self.primary.embed_documents([text])[0]
        except Exception as exc:
            print(f"Primary embeddings failed, switching to fallback: {exc}")
            self._using_fallback = True
            if is_query:
                return self.fallback.embed_query(text)
            return self.fallback.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_with_primary(t, is_query=False) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_with_primary(text, is_query=True)


def _google_embedding_candidates() -> list[str]:
    configured = (config.EMBEDDING_MODEL or "").strip() or "models/text-embedding-004"
    candidates = [configured]
    if configured.startswith("models/"):
        candidates.append(configured[len("models/") :])
    else:
        candidates.append(f"models/{configured}")
    candidates.extend(
        [
            "models/text-embedding-004",
            "text-embedding-004",
            "models/gemini-embedding-001",
            "gemini-embedding-001",
        ]
    )
    return list(dict.fromkeys(candidates))


def build_embeddings() -> Any:
    provider = config.EMBEDDING_PROVIDER
    if provider == "huggingface":
        models = [config.HF_EMBEDDING_MODEL, *config.HF_EMBEDDING_FALLBACKS]
        models = list(dict.fromkeys(m for m in models if m))
        primary = HuggingFaceInferenceEmbeddings(models)
        if config.GOOGLE_API_KEY:
            google = _build_google_embeddings()
            return FallbackEmbeddings(primary=primary, fallback=google)
        return primary

    return _build_google_embeddings()


def _build_google_embeddings() -> Any:
    last_error = None
    for model_name in _google_embedding_candidates():
        try:
            return GoogleGenerativeAIEmbeddings(
                model=model_name,
                google_api_key=config.GOOGLE_API_KEY,
            )
        except Exception as exc:
            last_error = exc
            msg = str(exc).lower()
            if (
                "not_found" in msg
                or "not found" in msg
                or "unexpected model name format" in msg
                or "invalid argument" in msg
                or "400" in msg
            ):
                print(f"Google embedding model rejected ({model_name}), trying next candidate...")
                continue
            raise

    raise RuntimeError(
        f"No supported Google embedding model worked. Tried: {_google_embedding_candidates()}"
    ) from last_error
