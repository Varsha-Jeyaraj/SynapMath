"""Helpers for Hugging Face hosted inference (router-compatible)."""

from huggingface_hub import InferenceClient

import config


def _client() -> InferenceClient:
    if not config.HF_API_KEY:
        raise RuntimeError("HF_API_KEY is missing. Set it in .env.")
    return InferenceClient(token=config.HF_API_KEY, timeout=config.HF_TIMEOUT_SECONDS)


def hf_text_generation(
    model_id: str,
    prompt: str,
    max_new_tokens: int = 1200,
    temperature: float = 0.3,
) -> str:
    try:
        text = _client().text_generation(
            prompt=prompt,
            model=model_id,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            return_full_text=False,
        )
        if text:
            return str(text)
    except Exception:
        # Fallback to conversational API for providers that do not expose text-generation.
        pass

    try:
        out = _client().chat_completion(
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_new_tokens,
            temperature=temperature,
        )
        if out and getattr(out, "choices", None):
            msg = out.choices[0].message
            content = getattr(msg, "content", None)
            if content:
                return str(content)
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                return str(reasoning)
        raise RuntimeError("No text content returned by chat_completion.")
    except Exception as exc:
        raise RuntimeError(f"HF text generation failed for model '{model_id}': {exc}") from exc


def hf_feature_extraction(model_id: str, text: str) -> list[float]:
    try:
        out = _client().feature_extraction(text=text, model=model_id)
        # huggingface_hub may return a numpy array
        if hasattr(out, "tolist"):
            out = out.tolist()
        if isinstance(out, list) and out and isinstance(out[0], list):
            return [float(v) for v in out[0]]
        if isinstance(out, list):
            return [float(v) for v in out]
        raise RuntimeError(f"Unexpected embedding response type: {type(out)}")
    except Exception as exc:
        raise RuntimeError(f"HF embedding failed for model '{model_id}': {exc}") from exc
