"""
Central configuration for the RAG MCQ Assessment System.
Loads settings from .env and provides typed access.
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

# ── Load environment variables ──────────────────────────────────────────────
load_dotenv()

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
STUDENT_RECORDS_DIR = PROJECT_ROOT / "guidance" / "student_records"
QUIZ_REFERENCE_DIR = Path(
    os.getenv("QUIZ_REFERENCE_DIR", str(DATA_DIR / "loadQuizRef"))
).expanduser()
QUIZ_REFERENCE_DIRS = [
    p
    for p in [
        Path(x.strip()).expanduser()
        for x in os.getenv(
            "QUIZ_REFERENCE_DIRS",
            f"{DATA_DIR / 'loadQuizRef'},{DATA_DIR / 'TextBooks'}",
        ).split(",")
        if x.strip()
    ]
]


def resolve_reference_dir(path: Path) -> Path:
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _extract_grade_from_name(path: Path) -> int | None:
    name = path.name.lower()
    match = re.search(r"\b(?:g|gr|grade)[\s\-_]?(\d{1,2})\b", name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def include_reference_file(path: Path) -> bool:
    """Include files that look like Grade 6-11 by filename."""
    name = path.name.lower()
    patterns = []
    for grade in range(6, 12):
        patterns.extend(
            [
                rf"\bg[\s\-_]?{grade}\b",
                rf"\bgrade[\s\-_]?{grade}\b",
                rf"gr[\s\-_]?{grade}\b",
            ]
        )
    return any(re.search(p, name) for p in patterns)


def include_quiz_reference_file(path: Path) -> bool:
    """
    Quiz-tab source policy:
    - allow all supported files under loadQuizRef
    - allow only Grade 10/11 files under TextBooks
    """
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".md", ".txt"}:
        return False

    load_quiz_ref_dir = resolve_reference_dir(DATA_DIR / "loadQuizRef")
    textbooks_dir = resolve_reference_dir(DATA_DIR / "TextBooks")

    if _is_within(path, load_quiz_ref_dir):
        return True

    if _is_within(path, textbooks_dir):
        grade = _extract_grade_from_name(path)
        return grade in {10, 11}

    return False

# ── LLM Settings ────────────────────────────────────────────────────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "huggingface").strip().lower()
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "huggingface").strip().lower()
HF_API_KEY = os.getenv("HF_API_KEY", "").strip()
HF_LLM_MODEL = os.getenv("HF_LLM_MODEL", "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B").strip()
HF_LLM_FALLBACKS = [
    m.strip()
    for m in os.getenv(
        "HF_LLM_FALLBACKS",
        "Qwen/Qwen2.5-7B-Instruct,microsoft/Phi-3.5-mini-instruct",
    ).split(",")
    if m.strip()
]
HF_EMBEDDING_MODEL = os.getenv("HF_EMBEDDING_MODEL", "BAAI/bge-m3").strip()
HF_EMBEDDING_FALLBACKS = [
    m.strip()
    for m in os.getenv(
        "HF_EMBEDDING_FALLBACKS",
        "sentence-transformers/all-MiniLM-L6-v2,intfloat/e5-base-v2",
    ).split(",")
    if m.strip()
]
HF_API_URL_BASE = os.getenv("HF_API_URL_BASE", "https://api-inference.huggingface.co/models").strip().rstrip("/")
HF_TIMEOUT_SECONDS = int(os.getenv("HF_TIMEOUT_SECONDS", "90"))

# ── Abacus AI (OpenAI-compatible RouteLLM API) ───────────────────────────────
ABACUS_API_KEY = os.getenv("ABACUS_API_KEY", "").strip()
ABACUS_BASE_URL = os.getenv("ABACUS_BASE_URL", "https://routellm.abacus.ai/v1").strip().rstrip("/")
ABACUS_MODEL = os.getenv("ABACUS_MODEL", "gemini-3.5-flash").strip()
ABACUS_COST_LIMIT = float(os.getenv("ABACUS_COST_LIMIT", "1.0"))

# Parallel LLM calls when building a full paper (topics × difficulty levels).
GENERATION_MAX_WORKERS = max(1, int(os.getenv("GENERATION_MAX_WORKERS", "4")))

# Optional MCQ diagram generation (Hugging Face Inference text-to-image).
IMAGE_GEN_ENABLED = os.getenv("IMAGE_GEN_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
IMAGE_GEN_MAX_PER_PAPER = max(0, int(os.getenv("IMAGE_GEN_MAX_PER_PAPER", "8")))
# Default to sd-turbo — widely available on HF Inference; FLUX often needs a paid tier.
HF_IMAGE_MODEL = os.getenv("HF_IMAGE_MODEL", "stabilityai/sd-turbo").strip()
HF_IMAGE_FALLBACKS = [
    m.strip()
    for m in os.getenv(
        "HF_IMAGE_FALLBACKS",
        "black-forest-labs/FLUX.1-schnell,runwayml/stable-diffusion-v1-5",
    ).split(",")
    if m.strip()
]
HF_IMAGE_TIMEOUT_SECONDS = int(os.getenv("HF_IMAGE_TIMEOUT_SECONDS", "120"))
HF_IMAGE_WIDTH = max(256, min(1024, int(os.getenv("HF_IMAGE_WIDTH", "512"))))
HF_IMAGE_HEIGHT = max(256, min(1024, int(os.getenv("HF_IMAGE_HEIGHT", "512"))))
_gs = os.getenv("HF_IMAGE_GUIDANCE_SCALE", "8.0").strip()
HF_IMAGE_GUIDANCE_SCALE = float(_gs) if _gs else None
HF_IMAGE_SEED_MODE = os.getenv("HF_IMAGE_SEED_MODE", "stable").strip().lower()
# When the MCQ JSON has no image_prompt, optionally generate from the question stem (often looks random; default off).
IMAGE_GEN_FALLBACK_QUESTION = os.getenv("IMAGE_GEN_FALLBACK_QUESTION", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
IMAGE_GEN_FALLBACK_MAX_CHARS = max(80, min(800, int(os.getenv("IMAGE_GEN_FALLBACK_MAX_CHARS", "280"))))
IMAGE_GEN_FALLBACK_REQUIRE_KEYWORDS = os.getenv(
    "IMAGE_GEN_FALLBACK_REQUIRE_KEYWORDS", "true"
).strip().lower() in ("1", "true", "yes", "on")

# ── Vector Store Settings ────────────────────────────────────────────────────
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", str(PROJECT_ROOT / "chroma_db"))
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "mcq_content")

# ── Data File Paths ──────────────────────────────────────────────────────────
TOPICS_FILE = DATA_DIR / "topics.json"
DIFFICULTY_LEVELS_FILE = DATA_DIR / "difficulty_levels.json"
SYLLABUS_FILE = DATA_DIR / "syllabus.json"

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
