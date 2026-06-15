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


def _is_recoverable_google_model_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "not_found" in msg
        or "not found" in msg
        or "unexpected model name format" in msg
        or "is not supported for embedcontent" in msg
        or "invalid argument" in msg
        or "404" in msg
        or "400" in msg
    )


class GoogleFallbackEmbeddings:
    """Try multiple Google embedding model IDs at runtime."""

    def __init__(self, model_ids: list[str], google_api_key: str):
        self.model_ids = list(dict.fromkeys(m for m in model_ids if m))
        self.google_api_key = google_api_key
        self._active_idx = 0
        self._active = GoogleGenerativeAIEmbeddings(
            model=self.model_ids[self._active_idx],
            google_api_key=self.google_api_key,
        )

    def _switch_model(self) -> bool:
        if self._active_idx + 1 >= len(self.model_ids):
            return False
        self._active_idx += 1
        model_name = self.model_ids[self._active_idx]
        print(f"Switching Google embedding model to: {model_name}")
        self._active = GoogleGenerativeAIEmbeddings(
            model=model_name,
            google_api_key=self.google_api_key,
        )
        return True

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        while True:
            try:
                return self._active.embed_documents(texts)
            except Exception as exc:
                last_error = exc
                if _is_recoverable_google_model_error(exc) and self._switch_model():
                    continue
                raise

    def embed_query(self, text: str) -> list[float]:
        last_error: Exception | None = None
        while True:
            try:
                return self._active.embed_query(text)
            except Exception as exc:
                last_error = exc
                if _is_recoverable_google_model_error(exc) and self._switch_model():
                    continue
                raise


def _google_embedding_candidates() -> list[str]:
    configured = (config.EMBEDDING_MODEL or "").strip() or "models/gemini-embedding-001"
    candidates = [configured]
    if configured.startswith("models/"):
        candidates.append(configured[len("models/") :])
    else:
        candidates.append(f"models/{configured}")
    candidates.extend(
        [
            "models/gemini-embedding-001",
            "gemini-embedding-001",
            "models/embedding-001",
            "embedding-001",
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
    return GoogleFallbackEmbeddings(
        model_ids=_google_embedding_candidates(),
        google_api_key=config.GOOGLE_API_KEY,
    )
