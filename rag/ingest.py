"""Document ingestion for quiz reference files."""

from pathlib import Path
import hashlib
import json
import re
import shutil

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_community.vectorstores import Chroma

import config
from rag.embeddings import build_embeddings


CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
MANIFEST_FILE_NAME = ".ingest_manifest.json"


def _extract_grade_from_filename(path: Path) -> int | None:
    name = path.name.lower()
    match = re.search(r"\b(?:g|gr|grade)[\s\-_]?(\d{1,2})\b", name)
    if not match:
        return None
    try:
        grade = int(match.group(1))
    except Exception:
        return None
    return grade if 6 <= grade <= 11 else None


def _clean_pdf_text(text: str) -> str:
    # Remove noisy parser warnings that can leak into extracted text.
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        l = line.strip()
        if not l:
            continue
        if "Multiple definitions in dictionary at byte" in l:
            continue
        cleaned.append(l)
    joined = "\n".join(cleaned)
    # Collapse excessive whitespace.
    joined = re.sub(r"[ \t]{2,}", " ", joined)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


def _load_file(path: Path) -> list:
    suffix = path.suffix.lower()
    grade = _extract_grade_from_filename(path)
    if suffix in {".md", ".txt"}:
        docs = TextLoader(str(path), encoding="utf-8").load()
        for d in docs:
            if grade is not None:
                d.metadata["grade"] = grade
        return docs
    if suffix == ".pdf":
        docs = PyPDFLoader(str(path)).load()
        filtered = []
        for d in docs:
            d.page_content = _clean_pdf_text(d.page_content or "")
            if grade is not None:
                d.metadata["grade"] = grade
            # Skip pages with very little readable content.
            if len(d.page_content) >= 120:
                filtered.append(d)
        return filtered
    return []


def _reference_files(data_dirs: list[Path] | None = None) -> list[Path]:
    """Return supported and included files from configured reference directories."""
    if data_dirs is None:
        data_dirs = config.QUIZ_REFERENCE_DIRS
    files: list[Path] = []
    for configured_dir in data_dirs:
        base_dir = config.resolve_reference_dir(configured_dir)
        if not base_dir.exists():
            continue
        for path in base_dir.rglob("*"):
            if not path.is_file():
                continue
            if not config.include_quiz_reference_file(path):
                continue
            files.append(path)
    return sorted(files, key=lambda p: str(p).lower())


def load_documents(data_dirs: list[Path] | None = None) -> list:
    """Load supported files from configured quiz reference directories."""
    docs = []
    for path in _reference_files(data_dirs):
        docs.extend(_load_file(path))
    return docs


def split_documents(documents: list) -> list:
    """Split documents into chunks for embedding."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    return splitter.split_documents(documents)


def create_vector_store(chunks: list) -> None:
    """Embed chunks and persist them to ChromaDB."""
    embeddings = build_embeddings()
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=config.CHROMA_PERSIST_DIR,
        collection_name=config.CHROMA_COLLECTION_NAME,
    )
    print(f"Stored {vectorstore._collection.count()} chunks in ChromaDB.")
    if config.EMBEDDING_PROVIDER == "huggingface":
        print(f"Embedding provider: huggingface ({config.HF_EMBEDDING_MODEL})")
    else:
        print(f"Embedding provider: google ({config.EMBEDDING_MODEL})")


def _manifest_path() -> Path:
    return Path(config.CHROMA_PERSIST_DIR) / MANIFEST_FILE_NAME


def _compute_manifest_hash(source_files: list[Path]) -> str:
    payload = {
        "source_files": [
            {
                "path": str(p.resolve()),
                "size": p.stat().st_size,
                "mtime_ns": p.stat().st_mtime_ns,
            }
            for p in source_files
        ],
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "embedding_provider": config.EMBEDDING_PROVIDER,
        "hf_embedding_model": getattr(config, "HF_EMBEDDING_MODEL", ""),
        "hf_embedding_fallbacks": getattr(config, "HF_EMBEDDING_FALLBACKS", []),
        "google_embedding_model": getattr(config, "EMBEDDING_MODEL", ""),
        "collection_name": config.CHROMA_COLLECTION_NAME,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_manifest_hash() -> str | None:
    path = _manifest_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("manifest_hash")
        return str(value) if value else None
    except Exception:
        return None


def _write_manifest_hash(manifest_hash: str) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"manifest_hash": manifest_hash}, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def ingest_all(force: bool = False) -> dict:
    """Full pipeline: load -> split -> store; skip if unchanged unless force=True."""
    resolved = [str(config.resolve_reference_dir(d)) for d in config.QUIZ_REFERENCE_DIRS]
    print("Loading documents from:")
    for d in resolved:
        print(f" - {d}")
    print("Filename filter: loadQuizRef + TextBooks Grade 10/11")
    source_files = _reference_files()
    if not source_files:
        raise ValueError(
            "No supported quiz reference files found. "
            "Expected: any .pdf/.md/.txt in loadQuizRef, or Grade 10/11 files in TextBooks."
        )

    new_manifest_hash = _compute_manifest_hash(source_files)
    old_manifest_hash = _read_manifest_hash()
    if not force and old_manifest_hash == new_manifest_hash:
        print("Ingestion skipped: source files and embedding settings unchanged.")
        return {"status": "skipped", "message": "Ingestion skipped (no source changes detected)."}

    docs = load_documents()
    print(f"Found {len(docs)} document(s).")
    if not docs:
        raise ValueError(
            "No supported quiz reference files found. "
            "Expected: any .pdf/.md/.txt in loadQuizRef, or Grade 10/11 files in TextBooks."
        )

    print("Splitting into chunks...")
    chunks = split_documents(docs)
    print(f"Created {len(chunks)} chunk(s).")

    print("Creating vector store...")
    persist_dir = Path(config.CHROMA_PERSIST_DIR)
    if persist_dir.exists():
        # Rebuild from scratch to avoid duplicate vectors across re-index runs.
        shutil.rmtree(persist_dir)
    create_vector_store(chunks)
    _write_manifest_hash(new_manifest_hash)
    print("Ingestion complete.")
    return {"status": "rebuilt", "message": "Ingestion complete."}


if __name__ == "__main__":
    ingest_all()
